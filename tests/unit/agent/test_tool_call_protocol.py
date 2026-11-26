"""Deep Agent 无效工具参数的调用关联与路由测试。"""

from __future__ import annotations

import json

import pytest
from langchain.agents.factory import _get_can_jump_to, _make_model_to_tools_edge
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai.chat_models.base import _construct_responses_api_input
from langgraph.graph.message import add_messages

from Agent.deep_agent.tool_call_protocol import (
    ToolCallProtocolError,
    ToolCallProtocolMiddleware,
)
from Agent.deep_agent.finalization import (
    StructuredResponseError,
    validate_structured_response,
)


def _invalid_call(call_id: str, name: str = "causal_pc") -> dict[str, str]:
    return {
        "name": name,
        "args": '{"public_decision": summary:',
        "id": call_id,
        "error": "Error parsing arguments as JSON",
    }


def _function_call_block(name: str, call_id: str, arguments: str) -> dict[str, str]:
    return {
        "type": "function_call",
        "name": name,
        "call_id": call_id,
        "arguments": arguments,
    }


def _model_to_tools(*, structured_output_tools: dict | None = None):
    return _make_model_to_tools_edge(
        model_destination="budget.before_model",
        structured_output_tools=structured_output_tools or {},
        end_destination="end",
    )


def test_after_model_hook_declares_model_jump_with_langchain_factory() -> None:
    middleware = ToolCallProtocolMiddleware()

    assert _get_can_jump_to(middleware, "after_model") == ["model"]


def test_all_invalid_calls_get_error_outputs_and_return_to_model() -> None:
    raw_arguments = '{"public_decision": summary:'
    call_id = "invalid-pc"
    response = AIMessage(
        content=[_function_call_block("causal_pc", call_id, raw_arguments)],
        invalid_tool_calls=[_invalid_call(call_id)],
    )
    state = {
        "messages": [response],
        "structured_response": {"summary": "premature decision"},
    }

    update = ToolCallProtocolMiddleware().after_model(state, None)

    assert update is not None
    assert update["structured_response"] is None
    assert update["jump_to"] == "model"
    with pytest.raises(StructuredResponseError, match="structured_response is required"):
        validate_structured_response(update["structured_response"])
    assert len(update["messages"]) == 1
    error_message = update["messages"][0]
    assert isinstance(error_message, ToolMessage)
    assert error_message.tool_call_id == call_id
    assert error_message.name == "causal_pc"
    assert error_message.status == "error"
    payload = json.loads(error_message.content)
    assert payload == {
        "error_code": "TOOL_ARGUMENTS_INVALID_JSON",
        "executed": False,
        "location": {"line": 1, "column": 21},
        "message": "工具参数不是合法 JSON，工具未执行。",
        "next_action": "请按照工具 schema 重新生成调用。",
    }
    assert raw_arguments not in error_message.content

    messages = add_messages(state["messages"], update["messages"])
    route = _model_to_tools()(
        {"messages": messages, "jump_to": update["jump_to"], "structured_response": None}
    )
    assert route == "budget.before_model"

    serialized = _construct_responses_api_input(messages)
    serialized_call_ids = {
        item["call_id"] for item in serialized if item.get("type") == "function_call"
    }
    serialized_output_ids = {
        item["call_id"]
        for item in serialized
        if item.get("type") == "function_call_output"
    }
    assert serialized_call_ids == {call_id}
    assert serialized_output_ids == {call_id}

    repeated_update = ToolCallProtocolMiddleware().after_model(
        {"messages": messages, "structured_response": None, "jump_to": "model"}, None
    )
    assert repeated_update == {"structured_response": None}


def test_mixed_response_keeps_valid_tool_pending_and_completes_both_ids() -> None:
    invalid_id = "invalid-pc"
    valid_id = "valid-cdfm"
    raw_invalid_arguments = '{"public_decision": summary:'
    raw_valid_arguments = '{"threshold": 0.5}'
    response = AIMessage(
        content=[
            _function_call_block("causal_pc", invalid_id, raw_invalid_arguments),
            _function_call_block("causal_cdfm", valid_id, raw_valid_arguments),
        ],
        tool_calls=[
            {
                "name": "causal_cdfm",
                "args": {"threshold": 0.5},
                "id": valid_id,
                "type": "tool_call",
            }
        ],
        invalid_tool_calls=[_invalid_call(invalid_id)],
    )
    state = {"messages": [response], "structured_response": {"summary": "pending"}}

    update = ToolCallProtocolMiddleware().after_model(state, None)

    assert update is not None
    assert update["structured_response"] is None
    assert "jump_to" not in update
    assert [message.tool_call_id for message in update["messages"]] == [invalid_id]

    messages = add_messages(state["messages"], update["messages"])
    route = _model_to_tools()(
        {"messages": messages, "structured_response": None}
    )
    assert len(route) == 1
    assert route[0].node == "tools"
    assert route[0].arg[0]["id"] == valid_id

    # 这里直接给路由器提供一个已完成的工具返回，不运行替代执行器。
    tool_result = ToolMessage(
        content='{"status":"not_ready","safe_error_code":"ALGORITHM_NOT_READY"}',
        tool_call_id=valid_id,
        name="causal_cdfm",
        status="error",
    )
    messages = add_messages(messages, [tool_result])
    next_route = _model_to_tools()(
        {"messages": messages, "structured_response": None}
    )
    assert next_route == "budget.before_model"

    serialized = _construct_responses_api_input(messages)
    serialized_call_ids = {
        item["call_id"] for item in serialized if item.get("type") == "function_call"
    }
    serialized_output_ids = {
        item["call_id"]
        for item in serialized
        if item.get("type") == "function_call_output"
    }
    assert serialized_call_ids == {invalid_id, valid_id}
    assert serialized_output_ids == {invalid_id, valid_id}


def test_multiple_invalid_calls_keep_independent_ids() -> None:
    response = AIMessage(
        content=[
            _function_call_block("causal_pc", "pc-id", '{"broken":'),
            _function_call_block("causal_cdfm", "cdfm-id", '{"broken":'),
        ],
        invalid_tool_calls=[
            _invalid_call("pc-id", "causal_pc"),
            _invalid_call("cdfm-id", "causal_cdfm"),
        ],
    )

    update = ToolCallProtocolMiddleware().after_model({"messages": [response]}, None)

    assert update is not None
    assert [message.tool_call_id for message in update["messages"]] == [
        "pc-id",
        "cdfm-id",
    ]
    assert [message.name for message in update["messages"]] == [
        "causal_pc",
        "causal_cdfm",
    ]


@pytest.mark.parametrize(
    "valid_calls,invalid_calls",
    [
        ([], [_invalid_call("")]),
        (
            [{"id": "same-id", "name": "causal_cdfm", "args": {}}],
            [_invalid_call("same-id")],
        ),
        (
            [{"id": None, "name": "causal_cdfm", "args": {}}],
            [_invalid_call("invalid-id")],
        ),
        ([], [_invalid_call("same-id"), _invalid_call("same-id")]),
    ],
)
def test_missing_or_ambiguous_ids_fail_closed(valid_calls, invalid_calls) -> None:
    response = AIMessage(
        content="",
        tool_calls=valid_calls,
        invalid_tool_calls=invalid_calls,
    )

    with pytest.raises(ToolCallProtocolError):
        ToolCallProtocolMiddleware().after_model({"messages": [response]}, None)


def test_protocol_hook_ignores_schema_errors_and_older_invalid_messages() -> None:
    older_response = AIMessage(
        content="",
        invalid_tool_calls=[_invalid_call("old-invalid")],
    )
    current_response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "causal_pc",
                "args": {"unsupported": True},
                "id": "schema-invalid",
                "type": "tool_call",
            }
        ],
    )

    update = ToolCallProtocolMiddleware().after_model(
        {"messages": [older_response, current_response]}, None
    )

    assert update is None
    route = _model_to_tools()(
        {"messages": [older_response, current_response], "structured_response": None}
    )
    assert len(route) == 1
    assert route[0].arg[0]["id"] == "schema-invalid"


def test_existing_output_is_not_duplicated_and_stop_decision_is_preserved() -> None:
    call_id = "invalid-pc"
    response = AIMessage(
        content="",
        invalid_tool_calls=[_invalid_call(call_id)],
    )
    prior_output = ToolMessage(
        content='{"error_code":"TOOL_ARGUMENTS_INVALID_JSON","executed":false}',
        tool_call_id=call_id,
        name="causal_pc",
        status="error",
    )
    state = {
        "messages": [response, prior_output],
        "structured_response": {"summary": "premature"},
        "jump_to": "end",
    }

    update = ToolCallProtocolMiddleware().after_model(state, None)

    assert update == {"structured_response": None}
    assert _model_to_tools()(
        {"messages": state["messages"], "jump_to": state["jump_to"]}
    ) == "end"
