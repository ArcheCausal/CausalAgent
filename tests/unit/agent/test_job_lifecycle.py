import os
import json
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
    MAX_ATTEMPTS_ERROR,
    FailJobResult,
    claim_next_job,
    complete_job,
    complete_job_with_chat,
    fail_job,
    write_event,
)


class FakeCursor:
    """记录 SQL，并按顺序返回预设的锁定查询结果。"""

    def __init__(self, fetch_results=None, rowcount=1, lock_job=None, event=None):
        self.fetch_results = list(fetch_results or [])
        self.rowcount = rowcount
        self.lastrowid = 17
        self.lock_job = lock_job or {
            "status": "running",
            "worker_id": "worker-a",
            "attempt_count": 2,
            "lease_epoch": 1,
            "user_id": 7,
            "session_id": "session-1",
        }
        self.event = event
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        statement = self.statements[-1][0] if self.statements else ""
        if "FROM analysis_job_events" in statement and "event_key" in statement:
            return self.event
        if (
            "SELECT *" in statement
            and "FROM analysis_jobs" in statement
            and "FOR UPDATE" in statement
            and "SKIP LOCKED" not in statement
        ):
            return self.lock_job
        if "SELECT status, worker_id, attempt_count, lease_epoch" in statement:
            return self.lock_job
        if "FROM analysis_job_inputs" in statement:
            return None
        if "FROM chat_messages" in statement and "source_event_id" in statement:
            return None
        if "FROM sessions" in statement and "SELECT id" in statement:
            return {"id": "session-1"}
        return self.fetch_results.pop(0) if self.fetch_results else None


class FakeConnection:
    """模拟 worker 事务连接，并记录提交与回滚次数。"""

    def __init__(self, fetch_results=None, rowcount=1, fail_commit=False, lock_job=None, event=None):
        self.fake_cursor = FakeCursor(
            fetch_results,
            rowcount=rowcount,
            lock_job=lock_job,
            event=event,
        )
        self.started_transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_commit = fail_commit

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self, **kwargs):
        return self.fake_cursor

    def start_transaction(self):
        self.started_transactions += 1

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def build_job(attempt_count=2):
    """构造一个由指定 attempt 持有的最小 job。"""
    return {
        "job_id": "job-1",
        "user_id": 7,
        "session_id": "session-1",
        "message": "hello",
        "attempt_count": attempt_count,
    }


class JobLifecycleTests(unittest.TestCase):
    """验证 job 终态事务、租约 fencing 和重试耗尽收敛。"""

    def test_terminal_success_commits_chat_event_and_job_together(self):
        """成功终态必须先设置幂等标记，再在一次事务中写完所有数据。"""
        connection = FakeConnection(
            fetch_results=[],
        )

        with patch("app.agent.job_service.get_write_connection", return_value=connection):
            saved = complete_job_with_chat(
                build_job(),
                "worker-a",
                "final_result",
                {"type": "final_result", "data": {"summary": "done"}},
                {"type": "text", "summary": "done"},
                {"summary": "done"},
            )

        self.assertTrue(saved)
        self.assertEqual(connection.started_transactions, 1)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        sql = [statement for statement, _params in connection.fake_cursor.statements]
        chat_insert_index = next(i for i, statement in enumerate(sql) if "INSERT INTO chat_messages" in statement)
        event_index = next(i for i, statement in enumerate(sql) if "INSERT INTO analysis_job_events" in statement)
        status_index = next(i for i, statement in enumerate(sql) if "SET status = 'succeeded'" in statement)
        self.assertLess(event_index, status_index)
        self.assertLess(chat_insert_index, status_index)

    def test_terminal_commit_error_rolls_back_all_changes(self):
        """终态提交失败时必须回滚聊天、事件和 job 更新。"""
        connection = FakeConnection(
            fetch_results=[],
            fail_commit=True,
        )

        with patch("app.agent.job_service.get_write_connection", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                complete_job_with_chat(
                    build_job(),
                    "worker-a",
                    "final_result",
                    {"type": "final_result", "data": {"summary": "done"}},
                    {"type": "text", "summary": "done"},
                    {"summary": "done"},
                )

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 1)

    def test_existing_terminal_event_skips_duplicate_chat_rows(self):
        """同一终态事件重放时由 event_key 幂等，不再次写聊天。"""
        payload = {"type": "final_result", "data": {"summary": "done"}}
        connection = FakeConnection(
            event={"id": 99, "payload_json": json.dumps(payload)},
        )

        with patch("app.agent.job_service.get_write_connection", return_value=connection):
            saved = complete_job_with_chat(
                build_job(),
                "worker-a",
                "final_result",
                payload,
                {"type": "text", "summary": "done"},
                {"summary": "done"},
            )

        self.assertTrue(saved)
        self.assertEqual(connection.commits, 1)
        self.assertFalse(any("INSERT INTO chat_messages" in sql for sql, _ in connection.fake_cursor.statements))

    def test_stale_worker_cannot_write_event_or_fail_new_attempt(self):
        """旧 worker 的事件和失败更新都必须被当前租约检查拒绝。"""
        event_connection = FakeConnection(
            lock_job={
                "status": "running",
                "worker_id": "worker-new",
                "attempt_count": 3,
                "lease_epoch": 9,
            }
        )
        with patch("app.agent.job_service.get_write_connection", return_value=event_connection):
            event_id = write_event("job-1", "worker-old", 2, "progress", {"value": 1})

        self.assertIsNone(event_id)
        self.assertEqual(event_connection.commits, 0)
        self.assertEqual(event_connection.rollbacks, 1)
        self.assertFalse(any("INSERT INTO analysis_job_events" in sql for sql, _ in event_connection.fake_cursor.statements))

        fail_connection = FakeConnection(
            lock_job={
                "status": "running",
                "worker_id": "worker-new",
                "attempt_count": 3,
                "lease_epoch": 9,
            }
        )
        with (
            patch("app.agent.job_service.get_write_connection", return_value=fail_connection),
            patch("app.agent.job_service.get_job_by_id", return_value=build_job()),
        ):
            failed = fail_job("job-1", "worker-old", 2, "old failure")

        self.assertEqual(failed, FailJobResult.OTHER_FENCED)
        self.assertEqual(fail_connection.commits, 0)
        self.assertEqual(fail_connection.rollbacks, 1)
        self.assertFalse(any("SET status = 'failed'" in sql for sql, _ in fail_connection.fake_cursor.statements))

    def test_fail_job_reports_canceled_fencing_without_error_event(self):
        """取消事务先获胜时，失败路径只能报告 canceled fencing。"""
        connection = FakeConnection(
            lock_job={
                "job_id": "job-1",
                "status": "canceled",
                "execution_state": "draining",
                "worker_id": "worker-a",
                "attempt_count": 2,
                "lease_epoch": 9,
            }
        )

        with patch("app.agent.job_service.get_write_connection", return_value=connection):
            failed = fail_job(
                "job-1",
                "worker-a",
                2,
                "迟到的 graph failure",
                lease_epoch=9,
            )

        self.assertEqual(failed, FailJobResult.CANCELED_FENCED)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("INSERT INTO analysis_job_events" in sql for sql, _ in connection.fake_cursor.statements))

    def test_fail_job_applies_error_event_and_chat_after_locked_match(self):
        """当前 worker 获得 Job 行锁后，失败事件、聊天和 failed 状态同事务提交。"""
        connection = FakeConnection(
            lock_job={
                "job_id": "job-1",
                "user_id": 7,
                "session_id": "session-1",
                "status": "running",
                "execution_state": "leased",
                "worker_id": "worker-a",
                "attempt_count": 2,
                "lease_epoch": 9,
            }
        )

        with patch("app.agent.job_service.get_write_connection", return_value=connection):
            failed = fail_job(
                "job-1",
                "worker-a",
                2,
                "节点执行失败",
                lease_epoch=9,
            )

        self.assertEqual(failed, FailJobResult.APPLIED)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        sql = [statement for statement, _params in connection.fake_cursor.statements]
        self.assertTrue(any("INSERT INTO analysis_job_events" in statement for statement in sql))
        self.assertTrue(any("SET status = 'failed'" in statement for statement in sql))

    def test_complete_job_sql_requires_worker_and_attempt(self):
        """非聊天成功更新也必须在 SQL 层带上 worker 和 attempt fencing。"""
        connection = FakeConnection(
            rowcount=0,
            lock_job={
                "status": "running",
                "worker_id": "worker-new",
                "attempt_count": 3,
                "lease_epoch": 9,
            },
        )
        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch("app.agent.job_service.get_job_by_id", return_value=build_job()),
        ):
            completed = complete_job("job-1", "worker-old", 2, {"summary": "done"})

        self.assertFalse(completed)
        lock_sql, _params = next(
            (sql, params)
            for sql, params in connection.fake_cursor.statements
            if "SELECT status, worker_id, attempt_count, lease_epoch" in sql
        )
        self.assertIn("WHERE job_id = %s", lock_sql)
        self.assertEqual(connection.fake_cursor.statements[-1][0], lock_sql)

    def test_complete_job_attaches_usage_to_final_result_event(self):
        """兜底成功路径落库的 final_result 事件必须携带用量快照。"""
        usage = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
        connection = FakeConnection()

        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch("app.agent.job_service.get_job_by_id", return_value=build_job()),
        ):
            completed = complete_job(
                "job-1",
                "worker-a",
                2,
                {},
                lease_epoch=1,
                usage=usage,
            )

        self.assertTrue(completed)
        _sql, params = next(
            (sql, params)
            for sql, params in connection.fake_cursor.statements
            if "INSERT INTO analysis_job_events" in sql
        )
        payload = json.loads(params[3])
        self.assertEqual(payload["type"], "final_result")
        self.assertEqual(payload["usage"], usage)

    def test_claim_final_exhausted_job_as_failed_and_releases_session(self):
        """领取时应收敛过期且耗尽尝试次数的 running job，避免永久占用会话。"""
        connection = FakeConnection(
            fetch_results=[
                {
                    "job_id": "job-max",
                    "user_id": 7,
                    "session_id": "session-1",
                    "attempt_count": 3,
                    "lease_epoch": 4,
                },
                None,
            ]
        )

        with (
            patch("app.agent.job_service.get_write_connection", return_value=connection),
            patch("app.agent.job_service._get_pending_checkpoint_interrupt", return_value=None),
        ):
            claimed = claim_next_job("worker-a", stale_after_seconds=120)

        self.assertIsNone(claimed)
        self.assertEqual(connection.started_transactions, 1)
        self.assertEqual(connection.commits, 1)
        sql = [statement for statement, _params in connection.fake_cursor.statements]
        self.assertTrue(any("SET status = 'failed'" in statement for statement in sql))
        self.assertTrue(any("active_session_key = NULL" in statement for statement in sql))
        event_statement = next(statement for statement in sql if "INSERT INTO analysis_job_events" in statement)
        self.assertIn("INSERT INTO analysis_job_events", event_statement)
        self.assertIn("event_key", event_statement)
        update_params = next(params for statement, params in connection.fake_cursor.statements if "SET status = 'failed'" in statement)
        self.assertEqual(update_params[:2], (MAX_ATTEMPTS_ERROR, MAX_ATTEMPTS_ERROR))


if __name__ == "__main__":
    unittest.main()
