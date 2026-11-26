"""处理失败经真实图、worker 终态构造和公共事件白名单传播；不连接数据库。"""

from typing import Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from Agent.data_processing.graph import dataset_failure_node
from app.agent.public_events import public_event_payload
from app.agent.worker.graph_runner import _build_final_result


class FailureState(TypedDict, total=False):
    data_processing_failure_code: str
    data_processing_failure_message: str
    messages: list[Any]


@pytest.mark.parametrize("code", [
    "parameter_extraction_failed", "processing_failed", "sandbox_unavailable",
    "candidate_missing", "question_limit_reached", "validation_budget_exhausted",
])
def test_processing_failure_emits_error_instead_of_success(code):
    builder = StateGraph(FailureState)
    builder.add_node("data_processing_failed", dataset_failure_node)
    builder.add_edge(START, "data_processing_failed")
    builder.add_edge("data_processing_failed", END)
    state = builder.compile().invoke({"data_processing_failure_code": code})
    event = _build_final_result(state, job_attempt=2)
    public = public_event_payload(event["type"], event)
    assert public["type"] == "error"
    assert "本次未启动因果分析" in public["message"]
    assert "data" not in public
    assert "_context_commit" not in event
    assert "attempt" not in public
    if code == "parameter_extraction_failed":
        assert "参数解析失败" in public["message"]


def test_normal_chat_remains_a_success_terminal():
    from langchain_core.messages import AIMessage

    state = {"messages": [AIMessage(content="正常回复", name="normal_chat")]}
    event = _build_final_result(state, job_attempt=1)
    assert event["type"] == "final_result"
    assert event["data"] == {"type": "text", "summary": "正常回复"}
