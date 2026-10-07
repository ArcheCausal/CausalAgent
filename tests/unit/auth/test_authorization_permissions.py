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


from app.auth.authorization import (  # noqa: E402
    AUTH_SIGN_IN_PATH,
    admin_login_url,
    get_current_permissions,
    has_permission,
    require_authenticated_user,
    require_permission,
    safe_return_target,
    sign_in_url,
)
from app.auth.rbac import (  # noqa: E402
    PERMISSION_DASHBOARD_ACCESS,
    PERMISSION_RAG_EVAL_ACCESS,
)


ACTIVE_USER = {"id": 5, "username": "operator", "role": "user", "is_active": True}


def build_app():
    """构建只包含页面与接口守卫的最小应用。"""
    app = Flask(__name__)
    app.secret_key = "authorization-test-secret"

    @app.route("/dashboard")
    @require_permission(PERMISSION_DASHBOARD_ACCESS, page=True)
    def dashboard_page():
        return "dashboard-shell"

    @app.route("/rag-eval")
    @require_permission(PERMISSION_RAG_EVAL_ACCESS, page=True)
    def rag_eval_page():
        return "rag-shell"

    @app.route("/api/rag_eval/example")
    @require_permission(PERMISSION_RAG_EVAL_ACCESS)
    def rag_eval_api():
        return {"success": True}

    @app.route("/api/identity")
    @require_authenticated_user
    def identity_api():
        return {
            "hasDashboard": has_permission(PERMISSION_DASHBOARD_ACCESS),
            "hasRagEval": has_permission(PERMISSION_RAG_EVAL_ACCESS),
        }

    return app


class ReturnTargetTests(unittest.TestCase):
    """验证登录回跳只接受已登记的同源页面。"""

    def test_registered_pages_are_accepted_without_query_or_fragment(self):
        accepted = {
            "/dashboard": "/dashboard",
            "/dashboard/": "/dashboard",
            "/dashboard/settings": "/dashboard/settings",
            "/dashboard/session/abc-123": "/dashboard/session/abc-123",
            "/rag-eval": "/rag-eval",
            "/admin": "/admin",
            "/admin/": "/admin",
            "/admin/database?tab=slow#digest": "/admin/database",
        }
        for raw_target, expected in accepted.items():
            with self.subTest(raw_target=raw_target):
                self.assertEqual(safe_return_target(raw_target), expected)

    def test_unregistered_or_external_targets_are_rejected(self):
        rejected = (
            None,
            "",
            "/",
            "/product",
            "/dashboard/unknown",
            "/dashboard/session/" + "x" * 80,
            "/dashboard/session/%2F%2Fevil",
            "/chat-next",
            "/rag_eval",
            "admin/database",
            "https://evil.example/dashboard",
            "//evil.example/dashboard",
            r"/dashboard\session",
            "/dashboard\nsession",
        )
        for raw_target in rejected:
            with self.subTest(raw_target=raw_target):
                self.assertIsNone(safe_return_target(raw_target))

    def test_sign_in_urls_keep_only_validated_targets(self):
        self.assertEqual(sign_in_url("/dashboard"), AUTH_SIGN_IN_PATH + "?next=%2Fdashboard")
        self.assertEqual(sign_in_url("/rag-eval"), AUTH_SIGN_IN_PATH + "?next=%2Frag-eval")
        self.assertEqual(sign_in_url("https://evil.example"), AUTH_SIGN_IN_PATH)
        self.assertEqual(admin_login_url("/admin/database"), AUTH_SIGN_IN_PATH + "?next=%2Fadmin%2Fdatabase")
        self.assertEqual(admin_login_url("/dashboard"), AUTH_SIGN_IN_PATH)


class PermissionEnforcementTests(unittest.TestCase):
    """验证未登录、缺少权限和拥有权限三种边界。"""

    def test_anonymous_page_requests_are_redirected_to_sign_in(self):
        app = build_app()
        with patch("app.auth.authorization.get_current_session_user", return_value=None):
            with app.test_client() as client:
                dashboard = client.get("/dashboard")
                rag_eval = client.get("/rag-eval")

        self.assertEqual(dashboard.status_code, 302)
        self.assertEqual(dashboard.headers["Location"], AUTH_SIGN_IN_PATH + "?next=%2Fdashboard")
        self.assertEqual(rag_eval.status_code, 302)
        self.assertEqual(rag_eval.headers["Location"], AUTH_SIGN_IN_PATH + "?next=%2Frag-eval")

    def test_anonymous_api_requests_return_401(self):
        app = build_app()
        with patch("app.auth.authorization.get_current_session_user", return_value=None):
            with app.test_client() as client:
                response = client.get("/api/rag_eval/example")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "auth_required")

    def test_missing_permission_returns_api_403_and_controlled_page(self):
        app = build_app()
        with (
            patch("app.auth.authorization.get_current_session_user", return_value=ACTIVE_USER),
            patch(
                "app.auth.authorization.get_current_permissions",
                return_value=frozenset({PERMISSION_DASHBOARD_ACCESS}),
            ),
        ):
            with app.test_client() as client:
                api_response = client.get("/api/rag_eval/example")
                page_response = client.get("/rag-eval")
                allowed_page = client.get("/dashboard")

        self.assertEqual(api_response.status_code, 403)
        self.assertEqual(api_response.get_json()["code"], "permission_denied")
        self.assertEqual(page_response.status_code, 403)
        self.assertEqual(page_response.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(page_response.headers["Cache-Control"], "no-store")
        self.assertIn("无访问权限", page_response.get_data(as_text=True))
        self.assertIn(PERMISSION_RAG_EVAL_ACCESS, page_response.get_data(as_text=True))
        self.assertEqual(allowed_page.status_code, 200)
        self.assertEqual(allowed_page.get_data(as_text=True), "dashboard-shell")

    def test_disabled_user_is_treated_as_anonymous(self):
        app = build_app()
        disabled_user = {**ACTIVE_USER, "is_active": False}
        with patch("app.auth.authorization.get_current_session_user", return_value=disabled_user):
            with app.test_client() as client:
                response = client.get("/api/rag_eval/example")

        self.assertEqual(response.status_code, 401)

    def test_permissions_are_resolved_once_per_request_and_not_reused_across_requests(self):
        app = build_app()
        with (
            patch("app.auth.authorization.get_current_session_user", return_value=ACTIVE_USER),
            patch(
                "app.auth.authorization.get_user_permissions",
                return_value=frozenset({PERMISSION_DASHBOARD_ACCESS}),
            ) as lookup,
        ):
            with app.test_client() as client:
                first_response = client.get("/api/identity")
                second_response = client.get("/api/identity")

        self.assertEqual(first_response.get_json(), {"hasDashboard": True, "hasRagEval": False})
        self.assertEqual(second_response.get_json(), {"hasDashboard": True, "hasRagEval": False})
        # 每个请求解析一次权限；同一请求内的多次判断复用同一结果。
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(lookup.call_args.args, (5,))

    def test_anonymous_permissions_are_empty_without_extra_lookup(self):
        app = build_app()
        with (
            patch("app.auth.authorization.get_current_session_user", return_value=None),
            patch("app.auth.authorization.get_user_permissions") as lookup,
            app.test_request_context("/api/identity"),
        ):
            self.assertEqual(get_current_permissions(), frozenset())
            self.assertEqual(get_current_permissions(), frozenset())

        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
