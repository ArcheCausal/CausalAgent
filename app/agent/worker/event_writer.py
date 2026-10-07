"""按 LangGraph 源事件顺序持久化 worker 事件。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent import job_service
from app.agent.worker.event_adapter import FailureDiagnostic
from app.agent.worker.execution_guard import JobExecutionGuard, JobExecutionRevoked


TEXT_FLUSH_INTERVAL_SECONDS = 0.050
TEXT_FLUSH_CHARACTER_LIMIT = 64


async def _complete_terminal_event(
    job: dict[str, Any],
    worker_id: str,
    payload: dict[str, Any],
    context_update: dict[str, Any] | None = None,
) -> bool:
    """在一个事务内完成终态事件、聊天保存和 job 成功更新。"""
    event_type = payload.get("type")
    if event_type == "final_result":
        response_data = payload.get("data", {})
    elif event_type == "interrupt":
        response_data = {
            "type": "human_input_required",
            "summary": payload.get("message", ""),
        }
    else:
        return False

    result = payload.get("data") if event_type == "final_result" else payload
    return await asyncio.to_thread(
        job_service.complete_job_with_chat,
        job,
        worker_id,
        event_type,
        payload,
        response_data,
        result,
        lease_epoch=int(job.get("lease_epoch") or 0),
        question_id=payload.get("question_id"),
        context_update=context_update,
    )


class OrderedEventWriter:
    """按源事件顺序持久化，并按时间或字符数批量合并文字增量。"""

    def __init__(
        self,
        job: dict[str, Any],
        worker_id: str,
        execution_guard: JobExecutionGuard | None = None,
    ):
        """为一个 job 创建独占队列和单一消费协程。"""
        self.job = job
        self.worker_id = worker_id
        self.execution_guard = execution_guard
        self.queue: asyncio.Queue = asyncio.Queue()
        self.buffer: dict[str, Any] | None = None
        self.persisted_sequences: dict[str, int] = {}
        self.terminal_seen = False
        self.terminal_type: str | None = None
        # 与 terminal_type == "error" 同步置位的内部诊断；只提供只读日志用途，
        # 其中的原始异常不会进入 fail_job 或 analysis_job_events。
        self.terminal_diagnostic: FailureDiagnostic | None = None
        self.error: BaseException | None = None
        self.aborted = False
        self.abort_error: BaseException | None = None
        self._abort_lock = asyncio.Lock()
        self.task = asyncio.create_task(self._consume())

    async def submit(self, payload: dict[str, Any]) -> None:
        """把一个结构化事件交给单写协程。"""
        if self.error:
            raise self.error
        if self.aborted:
            raise self.abort_error or JobExecutionRevoked("Event writer aborted")
        accepted = asyncio.get_running_loop().create_future()
        await self.queue.put((payload, accepted))
        await accepted

    async def close(self) -> None:
        """刷新剩余文字并等待所有数据库写入完成。"""
        if self.error:
            raise self.error
        if self.aborted:
            raise self.abort_error or JobExecutionRevoked("Event writer aborted")
        accepted = asyncio.get_running_loop().create_future()
        await self.queue.put((None, accepted))
        await accepted
        await self.task
        if self.error:
            raise self.error

    async def abort(self, error: BaseException | None = None) -> None:
        """停止持久化，丢弃文字 buffer，并结束所有排队 Future。"""
        async with self._abort_lock:
            if not self.aborted:
                self.aborted = True
                self.abort_error = error or JobExecutionRevoked("Event writer aborted")
                self.buffer = None
                self._fail_queued(self.abort_error)
                if not self.task.done():
                    wake = asyncio.get_running_loop().create_future()
                    await self.queue.put((None, wake))
            try:
                await self.task
            except JobExecutionRevoked:
                # fencing/撤销是预期控制流；不能把它升级成 cleanup failure。
                return
            except asyncio.CancelledError:
                # 取消语义必须继续向上冒泡，不能把它伪装成 cleanup 成功。
                raise
            finally:
                self._fail_queued(self.abort_error or JobExecutionRevoked("Event writer aborted"))

    def _fail_queued(self, error: BaseException) -> None:
        """让队列中所有尚未被 consumer 处理的 Future 结束。"""
        while True:
            try:
                _payload, accepted = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not accepted.done():
                accepted.set_exception(error)

    async def _persist(self, payload: dict[str, Any]) -> None:
        """按事件类型调用 fenced 普通写入或现有事务终态入口。"""
        if self.execution_guard is not None:
            await self.execution_guard.ensure_active()
        event_key = payload.get("_event_key")
        if "_event_key" in payload:
            payload = {
                key: value for key, value in payload.items() if key != "_event_key"
            }
        # 上下文写回事实以内部字段传递：只在终态事务里使用，不落库、不出前端。
        context_update = payload.get("_context_commit")
        if "_context_commit" in payload:
            payload = {
                key: value for key, value in payload.items() if key != "_context_commit"
            }
        event_type = payload.get("type", "message")
        attempt_count = int(self.job["attempt_count"])
        if event_type in {"final_result", "interrupt"}:
            accepted = await _complete_terminal_event(
                self.job,
                self.worker_id,
                payload,
                context_update,
            )
            if not accepted:
                raise JobExecutionRevoked("terminal event fenced")
            self.terminal_seen = True
            self.terminal_type = event_type
            return
        if event_type == "error":
            # fail_job 只接受脱敏文案，内部诊断既不落库也不出前端。
            diagnostic = payload.get("_diagnostic")
            outcome = await asyncio.to_thread(
                job_service.fail_job,
                self.job["job_id"],
                self.worker_id,
                attempt_count,
                payload.get("message", "任务执行失败"),
                lease_epoch=int(self.job.get("lease_epoch") or 0),
            )
            if isinstance(outcome, job_service.FailJobResult):
                accepted = outcome is job_service.FailJobResult.APPLIED
                reason = outcome.value
            else:
                # 兼容只返回 bool 的旧测试替身；生产实现必须返回 FailJobResult。
                accepted = bool(outcome)
                reason = "fenced"
            if not accepted:
                raise JobExecutionRevoked(f"error event fenced: {reason}")
            self.terminal_seen = True
            self.terminal_type = "error"
            if isinstance(diagnostic, FailureDiagnostic):
                self.terminal_diagnostic = diagnostic
            return
        event_id = await asyncio.to_thread(
            job_service.write_event,
            self.job["job_id"],
            self.worker_id,
            attempt_count,
            event_type,
            payload,
            lease_epoch=int(self.job.get("lease_epoch") or 0),
            event_key=event_key,
        )
        if event_id is None:
            raise JobExecutionRevoked("event write fenced")

    async def _flush_text(self) -> None:
        """把当前增量缓冲合并为一个有序公开增量事件。"""
        if not self.buffer:
            return
        stream_id = self.buffer["stream_id"]
        sequence = self.persisted_sequences.get(stream_id, 0) + 1
        self.persisted_sequences[stream_id] = sequence
        source_type = self.buffer["source_type"]
        payload = {
            key: value
            for key, value in self.buffer.items()
            if key not in {"source_type", "chunks", "character_count", "started_at"}
        }
        payload.update(
            {
                "type": (
                    "decision_delta"
                    if source_type == "decision_chunk"
                    else "text_delta"
                ),
                "sequence": sequence,
                "delta": "".join(self.buffer["chunks"]),
            }
        )
        self.buffer = None
        await self._persist(payload)

    async def _accept_text(self, payload: dict[str, Any]) -> None:
        """接收内部 token，并在切流或字符阈值时刷新。"""
        stream_id = payload["stream_id"]
        source_type = payload.get("type")
        if self.buffer and (
            self.buffer["stream_id"] != stream_id
            or self.buffer["source_type"] != source_type
        ):
            await self._flush_text()
        if not self.buffer:
            self.buffer = {
                key: value
                for key, value in payload.items()
                if key not in {"type", "sequence", "delta"}
            }
            self.buffer.update(
                {
                    "source_type": source_type,
                    "stream_id": stream_id,
                    "chunks": [],
                    "character_count": 0,
                    "started_at": asyncio.get_running_loop().time(),
                }
            )
        self.buffer["chunks"].append(payload["delta"])
        self.buffer["character_count"] += len(payload["delta"])
        if self.buffer["character_count"] >= TEXT_FLUSH_CHARACTER_LIMIT:
            await self._flush_text()

    async def _consume(self) -> None:
        """串行消费队列，并按批次起始时间触发定时刷新。"""
        try:
            while True:
                try:
                    if self.buffer:
                        elapsed = (
                            asyncio.get_running_loop().time()
                            - self.buffer["started_at"]
                        )
                        payload, accepted = await asyncio.wait_for(
                            self.queue.get(),
                            timeout=max(
                                0.001,
                                TEXT_FLUSH_INTERVAL_SECONDS - elapsed,
                            ),
                        )
                    else:
                        payload, accepted = await self.queue.get()
                except asyncio.TimeoutError:
                    await self._flush_text()
                    continue

                if payload is None:
                    if self.aborted:
                        self.buffer = None
                    else:
                        await self._flush_text()
                    if not accepted.done():
                        accepted.set_result(None)
                    return
                if self.aborted:
                    raise self.abort_error or JobExecutionRevoked("Event writer aborted")
                if payload.get("type") in {"text_chunk", "decision_chunk"}:
                    await self._accept_text(payload)
                else:
                    await self._flush_text()
                    await self._persist(payload)
                if not accepted.done():
                    accepted.set_result(None)
        except BaseException as exc:
            self.error = exc
            if "accepted" in locals() and not accepted.done():
                accepted.set_exception(exc)
            self._fail_queued(exc)
            raise
