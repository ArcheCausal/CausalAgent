import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agent.knowledge_base.multimodal import evaluation
from Agent.knowledge_base.rag.rag_eval import rag_eval, ragas_eval, trace_export


class GenericRagEvalTests(unittest.TestCase):
    def test_ragas_judge_disables_langsmith_tracing_only_during_evaluate(self):
        observed = {}

        class FakeDataset:
            @classmethod
            def from_list(cls, rows):
                return rows

        class FakeMetric:
            name = "faithfulness"

        class FakeResult:
            def to_pandas(self):
                class FakeDataFrame:
                    def notnull(self):
                        return self

                    def where(self, *_args, **_kwargs):
                        return self

                    def to_dict(self, _orient):
                        return [{"faithfulness": 1.0}]

                return FakeDataFrame()

        def fake_evaluate(*_args, **_kwargs):
            observed.update(
                {
                    "LANGCHAIN_TRACING_V2": os.getenv("LANGCHAIN_TRACING_V2"),
                    "LANGSMITH_TRACING": os.getenv("LANGSMITH_TRACING"),
                    "LANGSMITH_TRACING_V2": os.getenv("LANGSMITH_TRACING_V2"),
                }
            )
            return FakeResult()

        components = {
            "EvaluationDataset": FakeDataset,
            "LangchainLLMWrapper": lambda value: value,
            "LangchainEmbeddingsWrapper": lambda value: value,
            "RunConfig": lambda **kwargs: kwargs,
            "evaluate": fake_evaluate,
            "metrics": [FakeMetric()],
            "ragas": object(),
        }
        with patch.dict(
            os.environ,
            {
                "LANGCHAIN_TRACING_V2": "true",
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_TRACING_V2": "true",
            },
        ), patch.object(ragas_eval, "_load_legacy_ragas_components", return_value=components):
            result = ragas_eval.run_ragas_baseline(
                {
                    "ragas_rows": [
                        {
                            "user_input": "问题",
                            "response": "回答",
                            "retrieved_contexts": ["证据"],
                        }
                    ]
                },
                metric_names=["faithfulness"],
                max_retries=0,
            )
            self.assertEqual(os.getenv("LANGCHAIN_TRACING_V2"), "true")

        self.assertEqual(observed, {
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
            "LANGSMITH_TRACING_V2": "false",
        })
        self.assertEqual(result["score_summary"], {"faithfulness": 1.0})

    def test_ragas_answer_relevancy_uses_explicit_embedding(self):
        observed = {}
        sentinel_embedding = object()

        class FakeDataset:
            @classmethod
            def from_list(cls, rows):
                return rows

        class FakeMetric:
            name = "answer_relevancy"

        class FakeResult:
            def to_pandas(self):
                class FakeDataFrame:
                    def notnull(self):
                        return self

                    def where(self, *_args, **_kwargs):
                        return self

                    def to_dict(self, _orient):
                        return [{"answer_relevancy": 1.0}]

                return FakeDataFrame()

        def wrap_embedding(value):
            observed["embedding"] = value
            return value

        components = {
            "EvaluationDataset": FakeDataset,
            "LangchainLLMWrapper": lambda value: value,
            "LangchainEmbeddingsWrapper": wrap_embedding,
            "RunConfig": lambda **kwargs: kwargs,
            "evaluate": lambda *_args, **_kwargs: FakeResult(),
            "metrics": [FakeMetric()],
            "ragas": object(),
        }
        with patch.object(ragas_eval, "_load_legacy_ragas_components", return_value=components), patch.object(
            ragas_eval, "_get_embedding_function", side_effect=AssertionError("should not use global embedding")
        ):
            result = ragas_eval.run_ragas_baseline(
                {
                    "ragas_rows": [
                        {
                            "user_input": "闂",
                            "response": "鍥炵瓟",
                            "retrieved_contexts": ["璇佹嵁"],
                        }
                    ]
                },
                metric_names=["answer_relevancy"],
                max_retries=0,
                embedding_function=sentinel_embedding,
            )

        self.assertIs(observed["embedding"], sentinel_embedding)
        self.assertEqual(result["score_summary"], {"answer_relevancy": 1.0})

    def test_dataset_uses_generic_contract_without_source_adaptation(self):
        payload = {
            "schema_version": "rag_eval_v1",
            "samples": [
                {
                    "sample_id": "q-001",
                    "question": "用户问题",
                    "reference_answer": "参考答案",
                    "expected_claims": ["关键结论"],
                    "gold_evidence": [{"document_id": "doc-1", "page_number": 3, "unit_id": "unit-7"}],
                    "source": {"dataset": "team-eval-v1", "row_index": 1},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dataset.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            samples = rag_eval.load_eval_dataset(str(path))

        self.assertEqual(samples[0]["gold_evidence"], payload["samples"][0]["gold_evidence"])
        self.assertEqual(samples[0]["source"], payload["samples"][0]["source"])
        self.assertNotIn("expected_modality", samples[0])

    def test_generic_match_uses_all_gold_locator_fields(self):
        sample = {
            "gold_evidence": [{"source_key": "book", "position": 17, "unit_key": "unit_match"}],
        }
        candidates = [
            {"metadata": {"source_key": "book", "position": 16, "unit_key": "unit_wrong_page"}},
            {"metadata": {"source_key": "book", "position": 17, "unit_key": "unit_wrong_unit"}},
            {"metadata": {"source_key": "book", "position": 17, "unit_key": "unit_match"}},
        ]

        metrics = rag_eval._collect_stage_metrics(candidates, sample["gold_evidence"])

        self.assertEqual(metrics["match_mode"], "locator")
        self.assertEqual(metrics["matched_evidence"][0]["unit_key"], "unit_match")
        self.assertEqual(metrics["first_relevant_rank"], 3)
        self.assertEqual(metrics["recall"], 1.0)

    def test_generic_match_does_not_add_source_specific_aliases(self):
        metadata = {"chunk_id": "source-format-id", "page": 4}

        self.assertEqual(rag_eval.candidate_evidence(metadata), metadata)
        self.assertFalse(
            rag_eval.locator_matches(
                metadata,
                {"unit_id": "source-format-id", "page_number": 4},
            )
        )

    def test_locator_match_excludes_index_binding_only_field(self):
        metadata = {"unit_id": "unit-7", "document_id": "doc-1", "page_number": 3}

        self.assertTrue(
            rag_eval.locator_matches(
                metadata,
                {
                    **metadata,
                    "bound_index_version": "mm_immutable",
                },
            )
        )

    def test_trace_export_preserves_generic_evidence_metadata(self):
        gold_evidence = [{"external_locator": "record-7", "offset": 12}]
        retrieved_evidence = [{"external_locator": "record-7", "offset": 12, "custom": "value"}]
        config = {
            "retrieval_result_path": "retrieval.json",
            "ragas_result_path": "ragas.json",
            "claim_result_path": "claim.json",
            "ragas_low_cases_path": "low.json",
            "ragas_cross_cases_path": "cross.json",
            "claim_bad_cases_path": "claim_bad.json",
            "context_preview_chars": 80,
            "answer_preview_chars": 80,
        }
        payloads = {
            "retrieval.json": {
                "details": [
                    {
                        "question": "q1",
                        "gold_evidence": gold_evidence,
                        "retrieved_evidence": retrieved_evidence,
                        "matched_evidence": retrieved_evidence,
                        "stage_results": {},
                    }
                ]
            },
            "ragas.json": {
                "ragas_rows": [{"user_input": "q1", "response": "a1", "retrieved_contexts": ["ctx"]}],
                "metadata": [
                    {
                        "question": "q1",
                        "final_evidence_payload": [
                            {"metadata": retrieved_evidence[0], "content": "ctx"}
                        ],
                    }
                ],
            },
            "claim.json": {},
            "low.json": {},
            "cross.json": {},
            "claim_bad.json": {},
        }

        with patch.object(
            trace_export,
            "_load_json_object",
            side_effect=lambda path, default=None: payloads.get(path.name, default or {}),
        ):
            rows = trace_export._build_trace_rows(config)

        self.assertEqual(rows[0]["retrieval_eval"]["gold_evidence"], gold_evidence)
        self.assertEqual(rows[0]["retrieval_eval"]["retrieved_evidence"], retrieved_evidence)
        self.assertEqual(
            rows[0]["generation"]["final_evidence_payload"][0]["metadata"],
            retrieved_evidence[0],
        )

    def test_missing_gold_is_unscored(self):
        metrics = rag_eval._collect_stage_metrics(
            [{"metadata": {"chunk_id": "unit-1"}}],
            [],
        )

        self.assertEqual(metrics["match_mode"], "unscored")
        self.assertIsNone(metrics["recall"])
        self.assertIsNone(metrics["reciprocal_rank"])

    def test_retrieval_result_keeps_missing_gold_unscored(self):
        candidate = {
            "metadata": {"chunk_id": "unit-1", "doc_id": "doc-1"},
            "page_content": "evidence",
        }
        trace = {
            "stages": {stage: [candidate] for stage in rag_eval.TRACE_STAGE_ORDER},
            "timings_ms": {},
            "evidence_payload": [],
        }
        with patch.object(rag_eval, "_validate_vector_store_matches_dataset", return_value={}), patch.object(
            rag_eval, "build_retrieval_trace", return_value=trace
        ):
            result = rag_eval.evaluate_retrieval(
                [{"sample_id": "q-1", "question": "question", "gold_evidence": []}]
            )

        self.assertIsNone(result["recall_at_k"])
        self.assertIsNone(result["mrr"])
        self.assertEqual(result["details"][0]["retrieval_match_mode"], "unscored")

    def test_dataset_validation_blocks_without_explicit_path(self):
        from Agent.knowledge_base.rag.operation_datasets import dataset_utils

        with patch.object(dataset_utils, "RAG_EVAL_DATASET_PATH", None):
            result = dataset_utils.validate_all_datasets()

        self.assertEqual(result["status"], "fail")
        self.assertIn("RAG_EVAL_DATASET_PATH is not configured.", result["errors"])

    def test_active_release_identity_binds_manifest_and_strategy(self):
        defaults = evaluation.load_production_defaults()
        embedding = dict(defaults["embedding"])
        manifest = {
            "index_version": "mm_test",
            "embedding": embedding,
            "parser": "docling",
            "build_configuration": {
                "pdf_parser": defaults["pdf_parser"],
                "vision": {"enabled": False, "local_ocr_enabled": defaults["vision"]["local_ocr_enabled"]},
            },
            "sources": [
                {
                    "source_id": source["source_id"],
                    "document_id": source["document_id"],
                    "relative_path": Path(source["path"]).name,
                    "controlled_path": source["path"],
                    "content_hash": source["sha256"],
                }
                for source in defaults["sources"]
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_dir = root / "indexes" / "mm_test"
            (index_dir / "chroma").mkdir(parents=True)
            manifest_path = index_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_sha256 = __import__("hashlib").sha256(manifest_path.read_bytes()).hexdigest()
            active_path = root / "active_index.json"
            active_path.write_text(
                json.dumps(
                    {
                        "index_version": "mm_test",
                        "index_path": "mm_test/chroma",
                        "collection_name": "causal_multimodal_mm_test",
                        "manifest_sha256": manifest_sha256,
                        "embedding": embedding,
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(active_path),
                    "MULTIMODAL_INDEX_ROOT": str(root / "indexes"),
                },
            ), patch.object(evaluation, "embedding_fingerprint", return_value=embedding):
                identity = evaluation.read_active_release_identity()

        self.assertEqual(identity["index_version"], "mm_test")
        self.assertEqual(identity["manifest_sha256"], manifest_sha256)
        self.assertTrue(identity["strategy_fingerprint"])


if __name__ == "__main__":
    unittest.main()
