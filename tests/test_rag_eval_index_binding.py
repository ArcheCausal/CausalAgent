import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from flask import Flask
import app.rag_eval.routes as routes
import app.rag_eval.isolated_runs as isolated_runs
from Agent.knowledge_base.rag.operation_datasets import benchmark_v2
from app.rag_eval.index_binding import IndexBindingError, IndexBindingGate
from app.rag_eval.isolated_runs import IsolatedRunManager
from tests.support.authorization import rag_eval_route_authorization


class IndexBindingGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run_id = "ingest_test"
        self.version = "mm_test"
        self.embedding = {"provider": "local", "model": "test-model"}
        self.run_dir = self.root / self.run_id
        self.index_dir = self.run_dir / "indexes" / self.version
        self.index_dir.mkdir(parents=True)
        self._write_index()
        manifest_sha = hashlib.sha256((self.index_dir / "manifest.json").read_bytes()).hexdigest()
        self.run = {
            "run_id": self.run_id,
            "kind": "ingestion",
            "status": "staged",
            "index_version": self.version,
            "collection_name": "isolated_test_collection",
            "manifest_sha256": manifest_sha,
            "unit_count": 1,
            "vector_count": 1,
        }
        self.gate = IndexBindingGate(
            lambda run_id: self.run if run_id == self.run_id else (_ for _ in ()).throw(KeyError(run_id)),
            lambda run_id: self.root / run_id,
            lambda: self.embedding,
        )
        self.authorization_stack = ExitStack()
        for authorization_context in rag_eval_route_authorization():
            self.authorization_stack.enter_context(authorization_context)

    def tearDown(self):
        self.authorization_stack.close()
        self.temporary.cleanup()

    def _write_index(self, *, unit_id="unit-1"):
        manifest = {"index_version": self.version, "embedding": self.embedding, "unit_count": 1}
        (self.index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.index_dir / "build_state.json").write_text(
            json.dumps({"status": "staged_complete", "unit_count": 1, "vector_count": 1}), encoding="utf-8"
        )
        (self.index_dir / "units.jsonl").write_text(json.dumps({
            "unit_id": unit_id, "document_id": "doc-1", "page_number": 1,
            "modality": "text", "content_kind": "paragraph",
        }) + "\n", encoding="utf-8")
        (self.index_dir / "chroma").mkdir(exist_ok=True)

    def _candidate(self, *, index_version=None, manifest_sha256=None, unit_id="unit-1"):
        identity = self.gate.resolve_staged_index(self.run_id, self.version)
        return {
            "schema_version": "rag_eval_v1", "dataset_id": "candidate", "dataset_kind": "generated_candidate",
            "source_snapshot": {"index_version": index_version or identity.index_version,
                                "manifest_sha256": manifest_sha256 or identity.manifest_sha256},
            "samples": [{"sample_id": "s1", "question": "问题", "reference_answer": "答案",
                         "expected_claims": ["事实"], "gold_evidence": [{"unit_id": unit_id,
                         "document_id": "doc-1", "page_number": 1, "modality": "text",
                         "content_kind": "paragraph", "bound_index_version": self.version}]}],
        }

    def assertMismatch(self, callback, code="index_binding_mismatch"):
        with self.assertRaises(IndexBindingError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_resolve_returns_complete_identity(self):
        identity = self.gate.resolve_staged_index(self.run_id, self.version)
        self.assertEqual(identity.index_version, self.version)
        self.assertEqual(identity.collection_name, "isolated_test_collection")
        self.assertEqual(identity.embedding_fingerprint, self.embedding)
        self.assertEqual(identity.index_dir, self.index_dir.resolve())

    def test_resolve_rejects_unstaged_run_and_wrong_version(self):
        self.run["status"] = "running"
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, self.version))
        self.run["status"] = "staged"
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, "other"))

    def test_resolve_rejects_traversal_before_index_file_access(self):
        self.run["index_version"] = "../escape"
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, "../escape"))

    def test_resolve_rejects_escape_or_missing_artifact(self):
        escaped = IndexBindingGate(lambda _: self.run, lambda _: self.root / "outside", lambda: self.embedding)
        self.assertMismatch(lambda: escaped.resolve_staged_index(self.run_id, self.version))
        (self.index_dir / "chroma").rmdir()
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, self.version))

    def test_resolve_rejects_hash_collection_embedding_and_count_mismatches(self):
        self.run["manifest_sha256"] = "bad"
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, self.version))
        self.run["manifest_sha256"] = hashlib.sha256((self.index_dir / "manifest.json").read_bytes()).hexdigest()
        self.run["collection_name"] = ""
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, self.version))
        self.run["collection_name"] = "isolated_test_collection"
        self.embedding = {"provider": "different"}
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, self.version))
        self.embedding = {"provider": "local", "model": "test-model"}
        self.run["vector_count"] = 2
        self.assertMismatch(lambda: self.gate.resolve_staged_index(self.run_id, self.version))

    def test_validate_generated_candidate_requires_matching_snapshot_and_locator(self):
        identity = self.gate.resolve_staged_index(self.run_id, self.version)
        self.gate.validate_dataset(self._candidate(), identity, {"generated_candidate"})
        self.assertMismatch(lambda: self.gate.validate_dataset(
            self._candidate(index_version="old"), identity, {"generated_candidate"}))
        self.assertMismatch(lambda: self.gate.validate_dataset(
            self._candidate(manifest_sha256="bad"), identity, {"generated_candidate"}))
        self.assertMismatch(lambda: self.gate.validate_dataset(
            self._candidate(unit_id="missing"), identity, {"generated_candidate"}))

    def test_validate_rejects_disallowed_kind_and_invalid_frozen_generated_gold(self):
        identity = self.gate.resolve_staged_index(self.run_id, self.version)
        self.assertMismatch(lambda: self.gate.validate_dataset(self._candidate(), identity, {"gold_regression"}),
                            "dataset_kind_not_allowed")
        gold = self._candidate()
        gold["dataset_kind"] = "gold_regression"
        gold["samples"][0]["source"] = {"generator": "candidate-generator", "index_binding": {
            "index_version": "old", "manifest_sha256": "bad"}}
        self.assertMismatch(lambda: self.gate.validate_dataset(gold, identity, {"gold_regression"}))

    def test_validate_rejects_generated_gold_without_index_binding(self):
        identity = self.gate.resolve_staged_index(self.run_id, self.version)
        gold = self._candidate()
        gold["dataset_kind"] = "gold_regression"
        gold["samples"][0]["source"] = {"generator": "candidate-generator"}
        self.assertMismatch(lambda: self.gate.validate_dataset(gold, identity, {"gold_regression"}))

    def test_candidate_route_maps_binding_error_to_conflict(self):
        app = Flask(__name__)
        app.register_blueprint(routes.rag_eval_bp)

        class _RejectingManager:
            def start_candidate_generation(self, *args, **kwargs):
                raise IndexBindingError("index_binding_mismatch", "staged index is incomplete")

        with patch.object(routes, "isolated_run_manager", _RejectingManager()):
            response = app.test_client().post("/api/rag_eval/isolated/candidate-runs", json={
                "ingestion_run_id": "ingest_test", "index_version": "mm_test",
            })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "index_binding_mismatch")

    def test_rebind_and_freeze_do_not_write_when_gate_rejects(self):
        run_id = "candidate_20260821_000000_abcdef12"
        ingestion_id = "ingest_20260821_000000_abcdef12"
        with tempfile.TemporaryDirectory() as temporary, patch.object(isolated_runs, "ISOLATED_RUN_ROOT", Path(temporary)):
            run_dir = isolated_runs._run_dir(run_id)
            run_dir.mkdir(parents=True)
            candidate_path = run_dir / "candidate.json"
            review_path = run_dir / "review.json"
            candidate_path.write_text("{}", encoding="utf-8")
            review_path.write_text("{}", encoding="utf-8")
            manager = IsolatedRunManager()
            manager._runs[run_id] = {
                "run_id": run_id, "kind": "candidate_generation", "status": "succeeded",
                "candidate_path": str(candidate_path), "review_manifest_artifact_name": review_path.name,
                "ingestion_run_id": ingestion_id, "index_version": "mm_test",
            }
            with patch.object(manager, "resolve_staged_index", side_effect=IndexBindingError("index_binding_mismatch", "bad index")), \
                 patch.object(benchmark_v2, "rebind_candidate_dataset_to_index") as rebind, \
                 patch.object(benchmark_v2, "freeze_pearl_gold_v2") as freeze:
                with self.assertRaises(IndexBindingError):
                    manager.rebind_candidate_to_current_index(
                        run_id, ingestion_run_id=ingestion_id, index_version="mm_test"
                    )
                with self.assertRaises(IndexBindingError):
                    manager.freeze_candidate_gold_v2(
                        run_id, expected_ingestion_run_id=ingestion_id, expected_index_version="mm_test"
                    )
            rebind.assert_not_called()
            freeze.assert_not_called()
