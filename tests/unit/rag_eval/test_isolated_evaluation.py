import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Agent.knowledge_base.rag.rag_eval.rag_eval import evaluate_retrieval
from Agent.knowledge_base.rag.operation_datasets import benchmark_v2
from app.rag_eval.isolated_evaluation import _build_ragas_options, execute_isolated_evaluation, normalize_dataset_payload
from app.rag_eval.isolated_runs import IsolatedRunManager, _run_dir


class _NoopThread:
    def __init__(self, *, target, args, daemon, name):
        self.target = target
        self.args = args

    def start(self):
        return None


class _FakeService:
    runtime = SimpleNamespace(embedding=object())

    def build_retrieval_trace(self, question, config=None):
        stages = {
            "dense_raw": [],
            "dense_thresholded": [],
            "dense_mmr": [],
            "sparse": [],
            "merged_before_rerank": [],
            "reranked": [],
            "final": [],
        }
        return {
            "stages": stages,
            "evidence_payload": [
                {"content": "隔离证据", "metadata": {"document_id": "doc-1", "page_number": 1}}
            ],
            "timings_ms": {"dense": 1.0},
        }

    def get_vector_db_metadata_summary(self):
        return {
            "exists": True,
            "persist_directory": "isolated-only",
            "collection_name": "isolated_collection",
            "release_id": "isolated:test",
            "vector_count": 1,
        }

    def answer_question(self, question_payload, evidence_payloads, answer_prompt=None):
        return {
            "status": "success",
            "answer": "基于隔离证据的回答",
            "confidence": "medium",
            "citations": ["E1"],
        }


class _FailedAnswerService(_FakeService):
    def answer_question(self, question_payload, evidence_payloads, answer_prompt=None):
        return {
            "status": "insufficient_evidence",
            "answer": "证据已检索，但回答生成失败：Error code: 402 - Insufficient Balance; fallback_failed=402",
            "confidence": "low",
            "citations": [],
        }


class IsolatedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_builtin_ragas_profiles_run_unless_explicitly_prepare_only(self):
        self.assertTrue(_build_ragas_options({"profile": "reviewed_5_core_metrics"})["run"])
        self.assertFalse(_build_ragas_options({"profile": "reviewed_all_prepare_only"})["run"])

    def test_normalize_rejects_incomplete_gold_dataset_before_enqueue(self):
        with self.assertRaisesRegex(ValueError, "reference_answer is required"):
            normalize_dataset_payload({
                "schema_version": "rag_eval_v1",
                "dataset_id": "incomplete-gold",
                "dataset_kind": "gold_regression",
                "samples": [{"sample_id": "q1", "question": "问题"}],
            })

    def test_retrieval_evaluator_uses_explicit_runtime_adapter(self):
        calls = []

        def trace_builder(question, config=None):
            calls.append(question)
            return {
                "stages": {name: [] for name in (
                    "dense_raw", "dense_thresholded", "dense_mmr", "sparse",
                    "merged_before_rerank", "reranked", "final",
                )},
                "evidence_payload": [],
                "timings_ms": {},
            }

        result = evaluate_retrieval(
            [{"sample_id": "q1", "question": "问题"}],
            retrieval_trace_builder=trace_builder,
            vector_summary_provider=lambda: {"release_id": "isolated"},
        )

        self.assertEqual(calls, ["问题"])
        self.assertEqual(result["vector_db_summary"]["release_id"], "isolated")

    def test_full_isolated_pipeline_writes_only_run_artifacts(self):
        dataset_path = self.root / "dataset_snapshot.json"
        output_dir = self.root / "evaluation"
        dataset_path.write_text(
            json.dumps({
                "schema_version": "rag_eval_v1",
                "dataset_id": "inline-test",
                "samples": [{"sample_id": "q1", "question": "问题"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        retrieval_result = {
            "status": "pass",
            "sample_count": 1,
            "recall_at_k": None,
            "mrr": None,
            "hit_rate": None,
            "config": {},
            "details": [{
                "sample_id": "q1",
                "question": "问题",
                "source": {},
                "expected_claims": [],
                "reference_answer": "",
                "judge_rubric": {},
                "gold_evidence": [],
                "retrieval_match_mode": "unscored",
                "retrieved_evidence": [],
                "matched_evidence": [],
                "recall": None,
                "reciprocal_rank": None,
                "stage_results": {},
                "gold_rank_summary": {},
                "trace_timings_ms": {},
                "final_evidence_payload": [{"content": "隔离证据", "metadata": {"document_id": "doc-1"}}],
                "loss_reasons": [],
            }],
        }

        with patch("app.rag_eval.isolated_evaluation.evaluate_retrieval", return_value=retrieval_result):
            result = execute_isolated_evaluation(
                run_id="eval_test_run",
                ingestion_run_id="ingest_test_run",
                index_version="mm_test",
                dataset_path=dataset_path,
                output_dir=output_dir,
                service=_FakeService(),
                retrieval_options={"profile": "active_current"},
                ragas_options={"profile": "quick_cached", "run": False},
                steps=None,
            )

        self.assertEqual(result["schema_version"], "rag_eval_evaluation_result_v1")
        self.assertEqual(result["summary"]["status"], "pass")
        self.assertTrue((output_dir / "machine" / "rag_eval_result.json").is_file())
        self.assertTrue((output_dir / "machine" / "ragas_eval_dataset.json").is_file())
        self.assertTrue((output_dir / "machine" / "trace.jsonl").is_file())
        self.assertTrue((output_dir / "summary.md").is_file())
        self.assertNotIn("Agent/knowledge_base/db", json.dumps(result, ensure_ascii=False))

    def test_answer_generation_failure_fails_ragas_without_fake_judge_completion(self):
        dataset_path = self.root / "dataset_snapshot.json"
        output_dir = self.root / "evaluation"
        dataset_path.write_text(
            json.dumps({
                "schema_version": "rag_eval_v1",
                "dataset_id": "answer-failure",
                "samples": [{"sample_id": "q1", "question": "问题", "reference_answer": "答案"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        retrieval_result = {
            "status": "pass",
            "sample_count": 1,
            "recall_at_k": 1.0,
            "mrr": 1.0,
            "hit_rate": 1.0,
            "config": {},
            "details": [{
                "sample_id": "q1",
                "question": "问题",
                "final_evidence_payload": [{"content": "证据", "metadata": {"document_id": "doc-1"}}],
                "retrieved_evidence": [],
                "trace_timings_ms": {},
            }],
        }
        events = []

        with patch("app.rag_eval.isolated_evaluation.evaluate_retrieval", return_value=retrieval_result), patch(
            "app.rag_eval.isolated_evaluation.run_repeated_ragas_baseline"
        ) as judge:
            result = execute_isolated_evaluation(
                run_id="eval_answer_failure",
                ingestion_run_id="ingest_test_run",
                index_version="mm_test",
                dataset_path=dataset_path,
                output_dir=output_dir,
                service=_FailedAnswerService(),
                retrieval_options={"profile": "active_current"},
                ragas_options={
                    "profile": "generic_pipeline",
                    "run": True,
                    "prepare_only": False,
                    "selected_metrics": ["faithfulness"],
                },
                steps=["validate_datasets", "retrieval_eval", "ragas_eval", "summary"],
                event_callback=lambda event_type, message, data: events.append((event_type, message, data)),
            )

        judge.assert_not_called()
        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["summary"]["status_reason"], "answer_generation_failed")
        ragas = json.loads((output_dir / "machine" / "ragas_eval_result.json").read_text(encoding="utf-8"))
        self.assertEqual(ragas["status"], "failed")
        self.assertEqual(ragas["invalid_answer_count"], 1)
        self.assertIn("402", ragas["error"])
        self.assertTrue(any(event_type == "step_error" and data.get("step") == "ragas_eval" for event_type, _, data in events))
        self.assertFalse(any(event_type == "step_done" and data.get("step") == "ragas_eval" for event_type, _, data in events))

    def test_isolated_ragas_uses_same_compressed_evidence_for_answer_and_judge(self):
        dataset_path = self.root / "dataset_snapshot.json"
        output_dir = self.root / "evaluation"
        dataset_path.write_text(
            json.dumps({
                "schema_version": "rag_eval_v1",
                "dataset_id": "aligned-context",
                "samples": [{"sample_id": "q1", "question": "问题"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        evidence = [
            {"evidence_id": "E1", "content": "正文一", "metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "text"}},
            {"evidence_id": "E2", "content": "正文二", "metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "text"}},
            {"evidence_id": "E3", "content": "表格", "metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "table"}},
        ]
        retrieval_result = {
            "status": "pass", "sample_count": 1, "recall_at_k": 1.0, "mrr": 1.0, "hit_rate": 1.0,
            "config": {}, "details": [{"sample_id": "q1", "question": "问题", "final_evidence_payload": evidence, "retrieved_evidence": [], "trace_timings_ms": {}}],
        }
        captured = []

        class CapturingService(_FakeService):
            def answer_question(self, question_payload, evidence_payloads, answer_prompt=None):
                captured.append(evidence_payloads)
                return {"status": "success", "answer": "ok", "confidence": "medium", "citations": ["E1"]}

        with patch("app.rag_eval.isolated_evaluation.evaluate_retrieval", return_value=retrieval_result):
            execute_isolated_evaluation(
                run_id="eval_aligned_context",
                ingestion_run_id="ingest_test_run",
                index_version="mm_test",
                dataset_path=dataset_path,
                output_dir=output_dir,
                service=CapturingService(),
                retrieval_options={"profile": "active_current", "overrides": {"answer_max_contexts": 2, "answer_context_compression": "page_dedupe", "official_only_when_available": False}},
                ragas_options={"profile": "quick_cached", "run": False, "max_contexts": 6},
                steps=["validate_datasets", "retrieval_eval", "ragas_eval", "summary"],
            )

        prepared = json.loads((output_dir / "machine" / "ragas_eval_dataset.json").read_text(encoding="utf-8"))
        self.assertEqual(len(captured), 1)
        self.assertEqual([item["content"] for item in captured[0]], ["正文一", "表格"])
        self.assertEqual(len(prepared["ragas_rows"][0]["retrieved_contexts"]), 2)
        self.assertEqual(prepared["metadata"][0]["ragas_context_count"], 2)

    def test_run_manager_propagates_failed_pipeline_summary(self):
        run_id = "eval_20260817_000000_failure"
        run_root = self.root / "runs"
        run_dir = run_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "dataset_snapshot.json").write_text(
            json.dumps({"schema_version": "rag_eval_v1", "dataset_id": "unit", "samples": [{"sample_id": "q1", "question": "问题"}]}),
            encoding="utf-8",
        )
        (run_dir / "run.json").write_text(json.dumps({
            "run_id": run_id,
            "kind": "evaluation",
            "status": "running",
            "started_at": "2026-08-17T00:00:00+08:00",
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "dataset_path": str(run_dir / "dataset_snapshot.json"),
            "retrieval_options": {},
            "ragas_options": {"run": True},
            "strategy_profile": {},
            "steps": ["ragas_eval"],
            "events": [],
        }), encoding="utf-8")
        manager = IsolatedRunManager()

        def failed_execute(**kwargs):
            result = {"summary": {"status": "failed", "status_reason": "answer_generation_failed"}}
            (Path(kwargs["output_dir"]) / "result.json").write_text(json.dumps(result), encoding="utf-8")
            return result

        with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", run_root), patch.object(
            manager, "_create_isolated_rag_service", return_value=_FakeService()
        ), patch(
            "app.rag_eval.isolated_evaluation.execute_isolated_evaluation",
            side_effect=failed_execute,
        ):
            manager.run_evaluation_sync(run_id)
            state = manager.get(run_id)

        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["status_reason"], "answer_generation_failed")
        self.assertTrue(state["result_available"])
        self.assertEqual(manager.get_result(run_id)["summary"]["status"], "failed")
        self.assertTrue(any(event["type"] == "run_error" for event in state["events"]))

    def test_ragas_judge_without_valid_scores_fails_closed(self):
        dataset_path = self.root / "dataset_snapshot.json"
        output_dir = self.root / "evaluation"
        dataset_path.write_text(
            json.dumps({
                "schema_version": "rag_eval_v1",
                "dataset_id": "judge-no-scores",
                "samples": [{"sample_id": "q1", "question": "问题", "reference_answer": "答案"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        retrieval_result = {
            "status": "pass",
            "sample_count": 1,
            "recall_at_k": 1.0,
            "mrr": 1.0,
            "hit_rate": 1.0,
            "config": {},
            "details": [{
                "sample_id": "q1",
                "question": "问题",
                "final_evidence_payload": [{"content": "证据", "metadata": {"document_id": "doc-1"}}],
                "retrieved_evidence": [],
                "trace_timings_ms": {},
            }],
        }
        events = []
        no_scores = {
            "status": "ragas_no_valid_scores",
            "warning": "judge API returned no numeric scores",
            "score_summary": {},
            "score_records": [],
            "metrics": ["faithfulness"],
        }

        with patch("app.rag_eval.isolated_evaluation.evaluate_retrieval", return_value=retrieval_result), patch(
            "app.rag_eval.isolated_evaluation.run_repeated_ragas_baseline", return_value=no_scores
        ):
            result = execute_isolated_evaluation(
                run_id="eval_judge_no_scores",
                ingestion_run_id="ingest_test_run",
                index_version="mm_test",
                dataset_path=dataset_path,
                output_dir=output_dir,
                service=_FakeService(),
                retrieval_options={"profile": "active_current"},
                ragas_options={"profile": "generic_pipeline", "run": True, "selected_metrics": ["faithfulness"]},
                steps=["validate_datasets", "retrieval_eval", "ragas_eval", "summary"],
                event_callback=lambda event_type, message, data: events.append((event_type, message, data)),
            )

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["summary"]["status_reason"], "ragas_judge_no_valid_scores")
        self.assertIn("no numeric scores", result["summary"]["error"])
        self.assertTrue(any(event_type == "step_error" and data.get("step") == "ragas_eval" for event_type, _, data in events))

    def test_sweep_is_added_without_retrieval_eval_step(self):
        dataset_path = self.root / "dataset_snapshot.json"
        output_dir = self.root / "evaluation"
        dataset_path.write_text(
            json.dumps({
                "schema_version": "rag_eval_v1",
                "dataset_id": "inline-test",
                "samples": [{"sample_id": "q1", "question": "问题"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        sweep_result = {
            "status": "pass",
            "sample_count": 1,
            "run_count": 1,
            "max_workers": 1,
            "baseline_failed": False,
            "runs": [],
            "recommended_candidates": [],
            "errors": [],
        }

        with patch("app.rag_eval.isolated_evaluation.sweep_retrieval_configs", return_value=sweep_result):
            execute_isolated_evaluation(
                run_id="eval_sweep_steps",
                ingestion_run_id="ingest_test_run",
                index_version="mm_test",
                dataset_path=dataset_path,
                output_dir=output_dir,
                service=_FakeService(),
                retrieval_options={
                    "profile": "active_current",
                    "sweep": [{"name": "baseline", "config": {"final_top_k": 4}}],
                },
                ragas_options={"profile": "quick_cached", "run": False},
                steps=["validate_datasets", "summary"],
            )

        manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["config"]["steps"], ["validate_datasets", "retrieval_sweep", "summary"])

    def test_evaluation_task_binds_dataset_snapshot_without_starting_thread_in_test(self):
        ingestion_id = "ingest_20260731_000000_abcd123456"
        index_version = "mm_test"
        with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", self.root / "runs"), patch.object(
            IsolatedRunManager, "_validate_staged_index"
        ), patch("app.rag_eval.isolated_runs.threading.Thread", _NoopThread), patch(
            "app.rag_eval.job_service.enqueue_job",
            return_value={"run_id": "queued", "status": "queued"},
        ) as enqueue:
            ingestion_dir = _run_dir(ingestion_id)
            ingestion_dir.mkdir(parents=True)
            (ingestion_dir / "run.json").write_text(
                json.dumps({
                    "run_id": ingestion_id,
                    "kind": "ingestion",
                    "status": "staged",
                    "index_version": index_version,
                    "collection_name": "isolated_collection",
                    "vector_count": 1,
                }),
                encoding="utf-8",
            )
            manager = IsolatedRunManager()
            state = manager.start_evaluation(
                ingestion_id,
                index_version,
                {
                    "schema_version": "rag_eval_v1",
                    "dataset_id": "unit",
                    "samples": [{"sample_id": "q1", "question": "问题"}],
                },
                ragas_options={"profile": "quick_cached", "run": False},
                batch_id="eval_batch_test",
                batch_position=1,
                batch_size=2,
            )
            self.assertTrue((_run_dir(state["run_id"]) / "dataset_snapshot.json").is_file())

        self.assertEqual(state["kind"], "evaluation")
        self.assertEqual(state["status"], "queued")
        self.assertEqual(state["execution_backend"], "persistent_worker")
        self.assertEqual(state["input_identity"]["dataset_id"], "unit")
        self.assertEqual(state["batch_id"], "eval_batch_test")
        self.assertEqual(state["batch_position"], 1)
        self.assertEqual(state["batch_size"], 2)
        enqueue.assert_called_once()

    def test_generated_candidate_rejects_cross_index_binding_before_enqueue(self):
        ingestion_id = "ingest_20260731_000000_abcd123456"
        index_version = "mm_test"
        run_root = self.root / "runs"
        with patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", run_root), patch.object(
            IsolatedRunManager, "_validate_staged_index"
        ), patch.object(
            benchmark_v2,
            "_load_index_units",
            return_value=(
                {
                    "unit-1": {
                        "unit_id": "unit-1",
                        "modality": "text",
                        "content_kind": "paragraph",
                        "metadata": {"document_id": "doc-1", "page_number": 1},
                    }
                },
                {"index_version": index_version},
            ),
        ), patch("app.rag_eval.job_service.enqueue_job") as enqueue:
            ingestion_dir = _run_dir(ingestion_id)
            ingestion_dir.mkdir(parents=True)
            (ingestion_dir / "run.json").write_text(
                json.dumps({
                    "run_id": ingestion_id,
                    "kind": "ingestion",
                    "status": "staged",
                    "index_version": index_version,
                    "collection_name": "isolated_collection",
                    "vector_count": 1,
                }),
                encoding="utf-8",
            )
            manager = IsolatedRunManager()
            dataset = {
                "schema_version": "rag_eval_v1",
                "dataset_id": "candidate-old-index",
                "dataset_kind": "generated_candidate",
                "source_snapshot": {"index_version": "mm_old"},
                "samples": [{
                    "sample_id": "q1",
                    "question": "问题",
                    "reference_answer": "答案",
                    "expected_claims": ["事实"],
                    "gold_evidence": [{
                        "unit_id": "unit-1",
                        "document_id": "doc-1",
                        "page_number": 1,
                        "modality": "text",
                        "content_kind": "paragraph",
                        "bound_index_version": "mm_old",
                    }],
                }],
            }

            with self.assertRaisesRegex(ValueError, "source_snapshot index_version"):
                manager.start_evaluation(ingestion_id, index_version, dataset)

        enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
