"""causal-mcp 在 CDFM runner 失败时写出的受控事件契约。"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from datetime import datetime, timedelta, timezone

import pytest

from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.CausalAgentMCP.executor_pool import (
    BoundedProcessPool,
    PoolExecutionError,
    RunnerExecutionError,
)
from Agent.CausalAgentMCP.runner_registry import build_default_registry
from Agent.CausalAgentMCP.service import CausalMcpService
from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, McpInvocationContext
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor


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


def test_runner_failure_emits_controlled_diagnostics_without_free_text(tmp_path, monkeypatch, caplog) -> None:
    """真实进程执行 runner，分别验证缺依赖或空权重目录的失败合同。"""
    # 只选择当前环境应满足的合同；两种环境都执行正式 runner，不跳过。
    cdfm_available = importlib.util.find_spec("cdfm") is not None
    monkeypatch.setenv("CDFM_MODEL_PATH", str(tmp_path))
    local_input = LocalAlgorithmExecutor().validated_input
    caplog.set_level(logging.INFO, logger="Agent.CausalAgentMCP.service")

    async def scenario():
        context = _context()
        command = _command(context)
        pool = BoundedProcessPool(process_workers=1)
        service = CausalMcpService(
            config=McpServerConfig(
                service_token="token", signing_key_current="secret", signing_key_previous=None,
                signing_key_id="current", signing_key_previous_id="previous",
            ),
            registry=build_default_registry(), executor_pool=pool,
            authority_reader=lambda _context: local_input,
        )
        await pool.start()
        try:
            return await service.execute_payload({
                "command": command.model_dump(mode="json"),
                "trusted_context": context.model_dump(mode="json"),
                "signature": sign_invocation(context, command, "secret"),
            })
        finally:
            await pool.close()

    payload = asyncio.run(scenario())
    assert payload["ok"] is True
    # raw_payload 来自真实子进程；不以测试构造的失败 payload 代替 runner。
    raw = payload["raw_payload"]
    assert raw["success"] is False
    assert raw["schema_version"] == "causal_discovery_v1"
    assert raw["algorithm"] == "cdfm"
    if cdfm_available:
        assert raw["error_type"] == "AlgorithmExecutionError"
        assert raw["message"] == "CDFM 模型加载失败。"
        diagnostics = raw["diagnostics"]
        assert diagnostics["model_path"] == str(tmp_path)
        assert diagnostics["model_source"] == "local_dir"
        assert isinstance(diagnostics["error_message"], str)
        assert diagnostics["error_message"]
        error_type = diagnostics["error_type"]
        assert isinstance(error_type, str) and error_type.isidentifier()
        assert len(error_type) <= 64
        expected_status = "execution_failed"
        expected_code = "ALGORITHM_EXECUTION_FAILED"
        expected_reason = "tool_error"
        expected_source = "local_dir"
    else:
        assert raw["error_type"] == "DependencyUnavailableError"
        assert raw["message"] == "CDFM 依赖不可用。"
        assert "diagnostics" not in raw
        error_type = "DependencyUnavailableError"
        expected_status = "not_ready"
        expected_code = "ALGORITHM_NOT_READY"
        expected_reason = "readiness_failed"
        expected_source = "unknown"

    result = payload["result"]
    assert result["status"] == expected_status
    assert result["diagnostics"]["safe_error_code"] == expected_code
    assert result["standardized_graph"] is None
    assert result["raw_result_ref"] is None
    assert raw["message"] not in str(result)
    failures = [record for record in caplog.records if getattr(record, "event_code", None) == "mcp.runner.failed"]
    assert len(failures) == 1
    details = failures[0].details
    assert details == {
        "capability": CDFM_SPEC.capability_id,
        "error_type": error_type,
        "reason_code": expected_reason,
        "model_source": expected_source,
    }
    assert str(tmp_path) not in str(details)


def _boom(csv_data, _parameters):
    """真实文件读取失败；子进程只能输出异常类名。"""
    print("third-party-noise: /tmp/secret-path")
    from pathlib import Path

    return Path(csv_data).read_text(encoding="utf-8")


def test_runner_exception_surfaces_only_its_type(tmp_path) -> None:
    """runner 抛异常时只把异常类名带回调用方，子进程输出不进入错误对象。"""

    async def scenario():
        pool = BoundedProcessPool(
            process_workers=1,
            queue_capacity=1,
            enqueue_timeout_seconds=5,
        )
        await pool.start()
        try:
            await pool.execute(
                capability_id="unit-test",
                runner=_boom,
                csv_data=str(tmp_path / "missing-weights"),
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
