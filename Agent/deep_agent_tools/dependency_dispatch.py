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
import json
from typing import Any, Awaitable, Callable

from .algorithm_specs import DEFAULT_ALGORITHM_SPECS
from .dependency_planner import (
    DependencyPlanningError,
    ToolCallRequest,
    build_dependency_plan,
    execute_dependency_plan,
)
from .runtime_updates import emit_public_decision_event, resolve_runtime_invocation
from .models import DataProfile
from .registry import AlgorithmRegistry

try:  # pragma: no cover - 仅在协议测试无 LangChain 依赖时使用
    from langchain.agents.middleware import AgentMiddleware
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    class AgentMiddleware:  # type: ignore[no-redef]
        """让协议层在缺少 LangChain 时仍可被导入；真实构造会 fail closed。"""

        pass


@dataclass
class _DispatchRun:
    """一个 model response 的一次性批处理和消费状态。"""

    future: asyncio.Future[dict[str, Any]]
    call_ids: frozenset[str]
    served_call_ids: set[str]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _state_data_profile(state: Mapping[str, Any]) -> DataProfile | None:
    value = state.get("data_profile")
    if isinstance(value, DataProfile):
        return value
    if isinstance(value, Mapping):
        try:
            return DataProfile.model_validate(value)
        except Exception:
            return None
    summary = state.get("file_summary")
    if not isinstance(summary, Mapping):
        return None
    columns = tuple(str(column) for column in summary.get("columns", ()) if str(column))
    try:
        return DataProfile(
            row_count=max(0, int(summary.get("rows") or 0)),
            column_count=len(columns),
            column_names=columns,
        )
    except (TypeError, ValueError):
        return None


def _initial_artifacts(state: Mapping[str, Any]) -> frozenset[str]:
    """从外层 admission 的画像推导已存在的数据 artifact。"""

    profile = _state_data_profile(state)
    if profile is None or profile.row_count < 2 or profile.column_count < 2:
        return frozenset()
    artifacts = {"tabular_dataset"}
    if (
        not profile.categorical_columns
        and len(profile.numeric_columns) == profile.column_count
    ):
        artifacts.add("continuous_tabular_dataset")
    return frozenset(artifacts)


def _tool_calls_from_state(
    state: Mapping[str, Any],
    algorithm_tool_names: frozenset[str],
) -> list[dict[str, Any]]:
    messages = state.get("messages") or []
    latest = next(
        (message for message in reversed(messages) if getattr(message, "tool_calls", None)),
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
            asyncio.create_task(
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
            return await handler(tool_request)

        try:
            plan = build_dependency_plan(
                planner_requests,
                registry=self.registry,
                initial_artifacts=_initial_artifacts(state),
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
                if outcome.value is not None and outcome.status == "succeeded":
                    outputs[call_id] = outcome.value
                    continue
                outputs[call_id] = _safe_tool_message(
                    name=outcome.request.tool_name,
                    call_id=call_id,
                    status=outcome.status,
                    safe_error_code=str(
                        getattr(
                            outcome.safe_error_code,
                            "value",
                            outcome.safe_error_code or "ALGORITHM_EXECUTION_FAILED",
                        )
                    ),
                )
            if not outputs:
                raise RuntimeError("dependency planner returned no tool outputs")
            if not dispatch.future.done():
                dispatch.future.set_result(outputs)
        except DependencyPlanningError as exc:
            code = str(getattr(exc.safe_error_code, "value", exc.safe_error_code))
            outputs = {
                str(call["id"]): _safe_tool_message(
                    name=str(call["name"]),
                    call_id=str(call["id"]),
                    status="not_ready",
                    safe_error_code=code,
                )
                for call in calls
            }
            if not dispatch.future.done():
                dispatch.future.set_result(outputs)
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
