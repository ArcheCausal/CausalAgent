"""P1 共享领域模型的纯单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from Agent.deep_agent_tools import (
    AlgorithmResult,
    AlgorithmResultProvenance,
    Diagnostics,
    FinalAnalysisDecision,
    McpInvocationContext,
    ResultAssessment,
    SafeErrorCode,
    StandardizedGraph,
    build_raw_result_metadata,
    canonical_json_bytes,
)


def _provenance() -> AlgorithmResultProvenance:
    return AlgorithmResultProvenance(
        job_id="job-1",
        attempt_count=2,
        lease_epoch=7,
        input_identity="file-object-sha256",
        spec_digest="spec-digest",
        invocation_id="invocation-1",
        capability_id="causal.pc",
        capability_version="1.0",
    )


def _valid_result() -> AlgorithmResult:
    return AlgorithmResult(
        result_ref="invocation-1:0",
        invocation_id="invocation-1",
        provider_call_id="call-1",
        capability_id="causal.pc",
        capability_version="1.0",
        status="valid",
        standardized_graph=StandardizedGraph(
            graph_semantics="dag",
            nodes=["x", "y"],
            edges=[],
        ),
        provenance=_provenance(),
    )


def test_valid_result_keeps_provenance_and_graph_semantics() -> None:
    result = _valid_result()

    assert result.graph_semantics == "dag"
    assert result.graph is result.standardized_graph
    assert result.input_identity == "file-object-sha256"
    assert result.provenance.invocation_id == result.invocation_id


def test_result_ref_must_be_canonical_and_derived_from_invocation() -> None:
    for result_ref in ("other:0", "invocation-1:00", "invocation-1:-1"):
        with pytest.raises(ValidationError, match="result_ref"):
            AlgorithmResult.model_validate(
                _valid_result().model_dump(mode="json")
                | {"result_ref": result_ref}
            )


def test_non_valid_result_requires_safe_error_code() -> None:
    with pytest.raises(ValidationError, match="safe_error_code"):
        AlgorithmResult(
            result_ref="invocation-1:0",
            invocation_id="invocation-1",
            provider_call_id="call-1",
            capability_id="causal.pc",
            capability_version="1.0",
            status="execution_failed",
            provenance=_provenance(),
        )

    result = AlgorithmResult(
        result_ref="invocation-1:0",
        invocation_id="invocation-1",
        provider_call_id="call-1",
        capability_id="causal.pc",
        capability_version="1.0",
        status="execution_failed",
        diagnostics=Diagnostics(
            safe_error_code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
        ),
        provenance=_provenance(),
    )
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID

    with pytest.raises(ValidationError, match="must not be classified as invalid_input"):
        AlgorithmResult(
            result_ref="invocation-1:0",
            invocation_id="invocation-1",
            provider_call_id="call-1",
            capability_id="causal.pc",
            capability_version="1.0",
            status="invalid_input",
            diagnostics=Diagnostics(
                safe_error_code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
            ),
            provenance=_provenance(),
        )


def test_raw_result_metadata_uses_stable_canonical_json() -> None:
    first = canonical_json_bytes({"z": 1, "a": "中文"})
    second = canonical_json_bytes({"a": "中文", "z": 1})
    assert first == second

    metadata = build_raw_result_metadata(
        raw_result_ref="/raw_algorithm_results/invocation-1/0.json",
        raw_result={"z": 1, "a": "中文"},
    )
    assert metadata.raw_result_size_bytes == len(first)
    assert len(metadata.raw_result_sha256) == 64
    assert metadata.raw_result_serialization_version == "canonical-json-v1"


def test_mcp_context_canonicalizes_uuid_and_requires_aware_window() -> None:
    context = McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        session_id="00000000-0000-0000-0000-000000000003",
        user_id=42,
        attempt_count=0,
        lease_epoch=3,
        worker_id="worker-1",
        input_snapshot_digest="digest",
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        key_id="current",
    )
    assert context.job_id == "00000000-0000-0000-0000-000000000002"

    invalid_payload = context.model_dump(mode="python")
    invalid_payload["job_id"] = "not-a-uuid"
    with pytest.raises(ValidationError, match="canonical UUID"):
        McpInvocationContext.model_validate(invalid_payload)


def test_final_analysis_decision_rejects_multiple_primary_graphs() -> None:
    with pytest.raises(ValidationError, match="primary_graph assessment"):
        FinalAnalysisDecision(
            outcome="algorithm_supported",
            primary_graph_result_ref="r1",
            effect_status="not_requested",
            result_assessments=[
                ResultAssessment(result_ref="r1", disposition="primary_graph", rationale="a"),
                ResultAssessment(result_ref="r2", disposition="primary_graph", rationale="b"),
            ],
            conflict_status="none",
            selection_rationale="选择 r1",
            confidence="medium",
        )


def test_standardized_graph_validates_probability_scores() -> None:
    graph = StandardizedGraph(
        graph_semantics="acyclic_directed_mixed_graph",
        nodes=["X0", "X1"],
        edge_score_matrix=[[0.0, 0.9], [0.1, 0.0]],
        score_type="probability",
    )
    assert graph.edge_score_matrix == [[0.0, 0.9], [0.1, 0.0]]
    assert graph.score_type == "probability"

    with pytest.raises(ValidationError):
        StandardizedGraph(graph_semantics="x", nodes=["X0"], edge_score_matrix=[[0.5]])
    with pytest.raises(ValidationError):
        StandardizedGraph(graph_semantics="x", nodes=["X0"], score_type="probability")
    with pytest.raises(ValidationError):
        StandardizedGraph(
            graph_semantics="x", nodes=["X0", "X1"],
            edge_score_matrix=[[1.2, 0.0], [0.0, 0.0]], score_type="probability",
        )
    with pytest.raises(ValidationError):
        StandardizedGraph(
            graph_semantics="x", nodes=["X0", "X1"],
            edge_score_matrix=[[0.0, 0.0]], score_type="probability",
        )
    with pytest.raises(ValidationError):
        StandardizedGraph(
            graph_semantics="x", nodes=["X0"],
            edge_score_matrix=[[0.5]], score_type="coefficient",
        )


def _decision_payload():
    return {"outcome": "algorithm_supported", "primary_graph_result_ref": "graph:0",
            "primary_effect_result_ref": "effect:0", "effect_status": "adopted",
            "result_assessments": [
                {"result_ref": "graph:0", "disposition": "primary_graph", "rationale": "使用源图"},
                {"result_ref": "effect:0", "disposition": "primary_effect", "rationale": "采用同图估计"},
            ], "conflict_status": "none", "selection_rationale": "分别选择图和效应", "confidence": "medium"}


def test_independent_primary_references_project_to_effect() -> None:
    decision = FinalAnalysisDecision.model_validate(_decision_payload())
    assert decision.primary_graph_result_ref == "graph:0"
    assert decision.primary_effect_result_ref == "effect:0"
    assert decision.primary_result_ref == "effect:0"
    assert FinalAnalysisDecision.model_validate(decision.model_dump(mode="json")) == decision


def test_graph_can_be_retained_without_adopting_effect() -> None:
    payload = _decision_payload()
    payload.update(primary_effect_result_ref=None, effect_status="not_adopted", effect_reason="模型假设尚未得到充分支持")
    payload["result_assessments"][1]["disposition"] = "supporting"
    decision = FinalAnalysisDecision.model_validate(payload)
    assert decision.primary_graph_result_ref == decision.primary_result_ref == "graph:0"
    assert decision.primary_effect_result_ref is None


@pytest.mark.parametrize("updates,message", [
    ({"primary_result_ref": "graph:0"}, "compatibility projection"),
    ({"effect_status": "incomplete"}, "effect_status=adopted"),
    ({"primary_graph_result_ref": None, "result_assessments": [
        {"result_ref": "effect:0", "disposition": "primary_effect", "rationale": "估计"}]}, "requires its source graph"),
])
def test_decision_rejects_inconsistent_primary_contract(updates, message) -> None:
    with pytest.raises(ValidationError, match=message):
        FinalAnalysisDecision.model_validate({**_decision_payload(), **updates})


def test_legacy_single_primary_cannot_infer_graph_or_effect() -> None:
    payload = _decision_payload()
    payload.pop("primary_graph_result_ref")
    payload.pop("primary_effect_result_ref")
    payload.update(primary_result_ref="effect:0", effect_status="not_recorded", result_assessments=[
        {"result_ref": "effect:0", "disposition": "primary", "rationale": "旧版选择"}])
    with pytest.raises(ValidationError) as failure:
        FinalAnalysisDecision.model_validate(payload)
    assert any(error["loc"] == ("result_assessments", 0, "disposition")
               and error["type"] == "literal_error" for error in failure.value.errors())


def test_legacy_primary_cannot_add_an_extra_primary_beside_explicit_roles() -> None:
    payload = _decision_payload()
    payload["result_assessments"].append({
        "result_ref": "extra:0", "disposition": "primary", "rationale": "旧主结果不能形成额外选择"})
    with pytest.raises(ValidationError) as failure:
        FinalAnalysisDecision.model_validate(payload)
    assert any(error["loc"] == ("result_assessments", 2, "disposition")
               and error["type"] == "literal_error" for error in failure.value.errors())
