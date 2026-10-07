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


from app.admin.routes import admin_bp
from app.request_context import register_request_context


ADMIN = {
    "id": 7,
    "username": "admin",
    "role": "admin",
    "is_active": True,
    "auth_version": 1,
}


from tests.support.authorization import (
    ADMIN_PERMISSIONS,
    USER_PERMISSIONS,
    authorized_as,
)

def build_app():
    """构造只注册管理员 API 和 request ID 的最小 Flask 应用。"""
    app = Flask(__name__)
    app.secret_key = "admin-write-api-test"
    register_request_context(app)
    app.register_blueprint(admin_bp)
    return app


class AdminWriteApiTests(unittest.TestCase):
    """验证 3.2 写接口继续复用现有权限、CSRF 和业务命名空间。"""

    def test_user_operation_requires_csrf_and_forwards_idempotency_key(self):
        """用户写操作缺少 CSRF 时拒绝，成功时必须透传幂等键。"""
        app = build_app()
        payload = {
            "operation_id": "operation-1",
            "operation_type": "user.set_active",
            "target_count": 1,
            "items": [],
            "replayed": False,
        }
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.execute_user_operation",
                return_value=payload,
            ) as service,
            patch(
                "app.admin.contracts.record_admin_audit_event",
                return_value=True,
            ),
            app.test_client() as client,
        ):
            rejected = client.post(
                "/api/admin/business/users/operations",
                json={
                    "action": "set_active",
                    "target_ids": [3],
                    "value": False,
                },
                headers={"Idempotency-Key": "1234567890abcdef"},
            )
            with client.session_transaction() as flask_session:
                flask_session["csrf_token"] = "write-csrf"
            accepted = client.post(
                "/api/admin/business/users/operations",
                json={
                    "action": "set_active",
                    "target_ids": [3],
                    "value": False,
                },
                headers={
                    "X-CSRF-Token": "write-csrf",
                    "Idempotency-Key": "1234567890abcdef",
                },
            )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.get_json()["code"], "csrf_invalid")
        self.assertEqual(accepted.status_code, 200)
        service.assert_called_once()
        self.assertEqual(
            service.call_args.kwargs["idempotency_key"],
            "1234567890abcdef",
        )
        self.assertEqual(service.call_args.kwargs["actor"], ADMIN)

    def test_user_delete_impact_and_delete_use_existing_user_path(self):
        """用户删除预览和 DELETE 必须挂在现有用户资源路径下。"""
        app = build_app()
        impact = {
            "user": {"id": 3, "username": "alice"},
            "impact": {"sessions": 2},
            "can_delete": True,
            "blockers": [],
        }
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.get_user_delete_impact",
                return_value=impact,
            ) as preview_service,
            patch(
                "app.admin.routes.delete_managed_user",
                return_value={
                    "operation_id": "operation-2",
                    "deleted": True,
                    "replayed": False,
                },
            ) as delete_service,
            patch(
                "app.admin.contracts.record_admin_audit_event",
                return_value=True,
            ),
            app.test_client() as client,
        ):
            preview = client.get("/api/admin/business/users/3/delete-impact")
            with client.session_transaction() as flask_session:
                flask_session["csrf_token"] = "delete-csrf"
            deleted = client.delete(
                "/api/admin/business/users/3",
                json={
                    "confirm_username": "alice",
                    "reauth_password": "not-logged",
                    "confirmed": True,
                },
                headers={
                    "X-CSRF-Token": "delete-csrf",
                    "Idempotency-Key": "delete-user-123456",
                },
            )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        preview_service.assert_called_once_with(3, actor=ADMIN)
        delete_service.assert_called_once()
        self.assertEqual(delete_service.call_args.args[0], 3)
        self.assertEqual(
            delete_service.call_args.kwargs["idempotency_key"],
            "delete-user-123456",
        )

    def test_file_delete_never_returns_request_password(self):
        """文件物理删除响应只返回去敏操作结果。"""
        app = build_app()
        with (
            authorized_as(ADMIN),
            patch(
                "app.admin.routes.delete_managed_file",
                return_value={
                    "operation_id": "operation-3",
                    "file_id": 9,
                    "deleted": True,
                    "blob_deleted": True,
                    "replayed": False,
                },
            ),
            patch(
                "app.admin.contracts.record_admin_audit_event",
                return_value=True,
            ),
            app.test_client() as client,
        ):
            with client.session_transaction() as flask_session:
                flask_session["csrf_token"] = "file-csrf"
            response = client.delete(
                "/api/admin/business/files/9",
                json={
                    "confirm_filename": "report.csv",
                    "reauth_password": "admin-secret",
                    "confirmed": True,
                },
                headers={
                    "X-CSRF-Token": "file-csrf",
                    "Idempotency-Key": "delete-file-123456",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("admin-secret", response.get_data(as_text=True))
        self.assertTrue(response.get_json()["data"]["blob_deleted"])


if __name__ == "__main__":
    unittest.main()
