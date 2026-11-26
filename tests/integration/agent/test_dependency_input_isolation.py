"""真实 ToolNode/Command 与本地 PC runner 的输入隔离；不连接 MCP 或数据库。

本地工具直接调用生产 PC runner 和结果转换函数，没有替代 AlgorithmExecutor。
参数和审批用例使用生产 AlgorithmTool/Adapter，拒绝发生在外部执行之前。
"""

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.runtime import Runtime
from langgraph.types import Command

from Agent.causal.causalachieve import run_pc_analysis
from Agent.deep_agent.context import AnalysisInputRef, AgentRunContext, TrustedJobIdentity
from Agent.measurement_semantics import semantic_kinds_from_validation_report
from Agent.deep_agent.graph import _build_message_identity_middleware
from Agent.deep_agent.state import ProjectDeepAgentState
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.adapters.normalization import result_from_runner_payload
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.dependency_dispatch import AlgorithmDependencyDispatchMiddleware
from Agent.deep_agent_tools.dependency_planner import ToolCallRequest, build_dependency_plan, execute_dependency_plan
from Agent.deep_agent_tools.identity import build_invocation_id
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, DataProfile, InvocationRecord
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.deep_agent_tools.runtime_updates import algorithm_terminal_command, normalize_tool_status, resolve_runtime_invocation
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor


def _csv():
    rows = np.random.default_rng(20261021).normal(size=(100, 3))
    return "x,y,z\n" + "\n".join(
        ",".join(f"{value:.12g}" for value in row) for row in rows
    )


def _context(events, *, semantic_kinds=None):
    report = LocalAlgorithmExecutor(_csv(), semantic_kinds=semantic_kinds).validation_report
    analysis_input = AnalysisInputRef()
    analysis_input.bind(
        dataset_id=1, object_id=1, content_hash="explicit-local-pc-csv",
        filename="local.csv", semantic_kinds=semantic_kinds_from_validation_report(report),
    )
    return AgentRunContext(
        execution_guard=None,
        trusted_identity=TrustedJobIdentity(
            job_id=str(uuid4()), session_id=str(uuid4()), user_id=7,
            attempt_count=1, lease_epoch=1, worker_id="local-input-isolation",
            input_identity="explicit-local-pc-csv",
            analysis_input=analysis_input,
        ), event_sink=events.append,
    )


def _result(payload, context, spec, call_id, response_id):
    identity = context.trusted_identity
    invocation_id = build_invocation_id(
        job_id=identity.job_id, response_identity=response_id, provider_call_id=call_id,
    )
    command = AlgorithmExecutionCommand(
        invocation_id=invocation_id, provider_call_id=call_id,
        capability_id=spec.capability_id, capability_version=spec.version,
        spec_digest=spec.spec_digest, input_identity=identity.input_identity,
    )
    return result_from_runner_payload(
        payload, command=command,
        context=identity.to_mcp_context(invocation_id=invocation_id), capability_version=spec.version,
    )


def _call(name, call_id, args=None):
    return {"name": name, "id": call_id, "args": args or {}, "type": "tool_call"}


async def _toolnode(profile, calls, *, local_pc=True, parameter_rejection=False, semantic_kinds=None):
    events, entered, runner_payloads, handler_errors = [], [], {}, []
    context = _context(events, semantic_kinds=semantic_kinds)
    registry = build_default_registry(build_default_adapters(executor=None))
    tools = [item.to_langchain_tool() for item in build_algorithm_tools(registry, runtime_context=context)]

    @tool("causal_pc")
    async def pc(runtime: ToolRuntime[AgentRunContext]) -> Command:
        """通过生产本地 PC runner 真实计算本次显式 CSV。"""
        identity = resolve_runtime_invocation(runtime)
        started_at = datetime.now(timezone.utc)
        payload = await asyncio.to_thread(run_pc_analysis, _csv(), alpha=0.05, test="fisherz")
        runner_payloads[runtime.tool_call_id] = payload
        return await algorithm_terminal_command(
            runtime, identity=identity, tool_name="causal_pc", started_at=started_at,
            result=_result(payload, context, PC_SPEC, identity.provider_call_id, identity.response_identity),
        )

    if local_pc:
        tools = [pc if item.name == "causal_pc" else item for item in tools]
    dispatch = AlgorithmDependencyDispatchMiddleware(registry=registry, timeout_seconds=60)
    hook = _build_message_identity_middleware(registry=registry)

    def register(state: ProjectDeepAgentState, runtime: Runtime[AgentRunContext]):
        return hook.after_model(state, runtime)

    async def running(state: ProjectDeepAgentState, runtime: Runtime[AgentRunContext]):
        return await dispatch.aafter_model(state, runtime)

    async def observe(request, handler):
        async def observed_handler(actual_request):
            entered.append(actual_request.tool_call["id"])
            try:
                return await handler(actual_request)
            except Exception as exc:
                handler_errors.append(exc)
                raise
        return await dispatch.awrap_tool_call(request, observed_handler)

    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("register", register)
    builder.add_node("running", running)
    builder.add_node("tools", ToolNode(tools, awrap_tool_call=observe, handle_tool_errors=False))
    builder.add_edge(START, "register")
    builder.add_edge("register", "running")
    builder.add_edge("running", "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid4())}}
    state = await graph.ainvoke({
        "messages": [AIMessage(content="", tool_calls=calls)], "data_profile": profile,
        "algorithm_results": {}, "action_ledger": {},
    }, config, context=context)
    if handler_errors and not parameter_rejection:
        raise handler_errors[0]
    if parameter_rejection:
        assert len(handler_errors) == 1 and isinstance(handler_errors[0], ToolInvocationError)
    results = {result.provider_call_id: result for result in state["algorithm_results"].values()}
    records = {record.provider_call_id: record for record in state["action_ledger"].values()}
    messages = [message for message in state["messages"] if isinstance(message, ToolMessage)]
    terminals = [event for event in events if event.get("type") == "tool_call_result"]
    ids = {call["id"] for call in calls}
    assert results.keys() == records.keys() == ids
    assert len(messages) == len(terminals) == len(ids)
    assert {message.tool_call_id for message in messages} == ids
    assert len({event["_event_key"] for event in terminals}) == len(ids)
    for call_id, result in results.items():
        record = InvocationRecord.model_validate(records[call_id])
        assert record.invocation_id == result.invocation_id
        assert record.final_status == record.attempts[0].status == normalize_tool_status(result.status)
        assert record.attempts[0].revision == 2
        assert record.result_ref == result.result_ref
    assert (await graph.aget_state(config)).next == ()
    return results, entered, runner_payloads, messages


@pytest.mark.parametrize("call_ids", [("pc",), ("pc-1", "pc-2")])
def test_cdfm_missing_input_does_not_stop_real_local_pc_or_merge_same_algorithm_calls(call_ids):
    # 历史输入只确认列名和规模，数值存储类型未知；不能发布 CDFM 输入。
    profile = DataProfile(row_count=100, column_count=3, column_names=("x", "y", "z"))
    calls = [_call("causal_pc", call_id) for call_id in call_ids] + [_call("causal_cdfm", "cdfm")]
    results, entered, payloads, _ = asyncio.run(_toolnode(profile, calls))
    assert set(entered) == set(payloads) == set(call_ids)
    for call_id in call_ids:
        assert payloads[call_id]["success"] is True
        assert results[call_id].status == "valid"
        assert results[call_id].standardized_graph is not None
    assert len({result.invocation_id for result in results.values()}) == len(calls)
    blocked = results["cdfm"]
    assert blocked.status == "not_ready"
    assert blocked.diagnostics.summary == "当前画像未确认全部列为数值类型，未执行算法。"
    assert blocked.raw_result_ref is None and blocked.standardized_graph is None


def test_both_inputs_missing_produce_separate_unexecuted_terminals():
    results, entered, payloads, _ = asyncio.run(_toolnode(
        DataProfile(row_count=0, column_count=0),
        [_call("causal_pc", "pc"), _call("causal_cdfm", "cdfm")],
    ))
    assert entered == [] and payloads == {}
    assert {result.status for result in results.values()} == {"not_ready"}
    assert results["pc"].diagnostics.summary == "当前画像未确认所需表格输入，未执行算法。"
    assert results["cdfm"].diagnostics.summary == "当前画像未确认全部列为数值类型，未执行算法。"
    assert all(result.standardized_graph is None and result.raw_result_ref is None for result in results.values())


@pytest.mark.parametrize("mode", ["missing_values", "removed_preprocessing_argument", "invalid_parameter"])
def test_algorithm_contract_rejects_incompatible_data_or_removed_arguments(mode):
    csv = "x,y,label\n1,3,red\n2,4,blue\n3,8,red\n4,9,blue\n"
    if mode == "missing_values":
        csv = "x,y,label\n1,3,red\n2,,blue\n3,8,red\n4,9,blue\n"
    profile = build_data_profile_from_csv(csv)
    args = {}
    if mode == "removed_preprocessing_argument":
        args = {"preprocessing": {"steps": [{"operation": "categorical_encode", "columns": ["label"], "method": "ordinal"}]}}
    elif mode == "invalid_parameter":
        # 生产 StructuredTool 在调用函数前拒绝不满足科学参数 schema 的值。
        args = {"alpha": 2.0}
    results, entered, payloads, messages = asyncio.run(_toolnode(
        profile, [_call("causal_pc", "pc", args), _call("causal_cdfm", "cdfm")],
        local_pc=False, parameter_rejection=mode in {"removed_preprocessing_argument", "invalid_parameter"},
    ))
    assert entered == ["pc"] and payloads == {}
    assert results["cdfm"].status == "not_ready"
    assert results["pc"].standardized_graph is None and results["pc"].raw_result_ref is None
    message = json.loads(next(message.content for message in messages if message.tool_call_id == "pc"))
    if mode == "invalid_parameter":
        # StructuredTool 在调用函数前执行 Pydantic 参数校验；调度器保存受控失败。
        assert results["pc"].status == "execution_failed"
        assert results["pc"].diagnostics.safe_error_code == "ALGORITHM_EXECUTION_FAILED"
    elif mode == "removed_preprocessing_argument":
        assert results["pc"].status == "execution_failed"
        assert results["pc"].diagnostics.safe_error_code == "ALGORITHM_EXECUTION_FAILED"
    else:
        assert results["pc"].status == "not_applicable"
        assert results["pc"].diagnostics.safe_error_code == "ALGORITHM_NOT_APPLICABLE"
        assert "缺失值" in (results["pc"].diagnostics.summary or "")


def test_category_snapshot_blocks_continuous_only_algorithm_without_stopping_real_pc():
    profile = build_data_profile_from_csv(_csv())
    assert profile.categorical_columns == ()
    results, entered, payloads, _ = asyncio.run(_toolnode(
        profile, [_call("causal_pc", "pc"), _call("causal_direct_lingam", "lingam")],
        semantic_kinds={"x": "category"},
    ))
    assert set(entered) == {"pc", "lingam"}
    assert set(payloads) == {"pc"}
    assert payloads["pc"]["success"] is True
    assert results["pc"].status == "valid"
    assert results["lingam"].status == "not_applicable"
    assert "分类变量" in results["lingam"].diagnostics.summary
    assert results["lingam"].raw_result_ref is None


@pytest.mark.parametrize("producer_missing", [True, False])
def test_real_pc_failure_or_missing_input_only_blocks_transitive_downstream(producer_missing):
    # 自定义的只是 DAG 元数据。唯一执行的计算仍为生产 PC runner。
    specs = [
        PC_SPEC.model_copy(update={"capability_id": "contract.producer", "tool_name": "producer",
                                   "requires": frozenset({"absent"} if producer_missing else {"tabular_dataset"}),
                                   "produces": frozenset({"prepared"})}),
        PC_SPEC.model_copy(update={"capability_id": "contract.child", "tool_name": "child",
                                   "requires": frozenset({"prepared"}), "produces": frozenset({"derived"})}),
        PC_SPEC.model_copy(update={"capability_id": "contract.grandchild", "tool_name": "grandchild",
                                   "requires": frozenset({"derived"}), "produces": frozenset({"final"})}), PC_SPEC,
    ]
    adapters = build_default_adapters(executor=None)
    registry = build_default_registry({spec.capability_id: adapters["causal.pc"] for spec in specs}, specs=specs)
    plan = build_dependency_plan(
        tuple(ToolCallRequest(name, name) for name in ("producer", "child", "grandchild", "causal_pc")),
        registry=registry, initial_artifacts={"tabular_dataset"},
    )
    entered, payloads = [], {}
    context = _context([])

    async def execute(request):
        entered.append(request.call_id)
        # 非数值字符串 CSV 触发 runner 的真实输入失败，不预置失败结果。
        csv = "x,y\na,b\nc,d\n" if request.call_id == "producer" else _csv()
        payload = await asyncio.to_thread(run_pc_analysis, csv, alpha=0.05, test="fisherz")
        payloads[request.call_id] = payload
        return _result(payload, context, registry.get_by_tool_name(request.tool_name).spec, request.call_id, "dag")

    outcomes = {outcome.request.call_id: outcome for outcome in asyncio.run(execute_dependency_plan(plan, execute))}
    assert set(entered) == ({"causal_pc"} if producer_missing else {"producer", "causal_pc"})
    assert payloads["causal_pc"]["success"] is True
    assert outcomes["causal_pc"].status == "succeeded"
    assert outcomes["producer"].status == ("not_ready" if producer_missing else "failed")
    if not producer_missing:
        assert payloads["producer"]["success"] is False
    for call_id in ("child", "grandchild"):
        assert outcomes[call_id].status == "not_ready"
        assert outcomes[call_id].value is None
        assert not outcomes[call_id].published_artifacts
