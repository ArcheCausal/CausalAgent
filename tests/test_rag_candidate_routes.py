import json
import hashlib
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.rag_eval import isolated_runs
from app.rag_eval import routes

from tests.support.authorization import rag_eval_route_authorization


class _FakeCandidateManager:
    def __init__(self, artifact_path: Path) -> None:
        self.artifact_path = artifact_path

    def start_candidate_generation(self, *args, **kwargs):
        return {
            "run_id": "candidate_20260807_000000_abcdef12",
            "kind": "candidate_generation",
            "status": "created",
            "args": list(args),
            "kwargs": kwargs,
        }

    def import_rebound_candidate(self):
        return {
            "run_id": "candidate_rebind_20260815_000000_abcdef12",
            "kind": "candidate_generation",
            "status": "succeeded",
            "review_status": "requires_reapproval",
            "candidate_artifact_name": "candidate_rebound.json",
            "review_manifest_artifact_name": "candidate_rebind_review.json",
        }

    def get(self, run_id):
        return {"run_id": run_id, "kind": "candidate_generation", "status": "succeeded"}

    def get_result(self, run_id):
        return {
            "run_id": run_id,
            "candidate_artifact_name": "candidate.json",
            "audit_artifact_name": "candidate.json.audit.json",
        }

    def get_artifact_path(self, run_id, artifact_name):
        if artifact_name != "candidate.json":
            raise ValueError("unknown artifact")
        return self.artifact_path

    def cancel(self, run_id):
        return {"run_id": run_id, "cancel_requested": True}

    def save_candidate_review(self, run_id, *, reviewer, decisions, updates):
        return {
            "run_id": run_id,
            "reviewer": reviewer,
            "decision_count": len(decisions),
            "updated_sample_count": len(updates),
        }

    def rebind_candidate_to_current_index(self, run_id, *, ingestion_run_id, index_version):
        return {
            "run_id": run_id,
            "ingestion_run_id": ingestion_run_id,
            "index_version": index_version,
            "candidate_artifact_name": "candidate_rebound.json",
            "review_status": "requires_reapproval",
        }

    def freeze_candidate_gold_v2(self, run_id, *, expected_ingestion_run_id, expected_index_version, replace_existing=False):
        return {
            "run_id": run_id,
            "ingestion_run_id": expected_ingestion_run_id,
            "index_version": expected_index_version,
            "sample_count": 72,
            "artifact_name": "gold_v2_freeze.json",
            "replace_existing": replace_existing,
        }

    def bind_baseline_v2(self):
        raise ValueError("Gold v2 is not frozen")

    def start_dataset_governance(self, evaluation_run_id, *, confirm):
        return {
            "run_id": "governance_20260818_000000_abcdef12",
            "kind": "dataset_governance",
            "status": "queued",
            "evaluation_run_id": evaluation_run_id,
            "confirm": confirm,
        }


class RagCandidateRouteTests(unittest.TestCase):
    def setUp(self):
        self.authorization_stack = ExitStack()
        for authorization_context in rag_eval_route_authorization():
            self.authorization_stack.enter_context(authorization_context)

    def tearDown(self):
        self.authorization_stack.close()

    def test_dataset_review_ui_owns_release_and_does_not_trigger_post_review_governance(self):
        """题集发布入口应位于候选审核页，对比页不得再发起治理任务。"""
        app_source = (Path(__file__).resolve().parents[1] / "app" / "rag_eval" / "frontend" / "src" / "App.vue").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("/gold-v2/governance", app_source)
        self.assertNotIn("startDatasetGovernance", app_source)
        self.assertNotIn("开始治理", app_source)
        self.assertIn("题集审核与发布", app_source)
        self.assertIn("查看评测诊断", app_source)
        for gate in ("证据蕴含", "检索合理性", "分布平衡"):
            self.assertIn(gate, app_source)

    def test_gold_status_read_does_not_modify_current_gold(self):
        """候选审核页读取 Gold 状态只能诊断，不能隐式改写正式文件。"""
        from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import DEFAULT_GOLD_V2_OUTPUT

        if not DEFAULT_GOLD_V2_OUTPUT.is_file():
            self.skipTest("当前工作区没有冻结 Gold 文件")
        before = hashlib.sha256(DEFAULT_GOLD_V2_OUTPUT.read_bytes()).hexdigest()
        app = Flask(__name__)
        app.register_blueprint(routes.rag_eval_bp)
        response = app.test_client().get("/api/rag_eval/gold-v2/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(hashlib.sha256(DEFAULT_GOLD_V2_OUTPUT.read_bytes()).hexdigest(), before)

    def test_import_rebound_candidate_creates_run_directory_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "Agent" / "knowledge_base" / "rag" / "data" / "eval"
            source_root.mkdir(parents=True)
            candidate = {
                "dataset_id": "candidate-rebound",
                "dataset_revision": "v1",
                "source_snapshot": {"index_version": "mm-current"},
                "samples": [{"sample_id": "candidate-1"}],
            }
            review = {"schema_version": "rag_candidate_review_v1", "decisions": []}
            (source_root / "pearl_candidate_mm_f956e532ed6d49ae1f0e_48_rebound_requires_reapproval_v3.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            (source_root / "pearl_candidate_mm_f956e532ed6d49ae1f0e_48_reapproval_manifest_v3.json").write_text(
                json.dumps(review), encoding="utf-8"
            )
            run_root = root / "runs"
            ingestion_dir = run_root / "ingest_20260815_000000_abcdef1234"
            ingestion_dir.mkdir(parents=True)
            (ingestion_dir / "run.json").write_text(
                json.dumps({
                    "run_id": ingestion_dir.name,
                    "kind": "ingestion",
                    "status": "staged",
                    "index_version": "mm-current",
                    "created_at": "2026-08-15T00:00:00+00:00",
                }),
                encoding="utf-8",
            )
            with patch.object(isolated_runs, "_PROJECT_ROOT", root), patch.object(
                isolated_runs, "ISOLATED_RUN_ROOT", run_root
            ):
                imported = isolated_runs.IsolatedRunManager().import_rebound_candidate()

            run_dir = run_root / imported["run_id"]
            self.assertEqual(imported["question_count"], 1)
            self.assertEqual(imported["review_status"], "requires_reapproval")
            self.assertEqual(imported["ingestion_run_id"], ingestion_dir.name)
            self.assertTrue((run_dir / "candidate_rebound.json").is_file())
            self.assertTrue((run_dir / "candidate_rebind_review.json").is_file())

    def test_candidate_http_flow_and_baseline_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "candidate.json"
            artifact.write_text(json.dumps({"dataset_kind": "generated_candidate"}), encoding="utf-8")
            fake_manager = _FakeCandidateManager(artifact)
            app = Flask(__name__)
            app.register_blueprint(routes.rag_eval_bp)
            client = app.test_client()

            with patch.object(routes, "isolated_run_manager", fake_manager):
                imported = client.post("/api/rag_eval/isolated/candidate-runs/rebound-import", json={})
                self.assertEqual(imported.status_code, 200)
                self.assertEqual(imported.json["data"]["review_status"], "requires_reapproval")

                created = client.post(
                    "/api/rag_eval/isolated/candidate-runs",
                    json={
                        "ingestion_run_id": "ingest-1",
                        "index_version": "mm-test",
                        "dataset_id": "candidate-v1",
                        "question_count": 48,
                        "max_workers": 2,
                    },
                )
                self.assertEqual(created.status_code, 202)
                self.assertTrue(created.json["success"])
                self.assertEqual(created.json["data"]["kwargs"]["question_count"], 48)
                self.assertEqual(created.json["data"]["kwargs"]["max_workers"], 2)

                run_id = created.json["data"]["run_id"]
                state = client.get(f"/api/rag_eval/isolated/candidate-runs/{run_id}")
                self.assertEqual(state.status_code, 200)
                self.assertEqual(state.json["data"]["kind"], "candidate_generation")

                result = client.get(f"/api/rag_eval/isolated/candidate-runs/{run_id}/result")
                self.assertEqual(result.status_code, 200)
                self.assertNotIn("dataset_path", result.json["data"])
                self.assertEqual(result.json["data"]["audit_artifact_name"], "candidate.json.audit.json")

                downloaded = client.get(
                    f"/api/rag_eval/isolated/candidate-runs/{run_id}/artifacts/candidate.json"
                )
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(downloaded.json["data"]["dataset_kind"], "generated_candidate")

                review = client.post(
                    f"/api/rag_eval/isolated/candidate-runs/{run_id}/review",
                    json={
                        "reviewer": "reviewer@example.test",
                        "decisions": [{"sample_id": "candidate-1", "decision": "approved"}],
                        "updates": [{"sample_id": "candidate-1", "question": "edited"}],
                    },
                )
                self.assertEqual(review.status_code, 200)
                self.assertEqual(review.json["data"]["decision_count"], 1)

                rebound = client.post(
                    f"/api/rag_eval/isolated/candidate-runs/{run_id}/rebind",
                    json={"ingestion_run_id": "ingest-2", "index_version": "mm-selected"},
                )
                self.assertEqual(rebound.status_code, 200)
                self.assertEqual(rebound.json["data"]["review_status"], "requires_reapproval")
                self.assertEqual(rebound.json["data"]["index_version"], "mm-selected")

                frozen = client.post("/api/rag_eval/gold-v2/freeze", json={
                    "candidate_run_id": run_id,
                    "ingestion_run_id": "ingest-2",
                    "index_version": "mm-selected",
                })
                self.assertEqual(frozen.status_code, 200)
                self.assertEqual(frozen.json["data"]["sample_count"], 72)
                self.assertEqual(frozen.json["data"]["index_version"], "mm-selected")

                baseline = client.post("/api/rag_eval/baseline-v2/bind", json={})
                self.assertEqual(baseline.status_code, 409)
                self.assertFalse(baseline.json["success"])
                self.assertIn("not frozen", baseline.json["error"])

                governance = client.post("/api/rag_eval/gold-v2/governance", json={
                    "evaluation_run_id": "eval-1",
                    "confirm": True,
                })
                self.assertEqual(governance.status_code, 202)
                self.assertEqual(governance.json["data"]["kind"], "dataset_governance")
                self.assertTrue(governance.json["data"]["confirm"])

    def test_candidate_http_rejects_invalid_integer_payload(self):
        app = Flask(__name__)
        app.register_blueprint(routes.rag_eval_bp)
        client = app.test_client()

        with patch.object(routes, "isolated_run_manager", _FakeCandidateManager(Path("candidate.json"))):
            response = client.post(
                "/api/rag_eval/isolated/candidate-runs",
                json={"ingestion_run_id": "ingest-1", "index_version": "mm-test", "max_workers": True},
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json["success"])


if __name__ == "__main__":
    unittest.main()
