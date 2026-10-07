"""普通用户可见的 analysis Job 事件字段白名单。"""

from __future__ import annotations

from typing import Any


_COMMON_FIELDS = {"type", "step_id", "node_name", "title"}
_EVENT_FIELDS = {
    "node_start": set(),
    "progress": {"summary"},
    "decision": {"summary", "decision_kind", "tool_name", "confidence"},
    "tool_call_start": {"tool_name", "argument_keys"},
    "tool_call_result": {"tool_name", "summary", "status", "safe_error_code"},
    "node_retry": {"message", "discard_stream_id"},
    "node_end": {"duration", "status", "message"},
    "text_delta": {"stream_id", "sequence", "delta"},
    "decision_delta": {
        "stream_id",
        "sequence",
        "delta",
        "decision_kind",
        "tool_name",
    },
    "final_result": {"data"},
    "interrupt": {"message", "question_id"},
    "error": {"message"},
    "canceled": {"message"},
    "heartbeat": {"job_id"},
}


def public_event_payload(event_type: str, payload: Any) -> dict[str, Any]:
    """只保留指定事件类型允许进入普通用户协议的字段。"""
    source = payload if isinstance(payload, dict) else {}
    allowed = _COMMON_FIELDS | _EVENT_FIELDS.get(event_type, set())
    public = {key: source[key] for key in allowed if key in source}
    public["type"] = event_type
    return public
