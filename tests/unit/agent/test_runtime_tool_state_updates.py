"""ToolRuntime identity, terminal Ledger and Command State update tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from Agent.deep_agent import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.state import ProjectDeepAgentState
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent_tools import (
    DataProfile,
    FakeAlgorithmExecutor,
    RagEvidenceTool,
    SafeErrorCode,
    WebEvidenceTool,
    build_algorithm_tools,
    build_default_registry,
)
from Agent.deep_agent_tools.adapters import build_default_adapters


IDENTITY = TrustedJobIdentity(
    job_id="00000000-0000-0000-0000-000000000001",
    session_id="00000000-0000-0000-0000-000000000002",
    user_id=7,
    attempt_count=0,
    lease_epoch=1,
    worker_id="worker-1",
    input_identity="input-sha",
)


def _context(executor=None) -> AgentRunContext:
    return AgentRunContext(
        execution_guard=None,
        trusted_identity=IDENTITY,
        algorithm_executor=executor or FakeAlgorithmExecutor(),
    )


def _runtime(context: AgentRunContext, *, call_id: str):
    return SimpleNamespace(
        tool_call_id=call_id,
        state={"message_execution_id": "message-execution-1"},
        context=context,
    )


def _algorithm_tool(*, executor: FakeAlgorithmExecutor):
    context = _context(executor)
    registry = build_default_registry(
        build_default_adapters(
            executor=executor,
            raw_backend=build_in_memory_backend(user_id=7),
        )
    )
    tools = build_algorithm_tools(
        registry,
        runtime_context=context,
        data_profile=DataProfile(
            row_count=100,
            column_count=2,
            column_names=("x", "y"),
            numeric_columns=("x", "y"),
        ),
    )
    wrapped = next(tool for tool in tools if tool.name == "causal_pc").to_langchain_tool()
    return context, wrapped


def test_algorithm_langchain_tool_writes_result_and_terminal_ledger() -> None:
    context, tool = _algorithm_tool(executor=FakeAlgorithmExecutor())

    command = asyncio.run(
        tool.coroutine(
            runtime=_runtime(context, call_id="provider-call-1"),
            alpha=0.05,
        )
    )

    assert "runtime" not in tool.tool_call_schema.model_json_schema()["properties"]
    result = next(iter(command.update["algorithm_results"].values()))
    ledger = next(iter(command.update["action_ledger"].values()))
    assert result.status == "valid"
    assert ledger.provider_call_id == "provider-call-1"
    assert ledger.response_identity_source == "message_execution_id"
    assert ledger.final_status == "succeeded"
    assert ledger.result_ref == result.result_ref
    assert ledger.attempts[0].revision == 2
    assert ledger.attempts[0].status == "succeeded"
    assert command.update["messages"][0].tool_call_id == "provider-call-1"


def test_algorithm_public_decision_is_emitted_and_removed_before_execution() -> None:
    executor = FakeAlgorithmExecutor()
    events = []
    context, tool = _algorithm_tool(executor=executor)
    context = replace(
        context,
        event_sink=lambda payload: events.append(dict(payload)),
    )
    tool = next(
        item
        for item in build_algorithm_tools(
            build_default_registry(
                build_default_adapters(
                    executor=executor,
                    raw_backend=build_in_memory_backend(user_id=7),
                )
            ),
            runtime_context=context,
            data_profile=DataProfile(
                row_count=100,
                column_count=2,
                column_names=("x", "y"),
                numeric_columns=("x", "y"),
            ),
        )
        if item.name == "causal_pc"
    ).to_langchain_tool()

    asyncio.run(
        tool.coroutine(
            runtime=_runtime(context, call_id="provider-call-public-decision"),
            alpha=0.05,
            public_decision={"summary": "数据为连续数值，因此调用 PC 比较结构稳定性。"},
        )
    )

    assert [event["type"] for event in events] == [
        "decision",
        "tool_call_start",
        "tool_call_result",
    ]
    assert events[0]["decision_kind"] == "algorithm"
    assert events[0]["tool_name"] == "causal_pc"
    assert executor.calls[0].command.parameters == {"alpha": 0.05}


def test_invalid_public_decision_does_not_block_algorithm_execution() -> None:
    executor = FakeAlgorithmExecutor()
    events = []
    context, tool = _algorithm_tool(executor=executor)
    context = replace(
        context,
        event_sink=lambda payload: events.append(dict(payload)),
    )
    tool = next(
        item
        for item in build_algorithm_tools(
            build_default_registry(
                build_default_adapters(
                    executor=executor,
                    raw_backend=build_in_memory_backend(user_id=7),
                )
            ),
            runtime_context=context,
            data_profile=DataProfile(row_count=100, column_count=2),
        )
        if item.name == "causal_pc"
    ).to_langchain_tool()

    asyncio.run(
        tool.coroutine(
            runtime=_runtime(context, call_id="provider-call-invalid-decision"),
            alpha=0.05,
            public_decision={"summary": "bad\nsummary"},
        )
    )

    assert [event["type"] for event in events] == [
        "tool_call_start",
        "tool_call_result",
    ]
    assert len(executor.calls) == 1


def test_tool_node_injects_runtime_and_reducers_commit_command_update() -> None:
    context, tool = _algorithm_tool(executor=FakeAlgorithmExecutor())
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("tools", ToolNode([tool], handle_tool_errors=False))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()

    state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "causal_pc",
                                "args": {"alpha": 0.05},
                                "id": "tool-node-call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "message_execution_id": "message-execution-1",
                "algorithm_results": {},
                "action_ledger": {},
                "rag_evidence": {},
                "web_evidence": {},
            },
            context=context,
        )
    )

    assert state["algorithm_results"], state["messages"][-1].content
    result = next(iter(state["algorithm_results"].values()))
    ledger = next(iter(state["action_ledger"].values()))
    assert result.status == "valid"
    assert ledger.provider_call_id == "tool-node-call-1"
    assert ledger.result_ref == result.result_ref
    assert state["messages"][-1].tool_call_id == "tool-node-call-1"


def test_algorithm_execution_failed_is_returned_and_recorded() -> None:
    executor = FakeAlgorithmExecutor(
        failures={"causal.pc": SafeErrorCode.ALGORITHM_EXECUTION_FAILED}
    )
    context, tool = _algorithm_tool(executor=executor)

    command = asyncio.run(
        tool.coroutine(
            runtime=_runtime(context, call_id="provider-call-failed"),
            alpha=0.05,
        )
    )

    result = next(iter(command.update["algorithm_results"].values()))
    ledger = next(iter(command.update["action_ledger"].values()))
    message_payload = json.loads(command.update["messages"][0].content)
    assert result.status == "execution_failed"
    assert message_payload["status"] == "execution_failed"
    assert ledger.final_status == "failed"
    assert ledger.result_ref == result.result_ref
    assert ledger.attempts[0].safe_error_code == "ALGORITHM_EXECUTION_FAILED"


def test_rag_and_web_tools_write_evidence_and_terminal_ledgers() -> None:
    context = _context()

    class Retriever:
        def get_evidence(self, query, *, max_contexts=None):
            return {
                "status": "available",
                "release_id": "mm_" + "a" * 20,
                "evidence": [
                    {"evidence_ref": "rag:1", "snippet": "RAG evidence"}
                ],
            }

    rag_tool = RagEvidenceTool(Retriever()).to_langchain_tool()
    rag_command = asyncio.run(
        rag_tool.coroutine(
            runtime=_runtime(context, call_id="rag-call-1"),
            query="causal inference",
        )
    )
    rag_ledger = next(iter(rag_command.update["action_ledger"].values()))
    expected_rag_ref = f"rag:1:invocation:{rag_ledger.invocation_id}"
    assert set(rag_command.update["rag_evidence"]) == {expected_rag_ref}
    rag_payload = json.loads(rag_command.update["messages"][0].content)
    assert rag_payload["evidence"][0]["evidence_ref"] == expected_rag_ref
    assert rag_ledger.final_status == "succeeded"

    def search(query, *, max_results):
        return {
            "results": [
                {
                    "source": "arxiv",
                    "title": "Paper",
                    "url": "https://example.invalid/paper",
                    "snippet": "Web evidence",
                }
            ]
        }

    web_tool = WebEvidenceTool(search).to_langchain_tool()
    web_command = asyncio.run(
        web_tool.coroutine(
            runtime=_runtime(context, call_id="web-call-1"),
            query="causal inference",
        )
    )
    assert len(web_command.update["web_evidence"]) == 1
    web_ledger = next(iter(web_command.update["action_ledger"].values()))
    web_ref = next(iter(web_command.update["web_evidence"]))
    assert web_ref.endswith(f":invocation:{web_ledger.invocation_id}")
    assert web_ledger.final_status == "succeeded"


def test_parallel_rag_calls_scope_rank_refs_to_each_invocation() -> None:
    context = _context()

    class Retriever:
        def get_evidence(self, query, *, max_contexts=None):
            return {
                "status": "available",
                "release_id": "release-1",
                "evidence": [
                    {
                        "evidence_ref": "rag:release-1:E1",
                        "evidence_id": "E1",
                        "snippet": f"evidence for {query}",
                    }
                ],
            }

    tool = RagEvidenceTool(Retriever()).to_langchain_tool()
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("tools", ToolNode([tool], handle_tool_errors=False))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()

    state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "rag_evidence_search",
                                "args": {"query": "first query"},
                                "id": "rag-parallel-1",
                                "type": "tool_call",
                            },
                            {
                                "name": "rag_evidence_search",
                                "args": {"query": "second query"},
                                "id": "rag-parallel-2",
                                "type": "tool_call",
                            },
                        ],
                    )
                ],
                "message_execution_id": "message-execution-1",
                "algorithm_results": {},
                "action_ledger": {},
                "rag_evidence": {},
                "web_evidence": {},
            },
            context=context,
        )
    )

    ledgers = list(state["action_ledger"].values())
    expected_refs = {
        f"rag:release-1:E1:invocation:{record.invocation_id}"
        for record in ledgers
    }
    assert set(state["rag_evidence"]) == expected_refs
    assert {item.snippet for item in state["rag_evidence"].values()} == {
        "evidence for first query",
        "evidence for second query",
    }
    tool_payload_refs = {
        json.loads(message.content)["evidence"][0]["evidence_ref"]
        for message in state["messages"]
        if getattr(message, "name", None) == "rag_evidence_search"
    }
    assert tool_payload_refs == expected_refs


def test_parallel_web_calls_scope_overlapping_sources_to_each_invocation() -> None:
    context = _context()

    def search(query, *, max_results):
        return {
            "results": [
                {
                    "source": "arxiv",
                    "title": "Shared paper",
                    "url": "https://example.invalid/shared-paper",
                    "snippet": "Shared evidence",
                }
            ]
        }

    tool = WebEvidenceTool(search).to_langchain_tool()
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("tools", ToolNode([tool], handle_tool_errors=False))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()

    state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "web_evidence_search",
                                "args": {"query": "first query"},
                                "id": "web-parallel-1",
                                "type": "tool_call",
                            },
                            {
                                "name": "web_evidence_search",
                                "args": {"query": "second query"},
                                "id": "web-parallel-2",
                                "type": "tool_call",
                            },
                        ],
                    )
                ],
                "message_execution_id": "message-execution-1",
                "algorithm_results": {},
                "action_ledger": {},
                "rag_evidence": {},
                "web_evidence": {},
            },
            context=context,
        )
    )

    assert len(state["web_evidence"]) == 2
    invocation_ids = {
        record.invocation_id for record in state["action_ledger"].values()
    }
    assert {
        ref.rsplit(":invocation:", 1)[1]
        for ref in state["web_evidence"]
    } == invocation_ids


def test_rag_and_web_public_decisions_precede_tool_lifecycle() -> None:
    events = []
    context = replace(
        _context(),
        event_sink=lambda payload: events.append(dict(payload)),
    )

    class Retriever:
        def get_evidence(self, query, *, max_contexts=None):
            return {"status": "no_relevant_evidence", "evidence": []}

    rag_tool = RagEvidenceTool(Retriever()).to_langchain_tool()
    rag_public_schema = rag_tool.tool_call_schema.model_json_schema()
    assert "public_decision" in rag_public_schema["properties"]
    assert "runtime" not in rag_public_schema["properties"]
    asyncio.run(
        rag_tool.coroutine(
            runtime=_runtime(context, call_id="rag-public-decision"),
            query="causal inference",
            public_decision={"summary": "检索知识库以核对方法假设。"},
        )
    )

    def search(query, *, max_results):
        return {"results": []}

    web_tool = WebEvidenceTool(search).to_langchain_tool()
    web_public_schema = web_tool.tool_call_schema.model_json_schema()
    assert "public_decision" in web_public_schema["properties"]
    assert "runtime" not in web_public_schema["properties"]
    asyncio.run(
        web_tool.coroutine(
            runtime=_runtime(context, call_id="web-public-decision"),
            query="causal inference",
            public_decision={"summary": "检索外部文献以比较近期证据。"},
        )
    )

    assert [event["type"] for event in events] == [
        "decision",
        "tool_call_start",
        "tool_call_result",
        "decision",
        "tool_call_start",
        "tool_call_result",
    ]
    decisions = [event for event in events if event["type"] == "decision"]
    assert [event["decision_kind"] for event in decisions] == [
        "evidence",
        "evidence",
    ]
    assert [event["tool_name"] for event in decisions] == [
        "rag_evidence_search",
        "web_evidence_search",
    ]


def test_tool_lifecycle_sink_is_awaited_around_real_retrieval() -> None:
    events = []
    calls = []

    async def sink(payload):
        events.append((payload["type"], len(calls)))

    context = AgentRunContext(
        execution_guard=None,
        trusted_identity=IDENTITY,
        event_sink=sink,
    )

    class Retriever:
        def get_evidence(self, query, *, max_contexts=None):
            calls.append(query)
            return {"status": "no_relevant_evidence", "evidence": []}

    tool = RagEvidenceTool(Retriever()).to_langchain_tool()
    asyncio.run(
        tool.coroutine(
            runtime=_runtime(context, call_id="rag-lifecycle-1"),
            query="causal inference",
        )
    )

    assert events == [("tool_call_start", 0), ("tool_call_result", 1)]


def test_canceled_tool_lifecycle_uses_same_summary_for_sink_and_stream() -> None:
    events = []

    async def sink(payload):
        events.append(dict(payload))

    context = AgentRunContext(
        execution_guard=None,
        trusted_identity=IDENTITY,
        event_sink=sink,
    )

    class Retriever:
        def get_evidence(self, query, *, max_contexts=None):
            raise asyncio.CancelledError()

    tool = RagEvidenceTool(Retriever()).to_langchain_tool()

    try:
        asyncio.run(
            tool.coroutine(
                runtime=_runtime(context, call_id="rag-lifecycle-canceled"),
                query="causal inference",
            )
        )
    except asyncio.CancelledError:
        pass
    else:  # pragma: no cover
        raise AssertionError("canceled tool must propagate CancelledError")

    assert [event["type"] for event in events] == [
        "tool_call_start",
        "tool_call_result",
    ]
    assert events[1]["status"] == "canceled"
    assert events[1]["summary"] == "调用已取消"


def test_web_tool_honors_trusted_runtime_switch() -> None:
    calls = []
    events = []

    async def sink(payload):
        events.append(dict(payload))

    def search(query, *, max_results):
        calls.append(query)
        return {"results": []}

    context = AgentRunContext(
        execution_guard=None,
        trusted_identity=IDENTITY,
        web_search_enabled=False,
        event_sink=sink,
    )
    tool = WebEvidenceTool(search).to_langchain_tool()
    command = asyncio.run(
        tool.coroutine(
            runtime=_runtime(context, call_id="web-call-disabled"),
            query="causal inference",
        )
    )

    payload = json.loads(command.update["messages"][0].content)
    assert payload["status"] == "disabled"
    assert calls == []
    ledger = next(iter(command.update["action_ledger"].values()))
    assert ledger.final_status == "not_ready"
    assert ledger.attempts[0].safe_error_code == "WEB_SEARCH_DISABLED"
    assert events[1]["status"] == "not_ready"
    assert events[1]["summary"] == "未启用"


def test_evidence_unavailable_is_recorded_without_leaking_exception() -> None:
    events = []

    async def sink(payload):
        events.append(dict(payload))

    context = AgentRunContext(
        execution_guard=None,
        trusted_identity=IDENTITY,
        algorithm_executor=FakeAlgorithmExecutor(),
        event_sink=sink,
    )

    class BrokenRetriever:
        def get_evidence(self, query, *, max_contexts=None):
            raise RuntimeError("private failure")

    tool = RagEvidenceTool(BrokenRetriever()).to_langchain_tool()
    command = asyncio.run(
        tool.coroutine(
            runtime=_runtime(context, call_id="rag-call-failed"),
            query="q",
        )
    )
    ledger = next(iter(command.update["action_ledger"].values()))
    payload = json.loads(command.update["messages"][0].content)
    assert payload["status"] == "unavailable"
    assert "private failure" not in str(payload)
    assert ledger.final_status == "failed"
    assert ledger.attempts[0].safe_error_code == "RAG_RETRIEVAL_UNAVAILABLE"
    assert events[1]["status"] == "failed"
    assert events[1]["summary"] == "暂不可用"


def test_runtime_identity_has_no_static_fallback() -> None:
    context, tool = _algorithm_tool(executor=FakeAlgorithmExecutor())
    runtime = SimpleNamespace(
        tool_call_id=None,
        state={"message_execution_id": "message-execution-1"},
        context=context,
    )

    try:
        asyncio.run(tool.coroutine(runtime=runtime, alpha=0.05))
    except RuntimeError as exc:
        assert "tool_call_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing runtime identity must fail closed")
