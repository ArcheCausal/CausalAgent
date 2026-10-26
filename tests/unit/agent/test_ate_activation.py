"""causal.ate 激活后的注册面。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.CausalAgentMCP.executor_pool import ExecutionOutcome
from Agent.CausalAgentMCP.runner_registry import build_default_registry
from Agent.CausalAgentMCP.service import CausalMcpService
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.adapters.ate import AteAdapter
from Agent.deep_agent_tools.algorithm_specs import (
    CAUSAL_ATE_SPEC,
    DEFAULT_ALGORITHM_SPECS,
    build_tool_schema_snapshot,
)
from Agent.deep_agent_tools.fake_algorithm_executor import FakeAlgorithmExecutor
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    McpInvocationContext,
)


GRAPH_PAYLOAD = {
    "graph_semantics": "pdag",
    "nodes": ["x", "y"],
    "edges": [{"source": "x", "target": "y", "edge_type": "directed"}],
}


def test_spec_is_activated() -> None:
    assert CAUSAL_ATE_SPEC in DEFAULT_ALGORITHM_SPECS
    assert "causal.ate" in [
        item["capability_id"] for item in build_tool_schema_snapshot()
    ]


def test_mcp_registry_resolves_ate() -> None:
    entry = build_default_registry().resolve(
        CAUSAL_ATE_SPEC.capability_id,
        CAUSAL_ATE_SPEC.version,
        CAUSAL_ATE_SPEC.spec_digest,
    )
    assert entry.capability_id == "causal.ate"


def test_default_adapters_bind_ate() -> None:
    adapters = build_default_adapters(executor=FakeAlgorithmExecutor())
    assert isinstance(adapters["causal.ate"], AteAdapter)


def test_service_rejects_undeclared_artifact_keys_before_execution() -> None:
    class Pool:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def ready(self) -> bool:
            return True

        async def execute(self, **kwargs):
            self.calls.append(kwargs)
            return ExecutionOutcome(value={"success": True}, executor_slot_id="process-0")

    issued = datetime.now(timezone.utc)
    invocation_id = "00000000-0000-0000-0000-000000000001"
    context = McpInvocationContext(
        invocation_id=invocation_id,
        job_id="00000000-0000-0000-0000-000000000002",
        session_id="00000000-0000-0000-0000-000000000003",
        user_id=7,
        attempt_count=1,
        lease_epoch=4,
        worker_id="worker-1",
        input_snapshot_digest="file-hash",
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        key_id="current",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=invocation_id,
        capability_id=CAUSAL_ATE_SPEC.capability_id,
        capability_version=CAUSAL_ATE_SPEC.version,
        spec_digest=CAUSAL_ATE_SPEC.spec_digest,
        provider_call_id="call-1",
        input_identity="file-hash",
        parameters={"graph_result_ref": "inv-1:0", "treatment": "x", "outcome": "y"},
        artifacts={"standardized_graph": GRAPH_PAYLOAD, "raw_dataset": "x"},
    )
    pool = Pool()
    service = CausalMcpService(
        config=McpServerConfig(
            service_token="service-token",
            signing_key_current="secret",
            signing_key_previous=None,
            signing_key_id="current",
            signing_key_previous_id="previous",
        ),
        registry=build_default_registry(),
        executor_pool=pool,
        # 故意少于 ATE 的 min_rows：若 artifacts 校验晚于适用性检查，状态就不是 invalid_input。
        authority_reader=lambda _context: "A,B\n1,2\n3,4\n",
        database_probe=lambda: True,
    )
    payload = asyncio.run(
        service.execute_payload(
            {
                "command": command.model_dump(mode="json"),
                "trusted_context": context.model_dump(mode="json"),
                "signature": sign_invocation(context, command, "secret"),
            }
        )
    )
    assert payload["result"]["status"] == "invalid_input"
    assert payload["result"]["diagnostics"]["safe_error_code"] == "ALGORITHM_INPUT_INVALID"
    assert pool.calls == []
