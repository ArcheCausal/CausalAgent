"""被预算跳过的算法调用必须收尾为终态，否则 finalization 拒绝整次运行。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from Agent.deep_agent import TrustedJobIdentity
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS, PC_SPEC
from Agent.deep_agent_tools.dependency_dispatch import (
    build_algorithm_dependency_middleware,
)
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import ActionAttempt, InvocationRecord
from Agent.deep_agent_tools.registry import AlgorithmRegistry


IDENTITY = TrustedJobIdentity(
    job_id="00000000-0000-0000-0000-000000000201",
    session_id="00000000-0000-0000-0000-000000000202",
    user_id=7,
    attempt_count=1,
    lease_epoch=1,
    worker_id="worker-1",
    input_identity="input-sha",
)


def _middleware():
    registry = AlgorithmRegistry()
    for spec in DEFAULT_ALGORITHM_SPECS:
        registry.register(spec=spec, adapter=object())
    return build_algorithm_dependency_middleware(registry=registry)


def _pending(*, tool_name: str = PC_SPEC.tool_name, **overrides):
    data = dict(
        invocation_id=str(uuid4()),
        response_identity="response-1",
        response_identity_source="message_execution_id",
        provider_call_id="call-1",
        tool_name=tool_name,
        final_status="pending",
        job_id=IDENTITY.job_id,
        attempt_count=IDENTITY.attempt_count,
        lease_epoch=IDENTITY.lease_epoch,
        worker_id=IDENTITY.worker_id,
        input_identity=IDENTITY.input_identity,
        attempts={
            0: ActionAttempt(
                retry_ordinal=0,
                revision=1,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
        },
    )
    data.update(overrides)
    return InvocationRecord(**data)


def _close(state):
    runtime = SimpleNamespace(
        context=SimpleNamespace(trusted_identity=IDENTITY)
    )
    return asyncio.run(_middleware().aafter_agent(state, runtime))


def test_pending_algorithm_record_is_closed_with_matching_result() -> None:
    record = _pending()
    update = _close({"action_ledger": {record.invocation_id: record}})

    assert update is not None
    closed = update["action_ledger"][record.invocation_id]
    assert closed.final_status == "failed"
    assert closed.result_ref == f"{record.invocation_id}:0"
    attempt = closed.attempts[0]
    assert attempt.status == "failed"
    assert attempt.safe_error_code == SafeErrorCode.ALGORITHM_CANCELED
    assert attempt.finished_at is not None

    result = update["algorithm_results"][closed.result_ref]
    assert result.status == "execution_failed"
    assert result.result_ref == closed.result_ref
    assert result.invocation_id == record.invocation_id
    assert result.provider_call_id == record.provider_call_id
    assert result.capability_id == PC_SPEC.capability_id
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_CANCELED
    assert result.provenance.job_id == IDENTITY.job_id
    assert result.provenance.attempt_count == IDENTITY.attempt_count
    assert result.provenance.input_identity == IDENTITY.input_identity


def test_foreign_and_non_algorithm_records_are_left_alone() -> None:
    foreign = _pending(attempt_count=3)
    evidence = _pending(tool_name="rag_evidence_search")
    update = _close({
        "action_ledger": {
            foreign.invocation_id: foreign,
            evidence.invocation_id: evidence,
        }
    })

    assert update is None
