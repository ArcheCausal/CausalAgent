"""矩阵编码、混合图、门禁和报告资产的确定性检查。"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from Agent.causal.cdfm_runner import adjacency_to_edges
from Agent.causal_agent.analysis_context import graph_edge_text
from Agent.Report.assets import build_causal_graph_model
from Agent.deep_agent.context import TrustedJobIdentity
from Agent.deep_agent.finalization import FinalizationGate, StructuredResponseError
from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC, PC_SPEC
from Agent.deep_agent_tools.graph_validation import validate_graph_structure
from Agent.deep_agent_tools.identity import build_invocation_id
from Agent.deep_agent_tools.models import (
    ActionAttempt, AlgorithmExecutionCommand, InvocationRecord, McpInvocationContext,
)
from Agent.deep_agent_tools.normalization import result_from_runner_payload, standardize_runner_graph


@pytest.mark.parametrize(("forward", "backward", "expected"), [
    (0, 0, []),
    (1, 0, [{"from": "A", "to": "B", "arrows": "to"}]),
    (0, 1, [{"from": "B", "to": "A", "arrows": "to"}]),
    (1, 1, [{"from": "A", "to": "B", "arrows": "to,from"}]),
])
def test_pair_binary_decoding_matches_matrix_and_report(forward, backward, expected):
    edges = adjacency_to_edges(np.array([[0, forward], [backward, 0]]), ["A", "B"])
    assert edges == expected
    graph = standardize_runner_graph({"success": True, "data": {"nodes": ["A", "B"], "edges": edges}}, capability_id=CDFM_SPEC.capability_id)
    assert validate_graph_structure(graph, graph_semantics=CDFM_SPEC.graph_semantics).valid
    asset = build_causal_graph_model(graph.model_dump(mode="json"), graph_id="main")
    assert len(asset.edges) == len(edges)
    if forward and backward:
        assert asset.edges[0].edge_type == "bidirected"
        text = graph_edge_text(graph.edges[0].model_dump())
        assert "A ↔ B" in text and "尚未证实" in text


def test_undirected_closed_path_is_not_a_directed_cycle():
    graph = standardize_runner_graph({"success": True, "data": {"nodes": ["A", "B", "C"], "edges": [
        {"from": a, "to": b} for a, b in [("A", "B"), ("B", "C"), ("C", "A")]
    ]}}, capability_id=PC_SPEC.capability_id)
    assert all(edge.edge_type == "undirected" for edge in graph.edges)
    assert validate_graph_structure(graph, graph_semantics=PC_SPEC.graph_semantics).valid


def _cycle_contract():
    identity = TrustedJobIdentity(job_id="11111111-1111-4111-8111-111111111111",
        session_id="22222222-2222-4222-8222-222222222222", user_id=1, attempt_count=1,
        lease_epoch=1, worker_id="worker", input_identity="input")
    invocation = build_invocation_id(job_id=identity.job_id, response_identity="response", provider_call_id="call")
    now = datetime.now(timezone.utc)
    context = McpInvocationContext(invocation_id=invocation, job_id=identity.job_id, session_id=identity.session_id,
        user_id=1, attempt_count=1, lease_epoch=1, worker_id="worker", input_snapshot_digest="input",
        issued_at=now, expires_at=now + timedelta(minutes=1), key_id="key")
    command = AlgorithmExecutionCommand(invocation_id=invocation, capability_id=CDFM_SPEC.capability_id,
        capability_version=CDFM_SPEC.version, spec_digest=CDFM_SPEC.spec_digest, provider_call_id="call", input_identity="input")
    matrix = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]])
    payload = {"success": True, "data": {"nodes": ["A", "B", "C"], "edges": adjacency_to_edges(matrix, ["A", "B", "C"])}}
    result = result_from_runner_payload(payload, command=command, context=context, capability_version=CDFM_SPEC.version)
    return identity, result, now


def test_cycle_is_retained_and_fails_normalization_and_primary_gate():
    identity, result, now = _cycle_contract()
    assert result.status == "execution_failed"
    assert len(result.standardized_graph.edges) == 3
    assert result.graph_validation.violations == ["directed_cycle"]
    # 门禁必须独立复核，即使输入错误地声称状态 valid、结构检查通过。
    claimed = result.model_copy(deep=True, update={"status": "valid", "graph_validation": None})
    record = InvocationRecord(invocation_id=result.invocation_id, response_identity="response",
        response_identity_source="message_execution_id", provider_call_id="call", tool_name=CDFM_SPEC.tool_name,
        final_status="succeeded", result_ref=result.result_ref, job_id=identity.job_id, attempt_count=1,
        lease_epoch=1, worker_id="worker", input_identity="input",
        attempts={0: ActionAttempt(retry_ordinal=0, revision=2, status="succeeded", started_at=now, finished_at=now)})
    decision = {"outcome": "algorithm_supported", "primary_result_ref": result.result_ref,
        "result_assessments": [{"result_ref": result.result_ref, "disposition": "primary", "rationale": "contract input"}],
        "conflict_status": "none", "conflicts": [], "revision_proposals": [],
        "selection_rationale": "contract input", "confidence": "low", "confidence_basis": []}
    with pytest.raises(StructuredResponseError, match="graph structure"):
        FinalizationGate().validate(decision=decision, algorithm_results={result.result_ref: claimed},
            action_ledger={record.invocation_id: record}, trusted_identity=identity)
