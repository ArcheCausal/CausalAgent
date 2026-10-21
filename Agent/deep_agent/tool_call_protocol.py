"""补全模型参数 JSON 解析失败的工具调用协议。"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage


class ToolCallProtocolError(RuntimeError):
    """当前模型响应缺少可安全关联的工具调用身份。"""


def _call_id(call: Any) -> str:
    if not isinstance(call, Mapping):
        raise ToolCallProtocolError("tool call entry is not a mapping")
    value = call.get("id")
    if not isinstance(value, str) or not value or value != value.strip():
        raise ToolCallProtocolError("tool call id is missing or invalid")
    return value


def _json_error_location(call: Mapping[str, Any]) -> dict[str, int] | None:
    raw_arguments = call.get("args")
    if not isinstance(raw_arguments, str):
        return None
    try:
        json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return {"line": exc.lineno, "column": exc.colno}
    return None


def _invalid_call_message(call: Mapping[str, Any], call_id: str) -> ToolMessage:
    payload: dict[str, Any] = {
        "error_code": "TOOL_ARGUMENTS_INVALID_JSON",
        "executed": False,
        "message": "工具参数不是合法 JSON，工具未执行。",
        "next_action": "请按照工具 schema 重新生成调用。",
    }
    location = _json_error_location(call)
    if location is not None:
        payload["location"] = location

    name = call.get("name")
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        tool_call_id=call_id,
        name=name if isinstance(name, str) and name else None,
        status="error",
    )


class ToolCallProtocolMiddleware(AgentMiddleware):
    """为当前 AI 响应中的无效 JSON 调用补充安全错误返回。"""

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        del runtime
        messages = state.get("messages") if isinstance(state, Mapping) else None
        if not isinstance(messages, list):
            return None

        latest_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], AIMessage)
            ),
            None,
        )
        if latest_index is None:
            return None

        response = messages[latest_index]
        invalid_calls = response.invalid_tool_calls
        if not invalid_calls:
            return None

        valid_calls = response.tool_calls
        response_call_ids: set[str] = set()
        for call in [*valid_calls, *invalid_calls]:
            call_id = _call_id(call)
            if call_id in response_call_ids:
                raise ToolCallProtocolError(
                    "tool call id is duplicated in the current response"
                )
            response_call_ids.add(call_id)

        following_tool_messages = [
            message
            for message in messages[latest_index + 1 :]
            if isinstance(message, ToolMessage)
        ]
        returned_ids: set[str] = set()
        for message in following_tool_messages:
            if message.tool_call_id in response_call_ids:
                if message.tool_call_id in returned_ids:
                    raise ToolCallProtocolError(
                        "tool call has multiple outputs in the current response"
                    )
                returned_ids.add(message.tool_call_id)

        new_tool_messages = [
            _invalid_call_message(call, _call_id(call))
            for call in invalid_calls
            if _call_id(call) not in returned_ids
        ]
        update: dict[str, Any] = {"structured_response": None}
        if new_tool_messages:
            update["messages"] = new_tool_messages

        pending_valid_calls = [
            call
            for call in valid_calls
            if _call_id(call) not in returned_ids
        ]
        if not pending_valid_calls and not state.get("jump_to"):
            update["jump_to"] = "model"
        return update
