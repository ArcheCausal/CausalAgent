import os
import sys
import types
import unittest
from unittest.mock import Mock, patch

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

from app.auth.routes import auth_bp

from tests.support.authorization import ADMIN_PERMISSIONS, patch_login_identity


def build_app():
    """构建只注册认证蓝图的最小测试应用。"""
    app = Flask(__name__)
    app.secret_key = "session-test-secret"
    app.register_blueprint(auth_bp)
    return app


class SessionGuardTests(unittest.TestCase):
    """验证主库用户状态会立即约束已有浏览器会话。"""

    def test_deleted_user_session_is_cleared(self):
        """数据库中不存在的用户不能继续保持登录状态。"""
        app = build_app()
        with patch("app.auth.session_guard.find_user_by_id", return_value=None):
            with app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["user_id"] = 1
                    flask_session["username"] = "missing"

                response = client.get("/api/check_auth")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json(), {"isLoggedIn": False})
                with client.session_transaction() as flask_session:
                    self.assertNotIn("user_id", flask_session)
                    self.assertNotIn("username", flask_session)

    def test_disabled_user_session_is_cleared(self):
        """用户被禁用后，其旧 session 在下一请求立即失效。"""
        app = build_app()
        disabled_user = {
            "id": 2,
            "username": "disabled",
            "role": "admin",
            "is_active": False,
        }
        with patch("app.auth.session_guard.find_user_by_id", return_value=disabled_user):
            with app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["user_id"] = 2
                    flask_session["username"] = "disabled"

                response = client.get("/api/check_auth")

                self.assertEqual(response.get_json(), {"isLoggedIn": False})
                with client.session_transaction() as flask_session:
                    self.assertNotIn("user_id", flask_session)
                    self.assertNotIn("username", flask_session)

    def test_active_user_session_does_not_cache_role(self):
        """有效用户返回数据库角色，但 session 本身不保存角色副本。"""
        app = build_app()
        active_admin = {
            "id": 3,
            "username": "active-admin",
            "role": "admin",
            "is_active": True,
        }
        with (
            patch("app.auth.session_guard.find_user_by_id", return_value=active_admin),
            patch("app.auth.service.record_successful_login") as record_login,
            patch_login_identity(("user", "admin"), ADMIN_PERMISSIONS),
        ):
            with app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["user_id"] = 3
                    flask_session["username"] = "active-admin"

                response = client.get("/api/check_auth")

                payload = response.get_json()
                self.assertEqual(payload["isLoggedIn"], True)
                self.assertEqual(payload["username"], "active-admin")
                self.assertEqual(payload["role"], "admin")
                self.assertIn("admin.access", payload["permissions"])
                self.assertIsInstance(payload["csrf_token"], str)
                self.assertGreaterEqual(len(payload["csrf_token"]), 32)
                with client.session_transaction() as flask_session:
                    self.assertNotIn("role", flask_session)
                    self.assertEqual(flask_session["auth_version"], 1)
                    self.assertEqual(flask_session["csrf_token"], payload["csrf_token"])
        record_login.assert_not_called()

    def test_changed_auth_version_invalidates_old_session(self):
        """角色、状态或密码变更递增认证版本后，旧 Cookie 必须立即失效。"""
        app = build_app()
        changed_user = {
            "id": 6,
            "username": "changed-user",
            "role": "user",
            "is_active": True,
            "auth_version": 2,
        }
        with patch("app.auth.session_guard.find_user_by_id", return_value=changed_user):
            with app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["user_id"] = 6
                    flask_session["username"] = "changed-user"
                    flask_session["auth_version"] = 1

                response = client.get("/api/check_auth")

                self.assertEqual(response.get_json(), {"isLoggedIn": False})
                with client.session_transaction() as flask_session:
                    self.assertNotIn("user_id", flask_session)
                    self.assertNotIn("auth_version", flask_session)

    def test_disabled_user_cannot_login(self):
        """认证路由必须在密码校验前拒绝已禁用账号。"""
        app = build_app()
        service_module = types.ModuleType("app.auth.service")
        service_module.find_user = lambda _username: {
            "id": 4,
            "username": "disabled-login",
            "password_hash": "unused",
            "role": "user",
            "is_active": False,
        }
        service_module.record_successful_login = Mock(return_value=True)
        with patch.dict(sys.modules, {"app.auth.service": service_module}):
            with app.test_client() as client:
                response = client.post(
                    "/api/login",
                    json={"username": "disabled-login", "password": "secret"},
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"success": False, "error": "账号已被禁用"})
        service_module.record_successful_login.assert_not_called()

    def test_admin_login_returns_role_for_frontend_routing(self):
        """管理员登录成功响应应携带实时角色，供前端进入受保护后台。"""
        app = build_app()
        service_module = types.ModuleType("app.auth.service")
        service_module.find_user = lambda _username: {
            "id": 5,
            "username": "admin-login",
            "password_hash": "stored-hash",
            "role": "admin",
            "is_active": True,
        }
        service_module.record_successful_login = Mock(return_value=True)
        with (
            patch.dict(sys.modules, {"app.auth.service": service_module}),
            patch("app.auth.routes.bcrypt.checkpw", return_value=True),
            patch_login_identity(("user", "admin"), ADMIN_PERMISSIONS),
        ):
            with app.test_client() as client:
                response = client.post(
                    "/api/login",
                    json={"username": "admin-login", "password": "secret"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["success"], True)
        self.assertEqual(payload["username"], "admin-login")
        self.assertEqual(payload["role"], "admin")
        self.assertIn("rag_eval.access", payload["permissions"])
        self.assertEqual(payload["redirect_to"], "/admin/database")
        self.assertIsInstance(payload["csrf_token"], str)
        self.assertGreaterEqual(len(payload["csrf_token"]), 32)
        service_module.record_successful_login.assert_called_once_with(5)

    def test_last_login_write_failure_warns_but_keeps_login_successful(self):
        """最后登录时间写入失败时应返回稳定警告码并保留有效 Session。"""
        app = build_app()
        service_module = types.ModuleType("app.auth.service")
        service_module.find_user = lambda _username: {
            "id": 7,
            "username": "warning-login",
            "password_hash": "stored-hash",
            "role": "user",
            "is_active": True,
        }
        service_module.record_successful_login = Mock(return_value=False)
        with (
            patch.dict(sys.modules, {"app.auth.service": service_module}),
            patch("app.auth.routes.bcrypt.checkpw", return_value=True),
            patch_login_identity(),
        ):
            with app.test_client() as client:
                response = client.post(
                    "/api/login",
                    json={"username": "warning-login", "password": "secret"},
                )
                with client.session_transaction() as flask_session:
                    session_user_id = flask_session["user_id"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["warning_code"], "last_login_record_failed")
        self.assertEqual(session_user_id, 7)
        service_module.record_successful_login.assert_called_once_with(7)

    def test_wrong_password_does_not_record_last_login(self):
        """密码校验失败时不得更新最后登录时间或创建成功登录响应。"""
        app = build_app()
        service_module = types.ModuleType("app.auth.service")
        service_module.find_user = lambda _username: {
            "id": 10,
            "username": "wrong-password",
            "password_hash": "stored-hash",
            "role": "user",
            "is_active": True,
        }
        service_module.record_successful_login = Mock(return_value=True)
        with (
            patch.dict(sys.modules, {"app.auth.service": service_module}),
            patch("app.auth.routes.bcrypt.checkpw", return_value=False),
            app.test_client() as client,
        ):
            response = client.post(
                "/api/login",
                json={"username": "wrong-password", "password": "incorrect"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("warning_code", response.get_json())
        service_module.record_successful_login.assert_not_called()

    def test_login_only_returns_server_validated_admin_redirect(self):
        """登录接口只回显白名单管理页面，并对管理员保留安全默认落点。"""
        app = build_app()
        service_module = types.ModuleType("app.auth.service")
        service_module.find_user = lambda _username: {
            "id": 8,
            "username": "admin-login",
            "password_hash": "stored-hash",
            "role": "admin",
            "is_active": True,
        }
        service_module.record_successful_login = Mock(return_value=True)
        with (
            patch.dict(sys.modules, {"app.auth.service": service_module}),
            patch("app.auth.routes.bcrypt.checkpw", return_value=True),
            patch_login_identity(("user", "admin"), ADMIN_PERMISSIONS),
            app.test_client() as client,
        ):
            safe_response = client.post(
                "/api/login",
                json={
                    "username": "admin-login",
                    "password": "secret",
                    "next": "/admin/database/settings?ignored=true#ignored",
                },
            )
            unsafe_response = client.post(
                "/api/login",
                json={
                    "username": "admin-login",
                    "password": "secret",
                    "next": "https://evil.example/admin/database",
                },
            )

        self.assertEqual(
            safe_response.get_json()["redirect_to"],
            "/admin/database/settings",
        )
        self.assertEqual(
            unsafe_response.get_json()["redirect_to"],
            "/admin/database",
        )

    def test_normal_user_with_admin_return_target_reaches_authorization_boundary(self):
        """普通用户登录可恢复原管理 URL，但不能获得任何管理员权限。"""
        app = build_app()
        service_module = types.ModuleType("app.auth.service")
        service_module.find_user = lambda _username: {
            "id": 9,
            "username": "normal-login",
            "password_hash": "stored-hash",
            "role": "user",
            "is_active": True,
        }
        service_module.record_successful_login = Mock(return_value=True)
        with (
            patch.dict(sys.modules, {"app.auth.service": service_module}),
            patch("app.auth.routes.bcrypt.checkpw", return_value=True),
            patch_login_identity(),
            app.test_client() as client,
        ):
            response = client.post(
                "/api/login",
                json={
                    "username": "normal-login",
                    "password": "secret",
                    "next": "/admin/users",
                },
            )

        payload = response.get_json()
        self.assertEqual(payload["role"], "user")
        self.assertEqual(payload["redirect_to"], "/admin/users")


if __name__ == "__main__":
    unittest.main()
