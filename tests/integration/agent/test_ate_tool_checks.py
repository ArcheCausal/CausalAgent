"""真实 Tool/Adapter/MCP/OLS 协作；源图是已知输入，不证明发现图准确性或数据库绑定。"""

import asyncio
from io import StringIO
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from functools import partial

import numpy as np
import pandas as pd
import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import ToolRuntime
from langgraph.runtime import Runtime

from Agent.causal_agent.analysis_context import build_context_commit, build_report_analysis_context
from Agent.CausalAgentMCP.executor_pool import BoundedProcessPool
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
from Agent.deep_agent_tools.functional_form import build_effect_request
from Agent.deep_agent_tools.models import (
    ActionAttempt, AlgorithmResult, AlgorithmResultProvenance, InvocationRecord,
    StandardizedGraph,
)
from Agent.deep_agent_tools.runtime_updates import (
    build_pending_invocation, build_terminal_invocation, resolve_runtime_invocation,
)
from tests.support.bound_analysis_input import bind_analysis_input
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor
from tests.support.research_specs import fixed_effect_request, identity_effect_context


CSV = "z,x,y,other\n0,1,3,0\n1,3,8,1\n2,2,7,0\n3,5,14,1\n4,4,13,0\n5,7,20,1\n"
EFFECT_REQUEST = fixed_effect_request(1.0, 2.0)
PARAMETERS = {"target": "y", "treatment": "x", "columns": ["z", "x", "y", "other"], "effect_request": EFFECT_REQUEST}


def _wait_for_process_start(start_gate):
    start_gate.acquire()


def _setup(*, kinds=None, edge_type="directed", csv_data=CSV, executor_pool=None):
    kinds = {"x": "continuous", "y": "continuous", **(kinds or {})}
    effect_context = identity_effect_context("x", "y")
    executor = LocalAlgorithmExecutor(
        csv_data, semantic_kinds=kinds, executor_pool=executor_pool,
        analysis_parameters=PARAMETERS, effect_execution_context=effect_context,
    )
    identity = bind_analysis_input(TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000501",
        session_id="00000000-0000-0000-0000-000000000502", user_id=7,
        attempt_count=1, lease_epoch=3, worker_id="worker-ate", input_identity="source-input",
    ), csv_data, semantic_kinds=kinds, dataset_id=51, analysis_parameters=executor.analysis_parameters)
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
            research_identity=identity.analysis_input.research_identity,
        ),
    )
    source_record = build_terminal_invocation(
        identity=resolve_runtime_invocation(runtime), tool_name="causal_pc", attempt_status="succeeded",
        started_at=datetime.now(timezone.utc), result_ref=source.result_ref,
    )
    return context, registry, source, source_record


def _call(
    context, registry, source, source_record, *, ref=None, treatment="x", outcome="y",
    ledger=None, execution_id="ate-check-response", call_id="ate-call",
):
    tool = next(item for item in build_algorithm_tools(registry, runtime_context=context)
                if item.name == "causal_backdoor_ols").to_langchain_tool()
    runtime = ToolRuntime(
        tool_call_id=call_id, state={"message_execution_id": execution_id,
        "data_profile": context.algorithm_executor.data_profile,
        "algorithm_results": {source.result_ref: source},
        "analysis_parameters": PARAMETERS,
        "action_ledger": {
            source_record.invocation_id: source_record, **(ledger or {}),
        }},
        context=context, config={}, stream_writer=[].append, store=None,
    )
    command = asyncio.run(tool.coroutine(runtime=runtime, graph_result_ref=ref or source.result_ref,
        treatment=treatment, outcome=outcome, effect_request=EFFECT_REQUEST))
    result = next(iter(command.update["algorithm_results"].values()))
    record = next(iter(command.update["action_ledger"].values()))
    message = json.loads(command.update["messages"][0].content)
    assert message["status"] == result.status
    assert message["ate_check"] == (result.ate_check.model_dump(mode="json") if result.ate_check else None)
    assert record.result_ref == result.result_ref
    assert record.attempts[0].safe_error_code == result.diagnostics.safe_error_code
    return result, record


def _diagnose_record(
    identity, *, status="succeeded", subject_ref, treatment="x", outcome="y",
    effect_query=True,
):
    response_identity = "effect_diagnose-response-1"
    provider_call_id = "effect_diagnose-call-1"
    invocation_id = build_invocation_id(
        job_id=identity.job_id, response_identity=response_identity,
        provider_call_id=provider_call_id,
    )
    now = datetime.now(timezone.utc)
    return InvocationRecord(
        invocation_id=invocation_id, response_identity=response_identity,
        response_identity_source="message_execution_id",
        provider_call_id=provider_call_id, tool_name="effect_diagnose",
        final_status=status, subject_ref=subject_ref, job_id=str(identity.job_id),
        effect_query=(
            build_effect_request(
                graph_result_ref=subject_ref, treatment=treatment, outcome=outcome, effect_request=EFFECT_REQUEST,
            )
            if effect_query else None
        ),
        attempt_count=identity.attempt_count, lease_epoch=identity.lease_epoch,
        worker_id=identity.worker_id, input_identity=identity.current_input_identity(),
        source_input_identity=identity.source_input_identity(),
        analysis_dataset_id=identity.analysis_input.dataset_id,
        research_identity=identity.analysis_input.research_identity,
        attempts={0: ActionAttempt(retry_ordinal=0, revision=1, status=status,
            started_at=now, finished_at=now)},
    )


def _validate(
    context, registry, source, source_record, result, record, *, evidence_only=False,
    require_diagnosis=True,
):
    """按真实工具留下的效应请求校验当前查询；不在这里补造缺失的查询证明。"""

    valid = [item for item in (source, result) if item.status == "valid"]
    query = record.effect_query
    if query is None:
        diagnose = None
    else:
        diagnose = _diagnose_record(
            context.trusted_identity, subject_ref=query.graph_result_ref,
            treatment=query.treatment, outcome=query.outcome,
        )
    decision = {
        "outcome": "evidence_only" if evidence_only else "algorithm_supported",
        "primary_graph_result_ref": None if evidence_only else source.result_ref,
        "primary_effect_result_ref": result.result_ref if not evidence_only and result.status == "valid" else None,
        "effect_status": "adopted" if not evidence_only and result.status == "valid" else "incomplete",
        "effect_reason": "本次未采用可用效应估计。",
        "result_assessments": [{"result_ref": item.result_ref,
            "disposition": "discarded" if evidence_only else "primary_graph" if item is source else "primary_effect",
            "rationale": "依据输入图和实际工具结果评估。"} for item in valid],
        "conflict_status": "none", "conflicts": [], "revision_proposals": [],
        "selection_rationale": "保留方法边界。", "confidence": "low", "confidence_basis": ["tool contract"],
    }
    ledger = {item.invocation_id: item for item in (source_record, record)}
    if diagnose is not None and require_diagnosis:
        ledger[diagnose.invocation_id] = diagnose
    return FinalizationGate(registry=registry).validate(
        decision=decision, algorithm_results={item.result_ref: item for item in (source, result)},
        action_ledger=ledger,
        trusted_identity=context.trusted_identity, route_decision="data_processing", analysis_parameters=PARAMETERS,
    )


def test_incomplete_nonlinearity_review_keeps_real_ols_and_candidate_in_saved_report():
    from langgraph.runtime import Runtime
    from Agent.causal_agent.graph import _finalization_gate_node
    from Agent.causal_agent.analysis_context import build_context_commit, build_report_analysis_context
    from Agent.causal_agent.nodes import _report_graph_asset, _report_execution_summary
    from Agent.Report.document import ReportDraft, ReportDocument, build_report_document

    context, registry, source, source_record = _setup(kinds={"z": "continuous"})
    estimate, estimate_record = _call(context, registry, source, source_record)
    assert estimate.status == "valid"
    diagnosis = _diagnose_record(context.trusted_identity, subject_ref=source.result_ref)
    state = {
        "deep_agent_structured_response": {
            "outcome": "algorithm_supported", "primary_graph_result_ref": source.result_ref,
            "primary_effect_result_ref": estimate.result_ref, "effect_status": "adopted",
            "result_assessments": [
                {"result_ref": estimate.result_ref, "disposition": "primary_effect", "rationale": "实际 OLS 估计"},
                {"result_ref": source.result_ref, "disposition": "primary_graph", "rationale": "估计所用源图"},
            ],
            "conflict_status": "none", "selection_rationale": "仅保留初步估计。",
            "confidence": "low", "confidence_basis": ["灵活检查未完成"],
        },
        "deep_agent_algorithm_results": {r.result_ref: r for r in (source, estimate)},
        "deep_agent_action_ledger": {r.invocation_id: r for r in (source_record, estimate_record, diagnosis)},
        "route_decision": "data_processing", "deep_agent_budget_exhausted": True,
        "analysis_parameters": {**PARAMETERS, "nonlinearity": {"ratio": 1.05}},
        "messages": [],
    }
    update = asyncio.run(_finalization_gate_node(
        state, runtime=Runtime(context=context, stream_writer=[].append), config={},
        gate=FinalizationGate(registry=registry),
    ))
    assert update["finalization_status"] == "valid"
    assert update["deep_agent_accepted_decision"].primary_effect_result_ref == estimate.result_ref
    state.update(update)
    facts = build_report_analysis_context(state)
    assert facts["primary_result_ref"] == estimate.result_ref
    graph = _report_graph_asset(state)
    assert graph is not None
    draft = ReportDraft(title="初步分析", blocks=[
        {"id": "scope", "type": "markdown", "content": "基于已有结果。"},
        {"id": "graph", "type": "causal_graph", "asset_key": "graph_main"},
    ])
    document = build_report_document(draft, assets={"graph_main": graph},
        finalization_status="valid", execution_summary=_report_execution_summary(state))
    restored = ReportDocument.model_validate_json(document.model_dump_json())
    assert restored.finalization_status == "valid"
    assert any(getattr(block, "asset_key", None) == "graph_main" for block in restored.blocks)
    assert restored.assets["graph_main"] == graph
    committed = json.loads(json.dumps(build_context_commit(state)))
    regenerated = build_report_analysis_context({"report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": committed["algorithm_summary"]}})
    assert regenerated["analysis_limitations"] == facts["analysis_limitations"]
    assert regenerated["budget_exhausted"] is True


def test_continuous_confounder_runs_real_ols_and_passes_gate():
    context, registry, source, source_record = _setup(kinds={"z": "continuous"})
    result, record = _call(context, registry, source, source_record)
    assert result.status == "valid"
    assert result.ate_check.adjustment_set == ("z",)
    assert result.ate_check.execution_columns == ("x", "y", "z")
    assert result.causal_effect.estimate == pytest.approx(2)
    assert len(context.algorithm_executor.calls) == 1
    flex, flex_record = _call_g_formula(
        context, registry, source, source_record, outcome_model="hgb",
    )
    assert flex.status == "valid"
    # 有两种有效估计时，由决策选择主效应。
    _validate_with_estimations(context, registry, source, source_record,
        [(result, record), (flex, flex_record)],
        primary_ref=flex.result_ref, parameters=PARAMETERS)


@pytest.mark.parametrize("kind", ["category", "ordinal", "binary"])
def test_encoded_confounder_is_rejected_before_estimation_and_satisfies_gate(kind):
    csv_data = CSV
    if kind == "binary":
        frame = pd.read_csv(StringIO(CSV))
        frame["z"] = frame["z"] % 2
        csv_data = frame.to_csv(index=False)
    context, registry, source, source_record = _setup(kinds={"z": kind}, csv_data=csv_data)
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
    flex, flex_record = _call_g_formula(
        context, registry, source, source_record, outcome_model="hgb",
    )
    _validate_with_estimations(context, registry, source, source_record,
        [(result, record), (flex, flex_record)],
        primary_ref=result.result_ref, parameters=PARAMETERS)


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
    _validate(context, registry, source, source_record, result, record)


@pytest.mark.parametrize("evidence_only", [False, True])
def test_another_variable_pair_does_not_satisfy_current_query(evidence_only):
    """另一变量对的诊断与估计记录不能代替当前查询。"""

    context, registry, source, source_record = _setup()
    result, record = _call(context, registry, source, source_record)
    assert result.status == "valid"
    current = record.effect_query
    assert current is not None and (current.treatment, current.outcome) == ("x", "y")
    other_result, other_record = _call(
        context, registry, source, source_record, treatment="z", outcome="y",
        ledger={record.invocation_id: record},
        execution_id="other-pair-response", call_id="other-pair-call",
    )
    assert other_result.status == "invalid_input"
    other = _diagnose_record(
        context.trusted_identity, subject_ref=source.result_ref,
        treatment="z", outcome="y",
    )
    decision = {
        "outcome": "evidence_only" if evidence_only else "algorithm_supported",
        "primary_graph_result_ref": None if evidence_only else source.result_ref,
        "primary_effect_result_ref": None if evidence_only else result.result_ref,
        "effect_status": "incomplete" if evidence_only else "adopted",
        "effect_reason": "当前查询缺少诊断。",
        "result_assessments": [{"result_ref": item.result_ref,
            "disposition": (
                "discarded" if evidence_only
                else "primary_effect" if item is result
                else "primary_graph"
            ),
            "rationale": "依据真实执行状态评估。"}
            for item in (source, result, other_result) if item.status == "valid"],
        "conflict_status": "none", "conflicts": [], "revision_proposals": [],
        "selection_rationale": "保留方法边界。", "confidence": "low",
        "confidence_basis": ["tool contract"],
    }
    with pytest.raises(StructuredResponseError) as failure:
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={
                source.result_ref: source,
                result.result_ref: result,
                other_result.result_ref: other_result,
            },
            action_ledger={
                source_record.invocation_id: source_record,
                record.invocation_id: record,
                other_record.invocation_id: other_record,
                other.invocation_id: other,
            },
            trusted_identity=context.trusted_identity,
            route_decision="data_processing", analysis_parameters=PARAMETERS,
        )
    assert failure.value.rule == "effect_question_without_diagnose"

    # 补上当前查询自己的诊断后，同一组记录可以通过门禁。
    current = _diagnose_record(context.trusted_identity, subject_ref=source.result_ref)
    flex, flex_record = _call_g_formula(
        context, registry, source, source_record, outcome_model="hgb",
    )
    assert flex.status == "valid"
    accepted = FinalizationGate(registry=registry).validate(
        decision={
            "outcome": "algorithm_supported",
            "primary_graph_result_ref": source.result_ref,
            "primary_effect_result_ref": flex.result_ref, "effect_status": "adopted",
            "result_assessments": [
                {"result_ref": item.result_ref,
                 "disposition": "primary_effect" if item is flex else "primary_graph" if item is source else "supporting",
                 "rationale": "依据真实执行状态评估。"}
                for item in (source, result, flex)
            ],
            "conflict_status": "none", "conflicts": [], "revision_proposals": [],
            "selection_rationale": "保留方法边界。", "confidence": "low",
            "confidence_basis": ["tool contract"],
        },
        algorithm_results={
            source.result_ref: source,
            result.result_ref: result,
            other_result.result_ref: other_result,
            flex.result_ref: flex,
        },
        action_ledger={
            source_record.invocation_id: source_record,
            record.invocation_id: record,
            other_record.invocation_id: other_record,
            other.invocation_id: other,
            current.invocation_id: current,
            flex_record.invocation_id: flex_record,
        },
        trusted_identity=context.trusted_identity,
        route_decision="data_processing", analysis_parameters=PARAMETERS,
    )
    assert accepted.primary_result_ref == flex.result_ref


def test_estimate_without_query_proof_cannot_satisfy_gate():
    """旧 checkpoint 缺少效应请求时不能被猜测补齐。"""

    context, registry, source, source_record = _setup()
    result, record = _call(context, registry, source, source_record)
    assert result.status == "valid"
    legacy = record.model_copy(update={"effect_query": None}, deep=True)
    with pytest.raises(StructuredResponseError) as failure:
        _validate(
            context, registry, source, source_record, result, legacy,
        )
    assert failure.value.rule == "effect_question_without_diagnose"


def test_budget_closure_keeps_dataset_identity_without_claiming_a_check():
    context, registry, source, source_record = _setup()
    runtime = ToolRuntime(tool_call_id="budget-ate-call",
        state={"message_execution_id": "budget-response"}, context=context,
        config={}, stream_writer=[].append, store=None)
    pending = build_pending_invocation(identity=resolve_runtime_invocation(runtime), tool_name="causal_backdoor_ols")
    middleware = build_algorithm_dependency_middleware(registry=registry)
    update = asyncio.run(middleware.aafter_agent({"action_ledger": {pending.invocation_id: pending}},
        Runtime(context=context)))
    result = next(iter(update["algorithm_results"].values()))
    record = next(iter(update["action_ledger"].values()))
    assert result.provenance.analysis_dataset_id == record.analysis_dataset_id == 51
    assert result.provenance.source_input_identity == record.source_input_identity == "source-input"
    assert result.ate_check is None


def test_real_postcheck_failure_satisfies_gate_only_with_its_record():
    csv_data = "z,x,y,other\n" + "".join(
        f"{i},{2*i+i%3},{'1e308' if i%2 else '-1e308'},{i%2}\n" for i in range(12)
    )
    context, registry, source, source_record = _setup(csv_data=csv_data)
    result, record = _call(context, registry, source, source_record)
    assert result.status == "execution_failed"
    assert result.ate_check.applicability == "ready"
    _validate(context, registry, source, source_record, result, record)


def test_dispatch_deadline_retains_completed_local_check_for_current_call():
    # 用真实子进程的启动屏障固定超时条件，不依赖 OLS 计算速度或人工延时。
    process_context = multiprocessing.get_context("spawn")
    start_gate = process_context.Semaphore(0)
    pool = BoundedProcessPool(process_workers=1, executor_factory=partial(
        ProcessPoolExecutor, mp_context=process_context,
        initializer=_wait_for_process_start, initargs=(start_gate,),
    ))
    context, registry, source, source_record = _setup(executor_pool=pool)
    tools = [item.to_langchain_tool() for item in build_algorithm_tools(registry, runtime_context=context)]
    middleware = build_algorithm_dependency_middleware(registry=registry, timeout_seconds=1.0)
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=False,
        awrap_tool_call=middleware.awrap_tool_call))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    initial_state = {
        "messages": [AIMessage(content="", tool_calls=[{"name": "causal_backdoor_ols",
            "args": {"graph_result_ref": source.result_ref, "treatment": "x", "outcome": "y", "effect_request": EFFECT_REQUEST},
            "id": "deadline-ate-call", "type": "tool_call"}, {"name": "causal_backdoor_ols",
            "args": {"graph_result_ref": "absent-source:0", "treatment": "x", "outcome": "y", "effect_request": EFFECT_REQUEST},
            "id": "unchecked-ate-call", "type": "tool_call"}])],
        "message_execution_id": "deadline-response", "data_profile": context.algorithm_executor.data_profile,
        "algorithm_results": {source.result_ref: source},
        "analysis_parameters": PARAMETERS,
        "action_ledger": {source_record.invocation_id: source_record},
        "rag_evidence": {}, "web_evidence": {},
    }
    try:
        state = asyncio.run(builder.compile().ainvoke(initial_state, context=context))
    finally:
        start_gate.release()
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
    diagnose = _diagnose_record(context.trusted_identity, subject_ref=source.result_ref)
    FinalizationGate(registry=registry).validate(decision=decision,
        algorithm_results=state["algorithm_results"],
        action_ledger=state["action_ledger"] | {diagnose.invocation_id: diagnose},
        trusted_identity=context.trusted_identity, route_decision="data_processing", analysis_parameters=PARAMETERS)


TRIGGERED = {**PARAMETERS, "nonlinearity": {"ratio": 1.5, "verdict": "nonlinear"}}


def _nonlinear_csv(n=400):
    rng = np.random.default_rng(29)
    z = rng.normal(size=n)
    x = 0.8 * z + rng.normal(size=n)
    y = 3.0 * x**2 + 0.5 * z + 0.3 * rng.normal(size=n)
    return pd.DataFrame({"z": z, "x": x, "y": y, "other": np.arange(n) % 2}).to_csv(index=False)


def _call_g_formula(context, registry, source, source_record, *, contrast=(1.0, 2.0),
                    outcome_model="linear", bootstrap=20):
    tool = next(item for item in build_algorithm_tools(registry, runtime_context=context)
                if item.name == "causal_g_formula").to_langchain_tool()
    runtime = ToolRuntime(
        tool_call_id="g-formula-call",
        state={"message_execution_id": "g-formula-response",
               "data_profile": context.algorithm_executor.data_profile,
               "algorithm_results": {source.result_ref: source},
        "analysis_parameters": PARAMETERS,
               "action_ledger": {source_record.invocation_id: source_record}},
        context=context, config={}, stream_writer=[].append, store=None,
    )
    command = asyncio.run(tool.coroutine(
        runtime=runtime, graph_result_ref=source.result_ref, treatment="x", outcome="y",
        effect_request=fixed_effect_request(*contrast), outcome_model=outcome_model, bootstrap=bootstrap))
    result = next(iter(command.update["algorithm_results"].values()))
    record = next(iter(command.update["action_ledger"].values()))
    return result, record


def _validate_with_estimations(context, registry, source, source_record, entries, *,
                               primary_ref, parameters):
    diagnose = _diagnose_record(context.trusted_identity, subject_ref=source.result_ref)
    results = {source.result_ref: source, **{result.result_ref: result for result, _ in entries}}
    ledger = {
        source_record.invocation_id: source_record,
        **{record.invocation_id: record for _, record in entries},
        diagnose.invocation_id: diagnose,
    }
    decision = {
        "outcome": "algorithm_supported",
        "primary_graph_result_ref": source.result_ref,
        "primary_effect_result_ref": primary_ref if primary_ref != source.result_ref else None,
        "effect_status": "adopted" if primary_ref != source.result_ref else "not_adopted",
        "effect_reason": "依据已有结果保留方法限制。",
        "result_assessments": [
            {"result_ref": ref,
             "disposition": "primary_graph" if item is source else "primary_effect" if ref == primary_ref else (
                 "supporting" if item.status == "valid" else "discarded"),
             "rationale": "依据真实执行状态评估。"}
            for ref, item in results.items()
        ],
        "conflict_status": "none", "conflicts": [], "revision_proposals": [],
        "selection_rationale": "保留方法边界。", "confidence": "low",
        "confidence_basis": ["tool contract"],
    }
    return FinalizationGate(registry=registry).validate(
        decision=decision, algorithm_results=results, action_ledger=ledger,
        trusted_identity=context.trusted_identity, route_decision="data_processing",
        analysis_parameters=parameters,
    )


def test_confirmed_query_rejects_g_formula_with_different_intervention():
    context, registry, source, source_record = _setup()
    result, record = _call_g_formula(
        context, registry, source, source_record, contrast=(1.0, 3.0), outcome_model="hgb",
    )
    assert result.status == "invalid_input"
    assert record.final_status == "failed"
    assert context.algorithm_executor.calls == []


@pytest.mark.parametrize("with_flexible_estimator", [False, True])
def test_gate_allows_linear_primary_with_nonlinear_signal(with_flexible_estimator):
    context, registry, source, source_record = _setup(csv_data=_nonlinear_csv())
    ols, ols_record = _call(context, registry, source, source_record)
    entries = [(ols, ols_record)]
    if with_flexible_estimator:
        flex, flex_record = _call_g_formula(
            context, registry, source, source_record, outcome_model="hgb", bootstrap=20,
        )
        assert flex.status == "valid"
        entries.append((flex, flex_record))
    assert ols.status == "valid"
    accepted = _validate_with_estimations(
        context, registry, source, source_record, entries,
        primary_ref=ols.result_ref, parameters=TRIGGERED,
    )
    assert accepted.primary_effect_result_ref == ols.result_ref


def test_gate_allows_undetermined_flex_failure_with_graph_primary():
    context, registry, source, source_record = _setup(edge_type="undirected")
    ols, ols_record = _call(context, registry, source, source_record)
    flex, flex_record = _call_g_formula(
        context, registry, source, source_record, outcome_model="hgb",
    )
    assert ols.status == "not_applicable" and flex.status == "not_applicable"
    rejected = _diagnose_record(
        context.trusted_identity, status="not_applicable",
        subject_ref=source.result_ref,
    )
    decision = {
        "outcome": "algorithm_supported", "primary_graph_result_ref": source.result_ref,
        "effect_status": "not_identifiable", "effect_reason": "源图含未定向边。",
        "result_assessments": [
            {"result_ref": result.result_ref,
             "disposition": "primary_graph" if result is source else "discarded",
             "rationale": "依据真实执行状态评估。"}
            for result in (source, ols, flex)
        ],
        "conflict_status": "none", "conflicts": [], "revision_proposals": [],
        "selection_rationale": "保留方法边界。", "confidence": "low",
        "confidence_basis": ["tool contract"],
    }
    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={
            source.result_ref: source, ols.result_ref: ols, flex.result_ref: flex,
        },
        action_ledger={
            source_record.invocation_id: source_record,
            ols_record.invocation_id: ols_record,
            flex_record.invocation_id: flex_record,
            rejected.invocation_id: rejected,
        },
        trusted_identity=context.trusted_identity,
        route_decision="data_processing", analysis_parameters=TRIGGERED,
    )
    assert accepted.primary_result_ref == source.result_ref


def test_report_facts_carry_functional_form_and_survive_regeneration():
    context, registry, source, source_record = _setup(csv_data=_nonlinear_csv())
    ols, ols_record = _call(context, registry, source, source_record)
    flex, flex_record = _call_g_formula(context, registry, source, source_record,
        outcome_model="hgb", bootstrap=20)
    decision = _validate_with_estimations(
        context, registry, source, source_record,
        [(ols, ols_record), (flex, flex_record)],
        primary_ref=flex.result_ref, parameters=TRIGGERED,
    )
    state = {
        "deep_agent_algorithm_results": {
            source.result_ref: source, ols.result_ref: ols, flex.result_ref: flex,
        },
        "deep_agent_action_ledger": {
            ols_record.invocation_id: ols_record,
            flex_record.invocation_id: flex_record,
        },
        "analysis_parameters": TRIGGERED,
        "deep_agent_accepted_decision": decision,
        "finalization_status": "valid",
    }
    facts = build_report_analysis_context(state)
    assert facts["functional_form"]["verdict"] == "agent_assessed"
    comparison, = facts["effect_comparisons"]
    assert comparison["comparable"]
    assert comparison["difference_se"] > 0
    commit = json.loads(json.dumps(build_context_commit(state)))
    restored = build_report_analysis_context({
        "report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": commit["algorithm_summary"]},
    })
    assert restored == facts
