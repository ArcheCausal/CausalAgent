"""编排 worker 启动检查、runtime 创建和 slot 生命周期。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import logging
import signal
import socket
import sys
from typing import Any

from Agent.causal_agent.postgres_checkpointer import (
    open_checkpoint_pool,
    verify_checkpoint_schema,
)
from app.agent import job_service
from app.agent.worker.execution import run_job
from app.agent.worker.runtime import (
    ProcessRuntime,
    create_process_runtime,
    create_slot_runtime,
    initialize_production_runtime,
)
from app.db import check_database_readiness
from config.settings import settings
from observability.logging_runtime import (
    configure_logging,
    current_environment,
    log_context,
    log_event,
)


LOGGER = logging.getLogger(__name__)

if __name__ == "__main__":
    configure_logging("worker", current_environment(), logging.INFO)


async def run_slot(
    slot_index: int,
    checkpoint_pool: Any,
    process_runtime: ProcessRuntime,
    stop_event: asyncio.Event | None = None,
) -> None:
    """启动一个 slot；新链路只独占 Job context，不再独占 MCP session。"""
    stop_event = stop_event or asyncio.Event()
    worker_id = f"{socket.gethostname()}:{slot_index}"
    with log_context(worker_slot=slot_index):
        stack = AsyncExitStack()
        phase = "runtime_initialization"
        try:
            try:
                slot_runtime = await create_slot_runtime(
                    process_runtime,
                    stack,
                    checkpoint_pool,
                )
                log_event(
                    LOGGER,
                    "worker.slot.ready",
                    details={"tool_count": len(slot_runtime.mcp_tools)},
                )
                phase = "job_polling"
                while not stop_event.is_set():
                    job = await asyncio.to_thread(job_service.claim_next_job, worker_id)
                    if not job:
                        try:
                            await asyncio.wait_for(
                                stop_event.wait(),
                                timeout=settings.JOB_POLL_INTERVAL_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            pass
                        continue
                    if stop_event.is_set():
                        break
                    await run_job(
                        job,
                        slot_runtime,
                        worker_id,
                        worker_slot=slot_index,
                    )
            finally:
                await stack.aclose()
        except asyncio.CancelledError:
            raise
        except BaseException:
            log_event(
                LOGGER,
                "worker.slot.failed",
                details={
                    "phase": phase,
                    "reason_code": (
                        "initialization_failed"
                        if phase == "runtime_initialization"
                        else "runtime_failed"
                    ),
                },
                exc_info=True,
            )
            raise


def _install_shutdown_handlers(stop_event: asyncio.Event) -> dict[int, Any]:
    """把终止信号转换为可测试的异步 drain 事件。"""
    loop = asyncio.get_running_loop()
    previous: dict[int, Any] = {}

    def request_shutdown(signum: int, _frame: Any) -> None:
        """把收到的终止信号转发给当前事件循环。"""
        loop.call_soon_threadsafe(stop_event.set)

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.signal(signum, request_shutdown)
        except (OSError, ValueError):
            pass
    return previous


def _restore_shutdown_handlers(previous: dict[int, Any]) -> None:
    """恢复 worker 启动前的信号处理器。"""
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (OSError, ValueError):
            pass


async def _run_slots_until_shutdown(
    slot_tasks: list[asyncio.Task[Any]],
    stop_event: asyncio.Event,
) -> None:
    """等待 slots，收到停止信号后按超时执行 drain 并取消残留任务。"""
    shutdown_waiter = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait(
            [*slot_tasks, shutdown_waiter],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_waiter not in done:
            await asyncio.gather(*slot_tasks)
            return

        _, pending = await asyncio.wait(
            slot_tasks,
            timeout=settings.JOB_DRAIN_TIMEOUT_SECONDS,
        )
        if pending:
            for task in pending:
                task.cancel()
        await asyncio.gather(*slot_tasks, return_exceptions=True)
    finally:
        if not shutdown_waiter.done():
            shutdown_waiter.cancel()
        await asyncio.gather(shutdown_waiter, return_exceptions=True)
        pending_slots = [task for task in slot_tasks if not task.done()]
        for task in pending_slots:
            task.cancel()
        if pending_slots:
            await asyncio.gather(*pending_slots, return_exceptions=True)


async def main_async() -> None:
    """执行数据库、checkpoint、LLM/RAG 检查并启动固定数量的 slots。"""
    stop_event = asyncio.Event()
    previous_handlers = _install_shutdown_handlers(stop_event)
    phase = "database_readiness"
    dependency = "mysql"
    startup_ready = False
    try:
        check_database_readiness()
        phase = "checkpoint_pool"
        dependency = "postgresql"
        async with open_checkpoint_pool() as checkpoint_pool:
            phase = "checkpoint_schema"
            await verify_checkpoint_schema(checkpoint_pool)
            phase = "process_runtime"
            dependency = "worker_runtime"
            async with AsyncExitStack() as process_stack:
                process_runtime = await initialize_production_runtime(
                    checkpoint_pool,
                    process_stack,
                )
                phase = "rag_readiness"
                if not process_runtime.rag_available:
                    log_event(
                        LOGGER,
                        "rag.startup.unavailable",
                        details={"reason_code": process_runtime.rag_error_code or "unavailable"},
                    )
                slot_count = max(1, settings.JOB_WORKERS)
                log_event(LOGGER, "worker.startup.ready")
                startup_ready = True
                slot_tasks = [
                    asyncio.create_task(
                        run_slot(index + 1, checkpoint_pool, process_runtime, stop_event)
                    )
                    for index in range(slot_count)
                ]
                await _run_slots_until_shutdown(slot_tasks, stop_event)
    except asyncio.CancelledError:
        raise
    except Exception:
        if not startup_ready:
            log_event(
                LOGGER,
                "worker.startup.failed",
                details={
                    "phase": phase,
                    "dependency": dependency,
                    "reason_code": (
                        "readiness_failed"
                        if phase == "database_readiness"
                        else "initialization_failed"
                    ),
                },
                exc_info=True,
            )
        raise
    finally:
        _restore_shutdown_handlers(previous_handlers)


def main() -> None:
    """命令行入口：``python -m app.agent.worker``。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    configure_logging("worker", current_environment(), logging.INFO)
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        return
    except Exception:
        raise SystemExit(1) from None
