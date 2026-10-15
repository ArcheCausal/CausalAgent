import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MATRIX_PATH = REPOSITORY_ROOT / "Document" / "rag_eval_production_acceptance_matrix.json"
RUNNER_PATH = REPOSITORY_ROOT / "tests" / "acceptance" / "run_rag_eval_production_acceptance.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location("rag_eval_production_acceptance", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RagEvalProductionAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = _load_runner_module()
        cls.matrix = cls.runner.load_matrix(MATRIX_PATH)

    def test_matrix_has_unique_ids_and_required_source_formats(self):
        ids = [item["id"] for item in self.matrix["checks"]]
        self.assertEqual(len(ids), len(set(ids)))
        formats = {item["source_format"] for item in self.matrix["checks"]}
        self.assertTrue({"pdf", "txt", "markdown", "csv", "xlsx", "image"} <= formats)

    def test_matrix_schema_and_default_layer_are_exact_and_valid(self):
        self.assertEqual(self.matrix["schema_version"], "rag_eval_production_acceptance_v1")
        self.assertEqual(self.matrix["default_layer"], "contract")
        self.runner.validate_matrix(self.matrix)

    def test_acceptance_runner_resolves_repository_root_from_tests_acceptance(self):
        self.assertEqual(self.runner.REPOSITORY_ROOT, REPOSITORY_ROOT)

    def test_matrix_rejects_unknown_fields_that_could_inject_commands(self):
        invalid = json.loads(json.dumps(self.matrix))
        invalid["checks"][0]["command"] = "touch should-not-run"
        with self.assertRaises(ValueError):
            self.runner.validate_matrix(invalid)

    def test_matrix_rejects_unknown_runner_target_requires_and_expected(self):
        for field, value in [
            ("runner", "shell"),
            ("target", "tests.test_rag_eval;rm -rf ."),
            ("requires", ["arbitrary-command"]),
            ("expected", "ignore_failure"),
        ]:
            with self.subTest(field=field):
                invalid = json.loads(json.dumps(self.matrix))
                invalid["checks"][0][field] = value
                with self.assertRaises(ValueError):
                    self.runner.validate_matrix(invalid)

    def test_matrix_rejects_non_whitelisted_field_types(self):
        for field, value in [("layer", ["contract"]), ("runner", {}), ("expected", []), ("requires", "repository_metadata")]:
            with self.subTest(field=field):
                invalid = json.loads(json.dumps(self.matrix))
                invalid["checks"][0][field] = value
                with self.assertRaises(ValueError):
                    self.runner.validate_matrix(invalid)

    def test_matrix_rejects_non_string_requires_members(self):
        for value in (["repository_metadata", {"name": "metadata"}], ["repository_metadata", ["metadata"]]):
            with self.subTest(value=value):
                invalid = json.loads(json.dumps(self.matrix))
                invalid["checks"][0]["requires"] = value
                with self.assertRaises(ValueError):
                    self.runner.validate_matrix(invalid)

    def test_capabilities_map_to_their_specialist_contract_targets(self):
        targets = {item["id"]: item["target"] for item in self.matrix["checks"]}
        expected = {
            "contract.api.run_lifecycle": "tests.test_rag_eval_run_lifecycle",
            "contract.api.deprecated_compatibility": "tests.test_rag_eval_run_lifecycle",
            "contract.datasets.registry_and_ref": "tests.test_rag_eval_dataset_registry",
            "contract.index.binding_gate": "tests.test_rag_eval_index_binding",
            "contract.queue.capacity": "tests.test_rag_eval_queue_capacity",
        }
        self.assertEqual({key: targets[key] for key in expected}, expected)
        for source_format in ("pdf", "txt", "markdown", "csv", "xlsx", "image"):
            check = next(item for item in self.matrix["checks"] if item["source_format"] == source_format)
            self.assertEqual(check["target"], "tests.test_multimodal_contracts")

    def test_default_layer_is_contract_and_never_mutates_external_state(self):
        selected = self.runner.select_checks(self.matrix, layer="contract")
        self.assertTrue(selected)
        self.assertFalse(any(item["mutates_external_state"] for item in selected))

    def test_no_explicit_layer_selects_contract_only(self):
        selected = self.runner.select_checks(self.matrix)
        self.assertTrue(selected)
        self.assertTrue(all(item["layer"] == "contract" for item in selected))

    def test_production_requires_explicit_readiness_confirmation(self):
        with self.assertRaises(ValueError):
            self.runner.prepare_run(self.matrix, layer="production", confirmed=False)

    def test_confirmed_production_selects_read_only_readiness_checks_only(self):
        selected = self.runner.prepare_run(self.matrix, layer="production", confirmed=True)
        self.assertTrue(selected)
        self.assertTrue(all(item["runner"] == "readiness" for item in selected))
        self.assertFalse(any(item["mutates_external_state"] for item in selected))
        forbidden = {"publish", "freeze", "pointer_switch"}
        self.assertFalse(any(forbidden & set(item["requires"]) for item in selected))

    def test_list_does_not_execute_or_write_a_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json"
            with patch.object(self.runner, "execute_check") as execute:
                report = self.runner.run(self.matrix, layer=None, list_only=True, output=output)
            execute.assert_not_called()
            self.assertFalse(output.exists())
            self.assertEqual(report["layer"], "contract")

    def test_main_returns_nonzero_for_failed_execution_report(self):
        with patch.object(self.runner, "run", return_value={"passed": False}), patch.object(
            self.runner.sys, "argv", ["run_rag_eval_production_acceptance.py"]
        ):
            self.assertEqual(self.runner.main(), 1)

    def test_main_returns_zero_for_list_report_without_passed_flag(self):
        with patch.object(self.runner, "run", return_value={"layer": "contract"}), patch.object(
            self.runner.sys, "argv", ["run_rag_eval_production_acceptance.py", "--list"]
        ):
            self.assertEqual(self.runner.main(), 0)

    def test_output_path_refuses_matrix_existing_run_directory_and_isolated_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "existing.json"
            existing.write_text("exists", encoding="utf-8")
            isolated_run = REPOSITORY_ROOT / "tmp" / "rag_eval_isolated_runs" / "run-1" / "report.json"
            for path in (MATRIX_PATH, existing, isolated_run):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        self.runner.resolve_output_path(path, MATRIX_PATH)

    def test_unittest_command_uses_argument_array_and_current_interpreter(self):
        check = next(item for item in self.matrix["checks"] if item["runner"] == "unittest")
        with patch.object(self.runner.subprocess, "run") as subprocess_run:
            subprocess_run.return_value.returncode = 0
            result = self.runner.execute_check(check)
        command = subprocess_run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], self.runner.sys.executable)
        self.assertEqual(command[1:3], ["-m", "unittest"])
        self.assertEqual(result["status"], "pass")

    def test_readiness_checks_required_repository_files_without_subprocess(self):
        check = next(item for item in self.matrix["checks"] if item["runner"] == "readiness")
        with patch.object(self.runner.subprocess, "run") as subprocess_run:
            result = self.runner.execute_check(check)
        self.assertEqual(result["status"], "pass")
        subprocess_run.assert_not_called()

    def test_readiness_fails_when_a_required_repository_file_is_missing(self):
        check = next(item for item in self.matrix["checks"] if item["runner"] == "readiness")
        with patch.object(self.runner, "READINESS_REQUIRED_FILES", (Path("missing-required-file.py"),)), patch.object(
            self.runner.subprocess, "run"
        ) as subprocess_run:
            result = self.runner.execute_check(check)
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing-required-file.py", result["detail"])
        subprocess_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
