"""worker 领取/心跳/失败收敛逻辑的单元测试(用假游标模拟 MySQL,不连真库)"""
import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.rag_eval import isolated_runs
from app.rag_eval.isolated_runs import IsolatedRunManager
from app.rag_eval import worker


def _hammer_lock(root: str, run_id: str, iterations: int) -> None:
    """子进程：在跨进程文件锁内对 run.json 计数器做读-改-写。"""
    from app.rag_eval.isolated_runs import _run_dir, _run_file_lock, _write_json

    isolated_runs.ISOLATED_RUN_ROOT = Path(root)
    path = _run_dir(run_id) / "run.json"
    for _ in range(iterations):
        with _run_file_lock(run_id):
            state = json.loads(path.read_text(encoding="utf-8"))
            state["counter"] = state.get("counter", 0) + 1
            _write_json(path, state)


class _FakeJobService:
    def __init__(self):
        self.enqueued = []
        self.cancelled = []
        self.completed = []

    def enqueue_job(self, run_id, job_kind, payload):
        self.enqueued.append((run_id, job_kind, payload))
        return {"run_id": run_id, "job_kind": job_kind, "status": "queued"}

    def cancel_job(self, run_id):
        self.cancelled.append(run_id)
        return {"run_id": run_id, "status": "cancelled"}

    def complete_job(self, run_id):
        self.completed.append(run_id)
        return True

    def heartbeat_job(self, *_args):
        return True

    def get_job(self, run_id):
        return {"run_id": run_id, "status": "running"}

    def fail_job(self, *_args):
        raise AssertionError("unexpected failure")


class RagEvalWorkerQueueTests(unittest.TestCase):
    def test_worker_main_starts_configured_parallel_slots(self):
        threads = []

        class FakeThread:
            def __init__(self, *, target, args, daemon, name):
                self.target = target
                self.args = args
                self.daemon = daemon
                self.name = name
                self.started = False
                self.joined = False
                threads.append(self)

            def start(self):
                self.started = True

            def join(self):
                self.joined = True

        with patch.object(worker, "check_database_readiness"), \
                patch.object(worker.settings, "RAG_EVAL_EVALUATION_WORKERS", 3), \
                patch.object(worker.threading, "Thread", FakeThread):
            worker.main()

        self.assertEqual([thread.args for thread in threads], [(1,), (2,), (3,)])
        self.assertTrue(all(thread.started and thread.joined for thread in threads))

    def test_ingestion_creation_queues_without_starting_web_thread(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "sources"
            source_root.mkdir()
            source = source_root / "upload_a__source.txt"
            source.write_text("source", encoding="utf-8")
            fake_jobs = _FakeJobService()
            with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root / "runs"), \
                    patch("app.rag_eval.isolated_runs.RAG_EVAL_SOURCE_ROOT", source_root), \
                    patch(
                        "app.rag_eval.isolated_runs._resolve_source_inputs",
                        return_value=([source], ["upload_a"], ["测试来源"]),
                    ), \
                    patch("app.rag_eval.job_service.enqueue_job", fake_jobs.enqueue_job), \
                    patch("app.rag_eval.isolated_runs.threading.Thread") as thread:
                manager = IsolatedRunManager()
                state = manager.start_ingestion(source_ids=["upload_a"], max_pages=1)

            self.assertEqual(state["status"], "queued")
            self.assertEqual(state["execution_backend"], "persistent_worker")
            self.assertEqual(fake_jobs.enqueued[0][1], "ingestion")
            thread.assert_not_called()

    def test_ingestion_persists_explicit_remote_source_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "sources"
            source_root.mkdir()
            source = source_root / "upload_a__source.txt"
            source.write_text("source", encoding="utf-8")
            fake_jobs = _FakeJobService()
            with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root / "runs"), \
                    patch("app.rag_eval.isolated_runs.RAG_EVAL_SOURCE_ROOT", source_root), \
                    patch(
                        "app.rag_eval.isolated_runs._resolve_source_inputs",
                        return_value=([source], ["upload_a"], ["测试来源"]),
                    ), \
                    patch("app.rag_eval.isolated_runs._remote_data_enabled", return_value=True), \
                    patch("app.rag_eval.job_service.enqueue_job", fake_jobs.enqueue_job):
                manager = IsolatedRunManager()
                state = manager.start_ingestion(
                    source_ids=["upload_a"],
                    max_pages=1,
                    allow_remote_data=True,
                    authorized_source_ids=["upload_a"],
                )

            self.assertTrue(state["remote_enabled"])
            self.assertEqual(state["authorized_source_ids"], ["upload_a"])

    def test_candidate_creation_queues_without_starting_web_thread(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_jobs = _FakeJobService()
            manager = IsolatedRunManager()
            ingestion = {
                "run_id": "ingest_20260813_000000_abcdef1234",
                "kind": "ingestion",
                "status": "staged",
                "index_version": "mm_test",
                "collection_name": "isolated_mm_test",
                "unit_count": 48,
                "vector_count": 48,
                "source_ids": [],
            }
            with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root), \
                    patch.object(manager, "_validate_staged_index"), \
                    patch("app.rag_eval.job_service.enqueue_job", fake_jobs.enqueue_job), \
                    patch("app.rag_eval.isolated_runs.threading.Thread") as thread:
                manager._runs[ingestion["run_id"]] = ingestion
                state = manager.start_candidate_generation(ingestion["run_id"], "mm_test")

            self.assertEqual(state["status"], "queued")
            self.assertEqual(fake_jobs.enqueued[0][1], "candidate_generation")
            thread.assert_not_called()

    def test_candidate_creation_rejects_request_over_staged_unit_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_jobs = _FakeJobService()
            manager = IsolatedRunManager()
            ingestion = {
                "run_id": "ingest_20260813_000000_abcdef1234",
                "kind": "ingestion",
                "status": "staged",
                "index_version": "mm_test",
                "collection_name": "isolated_mm_test",
                "unit_count": 15,
                "vector_count": 15,
                "source_ids": [],
            }
            with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root), \
                    patch.object(manager, "_validate_staged_index"), \
                    patch("app.rag_eval.job_service.enqueue_job", fake_jobs.enqueue_job):
                manager._runs[ingestion["run_id"]] = ingestion
                with self.assertRaisesRegex(ValueError, "exceeds staged index capacity"):
                    manager.start_candidate_generation(ingestion["run_id"], "mm_test", question_count=16)

            self.assertEqual(fake_jobs.enqueued, [])

    def test_rag_query_creation_queues_without_starting_web_thread(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_jobs = _FakeJobService()
            manager = IsolatedRunManager()
            ingestion = {
                "run_id": "ingest_20260813_000000_abcdef1234",
                "kind": "ingestion",
                "status": "staged",
                "index_version": "mm_test",
                "collection_name": "isolated_mm_test",
                "vector_count": 1,
                "source_ids": [],
            }
            with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root), \
                    patch.object(manager, "_validate_staged_index"), \
                    patch("app.rag_eval.job_service.enqueue_job", fake_jobs.enqueue_job), \
                    patch("app.rag_eval.isolated_runs.threading.Thread") as thread:
                manager._runs[ingestion["run_id"]] = ingestion
                state = manager.start_rag_query(
                    ingestion["run_id"],
                    "mm_test",
                    [{"sample_id": "q-1", "question": "问题"}],
                )

            self.assertEqual(state["status"], "queued")
            self.assertEqual(state["execution_backend"], "persistent_worker")
            self.assertEqual(fake_jobs.enqueued[0][1], "rag_query")
            self.assertEqual(manager._load(state["run_id"])["questions"][0]["sample_id"], "q-1")
            thread.assert_not_called()
            with patch.object(manager, "_run_rag_query") as run_query:
                manager.run_queued_sync(state["run_id"])
            run_query.assert_called_once_with(
                state["run_id"],
                ingestion["run_id"],
                "mm_test",
                [{"sample_id": "q-1", "question": "问题"}],
            )

    def test_cancelled_queued_job_is_not_dispatched(self):
        fake_jobs = _FakeJobService()

        class FakeManager:
            def _load(self, _run_id):
                return {"status": "cancelled", "cancel_requested": True}

            def mark_worker_started(self, *_args):
                raise AssertionError("cancelled job must not start")

        with patch.object(worker, "job_service", fake_jobs):
            worker._run_one(FakeManager(), {"run_id": "ingest_20260813_000000_abcdef1234"}, "worker-1")

        self.assertEqual(fake_jobs.cancelled, ["ingest_20260813_000000_abcdef1234"])

    def test_ingestion_staged_is_treated_as_success(self):
        fake_jobs = _FakeJobService()

        class FakeManager:
            def _load(self, _run_id):
                return {
                    "run_id": "ingest_20260813_000000_abcdef1234",
                    "kind": "ingestion",
                    "status": "staged",
                    "execution_backend": "persistent_worker",
                }

            def mark_worker_started(self, *_args):
                return None

            def touch_worker_heartbeat(self, *_args):
                return None

            def run_queued_sync(self, _run_id):
                return None

            def mark_worker_fenced(self, *_args):
                raise AssertionError("staged ingestion must not be fenced")

            def mark_worker_timeout(self, *_args):
                raise AssertionError("staged ingestion must not be timed out")

        with patch.object(worker, "job_service", fake_jobs):
            worker._run_one(
                FakeManager(),
                {"run_id": "ingest_20260813_000000_abcdef1234"},
                "worker-1",
            )

        self.assertEqual(fake_jobs.completed, ["ingest_20260813_000000_abcdef1234"])
        self.assertEqual(fake_jobs.cancelled, [])

    def test_complete_false_with_sql_cancelled_converges_run_to_cancelled(self):
        run_id = "ingest_20260813_000000_abcdef1234"

        class FakeJobs:
            def complete_job(self, _run_id):
                return False

            def get_job(self, _run_id):
                return {"run_id": _run_id, "status": "cancelled"}

            def heartbeat_job(self, *_args):
                return True

        cancelled = []

        class FakeManager:
            def _load(self, _run_id):
                return {
                    "run_id": run_id,
                    "kind": "ingestion",
                    "status": "staged",
                    "execution_backend": "persistent_worker",
                }

            def mark_worker_started(self, *_args):
                return None

            def touch_worker_heartbeat(self, *_args):
                return None

            def run_queued_sync(self, _run_id):
                return None

            def mark_worker_cancelled(self, rid, _msg):
                cancelled.append(rid)

            def mark_worker_fenced(self, *_args):
                raise AssertionError("SQL cancelled must converge run to cancelled, not fenced")

            def mark_worker_timeout(self, *_args):
                raise AssertionError("must not timeout")

        with patch.object(worker, "job_service", FakeJobs()):
            worker._run_one(FakeManager(), {"run_id": run_id}, "worker-1")

        self.assertEqual(cancelled, [run_id])

    def test_complete_false_with_sql_failed_converges_run_to_failed(self):
        run_id = "ingest_20260813_000000_abcdef1234"

        class FakeJobs:
            def complete_job(self, _run_id):
                return False

            def get_job(self, _run_id):
                return {"run_id": _run_id, "status": "failed"}

            def heartbeat_job(self, *_args):
                return True

        fenced = []

        class FakeManager:
            def _load(self, _run_id):
                return {
                    "run_id": run_id,
                    "kind": "ingestion",
                    "status": "staged",
                    "execution_backend": "persistent_worker",
                }

            def mark_worker_started(self, *_args):
                return None

            def touch_worker_heartbeat(self, *_args):
                return None

            def run_queued_sync(self, _run_id):
                return None

            def mark_worker_fenced(self, rid, _msg):
                fenced.append(rid)

            def mark_worker_cancelled(self, *_args):
                raise AssertionError("SQL failed must converge run to failed, not cancelled")

            def mark_worker_timeout(self, *_args):
                raise AssertionError("must not timeout")

        with patch.object(worker, "job_service", FakeJobs()):
            worker._run_one(FakeManager(), {"run_id": run_id}, "worker-1")

        self.assertEqual(fenced, [run_id])

    def test_max_pages_with_empty_page_ranges_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "sources"
            source_root.mkdir()
            source = source_root / "upload_a__source.txt"
            source.write_text("source", encoding="utf-8")
            fake_jobs = _FakeJobService()
            with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root / "runs"), \
                    patch("app.rag_eval.isolated_runs.RAG_EVAL_SOURCE_ROOT", source_root), \
                    patch(
                        "app.rag_eval.isolated_runs._resolve_source_inputs",
                        return_value=([source], ["upload_a"], ["测试来源"]),
                    ), \
                    patch("app.rag_eval.job_service.enqueue_job", fake_jobs.enqueue_job), \
                    patch("app.rag_eval.isolated_runs.threading.Thread"):
                manager = IsolatedRunManager()
                state = manager.start_ingestion(source_ids=["upload_a"], max_pages=1, page_ranges=[])

            self.assertEqual(state["status"], "queued")
            self.assertEqual(state["page_ranges"], [])

    def test_run_file_lock_serializes_cross_process_writes(self):
        try:
            ctx = multiprocessing.get_context("fork")
        except ValueError:
            self.skipTest("fork start method unavailable on this platform")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "locktest_20260813_000000"
            run_dir = root / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"counter": 0}), encoding="utf-8")

            processes = [
                ctx.Process(target=_hammer_lock, args=(str(root), run_id, 50))
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join()

            final = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(final["counter"], 100)


if __name__ == "__main__":
    unittest.main()
