"""真实父图编译、确定性 Gate 与消息流契约；MySQL 父节点验收见 e2e/database。"""

from __future__ import annotations

import asyncio

from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

from Agent.causal_agent.graph import (
    _finalization_gate_node,
    _stream_deep_agent,
    build_deep_agent_parent_graph,
)
from Agent.causal_agent.graph_utils import bind_subgraph_node
from Agent.causal_agent.state import CausalAgentState
from Agent.data_processing.agent import build_data_processing_agent
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.finalization import FinalizationGate
from Agent.deep_agent.graph import DeepAgentGraphConfig, build_deep_agent
from Agent.deep_agent.state import ProjectDeepAgentState
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.identity import build_deep_agent_analysis_scope, build_deep_agent_run_id
from Agent.deep_agent_tools.models import FinalAnalysisDecision
from Agent.deep_agent_tools.registry import build_default_registry
from config.secret_file import require_secret
from tests.support.bound_analysis_input import bind_analysis_input
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor


def _identity():
    return bind_analysis_input(TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000421",
        session_id="00000000-0000-0000-0000-000000000422",
        user_id=7, attempt_count=2, lease_epoch=3,
        worker_id="worker-1", input_identity="original-input-sha",
    ))


def _decision():
    return FinalAnalysisDecision(
        outcome="evidence_only", primary_result_ref=None, result_assessments=[],
        conflict_status="none", conflicts=[], revision_proposals=[],
        selection_rationale="没有可采用的算法结果，保留证据型结论。",
        confidence="low", confidence_basis=["no algorithm result"],
    )


def test_parent_graph_compiles_deep_agent_gate_and_report_path() -> None:
    # 仅编译真实模型和图，不调用模型、算法或沙盒。
    model = ChatOpenAI(
        api_key=require_secret("DEEP_AGENT_API_KEY"),
        model="deepseek-v4-flash", use_responses_api=True,
    )
    registry = build_default_registry(build_default_adapters(executor=LocalAlgorithmExecutor()))
    saver = MemorySaver()
    config = DeepAgentGraphConfig(context_window_tokens=1000000)
    store = InMemoryStore()
    child = build_deep_agent(
        model=model, domain_tools=build_algorithm_tools(registry),
        backend=StateBackend(), store=store, registry=registry,
        checkpointer=saver, config=config,
    )
    processing = build_data_processing_agent(model=model, checkpointer=saver, config=config)
    graph = build_deep_agent_parent_graph(
        llm=model, deep_agent=child, registry=registry,
        data_processing_agent=processing, checkpointer=saver, store=store,
    )
    nodes = set(graph.get_graph().nodes)
    assert {
        "agent", "fold", "data_processing", "validate_dataset", "bind_dataset",
        "preprocess", "deep_agent", "finalization_gate", "report",
    }.issubset(nodes)
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("bind_dataset", "preprocess") in edges
    assert ("preprocess", "deep_agent") in edges
    assert ("deep_agent", "finalization_gate") in edges
    assert ("finalization_gate", "report") in edges


def test_bound_finalization_gate_executes_with_langgraph_config() -> None:
    decision = _decision()
    identity = _identity()
    registry = build_default_registry(build_default_adapters(executor=LocalAlgorithmExecutor()))
    builder = StateGraph(CausalAgentState, context_schema=AgentRunContext)
    builder.add_node("finalization_gate", bind_subgraph_node(
        _finalization_gate_node, event_node_name="finalization_gate",
        gate=FinalizationGate(registry=registry),
    ))
    builder.add_edge(START, "finalization_gate")
    builder.add_edge("finalization_gate", END)
    state = asyncio.run(builder.compile().ainvoke(
        {
            "deep_agent_structured_response": decision,
            "deep_agent_algorithm_results": {}, "deep_agent_action_ledger": {},
        },
        config={"configurable": {"thread_id": identity.job_id}},
        context=AgentRunContext(execution_guard=None, trusted_identity=identity),
    ))
    assert state["finalization_status"] == "valid"
    assert state["deep_agent_decision"] == decision


def test_parent_node_consumes_child_message_stream_before_projecting_state() -> None:
    """父节点必须消费内层 messages 流，不能用 ainvoke 把 token 收成一次状态。"""
    identity = TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000451",
        session_id="00000000-0000-0000-0000-000000000452",
        user_id=7,
        attempt_count=1,
        lease_epoch=2,
        worker_id="worker-1",
        input_identity="input-sha",
    )
    current_scope = build_deep_agent_analysis_scope(
        job_id=identity.job_id,
        input_identity=identity.input_identity,
    )
    child_values = {
        "messages": [],
        "algorithm_results": {},
        "action_ledger": {},
        "rag_evidence": {},
        "web_evidence": {},
        "structured_response": None,
        "deep_agent_run_id": build_deep_agent_run_id(job_id=identity.job_id),
        "execution_scope": current_scope,
    }
    forwarded = []

    async def scenario():
        async def message_node(state):
            # 显式消息输入经真实 LangGraph messages 流传输，不调用或替代模型。
            return {"messages": [AIMessage(content="公开片段")]}

        builder = StateGraph(ProjectDeepAgentState)
        builder.add_node("message", message_node)
        builder.add_edge(START, "message")
        builder.add_edge("message", END)
        child = builder.compile(checkpointer=MemorySaver())
        context = AgentRunContext(execution_guard=None, trusted_identity=identity)
        values = await _stream_deep_agent(
            child, child_values,
            child_config={"configurable": {"thread_id": identity.job_id, "checkpoint_ns": ""}},
            child_context=context, runtime=Runtime(context=context, stream_writer=forwarded.append),
        )
        assert values["messages"][-1].content == "公开片段"

    asyncio.run(scenario())
    assert len(forwarded) == 1
    assert forwarded[0]["type"] == "message_chunk"
    assert forwarded[0]["ns"] == ()
    assert forwarded[0]["data"][0].content == "公开片段"


def test_retry_path_emits_public_progress_notices() -> None:
    """真实 Gate 的未知结果引用错误触发一次修正及之后的降级事件。"""
    identity = _identity()
    events = []
    context = AgentRunContext(execution_guard=None, trusted_identity=identity)
    decision = FinalAnalysisDecision.model_validate({**_decision().model_dump(), "result_assessments": [{
        "result_ref": "unknown-result", "disposition": "discarded",
        "rationale": "引用并不存在的结果，验证程序必须拒绝。",
    }]})
    registry = build_default_registry(build_default_adapters(executor=LocalAlgorithmExecutor()))
    gate = FinalizationGate(registry=registry)
    state = {
        "deep_agent_structured_response": decision,
        "deep_agent_algorithm_results": {}, "deep_agent_action_ledger": {},
    }
    runtime = Runtime(context=context, stream_writer=events.append)
    rejected = asyncio.run(_finalization_gate_node(state, runtime=runtime, config={}, gate=gate))
    assert rejected["finalization_retry_count"] == 1
    assert "result_assessments 只能引用本次运行返回的算法结果" in rejected["deep_agent_retry_instruction"]
    assert len(events) == 1
    assert events[0]["type"] == "progress"
    assert events[0]["node_name"] == "finalization_gate"
    assert events[0]["_event_key"] == "finalization-gate-retry:2:1"
    assert "修正要求：" in events[0]["summary"]

    events.clear()
    degraded = asyncio.run(_finalization_gate_node(
        {**state, "finalization_retry_count": 1}, runtime=runtime, config={}, gate=gate,
    ))
    assert degraded["finalization_status"] == "degraded"
    assert [event["_event_key"] for event in events] == ["finalization-gate-degraded:2"]
