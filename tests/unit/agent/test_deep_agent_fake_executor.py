"""Merged P2-U/MCP runtime-boundary tests."""

from __future__ import annotations

import asyncio

from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.state import from_deep_agent_output, to_deep_agent_input
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.fake_algorithm_executor import FakeAlgorithmExecutor
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand


def _identity() -> TrustedJobIdentity:
    return TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000401",
        session_id="00000000-0000-0000-0000-000000000402",
        user_id=7,
        worker_id="worker-p2u",
        attempt_count=1,
        lease_epoch=2,
        input_identity="snapshot",
    )


def test_parent_projection_keeps_runtime_dependencies_out_of_state() -> None:
    deep = to_deep_agent_input(
        {
            "messages": [],
            "file_summary": {"rows": 10, "columns": ["A", "B"]},
            "algorithm_executor": object(),
        }
    )
    assert deep["data_profile"].row_count == 10
    assert deep["algorithm_results"] == {}
    assert "algorithm_executor" not in deep

    parent = from_deep_agent_output(deep)
    assert parent["deep_agent_algorithm_results"] == {}


def test_runtime_identity_builds_matching_mcp_context_for_fake_executor() -> None:
    identity = _identity()
    invocation_id = "00000000-0000-0000-0000-000000000403"
    trusted_context = identity.to_mcp_context(
        invocation_id=invocation_id,
        key_id="current",
    )
    executor = FakeAlgorithmExecutor()
    runtime = AgentRunContext(
        execution_guard=None,
        trusted_identity=identity,
        algorithm_executor=executor,
    )
    command = AlgorithmExecutionCommand(
        invocation_id=invocation_id,
        capability_id=PC_SPEC.capability_id,
        capability_version=PC_SPEC.version,
        spec_digest=PC_SPEC.spec_digest,
        provider_call_id="p2u-call",
        input_identity=identity.input_identity,
    )

    result = asyncio.run(executor.execute(command, trusted_context))

    runtime.assert_state_safe({"algorithm_results": {result.result_ref: result}})
    assert result.invocation_id == invocation_id
    assert result.provenance.job_id == identity.job_id
