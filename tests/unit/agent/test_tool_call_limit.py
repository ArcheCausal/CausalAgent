"""直接验证真实次数限制器的消息边界，不替代模型或工具执行。"""

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from Agent.deep_agent.tool_call_limit import PendingToolCallLimitMiddleware


@pytest.mark.parametrize("status", ["success", "error"])
def test_resolved_structured_call_does_not_consume_execution_budget(status):
    messages = [
        AIMessage(content="", tool_calls=[{
            "id": "decision", "name": "FinalAnalysisDecision", "args": {},
        }]),
        ToolMessage(content="Schema validation completed.", tool_call_id="decision", status=status),
    ]
    state = {
        "messages": messages,
        "run_tool_call_count": {"__all__": 8},
        "thread_tool_call_count": {"__all__": 8},
    }
    middleware = PendingToolCallLimitMiddleware(run_limit=8, exit_behavior="end")

    assert middleware.after_model(state, None) is None
    assert state["messages"] is messages
    assert len(messages) == 2
    assert state["run_tool_call_count"] == {"__all__": 8}


def test_pending_call_still_consumes_execution_budget():
    state = {"messages": [AIMessage(content="", tool_calls=[{
        "id": "read", "name": "read_file", "args": {"file_path": "/context.txt"},
    }])]}
    update = PendingToolCallLimitMiddleware(run_limit=1, exit_behavior="end").after_model(state, None)

    assert update["run_tool_call_count"] == {"__all__": 1}
    assert update["thread_tool_call_count"] == {"__all__": 1}
    assert "messages" not in update
    assert "jump_to" not in update


def test_exhausted_mixed_batch_only_rejects_pending_call():
    calls = [
        {"id": "decision", "name": "FinalAnalysisDecision", "args": {}},
        {"id": "read", "name": "read_file", "args": {"file_path": "/context.txt"}},
    ]
    messages = [
        AIMessage(content="", tool_calls=calls),
        ToolMessage(content="Schema validation completed.", tool_call_id="decision"),
    ]
    state = {
        "messages": messages,
        "run_tool_call_count": {"__all__": 1},
        "thread_tool_call_count": {"__all__": 1},
    }
    original_message = messages[0].model_dump()
    update = PendingToolCallLimitMiddleware(run_limit=1, exit_behavior="end").after_model(state, None)

    assert update["jump_to"] == "end"
    assert update["run_tool_call_count"] == {"__all__": 2}
    assert update["thread_tool_call_count"] == {"__all__": 1}
    rejected = [message for message in update["messages"] if isinstance(message, ToolMessage)]
    assert [(message.tool_call_id, message.status) for message in rejected] == [("read", "error")]
    assert messages[0].model_dump() == original_message
    assert state["messages"] is messages


def test_previous_turn_receipt_cannot_resolve_current_call():
    state = {"messages": [
        ToolMessage(content="Earlier result.", tool_call_id="read"),
        AIMessage(content="", tool_calls=[{
            "id": "read", "name": "read_file", "args": {"file_path": "/context.txt"},
        }]),
    ]}
    update = PendingToolCallLimitMiddleware(run_limit=1, exit_behavior="end").after_model(state, None)

    assert update["run_tool_call_count"] == {"__all__": 1}
