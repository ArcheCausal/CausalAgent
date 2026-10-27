"""真实 Tool/Adapter/MCP/OLS 协作；源图是已知输入，不证明发现图准确性或数据库绑定。"""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import ToolRuntime
from langgraph.runtime import Runtime

from Agent.causal_agent.analysis_context import build_context_commit, build_report_analysis_context
from Agent.deep_agent import AgentRunContext, FinalizationGate, TrustedJobIdentity
from Agent.deep_agent.finalization import StructuredResponseError
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent.state import ProjectDeepAgentState
from Agent.deep_agent_tools import build_default_registry
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.dependency_dispatch import build_algorithm_dependency_middleware
from Agent.deep_agent_tools.identity import build_invocation_id, build_result_ref
from Agent.deep_agent_tools.models import AlgorithmResult, AlgorithmResultProvenance, StandardizedGraph
from Agent.deep_agent_tools.runtime_updates import (
    build_pending_invocation, build_terminal_invocation, resolve_runtime_invocation,
)
from tests.support.bound_analysis_input import bind_analysis_input
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor


CSV = "z,x,y,other\n0,1,3,0\n1,3,8,1\n2,2,7,0\n3,5,14,1\n4,4,13,0\n5,7,20,1\n"
PARAMETERS = {"target": "y", "treatment": "x", "columns": ["z", "x", "y", "other"]}


def _setup(*, kinds=None, edge_type="directed", csv_data=CSV):
    executor = LocalAlgorithmExecutor(csv_data, semantic_kinds=kinds)
    identity = bind_analysis_input(TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000501",
        session_id="00000000-0000-0000-0000-000000000502", user_id=7,
        attempt_count=1, lease_epoch=3, worker_id="worker-ate", input_identity="source-input",
    ), csv_data, semantic_kinds=kinds, dataset_id=51)
    context = AgentRunContext(
        execution_guard=None, trusted_identity=identity, algorithm_executor=executor,
        filesystem_backend=build_in_memory_backend(user_id=7),
    )
    registry = build_default_registry(build_default_adapters(
        executor=executor, raw_backend=context.filesystem_backend,
    ))
    runtime = ToolRuntime(
        tool_call_id="source-call", state={"message_execution_id": "ate-check-response",
        "data_profile": executor.data_profile}, context=context,
        config={}, stream_writer=[].append, store=None,
    )
    invocation_id = build_invocation_id(job_id=identity.job_id,
        response_identity="ate-check-response", provider_call_id="source-call")
    graph = StandardizedGraph(
        graph_semantics="pdag", nodes=["z", "x", "y", "other"], edges=[
            {"source": "z", "target": "x", "edge_type": "directed"},
            {"source": "z", "target": "y", "edge_type": "directed"},
            {"source": "x", "target": "y", "edge_type": edge_type},
        ],
    )
    source = AlgorithmResult(
        result_ref=build_result_ref(invocation_id=invocation_id), invocation_id=invocation_id,
        provider_call_id="source-call", capability_id=PC_SPEC.capability_id,
        capability_version=PC_SPEC.version, status="valid", standardized_graph=graph,
        method=PC_SPEC.method_definition(), provenance=AlgorithmResultProvenance(
            job_id=str(identity.job_id), attempt_count=identity.attempt_count, lease_epoch=identity.lease_epoch,
            input_identity=identity.current_input_identity(), source_input_identity=identity.source_input_identity(),
            analysis_dataset_id=51, spec_digest=PC_SPEC.spec_digest,
        ),
    )
    source_record = build_terminal_invocation(
        identity=resolve_runtime_invocation(runtime), tool_name="causal_pc", attempt_status="succeeded",
        started_at=datetime.now(timezone.utc), result_ref=source.result_ref,
    )
    return context, registry, source, source_record


def _call(context, registry, source, source_record, *, ref=None, treatment="x", outcome="y"):
    tool = next(item for item in build_algorithm_tools(registry, runtime_context=context)
                if item.name == "causal_ate").to_langchain_tool()
    runtime = ToolRuntime(
        tool_call_id="ate-call", state={"message_execution_id": "ate-check-response",
        "data_profile": context.algorithm_executor.data_profile,
        "algorithm_results": {source.result_ref: source},
        "action_ledger": {source_record.invocation_id: source_record}},
        context=context, config={}, stream_writer=[].append, store=None,
    )
    command = asyncio.run(tool.coroutine(runtime=runtime, graph_result_ref=ref or source.result_ref,
        treatment=treatment, outcome=outcome))
    result = next(iter(command.update["algorithm_results"].values()))
    record = next(iter(command.update["action_ledger"].values()))
    message = json.loads(command.update["messages"][0].content)
    assert message["status"] == result.status
    assert message["ate_check"] == (result.ate_check.model_dump(mode="json") if result.ate_check else None)
    assert record.result_ref == result.result_ref
    assert record.attempts[0].safe_error_code == result.diagnostics.safe_error_code
    return result, record


def _validate(context, registry, source, source_record, result, record, *, evidence_only=False):
    valid = [item for item in (source, result) if item.status == "valid"]
    decision = {
        "outcome": "evidence_only" if evidence_only else "algorithm_supported",
        "primary_result_ref": None if evidence_only else source.result_ref,
        "result_assessments": [{"result_ref": item.result_ref,
            "disposition": "discarded" if evidence_only else "primary" if item is source else "supporting",
            "rationale": "依据输入图和实际工具结果评估。"} for item in valid],
        "conflict_status": "none", "conflicts": [], "revision_proposals": [],
        "selection_rationale": "保留方法边界。", "confidence": "low", "confidence_basis": ["tool contract"],
    }
    return FinalizationGate(registry=registry).validate(
        decision=decision, algorithm_results={item.result_ref: item for item in (source, result)},
        action_ledger={item.invocation_id: item for item in (source_record, record)},
        trusted_identity=context.trusted_identity, route_decision="fold", analysis_parameters=PARAMETERS,
    )


def test_continuous_confounder_runs_real_ols_and_passes_gate():
    context, registry, source, source_record = _setup(kinds={"z": "continuous"})
    result, record = _call(context, registry, source, source_record)
    assert result.status == "valid"
    assert result.ate_check.adjustment_set == ("z",)
    assert result.ate_check.execution_columns == ("x", "y", "z")
    assert result.causal_effect.estimate == pytest.approx(2)
    assert len(context.algorithm_executor.calls) == 1
    _validate(context, registry, source, source_record, result, record)


@pytest.mark.parametrize("kind", ["category", "ordinal", "binary"])
def test_encoded_confounder_is_rejected_before_estimation_and_satisfies_gate(kind):
    context, registry, source, source_record = _setup(kinds={"z": kind})
    result, record = _call(context, registry, source, source_record)
    assert result.status == "not_applicable"
    assert result.ate_check.reason_code == "ATE_ADJUSTMENT_SEMANTICS_UNSUPPORTED"
    assert result.ate_check.adjustment_set == ("z",)
    assert context.algorithm_executor.calls == []
    _validate(context, registry, source, source_record, result, record)
    state = {"deep_agent_algorithm_results": {
        source.result_ref: source, result.result_ref: result}, "analysis_parameters": PARAMETERS}
    facts = build_report_analysis_context(state)
    commit = build_context_commit(state)
    restored = build_report_analysis_context({"report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": commit["algorithm_summary"]}})
    assert restored == facts
    assert any(item["ate_check"] and item["ate_check"]["reason_code"] == result.ate_check.reason_code
               and item["diagnostics"]["summary"] == result.diagnostics.summary for item in restored["results"])


def test_unused_category_does_not_block_real_estimation():
    context, registry, source, source_record = _setup(kinds={"other": "category"})
    result, record = _call(context, registry, source, source_record)
    assert result.status == "valid"
    _validate(context, registry, source, source_record, result, record)


def test_undirected_graph_has_controlled_reason_and_null_adjustment():
    context, registry, source, source_record = _setup(edge_type="undirected")
    result, record = _call(context, registry, source, source_record)
    assert result.status == "not_applicable"
    assert result.ate_check.reason_code == "ATE_GRAPH_HAS_UNDIRECTED_EDGE"
    assert result.ate_check.adjustment_set is None
    assert "未定向" in result.diagnostics.summary
    _validate(context, registry, source, source_record, result, record)


def test_wrong_ref_returns_atomic_business_rejection():
    context, registry, source, source_record = _setup()
    result, record = _call(context, registry, source, source_record, ref="absent:0")
    assert result.status == "invalid_input"
    assert record.final_status == "failed"
    assert result.diagnostics.safe_error_code == "ALGORITHM_INPUT_INVALID"
    assert result.ate_check is None
    with pytest.raises(StructuredResponseError, match="applicability check"):
        _validate(context, registry, source, source_record, result, record)


@pytest.mark.parametrize("evidence_only", [False, True])
def test_another_variable_pair_does_not_satisfy_current_requirement(evidence_only):
    context, registry, source, source_record = _setup()
    result, record = _call(context, registry, source, source_record, treatment="z", outcome="y")
    assert result.status == "valid"
    with pytest.raises(StructuredResponseError, match="applicability check"):
        _validate(context, registry, source, source_record, result, record, evidence_only=evidence_only)


def test_budget_closure_keeps_dataset_identity_without_claiming_a_check():
    context, registry, source, source_record = _setup()
    runtime = ToolRuntime(tool_call_id="budget-ate-call",
        state={"message_execution_id": "budget-response"}, context=context,
        config={}, stream_writer=[].append, store=None)
    pending = build_pending_invocation(identity=resolve_runtime_invocation(runtime), tool_name="causal_ate")
    middleware = build_algorithm_dependency_middleware(registry=registry)
    update = asyncio.run(middleware.aafter_agent({"action_ledger": {pending.invocation_id: pending}},
        Runtime(context=context)))
    result = next(iter(update["algorithm_results"].values()))
    record = next(iter(update["action_ledger"].values()))
    assert result.provenance.analysis_dataset_id == record.analysis_dataset_id == 51
    assert result.provenance.source_input_identity == record.source_input_identity == "source-input"
    assert result.ate_check is None
    with pytest.raises(StructuredResponseError, match="applicability check"):
        _validate(context, registry, source, source_record, result, record)


def test_real_postcheck_failure_satisfies_gate_only_with_its_record():
    csv_data = "z,x,y,other\n" + "".join(
        f"{i},{2*i+i%3},{'1e308' if i%2 else '-1e308'},{i%2}\n" for i in range(12)
    )
    context, registry, source, source_record = _setup(csv_data=csv_data)
    result, record = _call(context, registry, source, source_record)
    assert result.status == "execution_failed"
    assert result.ate_check.applicability == "ready"
    _validate(context, registry, source, source_record, result, record)
    old_result = AlgorithmResult.model_validate(result.model_dump() | {"ate_check": None})
    with pytest.raises(StructuredResponseError, match="applicability check"):
        _validate(context, registry, source, source_record, old_result, record)


@pytest.mark.parametrize("change", [
    {"graph_result_ref": "absent-source:0"},
    {"adjustment_set": [], "execution_columns": ["x", "y"]},
])
def test_gate_independently_checks_record_source_and_full_adjustment(change):
    context, registry, source, source_record = _setup(kinds={"z": "category"})
    result, record = _call(context, registry, source, source_record)
    # 直接测试 Gate 对可解析但内容错误的输入契约的拒绝，不替换执行器。
    changed = AlgorithmResult.model_validate(result.model_dump() | {
        "ate_check": result.ate_check.model_dump() | change,
    })
    with pytest.raises(StructuredResponseError, match="applicability check"):
        _validate(context, registry, source, source_record, changed, record)


def test_dispatch_deadline_retains_completed_local_check_for_current_call():
    context, registry, source, source_record = _setup()
    tools = [item.to_langchain_tool() for item in build_algorithm_tools(registry, runtime_context=context)]
    middleware = build_algorithm_dependency_middleware(registry=registry, timeout_seconds=0.1)
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=False,
        awrap_tool_call=middleware.awrap_tool_call))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    state = asyncio.run(builder.compile().ainvoke({
        "messages": [AIMessage(content="", tool_calls=[{"name": "causal_ate",
            "args": {"graph_result_ref": source.result_ref, "treatment": "x", "outcome": "y"},
            "id": "deadline-ate-call", "type": "tool_call"}, {"name": "causal_ate",
            "args": {"graph_result_ref": "absent-source:0", "treatment": "x", "outcome": "y"},
            "id": "unchecked-ate-call", "type": "tool_call"}])],
        "message_execution_id": "deadline-response", "data_profile": context.algorithm_executor.data_profile,
        "algorithm_results": {source.result_ref: source},
        "action_ledger": {source_record.invocation_id: source_record},
        "rag_evidence": {}, "web_evidence": {},
    }, context=context))
    result = next(item for item in state["algorithm_results"].values()
        if item.provider_call_id == "deadline-ate-call")
    unchecked = next(item for item in state["algorithm_results"].values()
        if item.provider_call_id == "unchecked-ate-call")
    record = state["action_ledger"][result.invocation_id]
    assert len(context.algorithm_executor.calls) == 1
    assert result.status == "timed_out"
    assert result.ate_check.applicability == "ready"
    assert result.ate_check.graph_result_ref == source.result_ref
    assert result.standardized_graph is None and result.raw_result_ref is None
    assert record.final_status == "timed_out"
    assert unchecked.status == "invalid_input" and unchecked.ate_check is None
    decision = _validate(context, registry, source, source_record, result, record)
    FinalizationGate(registry=registry).validate(decision=decision,
        algorithm_results=state["algorithm_results"], action_ledger=state["action_ledger"],
        trusted_identity=context.trusted_identity, route_decision="fold", analysis_parameters=PARAMETERS)
