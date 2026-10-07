"""主线 P1/P2-U 测试使用的确定性 fake AlgorithmExecutor。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from .algorithm_executor import AlgorithmExecutorError, validate_executor_result
from .error_codes import SafeErrorCode
from .identity import build_result_ref
from .models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultProvenance,
    McpInvocationContext,
    StandardizedGraph,
)


@dataclass(frozen=True)
class FakeExecutionCall:
    command: AlgorithmExecutionCommand
    trusted_context: McpInvocationContext


class FakeAlgorithmExecutor:
    """不连接 MCP、数据库或网络的可观察 fake。

    ``results`` 可以按 capability_id 或 invocation_id 提供结果模板；模板会
    被复制并重新绑定到当前 command/context，确保测试不会意外复用旧身份。
    ``failures`` 用于构造带安全错误码的失败路径。
    """

    def __init__(
        self,
        *,
        results: Mapping[str, AlgorithmResult] | None = None,
        failures: Mapping[str, BaseException | SafeErrorCode | str] | None = None,
    ) -> None:
        self._results = dict(results or {})
        self._failures = dict(failures or {})
        self.calls: list[FakeExecutionCall] = []

    async def execute(
        self,
        command: AlgorithmExecutionCommand,
        trusted_context: McpInvocationContext,
    ) -> AlgorithmResult:
        validate_executor_result_identity(command, trusted_context)
        self.calls.append(
            FakeExecutionCall(
                command=command.model_copy(deep=True),
                trusted_context=trusted_context.model_copy(deep=True),
            )
        )

        failure = self._failures.get(command.invocation_id)
        if failure is None:
            failure = self._failures.get(command.capability_id)
        if failure is not None:
            if isinstance(failure, AlgorithmExecutorError):
                raise failure
            if isinstance(failure, BaseException):
                raise AlgorithmExecutorError(
                    "fake executor configured to fail",
                    safe_error_code=SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
                ) from failure
            try:
                safe_code = SafeErrorCode(failure)
            except ValueError:
                safe_code = SafeErrorCode.ALGORITHM_EXECUTION_FAILED
            raise AlgorithmExecutorError(
                "fake executor configured to fail",
                safe_error_code=safe_code,
            )

        template = self._results.get(command.invocation_id)
        if template is None:
            template = self._results.get(command.capability_id)
        if template is None:
            template = self._default_result(command, trusted_context)
        else:
            template = self._rebind_result(template, command, trusted_context)
        return validate_executor_result(
            template,
            command=command,
            trusted_context=trusted_context,
        ).model_copy(deep=True)

    @staticmethod
    def _default_result(
        command: AlgorithmExecutionCommand,
        trusted_context: McpInvocationContext,
    ) -> AlgorithmResult:
        return AlgorithmResult(
            result_ref=build_result_ref(
                invocation_id=command.invocation_id,
                result_index=command.result_index,
            ),
            invocation_id=command.invocation_id,
            provider_call_id=command.provider_call_id,
            capability_id=command.capability_id,
            capability_version=command.capability_version,
            status="valid",
            standardized_graph=StandardizedGraph(
                graph_semantics="dag",
                nodes=[],
                edges=[],
            ),
            provenance=AlgorithmResultProvenance(
                job_id=trusted_context.job_id,
                attempt_count=trusted_context.attempt_count,
                lease_epoch=trusted_context.lease_epoch,
                input_identity=command.input_identity,
                spec_digest=command.spec_digest,
                invocation_id=command.invocation_id,
                capability_id=command.capability_id,
                capability_version=command.capability_version,
            ),
        )

    @staticmethod
    def _rebind_result(
        template: AlgorithmResult,
        command: AlgorithmExecutionCommand,
        trusted_context: McpInvocationContext,
    ) -> AlgorithmResult:
        provenance = template.provenance.model_copy(
            update={
                "job_id": trusted_context.job_id,
                "attempt_count": trusted_context.attempt_count,
                "lease_epoch": trusted_context.lease_epoch,
                "input_identity": command.input_identity,
                "spec_digest": command.spec_digest,
                "invocation_id": command.invocation_id,
                "capability_id": command.capability_id,
                "capability_version": command.capability_version,
            }
        )
        raw_result_ref = template.raw_result_ref
        if raw_result_ref is not None:
            raw_result_ref = (
                f"/raw_algorithm_results/{command.invocation_id}/"
                f"{command.result_index}.json"
            )
        return template.model_copy(
            deep=True,
            update={
                "result_ref": build_result_ref(
                    invocation_id=command.invocation_id,
                    result_index=command.result_index,
                ),
                "invocation_id": command.invocation_id,
                "provider_call_id": command.provider_call_id,
                "capability_id": command.capability_id,
                "capability_version": command.capability_version,
                "raw_result_ref": raw_result_ref,
                "provenance": provenance,
            },
        )


def validate_executor_result_identity(
    command: AlgorithmExecutionCommand,
    trusted_context: McpInvocationContext,
) -> None:
    """fake 与真实 executor 共用的第一层 command/context 身份检查。"""

    for actual, expected, field_name in (
        (command.invocation_id, trusted_context.invocation_id, "invocation_id"),
        (
            command.input_identity,
            trusted_context.input_snapshot_digest,
            "input_identity",
        ),
    ):
        if actual != expected:
            raise AlgorithmExecutorError(
                f"command/context identity mismatch: {field_name}",
                safe_error_code=SafeErrorCode.MCP_CONTEXT_INVALID,
            )
