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
    "interrupt": {"message", "question_id", "interaction"},
    "error": {"message"},
    "canceled": {"message"},
    "heartbeat": {"job_id"},
}


def public_event_payload(event_type: str, payload: Any) -> dict[str, Any]:
    """只保留指定事件类型允许进入普通用户协议的字段。"""
    source = payload if isinstance(payload, dict) else {}
    allowed = _COMMON_FIELDS | _EVENT_FIELDS.get(event_type, set())
    public = {key: source[key] for key in allowed if key in source}
    interaction = public.get("interaction")
    if isinstance(interaction, dict):
        public["interaction"] = _public_interaction(interaction)
    public["type"] = event_type
    return public


_INTERACTION_FIELDS = {
    "type",
    "interaction_type",
    "question_id",
    "message",
    "expires_at",
    "allow_other",
}
_OPTION_FIELDS = {"option_id", "label", "capability_id", "steps", "impact", "risks"}
_STEP_FIELDS = {"operation", "columns", "method"}


def _public_interaction(value: dict[str, Any]) -> dict[str, Any]:
    """只保留已登记的公开交互字段，拒绝把内部标识或运行时对象带出。"""

    public = {key: value[key] for key in _INTERACTION_FIELDS if key in value}
    raw_options = value.get("options")
    if isinstance(raw_options, list):
        options: list[dict[str, Any]] = []
        for raw in raw_options[:3]:
            if not isinstance(raw, dict):
                continue
            option = {key: raw[key] for key in _OPTION_FIELDS if key in raw}
            steps = option.get("steps")
            if isinstance(steps, list):
                option["steps"] = [
                    {key: step[key] for key in _STEP_FIELDS if key in step}
                    for step in steps[:8]
                    if isinstance(step, dict)
                ]
            for key in ("impact", "risks"):
                if isinstance(option.get(key), list):
                    option[key] = [str(item)[:600] for item in option[key][:6]]
            options.append(option)
        public["options"] = options
    return public
