"""UUIDv5 invocation identity 和 MCP canonical context 测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from Agent.deep_agent_tools import (
    McpInvocationContext,
    build_deep_agent_execution_scope,
    build_invocation_id,
    build_logical_call_key,
    build_result_ref,
    canonical_mcp_context_payload,
    resolve_response_identity,
)


JOB_ID = "00000000-0000-0000-0000-000000000001"


def _context() -> McpInvocationContext:
    return McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000010",
        job_id=JOB_ID,
        session_id="00000000-0000-0000-0000-000000000002",
        user_id=7,
        attempt_count=1,
        lease_epoch=4,
        worker_id="worker-1",
        input_snapshot_digest="snapshot",
        issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        key_id="current",
    )


def test_provider_response_identity_has_priority_over_local_fallback() -> None:
    identity = resolve_response_identity(
        provider_response_id="resp_123",
        message_execution_id="local_123",
    )
    assert identity.value == "resp_123"
    assert identity.source == "provider_response_id"

    fallback = resolve_response_identity(
        provider_response_id=None,
        message_execution_id="local_123",
    )
    assert fallback.value == "local_123"
    assert fallback.source == "message_execution_id"


def test_missing_response_identity_is_not_replaced_by_message_index() -> None:
    with pytest.raises(ValueError, match="message_execution_id"):
        resolve_response_identity(provider_response_id=None, message_execution_id=None)


def test_uuid5_identity_is_deterministic_and_item_id_is_not_part_of_key() -> None:
    key = build_logical_call_key(
        job_id=JOB_ID,
        response_identity="resp_123",
        provider_call_id="call_1",
    )
    first = build_invocation_id(
        job_id=JOB_ID,
        response_identity="resp_123",
        provider_call_id="call_1",
    )
    second = build_invocation_id(
        job_id=JOB_ID,
        response_identity="resp_123",
        provider_call_id="call_1",
    )
    different_call = build_invocation_id(
        job_id=JOB_ID,
        response_identity="resp_123",
        provider_call_id="call_2",
    )
    assert key == f"{JOB_ID}:resp_123:call_1"
    assert first == second
    assert first != different_call
    assert build_result_ref(invocation_id=first, result_index=0) == f"{first}:0"


def test_context_payload_is_canonical_json() -> None:
    payload = canonical_mcp_context_payload(_context())
    assert b'"job_id":"00000000-0000-0000-0000-000000000001"' in payload
    assert payload == canonical_mcp_context_payload(_context())


def test_deep_agent_execution_scope_changes_with_lease_or_input_snapshot() -> None:
    base = build_deep_agent_execution_scope(
        job_id=JOB_ID,
        attempt_count=1,
        lease_epoch=4,
        input_identity="snapshot",
    )
    assert base == build_deep_agent_execution_scope(
        job_id=JOB_ID,
        attempt_count=1,
        lease_epoch=4,
        input_identity="snapshot",
    )
    assert base != build_deep_agent_execution_scope(
        job_id=JOB_ID,
        attempt_count=2,
        lease_epoch=5,
        input_identity="snapshot",
    )
    assert base != build_deep_agent_execution_scope(
        job_id=JOB_ID,
        attempt_count=1,
        lease_epoch=4,
        input_identity="other-snapshot",
    )

