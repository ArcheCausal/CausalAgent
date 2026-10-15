"""RAG portable release 与 embedding 配置隔离的 contract tests。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Agent.knowledge_base.embedding_runtime import (
    EmbeddingApiCircuitBreaker,
    EmbeddingApiError,
    EmbeddingCircuitOpenError,
    EmbeddingConfiguration,
    classify_embedding_api_error,
)
from Agent.knowledge_base.multimodal.pipeline import MultimodalKnowledgeBaseMaintenance
from Agent.knowledge_base.multimodal.release import (
    ReleaseConcurrencyError,
    ReleaseManager,
    ReleaseValidationError,
    compute_manifest_sha256,
    directory_sha256,
    seal_evaluation_binding,
    seal_manifest,
)
from Agent.knowledge_base.multimodal.pipeline import MultimodalKnowledgeBaseMaintenance
from Agent.knowledge_base.rag_runtime import RagRuntimeConfig


def _embedding(model: str = "portable-test") -> EmbeddingConfiguration:
    return EmbeddingConfiguration(
        mode="local",
        provider="huggingface",
        model=model,
        dimension=3,
        normalized=True,
        path="unused-model-path",
    )


def _write_candidate(root: Path, model: str = "portable-test") -> tuple[Path, str]:
    candidate = root / f"candidate-{model}"
    candidate.mkdir(parents=True)
    config = _embedding(model)
    units_payload = b"unit\n"
    state_payload = json.dumps({"status": "staged_complete", "unit_count": 1, "vector_count": 1}).encode("utf-8")
    (candidate / "units.jsonl").write_bytes(units_payload)
    (candidate / "chroma").mkdir()
    (candidate / "chroma" / "vectors.bin").write_bytes(b"vector")
    chroma_sha256, chroma_size_bytes = directory_sha256(candidate / "chroma")
    (candidate / "build_state.json").write_bytes(state_payload)
    manifest = seal_manifest(
        {
            "schema_version": 6,
            "sources": [
                {
                    "source_id": "source-test",
                    "document_id": "doc_" + "a" * 64,
                    "relative_path": "source.txt",
                    "content_hash": "b" * 64,
                }
            ],
            "parser": {"name": "text", "version": "1"},
            "embedding": config.fingerprint(),
            "embedding_config": config.to_manifest(),
            "chunking": {"schema": "chunk-v1", "size": 256, "overlap": 0},
            "retrieval_index": {"backend": "chroma", "collection": "portable"},
            "counts": {"document_count": 1, "page_count": 1, "unit_count": 1, "vector_count": 1, "issues_count": 0, "partial": False},
            "artifacts": [
                {"path": "units.jsonl", "type": "units", "required": True, "size_bytes": len(units_payload), "sha256": hashlib.sha256(units_payload).hexdigest()},
                {"path": "build_state.json", "type": "build_state", "required": True, "size_bytes": len(state_payload), "sha256": hashlib.sha256(state_payload).hexdigest()},
                {"path": "chroma", "type": "directory", "required": True},
            ],
            "artifact_integrity": {"chroma": {"sha256": chroma_sha256, "size_bytes": chroma_size_bytes}},
        }
    )
    manifest_path = candidate / "manifest.json"
    manifest_path.write_bytes(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    return candidate, str(manifest["release_id"])


class PortableReleaseTests(unittest.TestCase):
    def test_identity_is_deterministic_and_manifest_excludes_secret_values(self) -> None:
        config = EmbeddingConfiguration(
            mode="api",
            provider="openai_compatible",
            model="embedding-test",
            dimension=4,
            api_key_env="RAG_EVAL_EMBEDDING_API_KEY",
            base_url_env="RAG_EVAL_EMBEDDING_BASE_URL",
            endpoint_identity="https://embedding.example/v1",
        )
        base = {
            "schema_version": 6,
            "sources": [],
            "embedding": config.fingerprint(),
            "embedding_config": config.to_manifest(),
            "counts": {"document_count": 0, "page_count": 0, "unit_count": 0, "vector_count": 0, "issues_count": 0, "partial": False},
            "artifacts": [{"path": "chroma", "type": "directory", "required": True}],
        }
        first = seal_manifest(base)
        second = seal_manifest({**base, "created_at": "different"})
        self.assertEqual(first["identity_sha256"], second["identity_sha256"])
        self.assertEqual(first["release_id"], "mm_" + first["identity_sha256"])
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("SHARED_EMBEDDING_KEY_VALUE", serialized)
        self.assertNotIn("base_url", first["embedding_config"])
        with self.assertRaises(ReleaseValidationError):
            seal_manifest({**base, "api_key": "SHARED_EMBEDDING_KEY_VALUE"})

    def test_new_manifest_requires_dimension_and_api_endpoint_identity(self) -> None:
        api = EmbeddingConfiguration(
            mode="api",
            provider="openai_compatible",
            model="embedding-test",
            dimension=4,
            api_key_env="RAG_EVAL_EMBEDDING_API_KEY",
            base_url_env="RAG_EVAL_EMBEDDING_BASE_URL",
            endpoint_identity="https://embedding.example/v1",
        )
        base = {
            "schema_version": 6,
            "sources": [],
            "embedding": api.fingerprint(),
            "embedding_config": api.to_manifest(),
            "counts": {"document_count": 0, "page_count": 0, "unit_count": 0, "vector_count": 0, "issues_count": 0, "partial": False},
            "artifacts": [{"path": "chroma", "type": "directory", "required": True}],
        }
        invalid_dimension = json.loads(json.dumps(base))
        invalid_dimension["embedding_config"]["dimension"] = None
        with self.assertRaisesRegex(ReleaseValidationError, "dimension"):
            seal_manifest(invalid_dimension)

        invalid_endpoint = json.loads(json.dumps(base))
        invalid_endpoint["embedding_config"]["endpoint_identity"] = ""
        with self.assertRaisesRegex(ReleaseValidationError, "endpoint"):
            seal_manifest(invalid_endpoint)

    def test_manifest_counts_are_typed_nonnegative_and_consistent(self) -> None:
        config = _embedding("count-contract")
        base = {
            "schema_version": 6,
            "sources": [],
            "embedding": config.fingerprint(),
            "embedding_config": config.to_manifest(),
            "counts": {
                "document_count": 0,
                "page_count": 0,
                "unit_count": 0,
                "vector_count": 0,
                "issues_count": 0,
                "partial": False,
            },
            "artifacts": [{"path": "chroma", "type": "directory", "required": True}],
        }
        for field, value in {
            "document_count": -1,
            "page_count": "0",
            "unit_count": True,
            "issues_count": 1.5,
            "partial": 0,
        }.items():
            invalid = json.loads(json.dumps(base))
            invalid["counts"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ReleaseValidationError, "counts"):
                seal_manifest(invalid)

        invalid = json.loads(json.dumps(base))
        invalid["counts"]["unit_count"] = 1
        with self.assertRaisesRegex(ReleaseValidationError, "counts"):
            seal_manifest(invalid)

    def test_release_manifest_counts_must_match_build_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            manager = ReleaseManager(root / "formal", root / "runtime" / "active_index.json")
            candidate, _ = _write_candidate(staged, "count-mismatch")
            manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
            manifest["counts"]["unit_count"] = 2
            manifest["counts"]["vector_count"] = 2
            (candidate / "manifest.json").write_bytes(
                (json.dumps(seal_manifest(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            )

            with self.assertRaisesRegex(ReleaseValidationError, "counts"):
                manager.publish(candidate)

    def test_production_active_source_identity_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            manager = ReleaseManager(root / "formal", root / "runtime" / "active_index.json")
            first, first_id = _write_candidate(staged, "identity-first")
            second, second_id = _write_candidate(staged, "identity-second")
            manager.publish(first)
            manager.publish(second, expected_generation=1)

            with patch(
                "Agent.knowledge_base.multimodal.production.validate_production_manifest",
                return_value=[],
            ), patch(
                "Agent.knowledge_base.multimodal.production.has_frozen_production_identity",
                side_effect=[False, True],
            ):
                result = manager.validate_active(enforce_production_policy=True)

            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(result["release_id"], first_id)
            self.assertEqual(manager.status()["active"]["release_id"], first_id)
            self.assertIsNone(manager.status()["fallback"])
            self.assertNotEqual(first_id, second_id)

    def test_maintenance_rollback_enforces_production_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = MultimodalKnowledgeBaseMaintenance(
                asset_root=root / "assets",
                index_root=root / "formal",
                active_config=root / "runtime" / "active_index.json",
            )
            with patch(
                "Agent.knowledge_base.multimodal.pipeline.ReleaseManager.rollback",
                return_value={"status": "rolled_back"},
            ) as rollback:
                service.rollback("mm_" + "a" * 64, expected_generation=3)

            self.assertTrue(rollback.call_args.kwargs["enforce_production_policy"])

    def test_explicit_api_embedding_config_reports_missing_environment(self) -> None:
        from Agent.knowledge_base.embedding_runtime import create_embedding_function, validate_embedding_env_references

        config = EmbeddingConfiguration(
            mode="api",
            provider="openai_compatible",
            model="embedding-test",
            dimension=4,
            api_key_env="RAG_EVAL_EMBEDDING_API_KEY",
            base_url_env="RAG_EVAL_EMBEDDING_BASE_URL",
            endpoint_identity="https://embedding.example/v1",
        )
        with patch.dict(os.environ, {}, clear=True):
            resolved = validate_embedding_env_references(config)
            self.assertEqual(resolved.status, "missing")
            with patch("Agent.knowledge_base.embedding_runtime.log_event") as log_event:
                with self.assertRaises(EmbeddingApiError) as error:
                    create_embedding_function(config, scope="evaluation")
            self.assertEqual(error.exception.category, "unavailable")
            self.assertEqual(log_event.call_args.args[1], "rag.enrichment.degraded")

    def test_publish_keeps_only_active_and_fallback_and_uses_atomic_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            formal = root / "formal"
            pointer = root / "runtime" / "active_index.json"
            manager = ReleaseManager(formal, pointer)
            first_dir, first_id = _write_candidate(staged, "first")
            second_dir, second_id = _write_candidate(staged, "second")
            third_dir, third_id = _write_candidate(staged, "third")

            first = manager.publish(first_dir, expected_generation=0)
            second = manager.publish(second_dir, expected_generation=first["generation"])
            third = manager.publish(third_dir, expected_generation=second["generation"])

            self.assertEqual(third["active"]["release_id"], third_id)
            self.assertEqual(third["fallback"]["release_id"], second_id)
            self.assertFalse((formal / first_id).exists())
            self.assertTrue((formal / second_id).is_dir())
            self.assertTrue((formal / third_id).is_dir())
            pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
            self.assertNotIn("embedding", pointer_payload)
            self.assertEqual(pointer_payload["generation"], 3)

    def test_chroma_content_integrity_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            manager = ReleaseManager(root / "formal", root / "runtime" / "active_index.json")
            candidate, release_id = _write_candidate(staged, "integrity")

            manager.publish(candidate)
            active = root / "formal" / release_id
            manager.validate_active()
            (active / "chroma" / "vectors.bin").write_bytes(b"tampered")

            with self.assertRaises(ReleaseValidationError) as error:
                manager.validate_active()
            self.assertIn(error.exception.code, {"artifact_hash_mismatch", "artifact_size_mismatch"})

    def test_manifest_hash_ignores_platform_newline_representation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, _ = _write_candidate(root, "newlines")
            manifest_path = candidate / "manifest.json"
            lf_path = root / "lf.json"
            crlf_path = root / "crlf.json"
            payload = manifest_path.read_bytes()
            lf_path.write_bytes(payload)
            crlf_path.write_bytes(payload.replace(b"\n", b"\r\n"))

            self.assertEqual(compute_manifest_sha256(lf_path), compute_manifest_sha256(crlf_path))

    def test_evaluation_binding_is_sealed_without_changing_release_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate, release_id = _write_candidate(Path(directory), "binding")
            manifest_path = candidate / "manifest.json"
            binding = {
                "evaluation_run_id": "eval-test",
                "dataset_revision": "dataset-v1",
                "dataset_sha256": "a" * 64,
                "report_sha256": "b" * 64,
                "gate_sha256": "c" * 64,
            }
            seal_evaluation_binding(manifest_path, binding)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["release_id"], release_id)
            self.assertEqual(manifest["evaluation_binding"], binding)

            sealed_bytes = manifest_path.read_bytes()
            self.assertEqual(seal_evaluation_binding(manifest_path, binding), manifest)
            self.assertEqual(manifest_path.read_bytes(), sealed_bytes)
            with self.assertRaisesRegex(ReleaseValidationError, "already sealed"):
                seal_evaluation_binding(
                    manifest_path,
                    {**binding, "dataset_revision": "dataset-v2"},
                )

    def test_formal_materialization_excludes_run_only_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, release_id = _write_candidate(root / "staged", "allowlist")
            (candidate / "checkpoints.sqlite3").write_bytes(b"checkpoint")
            (candidate / "evaluation.json").write_bytes(b"evaluation")
            (candidate / "production_evaluation.json").write_bytes(b"production")

            ReleaseManager(root / "formal", root / "runtime" / "active_index.json").publish(candidate)
            published = root / "formal" / release_id

            self.assertFalse((published / "checkpoints.sqlite3").exists())
            self.assertFalse((published / "evaluation.json").exists())
            self.assertFalse((published / "production_evaluation.json").exists())
            self.assertTrue((published / "chroma").is_dir())

    def test_materialization_or_cas_failure_does_not_change_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            manager = ReleaseManager(root / "formal", root / "runtime" / "active_index.json")
            candidate, release_id = _write_candidate(staged, "candidate")
            published = manager.publish(candidate)
            pointer_before = manager.pointer_path.read_bytes()

            tampered = root / "tampered"
            tampered.mkdir()
            (tampered / "manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ReleaseValidationError):
                manager.publish(tampered)
            self.assertEqual(manager.pointer_path.read_bytes(), pointer_before)
            self.assertEqual(manager.status()["active"]["release_id"], release_id)

            replacement, replacement_id = _write_candidate(staged, "replacement")
            with self.assertRaises(ReleaseConcurrencyError):
                manager.publish(replacement, expected_generation=published["generation"] - 1)
            self.assertEqual(manager.pointer_path.read_bytes(), pointer_before)
            self.assertFalse((root / "formal" / replacement_id).exists())

    def test_active_integrity_failure_quarantines_active_and_promotes_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            manager = ReleaseManager(root / "formal", root / "runtime" / "active_index.json")
            first, first_id = _write_candidate(staged, "first")
            second, second_id = _write_candidate(staged, "second")
            manager.publish(first)
            manager.publish(second, expected_generation=1)
            active_manifest = root / "formal" / second_id / "manifest.json"
            active_manifest.write_text("{\"tampered\":true}\n", encoding="utf-8")

            result = manager.validate_active()

            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(manager.status()["active"]["release_id"], first_id)
            self.assertIsNone(manager.status()["fallback"])
            quarantine = root / "formal" / "quarantine"
            self.assertTrue(any(path.name.startswith(second_id) for path in quarantine.iterdir()))
            audit = (root / "formal" / "rollback_audit.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event":"release_rollback"', audit)

    def test_embedding_api_quota_opens_only_api_circuit(self) -> None:
        breaker = EmbeddingApiCircuitBreaker(failure_threshold=3)

        class QuotaError(Exception):
            status_code = 402

        def fail() -> None:
            raise QuotaError()

        with patch("Agent.knowledge_base.embedding_runtime.log_event") as log_event, self.assertRaises(EmbeddingApiError) as first:
            breaker.call(fail)
        self.assertEqual(first.exception.category, "quota_billing")
        self.assertEqual(log_event.call_args.args[1], "rag.enrichment.degraded")
        with self.assertRaises(EmbeddingCircuitOpenError):
            breaker.call(lambda: "must-not-run")
        self.assertTrue(breaker.is_open)

    def test_free_tier_only_error_is_classified_as_quota_billing(self) -> None:
        class FreeTierOnlyError(Exception):
            status_code = 400
            body = {
                "code": "AllocationQuota.FreeTierOnly",
                "message": "Free quota exhausted.",
            }

        self.assertEqual(
            classify_embedding_api_error(FreeTierOnlyError("Bad request")),
            "quota_billing",
        )

    def test_runtime_loads_embedding_from_active_manifest_without_global_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            config = _embedding("manifest-model")
            manifest = seal_manifest({
                "schema_version": 6,
                "sources": [],
                "embedding": config.fingerprint(),
                "embedding_config": config.to_manifest(),
                "counts": {"document_count": 1, "page_count": 1, "unit_count": 1, "vector_count": 1, "issues_count": 0, "partial": False},
                "artifacts": [{"path": "chroma", "type": "directory", "required": True}],
            })
            release_id = str(manifest["release_id"])
            release_dir = formal / release_id
            release_dir.mkdir(parents=True, exist_ok=True)
            (release_dir / "chroma").mkdir()
            chroma_sha256, chroma_size_bytes = directory_sha256(release_dir / "chroma")
            manifest["artifact_integrity"] = {"chroma": {"sha256": chroma_sha256, "size_bytes": chroma_size_bytes}}
            manifest_path = release_dir / "manifest.json"
            manifest_path.write_bytes(
                (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            )
            (release_dir / "units.jsonl").write_text("unit\n", encoding="utf-8")
            (release_dir / "build_state.json").write_text(
                json.dumps({"status": "staged_complete", "unit_count": 1, "vector_count": 1}),
                encoding="utf-8",
            )
            pointer = root / "runtime" / "active_index.json"
            pointer.parent.mkdir()
            pointer.write_bytes(
                (json.dumps({
                    "schema_version": "multimodal_release_pointer_v1",
                    "generation": 1,
                    "active": {
                        "release_id": release_id,
                        "index_version": release_id,
                        "index_path": f"{release_id}/chroma",
                        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    },
                    "fallback": None,
                }, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            )
            with patch.dict(os.environ, {
                "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(pointer),
                "MULTIMODAL_INDEX_ROOT": str(formal),
                "MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE": "true",
            }), patch("Agent.knowledge_base.rag_runtime.resolve_embedding_runtime_config", side_effect=AssertionError("global resolver used")):
                config = RagRuntimeConfig.from_environment()

            self.assertEqual(config.release_id, release_id)
            self.assertEqual(config.embedding_config["model"], "manifest-model")

    def test_runtime_rejects_manifest_counts_that_disagree_with_build_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            candidate, _ = _write_candidate(formal, "runtime-count-mismatch")
            manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
            manifest["counts"]["unit_count"] = 2
            manifest["counts"]["vector_count"] = 2
            (candidate / "manifest.json").write_bytes(
                (json.dumps(seal_manifest(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            )
            pointer = root / "runtime" / "active_index.json"
            pointer.parent.mkdir()
            pointer.write_bytes(
                (json.dumps(
                    {
                        "schema_version": "multimodal_release_pointer_v1",
                        "generation": 1,
                        "active": {
                            "release_id": manifest["release_id"],
                            "index_version": manifest["release_id"],
                            "index_path": f"{candidate.name}/chroma",
                            "manifest_sha256": compute_manifest_sha256(candidate / "manifest.json"),
                        },
                        "fallback": None,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ) + "\n").encode("utf-8")
            )
            with patch.dict(os.environ, {
                "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(pointer),
                "MULTIMODAL_INDEX_ROOT": str(formal),
                "MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE": "true",
            }):
                with self.assertRaisesRegex(ValueError, "counts"):
                    RagRuntimeConfig.from_environment()


if __name__ == "__main__":
    unittest.main()
