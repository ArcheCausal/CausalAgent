"""多模态隔离 staged 产物晋级正式候选目录的契约测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agent.knowledge_base.multimodal.assets import AssetStore
from Agent.knowledge_base.multimodal.contracts import KnowledgeUnit, UnitStatus
from Agent.knowledge_base.multimodal.index import embedding_fingerprint
from Agent.knowledge_base.multimodal.pipeline import MultimodalKnowledgeBaseMaintenance


class MultimodalReleaseTests(unittest.TestCase):
    def test_promote_staged_copies_index_without_formal_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged_index_root = root / "staged" / "indexes"
            staged_asset_root = root / "staged" / "assets"
            formal_index_root = root / "formal" / "indexes"
            formal_asset_root = root / "formal" / "assets"
            version_dir = staged_index_root / "mm_release_test"
            version_dir.mkdir(parents=True)
            (version_dir / "manifest.json").write_text(json.dumps({"index_version": "mm_release_test"}), encoding="utf-8")
            (version_dir / "units.jsonl").write_text("unit\n", encoding="utf-8")
            asset = staged_asset_root / "doc" / "images" / "page.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"asset")

            maintenance = MultimodalKnowledgeBaseMaintenance(
                asset_root=formal_asset_root,
                index_root=formal_index_root,
                active_config=root / "formal" / "active_index.json",
            )
            result = maintenance.promote_staged(
                source_index_root=staged_index_root,
                source_asset_root=staged_asset_root,
                index_version="mm_release_test",
            )

            self.assertEqual(result["status"], "promoted")
            self.assertTrue((formal_index_root / "mm_release_test" / "manifest.json").is_file())
            self.assertFalse((formal_asset_root / "doc" / "images" / "page.png").exists())
            self.assertEqual(asset.read_bytes(), b"asset")
            self.assertIsNone(maintenance.registry.read())

            reused = maintenance.promote_staged(
                source_index_root=staged_index_root,
                source_asset_root=staged_asset_root,
                index_version="mm_release_test",
            )
            self.assertEqual(reused["status"], "reused")

    def test_formal_revalidation_allows_external_parse_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document_id = "doc_" + "b" * 64
            unit = KnowledgeUnit(
                unit_id="unit_" + "a" * 64,
                document_id=document_id,
                modality="text",
                content_kind="paragraph",
                raw_text="正文",
                retrieval_text="正文",
                content_hash="c" * 64,
                parser_name="text",
                parser_version="1",
                embedding_provider="huggingface",
                embedding_model="model",
                status=UnitStatus.COMPLETED,
            )
            manifest = {
                "sources": [{
                    "source_id": "source_a",
                    "document_id": document_id,
                    "relative_path": "book.pdf",
                    "content_hash": "c" * 64,
                }],
                "documents": [{
                    "source_id": "source_a",
                    "document_id": document_id,
                    "relative_path": "book.pdf",
                    "content_hash": "c" * 64,
                    "source_asset_uri": "missing/source.pdf",
                    "parser_artifacts": [{
                        "name": "docling_page_0001.json",
                        "asset_uri": "missing/page.json",
                        "content_hash": "d" * 64,
                    }],
                }],
            }
            maintenance = MultimodalKnowledgeBaseMaintenance(
                asset_root=root / "assets",
                index_root=root / "indexes",
                active_config=root / "active_index.json",
            )
            store = AssetStore(root / "assets")

            self.assertEqual(maintenance._audit_manifest_chain(manifest, [unit], store, require_assets=False), [])
            self.assertEqual(maintenance._audit_manifest_chain(manifest, [unit], store, require_assets=True), ["missing_audit_asset"])

    def test_formal_evaluation_skips_external_parse_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version = "mm_" + "c" * 20
            index_dir = root / "indexes" / version
            index_dir.mkdir(parents=True)
            document_id = "doc_" + "d" * 64
            unit = KnowledgeUnit(
                unit_id="unit_" + "e" * 64,
                document_id=document_id,
                modality="text",
                content_kind="paragraph",
                raw_text="正文",
                retrieval_text="正文",
                content_hash="f" * 64,
                parser_name="text",
                parser_version="1",
                embedding_provider="huggingface",
                embedding_model="model",
                status=UnitStatus.COMPLETED,
            )
            manifest = {
                "schema_version": 2,
                "index_version": version,
                "embedding": embedding_fingerprint(),
                "build_configuration": {},
                "quality_policy": {"min_eligible_images": 0, "min_enriched_images": 0, "min_enrichment_rate": 0},
                "quality_observations": {},
                "sources": [{
                    "source_id": "source_a",
                    "document_id": document_id,
                    "relative_path": "book.pdf",
                    "content_hash": "a" * 64,
                }],
                "documents": [{
                    "source_id": "source_a",
                    "document_id": document_id,
                    "relative_path": "book.pdf",
                    "content_hash": "a" * 64,
                    "source_asset_uri": "missing/source.pdf",
                    "parser_artifacts": [{
                        "name": "docling_page_0001.json",
                        "asset_uri": "missing/page.json",
                        "content_hash": "b" * 64,
                    }],
                }],
            }
            (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (index_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8")
            (index_dir / "issues.jsonl").write_text("", encoding="utf-8")
            maintenance = MultimodalKnowledgeBaseMaintenance(
                asset_root=root / "assets",
                index_root=root / "indexes",
                active_config=root / "active_index.json",
            )

            with patch("Agent.knowledge_base.multimodal.pipeline.StagedIndex.count", return_value=1):
                result = maintenance.evaluate(version, require_assets=False)

            self.assertTrue(result["passed"])
            self.assertNotIn("missing_asset", result["failures"])
            self.assertNotIn("missing_audit_asset", result["failures"])


if __name__ == "__main__":
    unittest.main()
