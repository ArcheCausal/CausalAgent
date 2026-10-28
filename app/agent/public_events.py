"""普通用户可见的 analysis Job 事件字段白名单。"""

from __future__ import annotations

from itertools import islice
import re
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
    "algorithm_name",
    "expires_at",
    "allow_other",
}
_OPTION_FIELDS = {"option_id", "label", "capability_id", "steps", "impact", "risks"}
_STEP_FIELDS = {"operation", "columns", "method"}


def _public_interaction(value: dict[str, Any]) -> dict[str, Any]:
    """只保留已登记的公开交互字段，拒绝把内部标识或运行时对象带出。"""

    public = {key: value[key] for key in _INTERACTION_FIELDS if key in value}
    plan_version = value.get("plan_version")
    if isinstance(plan_version, str) and re.fullmatch(r"[0-9a-f]{64}", plan_version):
        public["plan_version"] = plan_version
    plan = _public_processing_plan(value.get("plan"))
    if plan is not None:
        public["plan"] = plan
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


def _public_plan_strings(value: Any) -> list[str]:
    """只公开有限数量的文字条目，不把对象转换为文字。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [item[:600] for item in value[:12] if isinstance(item, str)]


def _public_processing_plan(value: Any) -> dict[str, Any] | None:
    """按 DataProcessingPlanSummary 重建摘要，禁止透传脚本和内部字段。

    保留契约的必填字段和集合数量限制；公开条目另限 600 字符，
    变量名限 255 字符，超长变量名丢弃以避免截断后混淆变量身份。
    """
    if not isinstance(value, dict):
        return None
    summary = value.get("summary")
    observation_unit = value.get("observation_unit")
    if not isinstance(summary, str) or not summary:
        return None
    if not isinstance(observation_unit, str) or not observation_unit:
        return None

    transformations = _public_plan_strings(value.get("transformations"))
    raw_semantics = value.get("variable_semantics")
    if not transformations or not isinstance(raw_semantics, dict):
        return None
    variable_semantics = {
        name: description[:600]
        for name, description in islice(raw_semantics.items(), 128)
        if isinstance(name, str)
        and len(name) <= 255
        and isinstance(description, str)
    }
    if not variable_semantics:
        return None

    public = {
        "summary": summary[:1200],
        "observation_unit": observation_unit[:500],
        "transformations": transformations,
        "variable_semantics": variable_semantics,
        "limitations": _public_plan_strings(value.get("limitations")),
    }
    if isinstance(value.get("role_columns"), dict):
        public["role_columns"] = {
            role: column
            for role, column in value["role_columns"].items()
            if role in {"target", "treatment"}
            and (column is None or isinstance(column, str) and 0 < len(column) <= 255)
        }
    return public
