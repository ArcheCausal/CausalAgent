"""验证 run_job 在失败/取消竞态和 cleanup fencing 下的收敛。"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch


for _key, _value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "test-database",
}.items():
    os.environ.setdefault(_key, _value)

from app.agent.job_service import FailJobResult  # noqa: E402
from app.agent.worker import execution  # noqa: E402
from app.agent.worker.event_adapter import (  # noqa: E402
    classify_graph_failure,
    sanitize_public_error,
)
from app.agent.worker.execution_guard import JobExecutionRevoked  # noqa: E402
from observability.logging_runtime import current_log_context  # noqa: E402


def _job() -> dict:
    """构造当前 worker lease 的最小 Job。"""
    return {
        "job_id": "job-1",
        "request_id": "request-1",
        "user_id": 7,
        "session_id": "session-1",
        "status": "running",
        "execution_state": "leased",
        "attempt_count": 2,
        "lease_epoch": 9,
        "claim_kind": "initial",
    }


class FakeWriter:
    """只记录 run_job 需要的 writer cleanup 行为。"""

    def __init__(self, job, worker_id, execution_guard=None):
        self.terminal_seen = False
        self.terminal_type = None
        self.terminal_diagnostic = None
        self.abort_calls = []
        self.close_calls = 0

    async def submit(self, payload):
        raise AssertionError("graph failure test should fail before submitting an event")

    async def close(self):
        self.close_calls += 1

    async def abort(self, error=None):
        self.abort_calls.append(error)


class FailingCleanupWriter(FakeWriter):
    """模拟 consumer 已经失败，abort 必须报告 cleanup failure。"""

    async def abort(self, error=None):
        self.abort_calls.append(error)
        raise RuntimeError("event consumer cleanup failed")


class TerminalWriter(FakeWriter):
    """根据 graph 终态设置只读终态类型，并同步内部诊断。"""

    async def submit(self, payload):
        if payload.get("type") in {"final_result", "interrupt", "error"}:
            self.terminal_seen = True
            self.terminal_type = payload["type"]
            self.terminal_diagnostic = payload.get("_diagnostic")


async def _failing_graph():
    """产生一个没有事件输出的 graph 异常。"""
    raise RuntimeError("graph failed")
    yield  # pragma: no cover


async def _blocking_graph():
    """保持 graph 调用悬挂，供 task-level CancelledError 测试 cleanup。"""
    await asyncio.Event().wait()
    yield  # pragma: no cover


async def _terminal_graph(event_type: str):
    yield {"type": event_type, "data": {"type": "text", "summary": "safe"}}


async def _failing_terminal_graph(error: BaseException):
    """产生带内部诊断的 graph error 终态，复现真实的 graph_runner 输出。"""
    try:
        raise error
    except Exception as exc:  # noqa: BLE001 - 复现 graph_runner 的 except Exception
        yield {
            "type": "error",
            "message": sanitize_public_error(exc),
            "attempt": 2,
            "_diagnostic": classify_graph_failure(exc),
        }


class WorkerExecutionTests(IsolatedAsyncioTestCase):
    """验证 run_job 不会在取消获胜后再次写普通失败。"""

    def _patch_common(self, writer_cls):
        writer = writer_cls(_job(), "worker-a")
        patches = (
            patch("app.agent.worker.execution.OrderedEventWriter", return_value=writer),
            patch("app.agent.worker.execution.ai_call_stream", return_value=_failing_graph()),
            patch(
                "app.agent.worker.execution.heartbeat_until_stopped",
                new=AsyncMock(),
            ),
            patch(
                "app.agent.worker.execution.JobExecutionGuard.ensure_active",
                new=AsyncMock(),
            ),
            patch(
                "app.agent.worker.execution.JobExecutionGuard.check_after_call",
                new=AsyncMock(),
            ),
            patch(
                "app.agent.worker.execution.job_service.get_latest_input_value",
                return_value={
                    "input_type": "initial",
                    "runtime_value": "hello",
                    "stored_text": "hello",
                    "chat_message_id": 1,
                },
            ),
        )
        return writer, patches

    async def test_canceled_fencing_does_not_call_fail_job_again(self):
        """graph 异常后若 cancel 先提交，run_job 只走撤销释放路径。"""
        writer, patches = self._patch_common(FakeWriter)
        fail_job = Mock(return_value=FailJobResult.CANCELED_FENCED)
        release = Mock(return_value=True)
        observed = []
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
            "app.agent.worker.execution.job_service.fail_job",
            fail_job,
        ), patch(
            "app.agent.worker.execution.job_service.release_execution_ownership",
            release,
        ), patch(
            "app.agent.worker.execution.log_event",
            side_effect=lambda _logger, event_code, **kwargs: observed.append(
                (event_code, kwargs)
            ),
        ):
            await execution.run_job(_job(), SimpleNamespace(graph=object(), mcp_tools=[]), "worker-a")

        fail_job.assert_called_once()
        self.assertEqual(len(writer.abort_calls), 1)
        release.assert_called_once_with("job-1", "worker-a", 2, 9)
        revoked = [item for item in observed if item[0] == "worker.job.revoked"]
        self.assertEqual(len(revoked), 1)
        self.assertEqual(revoked[0][1]["details"]["reason_code"], "canceled")

    async def test_cleanup_failure_keeps_draining_without_worker_confirmed_release(self):
        """EventWriter cleanup 失败时不能伪造 worker_confirmed 释放。"""
        writer, patches = self._patch_common(FailingCleanupWriter)
        fail_job = Mock(return_value=FailJobResult.CANCELED_FENCED)
        release = Mock(return_value=True)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
            "app.agent.worker.execution.job_service.fail_job",
            fail_job,
        ), patch(
            "app.agent.worker.execution.job_service.release_execution_ownership",
            release,
        ):
            await execution.run_job(_job(), SimpleNamespace(graph=object(), mcp_tools=[]), "worker-a")

        fail_job.assert_called_once()
        self.assertEqual(len(writer.abort_calls), 1)
        release.assert_not_called()

    async def test_task_cancellation_aborts_writer_before_release(self):
        """worker task 被取消时也必须结束 writer，再条件释放执行占用。"""
        writer, patches = self._patch_common(FakeWriter)
        patches = list(patches)
        patches[1] = patch(
            "app.agent.worker.execution.ai_call_stream",
            return_value=_blocking_graph(),
        )
        release = Mock(return_value=True)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
            "app.agent.worker.execution.job_service.release_execution_ownership",
            release,
        ):
            task = asyncio.create_task(
                execution.run_job(
                    _job(),
                    SimpleNamespace(graph=object(), mcp_tools=[]),
                    "worker-a",
                )
            )
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(len(writer.abort_calls), 1)
        release.assert_called_once_with("job-1", "worker-a", 2, 9)

    async def test_final_interrupt_and_error_each_emit_one_job_result_with_full_context(self):
        expectations = {
            "final_result": "worker.job.finished",
            "interrupt": "worker.job.interrupted",
            "error": "worker.job.failed",
        }
        for terminal_type, expected_code in expectations.items():
            writer = TerminalWriter(_job(), "worker-a")
            observed = []

            def capture(_logger, event_code, **kwargs):
                observed.append((event_code, kwargs, current_log_context()))

            with patch(
                "app.agent.worker.execution.OrderedEventWriter",
                return_value=writer,
            ), patch(
                "app.agent.worker.execution.ai_call_stream",
                return_value=_terminal_graph(terminal_type),
            ), patch(
                "app.agent.worker.execution.heartbeat_until_stopped",
                new=AsyncMock(),
            ), patch(
                "app.agent.worker.execution.JobExecutionGuard.ensure_active",
                new=AsyncMock(),
            ), patch(
                "app.agent.worker.execution.JobExecutionGuard.check_after_call",
                new=AsyncMock(),
            ), patch(
                "app.agent.worker.execution.job_service.get_latest_input_value",
                return_value={
                    "input_type": "initial",
                    "runtime_value": "hello",
                    "stored_text": "hello",
                    "chat_message_id": 1,
                },
            ), patch(
                "app.agent.worker.execution.log_event",
                side_effect=capture,
            ):
                await execution.run_job(
                    _job(),
                    SimpleNamespace(graph=object(), mcp_tools=[]),
                    "host:4",
                    worker_slot=4,
                )

            codes = [event[0] for event in observed]
            self.assertEqual(codes.count("worker.job.claimed"), 1)
            self.assertEqual(codes.count(expected_code), 1)
            self.assertEqual(len([code for code in codes if code.startswith("worker.job.")]), 2)
            for _code, _kwargs, context in observed:
                self.assertEqual(context["request_id"], "request-1")
                self.assertEqual(context["user_id"], "7")
                self.assertEqual(context["session_id"], "session-1")
                self.assertEqual(context["job_id"], "job-1")
                self.assertEqual(context["worker_slot"], "4")
            self.assertEqual(current_log_context(), {})

    async def _run_with_graph(self, graph_source, writer_cls=TerminalWriter):
        """在完整 patch 环境下跑一次 run_job，返回捕获到的日志调用。"""
        writer = writer_cls(_job(), "worker-a")
        observed = []

        def capture(_logger, event_code, **kwargs):
            observed.append((event_code, kwargs))

        with patch(
            "app.agent.worker.execution.OrderedEventWriter",
            return_value=writer,
        ), patch(
            "app.agent.worker.execution.ai_call_stream",
            return_value=graph_source,
        ), patch(
            "app.agent.worker.execution.heartbeat_until_stopped",
            new=AsyncMock(),
        ), patch(
            "app.agent.worker.execution.JobExecutionGuard.ensure_active",
            new=AsyncMock(),
        ), patch(
            "app.agent.worker.execution.JobExecutionGuard.check_after_call",
            new=AsyncMock(),
        ), patch(
            "app.agent.worker.execution.job_service.get_latest_input_value",
            return_value={
                "input_type": "initial",
                "runtime_value": "hello",
                "stored_text": "hello",
                "chat_message_id": 1,
            },
        ), patch(
            "app.agent.worker.execution.log_event",
            side_effect=capture,
        ):
            await execution.run_job(
                _job(),
                SimpleNamespace(graph=object(), mcp_tools=[]),
                "host:4",
                worker_slot=4,
            )
        return observed

    async def test_graph_terminal_error_logs_classification_and_exception_metadata(self):
        """graph 终态失败必须带上稳定故障域、真实原因码和异常元数据。"""
        error = ConnectionRefusedError("mysql://secret-host refused")
        observed = await self._run_with_graph(_failing_terminal_graph(error))

        failures = [item for item in observed if item[0] == "worker.job.failed"]
        self.assertEqual(len(failures), 1)
        _code, kwargs = failures[0]

        details = kwargs["details"]
        self.assertEqual(details["failure_phase"], "graph_terminal")
        self.assertEqual(details["reason_code"], "connection_unavailable")
        self.assertEqual(details["error_category"], "provider_error")
        self.assertEqual(details["attempt"], 2)
        self.assertNotIn("secret-host", repr(details))

        exc_info = kwargs["exc_info"]
        self.assertIs(exc_info[0], ConnectionRefusedError)
        self.assertIs(exc_info[1], error)
        self.assertIsNotNone(exc_info[2])

    async def test_graph_terminal_error_without_diagnostic_keeps_legacy_shape(self):
        """没有内部诊断时保持既有 reason_code，且不写入 error_category 键。"""
        observed = await self._run_with_graph(_terminal_graph("error"))

        failures = [item for item in observed if item[0] == "worker.job.failed"]
        self.assertEqual(len(failures), 1)
        _code, kwargs = failures[0]

        self.assertEqual(kwargs["details"]["reason_code"], "node_error")
        self.assertIsNone(kwargs["details"]["error_category"])
        self.assertIsNone(kwargs["exc_info"])

    async def test_two_jobs_in_different_slots_never_cross_log_context(self):
        jobs = []
        for index in (1, 2):
            job = _job()
            job.update({
                "job_id": f"job-{index}",
                "request_id": f"request-{index}",
                "user_id": index,
                "session_id": f"session-{index}",
            })
            jobs.append(job)
        observed = []

        def capture(_logger, event_code, **kwargs):
            observed.append((event_code, current_log_context().copy()))

        with patch(
            "app.agent.worker.execution.OrderedEventWriter",
            side_effect=lambda job, worker_id, execution_guard=None: TerminalWriter(
                job,
                worker_id,
                execution_guard,
            ),
        ), patch(
            "app.agent.worker.execution.ai_call_stream",
            side_effect=lambda *args, **kwargs: _terminal_graph("final_result"),
        ), patch(
            "app.agent.worker.execution.heartbeat_until_stopped",
            new=AsyncMock(),
        ), patch(
            "app.agent.worker.execution.JobExecutionGuard.ensure_active",
            new=AsyncMock(),
        ), patch(
            "app.agent.worker.execution.JobExecutionGuard.check_after_call",
            new=AsyncMock(),
        ), patch(
            "app.agent.worker.execution.job_service.get_latest_input_value",
            return_value={
                "input_type": "initial",
                "runtime_value": "safe",
                "stored_text": "safe",
                "chat_message_id": 1,
            },
        ), patch(
            "app.agent.worker.execution.log_event",
            side_effect=capture,
        ):
            await asyncio.gather(
                execution.run_job(
                    jobs[0],
                    SimpleNamespace(graph=object(), mcp_tools=[]),
                    "host:1",
                    worker_slot=1,
                ),
                execution.run_job(
                    jobs[1],
                    SimpleNamespace(graph=object(), mcp_tools=[]),
                    "host:2",
                    worker_slot=2,
                ),
            )

        by_job = {}
        for event_code, context in observed:
            self.assertIn(event_code, {"worker.job.claimed", "worker.job.finished"})
            by_job.setdefault(context["job_id"], []).append(context)
        self.assertEqual(set(by_job), {"job-1", "job-2"})
        for index in (1, 2):
            contexts = by_job[f"job-{index}"]
            self.assertEqual(len(contexts), 2)
            for context in contexts:
                self.assertEqual(context["request_id"], f"request-{index}")
                self.assertEqual(context["user_id"], str(index))
                self.assertEqual(context["session_id"], f"session-{index}")
                self.assertEqual(context["worker_slot"], str(index))
        self.assertEqual(current_log_context(), {})
