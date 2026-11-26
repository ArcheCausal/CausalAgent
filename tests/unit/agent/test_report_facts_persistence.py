"""报告安全投影与新分析快照的确定性检查，不访问模型或数据库。"""

import json

import pytest

from Agent.causal_agent.analysis_context import build_context_commit, build_report_analysis_context, report_primary_result
from Agent.causal_agent.nodes import _report_graph_asset, _report_execution_summary
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC, CDFM_SPEC, CAUSAL_BACKDOOR_OLS_SPEC, DIRECT_LINGAM_SPEC
from Agent.deep_agent_tools.models import (
    AlgorithmResult, AlgorithmResultProvenance, CausalEffect, DataViewSummary, Diagnostics,
    FinalAnalysisDecision, GraphEdge, StandardizedGraph,
)


def result(spec, invocation, *, rows=100, operations=()):
    return AlgorithmResult(
        result_ref=f"{invocation}:0", invocation_id=invocation, provider_call_id=invocation,
        capability_id=spec.capability_id, capability_version=spec.version, status="valid",
        method=spec.method_definition(),
        standardized_graph=StandardizedGraph(graph_semantics=spec.graph_semantics, nodes=["A", "B"],
            edges=[GraphEdge(source="A", target="B")]),
        data_view=DataViewSummary(row_count=rows, column_count=2, columns=("A", "B"), operations=operations),
        diagnostics=Diagnostics(metrics={"runtime_sec": 1.25, "raw_array": "must-be-excluded"}),
        provenance=AlgorithmResultProvenance(job_id="job", attempt_count=1, lease_epoch=1,
            input_identity="private-input", spec_digest=spec.spec_digest),
    )


def state_with_selection():
    first = result(PC_SPEC, "pc")
    second = result(CDFM_SPEC, "cdfm", rows=90, operations=("drop_rows",))
    decision = FinalAnalysisDecision(outcome="algorithm_supported", primary_result_ref=first.result_ref,
        result_assessments=[{"result_ref": first.result_ref, "disposition": "primary", "rationale": "本次检验与问题相符"},
                            {"result_ref": second.result_ref, "disposition": "supporting", "rationale": "仅作结构比较"}],
        selection_rationale="以PC为主，CDFM作为辅助；没有合并图。", confidence="low",
        confidence_basis=["尚未验证方向"], conflict_status="unresolved",
        conflicts=[{"conflict_id": "view-difference", "result_refs": [first.result_ref, second.result_ref], "summary": "输入行数不同"}])
    return {"analysis_context": {"analysis_context_id": "ctx"},
        "analysis_parameters": {"columns": ["A", "B"], "n_rows": 100, "n_cols": 2,
            "column_profiles": {"A": {"storage_type": "int64", "value_counts": {"CSV-must-be-excluded": 1},
                "measurement_semantics": {"kind": "unknown", "source": "csv_inference", "status": "uncertain"}}}},
        "deep_agent_algorithm_results": {first.result_ref: first, second.result_ref: second},
        "deep_agent_decision": decision, "finalization_status": "valid",
        "deep_agent_action_ledger": {"must-be-excluded": "ledger"}, "dataset_csv": "CSV-must-be-excluded",
        "runtime": "must-be-excluded"}


def test_first_generation_and_json_restoration_keep_same_selection_and_methods():
    state = state_with_selection()
    facts = build_report_analysis_context(state)
    commit = json.loads(json.dumps(build_context_commit(state)))
    regenerated_state = {"report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": commit["algorithm_summary"]}}
    restored = build_report_analysis_context(regenerated_state)
    assert restored == facts
    assert restored["primary_result_ref"] == "pc:0"
    assert restored["decision"]["confidence_basis"] == ["尚未验证方向"]
    assert restored["decision"]["conflicts"]
    assert restored["data_view_comparison"]["data_views_differ"] is True
    assert _report_graph_asset(regenerated_state).metadata["algorithm"] == PC_SPEC.capability_id
    assert _report_execution_summary(regenerated_state)["data_views_differ"] is True
    payload = json.dumps(facts)
    assert "must-be-excluded" not in payload and "private-input" not in payload


def test_missing_selection_never_chooses_the_only_valid_result_or_an_old_graph():
    state = state_with_selection()
    state.pop("deep_agent_decision")
    state["deep_agent_algorithm_results"].pop("cdfm:0")
    state["analysis_context"]["latest_algorithm_summary"] = {"graph": {"nodes": ["old"], "edges": []}}
    facts = build_report_analysis_context(state)
    assert report_primary_result(facts) is None
    assert _report_graph_asset(state) is None
    saved = build_context_commit(state)["algorithm_summary"]
    assert saved["graph"] is None and saved["status"] == "no_primary"


def test_same_columns_but_different_processing_or_rows_are_different_views():
    state = state_with_selection()
    second = state["deep_agent_algorithm_results"]["cdfm:0"]
    second.data_view = second.data_view.model_copy(update={"row_count": 100})
    assert build_report_analysis_context(state)["data_view_comparison"]["status"] == "different"
    second.data_view = second.data_view.model_copy(update={"operations": ()})
    assert build_report_analysis_context(state)["data_view_comparison"]["status"] == "same"
    second.data_view = None
    assert build_report_analysis_context(state)["data_view_comparison"]["status"] == "insufficient"


def test_report_edge_counts_preserve_mixed_edge_identity_and_json_round_trip():
    state = state_with_selection()
    state["deep_agent_algorithm_results"]["pc:0"].standardized_graph.edges = [
        GraphEdge(source="A", target="B", edge_type="undirected"),
    ]
    state["deep_agent_algorithm_results"]["cdfm:0"].standardized_graph.edges = [
        GraphEdge(source="A", target="B", edge_type="bidirected"),
    ]
    facts = build_report_analysis_context(state)
    assert facts["results"][0]["edge_counts"] == {
        "total": 1, "directed": 0, "undirected": 1, "bidirected": 0, "partially_directed": 0,
    }
    assert facts["results"][1]["edge_counts"] == {
        "total": 1, "directed": 0, "undirected": 0, "bidirected": 1, "partially_directed": 0,
    }
    commit = json.loads(json.dumps(build_context_commit(state)))
    restored = build_report_analysis_context({"report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": commit["algorithm_summary"]}})
    assert restored == facts


@pytest.mark.parametrize("edges, expected", [
    ([GraphEdge(source="A", target="B")], "same"),
    ([GraphEdge(source="B", target="A")], "opposite_direction"),
    ([GraphEdge(source="A", target="B", edge_type="bidirected")], "edge_type_difference"),
    ([], "left_only"),
])
def test_graph_comparison_distinguishes_absent_reversed_and_mixed_edges(edges, expected):
    state = state_with_selection()
    state["deep_agent_algorithm_results"]["cdfm:0"].standardized_graph.edges = edges
    facts = build_report_analysis_context(state)
    pair = facts["graph_comparisons"][0]["pairs"][0]
    comparison = facts["graph_comparisons"][0]
    assert comparison["left_algorithm"] == PC_SPEC.capability_id
    assert comparison["right_algorithm"] == CDFM_SPEC.capability_id
    assert comparison["relation_counts"][expected] == 1
    assert sum(comparison["relation_counts"].values()) == 1
    assert pair["variables"] == ["A", "B"] and pair["relation"] == expected
    assert pair["left_edges"] == [{"source": "A", "target": "B", "edge_type": "directed"}]
    assert bool(pair["right_edges"]) == bool(edges)


def test_estimation_primary_uses_source_graph_semantics_for_report():
    source = result(DIRECT_LINGAM_SPEC, "lingam")
    spec = CAUSAL_BACKDOOR_OLS_SPEC
    estimate = AlgorithmResult(
        result_ref="ate:0", invocation_id="ate", provider_call_id="ate",
        capability_id=spec.capability_id, capability_version=spec.version, status="valid",
        method=spec.method_definition(),
        standardized_graph=StandardizedGraph(graph_semantics="dag_target_to_source", nodes=["A", "B"],
            edges=[GraphEdge(source="A", target="B")]),
        causal_effect=CausalEffect(estimand="ATE", graph_result_ref=source.result_ref,
            treatment="A", outcome="B", estimate=1.0, estimator="backdoor_ols", n_rows=100),
        diagnostics=Diagnostics(),
        provenance=AlgorithmResultProvenance(job_id="job", attempt_count=1, lease_epoch=1,
            input_identity="private-input", spec_digest=spec.spec_digest),
    )
    decision = FinalAnalysisDecision(outcome="algorithm_supported", primary_result_ref=estimate.result_ref,
        result_assessments=[{"result_ref": estimate.result_ref, "disposition": "primary", "rationale": "效应估计"},
                            {"result_ref": source.result_ref, "disposition": "supporting", "rationale": "输入图"}],
        selection_rationale="由效应估计回答用户问题。", confidence="low",
        confidence_basis=["方向未定"], conflict_status="none")
    state = {"analysis_parameters": {"columns": ["A", "B"], "n_rows": 100, "n_cols": 2},
        "deep_agent_algorithm_results": {source.result_ref: source, estimate.result_ref: estimate},
        "deep_agent_decision": decision, "finalization_status": "valid"}

    facts = build_report_analysis_context(state)

    assert facts["primary_result_ref"] == estimate.result_ref
    assert facts["no_primary_reason"] is None
    primary = report_primary_result(facts)
    assert primary["result_ref"] == estimate.result_ref
    assert primary["causal_effect"] == {
        "estimand": "ATE", "graph_result_ref": source.result_ref,
        "treatment": "A", "outcome": "B", "adjustment_set": [],
        "estimate": 1.0, "standard_error": None, "ci95": None,
        "estimator": "backdoor_ols", "n_rows": 100,
        "contrast": None, "outcome_model": None, "bootstrap_n": None,
    }
    asset = _report_graph_asset(state)
    assert asset is not None
    assert asset.metadata["graph_semantics"] == "dag_target_to_source"

    commit = json.loads(json.dumps(build_context_commit(state)))
    restored = build_report_analysis_context({
        "report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": commit["algorithm_summary"]},
    })
    assert restored == facts
    assert report_primary_result(restored)["causal_effect"]["estimate"] == 1.0


def test_graph_comparison_keeps_single_method_edges_distinct_from_opposite_directions():
    state = state_with_selection()
    state["deep_agent_algorithm_results"]["pc:0"].standardized_graph.edges = []
    pair = build_report_analysis_context(state)["graph_comparisons"][0]["pairs"][0]
    assert pair["relation"] == "right_only" and not pair["left_edges"]
