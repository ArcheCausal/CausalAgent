from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import NodeError
from langgraph.runtime import Runtime

from app.agent.worker.execution_guard import (
    JobExecutionRevoked,
    current_execution_guard,
)
from observability.logging_runtime import log_context, log_event


LOGGER = logging.getLogger(__name__)
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


def _runtime_guard(runtime: Runtime):
    """读取 LangGraph runtime context；测试/旧直接调用退回当前异步上下文。"""
    context = getattr(runtime, "context", None)
    # 新调用链传入 AgentRunContext，旧的直接图调用和测试则可能把
    # JobExecutionGuard 本身作为 context；两种形式都必须保留取消/失租检查。
    if hasattr(context, "ensure_active"):
        return context
    guard = getattr(context, "execution_guard", None)
    return guard if hasattr(guard, "ensure_active") else current_execution_guard()


def _tool_name_from_state(state) -> str | None:
    """只从已规范化的第一个 ToolCall 提取稳定工具名。"""
    if not isinstance(state, Mapping):
        return None
    messages = state.get("messages") or []
    latest = messages[-1] if messages else None
    calls = getattr(latest, "tool_calls", None) or []
    first = calls[0] if calls else None
    if not isinstance(first, Mapping):
        return None
    name = first.get("name")
    return name if isinstance(name, str) and _TOOL_NAME_PATTERN.fullmatch(name) else None


def _node_log_context(state, node_name: str):
    tool = _tool_name_from_state(state) if node_name.endswith("tool_node") else None
    return log_context(node=node_name, tool=tool)


def _final_attempt(runtime: Runtime, error: NodeError) -> int:
    execution_info = getattr(runtime, "execution_info", None)
    candidates = (
        getattr(execution_info, "node_attempt", None),
        getattr(error, "attempt", None),
    )
    for value in candidates:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return 1


def _cause_code(error: NodeError) -> str | None:
    """读取底层异常的稳定原因代码；没有时返回 None，日志层会跳过该键。"""
    underlying = _underlying_error(error)
    if underlying is None:
        return None
    value = getattr(underlying, "safe_cause_code", None)
    return value if isinstance(value, str) and value else None


def _underlying_error(error: NodeError) -> BaseException | None:
    candidate = getattr(error, "error", error)
    return candidate if isinstance(candidate, BaseException) else None


def _is_timeout_error(error: NodeError) -> bool:
    underlying = _underlying_error(error)
    name = type(underlying).__name__ if underlying is not None else type(error).__name__
    return "timeout" in name.lower()


def _failure_kind(error: NodeError, node_name: str) -> str:
    underlying = _underlying_error(error)
    name = type(underlying).__name__.lower() if underlying is not None else ""
    if any(token in name for token in ("connection", "network", "transport", "http")):
        return "dependency_error"
    if node_name.endswith("tool_node"):
        return "tool_error"
    return "node_error"


def _safe_exc_info(error: NodeError):
    underlying = _underlying_error(error)
    if underlying is None:
        return None
    return type(underlying), underlying, underlying.__traceback__


def _structured_output_details(error: NodeError) -> dict[str, Any]:
    """读取结构化输出失败的受控诊断字段；缺失时返回空字典。

    只记录 schema 名称、统一入口的尝试次数，以及校验错误的字段路径与错误码，
    不记录模型取值、提示词或异常正文。
    """
    underlying = _underlying_error(error)
    details: dict[str, Any] = {}
    if underlying is None:
        return details
    schema_name = getattr(underlying, "schema_name", None)
    if isinstance(schema_name, str) and schema_name:
        details["schema_name"] = schema_name
    attempts = getattr(underlying, "structured_attempts", None)
    if isinstance(attempts, int) and attempts > 0:
        details["structured_attempts"] = attempts
    summary = getattr(underlying, "safe_validation", None)
    if not isinstance(summary, Mapping):
        return details
    count = summary.get("count")
    if isinstance(count, int) and count >= 0:
        details["validation_error_count"] = count
    first_type = summary.get("first_type")
    if isinstance(first_type, str) and first_type:
        details["validation_first_type"] = first_type
    first_loc = summary.get("first_loc")
    if isinstance(first_loc, str) and first_loc:
        details["validation_first_loc"] = first_loc
    return details


def _accepts_keyword_argument(func, name: str) -> bool:
    """判断节点函数是否显式声明了某个可传入的关键字参数。"""
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    parameter = parameters.get(name)
    if parameter is None:
        return False
    return parameter.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def bind_node(func, *, event_node_name: str | None = None, **bound_kwargs):
    """绑定节点依赖，并通过 custom 流暴露真实的节点执行尝试。"""
    node_name = event_node_name or getattr(func, "__name__", "bound_node").removesuffix("_node")
    # 只有显式声明 runtime 参数的节点才拿到 invocation 运行上下文；
    # 其余节点保持原有的 (state, **bound_kwargs) 调用契约。
    wants_runtime = _accepts_keyword_argument(func, "runtime")

    async def _node(state, runtime: Runtime):
        """执行函数节点，并把 attempt 边界写入 custom 流。"""
        with _node_log_context(state, node_name):
            guard = _runtime_guard(runtime)
            if guard is not None:
                await guard.ensure_active()
            execution_info = runtime.execution_info
            task_id = execution_info.task_id if execution_info else None
            node_attempt = execution_info.node_attempt if execution_info else 0
            runtime.stream_writer({
                "type": "node_attempt_start",
                "node_name": node_name,
                "task_id": task_id,
                "node_attempt": node_attempt,
            })
            try:
                kwargs = dict(bound_kwargs)
                if wants_runtime:
                    kwargs["runtime"] = runtime
                result = await func(state, **kwargs)
                if guard is not None:
                    await guard.check_after_call()
                return result
            except JobExecutionRevoked:
                raise
            except Exception as exc:
                if guard is not None:
                    await guard.check_after_call()
                runtime.stream_writer({
                    "type": "node_attempt_failed",
                    "node_name": node_name,
                    "task_id": task_id,
                    "node_attempt": node_attempt,
                    "error_kind": type(exc).__name__,
                })
                raise

    _node.__name__ = getattr(func, "__name__", "bound_node")
    return _node


def bind_runnable_node(runnable, *, event_node_name: str):
    """包装 ToolNode 等 Runnable，同时保留其执行 config 和 retry attempt 事件。"""
    async def _node(
        state,
        runtime: Runtime,
        config: RunnableConfig,
    ):
        """执行 Runnable 节点，并保留 LangGraph 传入的运行配置。"""
        with _node_log_context(state, event_node_name):
            guard = _runtime_guard(runtime)
            if guard is not None:
                await guard.ensure_active()
            execution_info = runtime.execution_info
            task_id = execution_info.task_id if execution_info else None
            node_attempt = execution_info.node_attempt if execution_info else 0
            runtime.stream_writer({
                "type": "node_attempt_start",
                "node_name": event_node_name,
                "task_id": task_id,
                "node_attempt": node_attempt,
            })
            try:
                result = await runnable.ainvoke(state, config)
                if guard is not None:
                    await guard.check_after_call()
                return result
            except JobExecutionRevoked:
                raise
            except Exception as exc:
                if guard is not None:
                    await guard.check_after_call()
                runtime.stream_writer({
                    "type": "node_attempt_failed",
                    "node_name": event_node_name,
                    "task_id": task_id,
                    "node_attempt": node_attempt,
                    "error_kind": type(exc).__name__,
                })
                raise

    _node.__name__ = event_node_name
    return _node


def bind_subgraph_node(func, *, event_node_name: str, **bound_kwargs):
    """绑定需要显式透传 ``config`` 和 runtime context 的子图适配节点。"""

    async def _node(
        state,
        runtime: Runtime,
        config: RunnableConfig,
    ):
        """执行父子 State 映射，并保留父图的 Guard 与 attempt 边界。"""
        with _node_log_context(state, event_node_name):
            guard = _runtime_guard(runtime)
            if guard is not None:
                await guard.ensure_active()
            execution_info = runtime.execution_info
            task_id = execution_info.task_id if execution_info else None
            node_attempt = execution_info.node_attempt if execution_info else 0
            runtime.stream_writer({
                "type": "node_attempt_start",
                "node_name": event_node_name,
                "task_id": task_id,
                "node_attempt": node_attempt,
            })
            try:
                result = await func(
                    state,
                    runtime=runtime,
                    config=config,
                    **bound_kwargs,
                )
                if guard is not None:
                    await guard.check_after_call()
                return result
            except JobExecutionRevoked:
                raise
            except Exception as exc:
                if guard is not None:
                    await guard.check_after_call()
                runtime.stream_writer({
                    "type": "node_attempt_failed",
                    "node_name": event_node_name,
                    "task_id": task_id,
                    "node_attempt": node_attempt,
                    "error_kind": type(exc).__name__,
                })
                raise

    _node.__name__ = getattr(func, "__name__", event_node_name)
    return _node


def guarded_router(func):
    """在 conditional edge 读取 State 前后检查 invocation 执行资格。"""

    async def _router(state, runtime: Runtime):
        guard = _runtime_guard(runtime)
        if guard is not None:
            await guard.ensure_active()
        result = func(state)
        if inspect.isawaitable(result):
            result = await result
        if guard is not None:
            await guard.check_after_call()
        return result

    _router.__name__ = getattr(func, "__name__", "guarded_router")
    return _router


def guarded_context_router(func):
    """在 conditional edge 检查执行资格，并把 runtime.context 一并传给路由函数。"""

    async def _router(state, runtime: Runtime):
        guard = _runtime_guard(runtime)
        if guard is not None:
            await guard.ensure_active()
        context = getattr(runtime, "context", None)
        result = func(state, context)
        if inspect.isawaitable(result):
            result = await result
        if guard is not None:
            await guard.check_after_call()
        return result

    _router.__name__ = getattr(func, "__name__", "guarded_context_router")
    return _router


def guarded_error_handler(
    func,
    *,
    event_node_name: str | None = None,
    timeout_ms: int = 0,
    fallback: str | None = None,
    mcp_transport: bool = False,
):
    """阻断撤销后的 fallback State/Command 生成，并保留 LangGraph 参数注入。"""

    async def _handler(
        state,
        error: NodeError,
        runtime: Runtime,
    ):
        node_name = event_node_name or getattr(func, "__name__", "guarded_error_handler")
        with _node_log_context(state, node_name):
            guard = _runtime_guard(runtime)
            if guard is not None:
                await guard.ensure_active()
            try:
                result = func(state, error)
                if inspect.isawaitable(result):
                    result = await result
                if guard is not None:
                    await guard.check_after_call()

                final_attempt = _final_attempt(runtime, error)
                fallback_name = fallback or getattr(func, "__name__", "fallback")
                if mcp_transport:
                    log_event(
                        LOGGER,
                        "mcp.transport.failed",
                        details={
                            "reason_code": "transport_error",
                            "final_attempt": final_attempt,
                            "duration_ms": (
                                max(0, int(timeout_ms))
                                if _is_timeout_error(error)
                                else 0
                            ),
                        },
                        exc_info=_safe_exc_info(error),
                    )
                elif _is_timeout_error(error):
                    log_event(
                        LOGGER,
                        "job.node.timeout",
                        details={
                            "final_attempt": final_attempt,
                            "timeout_ms": max(0, int(timeout_ms)),
                            "fallback": fallback_name,
                        },
                        exc_info=_safe_exc_info(error),
                    )
                else:
                    details = {
                        "failure_kind": _failure_kind(error, node_name),
                        "final_attempt": final_attempt,
                        "fallback": fallback_name,
                        "cause_code": _cause_code(error),
                    }
                    details.update(_structured_output_details(error))
                    log_event(
                        LOGGER,
                        "job.node.degraded",
                        details=details,
                        exc_info=_safe_exc_info(error),
                    )
                return result
            except JobExecutionRevoked:
                raise
            except Exception:
                if guard is not None:
                    await guard.check_after_call()
                raise

    _handler.__name__ = getattr(func, "__name__", "guarded_error_handler")
    return _handler
