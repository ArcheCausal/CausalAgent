"""从 AlgorithmSpec/Registry 生成模型可见的本地 function tools。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from Agent.execution_control import JobExecutionRevoked
from Agent.measurement_semantics import normalize_semantic_kinds

if TYPE_CHECKING:
    from Agent.deep_agent.context import AgentRunContext

from .adapters.base import AdapterInput
from .algorithm_specs import AlgorithmSpec, build_model_tool_input_schema, get_algorithm_spec
from .models import (
    ActionAttempt,
    AlgorithmResult,
    AlgorithmResultProvenance,
    DataProfile,
    Diagnostics,
    InvocationRecord,
)
from .error_codes import SafeErrorCode
from .graph_validation import validate_graph_structure
from .identity import (
    build_invocation_id,
    build_result_ref,
    current_input_identity,
    provenance_matches_identity,
)
from .registry import AlgorithmRegistry, RegistryEntry
from .runtime_updates import (
    algorithm_terminal_command,
    emit_public_decision_event,
    emit_tool_lifecycle_event,
    normalize_tool_status,
    resolve_runtime_invocation,
    with_tool_runtime_schema,
)


def _graph_source_identity(trusted_identity: Any) -> tuple[str | None, int | None]:
    """当前可信身份的用户源文件摘要与分析数据集标识。"""

    resolver = getattr(trusted_identity, "source_input_identity", None)
    source = resolver() if callable(resolver) else None
    analysis_input = getattr(trusted_identity, "analysis_input", None)
    dataset_id = getattr(analysis_input, "dataset_id", None)
    return (
        source if isinstance(source, str) and source else None,
        dataset_id if isinstance(dataset_id, int) and not isinstance(dataset_id, bool) else None,
    )


def _trusted_graph_result_refs(
    state: Mapping[str, Any],
    *,
    trusted_identity: Any,
) -> list[str]:
    """列出本调用开始前已存在、可被当前模型引用的有效图结果。"""

    source, dataset_id = _graph_source_identity(trusted_identity)
    refs: list[str] = []
    for ref, raw_result in (state.get("algorithm_results") or {}).items():
        try:
            result = (
                raw_result
                if isinstance(raw_result, AlgorithmResult)
                else AlgorithmResult.model_validate(raw_result)
            )
            source_spec = get_algorithm_spec(result.capability_id)
        except (TypeError, ValueError):
            continue
        if (
            ref == result.result_ref
            and source_spec.kind == "causal_discovery"
            and result.capability_version == source_spec.version
            and result.provenance.spec_digest == source_spec.spec_digest
            and result.status == "valid"
            and result.standardized_graph is not None
            and validate_graph_structure(
                result.standardized_graph,
                graph_semantics=source_spec.graph_semantics,
            ).valid
            and provenance_matches_identity(result.provenance, trusted_identity)
            and result.provenance.source_input_identity == source
            and result.provenance.analysis_dataset_id == dataset_id
        ):
            refs.append(ref)
    return sorted(refs)


def _resolve_graph_artifacts(
    *,
    spec: AlgorithmSpec,
    state: Mapping[str, Any],
    arguments: Mapping[str, Any],
    trusted_identity: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """估计能力在调用前校验图引用；拒绝时返回可行动的 ToolMessage 载荷。"""

    if spec.kind != "causal_estimation":
        return None, {}
    available = _trusted_graph_result_refs(
        state, trusted_identity=trusted_identity
    )
    ref = arguments.get("graph_result_ref")
    if not isinstance(ref, str) or ref not in available:
        raw_source = (state.get("algorithm_results") or {}).get(ref) if isinstance(ref, str) else None
        not_ready = ref is None and not available
        if raw_source is not None:
            try:
                source_result = AlgorithmResult.model_validate(raw_source)
                not_ready = (
                    source_result.status == "not_ready"
                    and provenance_matches_identity(source_result.provenance, trusted_identity)
                    and source_result.provenance.source_input_identity == _graph_source_identity(trusted_identity)[0]
                    and source_result.provenance.analysis_dataset_id == _graph_source_identity(trusted_identity)[1]
                )
            except (TypeError, ValueError):
                pass
        return (
            {
                "status": "not_ready" if not_ready else "invalid_input",
                "reason": (
                    "所引用的因果图不存在、不是本次运行的有效结果，"
                    "或与当前数据版本不一致。"
                ),
                "available_graph_result_refs": available,
                "next_action": (
                    "先调用因果发现工具得到 status=valid 的结果，再携带它的 "
                    "result_ref 重试；若列表为空，确认数据版本与本次一致。"
                ),
                "unmet_requirements": ["valid_graph_result"],
            },
            {},
        )
    result = state["algorithm_results"][ref]
    if not isinstance(result, AlgorithmResult):
        result = AlgorithmResult.model_validate(result)
    return None, {"standardized_graph": result.standardized_graph.model_dump(mode="json")}


def _mark_unfinished_invocation(
    error: BaseException,
    *,
    invocation_id: str,
    pending_records: tuple[InvocationRecord, ...] = (),
) -> None:
    """在控制流异常上保留远端调用 identity，不构造失败结果。"""

    error.invocation_id = invocation_id
    error.remote_execution_status = "unknown"
    if pending_records:
        error.pending_ledger_records = pending_records


@dataclass(frozen=True)
class AlgorithmTool:
    """轻量工具描述；真实 LangChain Tool 在 graph builder 中惰性包装。"""

    entry: RegistryEntry
    adapter: Any
    runtime_context: "AgentRunContext | None" = None
    adapter_input: AdapterInput | None = None

    @property
    def name(self) -> str:
        return self.entry.spec.tool_name

    @property
    def description(self) -> str:
        return self.entry.spec.model_description()

    @property
    def args_schema(self) -> type:
        return self.entry.spec.model_input_schema

    def schema(self) -> dict[str, Any]:
        return self.entry.spec.build_tool_schema()

    @staticmethod
    def _data_profile_from_state(state: Mapping[str, Any]) -> DataProfile:
        profile = state.get("data_profile")
        if isinstance(profile, DataProfile):
            return profile.model_copy(deep=True)
        if isinstance(profile, Mapping):
            return DataProfile.model_validate(profile)
        raise ValueError("converted data_profile is required in algorithm State")

    @staticmethod
    def _trusted_semantic_kinds(identity: Any, profile: DataProfile) -> dict[str, str]:
        analysis_input = getattr(identity, "analysis_input", None)
        if analysis_input is None or analysis_input.dataset_id is None:
            raise ValueError("bound analysis dataset is required for algorithm input")
        return normalize_semantic_kinds(
            analysis_input.semantic_kinds, columns=profile.column_names,
        )

    def _adapter_input_from_runtime(
        self,
        runtime: Any,
    ) -> AdapterInput:
        """从当前 ToolRuntime 生成输入快照；文件正文不进入 State。"""

        context = getattr(runtime, "context", None)
        identity = getattr(context, "trusted_identity", None)
        if identity is None:
            raise RuntimeError("trusted runtime context is required for algorithm input")
        state = getattr(runtime, "state", None)
        if not isinstance(state, Mapping):
            raise RuntimeError("ToolRuntime.state must be a mapping")
        profile = self._data_profile_from_state(state)
        return AdapterInput(
            data_profile=profile,
            input_identity=current_input_identity(identity),
            missing_values_present=bool(state.get("missing_values_present")),
            dataset_authority_available=True,
            semantic_kinds=self._trusted_semantic_kinds(identity, profile),
        )

    def _record(
        self,
        *,
        provider_call_id: str,
        response_identity: str,
        response_identity_source: Literal["provider_response_id", "message_execution_id"],
        retry_ordinal: int,
        result: AlgorithmResult | None,
        status: str,
        revision: int,
        result_ref: str | None = None,
        runtime_context: "AgentRunContext | None" = None,
    ) -> InvocationRecord:
        active_context = runtime_context or self.runtime_context
        if active_context is None or active_context.trusted_identity is None:
            raise RuntimeError("trusted runtime context is required for Ledger")
        invocation_id = build_invocation_id(
            job_id=active_context.trusted_identity.job_id,
            response_identity=response_identity,
            provider_call_id=provider_call_id,
        )
        safe_error_code = None
        if result is not None:
            safe_error_code = result.diagnostics.safe_error_code
        return InvocationRecord(
            invocation_id=invocation_id,
            response_identity=response_identity,
            response_identity_source=response_identity_source,
            provider_response_id=(
                response_identity if response_identity_source == "provider_response_id" else None
            ),
            provider_call_id=provider_call_id,
            tool_name=self.name,
            final_status=(
                "pending"
                if status in {"queued", "running"}
                else "succeeded"
                if status == "succeeded"
                else "timed_out"
                if status == "timed_out"
                else "not_ready"
                if status == "not_ready"
                else "not_applicable"
                if status == "not_applicable"
                else "skipped"
                if status == "skipped"
                else "failed"
            ),
            result_ref=result_ref,
            job_id=str(active_context.trusted_identity.job_id),
            attempt_count=int(active_context.trusted_identity.attempt_count),
            lease_epoch=int(active_context.trusted_identity.lease_epoch),
            worker_id=active_context.trusted_identity.worker_id,
            input_identity=current_input_identity(active_context.trusted_identity),
            source_input_identity=active_context.trusted_identity.source_input_identity(),
            analysis_dataset_id=(
                active_context.trusted_identity.analysis_input.dataset_id
                if active_context.trusted_identity.analysis_input is not None
                else None
            ),
            attempts={
                retry_ordinal: ActionAttempt(
                    retry_ordinal=retry_ordinal,
                    revision=revision,
                    status=status,
                    started_at=datetime.now(timezone.utc) if status == "running" else None,
                    finished_at=(
                        datetime.now(timezone.utc)
                        if status not in {"queued", "running"}
                        else None
                    ),
                    safe_error_code=safe_error_code,
                )
            },
        )

    async def ainvoke(
        self,
        arguments: Mapping[str, Any] | None = None,
        *,
        provider_call_id: str,
        response_identity: str,
        retry_ordinal: int = 0,
        result_index: int = 0,
        runtime_context: "AgentRunContext | None" = None,
        adapter_input: AdapterInput | None = None,
    ) -> AlgorithmResult:
        """调用 Adapter；身份参数由 Tool runtime 注入而不是模型 schema。"""

        active_context = runtime_context or self.runtime_context
        if active_context is None or active_context.trusted_identity is None:
            raise RuntimeError("trusted runtime context is required for algorithm execution")
        await active_context.ensure_active()
        active_input = adapter_input or self.adapter_input
        if active_input is None:
            state = getattr(active_context, "_tool_state", None)
            if not isinstance(state, Mapping):
                state = {}
            active_input = AdapterInput(
                data_profile=self._data_profile_from_state(state),
                input_identity=current_input_identity(active_context.trusted_identity),
                missing_values_present=bool(state.get("missing_values_present")),
                dataset_authority_available=True,
            )
        # 每次调用重读可信绑定；显式 AdapterInput 和 State 都不能覆盖语义。
        active_input = replace(
            active_input,
            dataset_authority_available=True,
            semantic_kinds=self._trusted_semantic_kinds(
                active_context.trusted_identity, active_input.data_profile,
            ),
        )
        invocation_id = build_invocation_id(
            job_id=active_context.trusted_identity.job_id,
            response_identity=response_identity,
            provider_call_id=provider_call_id,
        )
        try:
            result = await self.adapter.run(
                parameters=dict(arguments or {}),
                adapter_input=active_input,
                trusted_context=active_context.trusted_identity,
                provider_call_id=provider_call_id,
                response_identity=response_identity,
                retry_ordinal=retry_ordinal,
                result_index=result_index,
                executor=active_context.algorithm_executor,
                raw_backend=active_context.filesystem_backend,
            )
            # Adapter/MCP 返回后必须再次确认 Job 资格，防止迟到结果进入
            # ToolMessage、AlgorithmResult 或 FinalizationGate。
            await active_context.ensure_active()
            return result
        except (JobExecutionRevoked, asyncio.CancelledError) as exc:
            _mark_unfinished_invocation(exc, invocation_id=invocation_id)
            raise

    async def ainvoke_with_ledger(
        self,
        arguments: Mapping[str, Any] | None = None,
        *,
        provider_call_id: str,
        response_identity: str,
        response_identity_source: Literal["provider_response_id", "message_execution_id"] = "provider_response_id",
        retry_ordinal: int = 0,
        result_index: int = 0,
    ) -> tuple[AlgorithmResult, tuple[InvocationRecord, ...]]:
        """执行一次调用并返回 queued/running/terminal 的完整 Ledger 片段。"""

        queued = self._record(
            provider_call_id=provider_call_id,
            response_identity=response_identity,
            response_identity_source=response_identity_source,
            retry_ordinal=retry_ordinal,
            result=None,
            status="queued",
            revision=0,
            runtime_context=self.runtime_context,
        )
        running = queued.model_copy(
            deep=True,
            update={
                "attempts": {
                    retry_ordinal: ActionAttempt(
                        retry_ordinal=retry_ordinal,
                        revision=1,
                        status="running",
                        started_at=datetime.now(timezone.utc),
                    )
                }
            },
        )
        # Adapter 会把可预期执行错误转换为 AlgorithmResult；这里仍会抛出的
        # 是取消、lease/guard 撤销或程序错误，不能在失去写资格后伪造 Ledger。
        try:
            result = await self.ainvoke(
                arguments,
                provider_call_id=provider_call_id,
                response_identity=response_identity,
                retry_ordinal=retry_ordinal,
                result_index=result_index,
            )
        except (JobExecutionRevoked, asyncio.CancelledError) as exc:
            _mark_unfinished_invocation(
                exc,
                invocation_id=queued.invocation_id,
                pending_records=(queued, running),
            )
            raise

        terminal_status = normalize_tool_status(result.status)
        terminal = self._record(
            provider_call_id=provider_call_id,
            response_identity=response_identity,
            response_identity_source=response_identity_source,
            retry_ordinal=retry_ordinal,
            result=result,
            status=terminal_status,
            revision=2,
            result_ref=result.result_ref if result.status == "valid" else None,
            runtime_context=self.runtime_context,
        )
        return result, (queued, running, terminal)

    def invoke(self, arguments: Mapping[str, Any] | None = None, **kwargs: Any) -> AlgorithmResult:
        """同步测试入口；运行中的 event loop 不会被偷偷嵌套。"""

        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.ainvoke(arguments, **kwargs))
        raise RuntimeError("AlgorithmTool.invoke cannot run inside an active event loop")

    def to_langchain_tool(self) -> Any:
        """包装为使用 ToolRuntime/Command 原子回写结果和 Ledger 的 Tool。"""

        from langchain.tools import ToolRuntime
        from langchain_core.tools import StructuredTool

        async def call(runtime: Any, **kwargs: Any) -> Any:
            identity = resolve_runtime_invocation(
                runtime,
                expected_context=self.runtime_context,
            )
            state = getattr(runtime, "state", None)
            if not isinstance(state, Mapping):
                raise RuntimeError("ToolRuntime.state must be a mapping")
            started_at = datetime.now(timezone.utc)
            runtime_adapter_input = self._adapter_input_from_runtime(runtime)
            active_adapter_input = runtime_adapter_input
            graph_rejection, graph_artifacts = _resolve_graph_artifacts(
                spec=self.entry.spec,
                state=state,
                arguments=kwargs,
                trusted_identity=identity.runtime_context.trusted_identity,
            )
            if graph_rejection is not None:
                await identity.runtime_context.ensure_active()
                trusted = identity.runtime_context.trusted_identity
                invocation_id = build_invocation_id(
                    job_id=trusted.job_id, response_identity=identity.response_identity,
                    provider_call_id=identity.provider_call_id,
                )
                source, dataset_id = _graph_source_identity(trusted)
                result = AlgorithmResult(
                    result_ref=build_result_ref(invocation_id=invocation_id, result_index=0),
                    invocation_id=invocation_id, provider_call_id=identity.provider_call_id,
                    capability_id=self.entry.spec.capability_id,
                    capability_version=self.entry.spec.version,
                    status=graph_rejection["status"], method=self.entry.spec.method_definition(),
                    diagnostics=Diagnostics(
                        summary=graph_rejection["reason"] + graph_rejection["next_action"],
                        safe_error_code=(
                            SafeErrorCode.ALGORITHM_NOT_READY if graph_rejection["status"] == "not_ready"
                            else SafeErrorCode.ALGORITHM_INPUT_INVALID
                        ),
                    ),
                    provenance=AlgorithmResultProvenance(
                        job_id=str(trusted.job_id), attempt_count=trusted.attempt_count,
                        lease_epoch=trusted.lease_epoch, input_identity=current_input_identity(trusted),
                        source_input_identity=source, analysis_dataset_id=dataset_id,
                        spec_digest=self.entry.spec.spec_digest,
                        invocation_id=invocation_id, capability_id=self.entry.spec.capability_id,
                        capability_version=self.entry.spec.version,
                    ),
                )
                return await algorithm_terminal_command(
                    runtime, identity=identity, tool_name=self.name,
                    result=result, started_at=started_at,
                )
            if graph_artifacts:
                active_adapter_input = replace(
                    active_adapter_input, artifacts=graph_artifacts
                )
            public_decision = kwargs.pop("public_decision", None)
            if public_decision is not None:
                await emit_public_decision_event(
                    runtime,
                    identity=identity,
                    tool_name=self.name,
                    public_decision=public_decision,
                )
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_start",
                identity=identity,
                tool_name=self.name,
            )
            # 取消只清理资源并传播；调度层区分 deadline 和外部撤销，统一收口终态。
            result = await self.ainvoke(
                kwargs,
                provider_call_id=identity.provider_call_id,
                response_identity=identity.response_identity,
                runtime_context=identity.runtime_context,
                adapter_input=active_adapter_input,
            )
            return await algorithm_terminal_command(
                runtime, identity=identity, tool_name=self.name,
                result=result, started_at=started_at,
            )

        # StructuredTool 会把 ToolRuntime 识别为 injected arg；显式 args_schema
        # 仍只暴露 AlgorithmSpec 中的科学参数，不让模型填写内部身份。
        call.__annotations__["runtime"] = ToolRuntime
        runtime_args_schema = with_tool_runtime_schema(
            build_model_tool_input_schema(
                self.args_schema,
            ),
            tool_name=self.name,
            tool_runtime_type=ToolRuntime,
        )
        return StructuredTool.from_function(
            coroutine=call,
            name=self.name,
            description=self.description,
            args_schema=runtime_args_schema,
        )


def build_algorithm_tools(
    registry: AlgorithmRegistry,
    *,
    runtime_context: "AgentRunContext | None" = None,
    data_profile: DataProfile | None = None,
    input_identity: str | None = None,
    missing_values_present: bool = False,
) -> tuple[AlgorithmTool, ...]:
    """按 Registry 静态顺序生成当前启用的领域工具。"""

    adapter_input = None
    if runtime_context is not None and data_profile is not None:
        identity = input_identity or (
            current_input_identity(runtime_context.trusted_identity)
            if runtime_context.trusted_identity is not None
            else "runtime-input"
        )
        adapter_input = AdapterInput(
            data_profile=data_profile,
            input_identity=identity,
            missing_values_present=missing_values_present,
            dataset_authority_available=True,
            semantic_kinds=AlgorithmTool._trusted_semantic_kinds(
                runtime_context.trusted_identity, data_profile,
            ),
        )
    tools: list[AlgorithmTool] = []
    for entry in registry.entries:
        tools.append(
            AlgorithmTool(
                entry=entry,
                adapter=entry.adapter,
                runtime_context=runtime_context,
                adapter_input=adapter_input,
            )
        )
    return tuple(tools)


def build_default_algorithm_tools(
    *,
    runtime_context: "AgentRunContext | None" = None,
    data_profile: DataProfile | None = None,
    adapters: Mapping[str, Any],
    **kwargs: Any,
) -> tuple[AlgorithmTool, ...]:
    """显式传入 Adapter 绑定，防止从远端 MCP list_tools 动态注册。"""

    from .registry import build_default_registry

    registry = build_default_registry(adapters)
    return build_algorithm_tools(
        registry,
        runtime_context=runtime_context,
        data_profile=data_profile,
        **kwargs,
    )
