"""causal-mcp 在 CDFM runner 失败时写出的受控事件契约。"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.CausalAgentMCP.executor_pool import (
    BoundedProcessPool,
    ExecutionOutcome,
    PoolExecutionError,
    RunnerExecutionError,
)
from Agent.CausalAgentMCP.runner_registry import RunnerRegistry, RunnerSpec
from Agent.CausalAgentMCP.service import CausalMcpService
from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, McpInvocationContext


class _RunnerFailurePool:
    """返回 runner 失败 payload 的执行池替身，不启动真实进程。"""

    def ready(self) -> bool:
        return True

    async def execute(self, **kwargs):
        await kwargs["before_start"](0.0)
        return ExecutionOutcome(
            value={
                "schema_version": "causal_discovery_v1",
                "success": False,
                "algorithm": "cdfm",
                "runner_version": "cdfm-runner-v1",
                "error_type": "AlgorithmExecutionError",
                "message": "CDFM 模型加载失败。",
                "diagnostics": {
                    "error_type": "FileNotFoundError",
                    "error_message": "权重缺失",
                    "model_path": "/opt/cdfm-weights",
                    "model_source": "local_dir",
                },
            },
            executor_slot_id="process-0",
        )


def _context() -> McpInvocationContext:
    now = datetime.now(timezone.utc)
    return McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000801",
        job_id="00000000-0000-0000-0000-000000000802",
        session_id="00000000-0000-0000-0000-000000000803",
        user_id=7,
        attempt_count=1,
        lease_epoch=1,
        worker_id="unit-test-worker:0",
        input_snapshot_digest="a" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        key_id="current",
    )


def _command(context: McpInvocationContext) -> AlgorithmExecutionCommand:
    return AlgorithmExecutionCommand(
        invocation_id=context.invocation_id,
        capability_id=CDFM_SPEC.capability_id,
        capability_version=CDFM_SPEC.version,
        spec_digest=CDFM_SPEC.spec_digest,
        provider_call_id="unit-test-call",
        input_identity=context.input_snapshot_digest,
        parameters={"threshold": 0.5},
        timeout_seconds=30,
    )


def test_runner_failure_emits_controlled_diagnostics_without_free_text() -> None:
    """runner 失败必须写稳定字段，且不能把异常自由文本带进事件详情。"""

    context = _context()
    command = _command(context)
    registry = RunnerRegistry(
        [
            RunnerSpec(
                capability_id=CDFM_SPEC.capability_id,
                version=CDFM_SPEC.version,
                spec_digest=CDFM_SPEC.spec_digest,
                timeout_seconds=600,
                concurrency=1,
                runner=lambda *_: {},
            )
        ]
    )
    service = CausalMcpService(
        config=McpServerConfig(
            service_token="token",
            signing_key_current="secret",
            signing_key_previous=None,
            signing_key_id="current",
            signing_key_previous_id="previous",
            process_workers=1,
            queue_capacity=1,
            enqueue_timeout_seconds=1.0,
            algorithm_deadline_seconds=600,
            slow_log_seconds=60.0,
        ),
        registry=registry,
        executor_pool=_RunnerFailurePool(),
        authority_reader=lambda _context: "A,B\n1,2\n3,4\n",
        database_probe=lambda: True,
    )
    observed: list[tuple[str, dict | None]] = []

    def capture(_logger, event_code, **kwargs):
        observed.append((event_code, kwargs.get("details")))

    with patch("Agent.CausalAgentMCP.service.log_event", side_effect=capture):
        payload = asyncio.run(
            service.execute_payload(
                {
                    "command": command.model_dump(mode="json"),
                    "trusted_context": context.model_dump(mode="json"),
                    "signature": sign_invocation(context, command, "secret"),
                }
            )
        )

    assert payload["ok"] is True
    assert payload["result"]["status"] == "execution_failed"
    events = dict(observed)
    assert "mcp.runner.failed" in events
    details = events["mcp.runner.failed"]
    assert details["capability"] == CDFM_SPEC.capability_id
    assert details["error_type"] == "FileNotFoundError"
    assert details["reason_code"] == "tool_error"
    assert details["model_source"] == "local_dir"
    assert "error_message" not in details


def _boom(_csv_data, _parameters, _artifacts):
    """模块级 runner 替身；抛异常时必须保留异常类名而不是输出正文。"""
    print("third-party-noise: /tmp/secret-path")
    raise FileNotFoundError("weights missing")


def test_runner_exception_surfaces_only_its_type() -> None:
    """runner 抛异常时只把异常类名带回调用方，子进程输出不进入错误对象。"""

    async def scenario():
        pool = BoundedProcessPool(
            process_workers=1,
            queue_capacity=1,
            enqueue_timeout_seconds=5,
            executor_factory=ThreadPoolExecutor,
        )
        await pool.start()
        try:
            await pool.execute(
                capability_id="unit-test",
                runner=_boom,
                csv_data="A,B\n1,2\n",
                parameters={},
                timeout_seconds=30,
                concurrency=1,
            )
        finally:
            await pool.close()

    with pytest.raises(PoolExecutionError) as error:
        asyncio.run(scenario())

    assert error.value.safe_error_code.value == "ALGORITHM_EXECUTION_FAILED"
    assert error.value.runner_error_type == "FileNotFoundError"
    assert "secret-path" not in repr(error.value)


def test_runner_execution_error_round_trips_its_type() -> None:
    """异常跨进程归约后仍保留异常类名。"""

    import pickle

    restored = pickle.loads(pickle.dumps(RunnerExecutionError("RuntimeError")))

    assert restored.exception_type == "RuntimeError"
