import os
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


from app.rag_eval import routes  # noqa: E402
from app.request_context import register_request_context  # noqa: E402
from tests.support.authorization import (  # noqa: E402
    RAG_ADMIN_USER,
    authorized_as,
    csrf_verified,
)


def build_app():
    """注册 RAG 蓝图并启用请求上下文的最小应用。"""
    app = Flask(__name__)
    app.secret_key = "rag-eval-audit-secret"
    register_request_context(app)
    app.register_blueprint(routes.rag_eval_bp)
    return app


class RagEvalOperationAuditTests(unittest.TestCase):
    """发布、回滚和治理操作必须留下可检索的审计事件。"""

    def test_successful_publish_is_audited_with_actor_and_request_id(self):
        app = build_app()
        audit = Mock(return_value=True)
        with (
            authorized_as(RAG_ADMIN_USER),
            csrf_verified(),
            patch("app.rag_eval.routes.record_admin_audit_event", audit),
            patch(
                "app.rag_eval.routes.publish_current_config_to_production",
                return_value={"retrieval_profile": "active_current"},
            ),
        ):
            with app.test_client() as client:
                response = client.post(
                    "/api/rag_eval/production-config/publish",
                    json={},
                    headers={"X-Request-ID": "audit-request-1"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(audit.call_count, 1)
        event = audit.call_args.kwargs
        self.assertEqual(event["action"], "rag_eval.production_config.publish")
        self.assertEqual(event["result"], "success")
        self.assertEqual(event["target_type"], "rag_eval_operation")
        self.assertEqual(event["target_id"], "/api/rag_eval/production-config/publish")
        self.assertEqual(event["actor"], RAG_ADMIN_USER)
        self.assertEqual(event["request_id"], "audit-request-1")
        self.assertIsNone(event["error_code"])
        self.assertEqual(event["new_values"], {"method": "POST", "status_code": 200})

    def test_rejected_gate_check_is_audited_with_http_error_code(self):
        app = build_app()
        audit = Mock(return_value=True)
        with (
            authorized_as(RAG_ADMIN_USER),
            csrf_verified(),
            patch("app.rag_eval.routes.record_admin_audit_event", audit),
            patch.object(
                routes,
                "isolated_run_manager",
                Mock(check_release=Mock(side_effect=ValueError("缺少 ingestion_run_id"))),
            ),
        ):
            with app.test_client() as client:
                response = client.post(
                    "/api/rag_eval/multimodal/releases/gate-check",
                    json={},
                    headers={"X-Request-ID": "audit-request-2"},
                )

        self.assertEqual(response.status_code, 400)
        event = audit.call_args.kwargs
        self.assertEqual(event["action"], "rag_eval.release.gate_check")
        self.assertEqual(event["result"], "rejected")
        self.assertEqual(event["error_code"], "http_400")
        self.assertEqual(event["request_id"], "audit-request-2")

    def test_unexpected_publish_failure_is_audited_as_failed(self):
        app = build_app()
        audit = Mock(return_value=True)
        with (
            authorized_as(RAG_ADMIN_USER),
            csrf_verified(),
            patch("app.rag_eval.routes.record_admin_audit_event", audit),
            patch(
                "app.rag_eval.routes.publish_current_config_to_production",
                side_effect=RuntimeError("写正式配置失败"),
            ),
        ):
            with app.test_client() as client:
                response = client.post(
                    "/api/rag_eval/production-config/publish",
                    json={},
                    headers={"X-Request-ID": "audit-request-3"},
                )

        self.assertEqual(response.status_code, 500)
        event = audit.call_args.kwargs
        self.assertEqual(event["result"], "failed")
        self.assertEqual(event["error_code"], "http_500")

    def test_denied_request_never_reaches_the_audit_wrapper(self):
        app = build_app()
        audit = Mock(return_value=True)
        with (
            authorized_as(RAG_ADMIN_USER, ("dashboard.access",)),
            csrf_verified(),
            patch("app.rag_eval.routes.record_admin_audit_event", audit),
        ):
            with app.test_client() as client:
                response = client.post("/api/rag_eval/production-config/publish", json={})

        self.assertEqual(response.status_code, 403)
        audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

