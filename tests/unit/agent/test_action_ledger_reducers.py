"""AlgorithmResult、Action Ledger 和 evidence reducer 测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from Agent.deep_agent_tools import (
    ActionAttempt,
    AlgorithmResult,
    AlgorithmResultProvenance,
    Diagnostics,
    EvidenceResult,
    InvocationRecord,
    ReducerConflictError,
    StandardizedGraph,
    WebEvidenceResult,
    merge_action_ledger,
    merge_algorithm_results,
    merge_evidence_results,
)


def _result(*, result_ref: str = "inv-1:0", summary: str | None = None) -> AlgorithmResult:
    return AlgorithmResult(
        result_ref=result_ref,
        invocation_id=result_ref.split(":")[0],
        provider_call_id="call-1",
        capability_id="causal.pc",
        capability_version="1.0",
        status="valid",
        standardized_graph=StandardizedGraph(graph_semantics="dag"),
        diagnostics=Diagnostics(summary=summary),
        provenance=AlgorithmResultProvenance(
            job_id="job-1",
            attempt_count=0,
            lease_epoch=1,
            input_identity="input-1",
            spec_digest="digest",
            invocation_id=result_ref.split(":")[0],
            capability_id="causal.pc",
            capability_version="1.0",
        ),
    )


def _record(*, revision: int, status: str = "running", result_ref: str | None = None) -> InvocationRecord:
    return InvocationRecord(
        invocation_id="inv-1",
        response_identity="resp-1",
        response_identity_source="provider_response_id",
        provider_response_id="resp-1",
        provider_call_id="call-1",
        tool_name="causal_pc",
        final_status="pending" if status in {"queued", "running"} else status,
        result_ref=result_ref,
        attempts={
            0: ActionAttempt(
                retry_ordinal=0,
                revision=revision,
                status=status,
            )
        },
    )


def test_algorithm_results_are_immutable_and_replay_idempotent() -> None:
    result = _result()
    merged = merge_algorithm_results({result.result_ref: result}, {result.result_ref: result})
    assert list(merged) == [result.result_ref]
    assert merged[result.result_ref] is not result

    changed = _result(summary="changed")
    with pytest.raises(ReducerConflictError, match="immutable"):
        merge_algorithm_results({result.result_ref: result}, {result.result_ref: changed})


def test_action_ledger_keeps_new_revision_and_ignores_stale_revision() -> None:
    queued = _record(revision=1, status="queued")
    running = _record(revision=2, status="running")
    merged = merge_action_ledger(
        {queued.invocation_id: queued},
        {running.invocation_id: running},
    )
    assert merged["inv-1"].attempts[0].revision == 2
    assert merged["inv-1"].attempts[0].status == "running"

    stale = _record(revision=1, status="queued")
    replayed = merge_action_ledger(merged, {stale.invocation_id: stale})
    assert replayed["inv-1"].attempts[0].revision == 2


def test_action_ledger_rejects_same_revision_with_different_content() -> None:
    first = _record(revision=2, status="running")
    second = _record(revision=2, status="failed")
    with pytest.raises(ReducerConflictError, match="conflicting revision"):
        merge_action_ledger(
            {first.invocation_id: first},
            {second.invocation_id: second},
        )


def test_action_ledger_retains_retry_history() -> None:
    first = _record(revision=3, status="failed")
    retry = first.model_copy(
        deep=True,
        update={
            "final_status": "succeeded",
            "result_ref": "inv-1:0",
            "attempts": {
                0: first.attempts[0],
                1: ActionAttempt(
                    retry_ordinal=1,
                    revision=1,
                    status="succeeded",
                    finished_at=datetime.now(timezone.utc),
                ),
            },
        },
    )
    merged = merge_action_ledger(
        {first.invocation_id: first},
        {retry.invocation_id: retry},
    )
    assert set(merged["inv-1"].attempts) == {0, 1}
    assert merged["inv-1"].final_status == "succeeded"


def test_evidence_reducer_uses_evidence_ref_and_keeps_web_fields() -> None:
    rag = EvidenceResult(evidence_ref="e1", snippet="RAG snippet")
    web = WebEvidenceResult(
        evidence_ref="e2",
        snippet="Web snippet",
        provider_status="ok",
        fetched_at=datetime.now(timezone.utc),
    )
    merged = merge_evidence_results({"e1": rag}, {"e2": web})
    assert set(merged) == {"e1", "e2"}
    assert isinstance(merged["e2"], WebEvidenceResult)

    with pytest.raises(ReducerConflictError, match="immutable"):
        merge_evidence_results(
            {"e1": rag},
            {"e1": EvidenceResult(evidence_ref="e1", snippet="changed")},
        )
