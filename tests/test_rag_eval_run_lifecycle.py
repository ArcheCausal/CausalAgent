import json
import queue
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import app.rag_eval.routes as routes

from tests.support.authorization import rag_eval_route_authorization


class _FakeRunManager:
    def __init__(self, artifact_root: Path) -> None:
        self._artifact_root = artifact_root
        self._state = {"run_id": "run-1", "kind": "evaluation", "status": "completed"}

    def get(self, run_id):
        if run_id != "run-1":
            raise KeyError(run_id)
        return self._state

    def get_result(self, run_id):
        self.get(run_id)
        return {"run_id": run_id, "status": "pass"}

    def get_artifact_path(self, run_id, artifact_name):
        self.get(run_id)
        if artifact_name not in {"summary.json", "summary.md"}:
            raise ValueError("artifact is unavailable")
        return self._artifact_root / artifact_name

    def cancel(self, run_id):
        return {**self.get(run_id), "status": "cancelling", "cancel_requested": True}

    def subscribe(self, run_id):
        self.get(run_id)
        events = queue.Queue()
        events.put({"type": "run_done", "run_id": run_id})
        return events

    def unsubscribe(self, run_id):
        return None


class RunLifecycleRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        artifact_root = Path(self.temporary.name)
        (artifact_root / "summary.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
        (artifact_root / "summary.md").write_text("# summary\n", encoding="utf-8")
        self.manager = _FakeRunManager(artifact_root)
        self.app = Flask(__name__)
        self.app.register_blueprint(routes.rag_eval_bp)
        self.client = self.app.test_client()
        self.manager_patch = patch.object(routes, "isolated_run_manager", self.manager)
        self.manager_patch.start()
        self.authorization_stack = ExitStack()
        for authorization_context in rag_eval_route_authorization():
            self.authorization_stack.enter_context(authorization_context)

    def tearDown(self):
        self.authorization_stack.close()
        self.manager_patch.stop()
        self.temporary.cleanup()

    def test_official_run_lifecycle_routes(self):
        state = self.client.get("/api/rag_eval/isolated/runs/run-1")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.get_json()["data"]["status"], "completed")

        result = self.client.get("/api/rag_eval/isolated/runs/run-1/result")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["data"]["status"], "pass")

        json_artifact = self.client.get("/api/rag_eval/isolated/runs/run-1/artifacts/summary.json")
        self.assertEqual(json_artifact.status_code, 200)
        self.assertEqual(json_artifact.get_json()["data"], {"status": "pass"})

        markdown_artifact = self.client.get("/api/rag_eval/isolated/runs/run-1/artifacts/summary.md")
        self.assertEqual(markdown_artifact.status_code, 200)
        self.assertEqual(markdown_artifact.mimetype, "text/markdown")
        self.assertEqual(markdown_artifact.text, "# summary\n")

        stream = self.client.get("/api/rag_eval/isolated/runs/run-1/stream")
        self.assertEqual(stream.status_code, 200)
        self.assertIn(b'"type": "connected"', stream.data)

        cancelled = self.client.post("/api/rag_eval/isolated/runs/run-1/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertTrue(cancelled.get_json()["data"]["cancel_requested"])

    def test_legacy_lifecycle_routes_advertise_the_official_successor(self):
        legacy_requests = [
            ("get", "/api/rag_eval/isolated/evaluation-runs/run-1"),
            ("get", "/api/rag_eval/isolated/evaluation-runs/run-1/result"),
            ("get", "/api/rag_eval/isolated/evaluation-runs/run-1/artifacts/summary.json"),
            ("get", "/api/rag_eval/isolated/evaluation-runs/run-1/stream"),
            ("post", "/api/rag_eval/isolated/evaluation-runs/run-1/cancel"),
        ]
        for method, path in legacy_requests:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("Deprecation"), "true")
                self.assertEqual(
                    response.headers.get("Link"),
                    '</api/rag_eval/isolated/runs/run-1>; rel="successor-version"',
                )


if __name__ == "__main__":
    unittest.main()
