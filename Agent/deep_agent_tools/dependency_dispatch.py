"""把静态 AlgorithmSpec 调度器接到 LangChain ToolNode 边界。

``ToolNode`` 在 LangChain 1.4 中会为同一条 AIMessage 并发启动每个
``awrap_tool_call``。本模块利用官方 middleware hook 收集同一响应中的算法
calls，先构造 ``DependencyPlan``，再由唯一的 batch task 调用真实 Tool handler。
这样不会复制一套 ToolNode，也不会让模型绕过 ``requires/produces`` 调度。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from typing import Any, Awaitable, Callable

from .algorithm_specs import DEFAULT_ALGORITHM_SPECS, get_algorithm_spec
from .dependency_planner import (
    DependencyPlanningError,
    ToolCallRequest,
    build_dependency_plan,
    execute_dependency_plan,
)
from .runtime_updates import (
    algorithm_terminal_command, defer_algorithm_terminal_events,
    emit_public_decision_event, emit_tool_lifecycle_event, resolve_runtime_invocation,
)
from .interaction import DATA_PROCESSING_TOOL_NAME
from .models import (
    ActionAttempt, AlgorithmResult, AlgorithmResultProvenance, DataProfile,
    Diagnostics, InvocationRecord,
)
from .identity import (
    build_invocation_id,
    build_result_ref,
    current_input_identity,
    provenance_matches_identity,
)
from .registry import AlgorithmRegistry

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command


@dataclass
class _DispatchRun:
    """一个 model response 的一次性批处理和消费状态。"""

    future: asyncio.Future[dict[str, Any]]
    call_ids: frozenset[str]
    served_call_ids: set[str]
    task: asyncio.Task[None] | None = None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _state_data_profile(state: Mapping[str, Any]) -> DataProfile | None:
    value = state.get("data_profile")
    if isinstance(value, DataProfile):
        return value
    if isinstance(value, Mapping):
        return DataProfile.model_validate(value)
    if value is None:
        return None
    raise ValueError("data_profile must be a DataProfile or mapping")


def _initial_artifacts(
    state: Mapping[str, Any], *, trusted_identity: Any = None
) -> frozenset[str]:
    """读取画像与上一轮已发布的算法产物，判断本轮可用 artifact。"""

    profile = _state_data_profile(state)
    if profile is None or profile.row_count < 2 or profile.column_count < 2:
        artifacts: set[str] = set()
    else:
        artifacts = {"tabular_dataset"}
        if (
            not profile.categorical_columns
            and len(profile.numeric_columns) == profile.column_count
            and len(set(profile.column_names)) == profile.column_count
            and set(profile.numeric_columns) == set(profile.column_names)
        ):
            artifacts.add("continuous_tabular_dataset")
    if trusted_identity is not None:
        for raw_result in (state.get("algorithm_results") or {}).values():
            try:
                result = (
                    raw_result
                    if isinstance(raw_result, AlgorithmResult)
                    else AlgorithmResult.model_validate(raw_result)
                )
            except (TypeError, ValueError):
                continue
            if (
                result.status == "valid"
                and result.standardized_graph is not None
                and provenance_matches_identity(result.provenance, trusted_identity)
            ):
                artifacts |= set(
                    get_algorithm_spec(result.capability_id).produces
                )
    return frozenset(artifacts)


def _tool_calls_from_state(
    state: Mapping[str, Any],
    algorithm_tool_names: frozenset[str],
) -> list[dict[str, Any]]:
    messages = state.get("messages") or []
    latest = next(
        (message for message in reversed(messages) if hasattr(message, "tool_calls")),
        None,
    )
    calls = getattr(latest, "tool_calls", None) or []
    result: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        name = call.get("name")
        call_id = call.get("id")
        if name not in algorithm_tool_names or not isinstance(call_id, str) or not call_id:
            continue
        result.append(
            {
                "name": name,
                "id": call_id,
                "type": "tool_call",
                "args": dict(call.get("args") or {})
                if isinstance(call.get("args") or {}, Mapping)
                else {},
            }
        )
    return result


def _safe_tool_message(
    *,
    name: str,
    call_id: str,
    status: str,
    safe_error_code: str,
) -> Any:
    from langchain_core.messages import ToolMessage

    return ToolMessage(
        content=json.dumps(
            {"status": status, "safe_error_code": safe_error_code},
            ensure_ascii=False,
            sort_keys=True,
        ),
        name=name,
        tool_call_id=call_id,
        status="error" if status != "succeeded" else "success",
    )


def _require_processing_message(
    *,
    name: str,
    call_id: str,
    reason: str,
    unmet_requirements: list[str] | None = None,
) -> Any:
    """返回结构化的“需要确认处理”，模型不能把它当作可原样重试的失败。"""

    from langchain_core.messages import ToolMessage

    return ToolMessage(
        content=json.dumps(
            {
                "status": "needs_confirmation",
                "reason": reason,
                "next_action": (
                    "调用 request_data_processing 提交受控选项并等待用户确认，"
                    "不要使用相同参数重复调用算法。"
                ),
                "unmet_requirements": unmet_requirements or ["data_processing"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        name=name,
        tool_call_id=call_id,
        status="error",
    )


def _tool_message_for_processing_result(value: Any) -> Any | None:
    """把 Adapter 的“需要处理/不适用”结果转成结构化确认请求。"""

    if isinstance(value, AlgorithmResult):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        return None
    status = getattr(value.get("status"), "value", value.get("status"))
    if status not in {"invalid_input", "not_applicable", "skipped"}:
        return None
    diagnostics = value.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    code = str(diagnostics.get("safe_error_code") or value.get("safe_error_code") or "")
    from .error_codes import is_processing_error

    if not is_processing_error(code):
        return None
    return {
        "reason": str(diagnostics.get("summary") or value.get("summary") or ""),
        "code": code,
    }


class AlgorithmDependencyDispatchMiddleware(AgentMiddleware):
    """强制算法 Tool 经过 ``DependencyPlan`` 后才进入真实 handler。"""

    def __init__(
        self,
        *,
        registry: AlgorithmRegistry,
        max_parallel_tools_per_job: int = 2,
        timeout_seconds: float = 720.0,
    ) -> None:
        if max_parallel_tools_per_job <= 0 or timeout_seconds <= 0:
            raise ValueError("dependency dispatch limits must be positive")
        self.registry = registry
        self.max_parallel_tools_per_job = max_parallel_tools_per_job
        self.timeout_seconds = timeout_seconds
        self._tool_names = frozenset(
            entry.spec.tool_name for entry in registry.entries
        )
        self._runs: dict[tuple[str, str], _DispatchRun] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "AlgorithmDependencyDispatchMiddleware"

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """把已登记的调用推进到 running，先提交状态再进入 ToolNode。"""

        await runtime.context.ensure_active()
        records = {}
        for call in _tool_calls_from_state(state, self._tool_names):
            invocation_id = build_invocation_id(
                job_id=runtime.context.trusted_identity.job_id,
                response_identity=state.get("provider_response_id") or state["message_execution_id"],
                provider_call_id=call["id"],
            )
            record = InvocationRecord.model_validate(state["action_ledger"][invocation_id])
            if record.final_status != "pending":
                continue
            records[invocation_id] = record.model_copy(update={"attempts": {
                0: ActionAttempt(retry_ordinal=0, revision=1, status="running",
                                 started_at=datetime.now(timezone.utc)),
            }})
        return {"action_ledger": records} if records else None

    @staticmethod
    def _run_key(request: Any, state: Mapping[str, Any]) -> tuple[str, str]:
        runtime_context = getattr(request.runtime, "context", None)
        identity = getattr(runtime_context, "trusted_identity", None)
        job_id = str(getattr(identity, "job_id", "untracked"))
        response_identity = str(
            state.get("provider_response_id")
            or state.get("message_execution_id")
            or ""
        ).strip()
        if not response_identity:
            raise RuntimeError(
                "provider_response_id or message_execution_id is required before dispatch"
            )
        return job_id, response_identity

    @staticmethod
    def _tool_map(runtime: Any) -> dict[str, Any]:
        return {
            str(tool.name): tool
            for tool in (getattr(runtime, "tools", None) or [])
            if getattr(tool, "name", None)
        }

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        call = request.tool_call
        if (
            isinstance(call, Mapping)
            and call.get("name") == DATA_PROCESSING_TOOL_NAME
        ):
            # 处理确认必须独占工具轮次；不能与算法调用一起进入调度计划。
            calls = _tool_calls_from_state(
                _as_mapping(request.state),
                self._tool_names,
            )
            if calls:
                return _safe_tool_message(
                    name=DATA_PROCESSING_TOOL_NAME,
                    call_id=str(call.get("id") or ""),
                    status="rejected",
                    safe_error_code="INTERACTION_MUST_BE_EXCLUSIVE",
                )
            return await handler(request)
        if not isinstance(call, Mapping) or call.get("name") not in self._tool_names:
            return await handler(request)

        state = _as_mapping(request.state)
        key = self._run_key(request, state)
        current_call_id = str(call.get("id") or "")
        if not current_call_id:
            raise RuntimeError("algorithm tool call id is required before dispatch")

        async with self._lock:
            dispatch = self._runs.get(key)
            first = dispatch is None
            if dispatch is None:
                calls = _tool_calls_from_state(state, self._tool_names)
                call_ids = frozenset(str(item["id"]) for item in calls)
                if current_call_id not in call_ids:
                    calls.append(dict(call))
                    call_ids = frozenset(str(item["id"]) for item in calls)
                dispatch = _DispatchRun(
                    future=asyncio.get_running_loop().create_future(),
                    call_ids=call_ids,
                    served_call_ids=set(),
                )
                self._runs[key] = dispatch

        if first:
            dispatch.task = asyncio.create_task(
                self._execute_batch(
                    key=key,
                    dispatch=dispatch,
                    request=request,
                    handler=handler,
                    state=state,
                )
            )

        try:
            # 一个 ToolNode wrapper 被取消时不能连带取消同一 response 的共享
            # dispatch future；batch task 仍需完成 sibling 的取消/等待收口。
            outputs = await asyncio.shield(dispatch.future)
            return outputs[current_call_id]
        except asyncio.CancelledError:
            if dispatch.task is not None and not dispatch.task.done():
                dispatch.task.cancel()
                await asyncio.gather(dispatch.task, return_exceptions=True)
            # 取消整批后消费共享 future 的控制流异常，避免无人读取的异常日志。
            if dispatch.future.done() and not dispatch.future.cancelled():
                dispatch.future.exception()
            raise
        finally:
            async with self._lock:
                dispatch.served_call_ids.add(current_call_id)
                if (
                    dispatch.future.done()
                    and dispatch.call_ids.issubset(dispatch.served_call_ids)
                ):
                    self._runs.pop(key, None)

    async def _execute_batch(
        self,
        *,
        key: tuple[str, str],
        dispatch: _DispatchRun,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
        state: Mapping[str, Any],
    ) -> None:
        from langchain.agents.middleware import ToolCallRequest as LangToolCallRequest

        calls = _tool_calls_from_state(state, self._tool_names)
        if not calls:
            calls = [dict(request.tool_call)]
        planner_requests = tuple(
            ToolCallRequest(
                call_id=str(call["id"]),
                tool_name=str(call["name"]),
                arguments={
                    key: value
                    for key, value in (call.get("args") or {}).items()
                    if key != "public_decision"
                },
            )
            for call in calls
        )
        call_by_id = {str(call["id"]): call for call in calls}
        tool_by_name = self._tool_map(request.runtime)

        async def terminal_output(
            call_id: str, status: str, code: Any, *, summary: str | None = None,
        ) -> Any:
            """为未返回 Command 的普通终态补齐真实诊断结果与账本。"""

            call = call_by_id[call_id]
            tool_runtime = replace(request.runtime, tool_call_id=call_id)
            identity = resolve_runtime_invocation(tool_runtime)
            trusted = identity.runtime_context.trusted_identity
            await identity.runtime_context.ensure_active()
            spec = self.registry.get_by_tool_name(call["name"]).spec
            invocation_id = build_invocation_id(
                job_id=trusted.job_id, response_identity=identity.response_identity,
                provider_call_id=call_id,
            )
            result = AlgorithmResult(
                result_ref=build_result_ref(invocation_id=invocation_id, result_index=0),
                invocation_id=invocation_id, provider_call_id=call_id,
                capability_id=spec.capability_id, capability_version=spec.version,
                status=status if status in {"timed_out", "not_ready", "not_applicable", "skipped"} else "execution_failed",
                method=spec.method_definition(),
                diagnostics=Diagnostics(
                    safe_error_code=code,
                    summary=summary or (
                        "工具输入尚未就绪，未执行算法。" if status == "not_ready" else None
                    ),
                ),
                provenance=AlgorithmResultProvenance(
                    job_id=str(trusted.job_id), attempt_count=int(trusted.attempt_count),
                    lease_epoch=int(trusted.lease_epoch),
                    input_identity=current_input_identity(trusted),
                    spec_digest=spec.spec_digest, invocation_id=invocation_id,
                    capability_id=spec.capability_id, capability_version=spec.version,
                ),
            )
            record = (state.get("action_ledger") or {}).get(invocation_id)
            started_at = datetime.now(timezone.utc)
            if record is not None:
                record = InvocationRecord.model_validate(record)
                started_at = record.attempts[0].started_at or started_at
            return await algorithm_terminal_command(
                tool_runtime, identity=identity, tool_name=call["name"],
                result=result, started_at=started_at,
            )

        async def execute_one(planned: ToolCallRequest) -> Any:
            tool = tool_by_name.get(planned.tool_name)
            if tool is None:
                raise RuntimeError("registered algorithm tool is unavailable")
            source_call = call_by_id[planned.call_id]
            raw_arguments = dict(source_call.get("args") or {})
            public_decision = raw_arguments.pop("public_decision", None)
            raw_call = dict(source_call)
            raw_call["args"] = raw_arguments
            tool_runtime = replace(request.runtime, tool_call_id=planned.call_id)
            if public_decision is not None:
                identity = resolve_runtime_invocation(tool_runtime)
                await emit_public_decision_event(
                    tool_runtime,
                    identity=identity,
                    tool_name=planned.tool_name,
                    public_decision=public_decision,
                )
            tool_request = LangToolCallRequest(
                tool_call=raw_call,
                tool=tool,
                state=request.state,
                runtime=tool_runtime,
            )
            with defer_algorithm_terminal_events():
                return await handler(tool_request)

        try:
            plan = build_dependency_plan(
                planner_requests,
                registry=self.registry,
                initial_artifacts=_initial_artifacts(
                    state,
                    trusted_identity=resolve_runtime_invocation(
                        request.runtime
                    ).runtime_context.trusted_identity,
                ),
            )
            outcomes = await execute_dependency_plan(
                plan,
                execute_one,
                max_parallel_tools_per_job=self.max_parallel_tools_per_job,
                timeout_seconds=self.timeout_seconds,
            )
            outputs: dict[str, Any] = {}
            for outcome in outcomes:
                call_id = outcome.request.call_id
                if isinstance(outcome.value, Command):
                    # 失败 Command 也包含不可丢弃的结果与终态 State 更新。
                    update = outcome.value.update
                    results = update.get("algorithm_results", {}) if isinstance(update, Mapping) else {}
                    try:
                        if not isinstance(results, Mapping) or len(results) != 1:
                            raise ValueError("algorithm Command must contain one result")
                        result = AlgorithmResult.model_validate(next(iter(results.values())))
                        spec = self.registry.get_by_tool_name(outcome.request.tool_name).spec
                        if result.provider_call_id != call_id or result.capability_id != spec.capability_id:
                            raise ValueError("algorithm Command identity mismatch")
                    except (TypeError, ValueError):
                        outputs[call_id] = await terminal_output(
                            call_id, "failed", "ALGORITHM_RESULT_CONTRACT_INVALID",
                        )
                        continue
                    tool_runtime = replace(request.runtime, tool_call_id=call_id)
                    identity = resolve_runtime_invocation(tool_runtime)
                    await identity.runtime_context.ensure_active()
                    # 此处已离开 execute_dependency_plan 的 deadline；持久化等待
                    # 不能把已完成的算法改判为超时，也不能先发布与调度结果冲突的状态。
                    await emit_tool_lifecycle_event(
                        tool_runtime, event_type="tool_call_result", identity=identity,
                        tool_name=outcome.request.tool_name, status=result.status,
                        safe_error_code=getattr(result.diagnostics.safe_error_code, "value", result.diagnostics.safe_error_code),
                    )
                    processing = next((
                        details for result in results.values()
                        if (details := _tool_message_for_processing_result(result)) is not None
                    ), None)
                    if processing is not None:
                        update["messages"] = [_require_processing_message(
                            name=outcome.request.tool_name, call_id=call_id,
                            reason=processing["reason"], unmet_requirements=[processing["code"]],
                        )]
                    outputs[call_id] = outcome.value
                    continue
                if isinstance(outcome.value, ToolMessage):
                    # 参数/审批拒绝同样结束已登记调用，但保留工具给模型的处理指令。
                    try:
                        payload = json.loads(outcome.value.content)
                    except (TypeError, ValueError):
                        payload = {}
                    needs_confirmation = isinstance(payload, Mapping) and payload.get("status") == "needs_confirmation"
                    command = await terminal_output(
                        call_id, "not_ready" if needs_confirmation else "failed",
                        "PREPROCESSING_APPROVAL_REQUIRED" if needs_confirmation else "ALGORITHM_RESULT_CONTRACT_INVALID",
                    )
                    command.update["messages"] = [outcome.value]
                    outputs[call_id] = command
                    continue
                processing = _tool_message_for_processing_result(outcome.value)
                if processing is not None:
                    command = await terminal_output(call_id, "not_ready", processing["code"])
                    command.update["messages"] = [_require_processing_message(
                        name=outcome.request.tool_name,
                        call_id=call_id,
                        reason=processing["reason"],
                        unmet_requirements=[processing["code"]],
                    )]
                    outputs[call_id] = command
                    continue
                missing = plan.missing_artifacts_by_call.get(call_id, frozenset())
                summary = None
                if outcome.status == "not_ready":
                    if "continuous_tabular_dataset" in missing:
                        summary = "当前画像未确认全部列为数值类型，未执行算法。"
                    elif "tabular_dataset" in missing:
                        summary = "当前画像未确认所需表格输入，未执行算法。"
                    elif missing:
                        summary = "所需输入尚未就绪，未执行算法。"
                    else:
                        summary = "上游调用未成功发布所需输入，未执行算法。"
                outputs[call_id] = await terminal_output(
                    call_id, outcome.status,
                    outcome.safe_error_code or "ALGORITHM_EXECUTION_FAILED",
                    summary=summary,
                )
            if not outputs:
                raise RuntimeError("dependency planner returned no tool outputs")
            if not dispatch.future.done():
                dispatch.future.set_result(outputs)
        except DependencyPlanningError as exc:
            code = str(getattr(exc.safe_error_code, "value", exc.safe_error_code))
            try:
                outputs = {}
                for call in calls:
                    outputs[str(call["id"])] = await terminal_output(
                        str(call["id"]), "failed", code,
                        summary="工具调用计划不符合执行契约，未执行算法。",
                    )
                if not dispatch.future.done():
                    dispatch.future.set_result(outputs)
            except BaseException as terminal_error:
                if not dispatch.future.done():
                    dispatch.future.set_exception(terminal_error)
        except BaseException as exc:
            if not dispatch.future.done():
                dispatch.future.set_exception(exc)
        finally:
            # 调用方可能已经在 future 完成前被取消，因此不能只依赖调用方
            # finally 清理；全部 wrapper 都消费/取消后即可释放本轮状态。
            async with self._lock:
                if (
                    dispatch.future.done()
                    and dispatch.call_ids.issubset(dispatch.served_call_ids)
                ):
                    self._runs.pop(key, None)


def build_algorithm_dependency_middleware(
    *,
    registry: AlgorithmRegistry,
    max_parallel_tools_per_job: int = 2,
    timeout_seconds: float = 720.0,
) -> AlgorithmDependencyDispatchMiddleware:
    """构造启动期静态校验过的算法 dispatch middleware。"""

    registered = {entry.spec.capability_id for entry in registry.entries}
    expected = {spec.capability_id for spec in DEFAULT_ALGORITHM_SPECS}
    if registered != expected:
        raise ValueError("algorithm dispatch registry does not match frozen capabilities")
    return AlgorithmDependencyDispatchMiddleware(
        registry=registry,
        max_parallel_tools_per_job=max_parallel_tools_per_job,
        timeout_seconds=timeout_seconds,
    )
