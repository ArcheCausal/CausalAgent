"""真实 LangGraph/ToolNode/内存 checkpoint 与算法终态的跨模块回归。

PC 使用生产本地 runner 和生产结果转换函数。deadline 工作负载仅使用真实
asyncio 原语，不运行 CDFM，不连接 MCP、模型、MySQL 或 PostgreSQL。
领域对象作为纯契约输入，不代表执行过对应算法；事件列表仅用于观察输出。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import ValidationError

from Agent.Report.document import (
    MarkdownBlock,
    ReportDraft,
    SectionBlock,
    build_report_document,
    iter_report_blocks,
)
from Agent.causal.causalachieve import run_pc_analysis
from Agent.causal_agent.graph import _finalization_gate_node
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.finalization import FinalizationGate, StructuredResponseError
from Agent.deep_agent.graph import _build_message_identity_middleware
from Agent.deep_agent.state import ProjectDeepAgentState
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.adapters.normalization import result_from_runner_payload
from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC, DIRECT_LINGAM_SPEC, PC_SPEC
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.dependency_dispatch import (
    AlgorithmDependencyDispatchMiddleware,
    _tool_calls_from_state,
)
from Agent.deep_agent_tools.dependency_planner import (
    DependencyPlan,
    PlannedToolCall,
    ToolCallRequest,
    _classify_execution_result,
    execute_dependency_plan,
)
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.identity import build_invocation_id, build_result_ref
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultProvenance,
    DataProfile,
    Diagnostics,
    FinalAnalysisDecision,
    ReducerConflictError,
    ResultAssessment,
    merge_action_ledger,
)
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.deep_agent_tools.runtime_updates import (
    RuntimeInvocationIdentity,
    algorithm_terminal_command,
    build_pending_invocation,
    build_terminal_invocation,
    normalize_tool_status,
    resolve_runtime_invocation,
    tool_lifecycle_event_key,
)
from Agent.execution_control import JobExecutionRevoked


@pytest.fixture
def context():
    return AgentRunContext(
        execution_guard=None,
        trusted_identity=TrustedJobIdentity(
            job_id="00000000-0000-0000-0000-000000000801",
            session_id="00000000-0000-0000-0000-000000000802",
            user_id=7,
            attempt_count=2,
            lease_epoch=3,
            worker_id="terminal-contract-worker",
            input_identity="local-terminal-input",
        ),
    )


@pytest.fixture
def registry():
    # 只读取真实 Registry/Adapter 的静态元数据；不调用 Adapter.execute。
    # 本地 runner 直接执行，因此没有需要替换的远端 executor。
    return build_default_registry(build_default_adapters(executor=None))


def invocation(context, call_id="pc-call", response_id="local-response"):
    return RuntimeInvocationIdentity(
        provider_call_id=call_id,
        response_identity=response_id,
        response_identity_source="message_execution_id",
        provider_response_id=None,
        runtime_context=context,
    )


def invocation_id(identity):
    return build_invocation_id(
        job_id=identity.runtime_context.trusted_identity.job_id,
        response_identity=identity.response_identity,
        provider_call_id=identity.provider_call_id,
    )


def execution_command(identity, spec=PC_SPEC):
    return AlgorithmExecutionCommand(
        invocation_id=invocation_id(identity),
        capability_id=spec.capability_id,
        capability_version=spec.version,
        spec_digest=spec.spec_digest,
        provider_call_id=identity.provider_call_id,
        input_identity=identity.runtime_context.trusted_identity.input_identity,
    )


def diagnostic_result(identity, status="timed_out", spec=CDFM_SPEC):
    """纯领域契约数据，不表示运行过 CDFM 或远端算法。"""
    trusted = identity.runtime_context.trusted_identity
    code = {
        "timed_out": SafeErrorCode.ALGORITHM_TIMED_OUT,
        "execution_failed": SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
        "not_ready": SafeErrorCode.ALGORITHM_NOT_READY,
        "not_applicable": SafeErrorCode.ALGORITHM_NOT_APPLICABLE,
        "skipped": SafeErrorCode.ALGORITHM_NOT_APPLICABLE,
    }[status]
    call_id = invocation_id(identity)
    return AlgorithmResult(
        result_ref=build_result_ref(invocation_id=call_id, result_index=0),
        invocation_id=call_id,
        provider_call_id=identity.provider_call_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        status=status,
        diagnostics=Diagnostics(safe_error_code=code),
        provenance=AlgorithmResultProvenance(
            job_id=str(trusted.job_id),
            attempt_count=trusted.attempt_count,
            lease_epoch=trusted.lease_epoch,
            input_identity=trusted.input_identity,
            spec_digest=spec.spec_digest,
            invocation_id=call_id,
            capability_id=spec.capability_id,
            capability_version=spec.version,
        ),
    )


@pytest.fixture(scope="module")
def pc_payload():
    """确定种子的合成数值输入由真正的 causal-learn PC runner 计算。"""
    rng = np.random.default_rng(20261020)
    x = rng.normal(size=160)
    y = 0.8 * x + rng.normal(size=160)
    z = 0.5 * y + rng.normal(size=160)
    csv = "x,y,z\n" + "\n".join(
        f"{a:.12g},{b:.12g},{c:.12g}" for a, b, c in zip(x, y, z)
    )
    payload = run_pc_analysis(csv, alpha=0.05, test="fisherz")
    assert payload["success"] is True, "真实本地 PC runner 必须成功"
    return payload


def pc_result(identity, payload):
    command = execution_command(identity)
    return result_from_runner_payload(
        payload,
        command=command,
        context=identity.runtime_context.trusted_identity.to_mcp_context(
            invocation_id=command.invocation_id,
        ),
        capability_version=PC_SPEC.version,
    )


def planned_call(identity, spec=PC_SPEC):
    return PlannedToolCall(
        request=ToolCallRequest(call_id=identity.provider_call_id, tool_name=spec.tool_name),
        capability_id=spec.capability_id,
        requires=spec.requires,
        produces=spec.produces,
        timeout_seconds=spec.default_timeout_seconds,
        concurrency_key=spec.concurrency_key,
        default_concurrency=spec.default_concurrency,
    )


def tool_runtime(context, identity, events):
    return ToolRuntime(
        state={"message_execution_id": identity.response_identity},
        context=context,
        config={},
        stream_writer=events.append,
        tool_call_id=identity.provider_call_id,
        store=None,
    )


def terminal(identity, result):
    return build_terminal_invocation(
        identity=identity,
        tool_name=result.capability_id.replace(".", "_"),
        attempt_status=normalize_tool_status(result.status),
        started_at=datetime.now(timezone.utc),
        result_ref=result.result_ref,
        safe_error_code=result.diagnostics.safe_error_code,
    )


def decision_for(pc, timeout=None):
    assessments = [ResultAssessment(
        result_ref=pc.result_ref, disposition="primary", rationale="采用有效 PC 结果。",
    )]
    if timeout is not None:
        assessments.append(ResultAssessment(
            result_ref=timeout.result_ref, disposition="discarded",
            rationale="CDFM 超时，未完成分析且没有可用因果图。",
        ))
    return FinalAnalysisDecision(
        outcome="algorithm_supported",
        primary_result_ref=pc.result_ref,
        result_assessments=assessments,
        conflict_status="none",
        selection_rationale="采用 PC；超时算法不参与结论。",
        confidence="medium",
    )


@pytest.mark.parametrize("status", [
    "execution_failed", "timed_out", "not_ready", "not_applicable", "skipped",
])
@pytest.mark.parametrize("serialized", [False, True])
def test_command_classifier_preserves_non_success_and_publishes_no_artifact(
    context, status, serialized,
):
    identity = invocation(context)
    result = diagnostic_result(identity, status, PC_SPEC)
    record = terminal(identity, result)
    command = Command(update={
        "algorithm_results": {result.result_ref: result.model_dump(mode="json") if serialized else result},
        "action_ledger": {record.invocation_id: record},
    })
    actual, code, artifacts = _classify_execution_result(planned_call(identity), command)
    assert actual == normalize_tool_status(status)
    assert code == result.diagnostics.safe_error_code
    assert artifacts == frozenset()
    assert command.update["action_ledger"][record.invocation_id] == record


@pytest.mark.parametrize("unknown", [
    None, "unstructured", {}, {"artifact": "standardized_graph"},
    Command(update={"messages": []}), Command(update={"algorithm_results": {}}),
])
def test_command_classifier_rejects_missing_controlled_status(context, unknown):
    status, code, artifacts = _classify_execution_result(
        planned_call(invocation(context)), unknown,
    )
    assert (status, code, artifacts) == (
        "failed", SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID, frozenset(),
    )


def test_command_classifier_accepts_real_pc_result(context, pc_payload):
    identity = invocation(context)
    result = pc_result(identity, pc_payload)
    assert _classify_execution_result(planned_call(identity), Command(update={
        "algorithm_results": {result.result_ref: result},
    })) == ("succeeded", None, PC_SPEC.produces)


@pytest.mark.parametrize("status,summary", [
    ("execution_failed", "调用失败"), ("timed_out", "调用超时"),
    ("not_ready", "暂不可用"), ("not_applicable", "不适用"), ("skipped", "未执行"),
])
def test_terminal_command_atomically_writes_result_ledger_message_and_event(context, status, summary):
    identity = invocation(context)
    result = diagnostic_result(identity, status, PC_SPEC)
    events = []
    command = asyncio.run(algorithm_terminal_command(
        tool_runtime(context, identity, events), identity=identity,
        tool_name=PC_SPEC.tool_name, result=result, started_at=datetime.now(timezone.utc),
    ))
    assert command.update["algorithm_results"] == {result.result_ref: result}
    record = command.update["action_ledger"][result.invocation_id]
    assert record.final_status == normalize_tool_status(status)
    assert record.attempts[0].revision == 2
    assert record.attempts[0].status == record.final_status
    assert record.result_ref == result.result_ref
    message, = command.update["messages"]
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.tool_call_id == identity.provider_call_id
    assert json.loads(message.content)["status"] == status
    event, = events
    assert event["status"] == record.final_status
    assert event["summary"] == summary
    assert event["_event_key"] == tool_lifecycle_event_key(
        invocation_id=record.invocation_id, attempt_count=context.trusted_identity.attempt_count,
        event_type="tool_call_result",
    )


def dependent_plan(identity):
    """真实领域计划的生产者/消费者契约，不伪装成默认 CDFM 依赖。"""
    producer = planned_call(identity)
    consumer = PlannedToolCall(
        request=ToolCallRequest(call_id="graph-consumer", tool_name="consume_graph"),
        capability_id="graph.consumer", requires=frozenset({"standardized_graph"}),
        produces=frozenset({"graph_summary"}), timeout_seconds=10,
        concurrency_key="graph-consumer", default_concurrency=1,
    )
    return DependencyPlan(
        batches=((producer,), (consumer,)),
        dependencies={producer.request.call_id: frozenset(), consumer.request.call_id: frozenset({producer.request.call_id})},
        initial_artifacts=frozenset({"tabular_dataset"}),
    )


@pytest.mark.parametrize("swallow_cancellation", [False, True])
def test_local_asyncio_deadline_waits_for_cleanup_and_never_unblocks_dependents(
    context, pc_payload, swallow_cancellation,
):
    async def scenario():
        identity = invocation(context)
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        calls = []

        async def local_workload(request):
            calls.append(request.call_id)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not swallow_cancellation:
                    raise
                # 真正吞掉取消，甚至返回有效 Command；调度器仍须固定为超时。
                result = pc_result(identity, pc_payload)
                return Command(update={"algorithm_results": {result.result_ref: result}})
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        outcomes = await execute_dependency_plan(
            dependent_plan(identity), local_workload, timeout_seconds=0.05,
        )
        assert entered.is_set() and cleaned.is_set()
        assert calls == [identity.provider_call_id]
        assert [outcome.status for outcome in outcomes] == ["timed_out", "not_ready"]
        assert outcomes[0].safe_error_code == SafeErrorCode.ALGORITHM_TIMED_OUT
        assert outcomes[0].value is None
        assert all(not outcome.published_artifacts for outcome in outcomes)

    asyncio.run(scenario())


@pytest.mark.parametrize("revoked", [False, True])
def test_local_execution_cancel_or_revocation_propagates_and_cleans_up(context, revoked):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        calls = []

        async def local_workload(request):
            calls.append(request.call_id)
            try:
                entered.set()
                if revoked:
                    raise JobExecutionRevoked("local control-flow revocation")
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        task = asyncio.create_task(execute_dependency_plan(
            dependent_plan(invocation(context)), local_workload, timeout_seconds=5,
        ))
        await asyncio.wait_for(entered.wait(), timeout=2)
        if not revoked:
            task.cancel()
        with pytest.raises(JobExecutionRevoked if revoked else asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        assert calls == ["pc-call"]

    asyncio.run(scenario())


@pytest.mark.parametrize("pc_failure", [False, True])
def test_real_graph_checkpoints_queued_running_and_timeout_then_recovers(
    context, registry, pc_payload, pc_failure,
):
    async def scenario():
        identity_hook = _build_message_identity_middleware(registry=registry)
        dispatch = AlgorithmDependencyDispatchMiddleware(registry=registry, timeout_seconds=0.1)
        calls, events = [], []
        cleaned = asyncio.Event()

        @tool("causal_pc")
        async def local_pc(runtime: ToolRuntime[AgentRunContext]) -> Command:
            """将真实本地 PC runner 的产物写入生产终态 Command。"""
            calls.append(runtime.tool_call_id)
            identity = resolve_runtime_invocation(runtime)
            payload = run_pc_analysis("x\n1\n2\n3\n") if pc_failure else pc_payload
            return await algorithm_terminal_command(
                runtime, identity=identity, tool_name="causal_pc",
                result=pc_result(identity, payload), started_at=datetime.now(timezone.utc),
            )

        @tool("causal_cdfm")
        async def local_deadline_workload(runtime: ToolRuntime[AgentRunContext]) -> Command:
            """仅验证 CDFM 工具路由的本地 deadline；不执行 CDFM/MCP。"""
            calls.append(runtime.tool_call_id)
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        def register(state: ProjectDeepAgentState, runtime: Runtime[AgentRunContext]):
            return identity_hook.after_model(state, runtime)

        async def running(state: ProjectDeepAgentState, runtime: Runtime[AgentRunContext]):
            return await dispatch.aafter_model(state, runtime)

        builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
        builder.add_node("register", register)
        builder.add_node("running", running)
        builder.add_node("tools", ToolNode(
            [local_pc, local_deadline_workload], awrap_tool_call=dispatch.awrap_tool_call,
        ))
        builder.add_edge(START, "register")
        builder.add_edge("register", "running")
        builder.add_edge("running", "tools")
        builder.add_edge("tools", END)
        saver = InMemorySaver()
        graph = builder.compile(checkpointer=saver, interrupt_after=["register", "running"])
        config = {"configurable": {"thread_id": str(uuid4())}}
        await graph.ainvoke({
            "messages": [AIMessage(content="", tool_calls=[
                {"id": "pc-call", "name": "causal_pc", "args": {}},
                {"id": "cdfm-call", "name": "causal_cdfm", "args": {}},
            ])],
            "algorithm_results": {}, "action_ledger": {},
            "data_profile": DataProfile(
                row_count=160, column_count=3, column_names=("x", "y", "z"),
                numeric_columns=("x", "y", "z"),
            ),
        }, config, context=context)
        queued = await graph.aget_state(config)
        assert queued.next == ("running",)
        assert not calls
        assert len(queued.values["action_ledger"]) == 2
        assert all(record.final_status == "pending" and record.attempts[0].status == "queued"
                   and record.attempts[0].revision == 0 for record in queued.values["action_ledger"].values())

        # 重新编译图并从真实 saver 恢复；不是将内存 dict 当作 checkpoint。
        restored = builder.compile(checkpointer=saver, interrupt_after=["register", "running"])
        await restored.ainvoke(None, config, context=context)
        running_snapshot = await restored.aget_state(config)
        assert running_snapshot.next == ("tools",)
        assert not calls
        assert all(record.attempts[0].status == "running" and record.attempts[0].revision == 1
                   for record in running_snapshot.values["action_ledger"].values())

        async for mode, chunk in restored.astream(None, config, context=context, stream_mode=["custom", "updates"]):
            if mode == "custom":
                events.append(chunk)
        final = await restored.aget_state(config)
        assert final.next == ()
        assert sorted(calls) == ["cdfm-call", "pc-call"]
        assert cleaned.is_set()
        results = final.values["algorithm_results"]
        pc_status = "execution_failed" if pc_failure else "valid"
        assert {result.status for result in results.values()} == {pc_status, "timed_out"}
        timeout = next(result for result in results.values() if result.status == "timed_out")
        assert timeout.standardized_graph is None and timeout.raw_result_ref is None
        assert timeout.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_TIMED_OUT
        records = final.values["action_ledger"]
        assert all(record.attempts[0].revision == 2 for record in records.values())
        public_status = "failed" if pc_failure else "succeeded"
        assert {record.final_status for record in records.values()} == {public_status, "timed_out"}
        messages = [message for message in final.values["messages"] if isinstance(message, ToolMessage)]
        assert len(messages) == 2
        assert {json.loads(message.content)["status"] for message in messages} == {pc_status, "timed_out"}
        terminals = [event for event in events if event.get("type") == "tool_call_result"]
        assert len(terminals) == 2
        assert {event["status"] for event in terminals} == {public_status, "timed_out"}
        assert {event["summary"] for event in terminals} == {
            "调用失败" if pc_failure else "调用完成", "调用超时",
        }

        pc = next(result for result in results.values() if result.capability_id == "causal.pc")
        decision = (
            FinalAnalysisDecision(
                outcome="no_valid_algorithm", conflict_status="none",
                selection_rationale="PC 输入未通过校验，CDFM 超时，没有可用因果图。",
                confidence="low",
            ) if pc_failure else decision_for(pc, timeout)
        )
        validated = FinalizationGate(registry=registry).validate(
            decision=decision, algorithm_results=results,
            action_ledger=records, trusted_identity=context.trusted_identity,
        )
        assert validated.primary_result_ref == (None if pc_failure else pc.result_ref)
        history = [snapshot async for snapshot in restored.aget_state_history(config)]
        revisions = {record.attempts[0].revision for snapshot in history
                     for record in snapshot.values.get("action_ledger", {}).values()}
        assert revisions == {0, 1, 2}

        await restored.aupdate_state(config, {"action_ledger": queued.values["action_ledger"]})
        await restored.aupdate_state(config, {"action_ledger": running_snapshot.values["action_ledger"]})
        recovered = await restored.aget_state(config)
        assert recovered.values["action_ledger"] == records
        assert recovered.values["algorithm_results"] == results
        assert len(calls) == 2
        # 同一 revision 内容改变必须报错，不能静默覆盖 checkpoint。
        first = next(iter(records.values()))
        conflicting = first.model_copy(update={"result_ref": f"{first.invocation_id}:1"})
        with pytest.raises(ReducerConflictError):
            merge_action_ledger(records, {first.invocation_id: conflicting})

    asyncio.run(scenario())


def test_slow_terminal_sink_does_not_timeout_completed_local_pc_toolnode(
    context, registry, pc_payload,
):
    """真实 PC 在 ToolNode 内执行；终态 sink 等待不消耗算法 deadline。"""
    # fixture 已通过真实 runner 加载 PC 依赖，下面仍在工具内部重新计算。
    assert pc_payload["success"] is True
    rng = np.random.default_rng(20261021)
    csv = "x,y,z\n" + "\n".join(
        ",".join(f"{value:.12g}" for value in row)
        for row in rng.normal(size=(80, 3))
    )

    async def scenario():
        deadline = 1.0
        sink_entered, release_sink, command_ready = (
            asyncio.Event(), asyncio.Event(), asyncio.Event(),
        )
        sink_calls, sink_completed, custom_events, pc_calls = [], [], [], []
        command_ready_at, sink_times = [], []
        loop = asyncio.get_running_loop()

        async def slow_terminal_sink(payload):
            if payload.get("type") != "tool_call_result":
                return
            sink_calls.append(dict(payload))
            # 终态 Command 必须先返回调度器，sink 才能开始等待。
            assert command_ready.is_set()
            sink_times.append(loop.time())
            sink_entered.set()
            await release_sink.wait()
            sink_completed.append(dict(payload))

        runtime_context = replace(context, event_sink=slow_terminal_sink)
        identity_hook = _build_message_identity_middleware(registry=registry)
        dispatch = AlgorithmDependencyDispatchMiddleware(
            registry=registry, timeout_seconds=deadline,
        )

        @tool("causal_pc")
        async def local_pc(runtime: ToolRuntime[AgentRunContext]) -> Command:
            """执行生产本地 PC runner，并通过生产终态函数构造 Command。"""
            pc_calls.append(runtime.tool_call_id)
            started_at = datetime.now(timezone.utc)
            identity = resolve_runtime_invocation(runtime)
            payload = run_pc_analysis(csv, alpha=0.05, test="fisherz")
            assert payload["success"] is True
            command = await algorithm_terminal_command(
                runtime, identity=identity, tool_name="causal_pc",
                result=pc_result(identity, payload), started_at=started_at,
            )
            command_ready_at.append(loop.time())
            command_ready.set()
            return command

        def register(state: ProjectDeepAgentState, runtime: Runtime[AgentRunContext]):
            return identity_hook.after_model(state, runtime)

        async def running(state: ProjectDeepAgentState, runtime: Runtime[AgentRunContext]):
            return await dispatch.aafter_model(state, runtime)

        builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
        builder.add_node("register", register)
        builder.add_node("running", running)
        builder.add_node("tools", ToolNode(
            [local_pc], awrap_tool_call=dispatch.awrap_tool_call,
        ))
        builder.add_edge(START, "register")
        builder.add_edge("register", "running")
        builder.add_edge("running", "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": str(uuid4())}}

        async def consume_graph():
            async for mode, chunk in graph.astream({
                "messages": [AIMessage(content="", tool_calls=[
                    {"id": "slow-sink-pc", "name": "causal_pc", "args": {}},
                ])],
                "algorithm_results": {}, "action_ledger": {},
                "data_profile": DataProfile(
                    row_count=80, column_count=3, column_names=("x", "y", "z"),
                    numeric_columns=("x", "y", "z"),
                ),
            }, config, context=runtime_context, stream_mode=["custom", "updates"]):
                if mode == "custom":
                    custom_events.append(chunk)

        task = asyncio.create_task(consume_graph())
        try:
            await asyncio.wait_for(sink_entered.wait(), timeout=5)
            await asyncio.sleep(deadline * 1.2)
            assert loop.time() - sink_times[0] > deadline
            assert not task.done(), "图必须等待真实终态 sink 完成"
            assert not sink_completed
            assert len(sink_calls) == 1
            assert sink_calls[0]["status"] == "succeeded"
            release_sink.set()
            await asyncio.wait_for(task, timeout=5)
        finally:
            release_sink.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert pc_calls == ["slow-sink-pc"]
        assert len(command_ready_at) == 1
        assert command_ready_at[0] <= sink_times[0]
        assert sink_completed == sink_calls
        custom_terminal = [
            event for event in custom_events if event.get("type") == "tool_call_result"
        ]
        assert len(custom_terminal) == 1
        assert custom_terminal[0] == sink_calls[0]
        assert sink_calls[0]["summary"] == "调用完成"
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ()
        result, = snapshot.values["algorithm_results"].values()
        record, = snapshot.values["action_ledger"].values()
        assert result.status == "valid" and result.standardized_graph is not None
        assert record.final_status == "succeeded"
        assert record.attempts[0].status == "succeeded"
        assert record.attempts[0].revision == 2
        assert record.result_ref == result.result_ref
        messages = [message for message in snapshot.values["messages"] if isinstance(message, ToolMessage)]
        message, = messages
        assert message.status == "success"
        assert json.loads(message.content)["status"] == "valid"
        assert sink_calls[0]["_event_key"] == tool_lifecycle_event_key(
            invocation_id=record.invocation_id,
            attempt_count=context.trusted_identity.attempt_count,
            event_type="tool_call_result",
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["pending", "terminal_result"])
def test_gate_rejects_registered_call_without_terminal_result_and_parent_stops_report(
    context, registry, pc_payload, missing,
):
    pc_identity = invocation(context)
    pc = pc_result(pc_identity, pc_payload)
    cdfm_identity = invocation(context, "cdfm-call")
    record = build_pending_invocation(identity=cdfm_identity, tool_name="causal_cdfm")
    if missing == "terminal_result":
        record = terminal(cdfm_identity, diagnostic_result(cdfm_identity))
    results = {pc.result_ref: pc}
    pc_record = terminal(pc_identity, pc)
    ledger = {pc.invocation_id: pc_record, record.invocation_id: record}
    gate = FinalizationGate(registry=registry)
    match = "not terminal" if missing == "pending" else "no terminal result"
    with pytest.raises(StructuredResponseError, match=match) as caught:
        gate.validate(decision=decision_for(pc), algorithm_results=results,
                      action_ledger=ledger, trusted_identity=context.trusted_identity)
    assert caught.value.rule is None
    with pytest.raises(StructuredResponseError, match=match):
        asyncio.run(_finalization_gate_node({
            "deep_agent_decision": decision_for(pc),
            "deep_agent_algorithm_results": results,
            "deep_agent_action_ledger": ledger,
        }, runtime=Runtime(context=context), config={}, gate=gate))


@pytest.mark.parametrize("artifact", ["graph", "raw"])
def test_timeout_model_rejects_graph_and_raw_artifact(context, pc_payload, artifact):
    result = diagnostic_result(invocation(context, "cdfm-call"))
    data = result.model_dump(mode="json")
    if artifact == "graph":
        data["standardized_graph"] = pc_result(invocation(context), pc_payload).standardized_graph.model_dump(mode="json")
    else:
        data.update(raw_result_ref="/raw_algorithm_results/forbidden.json", raw_result_sha256="a" * 64,
                    raw_result_size_bytes=2, raw_result_serialization_version="canonical-json-v1")
    with pytest.raises(ValidationError, match="timed-out AlgorithmResult"):
        AlgorithmResult.model_validate(data)


def test_latest_ai_without_calls_does_not_redispatch_previous_call(context, registry):
    state = {
        "messages": [AIMessage(content="", tool_calls=[
            {"id": "previous-pc", "name": "causal_pc", "args": {}},
        ]), ToolMessage(content="completed", tool_call_id="previous-pc"),
            AIMessage(content="分析结束", tool_calls=[])],
        "action_ledger": {}, "message_execution_id": "previous-response",
    }
    assert _tool_calls_from_state(state, frozenset({"causal_pc"})) == []
    hook = _build_message_identity_middleware(registry=registry)
    runtime = Runtime(context=context)
    update = hook.after_model(state, runtime)
    assert not update.get("action_ledger")
    state.update(update)
    dispatch = AlgorithmDependencyDispatchMiddleware(registry=registry)
    assert asyncio.run(dispatch.aafter_model(state, runtime)) is None


@pytest.mark.parametrize("collision_depth", [0, 1, 3])
def test_report_adds_deterministic_cdfm_timeout_limit_and_avoids_nested_block_id_collisions(collision_depth):
    blocks = [MarkdownBlock(id="pc-conclusion", content="结论采用已通过校验的 PC 结果。")]
    if collision_depth:
        blocks.append(SectionBlock(id="section", title="既有内容", children=[
            MarkdownBlock(id="algorithm_execution_limits" + "_" * index, content="保留原有正文。")
            for index in range(collision_depth)
        ]))
    draft = ReportDraft(title="PC 与 CDFM 分析", blocks=blocks)
    original = draft.model_dump(mode="json")
    summary = {"algorithms": [
        {"capability_id": PC_SPEC.capability_id, "status": "valid",
         "public_name": PC_SPEC.method_definition().public_name},
        {"capability_id": CDFM_SPEC.capability_id, "status": "timed_out",
         "public_name": CDFM_SPEC.method_definition().public_name},
    ]}
    document = build_report_document(draft, execution_summary=summary)
    limit = document.blocks[-1]
    assert isinstance(limit, MarkdownBlock)
    assert limit.id == "algorithm_execution_limits" + "_" * collision_depth
    assert "分析限制" in limit.content and "CDFM" in limit.content
    assert "超时" in limit.content and "未完成" in limit.content and "未提供可用因果图" in limit.content
    assert "causal.cdfm" not in limit.content
    assert "PC" not in limit.content
    assert document.blocks[0] == draft.blocks[0]
    ids = [block.id for block in iter_report_blocks(document.blocks)]
    assert len(ids) == len(set(ids))
    assert draft.model_dump(mode="json") == original
    assert document.execution_summary == summary


def test_report_without_timeout_does_not_append_limitation():
    draft = ReportDraft(title="PC 分析", blocks=[MarkdownBlock(id="pc", content="PC 结果。")])
    document = build_report_document(draft, execution_summary={
        "algorithms": [{"capability_id": "causal.pc", "status": "valid"}],
    })
    assert document.blocks == draft.blocks


@pytest.mark.parametrize("spec,blocked_by", [
    (PC_SPEC, "row_count"),
    (DIRECT_LINGAM_SPEC, "row_count"),
    (CDFM_SPEC, "row_count"),
    (DIRECT_LINGAM_SPEC, "numeric_columns"),
    (CDFM_SPEC, "numeric_columns"),
])
def test_real_toolnode_rejection_preserves_method_snapshot(context, registry, spec, blocked_by):
    """真实调度器拒绝未满足依赖的输入，不执行算法或使用执行器替身。"""
    profile = DataProfile(
        row_count=1 if blocked_by == "row_count" else 3,
        column_count=2, column_names=("A", "B"),
        numeric_columns=("A", "B") if blocked_by == "row_count" else ("B",),
        categorical_columns=() if blocked_by == "row_count" else ("A",),
    )
    tools = [item.to_langchain_tool() for item in build_algorithm_tools(
        registry, runtime_context=context, data_profile=profile,
    )]
    dispatch = AlgorithmDependencyDispatchMiddleware(registry=registry)
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("tools", ToolNode(
        tools, handle_tool_errors=False, awrap_tool_call=dispatch.awrap_tool_call,
    ))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    state = asyncio.run(builder.compile().ainvoke({
        "messages": [AIMessage(content="", tool_calls=[{
            "id": "rejected-call", "name": spec.tool_name, "args": {},
        }])],
        "message_execution_id": "rejected-response",
        "data_profile": profile, "algorithm_results": {}, "action_ledger": {},
    }, context=context))
    result, = state["algorithm_results"].values()
    assert result.status == "not_ready"
    assert result.method == spec.method_definition()
    assert result.implementation is None
    assert result.standardized_graph is None and result.raw_result_ref is None
    record, = state["action_ledger"].values()
    assert record.final_status == "not_ready" and record.result_ref == result.result_ref
    message, = [item for item in state["messages"] if isinstance(item, ToolMessage)]
    restored = AlgorithmResult.model_validate_json(message.content)
    assert restored.method == result.method
