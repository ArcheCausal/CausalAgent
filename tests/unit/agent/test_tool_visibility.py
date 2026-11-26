"""工具可见性纯函数与固定提示规则；不构造模型或执行器替身。"""

from copy import deepcopy

import pytest

from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent.prompts import build_deep_agent_system_prompt
from Agent.deep_agent.tool_visibility import filter_visible_tools, is_tool_visible
from Agent.deep_agent_tools.web_evidence_tool import build_default_web_evidence_tool


@pytest.mark.parametrize(
    ("enabled", "visible"),
    [(True, True), (False, False), (None, False), (1, False), ("true", False)],
)
def test_web_visibility_requires_explicit_true(enabled, visible):
    context = AgentRunContext(execution_guard=None, web_search_enabled=enabled)
    assert is_tool_visible("web_evidence_search", context) is visible
    assert is_tool_visible("causal_pc", context) is True
    assert context.web_search_enabled is enabled


def test_missing_context_hides_only_web():
    assert not is_tool_visible("web_evidence_search", None)
    assert is_tool_visible("FinalAnalysisDecision", None)


@pytest.mark.parametrize("enabled", [True, False, None])
def test_filter_preserves_order_objects_and_original_list(enabled):
    web = build_default_web_evidence_tool().to_langchain_tool()
    tools = [
        {"name": "read_file"},
        web,
        {"name": "causal_pc"},
        {"type": "function", "function": {"name": "web_evidence_search"}},
        {"name": "rag_evidence_search"},
        {"name": "edit_file"},
        {"name": "FinalAnalysisDecision"},
    ]
    original = list(tools)
    schemas = deepcopy([tool for tool in tools if isinstance(tool, dict)])
    context = AgentRunContext(execution_guard=None, web_search_enabled=enabled)
    selected = filter_visible_tools(tools, context)
    expected = original if enabled is True else [original[i] for i in (0, 2, 4, 5, 6)]
    assert selected is not tools
    assert len(selected) == len(expected)
    assert all(left is right for left, right in zip(selected, expected))
    assert tools == original
    assert [tool for tool in tools if isinstance(tool, dict)] == schemas


def test_filter_never_restores_removed_tools_and_contexts_are_independent():
    tools = [{"name": "web_evidence_search"}, {"name": "read_file"}]
    enabled = AgentRunContext(execution_guard=None, web_search_enabled=True)
    disabled = AgentRunContext(execution_guard=None, web_search_enabled=False)
    assert filter_visible_tools(tools, enabled) == tools
    reduced = filter_visible_tools(tools, disabled)
    assert reduced == [tools[1]]
    assert filter_visible_tools(reduced, enabled) == reduced
    assert filter_visible_tools(tools, enabled) == tools
    assert filter_visible_tools([], enabled) == []


def test_fixed_prompt_conditions_web_requirement_on_supplied_tools():
    prompt = build_deep_agent_system_prompt()
    assert "如果本次提供的工具包含 web_evidence_search" in prompt
    assert "同一次执行中已经完成的搜索可以继续使用" in prompt
    assert "需要补充证据时，可以再次调用" in prompt
