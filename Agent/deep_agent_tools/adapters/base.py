"""Algorithm Adapter 的共同可信执行边界。"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from Agent.execution_control import JobExecutionRevoked
from ..algorithm_executor import (
    AlgorithmExecutionResponse,
    AlgorithmExecutorError,
    validate_executor_result,
)
from ..algorithm_specs import AlgorithmSpec
from ..error_codes import SafeErrorCode
from ..identity import build_invocation_id, build_result_ref
from .normalization import result_from_runner_payload
from ..models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultProvenance,
    DataProfile,
    Diagnostics,
    McpInvocationContext,
    RAW_RESULT_SERIALIZATION_VERSION,
    RawResultMetadata,
    SafeWarning,
    build_raw_result_metadata,
    canonical_json_bytes,
)
from Agent.deep_agent.memory import CompositeBackend, build_in_memory_backend
from Agent.deep_agent.profile import normalize_virtual_path

if TYPE_CHECKING:
    from Agent.deep_agent.context import TrustedJobIdentity


class RawResultIntegrityError(ValueError):
    """raw 虚拟文件无法证明与 AlgorithmResult 元数据一致。"""


@dataclass(frozen=True)
class AdapterInput:
    """可信 Adapter 输入；不进入模型工具 schema。"""

    data_profile: DataProfile
    input_identity: str
    dataset_csv: str | None = None
    missing_values_present: bool = False
    # 新 MCP 路径由服务端按可信 Job/文件快照强读 CSV；因此不把文件正文
    # 放进 Deep Agent State。协议测试仍可要求显式 dataset_csv。
    dataset_authority_available: bool = False

    def __post_init__(self) -> None:
        if not self.input_identity or self.input_identity != self.input_identity.strip():
            raise ValueError("input_identity must be a non-blank string")


@dataclass(frozen=True)
class PreprocessingRecipe:
    version: str
    operations: tuple[str, ...]
    parameters: Mapping[str, Any]

    @property
    def digest(self) -> str:
        payload = {
            "version": self.version,
            "operations": list(self.operations),
            "parameters": dict(self.parameters),
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class AlgorithmAdapter(Protocol):
    spec: AlgorithmSpec

    async def run(
        self,
        *,
        parameters: Mapping[str, Any],
        adapter_input: AdapterInput,
        trusted_context: McpInvocationContext | "TrustedJobIdentity",
        provider_call_id: str,
        response_identity: str,
        retry_ordinal: int = 0,
        result_index: int = 0,
    ) -> AlgorithmResult:
        ...


class BaseAlgorithmAdapter(ABC):
    """统一执行顺序：参数→可信输入→预处理→executor→硬契约→raw metadata。"""

    def __init__(
        self,
        *,
        spec: AlgorithmSpec,
        executor: Any,
        raw_backend: CompositeBackend | None = None,
    ) -> None:
        self.spec = spec
        self.executor = executor
        self.raw_backend = raw_backend or build_in_memory_backend(user_id=1)

    @abstractmethod
    def preprocessing_recipe(self, adapter_input: AdapterInput) -> PreprocessingRecipe:
        """返回可逆、确定性且不删除样本的算法专属配方。"""

    @abstractmethod
    def validate_input(self, adapter_input: AdapterInput) -> tuple[str, SafeErrorCode] | None:
        """返回 (状态, 错误码) 表示不适用/未就绪，否则返回 None。"""

    def _context(
        self,
        trusted_context: McpInvocationContext | TrustedJobIdentity,
        *,
        invocation_id: str,
    ) -> McpInvocationContext:
        if isinstance(trusted_context, McpInvocationContext):
            if trusted_context.invocation_id != invocation_id:
                raise ValueError("trusted context invocation_id does not match call")
            return trusted_context
        return trusted_context.to_mcp_context(invocation_id=invocation_id)

    def _base_result(
        self,
        *,
        command: AlgorithmExecutionCommand,
        context: McpInvocationContext,
        status: str,
        safe_error_code: SafeErrorCode | str | None = None,
        summary: str | None = None,
        preprocessing_recipe_digest: str | None = None,
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
            status=status,
            diagnostics=Diagnostics(
                summary=summary,
                safe_error_code=safe_error_code,
            ),
            provenance=AlgorithmResultProvenance(
                job_id=context.job_id,
                attempt_count=context.attempt_count,
                lease_epoch=context.lease_epoch,
                input_identity=command.input_identity,
                spec_digest=command.spec_digest,
                invocation_id=command.invocation_id,
                capability_id=command.capability_id,
                capability_version=command.capability_version,
                preprocessing_recipe_digest=preprocessing_recipe_digest,
            ),
        )

    @staticmethod
    def _expected_raw_result_ref(command: AlgorithmExecutionCommand) -> str:
        return f"/raw_algorithm_results/{command.invocation_id}/{command.result_index}.json"

    @staticmethod
    def _read_backend_bytes(raw_backend: Any, raw_result_ref: str) -> bytes | None:
        """兼容协议 backend 的 bytes 读取和 Deep Agents ReadResult。"""

        stored = raw_backend.read(raw_result_ref)
        if isinstance(stored, (bytes, bytearray, memoryview)):
            return bytes(stored)
        file_data = getattr(stored, "file_data", None)
        if file_data is None and isinstance(stored, Mapping):
            file_data = stored.get("file_data")
        if not isinstance(file_data, Mapping):
            return None
        content = file_data.get("content")
        if not isinstance(content, str):
            return None
        return content.encode("utf-8")

    @staticmethod
    def _write_raw_result(
        raw_backend: Any,
        *,
        raw_result_ref: str,
        raw_result: Any,
    ) -> RawResultMetadata:
        """写入协议 backend 或官方 StateBackend 并返回 canonical 元数据。"""

        writer = getattr(raw_backend, "write_raw_result", None)
        if callable(writer):
            return writer(raw_result_ref=raw_result_ref, raw_result=raw_result)
        metadata = build_raw_result_metadata(
            raw_result_ref=raw_result_ref,
            raw_result=raw_result,
        )
        payload = canonical_json_bytes(raw_result)
        result = raw_backend.write(raw_result_ref, payload.decode("utf-8"))
        error = getattr(result, "error", None)
        if error is None and isinstance(result, Mapping):
            error = result.get("error")
        if error:
            raise RawResultIntegrityError("raw result backend rejected write")
        return metadata

    def _verify_raw_result_metadata(
        self,
        *,
        metadata: RawResultMetadata,
        expected_ref: str,
        raw_backend: Any | None = None,
    ) -> None:
        """从 backend 回读 raw 文件并验证完整性后才允许发布引用。"""

        raw_result_ref = metadata.raw_result_ref
        if not isinstance(raw_result_ref, str):
            raise RawResultIntegrityError("raw result reference is not a string")
        if raw_result_ref != expected_ref:
            raise RawResultIntegrityError("raw result reference does not match invocation")
        try:
            normalized_ref = normalize_virtual_path(raw_result_ref)
        except (TypeError, ValueError) as exc:
            raise RawResultIntegrityError("raw result reference is not a safe virtual path") from exc
        if normalized_ref != raw_result_ref or not raw_result_ref.startswith(
            "/raw_algorithm_results/"
        ):
            raise RawResultIntegrityError("raw result reference is not canonical")
        if metadata.raw_result_serialization_version != RAW_RESULT_SERIALIZATION_VERSION:
            raise RawResultIntegrityError("raw result serialization version is unsupported")

        # raw_backend.read() 是唯一的回读入口；这里不重新调用 executor，也不依据
        # executor 返回的 payload 代替 backend 中实际保存的字节。
        stored = self._read_backend_bytes(raw_backend or self.raw_backend, raw_result_ref)
        if stored is None:
            raise RawResultIntegrityError("raw result reference is missing")
        payload = stored

        try:
            decoded = payload.decode("utf-8")
            raw_value = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RawResultIntegrityError("raw result is not readable canonical JSON") from exc

        canonical_payload = canonical_json_bytes(raw_value)
        if payload != canonical_payload:
            raise RawResultIntegrityError("raw result is not canonical JSON")

        actual_size_bytes = len(payload)
        if actual_size_bytes != metadata.raw_result_size_bytes:
            raise RawResultIntegrityError("raw result size does not match metadata")
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != metadata.raw_result_sha256:
            raise RawResultIntegrityError("raw result hash does not match metadata")

    def _verify_raw_result(
        self,
        *,
        result: AlgorithmResult,
        command: AlgorithmExecutionCommand,
        raw_backend: Any | None = None,
    ) -> None:
        """统一验证 Adapter 写入和 executor 提供的 raw result 引用。"""

        if result.raw_result_ref is None:
            raise RawResultIntegrityError("raw result metadata is missing")
        metadata = RawResultMetadata(
            raw_result_ref=result.raw_result_ref,
            raw_result_sha256=result.raw_result_sha256,
            raw_result_size_bytes=result.raw_result_size_bytes,
            raw_result_serialization_version=result.raw_result_serialization_version,
        )
        self._verify_raw_result_metadata(
            metadata=metadata,
            expected_ref=self._expected_raw_result_ref(command),
            raw_backend=raw_backend,
        )

    async def run(
        self,
        *,
        parameters: Mapping[str, Any],
        adapter_input: AdapterInput,
        trusted_context: McpInvocationContext | "TrustedJobIdentity",
        provider_call_id: str,
        response_identity: str,
        retry_ordinal: int = 0,
        result_index: int = 0,
        executor: Any | None = None,
        raw_backend: Any | None = None,
    ) -> AlgorithmResult:
        if not provider_call_id or provider_call_id != provider_call_id.strip():
            raise ValueError("provider_call_id must be a non-blank string")
        if retry_ordinal < 0:
            raise ValueError("retry_ordinal must be non-negative")
        resolver = getattr(trusted_context, "current_input_identity", None)
        expected_input_identity = resolver() if callable(resolver) else None
        if expected_input_identity is None:
            expected_input_identity = getattr(trusted_context, "input_snapshot_digest", None)
        if expected_input_identity is None:
            expected_input_identity = getattr(trusted_context, "input_identity", None)
        if adapter_input.input_identity != expected_input_identity:
            raise AlgorithmExecutorError(
                "adapter input identity does not match trusted context",
                safe_error_code=SafeErrorCode.MCP_CONTEXT_INVALID,
            )

        # 先校验模型参数；Pydantic 错误只在内部用于分类，不原样进入输出。
        try:
            parsed_parameters = self.spec.model_input_schema.model_validate(dict(parameters))
        except Exception:
            invocation_id = build_invocation_id(
                job_id=(
                    trusted_context.job_id
                    if hasattr(trusted_context, "job_id")
                    else "00000000-0000-0000-0000-000000000000"
                ),
                response_identity=response_identity,
                provider_call_id=provider_call_id,
            )
            context = self._context(trusted_context, invocation_id=invocation_id)
            command = AlgorithmExecutionCommand(
                invocation_id=invocation_id,
                capability_id=self.spec.capability_id,
                capability_version=self.spec.version,
                spec_digest=self.spec.spec_digest,
                provider_call_id=provider_call_id,
                input_identity=adapter_input.input_identity,
                result_index=result_index,
                timeout_seconds=self.spec.default_timeout_seconds,
            )
            return self._base_result(
                command=command,
                context=context,
                status="invalid_input",
                safe_error_code=SafeErrorCode.ALGORITHM_INPUT_INVALID,
                preprocessing_recipe_digest=None,
            )

        invocation_id = build_invocation_id(
            job_id=(
                trusted_context.job_id
                if hasattr(trusted_context, "job_id")
                else "00000000-0000-0000-0000-000000000000"
            ),
            response_identity=response_identity,
            provider_call_id=provider_call_id,
        )
        context = self._context(trusted_context, invocation_id=invocation_id)
        recipe = self.preprocessing_recipe(adapter_input)
        command = AlgorithmExecutionCommand(
            invocation_id=invocation_id,
            capability_id=self.spec.capability_id,
            capability_version=self.spec.version,
            spec_digest=self.spec.spec_digest,
            provider_call_id=provider_call_id,
            input_identity=adapter_input.input_identity,
            parameters=parsed_parameters.model_dump(mode="json"),
            result_index=result_index,
            timeout_seconds=self.spec.default_timeout_seconds,
        )
        not_ready = self.validate_input(adapter_input)
        if not_ready is not None:
            status, safe_error_code = not_ready
            return self._base_result(
                command=command,
                context=context,
                status=status,
                safe_error_code=safe_error_code,
                preprocessing_recipe_digest=recipe.digest,
            )

        try:
            active_executor = executor or self.executor
            active_raw_backend = raw_backend or self.raw_backend
            execution_response = await active_executor.execute(command, context)
            raw_executor_payload = None
            if isinstance(execution_response, AlgorithmExecutionResponse):
                result = execution_response.result
                if execution_response.raw_payload is not None:
                    raw_executor_payload = dict(execution_response.raw_payload)
            else:
                result = execution_response
            if isinstance(result, Mapping):
                raw_executor_payload = dict(result)
                result = result_from_runner_payload(
                    result,
                    command=command,
                    context=context,
                    capability_version=self.spec.version,
                    runner_version=(
                        str(result.get("runner_version"))
                        if result.get("runner_version") is not None
                        else None
                    ),
                )
            result = validate_executor_result(
                result,
                command=command,
                trusted_context=context,
            )
            result = result.model_copy(
                deep=True,
                update={
                    "provenance": result.provenance.model_copy(
                        update={"preprocessing_recipe_digest": recipe.digest}
                    )
                },
            )
            # Fake executor 没有远端 raw payload，因此以标准化结果快照作为可复核
            # 的协议级 raw；真实 executor 若已提供 metadata 也必须从同一个 backend
            # 回读验证，不能仅凭引用和声明的 hash/size/version 放行。
            if result.raw_result_ref is None:
                raw_result_ref = (
                    f"/raw_algorithm_results/{command.invocation_id}/{result_index}.json"
                )
                raw_payload = raw_executor_payload or result.model_dump(mode="json")
                metadata = self._write_raw_result(
                    active_raw_backend,
                    raw_result_ref=raw_result_ref,
                    raw_result=raw_payload,
                )
                result = result.model_copy(
                    update={
                        "raw_result_ref": metadata.raw_result_ref,
                        "raw_result_sha256": metadata.raw_result_sha256,
                        "raw_result_size_bytes": metadata.raw_result_size_bytes,
                        "raw_result_serialization_version": metadata.raw_result_serialization_version,
                    }
                )
            self._verify_raw_result(
                result=result,
                command=command,
                raw_backend=active_raw_backend,
            )
            return result
        except AlgorithmExecutorError as exc:
            if exc.status == "canceled":
                # 取消/lease 失效属于控制流，不得被包装成普通算法降级结果。
                raise
            status = exc.status
            if status not in {
                "execution_failed",
                "timed_out",
                "not_ready",
                "canceled",
            }:
                status = "execution_failed"
            return self._base_result(
                command=command,
                context=context,
                status=status,
                safe_error_code=exc.safe_error_code,
                preprocessing_recipe_digest=recipe.digest,
            )
        except TimeoutError:
            return self._base_result(
                command=command,
                context=context,
                status="timed_out",
                safe_error_code=SafeErrorCode.ALGORITHM_TIMED_OUT,
                preprocessing_recipe_digest=recipe.digest,
            )
        except Exception as exc:
            # Job revoke 是 worker 控制流；它继承 RuntimeError，必须先于
            # 通用失败归类拦截，避免生成 execution_failed AlgorithmResult。
            if isinstance(exc, JobExecutionRevoked):
                raise
            return self._base_result(
                command=command,
                context=context,
                status="execution_failed",
                safe_error_code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
                preprocessing_recipe_digest=recipe.digest,
            )
