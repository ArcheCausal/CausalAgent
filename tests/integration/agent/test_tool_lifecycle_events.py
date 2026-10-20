"""真实事件转换、LangGraph custom 转发与内存 checkpoint 的契约检查。

不替换任何运行依赖，不调用模型或算法，也不把事件收集器当作 MySQL/SSE。
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from Agent.causal_agent.graph import _stream_deep_agent
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent_tools.identity import build_invocation_id
from Agent.deep_agent_tools.models import (
    ActionAttempt,
    AlgorithmResult,
    AlgorithmResultProvenance,
    Diagnostics,
    InvocationRecord,
    StandardizedGraph,
)
from Agent.deep_agent_tools.runtime_updates import (
    RuntimeInvocationIdentity,
    emit_tool_lifecycle_event,
    normalize_tool_status,
    tool_lifecycle_event_key,
)
from app.agent.public_events import public_event_payload
from app.agent.worker.event_adapter import LangGraphEventAdapter


JOB_ID = "d8242d39-d777-480d-95ad-f0cfad993e04"


def lifecycle_input(
    *, status="valid", call_id="pc-call-1", attempt_count=2,
    retry_ordinal=0, capability_id="causal.pc",
):
    """构造经过生产 schema 校验的事件输入，不执行或冒充算法。"""
    invocation_id = build_invocation_id(
        job_id=JOB_ID, response_identity="response-1", provider_call_id=call_id,
    )
    error_code = {
        "execution_failed": "ALGORITHM_EXECUTION_FAILED",
        "timed_out": "ALGORITHM_TIMED_OUT",
        "not_applicable": "ALGORITHM_NOT_APPLICABLE",
        "not_ready": "ALGORITHM_NOT_READY",
    }.get(status)
    result = AlgorithmResult(
        result_ref=f"{invocation_id}:0",
        invocation_id=invocation_id,
        provider_call_id=call_id,
        capability_id=capability_id,
        capability_version="1",
        status=status,
        standardized_graph=(
            StandardizedGraph(graph_semantics="dag", nodes=["x", "y"])
            if status == "valid" else None
        ),
        diagnostics=Diagnostics(safe_error_code=error_code),
        provenance=AlgorithmResultProvenance(
            job_id=JOB_ID, attempt_count=attempt_count, lease_epoch=3,
            input_identity="input-digest", spec_digest="spec-digest",
            invocation_id=invocation_id,
        ),
    )
    terminal_status = normalize_tool_status(status)
    record = InvocationRecord(
        invocation_id=invocation_id,
        response_identity="response-1",
        response_identity_source="provider_response_id",
        provider_response_id="response-1",
        provider_call_id=call_id,
        tool_name=capability_id.replace(".", "_"),
        final_status=terminal_status,
        result_ref=result.result_ref,
        job_id=JOB_ID, attempt_count=attempt_count, lease_epoch=3,
        worker_id="event-contract-worker", input_identity="input-digest",
        event_step_id="a" * 24,
        attempts={retry_ordinal: ActionAttempt(
            retry_ordinal=retry_ordinal, revision=2, status=terminal_status,
        )},
    )
    return result, record


def start_adapter(attempt_count=2):
    adapter = LangGraphEventAdapter(JOB_ID, attempt_count)
    adapter.convert({
        "type": "tasks", "ns": (),
        "data": {"id": "deep-task", "name": "deep_agent", "input": {}},
    })
    return adapter


def aggregate(adapter, pairs):
    return adapter.convert({
        "type": "updates", "ns": (), "data": {"deep_agent": {
            "deep_agent_algorithm_results": {
                result.result_ref: result for result, _ in pairs
            },
            "deep_agent_action_ledger": {
                record.invocation_id: record for _, record in pairs
            },
        }},
    })


@pytest.mark.parametrize("status,summary", [
    ("valid", "调用完成"), ("execution_failed", "调用失败"),
    ("timed_out", "调用超时"), ("not_applicable", "不适用"),
    ("not_ready", "暂不可用"),
])
def test_direct_custom_and_aggregate_share_terminal_key_and_status(status, summary):
    result, record = lifecycle_input(status=status, retry_ordinal=1)
    direct = []
    context = AgentRunContext(
        execution_guard=None,
        trusted_identity=TrustedJobIdentity(
            job_id=JOB_ID, session_id=JOB_ID, user_id=1, attempt_count=2,
            lease_epoch=3, worker_id="event-contract-worker",
            input_identity="input-digest",
        ),
        event_sink=direct.append,
        deep_agent_step_id="a" * 24,
    )

    async def emit():
        # Runtime 是 LangGraph 的真实对象；sink 仅收集输入，不模拟持久化。
        await emit_tool_lifecycle_event(
            Runtime(context=context), event_type="tool_call_result",
            identity=RuntimeInvocationIdentity(
                provider_call_id=record.provider_call_id,
                response_identity=record.response_identity,
                response_identity_source=record.response_identity_source,
                provider_response_id=record.provider_response_id,
                runtime_context=context,
            ),
            tool_name=record.tool_name, status=result.status,
            safe_error_code=result.diagnostics.safe_error_code, retry_ordinal=1,
        )

    asyncio.run(emit())
    adapter = start_adapter()
    custom = {"type": "custom", "data": direct[0]}
    converted = adapter.convert(custom)
    fallback = aggregate(start_adapter(), [(result, record)])
    assert len(converted) == len(fallback) == 1
    assert direct[0] == converted[0] == fallback[0]
    assert start_adapter(attempt_count=3).convert(custom) == converted
    for event in [direct[0], converted[0], fallback[0]]:
        assert event["_event_key"] == fallback[0]["_event_key"]
        assert event["status"] == normalize_tool_status(status)
        assert event["summary"] == summary
        public = public_event_payload("tool_call_result", event)
        assert set(public) <= {
            "type", "step_id", "node_name", "title", "tool_name",
            "summary", "status", "safe_error_code",
        }
        assert JOB_ID not in str(public)
        assert result.invocation_id not in str(public)
    assert adapter.convert(custom) == []
    assert aggregate(adapter, [(result, record)]) == []
    # 先收到聚合，随后收到迟到 custom 时也不能新增终态。
    reverse = start_adapter()
    assert len(aggregate(reverse, [(result, record)])) == 1
    assert reverse.convert(custom) == []


def test_same_tool_keeps_distinct_calls_attempts_and_retries_on_replay():
    first = lifecycle_input()
    second = lifecycle_input(call_id="pc-call-2")
    adapter = start_adapter()
    events = aggregate(adapter, [first, second])
    assert len(events) == 2
    assert len({event["_event_key"] for event in events}) == 2
    assert {event["tool_name"] for event in events} == {"causal_pc"}
    assert aggregate(adapter, [first, second]) == []
    # 新适配器从 checkpoint 重建时使用原执行 attempt，而非当前恢复 attempt。
    restored = aggregate(start_adapter(attempt_count=3), [first, second])
    assert [event["_event_key"] for event in restored] == [
        event["_event_key"] for event in events
    ]
    assert restored == events
    for pair in [lifecycle_input(attempt_count=3), lifecycle_input(retry_ordinal=1)]:
        newer = aggregate(adapter, [pair])
        assert len(newer) == 1
        assert newer[0]["_event_key"] != events[0]["_event_key"]


def test_cdfm_timeout_fallback_uses_registered_tool_and_safe_error_code():
    pair = lifecycle_input(status="timed_out", capability_id="causal.cdfm")
    events = aggregate(start_adapter(), [pair])
    assert len(events) == 1
    assert events[0]["tool_name"] == "causal_cdfm"
    assert events[0]["status"] == "timed_out"
    assert events[0]["summary"] == "调用超时"
    assert events[0]["safe_error_code"] == "ALGORITHM_TIMED_OUT"


@pytest.mark.parametrize("missing", [
    "ledger", "invocation_id", "attempt_count", "attempts", "pending",
    "running", "result_ref", "provider_call_id", "job_id", "tool_name",
    "terminal_mismatch", "lease_epoch", "input_identity", "event_step_id",
])
def test_aggregate_never_invents_terminal_without_matching_identity(missing):
    result, record = lifecycle_input()
    raw_result = result.model_dump(mode="json")
    raw_record = record.model_dump(mode="json")
    if missing == "invocation_id":
        raw_result.pop("invocation_id")
    elif missing == "attempt_count":
        raw_record["attempt_count"] = None
    elif missing == "attempts":
        raw_record["attempts"] = {}
    elif missing == "pending":
        raw_record["final_status"] = "pending"
    elif missing == "running":
        raw_record["attempts"]["0"]["status"] = "running"
    elif missing == "terminal_mismatch":
        raw_record["final_status"] = "timed_out"
        raw_record["attempts"]["0"]["status"] = "timed_out"
    elif missing == "lease_epoch":
        raw_record[missing] = 9
    elif missing == "input_identity":
        raw_record[missing] = "different-input"
    elif missing == "event_step_id":
        raw_record[missing] = None
    elif missing in {"result_ref", "provider_call_id", "job_id", "tool_name"}:
        raw_record[missing] = JOB_ID if missing == "job_id" else "different"
        if missing == "job_id":
            raw_record[missing] = "5301644c-074f-4096-8697-9af7f97f2b60"
    ledger = {} if missing == "ledger" else {record.invocation_id: raw_record}
    assert start_adapter().convert({"type": "updates", "data": {"deep_agent": {
        "deep_agent_algorithm_results": {result.result_ref: raw_result},
        "deep_agent_action_ledger": ledger,
    }}}) == []


@pytest.mark.parametrize("key", [None, "", "k" * 256])
def test_custom_without_usable_event_key_cannot_create_terminal(key):
    assert start_adapter().convert({"type": "custom", "data": {
        "type": "tool_call_result", "tool_name": "causal_pc",
        "status": "valid", "_event_key": key,
    }}) == []


@pytest.mark.parametrize("status,summary", [
    ("canceled", "调用已取消"), ("cancelled", "调用已取消"),
    ("timeout", "调用超时"), ("skipped", "未执行"),
])
def test_custom_controlled_statuses_use_shared_summary(status, summary):
    adapter = start_adapter()
    key = tool_lifecycle_event_key(
        invocation_id="control-call", attempt_count=2,
        event_type="tool_call_result",
    )
    events = adapter.convert({"type": "custom", "data": {
        "type": "tool_call_result", "tool_name": "causal_pc",
        "status": status, "_event_key": key, "summary": "内部正文",
        "invocation_id": "private-invocation", "retry_ordinal": 10,
    }})
    assert len(events) == 1
    assert events[0]["status"] == normalize_tool_status(status)
    assert events[0]["summary"] == summary
    assert "invocation_id" not in events[0]
    assert "retry_ordinal" not in events[0]


def test_real_child_graph_forwards_only_lifecycle_and_commits_before_execution():
    async def run():
        sink_events = []
        context = AgentRunContext(
            execution_guard=None,
            trusted_identity=TrustedJobIdentity(
                job_id=JOB_ID, session_id=JOB_ID, user_id=1, attempt_count=2,
                lease_epoch=3, worker_id="event-contract-worker",
                input_identity="input-digest",
            ),
            event_sink=sink_events.append,
        )
        child_config = {"configurable": {
            "thread_id": "lifecycle-contract", "checkpoint_ns": "",
        }}
        saver = InMemorySaver()

        async def queued(state):
            return {**state, "registration": "queued"}

        async def running(state):
            return {**state, "registration": "running"}

        async def lifecycle(state, runtime: Runtime[AgentRunContext]):
            checkpoints = [item async for item in saver.alist(child_config)]
            registrations = {
                item.checkpoint["channel_values"].get("__root__", {}).get(
                    "registration"
                ) for item in checkpoints
            }
            assert {"queued", "running"} <= registrations
            identity = RuntimeInvocationIdentity(
                provider_call_id="pc-call-1", response_identity="response-1",
                response_identity_source="provider_response_id",
                provider_response_id="response-1", runtime_context=runtime.context,
            )
            runtime.stream_writer({"type": "private_trace", "body": "private"})
            runtime.stream_writer({"type": "progress", "summary": "private"})
            for event_type, status in [
                ("tool_call_start", None), ("tool_call_result", "valid"),
            ]:
                await emit_tool_lifecycle_event(
                    runtime, event_type=event_type, identity=identity,
                    tool_name="causal_pc", status=status,
                )
            # 生命周期类型也必须过滤额外内部字段。
            runtime.stream_writer({**sink_events[-1], "private_body": "private"})
            return {**state, "completed": True}

        child_builder = StateGraph(dict, context_schema=AgentRunContext)
        for name, node in [("queued", queued), ("running", running),
                           ("lifecycle", lifecycle)]:
            child_builder.add_node(name, node)
        child_builder.add_edge(START, "queued")
        child_builder.add_edge("queued", "running")
        child_builder.add_edge("running", "lifecycle")
        child_builder.add_edge("lifecycle", END)
        child = child_builder.compile(checkpointer=saver)

        async def parent_node(state, runtime: Runtime[AgentRunContext]):
            return await _stream_deep_agent(
                child, state, child_config=child_config,
                child_context=runtime.context, runtime=runtime,
            )

        parent_builder = StateGraph(dict, context_schema=AgentRunContext)
        parent_builder.add_node("deep_agent", parent_node)
        parent_builder.add_edge(START, "deep_agent")
        parent_builder.add_edge("deep_agent", END)
        parent = parent_builder.compile()
        chunks = [chunk async for chunk in parent.astream(
            {}, context=context, stream_mode=["tasks", "custom", "updates"],
            version="v2",
        )]
        custom = [chunk["data"] for chunk in chunks if chunk["type"] == "custom"]
        assert [event["type"] for event in custom] == [
            "tool_call_start", "tool_call_result", "tool_call_result",
        ]
        assert all("private" not in str(event) for event in custom)
        assert [event["_event_key"] for event in custom[:2]] == [
            event["_event_key"] for event in sink_events
        ]
        adapter = LangGraphEventAdapter(JOB_ID, 2)
        events = [event for chunk in chunks for event in adapter.convert(chunk)]
        assert len([event for event in events if event["type"] == "tool_call_start"]) == 1
        terminals = [event for event in events if event["type"] == "tool_call_result"]
        assert len(terminals) == 1
        assert terminals[0]["summary"] == "调用完成"
        assert terminals[0]["_event_key"] == sink_events[-1]["_event_key"]
        assert any(chunk.get("data", {}).get("deep_agent", {}).get("completed")
                   for chunk in chunks if chunk["type"] == "updates")

    asyncio.run(run())
