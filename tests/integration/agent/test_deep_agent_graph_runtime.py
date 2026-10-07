"""目标依赖存在时验证真实 Deep Agents graph 的最小构造契约。"""

from __future__ import annotations

import pytest


deepagents = pytest.importorskip("deepagents")
pytest.importorskip("langchain.agents.middleware")


def test_real_deep_agent_graph_builds_without_default_subagent() -> None:
    from deepagents.backends import StateBackend
    from langchain_openai import ChatOpenAI
    from langgraph.store.memory import InMemoryStore

    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent.graph import DeepAgentGraphConfig, build_deep_agent
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.deep_agent_tools import RagEvidenceTool

    class Retriever:
        def get_evidence(self, query, *, max_contexts=None):
            return {"status": "no_relevant_evidence", "evidence": []}

    model = ChatOpenAI(
        api_key="test-only",
        base_url="https://example.invalid/v1",
        model="deepseek-v4-flash",
    )
    graph = build_deep_agent(
        model=model,
        domain_tools=[RagEvidenceTool(Retriever())],
        backend=StateBackend(),
        store=InMemoryStore(),
        config=DeepAgentGraphConfig(context_window_tokens=4096),
    )

    assert type(graph).__name__ == "CompiledStateGraph"
    node_names = set(graph.get_graph().nodes)
    assert "SubAgentMiddleware.before_agent" not in node_names
    assert "SummarizationMiddleware.before_model" in node_names
    assert "ModelCallLimitMiddleware.before_model" in node_names
    assert "ToolCallLimitMiddleware.after_model" in node_names
    assert ProjectDeepAgentState.__annotations__["algorithm_results"]
    assert AgentRunContext.__dataclass_fields__["trusted_identity"]


def test_real_deep_agent_graph_wires_static_algorithm_registry() -> None:
    """真实 Deep Agents 构造必须暴露三项静态算法并挂上 dispatch 边界。"""
    from deepagents.backends import StateBackend
    from langchain_openai import ChatOpenAI
    from langgraph.store.memory import InMemoryStore

    from Agent.deep_agent.graph import DeepAgentGraphConfig, build_deep_agent
    from Agent.deep_agent_tools import (
        DEFAULT_ALGORITHM_SPECS,
        FakeAlgorithmExecutor,
        build_algorithm_tools,
        build_default_registry,
    )
    from Agent.deep_agent_tools.adapters import build_default_adapters

    backend = StateBackend()
    executor = FakeAlgorithmExecutor()
    registry = build_default_registry(
        build_default_adapters(executor=executor, raw_backend=backend)
    )
    model = ChatOpenAI(
        api_key="test-only",
        base_url="https://example.invalid/v1",
        model="deepseek-v4-flash",
    )

    graph = build_deep_agent(
        model=model,
        domain_tools=build_algorithm_tools(registry),
        backend=backend,
        store=InMemoryStore(),
        config=DeepAgentGraphConfig(context_window_tokens=4096),
        registry=registry,
    )

    tool_node = graph.builder.nodes["tools"].runnable
    assert {
        spec.tool_name for spec in DEFAULT_ALGORITHM_SPECS
    }.issubset(tool_node._tools_by_name)
    assert {entry.spec.capability_id for entry in registry.entries} == {
        spec.capability_id for spec in DEFAULT_ALGORITHM_SPECS
    }
