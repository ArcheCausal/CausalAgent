"""worker 侧图引用门禁与跨轮 artifact 发布。"""

from __future__ import annotations

from Agent.deep_agent import TrustedJobIdentity
from Agent.deep_agent_tools.algorithm_specs import CAUSAL_ATE_SPEC, PC_SPEC
from Agent.deep_agent_tools.algorithm_tools import _resolve_graph_artifacts
from Agent.deep_agent_tools.dependency_dispatch import _initial_artifacts
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    AlgorithmResultProvenance,
    DataProfile,
    Diagnostics,
    GraphEdge,
    StandardizedGraph,
)


IDENTITY = TrustedJobIdentity(
    job_id="00000000-0000-0000-0000-000000000001",
    session_id="00000000-0000-0000-0000-000000000002",
    user_id=7,
    attempt_count=0,
    lease_epoch=1,
    worker_id="worker-1",
    input_identity="input-sha",
)

def _profile() -> DataProfile:
    return DataProfile(
        row_count=250,
        column_count=2,
        column_names=("x", "y"),
        numeric_columns=("x", "y"),
    )


def _discovery_result(
    *,
    status: str = "valid",
    attempt_count: int = 0,
    lease_epoch: int = 1,
    input_identity: str = "input-sha",
    source_input_identity: str | None = "input-sha",
    analysis_dataset_id: int | None = None,
) -> AlgorithmResult:
    graph = (
        StandardizedGraph(
            graph_semantics="pdag",
            nodes=["x", "y"],
            edges=[GraphEdge(source="x", target="y")],
        )
        if status == "valid"
        else None
    )
    return AlgorithmResult(
        result_ref="inv-1:0",
        invocation_id="inv-1",
        provider_call_id="call-0",
        capability_id="causal.pc",
        capability_version="2.0",
        status=status,
        standardized_graph=graph,
        diagnostics=(
            Diagnostics()
            if status == "valid"
            else Diagnostics(safe_error_code=SafeErrorCode.ALGORITHM_EXECUTION_FAILED)
        ),
        provenance=AlgorithmResultProvenance(
            job_id=str(IDENTITY.job_id),
            attempt_count=attempt_count,
            lease_epoch=lease_epoch,
            input_identity=input_identity,
            source_input_identity=source_input_identity,
            analysis_dataset_id=analysis_dataset_id,
            spec_digest=PC_SPEC.spec_digest,
        ),
    )


def test_resolves_graph_artifact_for_estimation() -> None:
    state = {"algorithm_results": {"inv-1:0": _discovery_result()}}
    rejection, artifacts = _resolve_graph_artifacts(
        spec=CAUSAL_ATE_SPEC,
        state=state,
        arguments={"graph_result_ref": "inv-1:0"},
        trusted_identity=IDENTITY,
    )
    assert rejection is None
    assert artifacts["standardized_graph"]["nodes"] == ["x", "y"]


def test_rejects_unknown_ref_and_lists_available() -> None:
    state = {"algorithm_results": {"inv-1:0": _discovery_result()}}
    rejection, artifacts = _resolve_graph_artifacts(
        spec=CAUSAL_ATE_SPEC,
        state=state,
        arguments={"graph_result_ref": "missing:0"},
        trusted_identity=IDENTITY,
    )
    assert artifacts == {}
    assert rejection is not None
    assert rejection["status"] == "not_ready"
    assert rejection["available_graph_result_refs"] == ["inv-1:0"]
    assert rejection["next_action"]


def test_rejects_non_valid_result() -> None:
    state = {
        "algorithm_results": {
            "inv-1:0": _discovery_result(status="execution_failed")
        }
    }
    rejection, _ = _resolve_graph_artifacts(
        spec=CAUSAL_ATE_SPEC,
        state=state,
        arguments={"graph_result_ref": "inv-1:0"},
        trusted_identity=IDENTITY,
    )
    assert rejection is not None
    assert rejection["available_graph_result_refs"] == []


def test_rejects_stale_attempt() -> None:
    state = {"algorithm_results": {"inv-1:0": _discovery_result(attempt_count=3)}}
    rejection, _ = _resolve_graph_artifacts(
        spec=CAUSAL_ATE_SPEC,
        state=state,
        arguments={"graph_result_ref": "inv-1:0"},
        trusted_identity=IDENTITY,
    )
    assert rejection is not None


def test_rejects_other_input_identity() -> None:
    state = {
        "algorithm_results": {"inv-1:0": _discovery_result(input_identity="other")}
    }
    rejection, _ = _resolve_graph_artifacts(
        spec=CAUSAL_ATE_SPEC,
        state=state,
        arguments={"graph_result_ref": "inv-1:0"},
        trusted_identity=IDENTITY,
    )
    assert rejection is not None


def test_rejects_other_source_input() -> None:
    state = {
        "algorithm_results": {
            "inv-1:0": _discovery_result(source_input_identity="other-sha")
        }
    }
    rejection, _ = _resolve_graph_artifacts(
        spec=CAUSAL_ATE_SPEC,
        state=state,
        arguments={"graph_result_ref": "inv-1:0"},
        trusted_identity=IDENTITY,
    )
    assert rejection is not None


def test_rejects_other_analysis_dataset() -> None:
    state = {
        "algorithm_results": {"inv-1:0": _discovery_result(analysis_dataset_id=42)}
    }
    rejection, _ = _resolve_graph_artifacts(
        spec=CAUSAL_ATE_SPEC,
        state=state,
        arguments={"graph_result_ref": "inv-1:0"},
        trusted_identity=IDENTITY,
    )
    assert rejection is not None


def test_non_estimation_spec_ignores_gate() -> None:
    rejection, artifacts = _resolve_graph_artifacts(
        spec=PC_SPEC,
        state={},
        arguments={},
        trusted_identity=IDENTITY,
    )
    assert rejection is None
    assert artifacts == {}


def test_initial_artifacts_include_published_graph() -> None:
    state = {
        "data_profile": _profile(),
        "algorithm_results": {"inv-1:0": _discovery_result()},
    }
    assert "standardized_graph" in _initial_artifacts(
        state, trusted_identity=IDENTITY
    )


def test_initial_artifacts_ignore_foreign_provenance() -> None:
    state = {
        "data_profile": _profile(),
        "algorithm_results": {"inv-1:0": _discovery_result(attempt_count=3)},
    }
    assert "standardized_graph" not in _initial_artifacts(
        state, trusted_identity=IDENTITY
    )
