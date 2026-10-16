"""不可变 R5 数据集注册中心的行为与 schema 合同。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from app.rag_eval.dataset_registry import DatasetRef, DatasetRegistry, DatasetRevisionConflict


class MemoryRepository:
    """测试用内存 adapter，保留生产 repository 所需的最小协议。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}

    def find(self, reference: DatasetRef):
        row = self.rows.get((reference.dataset_id, reference.dataset_revision))
        return dict(row) if row else None

    def insert(self, metadata: dict) -> None:
        reference = (metadata["dataset_id"], metadata["dataset_revision"])
        if reference in self.rows:
            raise ValueError("duplicate dataset identity")
        self.rows[reference] = dict(metadata)

    def list_datasets(self, *, dataset_kind=None, lifecycle_status=None, page=1, page_size=50):
        rows = list(self.rows.values())
        if dataset_kind is not None:
            rows = [row for row in rows if row["dataset_kind"] == dataset_kind]
        if lifecycle_status is not None:
            rows = [row for row in rows if row["lifecycle_status"] == lifecycle_status]
        rows.sort(key=lambda row: (row["created_at"], row["dataset_id"], row["dataset_revision"]), reverse=True)
        return rows[(page - 1) * page_size : page * page_size], len(rows)

    def list_revisions(self, dataset_id, *, page=1, page_size=50):
        rows = [row for row in self.rows.values() if row["dataset_id"] == dataset_id]
        rows.sort(key=lambda row: (row["created_at"], row["dataset_revision"]), reverse=True)
        return rows[(page - 1) * page_size : page * page_size], len(rows)


def valid_bundle(**overrides):
    bundle = {
        "schema_version": "rag_eval_v1",
        "dataset_id": "candidate.v1",
        "dataset_revision": "revision-1",
        "dataset_kind": "generated_candidate",
        "source_snapshot": {"index_version": "index-v1"},
        "samples": [{
            "sample_id": "q-1",
            "question": "什么是 Pearl 因果模型？",
            "reference_answer": "因果推断框架。",
            "expected_claims": ["Pearl 提出了结构因果模型。"],
        }],
    }
    bundle.update(overrides)
    return bundle


class DatasetRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = MemoryRepository()
        self.registry = DatasetRegistry(Path(self.temporary.name), self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_register_writes_canonical_json_and_metadata(self) -> None:
        result = self.registry.register(valid_bundle())

        snapshot = Path(self.temporary.name) / result["storage_uri"]
        self.assertTrue(snapshot.is_file())
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        self.assertEqual(payload["dataset_id"], "candidate.v1")
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["binding_mode"], "index_bound")
        self.assertEqual(
            result["content_sha256"],
            hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        )

    def test_register_is_idempotent_for_same_identity_and_content(self) -> None:
        first = self.registry.register(valid_bundle())
        second = self.registry.register(valid_bundle())

        self.assertEqual(first, second)
        self.assertEqual(len(self.repository.rows), 1)

    def test_register_rejects_changed_content_for_existing_identity(self) -> None:
        self.registry.register(valid_bundle())
        changed = valid_bundle()
        changed["samples"] = [dict(changed["samples"][0], question="不同问题")]

        with self.assertRaises(DatasetRevisionConflict):
            self.registry.register(changed)

    def test_register_rejects_invalid_identity_path_escape_empty_and_unknown_kind(self) -> None:
        invalid_bundles = (
            valid_bundle(dataset_id="../escape"),
            valid_bundle(dataset_revision="../escape"),
            valid_bundle(samples=[]),
            valid_bundle(dataset_kind="invented_kind"),
        )

        for bundle in invalid_bundles:
            with self.subTest(bundle=bundle):
                with self.assertRaises(ValueError):
                    self.registry.register(bundle)

    def test_resolve_fails_closed_when_snapshot_is_missing(self) -> None:
        result = self.registry.register(valid_bundle())
        (Path(self.temporary.name) / result["storage_uri"]).unlink()

        with self.assertRaises(ValueError):
            self.registry.resolve(DatasetRef("candidate.v1", "revision-1"))

    def test_resolve_rejects_snapshot_with_tampered_content_hash(self) -> None:
        result = self.registry.register(valid_bundle())
        snapshot = Path(self.temporary.name) / result["storage_uri"]
        snapshot.write_bytes(snapshot.read_bytes() + b" ")

        with self.assertRaises(ValueError):
            self.registry.resolve(DatasetRef("candidate.v1", "revision-1"))

    def test_resolve_rejects_identity_mismatch_even_when_hash_metadata_matches(self) -> None:
        result = self.registry.register(valid_bundle())
        snapshot = Path(self.temporary.name) / result["storage_uri"]
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        payload["dataset_id"] = "different.v1"
        tampered = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        snapshot.write_bytes(tampered)
        self.repository.rows[("candidate.v1", "revision-1")]["content_sha256"] = hashlib.sha256(tampered).hexdigest()

        with self.assertRaises(ValueError):
            self.registry.resolve(DatasetRef("candidate.v1", "revision-1"))

    def test_list_operations_return_metadata_without_samples(self) -> None:
        self.registry.register(valid_bundle())
        self.registry.register(valid_bundle(dataset_revision="revision-2"))

        datasets = self.registry.list_datasets()
        revisions = self.registry.list_revisions("candidate.v1")

        self.assertEqual(datasets["total"], 2)
        self.assertEqual(revisions["total"], 2)
        for row in datasets["items"] + revisions["items"]:
            self.assertNotIn("samples", row)
            self.assertEqual(row["dataset_id"], "candidate.v1")


class DatasetMigrationAndReadinessContracts(unittest.TestCase):
    def test_dataset_migration_and_readiness_contract(self) -> None:
        migration_path = Path("Database/migrations/versions/h8d9e0f1a2b3_add_rag_eval_datasets.py")
        migration_source = migration_path.read_text(encoding="utf-8")
        db_source = Path("app/db.py").read_text(encoding="utf-8")

        self.assertIn('revision: str = "h8d9e0f1a2b3"', migration_source)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "g7c8d9e0f1a2"', migration_source)
        self.assertIn("CREATE TABLE rag_eval_datasets", migration_source)
        self.assertIn("uq_rag_eval_datasets_identity", migration_source)
        self.assertIn("idx_rag_eval_datasets_list", migration_source)
        self.assertIn('"rag_eval_datasets"', db_source)
        self.assertIn('("rag_eval_datasets", "uq_rag_eval_datasets_identity")', db_source)
        self.assertIn('("rag_eval_datasets", "idx_rag_eval_datasets_list")', db_source)
        self.assertRegex(
            db_source,
            re.compile(
                r"table_name\s*=\s*'rag_eval_datasets'\s*"
                r"AND\s+index_name\s*=\s*'uq_rag_eval_datasets_identity'\s*"
                r"AND\s+non_unique\s*=\s*0",
                re.DOTALL,
            ),
        )


if __name__ == "__main__":
    unittest.main()
