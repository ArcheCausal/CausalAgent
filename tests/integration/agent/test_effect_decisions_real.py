"""真实数值计算与领域契约回归；不替换模型、MCP 或数据库返回。"""
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from Agent.CausalAgentMCP.inference.ate import estimate_ate
from Agent.CausalAgentMCP.inference.g_formula import estimate_g_formula
from Agent.causal_agent.analysis_context import build_report_analysis_context
from Agent.data_processing.parameters import historical_parameter_identity, parameter_identity
from Agent.deep_agent.context import AnalysisInputRef, TrustedJobIdentity
from Agent.deep_agent.finalization import FinalizationGate, StructuredResponseError
from Agent.deep_agent_tools.algorithm_specs import get_algorithm_spec
from Agent.deep_agent_tools.functional_form import build_effect_query, build_effect_request, compare_effect_results, estimation_pair
from Agent.deep_agent_tools.identity import build_invocation_id
from Agent.deep_agent_tools.models import (ActionAttempt, AlgorithmResult, AlgorithmResultProvenance,
    CausalEffect, FinalAnalysisDecision, InvocationRecord, StandardizedGraph)


@pytest.fixture(scope="module")
def computation():
    rng = np.random.default_rng(91)
    z = rng.uniform(-1, 1, 300)
    t = 7 + z + rng.normal(0, 0.7, 300)
    data = pd.DataFrame({"T": t, "Y": -2 * t + z + rng.normal(0, .2, 300), "Z": z})
    graph = StandardizedGraph(graph_semantics="dag_target_to_source", nodes=["T", "Y", "Z"],
        edges=[{"source": "Z", "target": "T", "edge_type": "directed"},
               {"source": "T", "target": "Y", "edge_type": "directed"},
               {"source": "Z", "target": "Y", "edge_type": "directed"}])
    request = {"effect": "average_total_effect", "intervention": {"type": "fixed_contrast", "from": 6., "to": 8., "unit": "L/min"}}
    execution = {"treatment_column": "T", "outcome_column": "Y", "treatment_unit": "L/min",
                 "treatment_transform": {"type": "identity"}, "outcome_transform": {"type": "identity"}}
    kwargs = dict(graph=graph, data=data, treatment="T", outcome="Y", effect_request=request,
                  execution_context=execution, semantic_kinds={"T": "continuous", "Y": "continuous", "Z": "continuous"})
    return graph, request, estimate_ate(**kwargs), estimate_g_formula(**kwargs, outcome_model="linear", bootstrap=12)


def execution_state(computation):
    """为已实际计算的结果建立本地执行记录，专门测试纯 Gate 契约。"""
    graph, request, ols, flex = computation
    params = {"target": "Y", "treatment": "T", "effect_request": request}
    bound = AnalysisInputRef()
    bound.bind(dataset_id=1, object_id=1, content_hash="a" * 64, filename="calculation.csv", research_identity=parameter_identity(params))
    identity = TrustedJobIdentity(job_id=str(uuid4()), session_id=str(uuid4()), user_id=1, attempt_count=1,
        lease_epoch=1, worker_id="local-computation", input_identity="b" * 64, analysis_input=bound)
    results, ledger = {}, {}
    refs = []
    for index, (capability, numeric) in enumerate((("causal.direct_lingam", None), ("causal.backdoor_ols", ols), ("causal.g_formula", flex))):
        spec = get_algorithm_spec(capability)
        call_id = f"computation-{index}"
        invocation = build_invocation_id(job_id=identity.job_id, response_identity="local-calculation", provider_call_id=call_id)
        ref = invocation + ":0"
        refs.append(ref)
        effect = CausalEffect(graph_result_ref=refs[0], **{key: value for key, value in numeric.items() if key != "diagnostics_metrics"}) if numeric else None
        result = AlgorithmResult(result_ref=ref, invocation_id=invocation, provider_call_id=call_id,
            capability_id=capability, capability_version=spec.version, status="valid", standardized_graph=graph,
            method=spec.method_definition(), causal_effect=effect,
            provenance=AlgorithmResultProvenance(job_id=identity.job_id, attempt_count=1, lease_epoch=1,
                input_identity="a"*64, source_input_identity="b"*64, analysis_dataset_id=1,
                research_identity=bound.research_identity, spec_digest=spec.spec_digest))
        if numeric:
            result.diagnostics.metrics = numeric.get("diagnostics_metrics", {})
        results[ref] = result
        query = build_effect_query(capability_id=capability, parameters={"graph_result_ref": refs[0],
            "treatment": "T", "outcome": "Y", "effect_request": request,
            **({"outcome_model": "linear", "bootstrap": 12} if capability.endswith("g_formula") else {})})
        ledger[invocation] = InvocationRecord(invocation_id=invocation, response_identity="local-calculation",
            response_identity_source="provider_response_id", provider_response_id="local-calculation", provider_call_id=call_id,
            tool_name=spec.tool_name, final_status="succeeded", result_ref=ref, effect_query=query,
            job_id=identity.job_id, attempt_count=1, lease_epoch=1, worker_id=identity.worker_id,
            input_identity="a"*64, source_input_identity="b"*64, analysis_dataset_id=1, research_identity=bound.research_identity,
            attempts={0: ActionAttempt(retry_ordinal=0, revision=2, status="succeeded")})
    diagnose_id = build_invocation_id(job_id=identity.job_id, response_identity="local-calculation", provider_call_id="diagnose")
    diagnose = next(iter(ledger.values())).model_copy(deep=True, update={"invocation_id": diagnose_id, "provider_call_id": "diagnose",
        "tool_name": "effect_diagnose", "result_ref": None, "subject_ref": refs[0],
        "effect_query": build_effect_request(graph_result_ref=refs[0], treatment="T", outcome="Y", effect_request=request)})
    ledger[diagnose_id] = diagnose
    return params, identity, results, ledger, refs


def decision(refs, selected=None):
    return FinalAnalysisDecision(outcome="algorithm_supported", primary_graph_result_ref=refs[0],
        primary_effect_result_ref=selected, effect_status="adopted" if selected else "not_adopted", effect_reason="已说明方法假设与限制。",
        result_assessments=[{"result_ref": ref, "disposition": "primary_graph" if ref == refs[0] else "primary_effect" if ref == selected else "supporting",
                             "rationale": "依据本次计算与适用条件选择。"} for ref in refs],
        conflict_status="none", selection_rationale="根据诊断选择。", confidence="low")


@pytest.mark.parametrize("selected_index", [None, 1, 2])
def test_gate_accepts_separate_graph_and_either_estimator(computation, selected_index):
    params, identity, results, ledger, refs = execution_state(computation)
    selected = refs[selected_index] if selected_index is not None else None
    accepted = FinalizationGate().validate(decision=decision(refs, selected), algorithm_results=results,
        action_ledger=ledger, trusted_identity=identity, analysis_parameters=params)
    assert accepted.primary_graph_result_ref == refs[0]
    assert accepted.primary_effect_result_ref == selected
    assert accepted.primary_result_ref == (selected or refs[0])


@pytest.mark.parametrize("status", ["not_identifiable", "not_applicable"])
@pytest.mark.parametrize("mismatch", [None, "graph", "request", "roles", "missing_query"])
def test_gate_effect_absence_requires_evidence_for_selected_graph(computation, status, mismatch):
    """显式构造 Gate 契约输入，不把诊断状态当成外部工具执行证据。"""
    params, identity, results, ledger, refs = execution_state(computation)
    proposed = decision(refs).model_copy(update={"effect_status": status})
    if status == "not_identifiable":
        record = next(item for item in ledger.values() if item.tool_name == "effect_diagnose")
    else:
        estimate = results[refs[1]]
        rejected = estimate.model_dump()
        rejected.update(status="not_applicable", causal_effect=None, standardized_graph=None)
        rejected["diagnostics"]["safe_error_code"] = "EFFECT_OUTSIDE_SUPPORT"
        results[refs[1]] = AlgorithmResult.model_validate(rejected)
        record = ledger[estimate.invocation_id]
    record.final_status = "not_applicable"
    record.attempts[0].status = "not_applicable"
    if mismatch == "graph":
        record.effect_query.graph_result_ref = "another-graph"
        if record.subject_ref is not None:
            record.subject_ref = "another-graph"
    elif mismatch == "request":
        record.effect_query.effect_request = {
            **params["effect_request"],
            "intervention": {"type": "fixed_contrast", "from": 5, "to": 7, "unit": "L/min"},
        }
    elif mismatch == "roles":
        record.effect_query.outcome = "Z"
    elif mismatch == "missing_query":
        record.effect_query = None
    if mismatch:
        with pytest.raises(StructuredResponseError, match="selected graph and request"):
            FinalizationGate().validate(decision=proposed, algorithm_results=results,
                action_ledger=ledger, trusted_identity=identity, analysis_parameters=params)
    else:
        accepted = FinalizationGate().validate(decision=proposed, algorithm_results=results,
            action_ledger=ledger, trusted_identity=identity, analysis_parameters=params)
        assert accepted.primary_graph_result_ref == refs[0]
        assert accepted.primary_effect_result_ref is None
        assert accepted.effect_status == status


@pytest.mark.parametrize("selected_index", [None, 1, 2])
@pytest.mark.parametrize("gap", ["complete", "budget", "limitation", "both"])
def test_report_ols_limitation_uses_selected_effect(computation, selected_index, gap):
    # 此导入需要正式应用依赖；在 Docker 测试环境执行，不替换节点或报告事实。
    from Agent.causal_agent.nodes import _report_execution_summary

    params, _, results, ledger, refs = execution_state(computation)
    selected = refs[selected_index] if selected_index is not None else None
    state = {"analysis_parameters": params, "deep_agent_algorithm_results": results,
        "deep_agent_action_ledger": ledger, "deep_agent_accepted_decision": decision(refs, selected),
        "finalization_status": "valid", "deep_agent_budget_exhausted": gap in {"budget", "both"},
        "analysis_limitations": [{"code": "effect_question_without_diagnose", "message": "诊断未完成。"}]
        if gap in {"limitation", "both"} else []}
    summary = _report_execution_summary(state)
    assert summary["preliminary_ols"] == (selected_index == 1 and gap != "complete")


def test_comparisons_refuse_different_graph_sample_or_request(computation):
    _, _, results, _, refs = execution_state(computation)
    estimates = [results[refs[1]], results[refs[2]]]
    assert compare_effect_results(estimates)[0]["comparable"]
    for field, value in (("graph_result_ref", "other-graph"), ("sample_identity", "other-sample"), ("adjustment_rule", "other-rule")):
        changed = estimates[1].model_copy(deep=True)
        setattr(changed.causal_effect, field, value)
        assert not compare_effect_results([estimates[0], changed])[0]["comparable"]


def test_structure_only_and_report_acceptance_boundary(computation):
    params, identity, results, ledger, refs = execution_state(computation)
    accepted = decision(refs, refs[1])
    state = {"analysis_parameters": params, "deep_agent_algorithm_results": results,
             "deep_agent_action_ledger": ledger, "deep_agent_structured_response": accepted,
             "deep_agent_decision": accepted, "finalization_status": "valid"}
    assert build_report_analysis_context(state)["primary_effect_result_ref"] is None
    state["deep_agent_accepted_decision"] = accepted
    facts = build_report_analysis_context(state)
    assert facts["primary_graph_result_ref"] == refs[0]
    assert facts["primary_effect_result_ref"] == refs[1]
    assert estimation_pair({**params, "effect_request": None}) is None


def test_gate_rejects_source_and_research_identity_mismatch(computation):
    params, identity, results, ledger, refs = execution_state(computation)
    results[refs[1]].provenance.research_identity = "changed"
    with pytest.raises(StructuredResponseError, match="research identity"):
        FinalizationGate().validate(decision=decision(refs, refs[1]), algorithm_results=results,
            action_ledger=ledger, trusted_identity=identity, analysis_parameters=params)


def test_diagnosis_cannot_substitute_another_definition(computation):
    params, identity, results, ledger, refs = execution_state(computation)
    record = next(item for item in ledger.values() if item.tool_name == "effect_diagnose")
    record.effect_query.effect_request = {
        **params["effect_request"], "intervention": {"type": "fixed_contrast", "from": 5, "to": 7, "unit": "L/min"}}
    with pytest.raises(StructuredResponseError, match="successful diagnosis"):
        FinalizationGate().validate(decision=decision(refs, refs[1]), algorithm_results=results,
            action_ledger=ledger, trusted_identity=identity, analysis_parameters=params)


def test_history_does_not_infer_unrequested_effect():
    for state in ({}, {"analysis_parameters": {"target": "Y", "treatment": "T"}},
                  {"report_revision_mode": "full_regeneration_from_context"}):
        facts = build_report_analysis_context(state)
        assert facts["effect_request_status"] == "not_recorded"
        assert facts["effect_status"] == "not_recorded"
        assert facts["primary_graph_result_ref"] is None
        assert facts["primary_effect_result_ref"] is None
    assert historical_parameter_identity({}, job_id=str(uuid4())) is None
    assert historical_parameter_identity({"analysis_parameter_identity": {"state_version": 3}}, job_id=str(uuid4())) is None


def test_structure_only_gate_retains_confirmed_graph(computation):
    params, identity, results, ledger, refs = execution_state(computation)
    params["effect_request"] = None
    identity.analysis_input.research_identity = parameter_identity(params)
    graph = results[refs[0]]
    graph.provenance.research_identity = identity.analysis_input.research_identity
    record = ledger[graph.invocation_id]
    record.research_identity = identity.analysis_input.research_identity
    accepted = FinalizationGate().validate(decision=decision(refs[:1]),
        algorithm_results={graph.result_ref: graph}, action_ledger={record.invocation_id: record},
        trusted_identity=identity, analysis_parameters=params)
    assert accepted.primary_graph_result_ref == graph.result_ref
    assert accepted.primary_effect_result_ref is None
    assert accepted.effect_status == "not_requested"


def test_query_keeps_effective_defaults_separate_from_comparison(computation):
    _, request, *_ = computation
    query = build_effect_query(capability_id="causal.g_formula", parameters={
        "graph_result_ref": "graph", "treatment": "T", "outcome": "Y", "effect_request": request})
    assert query.estimator_config == {"outcome_model": "linear", "bootstrap": 200}
    _, _, results, _, refs = execution_state(computation)
    comparison = compare_effect_results([results[refs[1]], results[refs[2]]])[0]
    assert comparison["comparable"]
    assert comparison["paired_difference_direction"] == "linear_minus_flexible"
    assert comparison["paired_effect_difference"] == pytest.approx(
        results[refs[1]].causal_effect.estimate - results[refs[2]].causal_effect.estimate)
    facts = build_report_analysis_context({"analysis_parameters": {"target": "Y", "treatment": "T", "effect_request": request},
        "deep_agent_algorithm_results": results, "deep_agent_accepted_decision": decision(refs, refs[1]),
        "finalization_status": "valid"})
    expected = facts["effect_comparisons"][0].pop("paired_effect_difference")
    restored = build_report_analysis_context({"report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": {"analysis_facts": facts}}})
    assert restored["effect_comparisons"][0]["paired_effect_difference"] == expected


def test_empty_primary_report_does_not_invent_a_result(computation):
    _, request, *_ = computation
    no_result = FinalAnalysisDecision(outcome="no_valid_algorithm",
        effect_status="incomplete", effect_reason="本次没有可用图，未执行效应估计。",
        conflict_status="none", selection_rationale="没有可采用结果，保留真实终态。", confidence="low")
    assert no_result.primary_graph_result_ref is None
    assert no_result.primary_effect_result_ref is None
    facts = build_report_analysis_context({"analysis_parameters": {"target": "Y", "treatment": "T", "effect_request": request},
        "deep_agent_accepted_decision": no_result, "finalization_status": "valid"})
    assert facts["primary_result_ref"] is None
    assert facts["effect_status"] == "incomplete"
