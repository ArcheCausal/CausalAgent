import json
import os
import threading
import time
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from langchain_chroma import Chroma

from Agent.knowledge_base.multimodal.index import StagedIndex, embedding_fingerprint
from Agent.knowledge_base.rag.operation_datasets import candidate_generation
from Agent.knowledge_base.rag.operation_datasets.candidate_generation import expand_candidate_dataset
from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import validate_candidate_gold_binding
from Agent.knowledge_base.rag.rag_eval import rag_eval


class RagCandidateGenerationTests(unittest.TestCase):
    def _write_staged_index(self, root: Path) -> Path:
        index_dir = root / "mm_candidate"
        index_dir.mkdir()
        embedding = embedding_fingerprint()
        units = []
        for index, modality in enumerate(("text", "image"), start=1):
            units.append(
                {
                    "unit_id": "unit_" + f"{index:064x}",
                    "document_id": "doc_" + "a" * 64,
                    "modality": modality,
                    "content_kind": "paragraph" if modality == "text" else "chart",
                    "page_number": index,
                    "raw_text": "标准化内容",
                    "retrieval_text": f"可用于回答问题的 {modality} 证据内容。",
                    "asset_uri": "assets/test.png" if modality == "image" else None,
                    "content_hash": "b" * 64,
                    "parser_name": "test",
                    "parser_version": "1",
                    "embedding_provider": embedding["provider"],
                    "embedding_model": embedding["model"],
                    "status": "completed",
                }
            )
        (index_dir / "manifest.json").write_text(
            json.dumps({
                "schema_version": 4,
                "index_version": index_dir.name,
                "embedding": embedding,
                "unit_count": len(units),
            }),
            encoding="utf-8",
        )
        (index_dir / "units.jsonl").write_text(
            "".join(json.dumps(unit, ensure_ascii=False) + "\n" for unit in units),
            encoding="utf-8",
        )
        (index_dir / "issues.jsonl").write_text("", encoding="utf-8")
        (index_dir / "build_state.json").write_text(
            json.dumps({"status": "staged_complete", "unit_count": len(units), "vector_count": len(units)}),
            encoding="utf-8",
        )
        db = Chroma(
            persist_directory=str(index_dir / "chroma"),
            collection_name=f"{os.getenv('MULTIMODAL_COLLECTION_PREFIX', 'causal_multimodal')}_{index_dir.name}",
        )
        db._collection.add(
            ids=[unit["unit_id"] for unit in units],
            embeddings=[[float(index), 0.0] for index, _unit in enumerate(units, start=1)],
            documents=[unit["retrieval_text"] for unit in units],
        )
        StagedIndex._close(db)
        return index_dir

    def test_expansion_writes_candidate_with_snapshot_and_locator(self):
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = self._write_staged_index(Path(temporary))
            output_path = Path(temporary) / "candidate.json"

            def generator(record, _count):
                return [{
                    "question": f"如何解释 {record['modality']} 证据？",
                    "reference_answer": f"该证据说明 {record['modality']} 内容。",
                    "expected_claims": [f"包含 {record['modality']} 内容"],
                }]

            result = expand_candidate_dataset(
                index_dir,
                output_path=output_path,
                max_units=2,
                max_workers=2,
                generator=generator,
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertTrue(result["candidate_only"])
            self.assertEqual(result["accepted_count"], 2)
            self.assertEqual(payload["dataset_kind"], "generated_candidate")
            self.assertEqual(payload["source_snapshot"]["index_version"], "mm_candidate")
            self.assertEqual(len(payload["samples"]), 2)
            self.assertTrue(payload["samples"][0]["gold_evidence"])
            self.assertEqual(
                payload["samples"][0]["gold_evidence"][0]["bound_index_version"],
                "mm_candidate",
            )
            self.assertEqual(
                payload["samples"][0]["source"]["index_binding"]["index_version"],
                "mm_candidate",
            )
            self.assertEqual(
                validate_candidate_gold_binding(payload, index_dir=index_dir)["index_version"],
                "mm_candidate",
            )
            self.assertEqual(payload["samples"][0]["source"]["review_status"], "candidate")
            self.assertEqual(payload["screening"]["schema_version"], "rag_candidate_screening_v1")
            coverage = payload["screening"]["coverage"]
            self.assertEqual(coverage["schema_version"], "rag_candidate_coverage_v1")
            self.assertEqual(coverage["selected_unit_count"], 2)
            self.assertEqual(coverage["covered_unit_count"], 2)
            self.assertEqual(coverage["samples_with_evidence"], 2)
            self.assertTrue(result["audit_path"])
            self.assertTrue(Path(result["audit_path"]).is_file())

    def test_default_generator_uses_ragas_chunks_and_maps_multihop_contexts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_dir = self._write_staged_index(root)

            def fake_ragas_rows(records, _testset_size, **_kwargs):
                return [{
                    "user_input": "如何联合解释两类证据？",
                    "reference": "这是一个足够长的参考答案，包含两个可核对事实。",
                    "expected_claims": ["两个可核对事实"],
                    "reference_contexts": [
                        f"<1-hop>\n\n{records[0]['content']}",
                        f"<2-hop>\n\n{records[1]['content']}",
                    ],
                    "synthesizer_name": "multi_hop_specific_query_synthesizer",
                }]

            with patch.object(candidate_generation, "_generate_ragas_rows", side_effect=fake_ragas_rows) as generate:
                result = expand_candidate_dataset(index_dir, output_path=root / "candidate.json", max_units=2)

            generate.assert_called_once()
            self.assertEqual(result["accepted_count"], 1)
            payload = json.loads((root / "candidate.json").read_text(encoding="utf-8"))
            sample = payload["samples"][0]
            self.assertEqual(sample["source"]["generator"], "ragas_0.4.3_generate_with_chunks")
            self.assertEqual(sample["source"]["synthesizer_name"], "multi_hop_specific_query_synthesizer")
            self.assertEqual(len(sample["gold_evidence"]), 2)
            coverage = payload["screening"]["coverage"]
            self.assertEqual(coverage["multi_evidence_sample_count"], 1)
            self.assertEqual(coverage["evidence_locator_count"], 2)
            self.assertEqual(coverage["covered_unit_count"], 2)

    def test_expansion_filters_duplicate_questions_without_promoting_gold(self):
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = self._write_staged_index(Path(temporary))

            def generator(_record, _count):
                return [{
                    "question": "同一个候选问题",
                    "reference_answer": "这是一个足够长的参考答案。",
                    "expected_claims": ["一个原子事实"],
                }]

            result = expand_candidate_dataset(
                index_dir,
                output_path=Path(temporary) / "candidate.json",
                max_units=2,
                generator=generator,
            )

            self.assertEqual(result["accepted_count"], 1)
            self.assertEqual(result["rejected_count"], 1)
            self.assertEqual(result["rejections"][0]["reason"], "duplicate_question")

    def test_expansion_rejects_incomplete_staged_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = self._write_staged_index(Path(temporary))
            (index_dir / "build_state.json").write_text(
                json.dumps({"status": "building", "unit_count": 2, "vector_count": 2}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "staged index is not complete"):
                expand_candidate_dataset(
                    index_dir,
                    output_path=Path(temporary) / "candidate.json",
                    generator=lambda *_args: [],
                )

    def test_expansion_rejects_empty_candidate_dataset_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = self._write_staged_index(Path(temporary))
            output_path = Path(temporary) / "candidate.json"

            with self.assertRaisesRegex(ValueError, "no candidates passed quality screening"):
                expand_candidate_dataset(index_dir, output_path=output_path, generator=lambda *_args: [])

            self.assertFalse(output_path.exists())
            self.assertTrue((Path(f"{output_path}.audit.json")).exists())

    def test_rejections_and_generation_errors_are_written_to_versioned_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_dir = self._write_staged_index(root)
            output_path = root / "candidate.json"

            def generator(record, _count):
                if record["modality"] == "text":
                    return [{"question": "短", "reference_answer": "无效", "expected_claims": []}]
                raise RuntimeError("provider unavailable")

            with self.assertRaisesRegex(ValueError, "audit="):
                expand_candidate_dataset(index_dir, output_path=output_path, generator=generator, max_workers=1)

            audit = json.loads(Path(f"{output_path}.audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["schema_version"], "rag_candidate_generation_audit_v1")
            self.assertTrue(audit["rejections"])
            self.assertTrue(audit["generation_errors"])

    def test_default_output_revision_is_unique_at_the_same_time(self):
        class FixedDateTime:
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)

        def generator(_record, _count):
            return [{
                "question": "同一时刻生成的候选问题",
                "reference_answer": "这是一个足够长的参考答案。",
                "expected_claims": ["一个原子事实"],
            }]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_dir = self._write_staged_index(root)
            with patch.object(candidate_generation, "DEFAULT_OUTPUT_ROOT", root / "output"), patch.object(
                candidate_generation, "datetime", FixedDateTime
            ), patch.object(
                candidate_generation.uuid,
                "uuid4",
                side_effect=[
                    type("Uuid", (), {"hex": "a" * 32})(),
                    type("Uuid", (), {"hex": "b" * 32})(),
                    type("Uuid", (), {"hex": "c" * 32})(),
                    type("Uuid", (), {"hex": "d" * 32})(),
                    type("Uuid", (), {"hex": "e" * 32})(),
                    type("Uuid", (), {"hex": "f" * 32})(),
                ],
            ):
                first = expand_candidate_dataset(index_dir, generator=generator, max_units=1)
                second = expand_candidate_dataset(index_dir, generator=generator, max_units=1)

            self.assertNotEqual(first["dataset_revision"], second["dataset_revision"])
            self.assertNotEqual(first["dataset_path"], second["dataset_path"])
            self.assertEqual(len(list((root / "output").glob("*.json"))), 4)

    def test_explicit_output_refuses_to_overwrite_previous_revision(self):
        def generator(record, _count):
            return [{
                "question": f"固定输出测试 {record['modality']}",
                "reference_answer": "这是一个足够长的参考答案。",
                "expected_claims": ["一个原子事实"],
            }]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_dir = self._write_staged_index(root)
            output_path = root / "candidate.json"
            expand_candidate_dataset(index_dir, output_path=output_path, max_units=1, generator=generator)

            with self.assertRaisesRegex(FileExistsError, "already exists"):
                expand_candidate_dataset(index_dir, output_path=output_path, max_units=1, generator=generator)

    def test_dataset_id_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = self._write_staged_index(Path(temporary))
            with self.assertRaisesRegex(ValueError, "dataset_id"):
                expand_candidate_dataset(
                    index_dir,
                    dataset_id="../outside",
                    generator=lambda *_args: [],
                )


class RetrievalSweepConcurrencyTests(unittest.TestCase):
    def test_failed_first_config_marks_baseline_failed_and_makes_no_recommendation(self):
        def fake_evaluate(_dataset, retrieval_config, **_kwargs):
            if retrieval_config.final_top_k == 4:
                raise RuntimeError("baseline unavailable")
            return {
                "status": "pass",
                "recall_at_k": 0.9,
                "mrr": 0.9,
                "hit_rate": 0.9,
                "details": [{"sample_id": "q1", "recall": 0.9, "retrieved_evidence": ["evidence"]}],
            }

        with patch.object(rag_eval, "evaluate_retrieval", side_effect=fake_evaluate):
            result = rag_eval.sweep_retrieval_configs(
                [{"sample_id": "q1", "question": "问题"}],
                [
                    {"name": "baseline", "config": {"final_top_k": 4}},
                    {"name": "candidate", "config": {"final_top_k": 6}},
                ],
            )

        self.assertTrue(result["baseline_failed"])
        self.assertEqual(result["baseline_run_id"], 1)
        self.assertEqual(result["baseline_status"], "failed")
        self.assertEqual(result["recommended_candidates"], [])

    def test_sweep_is_bounded_parallel_and_keeps_failed_runs(self):
        lock = threading.Lock()
        active = 0
        peak = 0

        def fake_evaluate(dataset, retrieval_config, **_kwargs):
            nonlocal active, peak
            if retrieval_config.final_top_k == 10:
                raise RuntimeError("synthetic failure")
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            recall = 0.6 if retrieval_config.final_top_k == 6 else 0.5
            return {
                "status": "pass",
                "recall_at_k": recall,
                "mrr": recall,
                "hit_rate": recall,
                "avg_timings_ms": {},
                "stage_metrics": {},
                "final_prefix_metrics": {},
                "loss_reason_counts": {},
                "details": [{"sample_id": "q1", "recall": recall, "retrieved_evidence": ["evidence"]}],
            }

        with patch.object(rag_eval, "evaluate_retrieval", side_effect=fake_evaluate):
            result = rag_eval.sweep_retrieval_configs(
                [{"sample_id": "q1", "question": "问题", "gold_evidence": [{"unit_id": "u"}]}],
                [
                    {"name": "e0", "config": {"final_top_k": 4}},
                    {"name": "candidate", "config": {"final_top_k": 6}},
                    {"name": "broken", "config": {"final_top_k": 10}},
                ],
                max_workers=2,
            )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["max_workers"], 2)
        self.assertEqual(len(result["errors"]), 1)
        self.assertLessEqual(peak, 2)
        self.assertEqual(result["recommended_candidates"][0]["name"], "candidate")

    def test_serial_cancellation_records_all_remaining_runs(self):
        cancel_requested = False

        def fake_evaluate(_dataset, retrieval_config, **_kwargs):
            del retrieval_config
            nonlocal cancel_requested
            cancel_requested = True
            return {
                "status": "pass",
                "recall_at_k": 0.5,
                "mrr": 0.5,
                "hit_rate": 0.5,
                "details": [],
            }

        with patch.object(rag_eval, "evaluate_retrieval", side_effect=fake_evaluate):
            result = rag_eval.sweep_retrieval_configs(
                [{"sample_id": "q1", "question": "问题"}],
                [{"name": f"run-{value}", "config": {"final_top_k": value}} for value in (4, 5, 6)],
                max_workers=1,
                cancel_checker=lambda: cancel_requested,
            )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["run_count"], 3)
        self.assertEqual([run["status"] for run in sorted(result["runs"], key=lambda run: run["run_id"])], [
            "pass",
            "cancelled",
            "cancelled",
        ])

    def test_parallel_cancellation_records_cancelled_futures(self):
        cancel_requested = threading.Event()
        release_workers = threading.Event()
        main_thread = threading.current_thread()

        def fake_evaluate(_dataset, retrieval_config, **_kwargs):
            if retrieval_config.final_top_k == 4:
                cancel_requested.set()
                return {
                    "status": "pass",
                    "recall_at_k": 0.5,
                    "mrr": 0.5,
                    "hit_rate": 0.5,
                    "details": [],
                }
            release_workers.wait(timeout=1)
            return {
                "status": "pass",
                "recall_at_k": 0.5,
                "mrr": 0.5,
                "hit_rate": 0.5,
                "details": [],
            }

        timer = threading.Timer(0.2, release_workers.set)
        timer.start()
        try:
            with patch.object(rag_eval, "evaluate_retrieval", side_effect=fake_evaluate):
                result = rag_eval.sweep_retrieval_configs(
                    [{"sample_id": "q1", "question": "问题"}],
                    [{"name": f"run-{value}", "config": {"final_top_k": value}} for value in range(4, 10)],
                    max_workers=2,
                    cancel_checker=lambda: cancel_requested.is_set() if threading.current_thread() is main_thread else False,
                )
        finally:
            release_workers.set()
            timer.cancel()

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["run_count"], 6)
        self.assertTrue(any(run["status"] == "cancelled" for run in result["runs"]))


if __name__ == "__main__":
    unittest.main()
