"""PostgreSQL Agent 持久化数据清理 outbox worker。

一个进程消费两张 MySQL outbox 表：Job 父子图 checkpoint 和用户长期记忆
Store namespace。两类任务共享同一个 PostgreSQL 连接池，以及由其构造的
`AsyncPostgresSaver` 和 `AsyncPostgresStore`。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from datetime import datetime, timezone
import time

from observability.logging_runtime import (
    configure_logging,
    current_environment,
    log_context,
    log_event,
)
from observability.noise_control import FailureTransitionTracker

if __name__ == "__main__":
    configure_logging("maintenance", current_environment(), logging.INFO)

LOGGER = logging.getLogger(__name__)
_RUNTIME_FAILURES = FailureTransitionTracker()
_STARTUP_READY = False

# Store schema 由业务 worker 首次启动时 setup；清理进程只等待就绪，不负责建表。
STORE_SCHEMA_WAIT_ATTEMPTS = 20
STORE_SCHEMA_WAIT_INTERVAL_SECONDS = 3.0

try:
    from Agent.causal_agent.postgres_checkpointer import (
        build_checkpointer,
        open_checkpoint_pool,
        verify_checkpoint_schema,
    )
    from Agent.deep_agent.memory import memory_namespace_for_user
    from Agent.deep_agent.postgres_store import (
        build_async_postgres_store,
        purge_store_namespace,
        verify_postgres_store_schema,
    )
    from Agent.deep_agent_tools.identity import build_deep_agent_run_id
    from app.agent.persistence_cleanup import (
        CHECKPOINT_TASK,
        USER_MEMORY_TASK,
        claim_cleanup_item,
        mark_cleanup_failed,
        mark_cleanup_succeeded,
        write_cleanup_runtime_snapshot,
    )
    from app.db import check_database_readiness
except Exception as exc:
    if __name__ == "__main__":
        log_event(
            LOGGER,
            "maintenance.startup.failed",
            details={
                "phase": "module_initialization",
                "dependency": "cleanup_runtime",
                "reason_code": "initialization_failed",
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        raise SystemExit(1) from None
    raise


def _float_env(name: str, default: float) -> float:
    """读取 worker 的非敏感浮点配置。"""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"配置错误: {name} 必须大于 0。")
    return value


def _safe_exc_info(exc: BaseException):
    return type(exc), exc, exc.__traceback__


def _record_runtime_failure(key: str, reason_code: str, exc: BaseException) -> None:
    decision = _RUNTIME_FAILURES.record_failure(key, type(exc).__name__)
    if decision.emit:
        log_event(
            LOGGER,
            "agent.persistence.cleanup.runtime.degraded",
            details={
                "reason_code": reason_code,
                "suppressed_count": decision.suppressed_count,
            },
            exc_info=_safe_exc_info(exc),
        )


def _record_runtime_success(key: str) -> None:
    recovery = _RUNTIME_FAILURES.record_success(key)
    if recovery is not None:
        log_event(
            LOGGER,
            "agent.persistence.cleanup.runtime.recovered",
            details={
                "downtime_ms": recovery.downtime_ms,
                "failure_count": recovery.failure_count,
            },
        )


async def _wait_for_store_schema(store) -> None:
    """等待业务 worker 完成官方 Store setup，超过上限则明确启动失败。"""
    last_error: Exception | None = None
    for _ in range(STORE_SCHEMA_WAIT_ATTEMPTS):
        try:
            await verify_postgres_store_schema(store)
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(STORE_SCHEMA_WAIT_INTERVAL_SECONDS)
    raise RuntimeError("PostgreSQL Store schema 未就绪") from last_error


async def _cleanup_checkpoint_task(saver, item: dict) -> None:
    """删除父图 thread 和 Deep Agent 子图 thread，两者都成功才算完成。"""
    job_id = item["job_id"]
    await saver.adelete_thread(job_id)
    await saver.adelete_thread(build_deep_agent_run_id(job_id=job_id))


async def _cleanup_user_memory_task(store, item: dict) -> int:
    """清空用户长期记忆 namespace，并返回删除的条目数量。"""
    return await purge_store_namespace(
        store,
        memory_namespace_for_user(item["user_id"]),
    )


async def _run_async() -> None:
    """连接两侧数据库并持续消费两张 cleanup outbox。"""
    global _STARTUP_READY
    _STARTUP_READY = False
    startup_phase = "database_readiness"
    startup_dependency = "mysql"
    try:
        check_database_readiness()
        startup_phase = "runtime_configuration"
        startup_dependency = "environment"
        poll_interval = _float_env("AGENT_PERSISTENCE_CLEANUP_POLL_INTERVAL_SECONDS", 1.0)
        heartbeat_interval = _float_env(
            "AGENT_PERSISTENCE_CLEANUP_HEARTBEAT_INTERVAL_SECONDS", 10.0
        )
        lease_seconds = int(
            _float_env("AGENT_PERSISTENCE_CLEANUP_LEASE_SECONDS", 300.0)
        )
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        state: dict[str, object] = {
            "worker_alias": "agent-persistence-cleanup",
            "worker_status": "idle",
            "current_task_type": None,
            "started_at": started_at,
            "heartbeat_at": started_at,
            "current_outbox_id": None,
            "run_success_count": 0,
            "run_failure_count": 0,
            "startup_success_count": 0,
            "startup_failure_count": 0,
            "heartbeat_interval_seconds": heartbeat_interval,
            "processing_started_at": None,
            "processing_duration_seconds": None,
            "current_processing_started_at": None,
            "current_processing_duration_seconds": None,
            "_processing_monotonic": None,
            "last_failure_at": None,
            "last_error_present": False,
        }

        def publish_runtime(*, force: bool = False) -> None:
            """按心跳周期发布安全状态；失败只进入转换式运行事件。"""
            now = time.monotonic()
            last = state.get("_last_publish_monotonic")
            if not force and isinstance(last, float) and now - last < heartbeat_interval:
                return
            heartbeat_at = datetime.now(timezone.utc)
            state["heartbeat_at"] = heartbeat_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            processing_started = state.get("_processing_monotonic")
            if isinstance(processing_started, float):
                state["processing_duration_seconds"] = round(max(0.0, now - processing_started), 3)
                state["current_processing_duration_seconds"] = state["processing_duration_seconds"]
            else:
                state["processing_duration_seconds"] = None
                state["current_processing_duration_seconds"] = None
            try:
                write_cleanup_runtime_snapshot(state)
            except Exception as exc:
                _record_runtime_failure("snapshot_publish", "snapshot_publish_failed", exc)
            else:
                state["_last_publish_monotonic"] = now
                _record_runtime_success("snapshot_publish")

        async def heartbeat_loop() -> None:
            """独立发布心跳，避免长时间 PostgreSQL 删除被误判失联。"""
            while True:
                await asyncio.to_thread(publish_runtime)
                await asyncio.sleep(min(heartbeat_interval, max(0.5, poll_interval)))

        startup_phase = "checkpoint_pool"
        startup_dependency = "postgresql"
        async with open_checkpoint_pool() as checkpoint_pool:
            startup_phase = "checkpoint_schema"
            await verify_checkpoint_schema(checkpoint_pool)
            saver = build_checkpointer(checkpoint_pool)
            startup_phase = "store_schema"
            store = build_async_postgres_store(pool=checkpoint_pool)
            await _wait_for_store_schema(store)
            await asyncio.to_thread(publish_runtime, force=True)
            log_event(
                LOGGER,
                "maintenance.startup.ready",
            )
            _STARTUP_READY = True
            heartbeat_task = asyncio.create_task(heartbeat_loop())
            try:
                while True:
                    try:
                        item = await asyncio.to_thread(
                            claim_cleanup_item,
                            worker_id,
                            lease_seconds=lease_seconds,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        _record_runtime_failure("claim", "claim_failed", exc)
                        await asyncio.sleep(poll_interval)
                        continue
                    _record_runtime_success("claim")
                    if not item:
                        await asyncio.sleep(poll_interval)
                        continue

                    task_type = str(item["task_type"])
                    outbox_id = int(item["id"])
                    attempt = int(item["attempts"])
                    state["worker_status"] = "processing"
                    state["current_task_type"] = task_type
                    state["current_outbox_id"] = outbox_id
                    state["_processing_monotonic"] = time.monotonic()
                    processing_started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    state["processing_started_at"] = processing_started_at
                    state["current_processing_started_at"] = processing_started_at
                    await asyncio.to_thread(publish_runtime, force=True)
                    attempt_started = time.perf_counter()
                    context = (
                        {"job_id": item["job_id"]}
                        if task_type == CHECKPOINT_TASK
                        else {"user_id": item["user_id"]}
                    )
                    with log_context(**context):
                        deleted_count: int | None = None
                        try:
                            if task_type == CHECKPOINT_TASK:
                                await _cleanup_checkpoint_task(saver, item)
                            else:
                                deleted_count = await _cleanup_user_memory_task(store, item)
                        except asyncio.CancelledError:
                            raise
                        except Exception as cleanup_exc:
                            event_exc: BaseException = cleanup_exc
                            reason_code = "cleanup_failed"
                            try:
                                await asyncio.to_thread(
                                    mark_cleanup_failed,
                                    task_type,
                                    outbox_id,
                                    str(cleanup_exc),
                                )
                            except asyncio.CancelledError:
                                raise
                            except Exception as persist_exc:
                                event_exc = persist_exc
                                reason_code = "persist_failed"
                            log_event(
                                LOGGER,
                                "agent.persistence.cleanup.failed",
                                details={
                                    "task_type": task_type,
                                    "outbox_id": outbox_id,
                                    "attempt": attempt,
                                    "duration_ms": int((time.perf_counter() - attempt_started) * 1000),
                                    "reason_code": reason_code,
                                },
                                exc_info=_safe_exc_info(event_exc),
                            )
                            state["run_failure_count"] = int(state["run_failure_count"]) + 1
                            state["startup_failure_count"] = int(state["startup_failure_count"]) + 1
                            state["last_failure_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                            state["last_error_present"] = True
                        else:
                            try:
                                await asyncio.to_thread(
                                    mark_cleanup_succeeded,
                                    task_type,
                                    outbox_id,
                                )
                            except asyncio.CancelledError:
                                raise
                            except Exception as persist_exc:
                                log_event(
                                    LOGGER,
                                    "agent.persistence.cleanup.failed",
                                    details={
                                        "task_type": task_type,
                                        "outbox_id": outbox_id,
                                        "attempt": attempt,
                                        "duration_ms": int((time.perf_counter() - attempt_started) * 1000),
                                        "reason_code": "persist_failed",
                                    },
                                    exc_info=_safe_exc_info(persist_exc),
                                )
                                state["run_failure_count"] = int(state["run_failure_count"]) + 1
                                state["startup_failure_count"] = int(state["startup_failure_count"]) + 1
                                state["last_failure_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                                state["last_error_present"] = True
                            else:
                                log_event(
                                    LOGGER,
                                    "agent.persistence.cleanup.succeeded",
                                    details={
                                        "task_type": task_type,
                                        "outbox_id": outbox_id,
                                        "attempt": attempt,
                                        "duration_ms": int((time.perf_counter() - attempt_started) * 1000),
                                        "deleted_count": deleted_count,
                                    },
                                )
                                state["run_success_count"] = int(state["run_success_count"]) + 1
                                state["startup_success_count"] = int(state["startup_success_count"]) + 1
                                state["last_error_present"] = False
                        finally:
                            state["worker_status"] = "idle"
                            state["current_task_type"] = None
                            state["current_outbox_id"] = None
                            state["processing_started_at"] = None
                            state["processing_duration_seconds"] = None
                            state["current_processing_started_at"] = None
                            state["current_processing_duration_seconds"] = None
                            state["_processing_monotonic"] = None
                            await asyncio.to_thread(publish_runtime, force=True)
            finally:
                state["worker_status"] = "stopped"
                await asyncio.to_thread(publish_runtime, force=True)
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        if not _STARTUP_READY:
            log_event(
                LOGGER,
                "maintenance.startup.failed",
                details={
                    "phase": startup_phase,
                    "dependency": startup_dependency,
                    "reason_code": "initialization_failed",
                },
                exc_info=True,
            )
        raise


def main() -> None:
    """命令行入口：python -m Database.agent_persistence_cleanup_worker。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    configure_logging("maintenance", current_environment(), logging.INFO)
    try:
        asyncio.run(_run_async())
    except KeyboardInterrupt:
        return
    except Exception as exc:
        if _STARTUP_READY:
            _record_runtime_failure("main_loop", "runtime_failed", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
