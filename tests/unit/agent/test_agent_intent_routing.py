"""agent 意图判断与后端路由映射的单元测试。"""

from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from typing import get_args


for key, value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "mysql",
    "MYSQL_USER": "app",
    "MYSQL_PASSWORD": "password",
    "MYSQL_DATABASE": "causalagent",
}.items():
    os.environ.setdefault(key, value)


def _install_import_stubs():
    """隔离路由测试不需要的数据库依赖。"""
    agent_connect = types.ModuleType("Database.agent_connect")
    agent_connect.require_frozen_file_for_job = lambda *args, **kwargs: None
    sys.modules.setdefault("Database.agent_connect", agent_connect)


_install_import_stubs()

from Agent.causal_agent import nodes
from Agent.llm_structured_output import StructuredOutputError


CONTEXT_WITH_REPORT = {
    "analysis_context_id": "ctx-1",
    "filename": "sales.csv",
    "target": "销售额",
    "latest_report_message_id": 9,
    "latest_report_title": "促销效果报告",
}


def _state(message="普通问题", **updates):
    """构造 agent 节点所需的最小状态。"""
    state = {
        "messages": [HumanMessage(content=message)],
        "user_id": 1,
        "username": "tester",
        "session_id": "session-1",
        "job_id": "job-1",
        "file_summary": {
            "user_file_id": 11,
            "object_id": 22,
            "file_hash": "a" * 64,
            "filename": "data.csv",
        },
    }
    state.update(updates)
    return state


def _decision(intent, **overrides):
    payload = {"intent": intent}
    payload.update(overrides)
    return nodes.AgentIntentDecision(**payload)


@pytest.mark.parametrize(
    "intent,expected_route",
    [
        ("normal_chat", "normal_chat"),
        ("start_analysis", "fold"),
        ("answer_report", "inquiry_answer"),
        ("revise_report", "report"),
        ("rerun_analysis", "fold"),
        ("switch_analysis_context", "context_switch"),
        ("clarify", "inquiry_answer"),
    ],
)
def test_agent_maps_every_intent_to_a_backend_route(monkeypatch, intent, expected_route):
    """七种意图都由后端固定映射到图路由，模型不决定节点。"""
    async def fake_invoke(**kwargs):
        return _decision(
            intent,
            context_hint=(
                "sales.csv" if intent == "switch_analysis_context" else None
            ),
            clarification_question=(
                "请说明要回到哪一次分析" if intent == "clarify" else None
            ),
        )

    monkeypatch.setattr(nodes, "ainvoke_structured", fake_invoke)
    result = asyncio.run(
        nodes.agent_node(
            _state("普通问题", analysis_context=CONTEXT_WITH_REPORT),
            object(),
        )
    )

    assert result["route_decision"] == expected_route
    assert result["agent_decision"]["intent"] == intent


def test_revise_report_uses_context_regeneration_mode(monkeypatch):
    """报告修订必须标记为按当前上下文重新生成。"""
    async def fake_invoke(**kwargs):
        return _decision("revise_report")

    monkeypatch.setattr(nodes, "ainvoke_structured", fake_invoke)
    result = asyncio.run(
        nodes.agent_node(
            _state("把报告写得更简洁", analysis_context=CONTEXT_WITH_REPORT),
            object(),
        )
    )

    assert result["route_decision"] == "report"
    assert result["report_revision_mode"] == "full_regeneration_from_context"


def test_rerun_analysis_with_context_hint_resolves_context_first(monkeypatch):
    """重新分析如果点名了历史分析，先解析上下文再决定是否重跑。"""
    async def fake_invoke(**kwargs):
        return _decision("rerun_analysis", context_hint="sales.csv")

    monkeypatch.setattr(nodes, "ainvoke_structured", fake_invoke)
    result = asyncio.run(nodes.agent_node(_state("重新分析 sales.csv"), object()))

    assert result["route_decision"] == "context_switch"


def test_report_intents_fall_back_when_no_report_exists(monkeypatch):
    """没有可引用报告时不能进入报告回答或报告修订。"""
    for intent in ("answer_report", "revise_report"):
        async def fake_invoke(**kwargs):
            return _decision(intent)

        monkeypatch.setattr(nodes, "ainvoke_structured", fake_invoke)
        result = asyncio.run(nodes.agent_node(_state("报告里说了什么"), object()))

        assert result["route_decision"] == "normal_chat"
        assert result["report_revision_mode"] == "normal_generation"


def test_structured_output_failure_keeps_safe_normal_chat_route(monkeypatch):
    """结构化输出失败时必须回退到普通问答，不能进入分析或报告路径。"""
    async def fail_structured(**kwargs):
        raise StructuredOutputError(
            node_name="agent",
            schema_name="AgentIntentDecision",
            cause=ValueError("invalid tool arguments"),
        )

    monkeypatch.setattr(nodes, "ainvoke_structured", fail_structured)
    result = asyncio.run(nodes.agent_node(_state(), object()))

    assert result["route_decision"] == "normal_chat"
    assert "agent_decision" not in result


def test_clarify_intent_keeps_backend_question_for_answer_node(monkeypatch):
    """澄清问题只由模型文本提供，路由仍然由后端固定。"""
    async def fake_invoke(**kwargs):
        return _decision("clarify", clarification_question="您想回到哪一次分析？")

    monkeypatch.setattr(nodes, "ainvoke_structured", fake_invoke)
    result = asyncio.run(nodes.agent_node(_state("回到刚才那个"), object()))

    assert result["route_decision"] == "inquiry_answer"
    assert result["agent_decision"]["clarification_question"] == "您想回到哪一次分析？"


def test_intent_schema_cannot_carry_database_identifiers():
    """结构化 schema 只有意图、上下文线索和澄清问题三个字段。"""
    decision = nodes.AgentIntentDecision.model_validate(
        {
            "intent": "normal_chat",
            "analysis_context_id": "ctx-1",
            "input_user_file_id": 5,
        }
    )
    dumped = decision.model_dump()

    assert set(dumped) == {"intent", "context_hint", "clarification_question"}
    assert "ctx-1" not in str(dumped)


def test_intent_routes_cover_every_declared_intent():
    """意图映射表必须覆盖 schema 的全部取值。"""
    declared = nodes.AgentIntentDecision.model_fields["intent"].annotation
    allowed = set(get_args(declared))

    assert allowed == set(nodes.INTENT_ROUTES)
    assert set(nodes.INTENT_ROUTES.values()) <= {
        "fold",
        "report",
        "normal_chat",
        "inquiry_answer",
        "context_switch",
    }


def test_explicit_analysis_shortcut_ignores_other_named_files():
    """点名了其他文件时不能走确定性分析分支。"""
    assert nodes._is_explicit_causal_analysis_request(
        "请使用 data.csv 立即执行因果分析",
        "data.csv",
    )
    assert not nodes._is_explicit_causal_analysis_request(
        "请使用 sales.csv 立即执行因果分析",
        "data.csv",
    )


def test_inquiry_answer_outputs_backend_clarification_without_model_call():
    """澄清问题原样输出，不经过模型改写。"""
    class ExplodingLLM:
        """如果节点访问模型，测试立即失败。"""

        def __getattr__(self, name):  # noqa: D105
            raise AssertionError("澄清路径不应调用模型")

    state = {
        "messages": [HumanMessage(content="回到那个分析")],
        "context_resolution": {"clarification_question": "请说明是哪一次分析。"},
    }
    result = asyncio.run(nodes.inquiry_answer_node(state, ExplodingLLM()))

    assert result["messages"][0].content == "请说明是哪一次分析。"
    assert result["messages"][0].name == "inquiry_answer"

def test_intent_history_caps_single_message_and_total_length():
    """意图历史限制单条与整体长度，并优先保留更近的对话。"""
    messages = []
    for index in range(6):
        messages.append(HumanMessage(content=f"问题{index}"))
        messages.append(AIMessage(content="长回答" * 500))
    state = _state("当前问题", messages=messages)

    text = nodes._recent_history_text(state)
    lines = text.split("\n")

    assert len(text) <= (
        nodes.INTENT_HISTORY_TOTAL_CHARS + nodes.INTENT_HISTORY_MESSAGE_CHARS + 8
    )
    for line in lines:
        body = line.split("：", 1)[1]
        assert len(body) <= nodes.INTENT_HISTORY_MESSAGE_CHARS + 1
    assert "问题5" in text
    assert "问题0" not in text
