import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Agent.knowledge_base.rag.operation_datasets.dataset_governance import govern_dataset
from app.rag_eval.isolated_runs import (
    IsolatedRunManager,
    _dataset_content_sha256,
    _is_retryable_governance_generation_error,
)


def _sample(sample_id, *, generator=None, origin=None):
    source = {}
    if generator is not None:
        source["generator"] = generator
    if origin is not None:
        source["origin"] = origin
    return {
        "sample_id": sample_id,
        "question": f"问题 {sample_id}",
        "reference_answer": "这是一个足够长的参考答案。",
        "expected_claims": ["一个事实"],
        "gold_evidence": [{"unit_id": "u1", "index_version": "idx-1"}],
        "source": source,
    }


class DatasetGovernanceTests(unittest.TestCase):
    def test_content_hash_ignores_snapshot_json_formatting(self):
        source = {
            "schema_version": "rag_eval_v1",
            "dataset_id": "pearl_gold_v2",
            "dataset_kind": "gold_regression",
            "dataset_revision": "rev-1",
            "samples": [_sample("generated", generator="ragas")],
        }
        snapshot = {
            "samples": json.loads(json.dumps(source["samples"], ensure_ascii=False, indent=2)),
            "dataset_revision": "rev-1",
            "dataset_kind": "gold_regression",
            "dataset_id": "pearl_gold_v2",
            "schema_version": "rag_eval_v1",
        }

        self.assertEqual(_dataset_content_sha256(source), _dataset_content_sha256(snapshot))

    def test_only_zero_output_generation_failures_are_retried(self):
        self.assertTrue(_is_retryable_governance_generation_error(ValueError(
            "no candidates passed quality screening (generated=0, rejected=0, generation_errors=1)"
        )))
        self.assertFalse(_is_retryable_governance_generation_error(ValueError(
            "no candidates passed quality screening (generated=3, rejected=3, generation_errors=0)"
        )))

    def test_governance_candidate_batches_preserve_completed_work_and_retry_only_failed_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "governance_12345678"
            state = {
                "run_id": run_id,
                "status": "running",
                "current_stage": "preflight",
                "cancel_requested": False,
                "events": [],
                "ingestion_run_id": "ingestion_12345678",
                "index_version": "index_12345678",
            }
            run_dir = root / run_id
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
            records = [
                {
                    "unit_id": f"unit-{position}",
                    "content": "可检索内容",
                    "metadata": {},
                    "modality": "text",
                    "content_kind": "paragraph",
                }
                for position in range(16)
            ]
            calls = []

            def fake_expand(_index_dir, *, output_path, selected_records, **_kwargs):
                batch_id = selected_records[0]["unit_id"]
                calls.append((batch_id, output_path.name))
                if batch_id == "unit-8" and len([item for item in calls if item[0] == batch_id]) == 1:
                    raise ValueError(
                        "no candidates passed quality screening "
                        "(generated=0, rejected=0, generation_errors=1)"
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps({
                    "samples": [_sample(f"candidate-{batch_id}", generator="ragas")],
                }), encoding="utf-8")

            with (
                patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root),
                patch(
                    "Agent.knowledge_base.rag.operation_datasets.candidate_generation.load_staged_unit_records",
                    return_value=(records, {"index_version": "index_12345678"}),
                ),
                patch(
                    "Agent.knowledge_base.rag.operation_datasets.candidate_generation.expand_candidate_dataset",
                    side_effect=fake_expand,
                ),
                patch("app.rag_eval.isolated_runs.time.sleep"),
            ):
                candidates = IsolatedRunManager()._generate_governance_candidates(state, required=2)

            self.assertEqual([item[0] for item in calls], ["unit-0", "unit-8", "unit-8"])
            self.assertEqual(len(candidates), 2)
            self.assertEqual(
                [item["source"]["index_binding"] for item in candidates],
                [{"index_version": "index_12345678"}, {"index_version": "index_12345678"}],
            )
            aggregate = json.loads((run_dir / "replacement_candidates.aggregate.audit.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate["accepted_count"], 2)
            self.assertEqual(len(aggregate["batches"]), 2)
            self.assertEqual(aggregate["batches"][1]["attempts"][0]["status"], "failed")
            self.assertEqual(aggregate["batches"][1]["attempts"][1]["status"], "succeeded")

    def test_governance_candidate_batches_continue_after_two_connection_failed_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "governance_continue_12345678"
            state = {
                "run_id": run_id,
                "status": "running",
                "current_stage": "preflight",
                "cancel_requested": False,
                "events": [],
                "ingestion_run_id": "ingestion_12345678",
                "index_version": "index_12345678",
            }
            run_dir = root / run_id
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
            records = [
                {
                    "unit_id": f"unit-{position}",
                    "content": "可检索内容",
                    "metadata": {},
                    "modality": "text",
                    "content_kind": "paragraph",
                }
                for position in range(24)
            ]
            calls: list[str] = []

            def fake_expand(_index_dir, *, output_path, selected_records, **_kwargs):
                batch_id = selected_records[0]["unit_id"]
                calls.append(batch_id)
                if batch_id in {"unit-0", "unit-8"}:
                    raise ValueError(
                        "no candidates passed quality screening "
                        "(generated=0, rejected=0, generation_errors=1)"
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps({
                    "samples": [
                        _sample(f"candidate-after-connection-failures-{position}", generator="ragas")
                        for position in range(6)
                    ],
                }), encoding="utf-8")

            with (
                patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root),
                patch(
                    "Agent.knowledge_base.rag.operation_datasets.candidate_generation.load_staged_unit_records",
                    return_value=(records, {"index_version": "index_12345678"}),
                ),
                patch(
                    "Agent.knowledge_base.rag.operation_datasets.candidate_generation.expand_candidate_dataset",
                    side_effect=fake_expand,
                ),
                patch("app.rag_eval.isolated_runs.time.sleep"),
            ):
                candidates = IsolatedRunManager()._generate_governance_candidates(state, required=6)

            self.assertEqual(calls, ["unit-0", "unit-0", "unit-0", "unit-8", "unit-8", "unit-8", "unit-16"])
            self.assertEqual(len(candidates), 6)
            aggregate = json.loads((run_dir / "replacement_candidates.aggregate.audit.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate["accepted_count"], 6)
            self.assertEqual(len(aggregate["batches"]), 3)

    def test_tuning_candidate_batches_are_isolated_per_round(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "tuning_12345678"
            state = {
                "run_id": run_id,
                "status": "running",
                "current_stage": "preflight",
                "cancel_requested": False,
                "events": [],
                "ingestion_run_id": "ingestion_12345678",
                "index_version": "index_12345678",
            }
            run_dir = root / run_id
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
            records = [
                {
                    "unit_id": f"unit-{position}",
                    "content": "可检索内容",
                    "metadata": {},
                    "modality": "text",
                    "content_kind": "paragraph",
                }
                for position in range(16)
            ]
            written_paths: list[str] = []

            def fake_expand(_index_dir, *, output_path, selected_records, **_kwargs):
                if output_path.exists():
                    raise FileExistsError(
                        f"candidate dataset already exists; choose a new revision path: {output_path}"
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                written_paths.append(str(output_path.relative_to(run_dir)).replace("\\", "/"))
                output_path.write_text(json.dumps({
                    "samples": [_sample(f"candidate-{selected_records[0]['unit_id']}", generator="ragas")],
                }), encoding="utf-8")

            with (
                patch("app.rag_eval.isolated_runs.ISOLATED_RUN_ROOT", root),
                patch(
                    "Agent.knowledge_base.rag.operation_datasets.candidate_generation.load_staged_unit_records",
                    return_value=(records, {"index_version": "index_12345678"}),
                ),
                patch(
                    "Agent.knowledge_base.rag.operation_datasets.candidate_generation.expand_candidate_dataset",
                    side_effect=fake_expand,
                ),
            ):
                first = IsolatedRunManager()._generate_governance_candidates(state, required=2, round_number=1)
                second = IsolatedRunManager()._generate_governance_candidates(state, required=2, round_number=2)

            self.assertEqual(len(first), 2)
            self.assertEqual(len(second), 2)
            self.assertEqual(
                written_paths,
                [
                    "replacement_candidate_batches/round_001/batch_001_attempt_1.json",
                    "replacement_candidate_batches/round_001/batch_002_attempt_1.json",
                    "replacement_candidate_batches/round_002/batch_001_attempt_1.json",
                    "replacement_candidate_batches/round_002/batch_002_attempt_1.json",
                ],
            )
            round_one_audit = json.loads(
                (run_dir / "replacement_candidate_batches" / "round_001" / "aggregate.audit.json").read_text(encoding="utf-8")
            )
            round_two_audit = json.loads(
                (run_dir / "replacement_candidate_batches" / "round_002" / "aggregate.audit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(round_one_audit["round"], 1)
            self.assertEqual(round_two_audit["round"], 2)
            self.assertEqual(round_one_audit["accepted_count"], 2)
            self.assertEqual(round_two_audit["accepted_count"], 2)

    def test_only_generated_samples_can_be_retired(self):
        dataset = {
            "schema_version": "rag_eval_v1",
            "dataset_kind": "gold_regression",
            "dataset_id": "pearl_gold_v2",
            "dataset_revision": "old",
            "samples": [
                _sample("human", origin="human_reviewed"),
                _sample("unmarked"),
                _sample("other-origin", origin="reference_free"),
                _sample("generated", generator="ragas_0.4.3"),
            ],
        }
        report = {
            "items": [
                {"sample_id": sample_id, "flags": ["ambiguous_reference"]}
                for sample_id in ("human", "unmarked", "other-origin", "generated")
            ]
        }
        calls = []

        def reviewer(sample, *, purpose):
            calls.append((sample["sample_id"], purpose))
            return {"verdict": "replace" if purpose == "retire" else "accept", "confidence": 0.9, "reason": "confirmed"}

        replacement = _sample("replacement", generator="ragas_0.4.3")
        governed, result = govern_dataset(dataset, report, [replacement], reviewer)

        self.assertEqual([item["sample_id"] for item in governed["samples"]], ["human", "unmarked", "other-origin", "generated"])
        self.assertEqual(result["protected_count"], 1)
        self.assertEqual(result["diagnosed_count"], 1)
        self.assertEqual(result["replaced_count"], 1)
        self.assertEqual(calls, [("generated", "retire"), ("replacement", "accept")])
        self.assertEqual(
            next(item for item in result["items"] if item["sample_id"] == "unmarked")["action"],
            "retained",
        )

    def test_reviewer_failure_retains_generated_sample_fail_closed(self):
        dataset = {
            "schema_version": "rag_eval_v1",
            "dataset_kind": "gold_regression",
            "dataset_revision": "old",
            "samples": [_sample("generated", generator="ragas")],
        }
        report = {"items": [{"sample_id": "generated", "flags": ["ambiguous_reference"]}]}

        def reviewer(_sample, *, purpose):
            self.assertEqual(purpose, "retire")
            raise TimeoutError("mock timeout")

        governed, result = govern_dataset(dataset, report, [], reviewer)

        self.assertEqual(governed["samples"][0]["sample_id"], "generated")
        self.assertEqual(result["replaced_count"], 0)
        item = result["items"][0]
        self.assertEqual(item["action"], "retained")
        self.assertIn("retain_fail_closed", item["reasons"])

    def test_no_replacement_keeps_revision_and_exposes_review_flags(self):
        dataset = {
            "schema_version": "rag_eval_v1",
            "dataset_kind": "gold_regression",
            "dataset_revision": "old",
            "samples": [_sample("generated", generator="ragas")],
        }
        report = {"items": [{"sample_id": "generated", "flags": ["ambiguous_reference"]}]}
        seen = {}

        def reviewer(sample, *, purpose):
            seen.update(sample["_governance_review"])
            return {"verdict": "retain", "confidence": 0.95, "reason": "still answerable"}

        governed, result = govern_dataset(dataset, report, [], reviewer)

        self.assertEqual(governed["dataset_revision"], "old")
        self.assertFalse(result["changed"])
        self.assertEqual(result["publish_status"], "no_change")
        self.assertEqual(seen["intrinsic_flags"], ["ambiguous_reference"])

    def test_stale_publish_cannot_overwrite_newer_gold(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pearl_gold_v2.json"
            original = {"dataset_revision": "old", "samples": []}
            newer = {"dataset_revision": "newer", "samples": []}
            path.write_text(json.dumps(original), encoding="utf-8")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            path.write_text(json.dumps(newer), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "stale run refused"):
                IsolatedRunManager._persist_governed_gold(
                    {"dataset_revision": "stale", "samples": []},
                    path,
                    expected_sha256=expected,
                )

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), newer)

    def test_candidate_binding_matches_current_index_without_copying_audit_counts(self):
        current = _sample("generated", generator="ragas")
        current["source"]["source_snapshot"] = {"index_version": "idx-1", "manifest_sha256": "m1"}
        current["source"]["index_binding"] = {"index_version": "idx-1", "checked_locator_count": 107}
        dataset = {
            "schema_version": "rag_eval_v1",
            "dataset_kind": "gold_regression",
            "dataset_revision": "old",
            "samples": [current],
        }
        candidate = _sample("replacement", generator="ragas")
        candidate["source"]["source_snapshot"] = {"index_version": "idx-1", "manifest_sha256": "m1"}
        candidate["source"]["index_binding"] = {"index_version": "idx-1"}

        def reviewer(_sample, *, purpose):
            return {
                "verdict": "replace" if purpose == "retire" else "accept",
                "confidence": 0.95,
                "reason": "confirmed",
            }

        governed, result = govern_dataset(
            dataset,
            {"items": [{"sample_id": "generated", "flags": ["ambiguous_reference"]}]},
            [candidate],
            reviewer,
        )

        self.assertTrue(result["changed"])
        self.assertEqual(governed["samples"][0]["source"]["index_binding"]["index_version"], "idx-1")


if __name__ == "__main__":
    unittest.main()
