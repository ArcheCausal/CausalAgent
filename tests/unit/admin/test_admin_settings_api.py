import os
import unittest
from unittest.mock import patch

from flask import Flask


TEST_ENV = {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "test-database",
}
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)


from Database.monitor_settings import (
    MonitorSettingsValidationError,
    MonitorSettingsVersionConflict,
)
from app.admin.routes import admin_bp
from app.request_context import register_request_context


ADMIN = {"id": 9, "username": "settings-admin", "role": "admin", "is_active": True}
OVERRIDES = {
    "auto_refresh_enabled": None,
    "realtime_interval_seconds": None,
    "sql_interval_seconds": None,
    "table_capacity_interval_seconds": None,
    "slow_query_warning_delta": None,
    "integrity_enabled": None,
    "integrity_interval_seconds": None,
}
CURRENT = {
    "version": 2,
    "overrides": OVERRIDES,
    "effective": {
        "auto_refresh_enabled": True,
        "realtime_interval_seconds": 10,
        "sql_interval_seconds": 60,
        "table_capacity_interval_seconds": 900,
        "slow_query_warning_delta": 1,
        "integrity_enabled": False,
        "integrity_interval_seconds": 86400,
    },
    "sources": {key: "default" for key in OVERRIDES},
    "limits": {},
    "updated_by": None,
    "updated_at": None,
    "state": "current",
    "warning": None,
}


from tests.support.authorization import (
    ADMIN_PERMISSIONS,
    USER_PERMISSIONS,
    authorized_as,
)

def build_app():
    """构建包含 request ID 钩子的管理员 API 测试应用。"""
    app = Flask(__name__)
    app.secret_key = "settings-api-secret"
    register_request_context(app)
    app.register_blueprint(admin_bp)
    return app


class AdminSettingsApiTests(unittest.TestCase):
    """验证监控配置 API 的 CSRF、版本、错误与审计关联契约。"""

    def authenticated_client(self, app):
        """返回写入 Session CSRF 的测试客户端。"""
        client = app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["csrf_token"] = "settings-csrf"
        return client

    def test_put_passes_actor_and_same_request_id_to_service(self):
        """保存接口把实时管理员与关联 ID 原样传给事务服务。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch("app.admin.routes.save_monitor_settings", return_value=CURRENT) as save,
        ):
            client = self.authenticated_client(app)
            response = client.put(
                "/api/admin/db/settings",
                json={"version": 1, "overrides": OVERRIDES},
                headers={
                    "X-CSRF-Token": "settings-csrf",
                    "X-Request-ID": "settings-put-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["version"], 2)
        self.assertEqual(response.headers["X-Request-ID"], "settings-put-1")
        self.assertEqual(save.call_args.kwargs["actor"], ADMIN)
        self.assertEqual(save.call_args.kwargs["request_id"], "settings-put-1")

    def test_validation_error_is_field_level_400(self):
        """服务字段错误应保持字段映射和请求 ID。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.save_monitor_settings",
                side_effect=MonitorSettingsValidationError({"realtime_interval_seconds": "必须在 5 到 10 之间"}),
            ),
        ):
            client = self.authenticated_client(app)
            response = client.put(
                "/api/admin/db/settings",
                json={"version": 1, "overrides": OVERRIDES},
                headers={
                    "X-CSRF-Token": "settings-csrf",
                    "X-Request-ID": "settings-invalid-1",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["code"], "validation_error")
        self.assertIn("realtime_interval_seconds", payload["fields"])
        self.assertEqual(payload["request_id"], "settings-invalid-1")

    def test_version_conflict_returns_current_configuration(self):
        """乐观锁冲突应返回 409 和当前配置供前端重载。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.save_monitor_settings",
                side_effect=MonitorSettingsVersionConflict(CURRENT),
            ),
        ):
            client = self.authenticated_client(app)
            response = client.put(
                "/api/admin/db/settings",
                json={"version": 1, "overrides": OVERRIDES},
                headers={"X-CSRF-Token": "settings-csrf"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "version_conflict")
        self.assertEqual(response.get_json()["current"]["version"], 2)

    def test_unexpected_write_failure_records_failed_event_with_request_id(self):
        """数据库写失败应尽力记录 failed 审计并返回可关联错误。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch("app.admin.routes.save_monitor_settings", side_effect=RuntimeError("offline")),
            patch("app.admin.routes.record_admin_audit_event") as audit,
        ):
            client = self.authenticated_client(app)
            response = client.put(
                "/api/admin/db/settings",
                json={"version": 1, "overrides": OVERRIDES},
                headers={
                    "X-CSRF-Token": "settings-csrf",
                    "X-Request-ID": "settings-failed-1",
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["request_id"], "settings-failed-1")
        self.assertEqual(audit.call_args.kwargs["result"], "failed")
        self.assertEqual(audit.call_args.kwargs["request_id"], "settings-failed-1")

    def test_reset_and_history_cursor_are_bounded(self):
        """重置要求版本，历史 limit/before_id 保持有界正整数。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch("app.admin.routes.reset_monitor_settings", return_value=CURRENT) as reset,
            patch(
                "app.admin.routes.list_monitor_setting_events",
                return_value={"items": [], "next_before_id": None},
            ) as history,
        ):
            client = self.authenticated_client(app)
            reset_response = client.post(
                "/api/admin/db/settings/reset",
                json={"version": 1},
                headers={"X-CSRF-Token": "settings-csrf"},
            )
            history_response = client.get(
                "/api/admin/db/settings/history?limit=50&before_id=101"
            )
            bad_limit = client.get("/api/admin/db/settings/history?limit=101")
            bad_cursor = client.get("/api/admin/db/settings/history?before_id=0")

        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(reset.call_args.kwargs["version"], 1)
        self.assertEqual(history_response.status_code, 200)
        history.assert_called_once_with(limit=50, before_id=101)
        self.assertEqual(bad_limit.status_code, 400)
        self.assertEqual(bad_cursor.status_code, 400)


if __name__ == "__main__":
    unittest.main()
