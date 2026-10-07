"""Bounded CPU execution with admission control and late-result isolation."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
import itertools
import time
from typing import Any, Callable, Mapping

try:
    import resource as _resource
except ImportError:  # pragma: no cover - Windows worker fallback
    _resource = None

from Agent.deep_agent_tools.error_codes import SafeErrorCode


@dataclass(frozen=True)
class _ProcessExecutionEnvelope:
    value: Any
    peak_rss_bytes: int
    cpu_seconds: float


def _run_runner(runner: Callable[..., Any], csv_data: str, parameters: Mapping[str, Any]) -> Any:
    """Top-level callable so a spawned process receives only pure data."""

    # Third-party causal libraries may print progress, warnings or file paths;
    # never let child-process output share the service protocol/log stream.
    before = _resource.getrusage(_resource.RUSAGE_SELF) if _resource else None
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        value = runner(csv_data, parameters)
    after = _resource.getrusage(_resource.RUSAGE_SELF) if _resource else None
    peak_rss_bytes = int(getattr(after, "ru_maxrss", 0) or 0) * 1024
    cpu_seconds = 0.0
    if before is not None and after is not None:
        cpu_seconds = max(
            0.0,
            (after.ru_utime + after.ru_stime)
            - (before.ru_utime + before.ru_stime),
        )
    return _ProcessExecutionEnvelope(value, peak_rss_bytes, cpu_seconds)


class PoolExecutionError(RuntimeError):
    def __init__(
        self,
        code: SafeErrorCode,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.safe_error_code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ExecutionOutcome:
    value: Any
    executor_slot_id: str
    peak_rss_bytes: int = 0
    cpu_seconds: float = 0.0


@dataclass(frozen=True)
class CancellationOutcome:
    """一次幂等远端取消请求的稳定结果。"""

    status: str
    invocation_id: str


@dataclass
class _InvocationHandle:
    invocation_id: str
    execution_identity: tuple[str, int, int, str] | None
    cancel_event: asyncio.Event
    done_event: asyncio.Event
    executor: Any | None = None
    future: asyncio.Future[Any] | None = None
    state: str = "queued"


class BoundedProcessPool:
    """用独立单进程 executor 隔离每个可取消算法 invocation。"""

    def __init__(
        self,
        *,
        process_workers: int = 2,
        queue_capacity: int = 4,
        enqueue_timeout_seconds: float = 5.0,
        timeout_recycle_threshold: int = 3,
        executor_factory: Callable[..., Any] | None = None,
    ) -> None:
        if process_workers <= 0 or queue_capacity < 0:
            raise ValueError("process_workers must be positive and queue_capacity non-negative")
        self.process_workers = process_workers
        self.queue_capacity = queue_capacity
        self.enqueue_timeout_seconds = enqueue_timeout_seconds
        self.timeout_recycle_threshold = timeout_recycle_threshold
        self._executor_factory = executor_factory or ProcessPoolExecutor
        self._admission = asyncio.Semaphore(process_workers + queue_capacity)
        self._running = asyncio.Semaphore(process_workers)
        self._capability_limits: dict[str, asyncio.Semaphore] = {}
        self._inflight: set[asyncio.Future[Any]] = set()
        self._invocations: dict[str, _InvocationHandle] = {}
        self._terminal: OrderedDict[
            str, tuple[tuple[str, int, int, str] | None, str]
        ] = OrderedDict()
        self._registry_lock = asyncio.Lock()
        self._sequence = itertools.count()
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("process pool is closed")
        self._started = True

    async def close(self) -> None:
        self._closed = True
        self._started = False
        async with self._registry_lock:
            handles = tuple(self._invocations.values())
            for handle in handles:
                handle.cancel_event.set()
        if handles:
            await asyncio.gather(
                *(handle.done_event.wait() for handle in handles),
                return_exceptions=True,
            )

    def ready(self) -> bool:
        return self._started and not self._closed

    async def _register(
        self,
        invocation_id: str,
        execution_identity: tuple[str, int, int, str] | None,
    ) -> _InvocationHandle:
        async with self._registry_lock:
            terminal = self._terminal.get(invocation_id)
            if invocation_id in self._invocations or (
                terminal is not None and terminal[1] == "canceled"
            ):
                raise PoolExecutionError(SafeErrorCode.DUPLICATE_INVOCATION)
            handle = _InvocationHandle(
                invocation_id=invocation_id,
                execution_identity=execution_identity,
                cancel_event=asyncio.Event(),
                done_event=asyncio.Event(),
            )
            self._invocations[invocation_id] = handle
            return handle

    async def _finish(
        self,
        handle: _InvocationHandle,
        state: str,
    ) -> None:
        async with self._registry_lock:
            if self._invocations.get(handle.invocation_id) is handle:
                self._invocations.pop(handle.invocation_id, None)
            if state != "not_started":
                self._terminal[handle.invocation_id] = (
                    handle.execution_identity,
                    state,
                )
                self._terminal.move_to_end(handle.invocation_id)
                while len(self._terminal) > 1024:
                    self._terminal.popitem(last=False)
            handle.state = state
            handle.done_event.set()

    @staticmethod
    async def _acquire_or_cancel(
        semaphore: asyncio.Semaphore,
        handle: _InvocationHandle,
        *,
        timeout_seconds: float | None,
    ) -> None:
        acquire_task = asyncio.create_task(semaphore.acquire())
        cancel_task = asyncio.create_task(handle.cancel_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {acquire_task, cancel_task},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and handle.cancel_event.is_set():
                if acquire_task in done and acquire_task.result() is True:
                    semaphore.release()
                acquire_task.cancel()
                await asyncio.gather(acquire_task, return_exceptions=True)
                raise PoolExecutionError(SafeErrorCode.ALGORITHM_CANCELED)
            if acquire_task not in done:
                acquire_task.cancel()
                await asyncio.gather(acquire_task, return_exceptions=True)
                raise PoolExecutionError(
                    SafeErrorCode.MCP_CAPACITY_EXHAUSTED,
                    retry_after_seconds=1,
                )
            await acquire_task
        except asyncio.CancelledError:
            acquire_task.cancel()
            await asyncio.gather(acquire_task, return_exceptions=True)
            raise
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    async def cancel(
        self,
        invocation_id: str,
        *,
        execution_identity: tuple[str, int, int, str] | None,
        wait_timeout_seconds: float = 3.0,
    ) -> CancellationOutcome:
        """幂等取消一个精确 invocation，不影响其他算法进程。"""

        async with self._registry_lock:
            handle = self._invocations.get(invocation_id)
            terminal = self._terminal.get(invocation_id)
            if handle is None:
                if terminal is None:
                    return CancellationOutcome("not_found", invocation_id)
                terminal_identity, terminal_state = terminal
                if terminal_identity != execution_identity:
                    return CancellationOutcome("identity_mismatch", invocation_id)
                status = (
                    "already_canceled"
                    if terminal_state == "canceled"
                    else "already_finished"
                )
                return CancellationOutcome(status, invocation_id)
            if handle.execution_identity != execution_identity:
                return CancellationOutcome("identity_mismatch", invocation_id)
            handle.cancel_event.set()
            done_event = handle.done_event
        try:
            await asyncio.wait_for(done_event.wait(), wait_timeout_seconds)
        except asyncio.TimeoutError:
            return CancellationOutcome("cancel_pending", invocation_id)
        return CancellationOutcome("canceled", invocation_id)

    async def execute(
        self,
        *,
        capability_id: str,
        runner: Callable[..., Any],
        csv_data: str,
        parameters: Mapping[str, Any],
        timeout_seconds: float,
        concurrency: int,
        before_start: Callable[[float], Awaitable[None] | None] | None = None,
        invocation_id: str | None = None,
        execution_identity: tuple[str, int, int, str] | None = None,
    ) -> ExecutionOutcome:
        if not self.ready():
            raise PoolExecutionError(SafeErrorCode.MCP_CAPACITY_EXHAUSTED)
        resolved_invocation_id = invocation_id or f"anonymous-{next(self._sequence)}"
        handle = await self._register(resolved_invocation_id, execution_identity)
        capability_semaphore = self._capability_limits.setdefault(
            capability_id, asyncio.Semaphore(concurrency)
        )
        queued_at = time.perf_counter()
        capability_acquired = False
        admission_acquired = False
        running_acquired = False
        terminal_state = "not_started"
        try:
            await self._acquire_or_cancel(
                capability_semaphore,
                handle,
                timeout_seconds=self.enqueue_timeout_seconds,
            )
            capability_acquired = True
            await self._acquire_or_cancel(
                self._admission,
                handle,
                timeout_seconds=self.enqueue_timeout_seconds,
            )
            admission_acquired = True
            await self._acquire_or_cancel(
                self._running,
                handle,
                timeout_seconds=None,
            )
            running_acquired = True

            loop = asyncio.get_running_loop()
            if before_start is not None:
                callback_result = before_start(time.perf_counter() - queued_at)
                if callback_result is not None:
                    await callback_result
            if handle.cancel_event.is_set():
                raise PoolExecutionError(SafeErrorCode.ALGORITHM_CANCELED)
            executor = self._executor_factory(max_workers=1)
            handle.executor = executor
            handle.state = "running"
            try:
                future = asyncio.ensure_future(
                    loop.run_in_executor(
                        executor, _run_runner, runner, csv_data, dict(parameters)
                    )
                )
            except BaseException:
                await asyncio.to_thread(_stop_executor, executor, True)
                raise
            handle.future = future
            terminal_state = "failed"
            self._inflight.add(future)
            cancel_task = asyncio.create_task(handle.cancel_event.wait())
            try:
                done, _pending = await asyncio.wait(
                    {future, cancel_task},
                    timeout=timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task in done and handle.cancel_event.is_set():
                    terminal_state = "canceled"
                    await asyncio.to_thread(_stop_executor, executor, True)
                    future.cancel()
                    await asyncio.gather(future, return_exceptions=True)
                    raise PoolExecutionError(SafeErrorCode.ALGORITHM_CANCELED)
                if future not in done:
                    terminal_state = "timed_out"
                    await asyncio.to_thread(_stop_executor, executor, True)
                    future.cancel()
                    await asyncio.gather(future, return_exceptions=True)
                    raise PoolExecutionError(SafeErrorCode.ALGORITHM_TIMED_OUT)
                value = await future
            except asyncio.CancelledError:
                terminal_state = "canceled"
                handle.cancel_event.set()
                await asyncio.shield(asyncio.to_thread(_stop_executor, executor, True))
                future.cancel()
                await asyncio.gather(future, return_exceptions=True)
                raise
            except Exception as exc:
                if isinstance(exc, PoolExecutionError):
                    raise
                raise PoolExecutionError(
                    SafeErrorCode.ALGORITHM_EXECUTION_FAILED
                ) from exc
            else:
                terminal_state = "completed"
                await asyncio.to_thread(_stop_executor, executor, False)
                if isinstance(value, _ProcessExecutionEnvelope):
                    execution_value = value.value
                    peak_rss_bytes = value.peak_rss_bytes
                    cpu_seconds = value.cpu_seconds
                else:
                    execution_value = value
                    peak_rss_bytes = 0
                    cpu_seconds = 0.0
                return ExecutionOutcome(
                    value=execution_value,
                    executor_slot_id=(
                        f"process-{next(self._sequence) % self.process_workers}"
                    ),
                    peak_rss_bytes=peak_rss_bytes,
                    cpu_seconds=cpu_seconds,
                )
            finally:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
                self._inflight.discard(future)
        except PoolExecutionError as exc:
            if exc.safe_error_code == SafeErrorCode.ALGORITHM_CANCELED:
                terminal_state = "canceled"
            elif exc.safe_error_code == SafeErrorCode.ALGORITHM_TIMED_OUT:
                terminal_state = "timed_out"
            raise
        except asyncio.CancelledError:
            terminal_state = "canceled"
            handle.cancel_event.set()
            raise
        finally:
            if running_acquired:
                self._running.release()
            if admission_acquired:
                self._admission.release()
            if capability_acquired:
                capability_semaphore.release()
            await self._finish(handle, terminal_state)

    async def recycle(self) -> None:
        """兼容维护入口：终止当前全部独立 invocation，随后继续接收请求。"""

        async with self._registry_lock:
            handles = tuple(self._invocations.values())
            for handle in handles:
                handle.cancel_event.set()
        if handles:
            await asyncio.gather(
                *(handle.done_event.wait() for handle in handles),
                return_exceptions=True,
            )
        try:
            from observability.logging_runtime import log_event
            import logging

            log_event(
                logging.getLogger(__name__),
                "mcp.process.recycled",
                details={"reason_code": "timeout"},
            )
        except Exception:
            pass


def _stop_executor(executor: Any, terminate_running: bool) -> None:
    """Stop an executor without assuming Future.cancel() kills CPU work.

    ProcessPoolExecutor does not expose a public hard-stop API on Python 3.11.
    Capture its child processes before shutdown, cancel queued futures, then
    explicitly terminate/kill surviving workers. Non-process test executors
    simply receive the normal non-blocking shutdown.
    """

    raw_processes = getattr(executor, "_processes", None)
    processes = tuple(raw_processes.values()) if isinstance(raw_processes, dict) else ()
    if not terminate_running:
        executor.shutdown(wait=True, cancel_futures=True)
        return
    executor.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        try:
            if process.is_alive():
                process.terminate()
        except (AttributeError, OSError):
            continue
    deadline = time.monotonic() + 1.0
    for process in processes:
        try:
            process.join(max(0.0, deadline - time.monotonic()))
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(0.2)
        except (AttributeError, OSError):
            continue
