"""检查概率的实际转换函数，不执行推理、MCP、模型或数据库。"""

import json

import numpy as np
import pytest
from pydantic import ValidationError

from Agent.causal.cdfm_runner import adjacency_to_edges
from Agent.causal_agent.analysis_context import (
    build_context_commit, build_report_analysis_context, normalize_algorithm_summary,
)
from Agent.causal_agent.nodes import _report_graph_asset
from Agent.Report.assets import build_causal_graph_model
from Agent.Report.document import CausalGraphModel
from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC
from Agent.deep_agent_tools.models import (
    AlgorithmResult, AlgorithmResultProvenance, Diagnostics, EdgePrediction, FinalAnalysisDecision,
)
from Agent.deep_agent_tools.normalization import RunnerContractError, standardize_runner_graph


@pytest.mark.parametrize("adjacency,source,target,kind,forward,reverse", [
    ([[0, 1], [0, 0]], "Z", "A", "directed", 0.8123456789012345, 0.23),
    ([[0, 0], [1, 0]], "A", "Z", "directed", 0.23, 0.8123456789012345),
    ([[0, 1], [1, 0]], "Z", "A", "bidirected", 0.8123456789012345, 0.23),
])
def test_matrix_positions_survive_normalization_facts_and_asset_json(
    adjacency, source, target, kind, forward, reverse,
):
    # 故意使用非字母顺序、非对称概率，以捕获按名称排序和正反颠倒。
    probabilities = np.array([[0.0, 0.8123456789012345], [0.23, 0.0]])
    edges = adjacency_to_edges(np.array(adjacency), ["Z", "A"], probabilities=probabilities)
    graph = standardize_runner_graph(
        {"success": True, "data": {"nodes": ["Z", "A"], "edges": edges}},
        capability_id=CDFM_SPEC.capability_id,
    )
    edge = graph.edges[0]
    assert (edge.source, edge.target, edge.edge_type, edge.weight) == (source, target, kind, None)
    expected = {"forward_probability": forward, "reverse_probability": reverse}
    assert edge.prediction.model_dump() == expected
    result = AlgorithmResult(
        result_ref="invocation:0", invocation_id="invocation", provider_call_id="call",
        capability_id=CDFM_SPEC.capability_id, capability_version=CDFM_SPEC.version,
        status="valid", method=CDFM_SPEC.method_definition(), standardized_graph=graph,
        diagnostics=Diagnostics(metrics={"threshold": 0.21234567890123456}),
        provenance=AlgorithmResultProvenance(job_id="job", attempt_count=1, lease_epoch=1,
            input_identity="input", spec_digest=CDFM_SPEC.spec_digest),
    )
    receipt = json.loads(result.model_dump_json())
    assert receipt["standardized_graph"]["edges"][0]["prediction"] == expected
    summary = normalize_algorithm_summary(result)
    restored = json.loads(json.dumps(summary))
    asset = build_causal_graph_model(
        dict(restored["graph"], diagnostics=restored["diagnostics"]),
        graph_id="main", algorithm=restored["algorithm"],
    )
    saved = CausalGraphModel.model_validate_json(asset.model_dump_json())
    assert saved.edges[0].prediction.model_dump() == expected
    assert saved.metadata["diagnostics"]["metrics"]["threshold"] == 0.21234567890123456
    decision = FinalAnalysisDecision(
        outcome="algorithm_supported", primary_graph_result_ref=result.result_ref,
        effect_status="not_requested", effect_reason="本次仅检查候选结构。",
        result_assessments=[{"result_ref": result.result_ref, "disposition": "primary_graph",
                             "rationale": "契约输入指定主图。"}],
        conflict_status="none", selection_rationale="契约输入指定主图。", confidence="low",
    )
    state = {"analysis_parameters": {"effect_request": None},
        "deep_agent_algorithm_results": {result.result_ref: result},
        "deep_agent_accepted_decision": decision, "finalization_status": "valid"}
    report_facts = build_report_analysis_context(state)
    assert report_facts["decision"]["confidence"] == "low"
    commit = json.loads(json.dumps(build_context_commit(state)))
    regeneration = {"report_revision_mode": "full_regeneration_from_context",
        "analysis_context": {"latest_algorithm_summary": commit["algorithm_summary"]}}
    assert build_report_analysis_context(regeneration) == report_facts
    first_asset, restored_asset = _report_graph_asset(state), _report_graph_asset(regeneration)
    assert first_asset.edges == restored_asset.edges == saved.edges
    assert restored_asset.metadata["diagnostics"] == saved.metadata["diagnostics"]
    public = json.dumps([receipt, restored, saved.model_dump(mode="json")])
    assert all(f'"{name}"' not in public for name in ("logits", "probabilities", "raw_results"))


def test_from_arrow_reverses_probability_endpoints_in_both_converters():
    graph = {"nodes": ["Z", "A"], "edges": [{"from": "Z", "to": "A", "arrows": "from",
        "prediction": {"forward_probability": 0.2, "reverse_probability": 0.8}}]}
    standardized = standardize_runner_graph({"success": True, "data": graph}, capability_id=CDFM_SPEC.capability_id)
    asset = build_causal_graph_model(graph, graph_id="main")
    assert standardized.edges[0].source == "A"
    assert standardized.edges[0].prediction == asset.edges[0].prediction
    assert asset.edges[0].prediction.forward_probability == 0.8


def test_absent_predictions_remain_absent_without_matrix_backfill():
    edges = adjacency_to_edges(np.array([[0, 1], [0, 0]]), ["Z", "A"])
    graph = standardize_runner_graph({"success": True, "data": {"nodes": ["Z", "A"], "edges": edges},
        "raw_results": {"probabilities": [[0, 0.9], [0.1, 0]]}}, capability_id=CDFM_SPEC.capability_id)
    assert graph.edges[0].prediction is None
    asset = build_causal_graph_model(graph.model_dump(mode="json"), graph_id="legacy")
    assert asset.edges[0].prediction is None
    assert "diagnostics" not in asset.metadata


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), True, "0.5"])
def test_invalid_prediction_fails_the_existing_contract_path(value):
    with pytest.raises(ValidationError):
        EdgePrediction(forward_probability=value, reverse_probability=0.1)
    with pytest.raises(RunnerContractError, match="prediction"):
        standardize_runner_graph({"success": True, "data": {"nodes": ["Z", "A"], "edges": [
            {"from": "Z", "to": "A", "arrows": "to",
             "prediction": {"forward_probability": value, "reverse_probability": 0.1}},
        ]}}, capability_id=CDFM_SPEC.capability_id)
    assert build_causal_graph_model({"nodes": ["Z", "A"], "edges": [
        {"from": "Z", "to": "A", "arrows": "from",
         "prediction": {"forward_probability": value, "reverse_probability": 0.1}},
    ]}, graph_id="invalid") is None


def test_no_edge_does_not_expose_probability_entries():
    assert adjacency_to_edges(np.zeros((2, 2)), ["Z", "A"], probabilities=np.full((2, 2), 0.2)) == []
