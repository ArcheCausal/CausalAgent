"""公开预览统计接口的合同、边界、脱敏和业务隔离测试。"""

from __future__ import annotations

import ast
import hashlib
import hmac
import io
import json
import logging
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from flask import Flask


TEST_ENV = {
    "SECRET_KEY": "public-analytics-test-secret",
    "API_KEY": "public-analytics-test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "public-analytics-test-model",
    "MYSQL_HOST": "public-analytics-test-mysql",
    "MYSQL_USER": "public-analytics-test-user",
    "MYSQL_PASSWORD": "public-analytics-test-password",
    "MYSQL_DATABASE": "public-analytics-test-database",
}
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)


from app.analytics.routes import analytics_bp  # noqa: E402
from app.auth.routes import auth_bp  # noqa: E402
from app.request_context import register_request_context  # noqa: E402
from config.settings import settings  # noqa: E402
from observability.logging_runtime import configure_logging  # noqa: E402
from tests.support.authorization import patch_login_identity  # noqa: E402


VISITOR_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
ANALYTICS_PATH = "/api/analytics/events"
VISITOR_HASH_PURPOSE = b"causalagent.analytics.visitor:"


@contextmanager
def captured_web_logging(monkeypatch: pytest.MonkeyPatch):
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    try:
        configure_logging("web", "test", logging.INFO)
        yield stream
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if getattr(handler, "_causalagent_json_handler", False):
                handler.close()
        for handler in old_handlers:
            root.addHandler(handler)
        root.setLevel(old_level)


def _records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _build_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "public-analytics-route-secret"
    register_request_context(app)
    app.register_blueprint(analytics_bp)
    return app


def _expected_visitor_hash(visitor_id: str) -> str:
    return hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        VISITOR_HASH_PURPOSE + visitor_id.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def test_anonymous_preview_events_are_accepted_and_hashed_without_raw_identifier(monkeypatch):
    """未登录请求返回 202，日志只保留 HMAC 摘要和登记过的标识。"""
    app = _build_app()
    body = {
        "visitor_id": VISITOR_ID,
        "events": [
            {"event": "analytics.public_preview.view", "page": "home", "demo_key": "overview"},
            {"event": "analytics.auth.panel_open", "page": "home"},
        ],
    }

    with captured_web_logging(monkeypatch) as stream:
        with app.test_client() as client:
            response = client.post(ANALYTICS_PATH, json=body)
            payload = response.get_json()
            status = response.status_code
            cookies = response.headers.getlist("Set-Cookie")
        raw = stream.getvalue()

    assert status == 202
    assert payload == {"success": True, "accepted": 2}
    assert cookies == []
    assert VISITOR_ID not in raw

    records = _records(stream)
    expected_hash = _expected_visitor_hash(VISITOR_ID)
    assert [record["event_code"] for record in records] == [
        "analytics.public_preview.view",
        "analytics.auth.panel_open",
    ]
    assert records[0]["details"] == {
        "visitor_hash": expected_hash,
        "page": "home",
        "demo_key": "overview",
    }
    assert records[1]["details"] == {"visitor_hash": expected_hash, "page": "home"}
    assert all(record["request_id"] for record in records)
    assert all(record["level"] == "info" for record in records)


def test_visitor_hash_is_stable_per_browser_identity_and_distinct_between_them(monkeypatch):
    """同一匿名标识得到同一摘要，不同标识互不相同。"""
    app = _build_app()
    other_visitor = "0f8fad5b-d9cb-469f-a165-70867728950e"

    with captured_web_logging(monkeypatch) as stream:
        with app.test_client() as client:
            for visitor_id in (VISITOR_ID, VISITOR_ID, other_visitor):
                response = client.post(ANALYTICS_PATH, json={
                    "visitor_id": visitor_id,
                    "events": [
                        {"event": "analytics.public_preview.send_click", "page": "home", "demo_key": "report"},
                    ],
                })
                assert response.status_code == 202

    hashes = [record["details"]["visitor_hash"] for record in _records(stream)]
    assert hashes[0] == hashes[1] == _expected_visitor_hash(VISITOR_ID)
    assert hashes[2] == _expected_visitor_hash(other_visitor)
    assert hashes[0] != hashes[2]


def test_batch_with_any_invalid_event_is_rejected_without_partial_logs(monkeypatch):
    """非法事件整体拒绝：合法项也不产生日志。"""
    app = _build_app()
    cases = {
        "unknown_event": {
            "visitor_id": VISITOR_ID,
            "events": [
                {"event": "analytics.public_preview.view", "page": "home", "demo_key": "overview"},
                {"event": "analytics.auth.login_success", "page": "home", "demo_key": "overview"},
            ],
        },
        "unknown_field": {
            "visitor_id": VISITOR_ID,
            "events": [
                {"event": "analytics.auth.panel_open", "page": "home", "message": "用户输入正文"},
            ],
        },
        "missing_field": {
            "visitor_id": VISITOR_ID,
            "events": [{"event": "analytics.public_preview.view", "page": "home"}],
        },
        "invalid_page": {
            "visitor_id": VISITOR_ID,
            "events": [{"event": "analytics.auth.panel_open", "page": "admin"}],
        },
        "invalid_demo_key": {
            "visitor_id": VISITOR_ID,
            "events": [
                {"event": "analytics.public_preview.demo_open", "page": "home", "demo_key": "unknown"},
            ],
        },
        "invalid_event": {
            "visitor_id": VISITOR_ID,
            "events": ["analytics.public_preview.view"],
        },
    }

    with captured_web_logging(monkeypatch) as stream:
        with app.test_client() as client:
            for expected_code, body in cases.items():
                response = client.post(ANALYTICS_PATH, json=body)
                assert response.status_code == 400, expected_code
                assert response.get_json()["code"] == expected_code
        raw = stream.getvalue()

    assert raw == ""


def test_request_shape_boundaries_are_enforced_before_logging(monkeypatch):
    """请求体字段、访客标识、事件数量和体积各自有明确上限。"""
    app = _build_app()
    valid_event = {"event": "analytics.auth.panel_open", "page": "home"}
    oversized_events = [
        {"event": "analytics.public_preview.send_click", "page": "home", "demo_key": "graph"}
        for _ in range(11)
    ]
    cases = [
        ("invalid_request", 400, {"visitor_id": VISITOR_ID, "events": [valid_event], "timestamp": 1}),
        ("invalid_visitor_id", 400, {"visitor_id": "not-a-uuid", "events": [valid_event]}),
        ("invalid_events", 400, {"visitor_id": VISITOR_ID, "events": []}),
        ("invalid_events", 400, {"visitor_id": VISITOR_ID, "events": oversized_events}),
        ("invalid_json", 400, None),
        ("payload_too_large", 413, {
            "visitor_id": VISITOR_ID,
            "events": [{"event": "analytics.auth.panel_open", "page": "home", "padding": "x" * 9000}],
        }),
    ]

    with captured_web_logging(monkeypatch) as stream:
        with app.test_client() as client:
            for expected_code, expected_status, body in cases:
                if body is None:
                    response = client.post(
                        ANALYTICS_PATH,
                        data=b"{\"visitor_id\":",
                        content_type="application/json",
                    )
                else:
                    response = client.post(ANALYTICS_PATH, json=body)
                assert response.status_code == expected_status, expected_code
                assert response.get_json()["code"] == expected_code
        raw = stream.getvalue()

    assert raw == ""


def test_analytics_route_module_keeps_no_business_dependency():
    """统计入口只依赖观测边界，不导入数据库、Session、Job 或文件模块。"""
    source = Path("app/analytics/routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported == {
        "__future__",
        "hashlib",
        "hmac",
        "json",
        "logging",
        "re",
        "typing",
        "flask",
        "config.settings",
        "observability.event_catalog",
        "observability.logging_runtime",
    }


def test_rejected_and_accepted_requests_leave_the_session_untouched(monkeypatch):
    """统计请求既不建立登录态，也不改变现有会话内容。"""
    app = Flask(__name__)
    app.secret_key = "public-analytics-session-secret"
    register_request_context(app)
    app.register_blueprint(analytics_bp)

    with captured_web_logging(monkeypatch) as stream:
        with app.test_client() as client:
            rejected = client.post(ANALYTICS_PATH, json={"visitor_id": "not-a-uuid", "events": []})
            accepted = client.post(ANALYTICS_PATH, json={
                "visitor_id": VISITOR_ID,
                "events": [{"event": "analytics.auth.panel_open", "page": "home"}],
            })
            with client.session_transaction() as flask_session:
                session_keys = set(flask_session)

    assert rejected.status_code == 400
    assert accepted.status_code == 202
    assert session_keys == set()
    assert [record["event_code"] for record in _records(stream)] == [
        "analytics.auth.panel_open",
    ]


def test_login_success_event_is_written_by_the_backend_login_route(monkeypatch):
    """登录成功由认证路由直接记录，不依赖前端上报的成功状态。"""
    app = Flask(__name__)
    app.secret_key = "public-analytics-login-secret"
    register_request_context(app)
    app.register_blueprint(auth_bp)
    service_module = types.ModuleType("app.auth.service")
    service_module.find_user = lambda _username: {
        "id": 12,
        "username": "preview-user",
        "password_hash": "stored-hash",
        "role": "user",
        "is_active": True,
        "auth_version": 1,
    }
    service_module.record_successful_login = Mock(return_value=True)

    with captured_web_logging(monkeypatch) as stream:
        with (
            patch.dict(sys.modules, {"app.auth.service": service_module}),
            patch("app.auth.routes.bcrypt.checkpw", return_value=True),
            patch_login_identity(),
        ):
            with app.test_client() as client:
                response = client.post(
                    "/api/login",
                    json={"username": "preview-user", "password": "secret"},
                )

    assert response.status_code == 200
    records = _records(stream)
    assert [record["event_code"] for record in records] == ["analytics.auth.login_success"]
    assert records[0]["user_id"] == "12"
    assert records[0]["details"] is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
