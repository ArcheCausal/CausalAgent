"""P3/P4 父图构造与新路径拓扑合同测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langchain_core.messages import AIMessageChunk

from Agent.causal_agent.graph import (
    _deep_agent_parent_node,
    _finalization_gate_node,
    build_deep_agent_parent_graph,
)
from Agent.causal_agent.graph_utils import bind_subgraph_node
from Agent.causal_agent.state import CausalAgentState
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.finalization import StructuredResponseError
from Agent.deep_agent.prompts import MANDATORY_WEB_INSTRUCTION
from Agent.deep_agent_tools.identity import (
    build_deep_agent_execution_scope,
    build_deep_agent_run_id,
    build_deep_agent_step_id,
)
from Agent.deep_agent_tools.models import FinalAnalysisDecision


class _FakeLLM:
    """只满足父图编译所需的 model_copy，不发起模型调用。"""

    def model_copy(self, *, update):
        return self


def test_parent_graph_compiles_deep_agent_gate_and_report_path() -> None:
    graph = build_deep_agent_parent_graph(
        llm=_FakeLLM(),
        deep_agent=object(),
        registry=type("Registry", (), {"entries": ()})(),
        checkpointer=MemorySaver(),
    )

    nodes = set(graph.get_graph().nodes)
    assert {
        "agent",
        "fold",
        "preprocess",
        "deep_agent",
        "finalization_gate",
        "report",
    }.issubset(nodes)
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("preprocess", "deep_agent") in edges
    assert ("deep_agent", "finalization_gate") in edges
    assert ("finalization_gate", "report") in edges


def test_bound_finalization_gate_executes_with_langgraph_config() -> None:
    decision = FinalAnalysisDecision(
        outcome="evidence_only",
        primary_result_ref=None,
        result_assessments=[],
        conflict_status="none",
        conflicts=[],
        revision_proposals=[],
        selection_rationale="没有可采用的算法结果，保留证据型结论。",
        confidence="low",
        confidence_basis=["no algorithm result"],
    )

    class Gate:
        def validate(self, **kwargs):
            assert kwargs["decision"] == decision
            return decision

    identity = TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000421",
        session_id="00000000-0000-0000-0000-000000000422",
        user_id=7,
        attempt_count=0,
        lease_epoch=1,
        worker_id="worker-1",
        input_identity="input-sha",
    )
    builder = StateGraph(CausalAgentState, context_schema=AgentRunContext)
    builder.add_node(
        "finalization_gate",
        bind_subgraph_node(
            _finalization_gate_node,
            event_node_name="finalization_gate",
            gate=Gate(),
        ),
    )
    builder.add_edge(START, "finalization_gate")
    builder.add_edge("finalization_gate", END)

    state = asyncio.run(
        builder.compile().ainvoke(
            {
                "deep_agent_structured_response": decision,
                "deep_agent_algorithm_results": {},
                "deep_agent_action_ledger": {},
            },
            config={"configurable": {"thread_id": identity.job_id}},
            context=AgentRunContext(
                execution_guard=None,
                trusted_identity=identity,
            ),
        )
    )

    assert state["finalization_status"] == "valid"
    assert state["deep_agent_decision"] == decision


def test_parent_node_uses_stable_child_thread_id_and_minimal_projection() -> None:
    calls = []

    class Child:
        async def ainvoke(self, value, *, config, context):
            calls.append((value, config, context))
            return {
                "messages": [],
                "algorithm_results": {},
                "action_ledger": {},
                "rag_evidence": {},
                "web_evidence": {},
                "structured_response": None,
                "deep_agent_run_id": value["deep_agent_run_id"],
                "execution_scope": value["execution_scope"],
            }

    identity = TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000401",
        session_id="00000000-0000-0000-0000-000000000402",
        user_id=7,
        attempt_count=1,
        lease_epoch=2,
        worker_id="worker-1",
        input_identity="input-sha",
    )
    context = AgentRunContext(
        execution_guard=None,
        trusted_identity=identity,
        web_search_enabled=True,
    )
    state = {
        "job_id": identity.job_id,
        "messages": [],
        "analysis_parameters": {},
        "file_summary": {},
        "route_decision": "fold",
    }

    update = asyncio.run(
        _deep_agent_parent_node(
            state,
            runtime=SimpleNamespace(context=context),
            config={"configurable": {"thread_id": identity.job_id}},
            deep_agent=Child(),
            store=None,
            memory_init_lock=asyncio.Lock(),
        )
    )

    expected = build_deep_agent_run_id(job_id=identity.job_id)
    assert update["deep_agent_run_id"] == expected
    assert update["deep_agent_status"] == "completed"
    assert calls[0][1]["configurable"]["thread_id"] == expected
    assert "execution_guard" not in calls[0][0]
    assert MANDATORY_WEB_INSTRUCTION in [
        message["content"] for message in calls[0][0]["messages"]
    ]


def test_parent_node_reads_matching_child_checkpoint_instead_of_projecting_messages() -> None:
    calls = []

    identity = TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000411",
        session_id="00000000-0000-0000-0000-000000000412",
        user_id=7,
        attempt_count=1,
        lease_epoch=2,
        worker_id="worker-1",
        input_identity="input-sha",
    )
    child_values = {
        "messages": ["private child message"],
        "algorithm_results": {},
        "action_ledger": {},
        "rag_evidence": {},
        "web_evidence": {},
        "structured_response": None,
        "deep_agent_run_id": build_deep_agent_run_id(job_id=identity.job_id),
        "execution_scope": build_deep_agent_execution_scope(
            job_id=identity.job_id,
            attempt_count=identity.attempt_count,
            lease_epoch=identity.lease_epoch,
            input_identity=identity.input_identity,
        ),
    }

    class Child:
        async def aget_state(self, config):
            return SimpleNamespace(values=child_values)

        async def ainvoke(self, value, *, config, context):
            calls.append(value)
            return child_values

    update = asyncio.run(
        _deep_agent_parent_node(
            {
                "job_id": identity.job_id,
                "messages": ["parent message"],
                "analysis_parameters": {},
                "file_summary": {},
            },
            runtime=SimpleNamespace(
                context=AgentRunContext(execution_guard=None, trusted_identity=identity)
            ),
            config={"configurable": {"thread_id": identity.job_id}},
            deep_agent=Child(),
            store=None,
            memory_init_lock=asyncio.Lock(),
        )
    )

    assert calls == [None]
    assert "deep_agent_messages" not in update


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
    current_scope = build_deep_agent_execution_scope(
        job_id=identity.job_id,
        attempt_count=identity.attempt_count,
        lease_epoch=identity.lease_epoch,
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
    stream_calls = []
    forwarded = []
    stream_chunk = {
        "type": "messages",
        "ns": (),
        "data": (
            AIMessageChunk(content="公开片段"),
            {"langgraph_node": "model"},
        ),
    }

    class Child:
        async def aget_state(self, config):
            return SimpleNamespace(values=child_values)

        async def astream(self, value, *, config, context, stream_mode, subgraphs, version):
            stream_calls.append((value, config, context, stream_mode, subgraphs, version))
            yield stream_chunk

    asyncio.run(
        _deep_agent_parent_node(
            {
                "job_id": identity.job_id,
                "messages": [],
                "analysis_parameters": {},
                "file_summary": {},
                "deep_agent_run_id": child_values["deep_agent_run_id"],
            },
            runtime=SimpleNamespace(
                context=AgentRunContext(execution_guard=None, trusted_identity=identity),
                stream_writer=forwarded.append,
            ),
            config={"configurable": {"thread_id": identity.job_id}},
            deep_agent=Child(),
            store=None,
            memory_init_lock=asyncio.Lock(),
        )
    )

    assert len(stream_calls) == 1
    assert stream_calls[0][3:] == (["messages", "values"], True, "v2")
    assert forwarded == [{
        "type": "message_chunk",
        "ns": (),
        "data": stream_chunk["data"],
    }]


def test_retry_path_emits_public_progress_notices() -> None:
    """Gate 拒绝与修正重试都必须留下可回放的阶段公开说明。"""

    identity = TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000431",
        session_id="00000000-0000-0000-0000-000000000432",
        user_id=7,
        attempt_count=2,
        lease_epoch=3,
        worker_id="worker-1",
        input_identity="input-sha",
    )
    events: list[dict] = []
    context = AgentRunContext(execution_guard=None, trusted_identity=identity)

    class RejectingGate:
        def validate(self, **kwargs):
            raise StructuredResponseError(
                "decision references an unknown algorithm result",
                rule="assessment_ref_unknown_result",
            )

    gate_runtime = SimpleNamespace(context=context, stream_writer=events.append)
    rejected = asyncio.run(
        _finalization_gate_node(
            {
                "deep_agent_structured_response": None,
                "deep_agent_algorithm_results": {},
                "deep_agent_action_ledger": {},
            },
            runtime=gate_runtime,
            config={},
            gate=RejectingGate(),
        )
    )

    assert rejected["finalization_retry_count"] == 1
    assert "result_assessments 只能引用本次运行返回的算法结果" in (
        rejected["deep_agent_retry_instruction"]
    )
    assert len(events) == 1
    assert events[0]["type"] == "progress"
    assert events[0]["node_name"] == "finalization_gate"
    assert events[0]["_event_key"] == "finalization-gate-retry:2:1"
    assert "修正要求：" in events[0]["summary"]

    events.clear()
    degraded = asyncio.run(
        _finalization_gate_node(
            {
                "deep_agent_structured_response": None,
                "deep_agent_algorithm_results": {},
                "deep_agent_action_ledger": {},
                "finalization_retry_count": 1,
            },
            runtime=gate_runtime,
            config={},
            gate=RejectingGate(),
        )
    )

    assert degraded["finalization_status"] == "degraded"
    assert [event["_event_key"] for event in events] == [
        "finalization-gate-degraded:2"
    ]

    events.clear()
    child_values = {
        "messages": ["private child message"],
        "algorithm_results": {},
        "action_ledger": {},
        "rag_evidence": {},
        "web_evidence": {},
        "structured_response": None,
        "deep_agent_run_id": build_deep_agent_run_id(job_id=identity.job_id),
        "execution_scope": build_deep_agent_execution_scope(
            job_id=identity.job_id,
            attempt_count=identity.attempt_count,
            lease_epoch=identity.lease_epoch,
            input_identity=identity.input_identity,
        ),
    }

    class Child:
        async def aget_state(self, config):
            return SimpleNamespace(values=child_values)

        async def ainvoke(self, value, *, config, context):
            assert "上一份" in value["messages"][-1]["content"]
            return child_values

    asyncio.run(
        _deep_agent_parent_node(
            {
                "job_id": identity.job_id,
                "messages": [],
                "analysis_parameters": {},
                "file_summary": {},
                "deep_agent_retry_instruction": "上一份结构化最终决策未通过程序事实校验。",
            },
            runtime=SimpleNamespace(
                context=context,
                stream_writer=events.append,
                execution_info=SimpleNamespace(task_id="task-deep-retry"),
            ),
            config={"configurable": {"thread_id": identity.job_id}},
            deep_agent=Child(),
            store=None,
            memory_init_lock=asyncio.Lock(),
        )
    )

    expected_step_id = build_deep_agent_step_id(
        job_id=identity.job_id,
        attempt_count=identity.attempt_count,
        task_id="task-deep-retry",
    )
    assert len(events) == 1
    assert events[0]["type"] == "progress"
    assert events[0]["node_name"] == "deep_agent"
    assert events[0]["step_id"] == expected_step_id
    assert events[0]["_event_key"] == f"deep-agent-retry:2:{expected_step_id}"
    assert "不重复调用工具" in events[0]["summary"]
