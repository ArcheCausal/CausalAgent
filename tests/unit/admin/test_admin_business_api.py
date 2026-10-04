import io
import os
import unittest
from unittest.mock import ANY, patch

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


from app.admin.contracts import AdminApiError, decode_cursor, encode_cursor, page_result
from app.admin.routes import admin_bp, admin_page_bp
from app.request_context import register_request_context


ADMIN = {"id": 7, "username": "admin", "role": "admin", "is_active": True}
NORMAL_USER = {"id": 8, "username": "user", "role": "user", "is_active": True}


from tests.support.authorization import (
    ADMIN_PERMISSIONS,
    USER_PERMISSIONS,
    authorized_as,
)

def build_app():
    """构造只注册管理员蓝图和请求上下文的最小 Flask 应用。"""
    app = Flask(__name__)
    app.secret_key = "admin-business-test"
    register_request_context(app)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_page_bp)
    return app


class AdminBusinessContractTests(unittest.TestCase):
    """验证 3.1 统一分页游标和 DTO 响应边界。"""

    def test_cursor_round_trip_and_extra_row_pagination(self):
        """游标必须保持不透明且仅在多取一行时发出下一页。"""
        cursor = encode_cursor(("2026-07-26T12:00:00", 42))
        self.assertNotIn("2026-07-26", cursor)
        self.assertEqual(decode_cursor(cursor, size=2), ["2026-07-26T12:00:00", 42])

        result = page_result(
            [
                {"created_at": "2026-07-26T12:00:00", "id": 42},
                {"created_at": "2026-07-26T11:00:00", "id": 41},
            ],
            limit=1,
            cursor_fields=("created_at", "id"),
        )
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["has_more"])
        self.assertEqual(
            decode_cursor(result["next_cursor"], size=2),
            ["2026-07-26T12:00:00", 42],
        )

    def test_invalid_cursor_has_stable_error_code(self):
        """不可解析游标必须返回稳定 invalid_cursor 错误。"""
        with self.assertRaises(AdminApiError) as context:
            decode_cursor("not-a-cursor", size=2)
        self.assertEqual(context.exception.code, "invalid_cursor")


class AdminBusinessApiTests(unittest.TestCase):
    """验证新增业务 API 的授权、错误、审计与下载契约。"""

    def test_user_list_passes_bounded_filters_and_returns_request_id(self):
        """用户列表只把已校验页大小和显式筛选交给查询服务。"""
        app = build_app()
        payload = {
            "items": [{"id": 1, "username": "alice", "role": "user"}],
            "limit": 20,
            "has_more": False,
            "next_cursor": None,
        }
        with (
            authorized_as(ADMIN),
            patch("app.admin.routes.list_users", return_value=payload) as service,
            app.test_client() as client,
        ):
            response = client.get(
                "/api/admin/business/users?role=user&is_active=true&q=ali",
                headers={"X-Request-ID": "business-list-1"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["request_id"], "business-list-1")
        self.assertNotIn("password_hash", body["data"]["items"][0])
        service.assert_called_once_with(
            limit=20,
            cursor=None,
            q="ali",
            role="user",
            is_active="true",
        )

    def test_invalid_limit_returns_stable_validation_error(self):
        """超出最大页大小时不得触发数据库查询。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch("app.admin.routes.list_users") as service,
            app.test_client() as client,
        ):
            response = client.get("/api/admin/business/users?limit=51")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_query")
        service.assert_not_called()

    def test_normal_user_is_denied_and_denial_is_audited(self):
        """普通用户访问敏感详情必须得到 403 且留存拒绝审计。"""
        app = build_app()
        with (
            authorized_as(NORMAL_USER, USER_PERMISSIONS),
            patch(
                "app.admin.contracts.record_admin_audit_event",
                return_value=True,
            ) as audit,
            patch("app.admin.routes.get_user_detail") as service,
            app.test_client() as client,
        ):
            response = client.get("/api/admin/business/users/3")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "admin_required")
        service.assert_not_called()
        self.assertEqual(audit.call_args.kwargs["result"], "rejected")
        self.assertEqual(audit.call_args.kwargs["target_id"], "3")
        self.assertIsNone(audit.call_args.kwargs["new_values"])

    def test_brand_logo_is_also_protected_from_normal_users(self):
        """侧栏 Logo 复用原图，但图片接口本身仍属于管理员权限边界。"""
        app = build_app()
        with (
            authorized_as(NORMAL_USER, USER_PERMISSIONS),
            app.test_client() as client,
        ):
            response = client.get("/api/admin/brand/logo")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "admin_required")

    def test_runtime_config_returns_grafana_url_for_admins_only(self):
        """运行期地址按部署环境下发，且同样受管理员权限边界约束。"""
        app = build_app()
        with (
            authorized_as(ADMIN, ADMIN_PERMISSIONS),
            app.test_client() as client,
        ):
            response = client.get("/api/admin/runtime-config")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("request_id", payload)
        self.assertIn("grafana_url", payload["data"])

        with (
            authorized_as(NORMAL_USER, USER_PERMISSIONS),
            app.test_client() as client,
        ):
            forbidden = client.get("/api/admin/runtime-config")

        self.assertEqual(forbidden.status_code, 403)

    def test_sensitive_success_fails_closed_when_audit_is_unavailable(self):
        """敏感详情已生成但成功审计失败时不得把正文返回给管理员。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.get_user_detail",
                return_value={"id": 3, "username": "alice"},
            ),
            patch(
                "app.admin.contracts.record_admin_audit_event",
                return_value=False,
            ),
            patch("app.admin.contracts.log_request_failure") as request_failure,
            app.test_client() as client,
        ):
            response = client.get("/api/admin/business/users/3")

        self.assertEqual(response.status_code, 503)
        body = response.get_json()
        self.assertEqual(body["code"], "audit_unavailable")
        self.assertNotIn("alice", response.get_data(as_text=True))
        request_failure.assert_called_once_with(
            ANY,
            status_code=503,
            reason_code="unavailable",
        )

    def test_missing_sensitive_record_is_audited_without_content(self):
        """404 目标也要记录拒绝结果，但审计载荷不能包含正文。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.get_user_detail",
                side_effect=AdminApiError("not_found", "记录不存在", 404),
            ),
            patch(
                "app.admin.contracts.record_admin_audit_event",
                return_value=True,
            ) as audit,
            app.test_client() as client,
        ):
            response = client.get("/api/admin/business/users/999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["code"], "not_found")
        self.assertEqual(audit.call_args.kwargs["error_code"], "not_found")
        self.assertIsNone(audit.call_args.kwargs["old_values"])
        self.assertIsNone(audit.call_args.kwargs["new_values"])

    def test_file_download_is_attachment_and_uses_atomic_service_audit(self):
        """文件成功下载必须使用附件头，并由同一事务服务完成计数与审计。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.download_file",
                return_value=(
                    io.BytesIO(b"safe,csv\n1,2\n"),
                    {"original_filename": "../report.csv", "mime_type": "text/csv"},
                ),
            ) as service,
            patch("app.admin.contracts.record_admin_audit_event") as outer_audit,
            app.test_client() as client,
        ):
            response = client.get("/api/admin/business/files/5/download")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("report.csv", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        service.assert_called_once_with(5, actor=ADMIN)
        outer_audit.assert_not_called()

    def test_deep_audit_request_is_csrf_protected_and_manual(self):
        """deep 审计只能通过带 CSRF 的写请求登记给 monitor。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.request_snapshot_refresh",
                return_value={
                    "groups": ["deep_audit"],
                    "requested_at": "2026-07-26T12:00:00.000Z",
                },
            ) as service,
            patch(
                "app.admin.contracts.record_admin_audit_event",
                return_value=True,
            ),
            app.test_client() as client,
        ):
            rejected = client.post("/api/admin/db/audit/run", json={"mode": "deep"})
            with client.session_transaction() as session:
                session["csrf_token"] = "audit-csrf"
            accepted = client.post(
                "/api/admin/db/audit/run",
                json={"mode": "deep"},
                headers={"X-CSRF-Token": "audit-csrf"},
            )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.get_json()["code"], "csrf_invalid")
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.get_json()["data"]["groups"], ["deep_audit"])
        service.assert_called_once_with(("deep_audit",))


if __name__ == "__main__":
    unittest.main()
