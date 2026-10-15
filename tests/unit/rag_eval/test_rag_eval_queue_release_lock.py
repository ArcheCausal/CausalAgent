"""验证命名锁释放后其他 worker 能立即接管的单元测试"""
import unittest
from unittest.mock import patch

from app.rag_eval import job_service


class _Cursor:
    def __init__(self):
        self.statement = ""
        self.release_result_consumed = False

    def execute(self, statement, _params=None):
        self.statement = statement

    def fetchone(self):
        if "GET_LOCK" in self.statement:
            return (1,)
        if "RELEASE_LOCK" in self.statement:
            self.release_result_consumed = True
            return (1,)
        return None

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, **_kwargs):
        return self._cursor

    def start_transaction(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class RagEvalQueueReleaseLockTests(unittest.TestCase):
    def test_claim_consumes_release_lock_result_before_returning_connection(self):
        cursor = _Cursor()
        with patch.object(job_service, "get_write_connection", return_value=_Connection(cursor)):
            self.assertIsNone(job_service.claim_next_job("test-worker"))
        self.assertTrue(cursor.release_result_consumed)
