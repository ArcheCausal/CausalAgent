"""分析上下文服务的事务语义与归属校验单元测试。"""

from __future__ import annotations

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

from Database.analysis_contexts import (  # noqa: E402
    AnalysisContextFencedError,
    switch_to_context,
)


JOB_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
SESSION_ID = "b1049e68-0826-4e40-b8a3-026ce9faca9c"
CONTEXT_ID = "ac73b767-bfc2-45ca-9ff1-628fd1e47bb8"


def _job_row(**overrides):
    row = {
        "job_id": JOB_ID,
        "user_id": 7,
        "session_id": SESSION_ID,
        "status": "running",
        "worker_id": "worker-a",
        "attempt_count": 1,
        "lease_epoch": 3,
        "execution_state": "leased",
        "input_user_file_id": 11,
        "input_object_id": 22,
        "input_file_hash": "a" * 64,
        "input_filename": "data.csv",
        "analysis_context_id": None,
    }
    row.update(overrides)
    return row


def _context_row(**overrides):
    row = {
        "analysis_context_id": CONTEXT_ID,
        "session_id": SESSION_ID,
        "user_id": 7,
        "status": "active",
        "input_user_file_id": 11,
        "file_object_id": 22,
        "file_hash": "a" * 64,
        "filename": "data.csv",
        "target": None,
        "treatment": None,
        "analysis_question": None,
        "latest_algorithm_summary": None,
        "latest_rag_evidence": None,
        "latest_web_evidence": None,
        "latest_report_message_id": None,
        "latest_report_id": None,
        "updated_at": "2026-09-20T07:41:22.834414",
    }
    row.update(overrides)
    return row


class ScriptedCursor:
    """按语句内容返回固定结果，并记录本次事务里的全部 SQL。"""

    def __init__(self, *, job_row, session_row, context_row, sessions_update_rowcount=1):
        self.job_row = job_row
        self.session_row = session_row
        self.context_row = context_row
        self.sessions_update_rowcount = sessions_update_rowcount
        self.rowcount = 1
        self.statements = []
        self._last = ""
        self.sessions_updates = 0
        self.last_sessions_rowcount = None

    def execute(self, sql, params=None):
        statement = " ".join(sql.split())
        self.statements.append((statement, params))
        self._last = statement
        self.rowcount = 1
        if statement.startswith("UPDATE sessions"):
            self.rowcount = self.sessions_update_rowcount
            self.sessions_updates += 1
            self.last_sessions_rowcount = self.sessions_update_rowcount

    def fetchone(self):
        statement = self._last
        if "FROM analysis_jobs" in statement:
            return self.job_row
        if "FROM sessions" in statement:
            return self.session_row
        if "FROM analysis_contexts" in statement:
            if "FOR UPDATE" in statement:
                return self.context_row
            return self.context_row
        if "FROM user_files" in statement:
            return {
                "user_file_id": 11,
                "object_id": 22,
                "file_hash": "b" * 64,
                "filename": "data.csv",
            }
        return None


class ScriptedConnection:
    """记录 commit/rollback，并提供带字典游标的 fake 连接。"""

    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **kwargs):
        return self.fake_cursor

    def start_transaction(self):
        """模拟开启事务。"""

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _switch(connection, **overrides):
    payload = {
        "user_id": 7,
        "session_id": SESSION_ID,
        "job_id": JOB_ID,
        "worker_id": "worker-a",
        "attempt_count": 1,
        "lease_epoch": 3,
        "context_id": CONTEXT_ID,
    }
    payload.update(overrides)
    with patch(
        "Database.analysis_contexts.get_write_connection",
        return_value=connection,
    ):
        return switch_to_context(**payload)


class SwitchToContextTests(unittest.TestCase):
    """验证切换事务的幂等写入、归属校验和 fencing。"""

    def test_idempotent_pointer_write_does_not_look_like_denied_access(self):
        """Session 指针本来就等于目标上下文时，rowcount 为 0 不代表越权。"""
        cursor = ScriptedCursor(
            job_row=_job_row(),
            session_row={"id": SESSION_ID, "active_analysis_context_id": CONTEXT_ID},
            context_row=_context_row(),
            sessions_update_rowcount=0,
        )
        connection = ScriptedConnection(cursor)

        result = _switch(connection)

        self.assertEqual(result["status"], "switched")
        self.assertEqual(result["context"]["analysis_context_id"], CONTEXT_ID)
        # 指针写入确实执行了，而且它的 rowcount 是 0（值未变化），
        # 这种情况下不能把写入当成越权。
        self.assertEqual(cursor.sessions_updates, 1)
        self.assertEqual(cursor.last_sessions_rowcount, 0)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)

    def test_context_of_another_session_is_rejected(self):
        """目标上下文属于其他会话时切换被拒绝，且不提交事务。"""
        cursor = ScriptedCursor(
            job_row=_job_row(),
            session_row={"id": SESSION_ID, "active_analysis_context_id": None},
            context_row=_context_row(session_id="other-session"),
        )
        connection = ScriptedConnection(cursor)

        with self.assertRaises(PermissionError):
            _switch(connection)

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_context_of_another_user_is_rejected(self):
        """目标上下文属于其他用户时切换被拒绝。"""
        cursor = ScriptedCursor(
            job_row=_job_row(),
            session_row={"id": SESSION_ID, "active_analysis_context_id": None},
            context_row=_context_row(user_id=99),
        )
        connection = ScriptedConnection(cursor)

        with self.assertRaises(PermissionError):
            _switch(connection)

        self.assertEqual(connection.commits, 0)

    def test_stale_lease_is_fenced(self):
        """worker 或 lease 不匹配时不允许写入上下文。"""
        cursor = ScriptedCursor(
            job_row=_job_row(lease_epoch=2),
            session_row={"id": SESSION_ID, "active_analysis_context_id": None},
            context_row=_context_row(),
        )
        connection = ScriptedConnection(cursor)

        with self.assertRaises(AnalysisContextFencedError):
            _switch(connection)

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
