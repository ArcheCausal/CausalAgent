"""Business boundary for the private causal-mcp execution tool."""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import io
import inspect
import logging
import re
import time
from typing import Any, Callable, Mapping

from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
from Agent.deep_agent_tools.ate_applicability import (
    ATE_REASON_SUMMARIES,
    check_ate_graph,
    check_ate_profile,
)
from Agent.deep_agent_tools.normalization import (
    result_from_runner_payload, runner_method_metadata, standardize_runner_graph,
)
from Agent.deep_agent_tools.identity import build_result_ref
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultProvenance,
    AteApplicabilityCheck,
    CausalEffect,
    DataViewSummary,
    Diagnostics,
    McpInvocationContext,
    StandardizedGraph,
    canonical_json_bytes,
)
from Agent.deep_agent_tools.graph_validation import validate_graph_structure
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
from Agent.measurement_semantics import (
    ValidatedAlgorithmInput, normalize_semantic_kinds,
    semantic_kinds_from_validation_report,
)

from .auth import McpAuthError, verify_invocation
from .config import McpServerConfig
from .executor_pool import BoundedProcessPool, PoolExecutionError
from .models import ExecuteAlgorithmRequest, error_payload
from .runner_registry import RunnerRegistry, RunnerRegistryError
from observability.logging_runtime import log_context, log_event


LOGGER = logging.getLogger(__name__)


class McpServiceError(RuntimeError):
    def __init__(self, code: SafeErrorCode) -> None:
        super().__init__(code.value)
        self.safe_error_code = code


def load_validated_input_from_connection(
    context: McpInvocationContext,
    connection: Any,
) -> ValidatedAlgorithmInput:
    """Read only the validated, Job-bound analysis dataset from the strong primary."""

    if context.analysis_dataset_id is None or context.source_input_identity is None:
        raise McpServiceError(SafeErrorCode.MCP_CONTEXT_INVALID)

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT job_id, user_id, session_id, worker_id, attempt_count, lease_epoch,
                   execution_state, input_file_hash, prepared_dataset_id
            FROM analysis_jobs
            WHERE job_id = %s
            LIMIT 1
            """,
            (context.job_id,),
        )
        job = cursor.fetchone()
        if (
            not job
            or int(job["user_id"]) != context.user_id
            or job["session_id"] != context.session_id
            or job["worker_id"] != context.worker_id
            or int(job["attempt_count"]) != context.attempt_count
            or int(job["lease_epoch"]) != context.lease_epoch
            or job["execution_state"] != "leased"
        ):
            raise McpServiceError(SafeErrorCode.MCP_LEASE_STALE)
        if (
            job["input_file_hash"] != context.source_input_identity
            or job["prepared_dataset_id"] is None
            or int(job["prepared_dataset_id"]) != context.analysis_dataset_id
        ):
            raise McpServiceError(SafeErrorCode.MCP_CONTEXT_INVALID)
        cursor.execute(
            """
            SELECT d.id, d.source_hash, d.data_hash, data.content_hash,
                   data.file_content, d.validation_report
            FROM analysis_job_datasets AS d
            JOIN file_objects AS data
              ON data.id = d.data_object_id
             AND data.owner_user_id = %s
             AND data.content_hash = d.data_hash
            WHERE d.id = %s
              AND d.job_id = %s
              AND d.status = 'validated'
            LIMIT 1
            """,
            (context.user_id, context.analysis_dataset_id, context.job_id),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
    if (
        not row
        or row.get("source_hash") != context.source_input_identity
        or row.get("data_hash") != context.input_snapshot_digest
        or row.get("content_hash") != context.input_snapshot_digest
        or not row.get("file_content")
    ):
        raise McpServiceError(SafeErrorCode.MCP_CONTEXT_INVALID)
    try:
        semantic_kinds = semantic_kinds_from_validation_report(row.get("validation_report"))
        return ValidatedAlgorithmInput(
            csv_data=row["file_content"].decode("utf-8"),
            semantic_kinds=semantic_kinds,
        )
    except (AttributeError, UnicodeError, ValueError, TypeError) as exc:
        raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID) from exc


def load_validated_input(context: McpInvocationContext) -> ValidatedAlgorithmInput:
    """Strong-read the current Job/lease/file tuple from MySQL primary."""

    from app.db import get_read_connection

    with get_read_connection(consistency="strong") as connection:
        return load_validated_input_from_connection(context, connection)


def load_frozen_csv_from_connection(context: McpInvocationContext, connection: Any) -> str:
    """兼容仅需 CSV 的旧调用，仍要求有效的校验报告。"""
    return load_validated_input_from_connection(context, connection).csv_data


def load_frozen_csv(context: McpInvocationContext) -> str:
    """兼容仅需 CSV 的旧调用；生产执行使用完整可信输入。"""
    return load_validated_input(context).csv_data


def _validate_csv_limits(csv_data: str, config: McpServerConfig) -> None:
    encoded = csv_data.encode("utf-8")
    if len(encoded) > config.input_max_bytes:
        raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID)
    try:
        rows = csv.reader(io.StringIO(csv_data))
        header = next(rows)
    except (csv.Error, StopIteration) as exc:
        raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID) from exc
    if not header or len(header) > config.input_max_columns:
        raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID)
    for row_count, _row in enumerate(rows, start=1):
        if row_count > config.input_max_rows:
            raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID)


def _standardized_graph(raw_result: Mapping[str, Any], capability_id: str) -> StandardizedGraph:
    return standardize_runner_graph(raw_result, capability_id=capability_id)


def _failure_status(code: SafeErrorCode) -> str:
    if code == SafeErrorCode.ALGORITHM_NOT_APPLICABLE:
        return "not_applicable"
    if code == SafeErrorCode.ALGORITHM_INPUT_INVALID:
        return "invalid_input"
    if code == SafeErrorCode.ALGORITHM_NOT_READY:
        return "not_ready"
    if code == SafeErrorCode.ALGORITHM_TIMED_OUT:
        return "timed_out"
    return "execution_failed"


def _rejection_reason(code: SafeErrorCode) -> str:
    if code == SafeErrorCode.MCP_AUTH_FAILED:
        return "auth_failed"
    if code == SafeErrorCode.MCP_LEASE_STALE:
        return "fenced"
    if code == SafeErrorCode.MCP_TRANSPORT_FAILED:
        return "transport_error"
    if code == SafeErrorCode.MCP_CAPACITY_EXHAUSTED:
        return "pool_exhausted"
    return "invalid_schema"


def _terminal_reason(code: SafeErrorCode) -> str:
    """把终态错误码映射为事件目录登记的稳定原因码。"""
    if code == SafeErrorCode.ALGORITHM_TIMED_OUT:
        return "timeout"
    if code == SafeErrorCode.MCP_LEASE_STALE:
        return "fenced"
    if code == SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID:
        return "invalid_result"
    return "tool_error"


def _runner_failure_reason(code: SafeErrorCode) -> str:
    """runner 自身返回失败时的稳定原因码；区分依赖缺失与其他执行失败。"""
    if code == SafeErrorCode.ALGORITHM_NOT_READY:
        return "readiness_failed"
    if code == SafeErrorCode.ALGORITHM_INPUT_INVALID:
        return "invalid_schema"
    return _terminal_reason(code)


def _runner_error_type(raw_result: Mapping[str, Any]) -> str:
    """只接受受限格式的错误类型名，不回显 runner 的自由文本。"""

    diagnostics = raw_result.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    candidate = str(diagnostics.get("error_type") or raw_result.get("error_type") or "")
    return _runner_exception_type(candidate)


def _runner_exception_type(value: Any) -> str:
    """只接受受限格式的异常类名，拒绝把自由文本写进事件详情。"""

    candidate = str(value or "")
    return candidate if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", candidate) else "UnknownError"


def _runner_model_source(raw_result: Mapping[str, Any]) -> str:
    """返回权重来源枚举，用于区分本地卷与远端 Hub 两种失败现场。"""

    diagnostics = raw_result.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    value = str(diagnostics.get("model_source") or "")
    return value if value in {"local_dir", "hub"} else "unknown"


def _error_result(
    command: AlgorithmExecutionCommand,
    context: McpInvocationContext,
    *,
    code: SafeErrorCode,
    capability_version: str,
    ate_check: AteApplicabilityCheck | None = None,
) -> AlgorithmResult:
    spec = next((spec for spec in DEFAULT_ALGORITHM_SPECS if spec.capability_id == command.capability_id), None)
    return AlgorithmResult(
        result_ref=build_result_ref(
            invocation_id=command.invocation_id,
            result_index=command.result_index,
        ),
        invocation_id=command.invocation_id,
        provider_call_id=command.provider_call_id,
        capability_id=command.capability_id,
        capability_version=capability_version,
        status=_failure_status(code),
        ate_check=ate_check,
        method=spec.method_definition() if spec is not None and spec.version == capability_version else None,
        diagnostics=Diagnostics(
            summary=(
                ATE_REASON_SUMMARIES[ate_check.reason_code]
                if ate_check is not None and ate_check.reason_code in ATE_REASON_SUMMARIES
                else "算法调用未产出可用结果。"
            ),
            safe_error_code=code,
        ),
        provenance=AlgorithmResultProvenance(
            job_id=context.job_id,
            attempt_count=context.attempt_count,
            lease_epoch=context.lease_epoch,
            input_identity=command.input_identity,
            source_input_identity=context.source_input_identity,
            analysis_dataset_id=context.analysis_dataset_id,
            spec_digest=command.spec_digest,
            invocation_id=command.invocation_id,
            capability_id=command.capability_id,
            capability_version=capability_version,
            algorithm_runner_version=None,
        ),
    )


def _estimation_result(
    raw_result: Mapping[str, Any],
    *,
    command: AlgorithmExecutionCommand,
    context: McpInvocationContext,
    capability_version: str,
    graph: StandardizedGraph,
    data_view: DataViewSummary | None,
    ate_check: AteApplicabilityCheck,
) -> AlgorithmResult:
    """估计能力的成功结果：图取入参图，效应取 runner 返回值。"""

    effect_payload = raw_result.get("causal_effect")
    if not isinstance(effect_payload, Mapping):
        raise ValueError("estimation runner did not return causal_effect")
    effect = CausalEffect.model_validate(
        {
            "graph_result_ref": command.parameters.get("graph_result_ref"),
            **dict(effect_payload),
        }
    )
    if (
        effect.treatment != command.parameters.get("treatment")
        or effect.outcome != command.parameters.get("outcome")
        or effect.graph_result_ref != command.parameters.get("graph_result_ref")
    ):
        raise ValueError("estimation result does not match the requested pair")
    if (
        ate_check is None
        or ate_check.applicability != "ready"
        or effect.graph_result_ref != ate_check.graph_result_ref
        or effect.treatment != ate_check.treatment
        or effect.outcome != ate_check.outcome
        or effect.adjustment_set != ate_check.adjustment_set
    ):
        raise ValueError("estimation result does not match the completed ATE check")
    if data_view is not None and effect.n_rows != data_view.row_count:
        raise ValueError("estimation result changed the checked sample count")
    validation = validate_graph_structure(graph, graph_semantics=graph.graph_semantics)
    if not validation.valid:
        raise ValueError("input graph failed structural validation")
    metadata = runner_method_metadata(raw_result, command.capability_id)
    return AlgorithmResult(
        result_ref=build_result_ref(
            invocation_id=command.invocation_id,
            result_index=command.result_index,
        ),
        invocation_id=command.invocation_id,
        provider_call_id=command.provider_call_id,
        capability_id=command.capability_id,
        capability_version=capability_version,
        status="valid",
        **metadata,
        standardized_graph=graph,
        graph_validation=validation,
        causal_effect=effect,
        ate_check=ate_check,
        summary="算法调用完成。",
        data_view=data_view,
        diagnostics=Diagnostics(
            summary="后门调整估计完成。",
            sample_count=effect.n_rows,
            assumptions_checked=[
                "all_edges_directed",
                "acyclic",
                "treatment_ancestor_of_outcome",
            ],
        ),
        input_identity=command.input_identity,
        provenance=AlgorithmResultProvenance(
            job_id=context.job_id,
            attempt_count=context.attempt_count,
            lease_epoch=context.lease_epoch,
            input_identity=command.input_identity,
            source_input_identity=context.source_input_identity,
            analysis_dataset_id=context.analysis_dataset_id,
            spec_digest=command.spec_digest,
            invocation_id=command.invocation_id,
            capability_id=command.capability_id,
            capability_version=capability_version,
            algorithm_runner_version=metadata["implementation"].runner_version,
        ),
    )


class CausalMcpService:
    """Validate, authorize, queue and normalize one internal algorithm call."""

    def __init__(
        self,
        *,
        config: McpServerConfig,
        registry: RunnerRegistry,
        executor_pool: BoundedProcessPool,
        authority_reader: Callable[[McpInvocationContext], Any] = load_validated_input,
        database_probe: Callable[[], bool] | None = None,
        service_instance_id: str = "causal-mcp",
    ) -> None:
        self.config = config
        self.registry = registry
        self.executor_pool = executor_pool
        self.authority_reader = authority_reader
        self.database_probe = database_probe or self._default_database_probe
        self.service_instance_id = service_instance_id

    @property
    def signing_keys(self) -> dict[str, str]:
        keys = {self.config.signing_key_id: self.config.signing_key_current}
        if self.config.signing_key_previous:
            keys[self.config.signing_key_previous_id] = self.config.signing_key_previous
        return keys

    async def ready(self) -> tuple[bool, bool, bool]:
        config_ready = True
        mysql_ready = False
        try:
            mysql_ready = bool(await asyncio.to_thread(self.database_probe))
        except Exception:
            mysql_ready = False
        return config_ready, mysql_ready, self.executor_pool.ready()

    async def _read_authority(self, context: McpInvocationContext) -> ValidatedAlgorithmInput:
        """读取可信 CSV 和语义快照，拒绝只含 CSV 的旧 reader。"""

        try:
            if inspect.iscoroutinefunction(self.authority_reader):
                csv_data = await self.authority_reader(context)
            else:
                csv_data = await asyncio.to_thread(self.authority_reader, context)
                if inspect.isawaitable(csv_data):
                    csv_data = await csv_data
        except McpServiceError:
            raise
        except Exception as exc:
            # Database/driver details must not cross the private MCP boundary.
            raise McpServiceError(SafeErrorCode.MCP_TRANSPORT_FAILED) from exc
        if not isinstance(csv_data, ValidatedAlgorithmInput):
            raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID)
        return csv_data

    async def _confirm_authority(
        self, context: McpInvocationContext, expected: ValidatedAlgorithmInput,
    ) -> None:
        if await self._read_authority(context) != expected:
            raise McpServiceError(SafeErrorCode.MCP_CONTEXT_INVALID)

    async def execute_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        try:
            request = ExecuteAlgorithmRequest.model_validate(payload)
        except Exception:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={"capability": "unknown", "reason_code": "invalid_schema"},
            )
            return error_payload(SafeErrorCode.MCP_CONTEXT_INVALID.value)

        command = request.command
        context = request.trusted_context
        trace = {
            "service_instance_id": self.service_instance_id,
            "invocation_id": context.invocation_id,
            "retry_ordinal": request.retry_ordinal,
        }
        if command.invocation_id != context.invocation_id:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={
                    "capability": "unknown",
                    "reason_code": "invalid_runtime_context",
                },
            )
            return error_payload(SafeErrorCode.MCP_CONTEXT_INVALID.value, trace=trace)
        if command.input_identity != context.input_snapshot_digest:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={
                    "capability": "unknown",
                    "reason_code": "invalid_runtime_context",
                },
            )
            return error_payload(SafeErrorCode.MCP_CONTEXT_INVALID.value, trace=trace)
        try:
            verify_invocation(
                context,
                command,
                request.signature,
                keys=self.signing_keys,
                clock_skew_seconds=self.config.clock_skew_seconds,
            )
        except McpAuthError as exc:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={"capability": "unknown", "reason_code": "auth_failed"},
            )
            return error_payload(exc.safe_error_code.value, trace=trace)
        try:
            entry = self.registry.resolve(
                command.capability_id,
                command.capability_version,
                command.spec_digest,
            )
        except RunnerRegistryError as exc:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={"capability": "unknown", "reason_code": "invalid_schema"},
            )
            return error_payload(exc.safe_error_code.value, trace=trace)

        with log_context(
            user_id=context.user_id,
            session_id=context.session_id,
            job_id=context.job_id,
            invocation_id=context.invocation_id,
            worker_slot=context.worker_id.rsplit(":", 1)[-1],
            node="mcp_tool_node",
            tool=entry.capability_id,
        ):
            log_event(
                LOGGER,
                "mcp.request.received",
                details={
                    "capability": entry.capability_id,
                    "retry_ordinal": request.retry_ordinal,
                },
            )
            return await self._execute_verified(
                request=request,
                entry=entry,
                trace=trace,
                started_at=started_at,
            )

    async def _execute_verified(
        self,
        *,
        request: ExecuteAlgorithmRequest,
        entry: Any,
        trace: dict[str, Any],
        started_at: float,
    ) -> dict[str, Any]:
        command = request.command
        context = request.trusted_context
        ate_check: AteApplicabilityCheck | None = None
        try:
            trusted_input = await self._read_authority(context)
            csv_data = trusted_input.csv_data
            _validate_csv_limits(csv_data, self.config)
            from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS

            spec = next(
                spec for spec in DEFAULT_ALGORITHM_SPECS
                if spec.capability_id == command.capability_id
            )
            parameters = spec.model_input_schema.model_validate(command.parameters).model_dump(
                mode="json"
            )
            artifacts = dict(command.artifacts)
            if set(artifacts) - set(spec.requires):
                raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID)
            graph: StandardizedGraph | None = None
            if "standardized_graph" in artifacts:
                try:
                    graph = StandardizedGraph.model_validate(
                        artifacts["standardized_graph"]
                    )
                except (TypeError, ValueError) as exc:
                    raise McpServiceError(
                        SafeErrorCode.ALGORITHM_INPUT_INVALID
                    ) from exc
            if spec.kind == "causal_estimation" and graph is None:
                raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID)
            if graph is not None and not validate_graph_structure(
                graph, graph_semantics=graph.graph_semantics,
            ).valid:
                raise McpServiceError(SafeErrorCode.ALGORITHM_INPUT_INVALID)
            processed_profile = build_data_profile_from_csv(csv_data)
            semantic_kinds = normalize_semantic_kinds(
                trusted_input.semantic_kinds, columns=processed_profile.column_names,
            )
            if command.capability_id == "causal.ate":
                graph_check = check_ate_graph(
                    graph=graph,
                    graph_result_ref=parameters["graph_result_ref"],
                    treatment=parameters["treatment"],
                    outcome=parameters["outcome"],
                )
                ate_check = check_ate_profile(
                    graph_check, profile=processed_profile, semantic_kinds=semantic_kinds,
                )
                applicability = ate_check.applicability
            else:
                applicability, _reason = spec.assess_applicability(
                    profile=processed_profile,
                    semantic_kinds=semantic_kinds,
                )
            if applicability != "ready":
                code = (
                    SafeErrorCode.ALGORITHM_NOT_APPLICABLE
                    if applicability == "not_applicable"
                    else SafeErrorCode.ALGORITHM_INPUT_INVALID
                )
                raise McpServiceError(code)
            data_view = DataViewSummary(
                row_count=processed_profile.row_count,
                column_count=processed_profile.column_count,
                columns=processed_profile.column_names,
            )
        except McpServiceError as exc:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={
                    "capability": entry.capability_id,
                    "reason_code": _rejection_reason(exc.safe_error_code),
                },
            )
            result = _error_result(
                command,
                context,
                code=exc.safe_error_code,
                capability_version=entry.version,
                ate_check=(
                    ate_check
                    if exc.safe_error_code == SafeErrorCode.ALGORITHM_NOT_APPLICABLE
                    else None
                ),
            )
            return {"ok": True, "result": result.model_dump(mode="json"), "trace": trace}
        except Exception:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={
                    "capability": entry.capability_id,
                    "reason_code": "invalid_schema",
                },
            )
            result = _error_result(
                command,
                context,
                code=SafeErrorCode.ALGORITHM_INPUT_INVALID,
                capability_version=entry.version,
            )
            return {"ok": True, "result": result.model_dump(mode="json"), "trace": trace}

        timeout_seconds = min(
            command.timeout_seconds or entry.timeout_seconds,
            entry.timeout_seconds,
            self.config.algorithm_deadline_seconds,
        )

        async def before_process_start(queue_wait_seconds: float) -> None:
            # A Job may lose its lease while waiting for process capacity.
            # Re-read the authoritative tuple immediately before submission.
            await self._confirm_authority(context, trusted_input)
            log_event(
                LOGGER,
                "mcp.request.accepted",
                details={
                    "capability": entry.capability_id,
                    "retry_ordinal": request.retry_ordinal,
                    "queue_wait_ms": int(queue_wait_seconds * 1000),
                    "timeout_seconds": timeout_seconds,
                },
            )

        try:
            execution_task = asyncio.create_task(
                self.executor_pool.execute(
                    capability_id=entry.capability_id,
                    runner=entry.runner,
                    csv_data=csv_data,
                    parameters=parameters,
                    artifacts=artifacts,
                    timeout_seconds=timeout_seconds,
                    concurrency=entry.concurrency,
                    before_start=before_process_start,
                    invocation_id=command.invocation_id,
                    execution_identity=(
                        context.job_id,
                        context.attempt_count,
                        context.lease_epoch,
                        context.worker_id,
                    ),
                )
            )
            try:
                outcome = await asyncio.wait_for(
                    asyncio.shield(execution_task),
                    timeout=self.config.slow_log_seconds,
                )
            except asyncio.TimeoutError:
                log_event(
                    LOGGER,
                    "mcp.tool.slow",
                    details={
                        "capability": entry.capability_id,
                        "duration_ms": int(
                            (time.perf_counter() - started_at) * 1000
                        ),
                        "timeout_seconds": int(self.config.slow_log_seconds),
                    },
                )
                outcome = await execution_task
            except asyncio.CancelledError:
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
                log_event(
                    LOGGER,
                    "mcp.tool.canceled",
                    details={
                        "capability": entry.capability_id,
                        "retry_ordinal": request.retry_ordinal,
                        "duration_ms": int(
                            (time.perf_counter() - started_at) * 1000
                        ),
                        "reason_code": "canceled",
                    },
                )
                raise
        except McpServiceError as exc:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={
                    "capability": entry.capability_id,
                    "reason_code": _rejection_reason(exc.safe_error_code),
                },
            )
            result = _error_result(
                command,
                context,
                code=exc.safe_error_code,
                capability_version=entry.version,
            )
            return {"ok": True, "result": result.model_dump(mode="json"), "trace": trace}
        except PoolExecutionError as exc:
            if exc.safe_error_code == SafeErrorCode.MCP_CAPACITY_EXHAUSTED:
                log_event(
                    LOGGER,
                    "mcp.capacity.rejected",
                    details={
                        "reason_code": "pool_exhausted",
                        "retry_after_seconds": exc.retry_after_seconds or 0,
                    },
                )
            elif exc.safe_error_code == SafeErrorCode.ALGORITHM_CANCELED:
                log_event(
                    LOGGER,
                    "mcp.tool.canceled",
                    details={
                        "capability": entry.capability_id,
                        "retry_ordinal": request.retry_ordinal,
                        "duration_ms": int(
                            (time.perf_counter() - started_at) * 1000
                        ),
                        "reason_code": "canceled",
                    },
                )
            else:
                log_event(
                    LOGGER,
                    "mcp.tool.failed",
                    details={
                        "capability": entry.capability_id,
                        "retry_ordinal": request.retry_ordinal,
                        "reason_code": (
                            "timeout"
                            if exc.safe_error_code == SafeErrorCode.ALGORITHM_TIMED_OUT
                            else "tool_error"
                        ),
                        "duration_ms": int(
                            (time.perf_counter() - started_at) * 1000
                        ),
                        "input_bytes": len(csv_data.encode("utf-8")),
                    },
                )
                if exc.safe_error_code == SafeErrorCode.ALGORITHM_EXECUTION_FAILED:
                    log_event(
                        LOGGER,
                        "mcp.runner.failed",
                        details={
                            "capability": entry.capability_id,
                            "error_type": _runner_exception_type(exc.runner_error_type),
                            "reason_code": _terminal_reason(exc.safe_error_code),
                            "model_source": "unknown",
                        },
                    )
            trace["executor_slot_id"] = None
            if exc.safe_error_code in {
                SafeErrorCode.ALGORITHM_TIMED_OUT,
                SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
            }:
                # A completed check cannot authorize publishing under a stale lease.
                try:
                    await self._confirm_authority(context, trusted_input)
                except McpServiceError as authority_error:
                    result = _error_result(
                        command, context, code=authority_error.safe_error_code,
                        capability_version=entry.version,
                    )
                    return {"ok": True, "result": result.model_dump(mode="json"), "trace": trace}
                result = _error_result(
                    command,
                    context,
                    code=exc.safe_error_code,
                    capability_version=entry.version,
                    ate_check=ate_check,
                )
                return {
                    "ok": True,
                    "result": result.model_dump(mode="json"),
                    "trace": trace,
                }
            return error_payload(
                exc.safe_error_code.value,
                retry_after_seconds=exc.retry_after_seconds,
                trace=trace,
            )

        trace["executor_slot_id"] = outcome.executor_slot_id
        trace["peak_rss_bytes"] = outcome.peak_rss_bytes
        trace["cpu_seconds"] = outcome.cpu_seconds
        try:
            # Never return a result produced by a worker/attempt/lease that
            # became stale while the CPU-bound algorithm was running.
            await self._confirm_authority(context, trusted_input)
        except McpServiceError as exc:
            log_event(
                LOGGER,
                "mcp.tool.failed",
                details={
                    "capability": entry.capability_id,
                    "retry_ordinal": request.retry_ordinal,
                    "duration_ms": int(
                        (time.perf_counter() - started_at) * 1000
                    ),
                    "input_bytes": len(csv_data.encode("utf-8")),
                    "reason_code": _rejection_reason(exc.safe_error_code),
                },
            )
            result = _error_result(
                command,
                context,
                code=exc.safe_error_code,
                capability_version=entry.version,
            )
            return {"ok": True, "result": result.model_dump(mode="json"), "trace": trace}
        raw_result = outcome.value
        completed_ate_check = ate_check
        if len(canonical_json_bytes(raw_result)) > self.config.result_max_bytes:
            result = _error_result(
                command,
                context,
                code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
                capability_version=entry.version,
                ate_check=ate_check,
            )
            return {"ok": True, "result": result.model_dump(mode="json"), "trace": trace}
        if not isinstance(raw_result, Mapping):
            result = _error_result(
                command,
                context,
                code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
                capability_version=entry.version,
                ate_check=ate_check,
            )
        elif raw_result.get("success") is not True:
            error_type = str(raw_result.get("error_type", ""))
            code = (
                SafeErrorCode.ALGORITHM_INPUT_INVALID
                if error_type == "InputValidationError"
                else SafeErrorCode.ALGORITHM_TIMED_OUT
                if error_type == "TimeoutError"
                else SafeErrorCode.ALGORITHM_NOT_READY
                if error_type == "DependencyUnavailableError"
                else SafeErrorCode.ALGORITHM_NOT_APPLICABLE
                if error_type == "NotApplicable"
                else SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
                if error_type == "ResultValidationError"
                else SafeErrorCode.ALGORITHM_EXECUTION_FAILED
            )
            if command.capability_id == "causal.ate" and error_type == "NotApplicable":
                reason = raw_result.get("reason_code")
                if isinstance(reason, str) and reason in ATE_REASON_SUMMARIES and ate_check is not None:
                    try:
                        ate_check = AteApplicabilityCheck.model_validate({
                            **ate_check.model_dump(),
                            "applicability": "not_applicable",
                            "reason_code": reason,
                        })
                    except ValueError:
                        code = SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
                else:
                    # Unknown exception text never becomes an applicability conclusion.
                    code = SafeErrorCode.ALGORITHM_EXECUTION_FAILED
            if ate_check is not None and code in {
                SafeErrorCode.ALGORITHM_INPUT_INVALID,
                SafeErrorCode.ALGORITHM_NOT_READY,
            }:
                # The trusted input already passed; a runner rejection is a later failure.
                code = SafeErrorCode.ALGORITHM_EXECUTION_FAILED
            log_event(
                LOGGER,
                "mcp.runner.failed",
                details={
                    "capability": entry.capability_id,
                    "error_type": _runner_error_type(raw_result),
                    "reason_code": _runner_failure_reason(code),
                    "model_source": _runner_model_source(raw_result),
                },
            )
            result = _error_result(
                command,
                context,
                code=code,
                capability_version=entry.version,
                ate_check=ate_check,
            )
            try:
                result = AlgorithmResult.model_validate({
                    **result.model_dump(),
                    **runner_method_metadata(raw_result, command.capability_id),
                })
            except ValueError:
                result = _error_result(
                    command, context,
                    code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
                    capability_version=entry.version,
                    ate_check=completed_ate_check,
                )
        else:
            try:
                metadata = runner_method_metadata(raw_result, command.capability_id)
                if spec.kind == "causal_estimation":
                    if graph is None:
                        raise ValueError("estimation requires a graph artifact")
                    result = _estimation_result(
                        raw_result,
                        command=command,
                        context=context,
                        capability_version=entry.version,
                        graph=graph,
                        data_view=data_view,
                        ate_check=ate_check,
                    )
                else:
                    normalized = result_from_runner_payload(
                        raw_result, command=command, context=context,
                        capability_version=entry.version,
                        runner_version=metadata["implementation"].runner_version,
                    )
                    result = AlgorithmResult(
                        result_ref=build_result_ref(
                            invocation_id=command.invocation_id,
                            result_index=command.result_index,
                        ),
                        invocation_id=command.invocation_id,
                        provider_call_id=command.provider_call_id,
                        capability_id=command.capability_id,
                        capability_version=entry.version,
                        status=normalized.status,
                        **metadata,
                        standardized_graph=normalized.standardized_graph,
                        graph_validation=normalized.graph_validation,
                        summary="算法调用完成。",
                        data_view=data_view,
                        diagnostics=normalized.diagnostics,
                        input_identity=command.input_identity,
                        provenance=AlgorithmResultProvenance(
                            job_id=context.job_id,
                            attempt_count=context.attempt_count,
                            lease_epoch=context.lease_epoch,
                            input_identity=command.input_identity,
                            source_input_identity=context.source_input_identity,
                            analysis_dataset_id=context.analysis_dataset_id,
                            spec_digest=command.spec_digest,
                            invocation_id=command.invocation_id,
                            capability_id=command.capability_id,
                            capability_version=entry.version,
                            algorithm_runner_version=normalized.provenance.algorithm_runner_version,
                        ),
                    )
            except Exception:
                result = _error_result(
                    command,
                    context,
                    code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
                    capability_version=entry.version,
                    ate_check=ate_check,
                )

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        input_bytes = len(csv_data.encode("utf-8"))
        if result.status == "valid":
            log_event(
                LOGGER,
                "mcp.tool.finished",
                details={
                    "capability": entry.capability_id,
                    "retry_ordinal": request.retry_ordinal,
                    "duration_ms": duration_ms,
                    "input_bytes": input_bytes,
                    "result_kind": "structured_result",
                },
            )
        else:
            safe_code = result.diagnostics.safe_error_code
            reason_code = _terminal_reason(safe_code)
            log_event(
                LOGGER,
                "mcp.tool.failed",
                details={
                    "capability": entry.capability_id,
                    "retry_ordinal": request.retry_ordinal,
                    "duration_ms": duration_ms,
                    "input_bytes": input_bytes,
                    "reason_code": reason_code,
                },
            )
        response = {"ok": True, "result": result.model_dump(mode="json"), "trace": trace}
        if isinstance(raw_result, Mapping) and result.status != "timed_out":
            response["raw_payload"] = dict(raw_result)
        return response

    async def cancel_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """鉴权并幂等取消一个精确的 MCP invocation。"""

        try:
            request = ExecuteAlgorithmRequest.model_validate(payload)
        except Exception:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={"capability": "unknown", "reason_code": "invalid_schema"},
            )
            return error_payload(SafeErrorCode.MCP_CONTEXT_INVALID.value)
        command = request.command
        context = request.trusted_context
        trace = {
            "service_instance_id": self.service_instance_id,
            "invocation_id": context.invocation_id,
            "retry_ordinal": request.retry_ordinal,
        }
        if (
            command.invocation_id != context.invocation_id
            or command.input_identity != context.input_snapshot_digest
        ):
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={
                    "capability": "unknown",
                    "reason_code": "invalid_runtime_context",
                },
            )
            return error_payload(SafeErrorCode.MCP_CONTEXT_INVALID.value, trace=trace)
        try:
            verify_invocation(
                context,
                command,
                request.signature,
                keys=self.signing_keys,
                clock_skew_seconds=self.config.clock_skew_seconds,
            )
            entry = self.registry.resolve(
                command.capability_id,
                command.capability_version,
                command.spec_digest,
            )
        except (McpAuthError, RunnerRegistryError) as exc:
            log_event(
                LOGGER,
                "mcp.request.rejected",
                details={
                    "capability": "unknown",
                    "reason_code": _rejection_reason(exc.safe_error_code),
                },
            )
            return error_payload(exc.safe_error_code.value, trace=trace)

        with log_context(
            user_id=context.user_id,
            session_id=context.session_id,
            job_id=context.job_id,
            invocation_id=context.invocation_id,
            worker_slot=context.worker_id.rsplit(":", 1)[-1],
            node="mcp_tool_node",
            tool=entry.capability_id,
        ):
            cancellation = await self.executor_pool.cancel(
                command.invocation_id,
                execution_identity=(
                    context.job_id,
                    context.attempt_count,
                    context.lease_epoch,
                    context.worker_id,
                ),
            )
            if cancellation.status == "identity_mismatch":
                log_event(
                    LOGGER,
                    "mcp.request.rejected",
                    details={
                        "capability": entry.capability_id,
                        "reason_code": "invalid_runtime_context",
                    },
                )
                return error_payload(SafeErrorCode.MCP_CONTEXT_INVALID.value, trace=trace)
            log_event(
                LOGGER,
                "mcp.cancel.finished",
                details={
                    "capability": entry.capability_id,
                    "cancellation_status": cancellation.status,
                },
            )
        return {
            "ok": True,
            "cancellation": {
                "status": cancellation.status,
                "invocation_id": cancellation.invocation_id,
            },
            "trace": trace,
        }

    @staticmethod
    def _default_database_probe() -> bool:
        from app.db import get_read_connection

        with get_read_connection(consistency="strong") as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT 1")
                return cursor.fetchone() is not None
            finally:
                cursor.close()
