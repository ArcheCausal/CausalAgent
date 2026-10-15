import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.rag_eval import isolated_runs
from app.rag_eval.isolated_runs import IsolatedRunManager


class CandidateRunTests(unittest.TestCase):
    def test_manager_recovers_interrupted_candidate_run_after_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "candidate_20260807_000000_abcdef12"
            run_dir.mkdir()
            stale_time = (datetime.now().astimezone() - timedelta(seconds=4000)).isoformat(timespec="seconds")
            (run_dir / "run.json").write_text(json.dumps({
                "run_id": run_dir.name,
                "kind": "candidate_generation",
                "status": "cancelling",
                "execution_backend": "persistent_worker",
                "last_activity_at": stale_time,
                "events": [],
            }), encoding="utf-8")

            with patch.object(isolated_runs, "ISOLATED_RUN_ROOT", root), \
                    patch("app.rag_eval.job_service.fail_job", return_value=None):
                manager = IsolatedRunManager()
                state = manager.get(run_dir.name)

            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["status_reason"], "worker heartbeat timeout")
            self.assertEqual(state["events"][-1]["type"], "run_error")

    def test_candidate_run_review_creates_new_revision_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ingestion_id = "ingest_20260807_000000_abcdef12"
            index_version = "mm_test"
            ingestion_dir = root / ingestion_id
            (ingestion_dir / "indexes" / index_version).mkdir(parents=True)
            (ingestion_dir / "run.json").write_text(json.dumps({
                "run_id": ingestion_id,
                "kind": "ingestion",
                "status": "staged",
                "index_version": index_version,
                "collection_name": "test_collection",
                "unit_count": 48,
                "vector_count": 48,
            }), encoding="utf-8")

            candidate = {
                "schema_version": "rag_eval_v1",
                "dataset_id": "pearl_candidate_mm_test",
                "dataset_kind": "generated_candidate",
                "dataset_revision": "revision-1",
                "samples": [{
                    "sample_id": "candidate-0001",
                    "question": "原始问题是什么？",
                    "reference_answer": "原始参考答案足够长。",
                    "expected_claims": ["原始事实"],
                    "gold_evidence": [{"unit_id": "unit-1"}],
                    "source": {"review_status": "candidate"},
                }],
            }

            def fake_generate(index_dir: Path, *, output_path: Path, **kwargs):
                del index_dir, kwargs
                output_path.write_text(json.dumps(candidate), encoding="utf-8")
                return {
                    "dataset_path": str(output_path),
                    "dataset_id": candidate["dataset_id"],
                    "dataset_revision": candidate["dataset_revision"],
                    "accepted_count": 1,
                    "rejected_count": 0,
                    "generation_errors": [],
                }

            with patch.object(isolated_runs, "ISOLATED_RUN_ROOT", root), \
                    patch.object(IsolatedRunManager, "_validate_staged_index", return_value=None), \
                    patch("app.rag_eval.job_service.enqueue_job", return_value={"run_id": "queued", "status": "queued"}), \
                    patch("Agent.knowledge_base.rag.operation_datasets.candidate_generation.expand_candidate_dataset", side_effect=fake_generate):
                manager = IsolatedRunManager()
                state = manager.start_candidate_generation(
                    ingestion_id,
                    index_version,
                    dataset_id="pearl_candidate_mm_test",
                    question_count=48,
                    max_workers=2,
                )
                # 候选生成现已进入持久队列，由 worker 异步执行；这里直接驱动 worker 侧执行。
                manager.run_queued_sync(state["run_id"])
                state = manager.get(state["run_id"])
                self.assertEqual(state["status"], "succeeded")
                original_path = root / state["run_id"] / "candidate.json"
                self.assertTrue(original_path.is_file())
                result = manager.get_result(state["run_id"])
                self.assertNotIn("dataset_path", result)

                review = manager.save_candidate_review(
                    state["run_id"],
                    reviewer="reviewer-1",
                    decisions=[{"sample_id": "candidate-0001", "decision": "approved"}],
                    updates=[{"sample_id": "candidate-0001", "question": "编辑后的问题是什么？"}],
                )
                self.assertNotEqual(review["candidate_artifact_name"], "candidate.json")
                self.assertTrue(original_path.is_file())
                reviewed_path = root / state["run_id"] / review["candidate_artifact_name"]
                reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
                self.assertEqual(reviewed["samples"][0]["question"], "编辑后的问题是什么？")
                self.assertTrue((root / state["run_id"] / review["review_manifest_artifact_name"]).is_file())

    def test_gold_and_baseline_endpoints_remain_fail_closed_without_review_or_gold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(isolated_runs, "ISOLATED_RUN_ROOT", root):
                manager = IsolatedRunManager()
                with self.assertRaises(KeyError):
                    manager.freeze_candidate_gold_v2(
                        "candidate_20260807_000000_abcdef12",
                        expected_ingestion_run_id="ingest-missing",
                        expected_index_version="mm-missing",
                    )
                with patch("Agent.knowledge_base.rag.operation_datasets.benchmark_v2.bind_baseline_v2", side_effect=ValueError("missing gold")):
                    with self.assertRaises(ValueError):
                        manager.bind_baseline_v2()


if __name__ == "__main__":
    unittest.main()
