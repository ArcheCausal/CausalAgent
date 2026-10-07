"""执行单个 analysis job，维护 lease monitor 并在取消后收敛执行占用。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agent import job_service
from app.agent.worker.event_adapter import sanitize_public_error
from app.agent.worker.event_writer import OrderedEventWriter
from app.agent.worker.execution_guard import JobExecutionGuard, JobExecutionRevoked
from app.agent.worker.graph_runner import ai_call_stream
from app.agent.worker.runtime import SlotRuntime
from config.settings import settings
from observability.logging_runtime import log_context, log_event
from observability.noise_control import FailureTransitionTracker


LOGGER = logging.getLogger(__name__)
_LEASE_FAILURES = FailureTransitionTracker()


def _duration_ms(started_at: float) -> int:
    return max(0, int((asyncio.get_running_loop().time() - started_at) * 1000))


def _resolved_worker_slot(worker_id: str, worker_slot: int | None) -> int | None:
    if worker_slot is not None:
        return worker_slot
    try:
        suffix = worker_id.rsplit(":", 1)[1]
        return int(suffix) if suffix.isdigit() else None
    except (IndexError, TypeError, ValueError):
        return None


async def heartbeat_until_stopped(
    job_id: str,
    worker_id: str,
    attempt_count: int,
    lease_epoch: int,
    stop: asyncio.Event,
    guard: JobExecutionGuard | None = None,
) -> None:
    """刷新 leased/draining 心跳，并按失败转换输出有限事件。"""
    tracker_key = (job_id, worker_id, attempt_count, lease_epoch)
    while not stop.is_set():
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=settings.JOB_HEARTBEAT_INTERVAL_SECONDS,
            )
            return
        except asyncio.TimeoutError:
            pass
        try:
            authority = await asyncio.to_thread(
                job_service.refresh_execution_lease,
                job_id,
                worker_id,
                attempt_count,
                lease_epoch,
            )
        except Exception as exc:
            decision = _LEASE_FAILURES.record_failure(
                tracker_key,
                type(exc).__name__,
            )
            if decision.emit:
                log_event(
                    LOGGER,
                    "worker.lease.refresh_failed",
                    details={
                        "consecutive_failures": decision.failure_count,
                        "suppressed_count": decision.suppressed_count,
                    },
                    exc_info=True,
                )
            continue
        recovery = _LEASE_FAILURES.record_success(tracker_key)
        if recovery is not None:
            log_event(
                LOGGER,
                "worker.lease.recovered",
                details={
                    "failure_count": recovery.failure_count,
                    "downtime_ms": recovery.downtime_ms,
                },
            )
        if not authority.get("active"):
            if guard is not None:
                guard.mark_revoked(
                    status=authority.get("status"),
                    execution_state=authority.get("execution_state"),
                )
            return


async def run_job(
    job: dict[str, Any],
    slot_runtime: SlotRuntime,
    worker_id: str,
    *,
    worker_slot: int | None = None,
) -> None:
    """在数据库确认的 Job 上下文内执行任务，并保证上下文按 token 恢复。"""
    slot = _resolved_worker_slot(worker_id, worker_slot)
    with log_context(
        request_id=job.get("request_id"),
        user_id=job.get("user_id"),
        session_id=job.get("session_id"),
        job_id=job.get("job_id"),
        worker_slot=slot,
    ):
        log_event(
            LOGGER,
            "worker.job.claimed",
            details={
                "claim_kind": job.get("claim_kind") or "initial",
                "attempt": int(job["attempt_count"]),
                "lease_epoch": int(job.get("lease_epoch") or 0),
            },
        )
        await _run_job(job, slot_runtime, worker_id)


async def _run_job(
    job: dict[str, Any],
    slot_runtime: SlotRuntime,
    worker_id: str,
) -> None:
    """保持原有 Job、fencing、取消和 cleanup 状态机的内部执行边界。"""
    job_id = job["job_id"]
    attempt_count = int(job["attempt_count"])
    lease_epoch = int(job.get("lease_epoch") or 0)
    guard = JobExecutionGuard(job_id, worker_id, attempt_count, lease_epoch)
    guard_token = guard.install()
    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        heartbeat_until_stopped(
            job_id,
            worker_id,
            attempt_count,
            lease_epoch,
            stop_heartbeat,
            guard,
        )
    )
    writer: OrderedEventWriter | None = None
    graph_stream = None
    revoked = False
    cleanup_complete = True
    cleanup_errors: list[BaseException] = []
    started_at = asyncio.get_running_loop().time()
    tracker_key = (job_id, worker_id, attempt_count, lease_epoch)
    cleanup_phases: list[str] = []
    terminal_logged = False
    failure_phase = "input_loading"

    def record_cleanup_failure(phase: str) -> None:
        nonlocal cleanup_complete
        cleanup_complete = False
        cleanup_phases.append(phase)

    def log_revoked(reason_code: str) -> None:
        nonlocal terminal_logged
        if terminal_logged:
            return
        log_event(
            LOGGER,
            "worker.job.revoked",
            details={
                "reason_code": reason_code,
                "status": guard.last_status or str(job.get("status") or "unknown"),
                "execution_state": (
                    guard.last_execution_state
                    or str(job.get("execution_state") or "unknown")
                ),
            },
        )
        terminal_logged = True

    def log_failed(
        phase: str,
        reason_code: str,
        *,
        error_category: str | None = None,
        exc_info: Any = None,
    ) -> None:
        nonlocal terminal_logged
        if terminal_logged:
            return
        # details 必须是字典字面量（静态日志政策要求）；error_category 为 None 时
        # validate_event_details 会跳过该键，无需在调用处分支。
        log_event(
            LOGGER,
            "worker.job.failed",
            details={
                "failure_phase": phase,
                "reason_code": reason_code,
                "error_category": error_category,
                "attempt": attempt_count,
                "duration_ms": _duration_ms(started_at),
            },
            exc_info=exc_info,
        )
        terminal_logged = True

    try:
        await guard.ensure_active()
        writer = OrderedEventWriter(job, worker_id, execution_guard=guard)
        build_run_context = getattr(slot_runtime, "build_run_context", None)
        agent_runtime_context = (
            build_run_context(
                job=job,
                execution_guard=guard,
                worker_id=worker_id,
                event_sink=writer.submit,
            )
            if callable(build_run_context)
            else None
        )
        latest_input = await asyncio.to_thread(job_service.get_latest_input_value, job_id)
        await guard.check_after_call()
        if latest_input is None:
            raise RuntimeError("任务输入账本为空")
        initial_input = latest_input
        if (
            job.get("claim_kind") == "stale_recovery"
            and latest_input.get("input_type") != "initial"
        ):
            initial_input = await asyncio.to_thread(
                job_service.get_initial_input_value,
                job_id,
            )
            await guard.check_after_call()
            if initial_input is None:
                raise RuntimeError("任务初始输入账本为空")
        failure_phase = "graph_execution"
        graph_stream = ai_call_stream(
            latest_input,
            job["user_id"],
            f"user-{job['user_id']}",
            job["session_id"],
            job_id=job_id,
            job_attempt=attempt_count,
            input_user_file_id=job.get("input_user_file_id"),
            input_object_id=job.get("input_object_id"),
            input_file_hash=job.get("input_file_hash"),
            input_filename=job.get("input_filename"),
            graph=slot_runtime.graph,
            claim_kind=job.get("claim_kind", "initial"),
            initial_input_record=initial_input,
            execution_guard=guard,
            web_search_enabled=bool(job.get("web_search_enabled")),
            agent_runtime_context=agent_runtime_context,
        )
        try:
            async for payload in graph_stream:
                await guard.ensure_active()
                await writer.submit(payload)
        finally:
            stream = graph_stream
            graph_stream = None
            if stream is not None:
                await stream.aclose()

        failure_phase = "event_writer_close"
        await writer.close()
        terminal_type = getattr(writer, "terminal_type", None)
        if not writer.terminal_seen:
            failure_phase = "success_terminalization"
            await guard.ensure_active()
            completed = await asyncio.to_thread(
                job_service.complete_job,
                job_id,
                worker_id,
                attempt_count,
                {},
                lease_epoch,
            )
            if not completed:
                raise JobExecutionRevoked("success terminalization fenced")
            terminal_type = "implicit_completion"

        duration_ms = _duration_ms(started_at)
        if terminal_type == "interrupt":
            log_event(
                LOGGER,
                "worker.job.interrupted",
                details={
                    "attempt": attempt_count,
                    "duration_ms": duration_ms,
                    "reason_code": "waiting_input",
                },
            )
        elif terminal_type == "error":
            diagnostic = getattr(writer, "terminal_diagnostic", None)
            log_failed(
                "graph_terminal",
                diagnostic.reason_code if diagnostic else "node_error",
                error_category=diagnostic.error_category if diagnostic else None,
                exc_info=diagnostic.exc_info if diagnostic else None,
            )
        else:
            log_event(
                LOGGER,
                "worker.job.finished",
                details={
                    "attempt": attempt_count,
                    "duration_ms": duration_ms,
                    "outcome": terminal_type or "final_result",
                },
            )
        terminal_logged = True
    except JobExecutionRevoked as exc:
        revoked = True
        log_revoked("canceled" if guard.last_status == "canceled" else "fenced")
        if writer is not None:
            try:
                await writer.abort(exc)
            except asyncio.CancelledError:
                cleanup_complete = False
                raise
            except BaseException:
                record_cleanup_failure("writer_abort")
    except asyncio.CancelledError as exc:
        # 进程/worker 级取消不是业务取消，但也不能遗留活跃 writer；只有完整
        # cleanup 后才允许 finally 条件释放 canceled/draining 执行占用。
        revoked = True
        guard.mark_revoked()
        log_revoked("worker_shutdown")
        if writer is not None and not writer.terminal_seen:
            try:
                await writer.abort(exc)
            except asyncio.CancelledError:
                cleanup_complete = False
                raise
            except BaseException:
                record_cleanup_failure("writer_abort")
        raise
    except Exception as exc:
        if writer is not None and not writer.terminal_seen:
            try:
                await writer.abort()
            except asyncio.CancelledError:
                cleanup_complete = False
                raise
            except BaseException:
                record_cleanup_failure("writer_abort")
        try:
            failure_result = await asyncio.to_thread(
                job_service.fail_job,
                job_id,
                worker_id,
                attempt_count,
                sanitize_public_error(exc),
                lease_epoch=lease_epoch,
            )
        except asyncio.CancelledError:
            cleanup_complete = False
            raise
        except Exception:
            record_cleanup_failure("failure_terminalization")
            log_failed("failure_terminalization", "unexpected_error", exc_info=True)
        else:
            if isinstance(failure_result, job_service.FailJobResult):
                fenced = failure_result is not job_service.FailJobResult.APPLIED
            else:
                fenced = not bool(failure_result)
            if fenced:
                revoked = True
                if failure_result is job_service.FailJobResult.CANCELED_FENCED:
                    guard.mark_revoked(
                        status="canceled",
                        execution_state="draining",
                    )
                    log_revoked("canceled")
                else:
                    guard.mark_revoked()
                    log_revoked("fenced")
            else:
                log_failed(
                    failure_phase,
                    "unexpected_error",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
    finally:
        try:
            if graph_stream is not None:
                stream = graph_stream
                graph_stream = None
                try:
                    await stream.aclose()
                except asyncio.CancelledError:
                    cleanup_complete = False
                    raise
                except BaseException:
                    record_cleanup_failure("graph_stream")
            stop_heartbeat.set()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                cleanup_complete = False
                raise
            except BaseException:
                record_cleanup_failure("lease_monitor")
            finally:
                _LEASE_FAILURES.clear(tracker_key)
            if revoked or guard.revoked:
                if cleanup_complete:
                    try:
                        released = await asyncio.to_thread(
                            job_service.release_execution_ownership,
                            job_id,
                            worker_id,
                            attempt_count,
                            lease_epoch,
                        )
                    except asyncio.CancelledError:
                        cleanup_complete = False
                        raise
                    except BaseException:
                        record_cleanup_failure("execution_release")
        finally:
            if cleanup_phases:
                log_event(
                    LOGGER,
                    "worker.job.cleanup_failed",
                    details={
                        "failure_count": len(cleanup_phases),
                        "phases": sorted(set(cleanup_phases)),
                    },
                )
            JobExecutionGuard.reset(guard_token)
