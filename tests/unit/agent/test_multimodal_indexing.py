"""多模态索引构建、质量门禁与发布契约。

文档职责：验证分期构建恢复、版本确定性、质量与完整性门禁、发布前置条件和原 RAG 检索入口的身份保留。

适用范围：修改 multimodal.pipeline、index 或 active pointer 发布规则时使用；解析和远程视觉由同目录其他测试覆盖。
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from Agent.knowledge_base.multimodal.assets import AssetStore
from Agent.knowledge_base.multimodal.index import StagedIndex
from Agent.knowledge_base.multimodal.contracts import (
    IngestionIssue,
    IssueSeverity,
    KnowledgeUnit,
    UnitStatus,
    sha256_bytes,
    stable_id,
)
from Agent.knowledge_base.multimodal.pipeline import MultimodalKnowledgeBaseMaintenance
from Agent.knowledge_base.multimodal.parsers import (
    ParsedDocument,
    ParsedItem,
    _convert_docling_page,
    _rapidocr_probe,
    decide_page_route,
    inspect_source,
    parse_document,
)
from Agent.knowledge_base.rag_runtime import RagRuntimeConfig
from Agent.knowledge_base.multimodal.vision import REQUIRED_MODEL, VisionAnalyzer
from Agent.tool_node.rag_questions import normalize_rag_question_output
from Agent.tool_node.rag_tool_registry import build_rag_tools
from tests.unit.agent.multimodal_fixtures import (
    REPOSITORY_ROOT,
    TEST_LOCAL_EMBEDDING,
    outbound_record as _outbound_record,
    png_bytes as _png_bytes,
    vision_payload as _vision_payload,
)


class MultimodalIndexingTests(unittest.TestCase):
    """索引构建与发布：使用临时目录和受控 stub。"""

    @patch("Agent.knowledge_base.multimodal.index._embeddings")
    @patch("Agent.knowledge_base.multimodal.index.Chroma")
    def test_staged_index_closes_client_after_write(self, chroma: MagicMock, _embeddings: MagicMock) -> None:
        """持久化写入在移动 staged 目录前释放 SQLite 客户端。"""
        db = MagicMock()
        db._collection.count.return_value = 1
        chroma.return_value = db
        unit = MagicMock(retrieval_text="causal text", unit_id="unit_1")
        unit.chroma_metadata.return_value = {}
        with tempfile.TemporaryDirectory() as directory:
            count = StagedIndex(Path(directory), "test_collection").write([unit])
        self.assertEqual(count, 1)
        db._client.close.assert_called_once_with()

    def test_inspect_is_deterministic_and_does_not_create_index(self) -> None:
        """inspect 只产生 manifest，不创建索引或资源目录。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "note.md"; source.write_text("因果图", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "runtime" / "active.json")
            first = service.inspect([str(source)]); second = service.inspect([str(source)])
            self.assertEqual(first["manifest"], second["manifest"])
            self.assertEqual(first["manifest"]["parser"], "docling")
            self.assertIn("parser", first["configuration"])
            self.assertIn("docling_layout_model_available", first["configuration"]["parser"])
            self.assertFalse((root / "indexes").exists())
            self.assertFalse((root / "assets").exists())

    def test_evaluate_rejects_any_required_source_parse_error(self) -> None:
        """任一默认 required 来源解析失败时不得发布混合成功版本。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); version = "mm_" + "a" * 20; index_dir = root / "indexes" / version; index_dir.mkdir(parents=True)
            unit = KnowledgeUnit(unit_id="unit_" + "a" * 64, document_id="doc_" + "b" * 64, modality="text", content_kind="paragraph", raw_text="正文", retrieval_text="类型：paragraph\n正文", content_hash="c" * 64, parser_name="text", parser_version="1", embedding_provider="huggingface", embedding_model="model", status=UnitStatus.COMPLETED)
            (index_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8")
            issue = IngestionIssue(code="pdf_parse_failed", message="PDF 失败", severity=IssueSeverity.ERROR)
            (index_dir / "issues.jsonl").write_text(issue.model_dump_json() + "\n", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "runtime" / "active.json")
            manifest = {"index_version": version, "embedding": __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()}
            (index_dir / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
            with patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.count", return_value=1):
                result = service.evaluate(version)
            self.assertFalse(result["passed"])
            self.assertIn("required_source_failed", result["failures"])

    def test_evaluate_rejects_missing_original_or_parser_audit_assets(self) -> None:
        """发布门禁必须验证来源和解析器原始产物均可回读。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); version = "mm_" + "a" * 20; index_dir = root / "indexes" / version; index_dir.mkdir(parents=True)
            unit = KnowledgeUnit(unit_id="unit_" + "a" * 64, document_id="doc_" + "b" * 64, modality="text", content_kind="paragraph", raw_text="正文", retrieval_text="类型：paragraph\n正文", content_hash="c" * 64, parser_name="text", parser_version="1", embedding_provider="huggingface", embedding_model="model", status=UnitStatus.COMPLETED)
            (index_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8")
            (index_dir / "issues.jsonl").write_text("", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "runtime" / "active.json")
            manifest = {"index_version": version, "embedding": __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint(), "sources": [], "documents": [{"document_id": unit.document_id, "content_hash": "c" * 64, "source_asset_uri": "missing/source.txt", "parser_artifact_uris": ["missing/parser.json"]}]}
            (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.count", return_value=1):
                result = service.evaluate(version)
            self.assertFalse(result["passed"])
            self.assertIn("missing_audit_asset", result["failures"])

    def test_evaluate_rejects_orphaned_standardized_unit(self) -> None:
        """标准化单元必须关联到 manifest 中经哈希验证的原始资料。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); version = "mm_" + "a" * 20; index_dir = root / "indexes" / version; index_dir.mkdir(parents=True)
            unit = KnowledgeUnit(unit_id="unit_" + "a" * 64, document_id="doc_" + "b" * 64, modality="text", content_kind="paragraph", raw_text="正文", retrieval_text="类型：paragraph\n正文", content_hash="c" * 64, parser_name="text", parser_version="1", embedding_provider="huggingface", embedding_model="model", status=UnitStatus.COMPLETED)
            (index_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8")
            (index_dir / "issues.jsonl").write_text("", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "runtime" / "active.json")
            manifest = {"index_version": version, "embedding": __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint(), "sources": [], "documents": []}
            (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.count", return_value=1):
                result = service.evaluate(version)
            self.assertFalse(result["passed"])
            self.assertIn("missing_document_audit_chain", result["failures"])

    def test_directory_scan_preserves_relative_paths_for_same_names(self) -> None:
        """同一目录树中的同名文件必须保持不同来源路径。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source_root = root / "sources"
            for folder, text in (("one", "甲"), ("two", "乙")):
                folder_path = source_root / folder; folder_path.mkdir(parents=True)
                (folder_path / "same.md").write_text(text, encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "runtime" / "active.json")
            paths = [entry["relative_path"] for entry in service.inspect([str(source_root)])["manifest"]["sources"]]
            self.assertEqual(paths, ["sources/one/same.md", "sources/two/same.md"])

    def test_unenriched_title_only_image_is_not_indexed(self) -> None:
        """未增强且无本地正文的独立图片不得作为低价值标题向量写入。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "image.png"; source.write_bytes(_png_bytes())
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            result = service.ingest([str(source)], embedding_config=TEST_LOCAL_EMBEDDING)
            manifest = json.loads((root / "indexes" / result["index_version"] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result["unit_count"], 0)
            self.assertEqual(manifest["quality_observations"]["low_value_images_skipped"], 1)

    def test_maintenance_run_is_idempotent_and_stops_before_publish_on_gate_failure(self) -> None:
        """维护编排复用既有版本，门禁失败或取消都不得切换 active pointer。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.md"; source.write_text("正文", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            version = service._index_version(service._manifest(service._scan([str(source)])[0]))
            version_dir = root / "indexes" / version
            version_dir.mkdir(parents=True)
            for name in ("manifest.json", "units.jsonl", "issues.jsonl"):
                (version_dir / name).write_text("{}" if name.endswith(".json") else "", encoding="utf-8")
            (version_dir / "build_state.json").write_text(json.dumps({"status": "staged_complete", "unit_count": 0, "vector_count": 0}), encoding="utf-8")
            with patch.object(service, "ingest") as ingest, patch.object(service, "evaluate", return_value={"passed": False, "failures": ["quality_gate_failed"]}), patch.object(service, "publish") as publish:
                result = service.run([str(source)])
            self.assertEqual(result["status"], "gate_failed"); ingest.assert_not_called(); publish.assert_not_called()
            with patch.object(service, "publish") as publish:
                result = service.run([str(source)], cancel_check=lambda: True)
            self.assertEqual(result["status"], "cancelled"); publish.assert_not_called()

    def test_only_complete_staged_version_can_be_reused(self) -> None:
        """崩溃留下的确定性版本目录不得被 run 当作已完成索引复用。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = "mm_" + "a" * 20
            version_dir = root / "indexes" / version
            version_dir.mkdir(parents=True)
            service = MultimodalKnowledgeBaseMaintenance(
                asset_root=root / "assets",
                index_root=root / "indexes",
                active_config=root / "active.json",
            )

            self.assertFalse(service._is_reusable_staged_version(version))
            (version_dir / "build_state.json").write_text(
                json.dumps({"status": "staged_complete", "unit_count": 2, "vector_count": 1}),
                encoding="utf-8",
            )
            self.assertFalse(service._is_reusable_staged_version(version))
            (version_dir / "build_state.json").write_text(
                json.dumps({"status": "staged_complete", "unit_count": 0, "vector_count": 0}),
                encoding="utf-8",
            )
            for name in ("manifest.json", "units.jsonl", "issues.jsonl"):
                (version_dir / name).write_text("{}" if name.endswith(".json") else "", encoding="utf-8")
            self.assertTrue(service._is_reusable_staged_version(version))

    def test_failed_vector_build_resumes_from_page_checkpoints(self) -> None:
        """页解析完成后向量写入失败，重试不得再次运行已完成页面。"""
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "two-pages.pdf"
            pdf = canvas.Canvas(str(source)); pdf.drawString(72, 720, "page one"); pdf.showPage(); pdf.drawString(72, 720, "page two"); pdf.save()
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            parsed_pages = [
                ParsedDocument("docling", "2.115.0", (ParsedItem("text", "paragraph", raw_text="page one has enough searchable causal inference content", page_number=1),), raw_artifacts=(("docling_page_0001.json", b"{}"),)),
                ParsedDocument("docling", "2.115.0", (ParsedItem("text", "paragraph", raw_text="page two has enough searchable causal inference content", page_number=2),), raw_artifacts=(("docling_page_0002.json", b"{}"),)),
            ]
            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", side_effect=parsed_pages) as parse_page, patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.write", side_effect=RuntimeError("vector failed")):
                with self.assertRaisesRegex(RuntimeError, "vector failed"):
                    service.ingest([str(source)], embedding_config=TEST_LOCAL_EMBEDDING)
            self.assertEqual(parse_page.call_count, 2)

            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page") as parse_page, patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.write", return_value=2):
                result = service.ingest([str(source)], embedding_config=TEST_LOCAL_EMBEDDING)
            parse_page.assert_not_called()
            self.assertEqual(result["unit_count"], 2)
            self.assertEqual(result["vector_count"], 2)

    def test_retry_candidate_reuses_only_non_error_page_checkpoints(self) -> None:
        """新的 retry 候选应只重新解析带 ERROR issue 的页，不能修改旧版本。"""
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "three-pages.pdf"
            pdf = canvas.Canvas(str(source))
            for page in ("one", "two", "three"):
                pdf.drawString(72, 720, page); pdf.showPage()
            pdf.save()
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            failed_issue = IngestionIssue(code="pdf_page_parse_failed", message="transient", severity=IssueSeverity.ERROR, blocking=True, source_path=str(source))
            failed = ParsedDocument("docling", "2.115.0", (), issues=(failed_issue,), raw_artifacts=(("docling_page_0001.json", b"{}"),))
            stable_two = ParsedDocument("docling", "2.115.0", (ParsedItem("text", "paragraph", raw_text="page two has enough searchable causal inference content", page_number=2),), raw_artifacts=(("docling_page_0002.json", b"{}"),))
            stable_three = ParsedDocument("docling", "2.115.0", (ParsedItem("text", "paragraph", raw_text="page three has enough searchable causal inference content", page_number=3),), raw_artifacts=(("docling_page_0003.json", b"{}"),))
            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", side_effect=[failed, stable_two, stable_three]), patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.write", side_effect=[2, 3]):
                original = service.ingest([str(source)], embedding_config=TEST_LOCAL_EMBEDDING)
                original_manifest = json.loads((root / "indexes" / original["index_version"] / "manifest.json").read_text(encoding="utf-8"))
                repaired = ParsedDocument("docling", "2.115.0", (ParsedItem("text", "paragraph", raw_text="page one now parses after resources recovered", page_number=1),), raw_artifacts=(("docling_page_0001.json", b"{}"),))
                with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=repaired) as parse_page:
                    retry = service.ingest([str(source)], retry_failed=True, retry_generation=1, retry_from_index_version=original["index_version"], embedding_config=TEST_LOCAL_EMBEDDING)
            parse_page.assert_called_once_with(source, "docling", 1)
            self.assertNotEqual(retry["index_version"], original["index_version"])
            self.assertEqual(retry["unit_count"], 3)
            self.assertEqual(original_manifest["index_version"], original["index_version"])

    def test_index_version_changes_with_embedding_or_vision_configuration(self) -> None:
        """影响向量或增强产物的配置变化必须生成新不可变版本。"""
        with tempfile.TemporaryDirectory() as directory:
            service = MultimodalKnowledgeBaseMaintenance(asset_root=Path(directory) / "assets", index_root=Path(directory) / "indexes", active_config=Path(directory) / "active.json")
            entries = [{"path": "unused", "relative_path": "source.md", "content_hash": "a" * 64}]
            baseline = service._index_version(service._manifest(entries, allow_remote_data=False))
            with patch.dict(os.environ, {"VISION_MODEL": "changed"}):
                changed_vision = service._index_version(service._manifest(entries, allow_remote_data=False))
            with patch("Agent.knowledge_base.multimodal.pipeline.embedding_fingerprint", return_value={"provider": "openai_compatible", "model": "changed", "mode": "api", "normalized": False}):
                changed_embedding = service._index_version(service._manifest(entries, allow_remote_data=False))
            self.assertNotEqual(baseline, changed_vision); self.assertNotEqual(baseline, changed_embedding)

    def test_quality_gate_blocks_complete_but_insufficient_enrichment(self) -> None:
        """质量不足即使完整性通过也必须阻止发布。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); version = "mm_" + "a" * 20; index_dir = root / "indexes" / version; index_dir.mkdir(parents=True)
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "runtime" / "active.json")
            content = b"source"; source_hash = sha256_bytes(content); document_id = stable_id("doc", {"path": "source.md", "content_hash": source_hash})
            source_uri = AssetStore(root / "assets").put(document_id, "source.md", content, category="source")
            unit = KnowledgeUnit(unit_id="unit_" + "a" * 64, document_id=document_id, modality="text", content_kind="paragraph", raw_text="正文", retrieval_text="正文", content_hash=sha256_bytes("正文".encode()), parser_name="text", parser_version="1", embedding_provider=embedding["provider"], embedding_model=embedding["model"], status=UnitStatus.COMPLETED)
            manifest = {"index_version": version, "embedding": embedding, "build_configuration": {}, "sources": [{"relative_path": "source.md", "content_hash": source_hash}], "documents": [{"document_id": document_id, "relative_path": "source.md", "content_hash": source_hash, "source_asset_uri": source_uri, "parser_artifacts": []}], "quality_policy": {"min_eligible_images": 1, "min_enriched_images": 1, "min_enrichment_rate": 1.0, "max_vision_failed_images": 0}, "quality_observations": {"eligible_images": 1, "enriched_images": 0, "vision_failed_images": 0, "skipped_images": 1}}
            (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8"); (index_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8"); (index_dir / "issues.jsonl").write_text("", encoding="utf-8")
            with patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.count", return_value=1):
                result = service.evaluate(version)
            self.assertFalse(result["passed"]); self.assertTrue(result["quality"]["failures"]); self.assertEqual(result["failures"], ["quality_gate_failed"])
            with self.assertRaisesRegex(ValueError, "blocking evaluation"):
                service.publish(version)
            manifest["quality_observations"]["enriched_images"] = 1
            (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.count", return_value=1):
                result = service.evaluate(version)
            self.assertTrue(result["passed"])
            with self.assertRaisesRegex(ValueError, "non-production manifest"):
                service.publish(version)

    def test_non_production_manifest_cannot_publish_active_pointer(self) -> None:
        """完整性与质量通过也不能让非正式来源切换 active pointer。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = "mm_" + "c" * 20
            index_dir = root / "indexes" / version
            index_dir.mkdir(parents=True)
            manifest = {
                "schema_version": 5,
                "index_version": version,
                "sources": [{"source_id": "uploaded", "document_id": "doc_" + "d" * 64, "relative_path": "uploaded.pdf", "content_hash": "a" * 64}],
                "documents": [],
                "build_configuration": {},
                "quality_policy": {},
                "quality_observations": {},
            }
            (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (index_dir / "evaluation.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            with self.assertRaisesRegex(ValueError, "non-production manifest"):
                service.publish(version)
            self.assertFalse((root / "active.json").exists())

    def test_legacy_manifest_cannot_pass_new_quality_or_publication_gates(self) -> None:
        """缺少 P0 构建和质量契约的历史版本不得被默认零值放行。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); version = "mm_" + "a" * 20; index_dir = root / "indexes" / version; index_dir.mkdir(parents=True)
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            unit = KnowledgeUnit(unit_id="unit_" + "a" * 64, document_id="doc_" + "b" * 64, modality="text", content_kind="paragraph", raw_text="正文", retrieval_text="正文", content_hash=sha256_bytes("正文".encode()), parser_name="text", parser_version="1", embedding_provider=embedding["provider"], embedding_model=embedding["model"], status=UnitStatus.COMPLETED)
            (index_dir / "manifest.json").write_text(json.dumps({"index_version": version, "embedding": embedding, "sources": [], "documents": []}), encoding="utf-8")
            (index_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8"); (index_dir / "issues.jsonl").write_text("", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            with patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.count", return_value=1):
                result = service.evaluate(version)
            self.assertFalse(result["passed"]); self.assertIn("legacy_manifest_missing_build_configuration", result["failures"]); self.assertIn("legacy_manifest_missing_quality_policy", result["quality"]["failures"])
            (index_dir / "evaluation.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-production manifest"):
                service.publish(version)

    def test_multimodal_is_the_default_and_only_rag_corpus(self) -> None:
        """未声明 corpus 时必须默认多模态，旧 medical 输入必须显式失败。"""
        payload = {"questions": [{"question": "解释文档", "intent": "证据", "priority": "high", "why_needed": "需要页面"}]}
        questions = normalize_rag_question_output(payload, 3)
        self.assertEqual(questions[0]["corpus"], "multimodal")

        payload["questions"][0]["corpus"] = "medical"
        with self.assertRaises(ValueError):
            normalize_rag_question_output(payload, 3)

    def test_default_rag_registry_keeps_original_tool_name(self) -> None:
        """默认 RAG 继续使用原工具名，但底层只绑定多模态 Service。"""
        self.assertEqual([tool.name for tool in build_rag_tools()], ["rag_enrichment_search"])

    def test_rag_runtime_defaults_to_published_multimodal_index(self) -> None:
        """原 RagRuntime 必须从多模态 active pointer 解析默认 collection。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = "mm_test"
            version_dir = root / "indexes" / version
            (version_dir / "chroma").mkdir(parents=True)
            manifest = {"index_version": version, "embedding": {"provider": "local", "model": "model", "mode": "local", "normalized": True}}
            manifest_path = version_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            active_path = root / "runtime" / "active.json"
            active_path.parent.mkdir()
            active_path.write_text(json.dumps({
                "index_version": version,
                "index_path": f"{version}/chroma",
                "collection_name": "causal_multimodal_mm_test",
                "manifest_sha256": __import__("hashlib").sha256(manifest_path.read_bytes()).hexdigest(),
                "embedding": manifest["embedding"],
            }), encoding="utf-8")
            embedding_config = {"status": "ready", "mode": "local", "provider": "local", "model": "model", "path": "model"}
            with patch.dict(os.environ, {"MULTIMODAL_INDEX_ROOT": str(root / "indexes"), "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(active_path), "MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE": "true"}), patch("Agent.knowledge_base.rag_runtime.resolve_embedding_runtime_config", return_value=embedding_config):
                config = RagRuntimeConfig.from_environment()
            self.assertEqual(Path(config.vector_db_dir), version_dir / "chroma")
            self.assertEqual(config.collection_name, "causal_multimodal_mm_test")
            self.assertEqual(config.release_id, version)

    def test_original_rag_metadata_normalizer_preserves_multimodal_identity(self) -> None:
        """原检索链必须识别多模态 document、page、asset 和内容类型字段。"""
        from Agent.knowledge_base.query_rag import _normalize_chunk_metadata

        metadata = _normalize_chunk_metadata({
            "document_id": "doc_1",
            "page_number": 3,
            "asset_uri": "doc_1/images/chart.png",
            "modality": "image",
            "content_kind": "chart",
            "content_hash": "abc",
        }, "图表检索文本")
        self.assertEqual(metadata["doc_id"], "doc_1")
        self.assertEqual(metadata["page"], 3)
        self.assertEqual(metadata["source_name"], "chart.png")
        self.assertEqual(metadata["doc_type"], "chart")
        self.assertEqual(metadata["corpus"], "multimodal")

    def test_local_parse_checkpoint_reuse_skips_docling_across_vision_policy(self) -> None:
        """--reuse-local-checkpoints-from 必须只复用本地解析，跨视觉策略跳过 Docling。"""
        from reportlab.pdfgen import canvas
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "page.pdf"
            pdf = canvas.Canvas(str(source)); pdf.drawString(72, 720, "causal inference content"); pdf.save()
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            parsed = ParsedDocument("docling", "2.115.0", (ParsedItem("text", "paragraph", raw_text="causal inference content", page_number=1),), raw_artifacts=(("docling_page_0001.json", b"{}"),))
            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=parsed), patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.write", return_value=1):
                ocr_only = service.ingest([str(source)], embedding_config=TEST_LOCAL_EMBEDDING)
            checkpoint_database = root / "indexes" / ocr_only["index_version"] / "checkpoints.sqlite3"
            self.assertTrue(checkpoint_database.is_file())
            connection = sqlite3.connect(checkpoint_database)
            try:
                rows = connection.execute("SELECT checkpoint_json FROM local_parse_checkpoints").fetchall()
            finally:
                connection.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0][0])["schema_version"], "local-parse-v2")
            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page") as parse_page, patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.write", return_value=1):
                source_id = service._scan([str(source)])[0][0]["source_id"]
                vlm_version = service.ingest([str(source)], allow_remote_data=True, authorized_source_ids=[source_id], max_images=1, reuse_local_from_index_version=ocr_only["index_version"], embedding_config=TEST_LOCAL_EMBEDDING)
            parse_page.assert_not_called()
            self.assertNotEqual(vlm_version["index_version"], ocr_only["index_version"])

    def test_reuse_local_and_retry_are_mutually_exclusive(self) -> None:
        """--reuse-local-checkpoints-from 和 --retry-from-index-version 不得同时使用。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "s.md"; source.write_text("正文", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                service.ingest([str(source)], retry_failed=True, retry_from_index_version="mm_" + "a" * 20, reuse_local_from_index_version="mm_" + "b" * 20)

    def test_invalid_local_checkpoint_reparses_instead_of_silently_reusing(self) -> None:
        """schema、资源或 parser 契约失配时必须回退本地解析。"""
        from reportlab.pdfgen import canvas
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "page.pdf"
            pdf = canvas.Canvas(str(source)); pdf.drawString(72, 720, "causal inference content"); pdf.save()
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            parsed = ParsedDocument("docling", "2.115.0", (ParsedItem("image", "image", raw_text="OCR text", asset_bytes=b"image-bytes", asset_name="page.png", page_number=1),), raw_artifacts=(("docling_page_0001.json", b"{}"),))
            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=parsed), patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.write", return_value=1):
                ocr_only = service.ingest([str(source)], embedding_config=TEST_LOCAL_EMBEDDING)
            checkpoint_database = root / "indexes" / ocr_only["index_version"] / "checkpoints.sqlite3"
            connection = sqlite3.connect(checkpoint_database)
            try:
                row = connection.execute("SELECT checkpoint_json FROM local_parse_checkpoints").fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(row)
            checkpoint = json.loads(row[0])
            source_asset = root / "assets" / checkpoint["items"][0]["asset_uri"]
            source_asset.unlink()
            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=parsed) as parse_page, patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.write", return_value=1):
                source_id = service._scan([str(source)])[0][0]["source_id"]
                service.ingest([str(source)], allow_remote_data=True, authorized_source_ids=[source_id], max_images=1, reuse_local_from_index_version=ocr_only["index_version"], embedding_config=TEST_LOCAL_EMBEDDING)
            parse_page.assert_called_once()

    def test_local_checkpoint_requires_schema_and_matching_parser_contract(self) -> None:
        """不带 schema 或 Docling 版本不匹配的 checkpoint 不得被复用。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            store = AssetStore(root / "assets")
            entry = {"relative_path": "page.pdf", "content_hash": "a" * 64}
            parsed = ParsedDocument("docling", service._docling_version(), (ParsedItem("text", "paragraph", raw_text="text", page_number=1),))
            checkpoint = service._build_local_parse_checkpoint(root, "doc_" + "a" * 64, 1, parsed, entry, 1, store)
            checkpoint.pop("schema_version")
            service._write_local_parse_checkpoint(root, "doc_" + "a" * 64, 1, checkpoint)
            self.assertIsNone(service._reusable_local_parse_checkpoint(root, "doc_" + "a" * 64, 1, entry, 1, store))
            checkpoint["schema_version"] = "local-parse-v2"
            checkpoint["contract"]["parser"]["version"] = "mismatch"
            service._write_local_parse_checkpoint(root, "doc_" + "a" * 64, 1, checkpoint)
            self.assertIsNone(service._reusable_local_parse_checkpoint(root, "doc_" + "a" * 64, 1, entry, 1, store))

    def test_manifest_includes_text_split_parameters(self) -> None:
        """文本切分参数必须写入 manifest.build_configuration，策略变化可生成新版本。"""
        from Agent.knowledge_base.multimodal.parsers import TEXT_SPLIT
        service = MultimodalKnowledgeBaseMaintenance()
        manifest = service._manifest([], allow_remote_data=False)
        self.assertEqual(manifest["build_configuration"]["text_split"], TEXT_SPLIT)

    def test_source_signature_mismatch_is_blocking(self) -> None:
        """伪装成 PDF 的图片必须在解析和外发前被拒绝。"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "misleading.pdf"
            source.write_bytes(_png_bytes())
            issue = inspect_source(source)
            self.assertIsNotNone(issue)
            self.assertEqual(issue.code, "source_format_mismatch")
            self.assertTrue(issue.blocking)


if __name__ == "__main__":
    unittest.main()
