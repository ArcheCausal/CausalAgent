import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch


TEST_ENV = {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "test-database",
}
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)

from app.agent.worker.event_adapter import classify_graph_failure  # noqa: E402
from app.agent.worker.event_writer import (  # noqa: E402
    OrderedEventWriter,
    TEXT_FLUSH_CHARACTER_LIMIT,
)
from app.agent.worker.execution_guard import JobExecutionRevoked  # noqa: E402
from app.agent.job_service import FailJobResult  # noqa: E402


def build_job() -> dict:
    """构造不访问数据库的最小 worker job。"""
    return {"job_id": "job-1", "attempt_count": 2}


def text_chunk(delta: str, stream_id: str = "stream-1") -> dict:
    """构造 core 到 worker 的内部文字 chunk。"""
    return {
        "type": "text_chunk",
        "step_id": "step-1",
        "stream_id": stream_id,
        "node_name": "normal_chat",
        "title": "生成回答",
        "sequence": 1,
        "delta": delta,
        "attempt": 2,
    }


def decision_chunk(delta: str, stream_id: str = "decision-stream-1") -> dict:
    """构造公开算法决策的内部增量 chunk。"""
    return {
        "type": "decision_chunk",
        "step_id": "step-1",
        "stream_id": stream_id,
        "node_name": "deep_agent",
        "title": "执行 Deep Agent 分析",
        "decision_kind": "algorithm",
        "tool_name": "causal_pc",
        "sequence": 1,
        "delta": delta,
        "attempt": 2,
    }


class OrderedEventWriterTests(unittest.IsolatedAsyncioTestCase):
    """验证文字批处理的时间、字符和事件边界。"""

    async def test_flushes_after_50ms_without_another_graph_event(self):
        """模型暂停时也必须由 timeout 主动刷新，而非等待下一事件。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        persisted = []
        writer._persist = AsyncMock(side_effect=lambda payload: persisted.append(payload))

        await writer.submit(text_chunk("hello"))
        await asyncio.sleep(0.20)
        await writer.close()

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["type"], "text_delta")
        self.assertEqual(persisted[0]["delta"], "hello")

    async def test_50ms_is_measured_from_batch_start_not_last_chunk(self):
        """持续到达的小 token 也不能无限延后时间阈值。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        persisted = []
        writer._persist = AsyncMock(side_effect=lambda payload: persisted.append(payload))

        await writer.submit(text_chunk("a"))
        await asyncio.sleep(0.02)
        await writer.submit(text_chunk("b"))
        await asyncio.sleep(0.04)
        await writer.close()

        self.assertGreaterEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["delta"], "ab")

    async def test_flushes_at_character_limit(self):
        """累计达到字符阈值时必须立即写入。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        persisted = []
        writer._persist = AsyncMock(side_effect=lambda payload: persisted.append(payload))

        await writer.submit(text_chunk("x" * TEXT_FLUSH_CHARACTER_LIMIT))
        await writer.close()

        self.assertEqual(len(persisted), 1)
        self.assertEqual(len(persisted[0]["delta"]), TEXT_FLUSH_CHARACTER_LIMIT)

    async def test_flushes_text_before_node_end_and_terminal_event(self):
        """阶段和终态事件不得越过尚未落库的文字。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        persisted = []
        writer._persist = AsyncMock(side_effect=lambda payload: persisted.append(payload))

        await writer.submit(text_chunk("draft"))
        await writer.submit({"type": "node_end", "step_id": "step-1"})
        await writer.submit({"type": "final_result", "data": {"summary": "draft"}})
        await writer.close()

        self.assertEqual(
            [payload["type"] for payload in persisted],
            ["text_delta", "node_end", "final_result"],
        )

    async def test_decision_chunks_are_persisted_as_decision_deltas(self):
        """公开决策增量必须在工具开始事件前按同一队列落库。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        persisted = []
        writer._persist = AsyncMock(side_effect=lambda payload: persisted.append(payload))

        await writer.submit(decision_chunk("连续数据"))
        await writer.submit({"type": "tool_call_start", "step_id": "step-1"})
        await writer.close()

        self.assertEqual(
            [payload["type"] for payload in persisted],
            ["decision_delta", "tool_call_start"],
        )
        self.assertEqual(persisted[0]["delta"], "连续数据")

    async def test_persisted_sequence_is_per_stream_and_not_token_sequence(self):
        """数据库 sequence 应按批次递增，不能沿用 token 序号。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        persisted = []
        writer._persist = AsyncMock(side_effect=lambda payload: persisted.append(payload))

        await writer.submit(text_chunk("a"))
        await writer.submit({"type": "progress", "step_id": "step-1"})
        second = text_chunk("b")
        second["sequence"] = 99
        await writer.submit(second)
        await writer.close()

        deltas = [payload for payload in persisted if payload["type"] == "text_delta"]
        self.assertEqual([payload["sequence"] for payload in deltas], [1, 2])

    async def test_abort_discards_buffer_and_is_idempotent(self):
        """取消后文字 buffer 不得再 flush，重复 cleanup 不得悬挂。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        persisted = []
        writer._persist = AsyncMock(side_effect=lambda payload: persisted.append(payload))

        await writer.submit(text_chunk("late text"))
        await writer.abort(JobExecutionRevoked("revoked"))
        await writer.abort(JobExecutionRevoked("revoked again"))

        self.assertEqual(persisted, [])
        self.assertTrue(writer.aborted)

    async def test_abort_surfaces_unexpected_consumer_error_and_is_repeatable(self):
        """consumer 的数据库异常不能被 abort 伪装成清理成功。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        writer._persist = AsyncMock(side_effect=RuntimeError("database write failed"))

        with self.assertRaisesRegex(RuntimeError, "database write failed"):
            await writer.submit({"type": "progress", "step_id": "step-1"})
        with self.assertRaisesRegex(RuntimeError, "database write failed"):
            await writer.abort()
        with self.assertRaisesRegex(RuntimeError, "database write failed"):
            await writer.abort()

        self.assertTrue(writer.task.done())

    async def test_abort_preserves_consumer_cancellation(self):
        """consumer 被取消时，abort 不能把 CancelledError 伪装成成功。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        started = asyncio.Event()
        blocked = asyncio.Event()

        async def persist(_payload):
            started.set()
            await blocked.wait()

        writer._persist = persist
        submit_task = asyncio.create_task(
            writer.submit({"type": "progress", "step_id": "step-1"})
        )
        await started.wait()
        writer.task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await writer.abort()
        with self.assertRaises(asyncio.CancelledError):
            await submit_task

    async def test_fenced_error_does_not_mark_terminal_seen(self):
        """canceled fencing 拒绝 error 终态时，terminal_seen 必须保持 False。"""
        writer = OrderedEventWriter(build_job(), "worker-a")
        with patch(
            "app.agent.job_service.fail_job",
            return_value=FailJobResult.CANCELED_FENCED,
        ):
            with self.assertRaises(JobExecutionRevoked):
                await writer.submit(
                    {
                        "type": "error",
                        "message": "迟到错误",
                        "_diagnostic": classify_graph_failure(RuntimeError("boom")),
                    }
                )

        self.assertFalse(writer.terminal_seen)
        self.assertIsNone(writer.terminal_type)
        self.assertIsNone(writer.terminal_diagnostic)
        await writer.abort(JobExecutionRevoked("cancelled"))

    async def test_terminal_type_distinguishes_final_interrupt_and_error(self):
        """worker 结果日志必须能区分成功、等待输入和错误终态。"""
        for event_type in ("final_result", "interrupt"):
            writer = OrderedEventWriter(build_job(), "worker-a")
            with patch(
                "app.agent.worker.event_writer._complete_terminal_event",
                new=AsyncMock(return_value=True),
            ):
                await writer.submit({"type": event_type, "data": {}})
                await writer.close()
            self.assertTrue(writer.terminal_seen)
            self.assertEqual(writer.terminal_type, event_type)

        writer = OrderedEventWriter(build_job(), "worker-a")
        with patch(
            "app.agent.job_service.fail_job",
            return_value=FailJobResult.APPLIED,
        ):
            await writer.submit({"type": "error", "message": "safe public error"})
            await writer.close()
        self.assertTrue(writer.terminal_seen)
        self.assertEqual(writer.terminal_type, "error")

    async def test_error_terminal_keeps_diagnostic_in_memory_without_persisting_it(self):
        """内部诊断只挂在 writer 上供日志使用，落库仍只有脱敏文案。"""
        error = ConnectionRefusedError("db.internal:5432 refused")
        writer = OrderedEventWriter(build_job(), "worker-a")
        fail_job = Mock(return_value=FailJobResult.APPLIED)
        with patch("app.agent.job_service.fail_job", fail_job):
            await writer.submit(
                {
                    "type": "error",
                    "message": "节点执行失败",
                    "attempt": 2,
                    "_diagnostic": classify_graph_failure(error),
                }
            )
            await writer.close()

        fail_job.assert_called_once()
        self.assertEqual(fail_job.call_args.args[3], "节点执行失败")
        self.assertNotIn("db.internal", repr(fail_job.call_args))

        diagnostic = writer.terminal_diagnostic
        self.assertEqual(diagnostic.error_category, "provider_error")
        self.assertEqual(diagnostic.reason_code, "connection_unavailable")
        self.assertIs(diagnostic.exc_info[1], error)


if __name__ == "__main__":
    unittest.main()
