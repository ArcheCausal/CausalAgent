"""AlgorithmExecutor Protocol 与 fake executor 测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from Agent.deep_agent_tools import (
    AlgorithmExecutionCommand,
    AlgorithmExecutor,
    AlgorithmExecutorError,
    McpInvocationContext,
    PC_SPEC,
    SafeErrorCode,
    FakeAlgorithmExecutor,
)


def _context() -> McpInvocationContext:
    return McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000010",
        job_id="00000000-0000-0000-0000-000000000011",
        session_id="00000000-0000-0000-0000-000000000012",
        user_id=1,
        attempt_count=2,
        lease_epoch=9,
        worker_id="worker-1",
        input_snapshot_digest="snapshot",
        issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        key_id="current",
    )


def _command(context: McpInvocationContext) -> AlgorithmExecutionCommand:
    return AlgorithmExecutionCommand(
        invocation_id=context.invocation_id,
        capability_id=PC_SPEC.capability_id,
        capability_version=PC_SPEC.version,
        spec_digest=PC_SPEC.spec_digest,
        provider_call_id="call-1",
        input_identity=context.input_snapshot_digest,
    )


def test_fake_executor_implements_transport_neutral_protocol() -> None:
    executor = FakeAlgorithmExecutor()
    assert isinstance(executor, AlgorithmExecutor)
    context = _context()
    result = asyncio.run(executor.execute(_command(context), context))
    assert result.invocation_id == context.invocation_id
    assert result.provider_call_id == "call-1"
    assert len(executor.calls) == 1


def test_fake_executor_failure_is_safe_and_observable() -> None:
    executor = FakeAlgorithmExecutor(
        failures={PC_SPEC.capability_id: SafeErrorCode.MCP_CAPACITY_EXHAUSTED}
    )
    context = _context()
    with pytest.raises(AlgorithmExecutorError) as exc_info:
        asyncio.run(executor.execute(_command(context), context))
    assert exc_info.value.safe_error_code == SafeErrorCode.MCP_CAPACITY_EXHAUSTED


def test_fake_executor_rejects_command_context_identity_mismatch() -> None:
    executor = FakeAlgorithmExecutor()
    context = _context()
    command = _command(context).model_copy(
        update={"invocation_id": "00000000-0000-0000-0000-000000000099"}
    )
    with pytest.raises(AlgorithmExecutorError) as exc_info:
        asyncio.run(executor.execute(command, context))
    assert exc_info.value.safe_error_code == SafeErrorCode.MCP_CONTEXT_INVALID

