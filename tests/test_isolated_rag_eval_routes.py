import json
import io
import hashlib
import queue
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import app.rag_eval.routes as routes
import app.rag_eval.isolated_runs as isolated_runs
from app.rag_eval.dataset_registry import DatasetRevisionConflict
from Agent.knowledge_base.rag.operation_datasets import benchmark_v2
from tests.support.authorization import rag_eval_route_authorization


class _FakeIsolatedRunManager:
    """为 HTTP 契约测试提供不启动后台线程的隔离任务边界。"""

    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root
        self.calls = []
        self.states = {
            "ingest-test": {"run_id": "ingest-test", "kind": "ingestion", "status": "staged"},
            "rag-test": {"run_id": "rag-test", "kind": "rag_query", "status": "completed"},
            "eval-test": {"run_id": "eval-test", "kind": "evaluation", "status": "completed"},
        }

    def start_ingestion(self, **kwargs):
        self.calls.append(("start_ingestion", kwargs))
        return {"run_id": "ingest-test", "kind": "ingestion", "status": "created"}

    def start_rag_query(self, *args, **kwargs):
        self.calls.append(("start_rag_query", args, kwargs))
        return {"run_id": "rag-test", "kind": "rag_query", "status": "created"}

    def start_evaluation(self, *args, **kwargs):
        self.calls.append(("start_evaluation", args, kwargs))
        return {"run_id": "eval-test", "kind": "evaluation", "status": "created"}

    def get(self, run_id):
        if run_id not in self.states:
            raise KeyError(run_id)
        return self.states[run_id]

    def list_ingestion_history(self, **kwargs):
        self.calls.append(("list_ingestion_history", kwargs))
        return {
            "items": [self.states["ingest-test"]],
            "page": 1,
            "page_size": 50,
            "total": 1,
            "total_pages": 1,
        }

    def get_result(self, run_id):
        if run_id not in self.states:
            raise KeyError(run_id)
        return {"run_id": run_id, "status": "pass", "kind": self.states[run_id]["kind"]}

    def release_status(self, ingestion_run_id="", index_version="", evaluation_run_id=None):
        self.calls.append(("release_status", ingestion_run_id, index_version, evaluation_run_id))
        return {"release": {"ingestion_run_id": ingestion_run_id, "index_version": index_version}, "active": None, "previous": None}

    def check_release(self, ingestion_run_id, index_version, evaluation_run_id=None, expected_active_index_version=None, expected_generation=None):
        self.calls.append(("check_release", ingestion_run_id, index_version, evaluation_run_id, expected_active_index_version, expected_generation))
        return {"state": "ready_to_publish", "publishable": True, "release": {"index_version": index_version}, "checks": []}

    def publish_release(self, ingestion_run_id, index_version, evaluation_run_id, expected_active_index_version=None, expected_generation=None):
        self.calls.append(("publish_release", ingestion_run_id, index_version, evaluation_run_id, expected_active_index_version, expected_generation))
        return {"status": "published", "active": {"index_version": index_version}, "previous": None}

    def rollback_release(self, index_version, expected_active_index_version=None, expected_generation=None):
        self.calls.append(("rollback_release", index_version, expected_active_index_version, expected_generation))
        return {"status": "rolled_back", "active": {"index_version": index_version}, "previous": None}

    def subscribe(self, run_id):
        if run_id not in self.states:
            raise KeyError(run_id)
        events = queue.Queue()
        events.put({"type": "run_done", "run_id": run_id, "status": "pass"})
        return events

    def unsubscribe(self, run_id):
        """模拟 SSE 完成后的订阅清理。"""
        return None

    def cancel(self, run_id):
        if run_id not in self.states:
            raise KeyError(run_id)
        return {**self.states[run_id], "status": "cancelling", "cancel_requested": True}

    def delete_evaluation_run(self, run_id, force=False):
        if run_id not in self.states:
            raise KeyError(run_id)
        self.calls.append(("delete_evaluation_run", run_id, force))
        self.states.pop(run_id)
        return {"run_id": run_id, "status": "deleted", "deleted": True}

    def delete_ingestion_run(self, run_id, cascade=False):
        if run_id not in self.states:
            raise KeyError(run_id)
        self.calls.append(("delete_ingestion_run", run_id, cascade))
        return {"run_id": run_id, "status": "deleted", "deleted": True}

    def delete_derived_run(self, run_id):
        self.calls.append(("delete_derived_run", run_id))
        return {"run_id": run_id, "status": "deleted", "deleted": True}

    def get_artifact_path(self, run_id, artifact_name):
        if run_id != "eval-test" or artifact_name not in {"summary.json", "summary.md"}:
            raise ValueError("artifact is unavailable")
        return self.artifact_root / artifact_name


class _FakeDatasetRegistry:
    """路由合同测试使用的内存题集注册中心，不连接 MySQL。"""

    def __init__(self) -> None:
        self.bundle = {
            "schema_version": "rag_eval_v1",
            "dataset_id": "pearl_gold_v2",
            "dataset_revision": "revision-1",
            "dataset_kind": "gold_regression",
            "samples": [{"sample_id": "q-1", "question": "注册题目", "reference_answer": "答案"}],
        }
        self.metadata = {
            "dataset_id": "pearl_gold_v2",
            "dataset_revision": "revision-1",
            "dataset_kind": "gold_regression",
            "schema_version": "rag_eval_v1",
            "content_sha256": "a" * 64,
            "sample_count": 1,
            "storage_uri": "pearl_gold_v2/revision-1/fixture.json",
            "lifecycle_status": "registered",
        }
        self.calls = []

    def register(self, bundle):
        self.calls.append(("register", bundle))
        if bundle.get("dataset_id") == "conflict":
            raise DatasetRevisionConflict("dataset revision already registered")
        if not isinstance(bundle, dict) or bundle.get("schema_version") != "rag_eval_v1":
            raise ValueError("invalid bundle")
        return dict(self.metadata)

    def list_datasets(self, **kwargs):
        self.calls.append(("list_datasets", kwargs))
        return {"items": [dict(self.metadata)], "page": kwargs["page"], "page_size": kwargs["page_size"], "total": 1}

    def list_revisions(self, dataset_id, **kwargs):
        self.calls.append(("list_revisions", dataset_id, kwargs))
        items = [dict(self.metadata)] if dataset_id == "pearl_gold_v2" else []
        return {"items": items, "page": kwargs["page"], "page_size": kwargs["page_size"], "total": len(items)}

    def resolve(self, reference):
        self.calls.append(("resolve", reference))
        if reference != {"dataset_id": "pearl_gold_v2", "dataset_revision": "revision-1"}:
            raise ValueError("dataset revision is not registered")
        return {**self.metadata, "bundle": dict(self.bundle)}


class IsolatedRagEvalRouteTests(unittest.TestCase):
    """验证前端实际使用的隔离 HTTP 契约。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        artifact_root = Path(self.temporary.name)
        (artifact_root / "summary.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
        (artifact_root / "summary.md").write_text("# summary\n", encoding="utf-8")
        self.manager = _FakeIsolatedRunManager(artifact_root)
        self.dataset_registry = _FakeDatasetRegistry()
        self.app = Flask(__name__)
        self.app.register_blueprint(routes.rag_eval_bp)
        self.client = self.app.test_client()
        self.manager_patch = patch.object(routes, "isolated_run_manager", self.manager)
        self.catalog_patch = patch.object(
            routes,
            "list_source_catalog",
            return_value=[
                {
                    "source_id": "source-test",
                    "name": "fixture.pdf",
                    "size_bytes": 12,
                    "content_sha256": "a" * 64,
                }
            ],
        )
        self.dataset_registry_patch = patch.object(
            routes, "dataset_registry", self.dataset_registry, create=True
        )
        self.manager_patch.start()
        self.catalog_patch.start()
        self.dataset_registry_patch.start()
        self.authorization_stack = ExitStack()
        for authorization_context in rag_eval_route_authorization():
            self.authorization_stack.enter_context(authorization_context)

    def tearDown(self):
        self.authorization_stack.close()
        self.catalog_patch.stop()
        self.manager_patch.stop()
        self.dataset_registry_patch.stop()
        self.temporary.cleanup()

    def test_dataset_registry_http_contracts(self):
        created = self.client.post("/api/rag_eval/datasets", json=self.dataset_registry.bundle)
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.get_json()["success"])

        listing = self.client.get("/api/rag_eval/datasets?dataset_kind=gold_regression&lifecycle_status=registered&page=1&page_size=10")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["data"]["items"][0]["storage_uri"], "pearl_gold_v2/revision-1/fixture.json")

        revisions = self.client.get("/api/rag_eval/datasets/pearl_gold_v2/revisions?page=1&page_size=10")
        self.assertEqual(revisions.status_code, 200)
        self.assertEqual(revisions.get_json()["data"]["total"], 1)

        detail = self.client.get("/api/rag_eval/datasets/pearl_gold_v2/revisions/revision-1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["data"]["bundle"], self.dataset_registry.bundle)

        conflict = self.client.post("/api/rag_eval/datasets", json={**self.dataset_registry.bundle, "dataset_id": "conflict"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["error_code"], "dataset_revision_conflict")

        invalid = self.client.post("/api/rag_eval/datasets", json={})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["error_code"], "invalid_request")

        missing = self.client.get("/api/rag_eval/datasets/missing/revisions/revision-1")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["error_code"], "dataset_not_found")

    def test_dataset_ref_is_resolved_for_evaluation_batch_and_rag_query(self):
        reference = {"dataset_id": "pearl_gold_v2", "dataset_revision": "revision-1"}
        evaluation = self.client.post("/api/rag_eval/isolated/evaluation-runs", json={
            "ingestion_run_id": "ingest-test", "index_version": "mm-test", "dataset_ref": reference,
        })
        self.assertEqual(evaluation.status_code, 202)
        self.assertEqual(self.manager.calls[-1][1][2], self.dataset_registry.bundle)

        batch = self.client.post("/api/rag_eval/isolated/evaluation-batches", json={
            "ingestion_run_id": "ingest-test", "index_version": "mm-test", "dataset_ref": reference,
            "experiments": [{"strategy_profile": {"profile_id": "one"}}, {"strategy_profile": {"profile_id": "two"}}],
        })
        self.assertEqual(batch.status_code, 202)
        batch_calls = [call for call in self.manager.calls if call[0] == "start_evaluation"][-2:]
        self.assertEqual([call[1][2] for call in batch_calls], [self.dataset_registry.bundle, self.dataset_registry.bundle])

        query = self.client.post("/api/rag_eval/isolated/rag-runs", json={
            "ingestion_run_id": "ingest-test", "index_version": "mm-test", "dataset_ref": reference,
        })
        self.assertEqual(query.status_code, 202)
        _, args, kwargs = self.manager.calls[-1]
        self.assertEqual(args[2], self.dataset_registry.bundle["samples"])
        self.assertEqual(kwargs["input_identity"], {
            "schema_version": "rag_eval_v1", "dataset_id": "pearl_gold_v2",
            "dataset_revision": "revision-1", "sample_count": 1, "content_sha256": "a" * 64,
        })

    def test_generated_candidate_can_start_evaluation_without_gold_reference(self):
        generated = {
            "schema_version": "rag_eval_v1",
            "dataset_id": "candidate-mm-test",
            "dataset_kind": "generated_candidate",
            "dataset_revision": "candidate-revision-1",
            "source_snapshot": {"index_version": "mm-test"},
            "samples": [{
                "sample_id": "candidate-1",
                "question": "自动生成题目",
                "reference_answer": "自动生成答案",
                "expected_claims": ["自动生成事实"],
            }],
        }
        started = self.client.post("/api/rag_eval/isolated/evaluation-runs", json={
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "dataset_source": "generated_candidate",
            "eval_dataset": generated,
        })
        self.assertEqual(started.status_code, 202)
        self.assertEqual(self.manager.calls[-1][1][2]["dataset_kind"], "generated_candidate")
        self.assertEqual(self.manager.calls[-1][1][2]["samples"][0]["sample_id"], "candidate-1")

    def test_dataset_ref_rejects_mutually_exclusive_and_gold_source_inputs(self):
        reference = {"dataset_id": "pearl_gold_v2", "dataset_revision": "revision-1"}
        inline = {"schema_version": "rag_eval_v1", "samples": [{"question": "内联题目"}]}
        payloads = [
            ("/api/rag_eval/isolated/evaluation-runs", {"dataset_ref": reference, "eval_dataset": inline}),
            ("/api/rag_eval/isolated/evaluation-batches", {"dataset_ref": reference, "dataset": inline, "experiments": [{}, {}]}),
            ("/api/rag_eval/isolated/rag-runs", {"dataset_ref": reference, "eval_dataset": inline}),
            ("/api/rag_eval/isolated/evaluation-runs", {"dataset_ref": reference, "dataset_source": "gold_v2"}),
        ]
        for path, payload in payloads:
            with self.subTest(path=path, payload=payload):
                response = self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 400)

    def test_source_catalog_and_ingestion_contract(self):
        catalog = self.client.get("/api/rag_eval/isolated/source-catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.get_json()["data"]["sources"][0]["source_id"], "source-test")
        self.assertNotIn("uri", catalog.get_json()["data"]["sources"][0])

        started = self.client.post(
            "/api/rag_eval/isolated/ingestion-runs",
            json={"source_ids": ["source-test"], "max_pages": 4},
        )
        self.assertEqual(started.status_code, 202)
        self.assertEqual(self.manager.calls[0][1], {
            "source_ids": ["source-test"],
            "sources": None,
            "max_pages": 4,
            "page_ranges": None,
            "allow_remote_data": False,
            "authorized_source_ids": [],
        })

        authorized = self.client.post(
            "/api/rag_eval/isolated/ingestion-runs",
            json={
                "source_ids": ["source-test"],
                "allow_remote_data": True,
                "authorized_source_ids": ["source-test"],
            },
        )
        self.assertEqual(authorized.status_code, 202)
        self.assertEqual(self.manager.calls[1][1]["allow_remote_data"], True)
        self.assertEqual(self.manager.calls[1][1]["authorized_source_ids"], ["source-test"])

        custom = self.client.post(
            "/api/rag_eval/isolated/ingestion-runs",
            json={
                "source_ids": ["source-test"],
                "page_ranges": [{"source_id": "source-test", "start_page": 40, "end_page": 55}],
            },
        )
        self.assertEqual(custom.status_code, 202)
        self.assertEqual(self.manager.calls[2][1]["page_ranges"][0]["start_page"], 40)

        invalid = self.client.post(
            "/api/rag_eval/isolated/ingestion-runs",
            json={"source_ids": "source-test"},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_legacy_frozen_source_id_resolves_to_current_content_hash_id(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Pearl-mono(1).pdf"
            content = b"frozen source"
            source.write_bytes(content)
            content_hash = hashlib.sha256(content).hexdigest()
            current_id = "source_" + content_hash
            legacy_id = "source_" + hashlib.sha256(f"{source.name}:{content_hash}".encode("utf-8")).hexdigest()[:20]
            record = {
                "source_id": current_id,
                "name": source.name,
                "display_name": source.name,
                "content_sha256": content_hash,
                "source_kind": "frozen",
                "page_count": 1,
            }
            with patch.object(isolated_runs, "_source_catalog_records", return_value=[(source, record)]):
                paths, source_ids, _ = isolated_runs._resolve_source_inputs([legacy_id], None)
            self.assertEqual(paths, [source.resolve()])
            self.assertEqual(source_ids, [current_id])

    def test_multimodal_release_api_contract_requires_explicit_publish_confirmation(self):
        status = self.client.get(
            "/api/rag_eval/multimodal/releases/status?ingestion_run_id=ingest-test&index_version=mm-test"
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["data"]["release"]["index_version"], "mm-test")

        gate = self.client.post("/api/rag_eval/multimodal/releases/gate-check", json={
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "evaluation_run_id": "eval-test",
            "expected_active_index_version": "mm-old",
        })
        self.assertEqual(gate.status_code, 200)
        self.assertTrue(gate.get_json()["data"]["publishable"])

        gate_with_generation = self.client.post("/api/rag_eval/multimodal/releases/gate-check", json={
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "evaluation_run_id": "eval-test",
            "expected_generation": 4,
        })
        self.assertEqual(gate_with_generation.status_code, 200)
        self.assertEqual(self.manager.calls[-1][-1], 4)

        not_confirmed = self.client.post("/api/rag_eval/multimodal/releases/publish", json={
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "evaluation_run_id": "eval-test",
        })
        self.assertEqual(not_confirmed.status_code, 400)

        published = self.client.post("/api/rag_eval/multimodal/releases/publish", json={
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "evaluation_run_id": "eval-test",
            "expected_active_index_version": "mm-old",
            "confirm": True,
        })
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.get_json()["data"]["status"], "published")

    def test_local_gold_dataset_status_and_evaluation_source(self):
        gold_path = Path(self.temporary.name) / "gold.json"
        gold_path.write_text(json.dumps({
            "schema_version": "rag_eval_v1",
            "dataset_id": "pearl_gold_v2",
            "dataset_revision": "fixture-revision",
            "samples": [{"sample_id": "gold-1", "question": "q", "reference_answer": "a"}],
        }), encoding="utf-8")

        with patch.object(benchmark_v2, "DEFAULT_GOLD_V2_OUTPUT", gold_path), \
             patch.object(benchmark_v2, "validate_frozen_gold_binding", return_value={"index_version": "mm-test"}):
            status = self.client.get("/api/rag_eval/gold-v2/status")
            self.assertEqual(status.status_code, 200)
            status_data = status.get_json()["data"]
            self.assertTrue(status_data["exists"])
            self.assertEqual(status_data["dataset_id"], "pearl_gold_v2")
            self.assertEqual(status_data["dataset_revision"], "fixture-revision")
            self.assertEqual(status_data["sample_count"], 1)

            started = self.client.post("/api/rag_eval/isolated/evaluation-runs", json={
                "ingestion_run_id": "ingest-test",
                "index_version": "mm-test",
                "dataset_source": "gold_v2",
                "retrieval": {},
                "ragas": {},
                "steps": ["validate_datasets"],
            })

        self.assertEqual(started.status_code, 202)
        self.assertEqual(self.manager.calls[-1][0], "start_evaluation")
        self.assertEqual(self.manager.calls[-1][1][2]["dataset_id"], "pearl_gold_v2")

    def test_local_gold_evaluation_rejects_unbound_candidate_locators(self):
        gold_path = Path(self.temporary.name) / "gold.json"
        gold_path.write_text(json.dumps({
            "schema_version": "rag_eval_v1",
            "dataset_id": "pearl_gold_v2",
            "dataset_kind": "gold_regression",
            "samples": [{"sample_id": "gold-1", "question": "q", "reference_answer": "a"}],
        }), encoding="utf-8")
        with patch.object(benchmark_v2, "DEFAULT_GOLD_V2_OUTPUT", gold_path), \
             patch.object(benchmark_v2, "validate_frozen_gold_binding", side_effect=ValueError("frozen candidate Gold is not bound")):
            response = self.client.post("/api/rag_eval/isolated/evaluation-runs", json={
                "ingestion_run_id": "ingest-test",
                "index_version": "mm-test",
                "dataset_source": "gold_v2",
                "retrieval": {},
                "ragas": {},
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("not bound", response.get_json()["error"])

    def test_gold_status_requires_complete_index_identity_query(self):
        gold_path = Path(self.temporary.name) / "gold.json"
        gold_path.write_text(json.dumps({
            "schema_version": "rag_eval_v1",
            "dataset_id": "pearl_gold_v2",
            "samples": [{"sample_id": "gold-1", "question": "q"}],
        }), encoding="utf-8")
        with patch.object(benchmark_v2, "DEFAULT_GOLD_V2_OUTPUT", gold_path):
            response = self.client.get("/api/rag_eval/gold-v2/status?ingestion_run_id=ingest-test")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])
        self.assertIn("provided together", response.get_json()["error"])

    def test_pdf_upload_contract_only_registers_source(self):
        registered = {
            "source_id": "upload-test",
            "name": "fixture.pdf",
            "size_bytes": 9,
            "content_sha256": "b" * 64,
            "source_kind": "uploaded",
            "page_count": 1,
        }
        with patch.object(routes, "register_uploaded_source", return_value=registered) as register:
            response = self.client.post(
                "/api/rag_eval/isolated/sources",
                data={"file": (io.BytesIO(b"%PDF-test"), "fixture.pdf")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["source"]["source_id"], "upload-test")
        register.assert_called_once_with("fixture.pdf", b"%PDF-test")
        self.assertEqual(self.manager.calls, [])

    def test_uploaded_source_delete_contract(self):
        with patch.object(
            routes,
            "delete_uploaded_source",
            return_value={"source_id": "upload-test", "status": "deleted", "deleted": True},
        ) as delete_source:
            response = self.client.delete("/api/rag_eval/isolated/sources/upload-test")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["data"]["deleted"])
        delete_source.assert_called_once_with("upload-test")

    def test_source_display_name_update_contract(self):
        updated = {
            "source_id": "source-test",
            "name": "fixture.pdf",
            "display_name": "Pearl 因果资料",
        }
        with patch.object(routes, "update_source_display_name", return_value=updated) as update_name:
            response = self.client.patch(
                "/api/rag_eval/isolated/sources/source-test",
                json={"display_name": "Pearl 因果资料"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["display_name"], "Pearl 因果资料")
        update_name.assert_called_once_with("source-test", "Pearl 因果资料")

    def test_source_display_name_persists_in_local_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"source metadata test"
            source_id = "upload_" + hashlib.sha256(content).hexdigest()[:24]
            (root / f"{source_id}__fixture.txt").write_bytes(content)
            with patch.object(isolated_runs, "RAG_EVAL_SOURCE_ROOT", root), patch.object(
                isolated_runs, "production_source_paths", return_value=[]
            ):
                before = isolated_runs.list_source_catalog()[0]
                self.assertEqual(before["display_name"], "fixture.txt")
                isolated_runs.update_source_display_name(source_id, "Pearl 因果资料")
                after = isolated_runs.list_source_catalog()[0]

            self.assertEqual(after["display_name"], "Pearl 因果资料")
            metadata = json.loads((root / "source_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], "rag_eval_source_metadata_v1")

    def test_ingestion_state_stream_and_cancel_contract(self):
        state = self.client.get("/api/rag_eval/isolated/ingestion-runs/ingest-test")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.get_json()["data"]["status"], "staged")

        history = self.client.get("/api/rag_eval/isolated/ingestion-runs?page=1&page_size=50")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.get_json()["data"]["items"][0]["run_id"], "ingest-test")

        stream = self.client.get("/api/rag_eval/isolated/ingestion-runs/ingest-test/stream")
        self.assertEqual(stream.status_code, 200)
        self.assertIn(b'"type": "connected"', stream.data)
        self.assertIn(b'"type": "run_done"', stream.data)

        cancelled = self.client.post("/api/rag_eval/isolated/ingestion-runs/ingest-test/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertTrue(cancelled.get_json()["data"]["cancel_requested"])

        deleted = self.client.delete("/api/rag_eval/isolated/ingestion-runs/ingest-test", json={"cascade": True})
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.get_json()["data"]["deleted"])
        self.assertEqual(self.manager.calls[-1], ("delete_ingestion_run", "ingest-test", True))

    def test_derived_run_delete_contracts(self):
        for path in [
            "/api/rag_eval/isolated/candidate-runs/candidate-test",
            "/api/rag_eval/isolated/tuning-dataset-runs/tuning-test",
            "/api/rag_eval/gold-v2/governance-runs/governance-test",
        ]:
            with self.subTest(path=path):
                response = self.client.delete(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["data"]["deleted"])
        self.assertEqual(
            self.manager.calls[-3:],
            [
                ("delete_derived_run", "candidate-test"),
                ("delete_derived_run", "tuning-test"),
                ("delete_derived_run", "governance-test"),
            ],
        )

    def test_rag_query_contract_carries_staged_identity(self):
        started = self.client.post(
            "/api/rag_eval/isolated/rag-runs",
            json={
                "ingestion_run_id": "ingest-test",
                "index_version": "mm-test",
                "question": "问题",
            },
        )
        self.assertEqual(started.status_code, 202)
        method, args, kwargs = self.manager.calls[0]
        self.assertEqual(method, "start_rag_query")
        self.assertEqual(args[:2], ("ingest-test", "mm-test"))
        self.assertEqual(args[2][0]["question"], "问题")
        self.assertEqual(kwargs["input_identity"]["schema_version"], "isolated_question_v1")

        for path, expected in [
            ("/api/rag_eval/isolated/rag-runs/rag-test", "completed"),
            ("/api/rag_eval/isolated/rag-runs/rag-test/result", "pass"),
        ]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["data"]["status"], expected)

        self.assertEqual(self.client.get("/api/rag_eval/isolated/rag-runs/rag-test/stream").status_code, 200)
        self.assertEqual(self.client.post("/api/rag_eval/isolated/rag-runs/rag-test/cancel").status_code, 200)

    def test_evaluation_contract_covers_result_artifacts_stream_and_cancel(self):
        payload = {
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "eval_dataset": {
                "schema_version": "rag_eval_v1",
                "dataset_id": "fixture-v1",
                "samples": [{"sample_id": "q-1", "question": "问题"}],
            },
            "retrieval": {},
            "ragas": {"prepare_only": True},
            "steps": ["validate_datasets"],
        }
        started = self.client.post("/api/rag_eval/isolated/evaluation-runs", json=payload)
        self.assertEqual(started.status_code, 202)
        method, args, kwargs = self.manager.calls[0]
        self.assertEqual(method, "start_evaluation")
        self.assertEqual(args[:2], ("ingest-test", "mm-test"))
        self.assertEqual(args[2]["schema_version"], "rag_eval_v1")
        self.assertEqual(kwargs["ragas_options"], {"prepare_only": True})

        missing_dataset = self.client.post(
            "/api/rag_eval/isolated/evaluation-runs",
            json={"ingestion_run_id": "ingest-test", "index_version": "mm-test"},
        )
        self.assertEqual(missing_dataset.status_code, 400)

        self.assertEqual(self.client.get("/api/rag_eval/isolated/evaluation-runs/eval-test").status_code, 200)
        result = self.client.get("/api/rag_eval/isolated/evaluation-runs/eval-test/result")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["data"]["status"], "pass")
        self.assertEqual(
            self.client.get("/api/rag_eval/isolated/evaluation-runs/eval-test/artifacts/summary.json").get_json()["data"],
            {"status": "pass"},
        )
        self.assertIn("# summary", self.client.get("/api/rag_eval/isolated/evaluation-runs/eval-test/artifacts/summary.md").data.decode())
        self.assertEqual(self.client.get("/api/rag_eval/isolated/evaluation-runs/eval-test/stream").status_code, 200)
        self.assertEqual(self.client.post("/api/rag_eval/isolated/evaluation-runs/eval-test/cancel").status_code, 200)
        deleted = self.client.delete("/api/rag_eval/isolated/evaluation-runs/eval-test", json={"force": True})
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.get_json()["data"]["deleted"])
        self.assertEqual(self.manager.calls[-1], ("delete_evaluation_run", "eval-test", True))

    def test_parallel_evaluation_batch_validates_then_creates_isolated_runs(self):
        response = self.client.post("/api/rag_eval/isolated/evaluation-batches", json={
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "eval_dataset": {
                "schema_version": "rag_eval_v1",
                "dataset_id": "fixture-v1",
                "samples": [{"sample_id": "q-1", "question": "问题"}],
            },
            "experiments": [
                {
                    "strategy_profile": {"profile_id": "baseline", "name": "基线"},
                    "retrieval": {"overrides": {"final_top_k": 4}},
                    "ragas": {"run": False},
                    "steps": ["validate_datasets", "retrieval_eval", "summary"],
                },
                {
                    "strategy_profile": {"profile_id": "experiment-2", "name": "实验二"},
                    "retrieval": {"overrides": {"final_top_k": 6}},
                    "ragas": {"run": False},
                    "steps": ["validate_datasets", "retrieval_eval", "summary"],
                },
            ],
        })

        self.assertEqual(response.status_code, 202)
        data = response.get_json()["data"]
        self.assertEqual(data["run_count"], 2)
        self.assertTrue(data["batch_id"].startswith("eval_batch_"))
        calls = [call for call in self.manager.calls if call[0] == "start_evaluation"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2]["batch_position"], 1)
        self.assertEqual(calls[1][2]["batch_position"], 2)
        self.assertEqual(calls[0][2]["batch_size"], 2)
        self.assertEqual(calls[0][2]["batch_id"], calls[1][2]["batch_id"])

    def test_parallel_evaluation_batch_rejects_duplicate_profiles_before_creation(self):
        response = self.client.post("/api/rag_eval/isolated/evaluation-batches", json={
            "ingestion_run_id": "ingest-test",
            "index_version": "mm-test",
            "eval_dataset": {
                "schema_version": "rag_eval_v1",
                "dataset_id": "fixture-v1",
                "samples": [{"sample_id": "q-1", "question": "问题"}],
            },
            "experiments": [
                {"strategy_profile": {"profile_id": "same"}},
                {"strategy_profile": {"profile_id": "same"}},
            ],
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("unique", response.get_json()["error"])
        self.assertFalse(any(call[0] == "start_evaluation" for call in self.manager.calls))


if __name__ == "__main__":
    unittest.main()
