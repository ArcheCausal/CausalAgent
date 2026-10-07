"""第二阶段受管日志事件目录合同测试。"""

from __future__ import annotations

import logging

from app.agent.worker.event_adapter import (
    ERROR_CATEGORY_CHECKPOINT,
    ERROR_CATEGORY_INTERNAL,
    ERROR_CATEGORY_PROTOCOL,
    ERROR_CATEGORY_PROVIDER,
    ERROR_CATEGORY_RUNTIME_CONTRACT,
)
from observability.event_catalog import (
    ERROR_CATEGORY,
    EVENT_SPECS,
    validate_event_details,
)


EXPECTED_CODES = {
    "logging.serialization_failed",
    "logging.contract_invalid",
    "worker.slot.ready",
    "worker.slot.failed",
    "web.request.unhandled",
    "web.request.failed",
    "job.create.accepted",
    "job.create.replayed",
    "job.create.failed",
    "admin.audit.write_failed",
    "security.login.disabled_account",
    "auth.login.last_login_update_failed",
    "security.authorization.denied",
    "security.csrf.rejected",
    "security.reauthentication.failed",
    "security.session.revoked",
    "db.connection.failed",
    "db.replica.fallback",
    "db.replica.recovered",
    "db.query.slow",
    "worker.job.claimed",
    "worker.job.finished",
    "worker.job.interrupted",
    "worker.job.revoked",
    "worker.job.failed",
    "worker.job.cleanup_failed",
    "worker.lease.refresh_failed",
    "worker.lease.recovered",
    "job.node.timeout",
    "job.node.degraded",
    "job.postprocess.degraded",
    "chat.attachment.degraded",
    "rag.runtime.ready",
    "rag.sparse.ready",
    "rag.startup.unavailable",
    "rag.enrichment.degraded",
    "rag.multimodal.parse_failed",
    "mcp.tool.finished",
    "mcp.tool.failed",
    "mcp.tool.canceled",
    "mcp.tool.slow",
    "mcp.request.received",
    "mcp.request.rejected",
    "mcp.request.accepted",
    "mcp.cancel.finished",
    "mcp.client.call.started",
    "mcp.client.cancel.requested",
    "mcp.client.cancel.finished",
    "mcp.client.cancel.failed",
    "mcp.capacity.rejected",
    "mcp.process.recycled",
    "mcp.client.reconnected",
    "mcp.transport.failed",
    "monitor.snapshot.failed",
    "monitor.snapshot.recovered",
    "monitor.config.degraded",
    "monitor.config.recovered",
    "monitor.lock.failed",
    "monitor.lock.recovered",
    "agent.persistence.cleanup.succeeded",
    "agent.persistence.cleanup.failed",
    "agent.persistence.cleanup.runtime.degraded",
    "agent.persistence.cleanup.runtime.recovered",
    "analytics.public_preview.view",
    "analytics.public_preview.demo_open",
    "analytics.public_preview.send_click",
    "analytics.auth.panel_open",
    "analytics.auth.login_success",
}
EXPECTED_CODES.update(
    f"{service}.startup.{outcome}"
    for service in ("web", "worker", "monitor", "mcp", "maintenance")
    for outcome in ("ready", "failed")
)


def _sample_value(field: str, rule):
    if bool in rule.types:
        return False
    if rule.choices:
        return sorted(rule.choices, key=str)[0]
    if field == "method":
        return "GET"
    if field == "status_code":
        return 500
    if field == "statement_digest":
        return "a" * 64
    if field == "visitor_hash":
        return "b" * 64
    if field == "phases":
        return ["writer_abort"]
    if field == "reason_code":
        return "unavailable"
    if field == "violation":
        return "unknown_event"
    if field in {
        "attempt",
        "chunk_count",
        "documents",
        "failure_count",
        "final_attempt",
        "outbox_id",
        "consecutive_failures",
        "generation",
        "retry_ordinal",
        "retry_after_seconds",
        "structured_attempts",
        "timeout_seconds",
    }:
        return 1
    if field.endswith("_count") or field in {
        "affected_count",
        "duration_ms",
        "downtime_ms",
        "elapsed_ms",
        "input_bytes",
        "queue_wait_ms",
        "lag_seconds",
        "lease_epoch",
        "max_workers",
        "page_number",
        "image_index",
        "table_index",
        "slot_count",
        "timeout_ms",
        "tool_count",
        "vocabulary",
    }:
        return 0
    return "safe_token"


def test_catalog_has_exact_phase_two_codes_and_fixed_contract_shape():
    assert set(EVENT_SPECS) == EXPECTED_CODES
    for event_code, spec in EVENT_SPECS.items():
        assert spec.event_code == event_code
        assert spec.level in {
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        }, event_code
        assert spec.category in {"request", "lifecycle", "dependency", "security"}
        assert isinstance(spec.message, str) and spec.message
        assert spec.message.strip() == spec.message
        for rule in spec.details.values():
            if any(value_type in {int, float} for value_type in rule.types):
                assert rule.maximum is not None, event_code


def test_every_declared_detail_rule_accepts_a_safe_value_and_rejects_unknown_keys():
    for event_code, spec in EVENT_SPECS.items():
        details = {
            field: _sample_value(field, rule)
            for field, rule in spec.details.items()
        }
        resolved, safe, violation = validate_event_details(event_code, details)
        assert resolved is spec, event_code
        assert violation is None, (event_code, violation)
        assert safe == details or (not details and safe is None)

        _resolved, _safe, violation = validate_event_details(
            event_code,
            {"not_allowed": "hidden-value"},
        )
        assert violation == "unknown_detail"


def test_catalog_rejects_context_ids_inside_details_and_unknown_events():
    _spec, safe, violation = validate_event_details(
        "worker.job.finished",
        {"job_id": "job-1"},
    )
    assert safe is None
    assert violation == "context_in_details"

    spec, safe, violation = validate_event_details("not.registered", None)
    assert spec is None
    assert safe is None
    assert violation == "unknown_event"


def test_last_login_update_failure_has_stable_message_and_reason_contract():
    """登录时间写入失败事件只允许稳定原因码，不回显异常原文。"""
    event_code = "auth.login.last_login_update_failed"
    spec = EVENT_SPECS[event_code]

    assert spec.message == "登录后的最后登录时间记录失败"
    assert set(spec.details) == {"reason_code"}

    resolved, safe, violation = validate_event_details(
        event_code,
        {"reason_code": "unexpected_error"},
    )
    assert resolved is spec
    assert safe == {"reason_code": "unexpected_error"}
    assert violation is None


def test_worker_job_failed_accepts_stable_error_category_and_drops_absent_one():
    """失败事件接受稳定故障域；未分类时省略该键而不是写入非法值。"""
    event_code = "worker.job.failed"
    spec = EVENT_SPECS[event_code]
    assert "error_category" in spec.details

    resolved, safe, violation = validate_event_details(
        event_code,
        {
            "failure_phase": "graph_terminal",
            "reason_code": "rate_limited",
            "error_category": "provider_error",
            "attempt": 1,
            "duration_ms": 12,
        },
    )
    assert resolved is spec
    assert violation is None
    assert safe["error_category"] == "provider_error"

    _resolved, safe, violation = validate_event_details(
        event_code,
        {"reason_code": "node_error", "error_category": None},
    )
    assert violation is None
    assert safe == {"reason_code": "node_error"}

    _resolved, _safe, violation = validate_event_details(
        event_code,
        {"reason_code": "node_error", "error_category": "RuntimeError"},
    )
    assert violation == "invalid_detail_value"


def test_error_category_vocabulary_matches_the_worker_classifier():
    """目录白名单与 worker 分类常量是两处声明，必须逐值一致。"""
    assert ERROR_CATEGORY.choices == frozenset(
        {
            ERROR_CATEGORY_PROVIDER,
            ERROR_CATEGORY_PROTOCOL,
            ERROR_CATEGORY_CHECKPOINT,
            ERROR_CATEGORY_RUNTIME_CONTRACT,
            ERROR_CATEGORY_INTERNAL,
        }
    )
def test_mcp_cancel_failure_reasons_and_reconnect_lane_are_catalogued():
    for reason_code in (
        "control_capacity_timeout",
        "response_timeout",
        "transport_error",
        "invalid_response",
    ):
        _spec, safe, violation = validate_event_details(
            "mcp.client.cancel.failed",
            {"capability": "causal.pc", "reason_code": reason_code},
        )
        assert violation is None
        assert safe == {
            "capability": "causal.pc",
            "reason_code": reason_code,
        }

    for lane in ("execute", "control"):
        _spec, safe, violation = validate_event_details(
            "mcp.client.reconnected",
            {"generation": 1, "pool_lane": lane},
        )
        assert violation is None
        assert safe == {"generation": 1, "pool_lane": lane}
