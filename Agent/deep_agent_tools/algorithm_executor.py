"""传输无关的 AlgorithmExecutor Protocol 和结果契约检查。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .error_codes import SafeErrorCode
from .identity import build_result_ref
from .models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultStatus,
    McpInvocationContext,
)


@dataclass(frozen=True)
class AlgorithmExecutionResponse:
    """Executor 返回的统一结果与仅供 Adapter 保存的 runner payload。"""

    result: AlgorithmResult
    raw_payload: Mapping[str, Any] | None = None


@runtime_checkable
class AlgorithmExecutor(Protocol):
    """Adapter 使用的异步执行接口。

    实现可以是 fake、未来的 MCP 2.2 client 或其他传输，但不能改变输入中
    的 invocation/capability/spec 身份，也不能把可信上下文交给模型构造。
    """

    async def execute(
        self,
        command: AlgorithmExecutionCommand,
        trusted_context: McpInvocationContext,
    ) -> AlgorithmResult | AlgorithmExecutionResponse:
        """执行一次只读算法调用并返回统一 AlgorithmResult。"""


class AlgorithmExecutorError(RuntimeError):
    """带稳定安全错误码的 executor 失败，不携带原始异常细节。"""

    def __init__(
        self,
        message: str,
        *,
        status: AlgorithmResultStatus = "execution_failed",
        safe_error_code: SafeErrorCode | str = SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.safe_error_code = safe_error_code


def validate_executor_command(
    command: AlgorithmExecutionCommand,
    *,
    trusted_context: McpInvocationContext,
) -> None:
    """在任何外部执行前校验 command 与可信上下文的身份绑定。"""

    checks = {
        "invocation_id": (command.invocation_id, trusted_context.invocation_id),
        "input_identity": (
            command.input_identity,
            trusted_context.input_snapshot_digest,
        ),
    }
    for field_name, (actual, expected) in checks.items():
        if actual != expected:
            raise AlgorithmExecutorError(
                f"command/context identity mismatch: {field_name}",
                safe_error_code=SafeErrorCode.MCP_CONTEXT_INVALID,
            )


def validate_executor_result(
    result: AlgorithmResult,
    *,
    command: AlgorithmExecutionCommand,
    trusted_context: McpInvocationContext,
) -> AlgorithmResult:
    """校验 executor 返回值与 command/context 的身份一致性。

    这是共享契约检查，不是 MCP 服务端鉴权；真正的 owner/lease 强读仍由
    后续运行时和 causal-mcp 负责。
    """

    validate_executor_command(command, trusted_context=trusted_context)
    checks = {
        "invocation_id": (result.invocation_id, command.invocation_id),
        "provider_call_id": (result.provider_call_id, command.provider_call_id),
        "capability_id": (result.capability_id, command.capability_id),
        "capability_version": (result.capability_version, command.capability_version),
        "provenance.spec_digest": (
            result.provenance.spec_digest,
            command.spec_digest,
        ),
        "provenance.input_identity": (
            result.provenance.input_identity,
            command.input_identity,
        ),
        "input_identity": (result.input_identity, command.input_identity),
        "provenance.job_id": (result.provenance.job_id, trusted_context.job_id),
        "provenance.attempt_count": (
            result.provenance.attempt_count,
            trusted_context.attempt_count,
        ),
        "provenance.lease_epoch": (
            result.provenance.lease_epoch,
            trusted_context.lease_epoch,
        ),
    }
    for field_name, (actual, expected) in checks.items():
        if actual != expected:
            raise AlgorithmExecutorError(
                f"executor result contract mismatch: {field_name}",
                safe_error_code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
            )
    expected_result_ref = build_result_ref(
        invocation_id=command.invocation_id,
        result_index=command.result_index,
    )
    if result.result_ref != expected_result_ref:
        raise AlgorithmExecutorError(
            "executor result contract mismatch: result_ref",
            safe_error_code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
        )
    return result
