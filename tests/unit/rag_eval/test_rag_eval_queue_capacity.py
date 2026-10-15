import unittest
from unittest.mock import patch

from app.rag_eval import job_service
from config.settings import settings


class _FakeCursor:
    def __init__(self, queued=None, running=None, capacity=None):
        self.queued = queued or []
        self.running = running or []
        self.capacity = capacity or {}
        self.executed = []
        self.rowcount = 1
        self._one = None

    def execute(self, statement, params=None):
        self.executed.append((statement, params))
        normalized = " ".join(statement.split())
        if "GET_LOCK" in normalized:
            self._one = (1,)
        elif "SELECT job_kind, COUNT(*) AS running_count" in normalized:
            self._one = None
            self._all = self.running
        elif "WHERE status = 'queued'" in normalized and "FOR UPDATE SKIP LOCKED" in normalized:
            self._one = self.queued[0] if self.queued else None
        elif "SELECT status, job_kind, COUNT(*) AS count" in normalized:
            self._all = self.capacity.get("counts", [])
        elif "oldest_queued_age_seconds" in normalized:
            self._one = self.capacity.get("oldest")
        elif "stale_running_count" in normalized:
            self._one = self.capacity.get("stale")
        elif "RELEASE_LOCK" in normalized:
            self._one = (1,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return getattr(self, "_all", [])


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.started = False
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, **_kwargs):
        return self._cursor

    def start_transaction(self):
        self.started = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class RagEvalQueueCapacityTests(unittest.TestCase):
    def test_priorities_are_server_defined(self):
        self.assertGreater(job_service.job_priority("rag_query"), job_service.job_priority("evaluation"))
        self.assertGreater(job_service.job_priority("evaluation"), job_service.job_priority("ingestion"))

    def test_default_worker_and_kind_limits(self):
        self.assertEqual(settings.RAG_EVAL_EVALUATION_WORKERS, 5)
        self.assertEqual(job_service.job_limits()["evaluation"], 3)
        self.assertEqual(job_service.job_limits()["ingestion"], 1)

    def test_claim_orders_same_kind_by_priority_then_creation_and_id(self):
        cursor = _FakeCursor(queued=[{"id": 8, "job_kind": "evaluation", "attempt_count": 0, "payload_json": "{}"}])
        conn = _FakeConnection(cursor)
        with patch.object(job_service, "get_write_connection", return_value=conn):
            claimed = job_service.claim_next_job("worker-1")

        self.assertEqual(claimed["id"], 8)
        select = next(statement for statement, _ in cursor.executed if "FOR UPDATE SKIP LOCKED" in statement)
        self.assertIn("priority DESC, created_at ASC, id ASC", select)
        self.assertTrue(conn.started and conn.committed and conn.closed)

    def test_claim_skips_evaluation_when_its_limit_is_reached(self):
        cursor = _FakeCursor(
            queued=[],
            running=[{"job_kind": "evaluation", "running_count": 3}],
        )
        conn = _FakeConnection(cursor)
        with patch.object(job_service, "get_write_connection", return_value=conn):
            claimed = job_service.claim_next_job("worker-1")

        self.assertIsNone(claimed)
        select = next(statement for statement, _ in cursor.executed if "FOR UPDATE SKIP LOCKED" in statement)
        self.assertIn("job_kind NOT IN", select)

    def test_claim_prioritizes_eligible_rag_query_over_older_ingestion(self):
        cursor = _FakeCursor(queued=[{"id": 9, "job_kind": "rag_query", "attempt_count": 0, "payload_json": "{}"}])
        conn = _FakeConnection(cursor)
        with patch.object(job_service, "get_write_connection", return_value=conn):
            job_service.claim_next_job("worker-1")

        select = next(statement for statement, _ in cursor.executed if "FOR UPDATE SKIP LOCKED" in statement)
        self.assertIn("priority DESC, created_at ASC, id ASC", select)

    def test_enqueue_ignores_payload_priority(self):
        cursor = _FakeCursor()
        conn = _FakeConnection(cursor)
        with patch.object(job_service, "get_write_connection", return_value=conn), \
                patch.object(job_service, "get_job", return_value={"run_id": "run-1", "priority": 50}):
            job_service.enqueue_job("run-1", "evaluation", {"priority": 999})

        insert = next((statement, params) for statement, params in cursor.executed if "INSERT INTO rag_eval_jobs" in statement)
        self.assertEqual(insert[1][2], 50)

    def test_capacity_snapshot_reports_slots_counts_limits_and_oldest_age(self):
        cursor = _FakeCursor(
            capacity={
                "counts": [
                    {"status": "queued", "job_kind": "ingestion", "count": 1},
                    {"status": "running", "job_kind": "evaluation", "count": 2},
                ],
                "oldest": {"oldest_queued_age_seconds": 17},
                "stale": {"stale_running_count": 1, "oldest_heartbeat_age_seconds": 23},
            }
        )
        conn = _FakeConnection(cursor)
        with patch.object(job_service, "get_read_connection") as get_connection:
            get_connection.return_value.__enter__.return_value = conn
            snapshot = job_service.get_capacity_snapshot()

        self.assertEqual(snapshot["configured_slots"], 5)
        self.assertEqual(snapshot["running_total"], 2)
        self.assertEqual(snapshot["available_slots"], 3)
        self.assertEqual(snapshot["kinds"]["evaluation"]["limit"], 3)
        self.assertEqual(snapshot["oldest_queued_age_seconds"], 17)
        self.assertEqual(snapshot["stale_running"]["count"], 1)
        self.assertTrue(all("SELECT" in statement.upper() for statement, _ in cursor.executed))


if __name__ == "__main__":
    unittest.main()
