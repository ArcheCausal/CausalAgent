"""多模态生产默认配置与人工检索集契约测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agent.knowledge_base.multimodal.production import (
    audit_production_coverage,
    evaluate_retrieval_cases,
    has_frozen_production_identity,
    is_legacy_production_manifest,
    is_production_manifest,
    load_production_defaults,
    production_source_paths,
)
from Agent.knowledge_base.multimodal import defaults as defaults_module
from Agent.knowledge_base.multimodal import production as production_module


class MultimodalProductionDefaultsTests(unittest.TestCase):
    """锁定 P0 的来源、embedding、评测 schema 与首轮门槛。"""

    def test_frozen_defaults_match_api_embedding_policy_and_source_contract(self) -> None:
        """默认配置必须固定 API-key embedding，并关闭本地生产 embedding 开关。"""
        config = load_production_defaults()

        self.assertEqual(
            config["embedding"],
            {
                "mode": "api",
                "provider": "openai_compatible",
                "model": "qwen3.7-text-embedding",
                "dimension": 1024,
                "normalized": True,
            },
        )
        self.assertEqual(
            config["embedding_env"],
            {
                "api_key_env": "EMBEDDING_API_KEY",
                "base_url_env": "EMBEDDING_BASE_URL",
                "model_env": "EMBEDDING_MODEL",
            },
        )
        self.assertFalse(config["local_embedding"]["enabled"])
        self.assertFalse(config["vision"]["remote_enabled"])
        self.assertEqual(config["parser"], "docling")
        self.assertEqual(
            config["pdf_parser"],
            {
                "page_range_mode": "single_page",
                "process_isolation": "spawn_per_batch",
                "batch_size": 8,
                "page_timeout_seconds": 900,
                "do_ocr": False,
                "do_table_structure": False,
                "generate_picture_images": False,
                "generate_page_images": False,
                "layout_batch_size": 1,
                "table_batch_size": 1,
                "ocr_batch_size": 1,
            },
        )
        self.assertEqual(len(config["sources"]), 2)
        self.assertTrue(all(source["required"] for source in config["sources"]))

    def test_frozen_sources_report_metadata_and_hash_verify_when_installed(self) -> None:
        """核对冻结来源元数据，正式文件已安装时再校验哈希与格式。"""
        config = load_production_defaults()
        metadata = defaults_module.resolve_production_source_metadata(config)

        self.assertEqual(
            [entry["configured_path"] for entry in metadata],
            [Path(source["path"]).as_posix() for source in config["sources"]],
        )
        self.assertEqual(
            [entry["content_hash"] for entry in metadata],
            [source["sha256"] for source in config["sources"]],
        )
        if not all(entry["available"] for entry in metadata):
            self.skipTest("正式来源未安装，未执行文件哈希与格式校验")
        self.assertTrue(all(path.is_file() for path in production_source_paths(config)))

    def test_manual_evaluation_set_has_24_complete_reviewed_cases(self) -> None:
        """首轮评测集必须是 20 至 30 条且具备截图要求的人工字段。"""
        config = load_production_defaults()
        dataset_path = Path(__file__).parents[3] / config["evaluation"]["dataset_path"]
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], "multimodal_retrieval_eval_v1")
        self.assertEqual(len(payload["cases"]), 24)
        for case in payload["cases"]:
            self.assertTrue(case["question"])
            self.assertTrue(case["gold_doc_ids"])
            self.assertTrue(case["gold_page_numbers"])
            self.assertIn(case["expected_modality"], {"text", "table", "image"})
            self.assertTrue(case["reference_answer"] or case["key_facts"])
            self.assertTrue(case["human_reviewed"])

    def test_production_embedding_resolves_from_api_key_environment_and_local_switch_is_off(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_API_KEY": "test-key",
                "EMBEDDING_BASE_URL": "https://embedding.example/v1",
            },
            clear=False,
        ):
            resolved = defaults_module.resolve_production_embedding_config()

        self.assertEqual(resolved["mode"], "api")
        self.assertEqual(resolved["provider"], "openai_compatible")
        self.assertEqual(resolved["endpoint_identity"], "https://embedding.example/v1")
        self.assertEqual(resolved["status"], "ready")

        local_config = load_production_defaults()
        local_config["embedding"] = {
            "mode": "local",
            "provider": "huggingface",
            "model": "bge-small-zh-v1.5",
            "dimension": 512,
            "normalized": True,
        }
        with self.assertRaisesRegex(ValueError, "local production embedding is disabled"):
            defaults_module.resolve_production_embedding_config(local_config)

    def test_reviewed_modality_cases_match_physical_pdf_evidence(self) -> None:
        """人工复核过的页码与模态必须对应 PDF 物理页上的实际证据。"""
        config = load_production_defaults()
        dataset_path = Path(__file__).parents[3] / config["evaluation"]["dataset_path"]
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        actual = {
            case["case_id"]: (case["gold_page_numbers"], case["expected_modality"])
            for case in payload["cases"]
        }

        self.assertEqual(actual["causality-003"], ([40], "image"))
        self.assertEqual(actual["causality-009"], ([197], "table"))
        self.assertEqual(actual["causality-011"], ([300], "table"))
        self.assertEqual(actual["why-003"], ([148], "table"))
        self.assertEqual(actual["why-005"], ([51], "image"))

    def test_retrieval_metrics_use_exact_document_and_page_locator(self) -> None:
        """Hit、MRR 和引用定位不得退化成仅匹配两本书的文档级指标。"""
        cases = [
            {
                "case_id": "case-1",
                "question": "q1",
                "gold_doc_ids": ["doc-a"],
                "gold_page_numbers": [7],
                "expected_modality": "text",
            },
            {
                "case_id": "case-2",
                "question": "q2",
                "gold_doc_ids": ["doc-b"],
                "gold_page_numbers": [3],
                "expected_modality": "table",
            },
        ]
        responses = {
            "case-1": [
                {"document_id": "doc-a", "page_number": 6, "modality": "text"},
                {"document_id": "doc-a", "page_number": 7, "modality": "text"},
            ],
            "case-2": [],
        }

        result = evaluate_retrieval_cases(cases, responses, k=5)

        self.assertEqual(result["case_count"], 2)
        self.assertEqual(result["hit_at_5"], 0.5)
        self.assertEqual(result["mrr"], 0.25)
        self.assertEqual(result["citation_location_accuracy"], 0.0)
        self.assertEqual(result["empty_result_rate"], 0.5)

    def test_retrieval_metrics_require_expected_modality(self) -> None:
        """同页正文不能冒充评测题要求的图片或表格证据。"""
        cases = [{
            "case_id": "image-case",
            "question": "q",
            "gold_doc_ids": ["doc-a"],
            "gold_page_numbers": [7],
            "expected_modality": "image",
        }]
        responses = {
            "image-case": [
                {"document_id": "doc-a", "page_number": 7, "modality": "text"},
                {"document_id": "doc-a", "page_number": 7, "modality": "image"},
            ]
        }

        result = evaluate_retrieval_cases(cases, responses, k=5)

        self.assertEqual(result["hit_at_5"], 1.0)
        self.assertEqual(result["mrr"], 0.5)
        self.assertEqual(result["citation_location_accuracy"], 0.0)

    def test_production_coverage_requires_every_gold_page_and_modality(self) -> None:
        """正式检索前必须证明 gold 页存在对应模态的可索引单元。"""
        cases = [
            {"case_id": "text", "gold_doc_ids": ["doc-a"], "gold_page_numbers": [1], "expected_modality": "text"},
            {"case_id": "image", "gold_doc_ids": ["doc-a"], "gold_page_numbers": [2], "expected_modality": "image"},
        ]
        units = [
            {"document_id": "doc-a", "page_number": 1, "modality": "text"},
            {"document_id": "doc-a", "page_number": 2, "modality": "text"},
        ]

        coverage = audit_production_coverage(units, cases)

        self.assertFalse(coverage["passed"])
        self.assertEqual(coverage["covered_gold_pages"], 2)
        self.assertEqual(coverage["total_gold_pages"], 2)
        self.assertEqual(coverage["missing_modality_cases"], ["image"])

    def test_staged_evaluation_uses_the_production_retrieval_trace(self) -> None:
        """暂存门禁必须复用 dense、BM25、rerank 的正式检索链路。"""
        from Agent.knowledge_base.multimodal.production import evaluate_staged_index

        case = {
            "case_id": "runtime-chain",
            "question": "q",
            "gold_doc_ids": ["doc-a"],
            "gold_page_numbers": [7],
            "expected_modality": "image",
        }
        unit = {
            "unit_id": "unit-a",
            "document_id": "doc-a",
            "page_number": 7,
            "modality": "image",
        }
        candidate = {
            "metadata": {
                "unit_id": "unit-a",
                "document_id": "doc-a",
                "page_number": 7,
                "modality": "image",
            }
        }

        class DenseOnlyMiss:
            def similarity_search_with_relevance_scores(self, _question, *, k):
                return []

        config = {"evaluation": {"thresholds": {
            "min_hit_at_5": 1.0,
            "min_mrr": 1.0,
            "min_citation_location_accuracy": 1.0,
            "max_empty_result_rate": 0.0,
        }}}
        with tempfile.TemporaryDirectory() as directory:
            version_dir = Path(directory)
            (version_dir / "units.jsonl").write_text(json.dumps(unit) + "\n", encoding="utf-8")
            (version_dir / "manifest.json").write_text(json.dumps({"documents": []}), encoding="utf-8")
            with (
                patch("Agent.knowledge_base.multimodal.production.Chroma", return_value=DenseOnlyMiss()),
                patch("Agent.knowledge_base.multimodal.production._embeddings", return_value=object()),
                patch("Agent.knowledge_base.multimodal.production.load_evaluation_cases", return_value=[case]),
                patch("Agent.knowledge_base.sparse_retriever.Bm25sSparseRetriever.from_vector_db", return_value=object()),
                patch(
                    "Agent.knowledge_base.query_rag._build_retrieval_trace_with_resources",
                    return_value={"stages": {"final": [candidate]}},
                ) as runtime_trace,
            ):
                report = evaluate_staged_index(version_dir, "collection", config=config)

        self.assertTrue(report["gate"]["passed"])
        runtime_trace.assert_called_once()

    def test_threshold_gate_reports_each_failed_metric(self) -> None:
        """固定阈值必须逐项阻止不合格评测结果。"""
        from Agent.knowledge_base.multimodal.production import apply_thresholds

        metrics = {
            "hit_at_5": 0.69,
            "mrr": 0.49,
            "citation_location_accuracy": 0.59,
            "empty_result_rate": 0.11,
        }
        thresholds = {
            "min_hit_at_5": 0.70,
            "min_mrr": 0.50,
            "min_citation_location_accuracy": 0.60,
            "max_empty_result_rate": 0.10,
        }

        gate = apply_thresholds(metrics, thresholds)

        self.assertFalse(gate["passed"])
        self.assertEqual(
            gate["failures"],
            [
                "hit_at_5_below_minimum",
                "mrr_below_minimum",
                "citation_location_accuracy_below_minimum",
                "empty_result_rate_above_maximum",
            ],
        )

    def test_non_production_sources_cannot_satisfy_the_production_source_contract(self) -> None:
        """任意非生产来源不得被误判为冻结的正式知识源。"""
        config = load_production_defaults()
        production_manifest = {
            "sources": [
                {"relative_path": Path(source["path"]).name, "content_hash": source["sha256"]}
                for source in config["sources"]
            ]
        }

        self.assertFalse(is_production_manifest(production_manifest, config))
        self.assertTrue(is_legacy_production_manifest(production_manifest, config))
        self.assertFalse(
            is_production_manifest(
                {"sources": [{"relative_path": "images/benchmark.png", "content_hash": "a" * 64}]},
                config,
            )
        )

    def test_frozen_identity_does_not_require_local_source_files(self) -> None:
        """已发布 release 的静态身份校验不能因构建者 PDF 缺失而失败。"""
        config = load_production_defaults()
        manifest = {
            "sources": [
                {
                    "source_id": source["source_id"],
                    "document_id": source["document_id"],
                    "relative_path": Path(source["path"]).name,
                    "controlled_path": source["path"],
                    "content_hash": source["sha256"],
                }
                for source in config["sources"]
            ]
        }

        with patch.object(
            production_module,
            "resolve_production_sources",
            side_effect=AssertionError("static identity must not resolve PDFs"),
        ):
            self.assertTrue(has_frozen_production_identity(manifest, config))

        with patch.object(
            production_module,
            "resolve_production_sources",
            side_effect=ValueError("controlled source is unavailable"),
        ):
            self.assertFalse(is_production_manifest(manifest, config))

    def test_frozen_identity_rejects_drift_duplicates_and_unsafe_paths(self) -> None:
        """静态身份校验拒绝来源漂移、重复来源和危险路径元数据。"""
        config = load_production_defaults()
        manifest = {
            "sources": [
                {
                    "source_id": source["source_id"],
                    "document_id": source["document_id"],
                    "relative_path": Path(source["path"]).name,
                    "controlled_path": source["path"],
                    "content_hash": source["sha256"],
                }
                for source in config["sources"]
            ]
        }

        for field, value in (
            ("source_id", "source-drift"),
            ("document_id", "doc_" + "f" * 64),
            ("content_hash", "f" * 64),
        ):
            drifted = json.loads(json.dumps(manifest))
            drifted["sources"][0][field] = value
            self.assertFalse(has_frozen_production_identity(drifted, config), field)

        duplicated = json.loads(json.dumps(manifest))
        duplicated["sources"][1] = json.loads(json.dumps(duplicated["sources"][0]))
        self.assertFalse(has_frozen_production_identity(duplicated, config))

        for field, value in (
            ("relative_path", "../outside.pdf"),
            ("controlled_path", "/etc/passwd"),
            ("controlled_path", "C:\\outside.pdf"),
        ):
            unsafe = json.loads(json.dumps(manifest))
            unsafe["sources"][0][field] = value
            self.assertFalse(has_frozen_production_identity(unsafe, config), value)

    def test_renamed_configured_pdf_keeps_canonical_document_id(self) -> None:
        """配置旧文件名但受控目录只有同 hash 新文件名时仍保持 gold document_id。"""
        content = b"%PDF-1.7\ncanonical"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controlled = root / "controlled"
            controlled.mkdir()
            actual = controlled / "book.pdf"
            actual.write_bytes(content)
            digest = __import__("hashlib").sha256(content).hexdigest()
            config = {
                "schema_version": "multimodal_production_defaults_v1",
                "controlled_source_directories": ["controlled"],
                "sources": [{
                    "source_id": "book-source",
                    "document_id": "doc_" + "a" * 64,
                    "path": "controlled/book(1).pdf",
                    "sha256": digest,
                    "page_count": 1,
                    "required": True,
                }],
                "evaluation": {"dataset_path": "eval.json", "thresholds": {}},
            }
            with patch.object(defaults_module, "ROOT", root):
                resolved = defaults_module.resolve_production_sources(config)
                self.assertEqual(resolved[0]["path"], actual.resolve())
                self.assertEqual(resolved[0]["document_id"], config["sources"][0]["document_id"])
                self.assertTrue(is_production_manifest({
                    "sources": [{
                        "source_id": "book-source",
                        "document_id": config["sources"][0]["document_id"],
                        "relative_path": "controlled/book.pdf",
                        "controlled_path": "controlled/book.pdf",
                        "content_hash": digest,
                    }]
                }, config))

    def test_production_source_hash_requires_exactly_one_controlled_hit(self) -> None:
        """零命中与多命中都必须失败关闭，不能按文件名猜测。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controlled = root / "controlled"
            controlled.mkdir()
            base = {
                "schema_version": "multimodal_production_defaults_v1",
                "controlled_source_directories": ["controlled"],
                "sources": [{
                    "source_id": "book-source",
                    "document_id": "doc_" + "a" * 64,
                    "path": "controlled/book.pdf",
                    "sha256": "b" * 64,
                    "page_count": 1,
                    "required": True,
                }],
                "evaluation": {"dataset_path": "eval.json", "thresholds": {}},
            }
            with patch.object(defaults_module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, r"\(0 matches\)"):
                    defaults_module.resolve_production_sources(base)
                content = b"%PDF-1.7\nsame"
                (controlled / "one.pdf").write_bytes(content)
                (controlled / "two.pdf").write_bytes(content)
                base["sources"][0]["sha256"] = __import__("hashlib").sha256(content).hexdigest()
                with self.assertRaisesRegex(ValueError, r"\(2 matches\)"):
                    defaults_module.resolve_production_sources(base)

    def test_production_source_rejects_extension_signature_and_hash_mismatch(self) -> None:
        """正式来源同时拒绝扩展名、文件签名和哈希错误。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controlled = root / "controlled"
            controlled.mkdir()
            content = b"not a pdf"
            actual = controlled / "book.txt"
            actual.write_bytes(content)
            config = {
                "schema_version": "multimodal_production_defaults_v1",
                "controlled_source_directories": ["controlled"],
                "sources": [{
                    "source_id": "book-source",
                    "document_id": "doc_" + "a" * 64,
                    "path": "controlled/book.pdf",
                    "sha256": __import__("hashlib").sha256(content).hexdigest(),
                    "page_count": 1,
                    "required": True,
                }],
                "evaluation": {"dataset_path": "eval.json", "thresholds": {}},
            }
            with patch.object(defaults_module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "extension mismatch"):
                    defaults_module.resolve_production_sources(config)
                actual.rename(controlled / "book.pdf")
                with self.assertRaisesRegex(ValueError, "signature mismatch"):
                    defaults_module.resolve_production_sources(config)
                config["sources"][0]["sha256"] = "c" * 64
                with self.assertRaisesRegex(ValueError, "0 matches"):
                    defaults_module.resolve_production_sources(config)

    def test_outside_same_hash_source_is_not_formal_and_document_drift_is_rejected(self) -> None:
        """受控目录外同 hash 文件与 document_id 漂移都不能满足正式契约。"""
        content = b"%PDF-1.7\noutside"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "controlled").mkdir()
            outside = root / "outside.pdf"
            outside.write_bytes(content)
            config = {
                "schema_version": "multimodal_production_defaults_v1",
                "controlled_source_directories": ["controlled"],
                "sources": [{
                    "source_id": "book-source",
                    "document_id": "doc_" + "a" * 64,
                    "path": "controlled/book.pdf",
                    "sha256": __import__("hashlib").sha256(content).hexdigest(),
                    "page_count": 1,
                    "required": True,
                }],
                "evaluation": {"dataset_path": "eval.json", "thresholds": {}},
            }
            with patch.object(defaults_module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "0 matches"):
                    defaults_module.resolve_production_sources(config)
            manifest = {"sources": [{
                "source_id": "book-source",
                "document_id": "doc_" + "b" * 64,
                "relative_path": "outside.pdf",
                "content_hash": config["sources"][0]["sha256"],
            }]}
            self.assertFalse(is_production_manifest(manifest, config))

    def test_vision_is_source_authorized_and_disabled_by_default(self) -> None:
        """生产 VLM 默认关闭，启用时必须携带来源级授权而不是页级授权。"""
        config = load_production_defaults()
        base = {
            "parser": config["parser"],
            "build_configuration": {
                "pdf_parser": config["pdf_parser"],
                "vision": {"enabled": False, "local_ocr_enabled": False},
                "embedding": config["embedding"],
            },
            "embedding": config["embedding"],
        }
        self.assertEqual(__import__("Agent.knowledge_base.multimodal.production", fromlist=["validate_production_manifest"]).validate_production_manifest(base, config), [])
        enabled = json.loads(json.dumps(base))
        enabled["build_configuration"]["vision"] = {"enabled": True, "local_ocr_enabled": False}
        self.assertIn("production_source_level_vision_authorization_missing", __import__("Agent.knowledge_base.multimodal.production", fromlist=["validate_production_manifest"]).validate_production_manifest(enabled, config))


if __name__ == "__main__":
    unittest.main()
