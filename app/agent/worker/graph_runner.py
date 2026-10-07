"""执行单个 LangGraph graph 并生成稳定的内部流事件。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.errors import NodeCancelledError
from langgraph.types import Command

from app.agent.checkpoint_recovery import checkpoint_identity
from app.chat.services import get_job_chat_history
from app.agent.worker.event_adapter import (
    LangGraphEventAdapter,
    classify_graph_failure,
    sanitize_public_error,
)
from app.agent.worker.execution_guard import JobExecutionGuard, JobExecutionRevoked
from app.agent.worker.result_presenter import process_final_result
from config.settings import settings
from Agent.causal_agent.analysis_context import build_context_commit, build_context_index
from Agent.causal_agent.context import AgentRunContext
from Database.analysis_contexts import load_active_context, load_context_index


# LangGraph 的节点错误处理器在 updates 流里使用这个前缀提交降级结果。
ERROR_HANDLER_STREAM_PREFIX = "__error_handler__"


def _build_final_result(state_values: dict[str, Any], *, job_attempt: int) -> dict[str, Any]:
    """构造稳定终态事件；上下文写回事实只作为内部字段交给 event writer。"""
    return {
        "type": "final_result",
        "data": process_final_result(state_values),
        "attempt": job_attempt,
        "_context_commit": build_context_commit(state_values),
    }


async def _recovered_after_node_error(
    graph: Any,
    config: dict[str, Any],
    *,
    handler_recovered: bool,
    job_attempt: int,
) -> dict[str, Any] | None:
    """节点错误处理器已提交降级结果时，把这一轮按正常终态收尾。

    LangGraph 的 astream 在节点错误处理器成功提交降级结果之后，仍会把原任务异常抛给
    调用方。此时图状态已经收敛（没有待执行节点、没有 pending interrupt），继续把降级
    结果当作本轮终态，比让整个 Job 失败更符合节点级降级的约定。读不到或无法确认收敛
    时返回 None，调用方保持原有的失败路径。
    """

    if not handler_recovered:
        return None
    try:
        state = await graph.aget_state(config)
    except Exception:
        return None
    if getattr(state, "next", None):
        return None
    if _snapshot_interrupts(state):
        return None
    values = getattr(state, "values", None)
    if not isinstance(values, dict):
        return None
    return _build_final_result(values, job_attempt=job_attempt)


def _snapshot_interrupts(snapshot: Any) -> list[Any]:
    """读取 StateSnapshot 中尚待恢复的 interrupt，并兼容旧 task 结构。"""
    public_interrupts = getattr(snapshot, "interrupts", None)
    if public_interrupts:
        return list(public_interrupts)
    return [
        item
        for task in (getattr(snapshot, "tasks", None) or ())
        for item in (getattr(task, "interrupts", None) or ())
    ]


def _raise_wrapped_cancellation(error: BaseException) -> None:
    """Restore worker control-flow semantics after LangGraph wraps node errors."""

    cause = getattr(error, "error", None) or error.__cause__
    if isinstance(error, NodeCancelledError) and isinstance(
        cause, (asyncio.CancelledError, JobExecutionRevoked)
    ):
        raise cause


def _interrupt_id(interrupt_obj: Any) -> str:
    """读取 LangGraph interrupt 的稳定 ID，不把完整对象写入公开事件。"""
    if isinstance(interrupt_obj, dict):
        value = interrupt_obj.get("id") or interrupt_obj.get("ns")
    else:
        value = getattr(interrupt_obj, "id", None) or getattr(interrupt_obj, "ns", None)
    if isinstance(value, (list, tuple)):
        value = ":".join(str(item) for item in value)
    return str(value or "interrupt-unknown")[:255]


def _graph_config(
    *,
    user_id: int,
    session_id: str,
    job_id: str,
) -> dict[str, Any]:
    """构造 Job 根图配置；业务 session 只作为 State/metadata 上下文。"""
    thread_id, _ = checkpoint_identity(job_id)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "user_id": user_id,
    }
    return {
        "configurable": configurable,
        "metadata": {
            "job_id": thread_id,
            "session_id": str(session_id),
        },
    }


def _snapshot_has_checkpoint(snapshot: Any) -> bool:
    """判断 StateSnapshot 是否来自真实 checkpoint，而不是空查询结果。"""
    if snapshot is None:
        return False
    if getattr(snapshot, "created_at", None) is not None:
        return True
    metadata = getattr(snapshot, "metadata", None)
    if isinstance(metadata, dict) and metadata:
        return True
    config = getattr(snapshot, "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable") or {}
        if configurable.get("checkpoint_id"):
            return True
    if getattr(snapshot, "next", None):
        return True
    if getattr(snapshot, "interrupts", None):
        return True
    if getattr(snapshot, "tasks", None):
        return True
    values = getattr(snapshot, "values", None)
    return bool(
        isinstance(values, dict)
        and any(value not in (None, "", [], {}, ()) for value in values.values())
    )


def _legacy_input_record(value: Any) -> dict[str, Any]:
    """兼容直接调用 graph runner 的旧测试，真实 worker 使用输入账本记录。"""
    return {
        "input_type": "initial",
        "runtime_value": value,
        "stored_text": value if isinstance(value, str) else str(value),
        "chat_message_id": None,
    }


async def _initial_graph_input(
    input_record: dict[str, Any],
    *,
    user_id: int,
    username: str,
    session_id: str,
    job_id: str,
    input_user_file_id: int | None,
    input_object_id: int | None,
    input_file_hash: str | None,
    input_filename: str | None,
) -> dict[str, Any]:
    """从 Job 创建时冻结的 MySQL 消息边界构造新 Job State。"""
    chat_message_id = input_record.get("chat_message_id")
    if chat_message_id is None:
        # 仅保留旧的直接函数调用兼容路径；worker 的账本记录必须有聊天关联。
        history = [HumanMessage(content=str(input_record.get("runtime_value", "")))]
    else:
        history = await asyncio.to_thread(
            get_job_chat_history,
            session_id,
            user_id,
            int(chat_message_id),
            settings.JOB_CHAT_HISTORY_LIMIT,
        )
        if not history:
            raise RuntimeError("Job 初始聊天历史为空")
    # 分析上下文是跨 Job 的业务事实来源，只在 Job 首次构造 State 时读取；
    # 同一 Job 的 resume 与 stale recovery 继续使用 checkpoint 中的投影。
    active_context = await asyncio.to_thread(load_active_context, user_id, session_id)
    index_rows = await asyncio.to_thread(
        load_context_index,
        user_id,
        session_id,
        exclude_context_id=(active_context or {}).get("analysis_context_id"),
    )
    return {
        "messages": history,
        "user_id": user_id,
        "username": username,
        "session_id": session_id,
        "job_id": job_id,
        "file_summary": {
            "user_file_id": input_user_file_id,
            "object_id": input_object_id,
            "file_hash": input_file_hash,
            "filename": input_filename,
        },
        "analysis_context": active_context or {},
        "analysis_context_index": build_context_index(index_rows),
    }


async def ai_call_stream(
    text: Any,
    user_id: int,
    username: str,
    session_id: str,
    *,
    job_id: str,
    job_attempt: int,
    input_user_file_id: int | None,
    input_object_id: int | None,
    input_file_hash: str | None,
    input_filename: str | None,
    graph: Any,
    claim_kind: str = "initial",
    input_record: dict[str, Any] | None = None,
    initial_input_record: dict[str, Any] | None = None,
    execution_guard: JobExecutionGuard | None = None,
    web_search_enabled: bool = False,
    agent_runtime_context: AgentRunContext | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """在显式传入的 graph 上执行一次调用并产出公开事件。"""
    if graph is None:
        raise RuntimeError("Agent Graph 尚未初始化")
    if execution_guard is not None:
        await execution_guard.ensure_active()

    current_input = input_record or (
        text if isinstance(text, dict) and "input_type" in text else _legacy_input_record(text)
    )
    initial_input = initial_input_record or current_input
    input_type = current_input.get("input_type")
    runtime_value = current_input.get("runtime_value")
    if input_type not in {"initial", "resume"}:
        raise RuntimeError("Job 输入类型无效")
    config = _graph_config(
        user_id=user_id,
        session_id=session_id,
        job_id=job_id,
    )

    state = await graph.aget_state(config)
    if execution_guard is not None:
        await execution_guard.check_after_call()
    interrupts = _snapshot_interrupts(state)
    has_checkpoint = _snapshot_has_checkpoint(state)
    if interrupts:
        if input_type != "resume":
            raise RuntimeError("checkpoint 存在 pending interrupt，但当前输入不是 resume")
        input_data: Any = Command(resume=runtime_value)
    elif claim_kind == "stale_recovery":
        if has_checkpoint:
            input_data = None
        else:
            input_data = await _initial_graph_input(
                initial_input,
                user_id=user_id,
                username=username,
                session_id=session_id,
                job_id=job_id,
                input_user_file_id=input_user_file_id,
                input_object_id=input_object_id,
                input_file_hash=input_file_hash,
                input_filename=input_filename,
            )
            if execution_guard is not None:
                await execution_guard.check_after_call()
    elif input_type == "resume":
        raise RuntimeError("resume 输入没有对应的 pending interrupt")
    elif claim_kind == "user_resume" or has_checkpoint:
        raise RuntimeError("Job 恢复输入没有可用的 pending interrupt")
    else:
        input_data = await _initial_graph_input(
            initial_input,
            user_id=user_id,
            username=username,
            session_id=session_id,
            job_id=job_id,
            input_user_file_id=input_user_file_id,
            input_object_id=input_object_id,
            input_file_hash=input_file_hash,
            input_filename=input_filename,
        )

    if execution_guard is not None:
        await execution_guard.check_after_call()

    adapter = LangGraphEventAdapter(job_id, job_attempt)
    streamed_interrupts: list[Any] = []
    handler_recovered = False
    stream_kwargs = {
        "stream_mode": ["updates", "messages", "custom", "tasks"],
        "subgraphs": True,
        "version": "v2",
    }
    stream_kwargs["context"] = agent_runtime_context or AgentRunContext(
        execution_guard=execution_guard,
        web_search_enabled=web_search_enabled,
    )
    try:
        async for chunk in graph.astream(
            input_data,
            config,
            **stream_kwargs,
        ):
            if execution_guard is not None:
                await execution_guard.ensure_active()
            if chunk.get("type") == "updates" and isinstance(chunk.get("data"), dict):
                if any(
                    str(key).startswith(ERROR_HANDLER_STREAM_PREFIX)
                    for key in chunk["data"]
                ):
                    handler_recovered = True
                interrupt_data = chunk["data"].get("__interrupt__")
                if interrupt_data:
                    streamed_interrupts.extend(
                        interrupt_data
                        if isinstance(interrupt_data, (list, tuple))
                        else [interrupt_data]
                    )
            for event_data in adapter.convert(chunk):
                if execution_guard is not None:
                    await execution_guard.check_after_call()
                yield event_data

        if execution_guard is not None:
            await execution_guard.ensure_active()
        state = await graph.aget_state(config)
        if execution_guard is not None:
            await execution_guard.check_after_call()
        interrupts = _snapshot_interrupts(state) or streamed_interrupts
        if interrupts:
            interrupt_obj = interrupts[0]
            question = (
                interrupt_obj.value
                if hasattr(interrupt_obj, "value")
                else str(interrupt_obj)
            )
            if execution_guard is not None:
                await execution_guard.check_after_call()
            yield {
                "type": "interrupt",
                "message": question,
                "question_id": _interrupt_id(interrupt_obj),
                "attempt": job_attempt,
            }
            return

        if execution_guard is not None:
            await execution_guard.ensure_active()
        final_result = _build_final_result(state.values, job_attempt=job_attempt)
        if execution_guard is not None:
            await execution_guard.check_after_call()
        yield final_result
    except JobExecutionRevoked:
        raise
    except Exception as exc:
        _raise_wrapped_cancellation(exc)
        if execution_guard is not None:
            await execution_guard.check_after_call()
        recovered = await _recovered_after_node_error(
            graph,
            config,
            handler_recovered=handler_recovered,
            job_attempt=job_attempt,
        )
        if recovered is not None:
            if execution_guard is not None:
                await execution_guard.check_after_call()
            yield recovered
            return
        # 公开事件只保留脱敏文案；真实异常通过下划线前缀的内部字段传给
        # OrderedEventWriter，后者只把 message 落库，诊断只用于 worker 日志。
        yield {
            "type": "error",
            "message": sanitize_public_error(exc),
            "attempt": job_attempt,
            "_diagnostic": classify_graph_failure(exc),
        }
