import os
import unittest
from pathlib import Path
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


from app.rag_eval import routes as rag_routes  # noqa: E402
from app.auth.rbac import (  # noqa: E402
    PERMISSION_RAG_EVAL_ACCESS,
    PERMISSION_RAG_EVAL_READ,
    PERMISSION_RAG_EVAL_RUN,
)


ADMIN = {"id": 1, "username": "rag-admin", "role": "admin", "is_active": True}
READ_REQUEST = "/api/rag_eval/status"
WRITE_REQUEST = "/api/rag_eval/profiles"


def build_app():
    """注册 RAG 蓝图的最小应用，用于验证访问守卫。"""
    app = Flask(__name__)
    app.secret_key = "rag-gate-test-secret"
    app.register_blueprint(rag_routes.rag_eval_bp)
    return app


class RagEvalGateTests(unittest.TestCase):
    """验证 /api/rag_eval 的访问守卫按方法区分读/运行权限。"""

    def _call_gate(self, app, path, *, method="GET"):
        with app.test_request_context(path, method=method):
            return rag_routes.enforce_rag_eval_access()

    def test_anonymous_request_returns_401(self):
        app = build_app()
        with patch("app.auth.authorization.get_current_session_user", return_value=None):
            payload, status = self._call_gate(app, READ_REQUEST)

        self.assertEqual(status, 401)
        self.assertEqual(payload.get_json()["code"], "auth_required")

    def test_missing_access_permission_returns_403(self):
        app = build_app()
        with (
            patch("app.auth.authorization.get_current_session_user", return_value=ADMIN),
            patch("app.auth.authorization.get_current_permissions", return_value=frozenset()),
        ):
            payload, status = self._call_gate(app, READ_REQUEST)

        self.assertEqual(status, 403)
        self.assertEqual(payload.get_json()["code"], "permission_denied")

    def test_read_and_run_permissions_depend_on_the_method(self):
        app = build_app()
        with (
            patch("app.auth.authorization.get_current_session_user", return_value=ADMIN),
            patch(
                "app.auth.authorization.get_current_permissions",
                return_value=frozenset({PERMISSION_RAG_EVAL_ACCESS, PERMISSION_RAG_EVAL_READ}),
            ),
            patch("app.rag_eval.routes.csrf_token_is_valid", return_value=True),
        ):
            allowed_read = self._call_gate(app, READ_REQUEST)
            denied_write = self._call_gate(app, WRITE_REQUEST, method="POST")

        self.assertIsNone(allowed_read)
        self.assertEqual(denied_write[1], 403)
        self.assertEqual(denied_write[0].get_json()["code"], "permission_denied")

        with (
            patch("app.auth.authorization.get_current_session_user", return_value=ADMIN),
            patch(
                "app.auth.authorization.get_current_permissions",
                return_value=frozenset({PERMISSION_RAG_EVAL_ACCESS, PERMISSION_RAG_EVAL_RUN}),
            ),
            patch("app.rag_eval.routes.csrf_token_is_valid", return_value=True),
        ):
            allowed_write = self._call_gate(app, WRITE_REQUEST, method="POST")

        self.assertIsNone(allowed_write)

    def test_write_requests_require_csrf_token(self):
        app = build_app()
        with (
            patch("app.auth.authorization.get_current_session_user", return_value=ADMIN),
            patch(
                "app.auth.authorization.get_current_permissions",
                return_value=frozenset({PERMISSION_RAG_EVAL_ACCESS, PERMISSION_RAG_EVAL_RUN}),
            ),
            patch("app.rag_eval.routes.csrf_token_is_valid", return_value=False),
        ):
            payload, status = self._call_gate(app, WRITE_REQUEST, method="POST")

        self.assertEqual(status, 403)
        self.assertEqual(payload.get_json()["code"], "csrf_invalid")

    def test_specific_permission_endpoints_are_exempt_from_method_check(self):
        app = build_app()
        publish_path = "/api/rag_eval/production-config/publish"
        with (
            patch("app.auth.authorization.get_current_session_user", return_value=ADMIN),
            patch(
                "app.auth.authorization.get_current_permissions",
                return_value=frozenset({PERMISSION_RAG_EVAL_ACCESS}),
            ),
            patch("app.rag_eval.routes.csrf_token_is_valid", return_value=True),
        ):
            with app.test_request_context(publish_path, method="POST"):
                self.assertIn(rag_routes.enforce_rag_eval_access.__name__, dir(rag_routes))
                self.assertIn(
                    "rag_eval.api_publish_production_config",
                    rag_routes.SPECIFIC_PERMISSION_ENDPOINTS,
                )
                self.assertIsNone(rag_routes.enforce_rag_eval_access())


class RagEvalPermissionWiringTests(unittest.TestCase):
    """高风险路由必须显式声明发布、回滚或治理权限。"""

    ROUTE_SOURCE = Path("app/rag_eval/routes.py")

    EXPECTED_DECORATORS = {
        "api_publish_strategy_profile": "PERMISSION_RAG_EVAL_PUBLISH",
        "api_publish_production_config": "PERMISSION_RAG_EVAL_PUBLISH",
        "api_multimodal_release_publish": "PERMISSION_RAG_EVAL_PUBLISH",
        "api_multimodal_release_rollback": "PERMISSION_RAG_EVAL_ROLLBACK",
        "api_multimodal_release_gate_check": "PERMISSION_RAG_EVAL_GOVERNANCE",
        "api_freeze_gold_v2": "PERMISSION_RAG_EVAL_GOVERNANCE",
        "api_bind_baseline_v2": "PERMISSION_RAG_EVAL_GOVERNANCE",
        "api_start_gold_v2_governance": "PERMISSION_RAG_EVAL_GOVERNANCE",
    }

    def test_high_risk_routes_declare_their_permission(self):
        lines = self.ROUTE_SOURCE.read_text(encoding="utf-8").splitlines()
        for function_name, permission_constant in self.EXPECTED_DECORATORS.items():
            with self.subTest(function_name=function_name):
                index = next(
                    position
                    for position, line in enumerate(lines)
                    if line.startswith(f"def {function_name}(")
                )
                decorators = [
                    line.strip()
                    for line in lines[max(0, index - 4):index]
                    if line.strip().startswith("@")
                ]
                self.assertIn(f"@require_permission({permission_constant})", decorators)
                self.assertTrue(
                    any(line.startswith("@audit_rag_eval_operation(") for line in decorators),
                    decorators,
                )

    def test_exempt_endpoint_list_matches_the_decorated_routes(self):
        expected = {f"rag_eval.{name}" for name in self.EXPECTED_DECORATORS}
        self.assertEqual(set(rag_routes.SPECIFIC_PERMISSION_ENDPOINTS), expected)


if __name__ == "__main__":
    unittest.main()
