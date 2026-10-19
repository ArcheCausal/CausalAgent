"""五分钟等待确认的 Job 状态与公开事件语义测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.agent import job_service


def _waiting_job(expires_at: datetime | None) -> dict:
    return {
        "job_id": "job-waiting",
        "user_id": 7,
        "session_id": "session-1",
        "status": "waiting_input",
        "attempt_count": 1,
        "input_wait_expires_at": expires_at,
    }


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rowcount = 1
        self.lastrowid = 1
        self.parameters: list[tuple] = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        self.parameters.append(tuple(params or ()))

    def fetchone(self):
        return {"id": 1, "input_id": 1}

    def fetchall(self):
        return []


class InputWaitTimeoutTests(unittest.TestCase):
    def test_expired_wait_writes_timeout_terminal_event(self) -> None:
        cursor = _Cursor()
        job = _waiting_job(datetime.now(timezone.utc) - timedelta(seconds=1))
        with patch.object(job_service, "_insert_event", return_value=42) as insert:
            applied = job_service._expire_job_in_transaction(
                cursor,
                job_id="job-waiting",
                job=job,
            )

        self.assertTrue(applied)
        insert.assert_called_once()
        payload = insert.call_args.kwargs["payload"]
        self.assertEqual(payload["message"], job_service.INPUT_WAIT_TIMEOUT_MESSAGE)
        self.assertEqual(payload["reason_code"], "human_input_timeout")
        self.assertTrue(
            any("status = 'failed'" in statement for statement in cursor.statements)
        )
        self.assertTrue(
            any("execution_release_reason = 'input_timeout'" in statement for statement in cursor.statements)
        )

    def test_active_wait_is_not_expired(self) -> None:
        cursor = _Cursor()
        job = _waiting_job(datetime.now(timezone.utc) + timedelta(seconds=120))
        self.assertFalse(
            job_service._expire_job_in_transaction(
                cursor,
                job_id="job-waiting",
                job=job,
            )
        )
        self.assertFalse(any("status = 'failed'" in item for item in cursor.statements))

    def test_legacy_wait_without_deadline_gets_full_window_only(self) -> None:
        cursor = _Cursor()
        job = _waiting_job(None)
        self.assertFalse(
            job_service._expire_job_in_transaction(
                cursor,
                job_id="job-waiting",
                job=job,
            )
        )
        self.assertTrue(
            any(
                "input_wait_expires_at IS NULL" in statement
                for statement in cursor.statements
            )
        )
        self.assertFalse(any("status = 'failed'" in item for item in cursor.statements))

    def test_resume_does_not_extend_deadline(self) -> None:
        """resume 只清理等待字段，不能重新设置截止时间。"""

        import inspect

        source = inspect.getsource(job_service.resume_job)
        self.assertNotIn("INTERVAL", source)
        self.assertIn("input_wait_expires_at = NULL", source)
