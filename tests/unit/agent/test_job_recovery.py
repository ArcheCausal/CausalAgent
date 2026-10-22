import os
import unittest
from unittest.mock import patch


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

from app.agent.job_service import (  # noqa: E402
    JobStateConflictError,
    _CheckpointRecoveryBlocked,
    claim_next_job,
    cancel_job,
    resume_job,
    write_event,
)


RESUME_KEY = "123e4567-e89b-42d3-a456-426614174001"
CANCEL_KEY = "123e4567-e89b-42d3-a456-426614174002"


class ScriptedCursor:
    """按 SQL 顺序返回恢复/取消事务所需的最小数据库结果。"""

    def __init__(self, fetch_results, *, rowcount=1, lastrowid=42):
        self.fetch_results = list(fetch_results)
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.statements = []

    def execute(self, sql, params=None):
        """记录 SQL，不模拟数据库查询规划。"""
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        """按预设顺序返回单行。"""
        statement = self.statements[-1][0] if self.statements else ""
        if "message_count" in statement:
            return self.fetch_results.pop(0) if self.fetch_results else None
        if "FROM sessions" in statement:
            return {"id": "session-1"}
        return self.fetch_results.pop(0) if self.fetch_results else None

    def fetchall(self):
        """恢复测试不需要多行读取。"""
        return []


class ScriptedConnection:
    """提供事务上下文、提交回滚和游标记录能力。"""

    def __init__(self, fetch_results, *, rowcount=1):
        self.cursor_value = ScriptedCursor(fetch_results, rowcount=rowcount)
        self.commits = 0
        self.rollbacks = 0
        self.started_transactions = 0

    def __enter__(self):
        """进入连接上下文。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """保留异常给调用方。"""
        return False

    def cursor(self, **_kwargs):
        """返回脚本游标。"""
        return self.cursor_value

    def start_transaction(self, **_kwargs):
        """记录事务开始。"""
        self.started_transactions += 1

    def commit(self):
        """记录提交。"""
        self.commits += 1

    def rollback(self):
        """记录回滚。"""
        self.rollbacks += 1

    def close(self):
        """兼容显式关闭连接的 worker 路径。"""


def _waiting_job():
    """构造等待用户输入的最小 Job。"""
    return {
        "job_id": "job-waiting",
        "user_id": 7,
        "session_id": "session-1",
        "status": "waiting_input",
        "current_question_id": "question-1",
        "attempt_count": 1,
        "lease_epoch": 2,
    }


class JobRecoveryTests(unittest.TestCase):
    """验证 stale recovery 与同一 Job 的恢复/取消事务边界。"""

    def test_stale_pending_interrupt_is_repaired_without_reclaiming_agent(self):
        """checkpoint 仍有 interrupt 时只修复 MySQL waiting_input，不启动新 attempt。"""
        stale_job = {
            "job_id": "job-stale",
            "user_id": 7,
            "session_id": "session-1",
            "status": "running",
            "attempt_count": 1,
            "lease_epoch": 2,
        }
        connection = ScriptedConnection([None, stale_job])
        pending = {"question_id": "question-2", "message": "请补充目标变量"}

        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch(
                "app.agent.job_service._get_pending_checkpoint_interrupt",
                return_value=pending,
            ),
        ):
            claimed = claim_next_job("worker-new", stale_after_seconds=120)

        self.assertIsNone(claimed)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        sql = [statement for statement, _params in connection.cursor_value.statements]
        self.assertTrue(any("SET status = 'waiting_input'" in statement for statement in sql))
        self.assertFalse(any("SET status = 'running'" in statement for statement in sql))
        self.assertTrue(any("event_key" in statement for statement in sql))

    def test_checkpoint_unavailable_rolls_back_stale_recovery(self):
        """无法确认 PostgreSQL 恢复位置时不得误重放或误标记失败。"""
        stale_job = {
            "job_id": "job-stale",
            "user_id": 7,
            "session_id": "session-1",
            "status": "running",
            "attempt_count": 1,
            "lease_epoch": 2,
        }
        connection = ScriptedConnection([None, stale_job])

        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch(
                "app.agent.job_service._get_pending_checkpoint_interrupt",
                side_effect=_CheckpointRecoveryBlocked("checkpoint unavailable"),
            ),
        ):
            claimed = claim_next_job("worker-new", stale_after_seconds=120)

        self.assertIsNone(claimed)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        sql = [statement for statement, _params in connection.cursor_value.statements]
        self.assertFalse(any("SET status = 'running'" in statement for statement in sql))

    def test_old_worker_with_wrong_lease_epoch_cannot_write_event(self):
        """同一 worker 名称重复出现时，旧 lease_epoch 也必须被拒绝。"""
        connection = ScriptedConnection([
            {
                "status": "running",
                "worker_id": "worker-a",
                "attempt_count": 2,
                "lease_epoch": 9,
            }
        ])

        with patch("app.agent.job_service.get_write_connection", return_value=connection):
            event_id = write_event(
                "job-1",
                "worker-a",
                2,
                "progress",
                {"value": 1},
                lease_epoch=8,
            )

        self.assertIsNone(event_id)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(
            any("INSERT INTO analysis_job_events" in sql for sql, _ in connection.cursor_value.statements)
        )

    def test_resume_writes_input_ledger_and_requeues_same_job(self):
        """恢复请求应追加 resume 输入、聊天关联并保留原 Job。"""
        job = _waiting_job()
        refreshed = {**job, "status": "queued", "resume_count": 1}
        connection = ScriptedConnection([
            job,
            None,
            {"next_sequence": 1},
            {"message_count": 1, "title": "分析"},
        ])

        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch("app.agent.job_service.get_job_for_user", return_value=refreshed),
        ):
            result, existing = resume_job(
                7,
                "job-waiting",
                "question-1",
                "目标变量是销售额",
                RESUME_KEY,
            )

        self.assertEqual(result, refreshed)
        self.assertFalse(existing)
        self.assertEqual(connection.commits, 1)
        sql = [statement for statement, _params in connection.cursor_value.statements]
        self.assertTrue(any("input_type, input_text, question_id" in statement for statement in sql))
        self.assertTrue(any("SET status = 'queued'" in statement for statement in sql))

    def test_resume_rejects_wrong_question_id_without_mutation(self):
        """恢复必须绑定当前 interrupt 的 question_id。"""
        connection = ScriptedConnection([_waiting_job(), None])

        with patch("app.agent.job_service.get_write_connection", return_value=connection):
            with self.assertRaises(JobStateConflictError):
                resume_job(
                    7,
                    "job-waiting",
                    "question-other",
                    "回答",
                    RESUME_KEY,
                )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_cancel_writes_terminal_event_and_releases_active_session(self):
        """取消等待任务应写 canceled 事件、聊天并清理 active_session_key。"""
        job = _waiting_job()
        refreshed = {**job, "status": "canceled"}
        connection = ScriptedConnection([
            job,
            None,
            None,
            {"input_id": 42},
            None,
            {"id": "session-1"},
        ])

        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch("app.agent.job_service.get_job_for_user", return_value=refreshed),
        ):
            result, existing = cancel_job(7, "job-waiting", CANCEL_KEY)

        self.assertEqual(result, refreshed)
        self.assertFalse(existing)
        self.assertEqual(connection.commits, 1)
        sql = [statement for statement, _params in connection.cursor_value.statements]
        parameter_sets = [params for _statement, params in connection.cursor_value.statements]
        self.assertTrue(
            any(values and "terminal:canceled" in values for values in parameter_sets)
        )
        cancel_updates = [
            statement for statement in sql
            if "UPDATE analysis_jobs" in statement and "cancel_idempotency_key = %s" in statement
        ]
        self.assertEqual(len(cancel_updates), 1)
        assignments = cancel_updates[0].split("SET", 1)[1].split("WHERE", 1)[0]
        self.assertTrue(assignments.rstrip().endswith("status = 'canceled'"))
        self.assertIn("active_session_key = NULL", assignments)
        self.assertIn("WHEN status = 'running' THEN 'draining'", assignments)
        self.assertTrue(any("active_session_key = NULL" in statement for statement in sql))

    def test_cancel_running_job_keeps_draining_execution_ownership(self):
        """运行中取消必须立即终止业务状态，但保留原 worker 的 draining 身份。"""
        job = {
            "job_id": "job-running",
            "user_id": 7,
            "session_id": "session-1",
            "status": "running",
            "execution_state": "leased",
            "worker_id": "worker-a",
            "attempt_count": 2,
            "lease_epoch": 9,
        }
        refreshed = {**job, "status": "canceled", "execution_state": "draining"}
        connection = ScriptedConnection([job, None, None, {"input_id": 42}, None])

        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch("app.agent.job_service.get_job_for_user", return_value=refreshed),
        ):
            result, existing = cancel_job(7, "job-running", CANCEL_KEY)

        self.assertEqual(result, refreshed)
        self.assertFalse(existing)
        self.assertEqual(connection.commits, 1)
        sql = [statement for statement, _params in connection.cursor_value.statements]
        self.assertTrue(any("WHEN status = 'running' THEN 'draining'" in statement for statement in sql))
        self.assertTrue(any("WHEN status = 'running' THEN worker_id" in statement for statement in sql))


if __name__ == "__main__":
    unittest.main()
