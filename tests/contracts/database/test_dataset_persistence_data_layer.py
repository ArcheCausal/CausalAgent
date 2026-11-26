"""Dataset persistence schema and context-switch contracts."""

from __future__ import annotations

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _assignment_value(path: str, name: str):
    tree = ast.parse((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
            return ast.literal_eval(statement.value)
    raise AssertionError(f"{path} does not define {name}")


def _function_string_literals(path: str, function_name: str) -> list[str]:
    tree = ast.parse((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    return [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _function_assignment_value(path: str, function_name: str, name: str):
    tree = ast.parse((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    for statement in ast.walk(function):
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise AssertionError(f"{path}:{function_name} does not assign {name}")


def test_readiness_requires_dataset_and_sandbox_schema() -> None:
    source = (REPOSITORY_ROOT / "app/db.py").read_text(encoding="utf-8")
    required_tables = set(
        _function_assignment_value("app/db.py", "check_database_readiness", "required_tables")
    )
    required_indexes = _function_assignment_value(
        "app/db.py",
        "check_database_readiness",
        "required_indexes",
    )

    assert {
        "analysis_job_datasets",
        "analysis_sandbox_sessions",
    } <= required_tables
    assert "'prepared_dataset_id'" in source
    assert "idx_analysis_jobs_prepared_dataset" in source
    assert "uq_analysis_job_datasets_job_revision" in source
    assert "uq_analysis_job_datasets_job_idempotency" in source
    assert "uq_analysis_job_datasets_job_id" in source
    assert "idx_analysis_job_datasets_objects" in source
    assert "idx_analysis_job_datasets_job_status" in source
    assert "uq_analysis_sandbox_sessions_request" in source
    assert "uq_analysis_sandbox_sessions_remote" in source
    assert "uq_analysis_sandbox_sessions_open_job" in source
    assert "idx_analysis_sandbox_sessions_expiry" in source
    assert "idx_analysis_sandbox_sessions_reconcile" in source
    assert "analysis_sandbox_capacity" not in source
    assert "analysis_sandbox_leases" not in source
    assert ("analysis_job_datasets", "idx_analysis_job_datasets_objects") in required_indexes
    assert ("analysis_job_datasets", "idx_analysis_job_datasets_job_status") in required_indexes
    assert ("analysis_sandbox_sessions", "idx_analysis_sandbox_sessions_expiry") in required_indexes
    assert ("analysis_jobs", "idx_analysis_jobs_prepared_dataset") in required_indexes


def test_deep_audit_requires_dataset_columns_indexes_and_foreign_keys() -> None:
    columns = _assignment_value("Database/deep_audit.py", "EXPECTED_COLUMNS")
    indexes = _assignment_value("Database/deep_audit.py", "EXPECTED_INDEXES")
    foreign_keys = _assignment_value("Database/deep_audit.py", "EXPECTED_FOREIGN_KEYS")
    delete_rules = _assignment_value(
        "Database/deep_audit.py",
        "EXPECTED_FOREIGN_KEY_DELETE_RULES",
    )

    assert "prepared_dataset_id" in columns["analysis_jobs"]
    assert {
        "id",
        "job_id",
        "revision",
        "data_object_id",
        "script_object_id",
        "status",
        "metadata",
        "validation_report",
    } <= columns["analysis_job_datasets"]
    assert "analysis_sandbox_capacity" not in columns
    assert "analysis_sandbox_leases" not in columns
    assert {"request_id", "request_digest", "source_hash", "job_id", "worker_id",
            "attempt_count", "lease_epoch", "remote_sandbox_id", "status", "metadata",
            "operation_id", "operation_digest", "operation_type", "operation_status",
            "operation_result", "heartbeat_at", "expires_at", "max_expires_at",
            "next_retry_at", "version", "created_at", "updated_at", "closed_at"} <= (
        columns["analysis_sandbox_sessions"]
    )
    assert "idx_analysis_jobs_prepared_dataset" in indexes["analysis_jobs"]
    assert "uq_analysis_job_datasets_job_revision" in indexes["analysis_job_datasets"]
    assert "idx_analysis_sandbox_sessions_expiry" in indexes["analysis_sandbox_sessions"]
    assert {
        "fk_analysis_job_datasets_job",
        "fk_analysis_job_datasets_data_object",
        "fk_analysis_job_datasets_script_object",
    } <= foreign_keys
    assert not any("sandbox" in name for name in foreign_keys)
    assert (
        delete_rules[("analysis_job_datasets", "fk_analysis_job_datasets_data_object")]
        == "RESTRICT"
    )
    assert (
        delete_rules[("analysis_job_datasets", "fk_analysis_job_datasets_script_object")]
        == "RESTRICT"
    )


def test_operational_audit_checks_dataset_file_object_delete_rules() -> None:
    expected = _assignment_value(
        "Database/inspection.py",
        "EXPECTED_FOREIGN_KEY_DELETE_RULES",
    )
    assert (
        "analysis_job_datasets",
        "fk_analysis_job_datasets_data_object",
        "RESTRICT",
    ) in expected
    assert (
        "analysis_job_datasets",
        "fk_analysis_job_datasets_script_object",
        "RESTRICT",
    ) in expected

    inspection_path = REPOSITORY_ROOT / "Database/inspection.py"
    source = inspection_path.read_text(encoding="utf-8")
    assert "information_schema.referential_constraints" in source
    assert "MAX(UPPER(delete_rule))" in source
    tree = ast.parse(source)
    audit_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_operational_integrity_definitions"
    )
    assert any(
        isinstance(node, ast.Name) and node.id == "EXPECTED_FOREIGN_KEY_DELETE_RULES"
        for node in ast.walk(audit_function)
    )


def test_context_switch_clears_only_stale_dataset_bindings() -> None:
    activate_sql = _function_string_literals("Database/analysis_contexts.py", "_activate_context")
    assert any(
        "UPDATE analysis_jobs" in sql
        and "prepared_dataset_id = CASE" in sql
        and "WHEN analysis_context_id = %s THEN prepared_dataset_id" in sql
        for sql in activate_sql
    )

    refreeze_sql = _function_string_literals("Database/analysis_contexts.py", "_refreeze_job_input")
    assert any(
        "UPDATE analysis_jobs" in sql
        and "prepared_dataset_id = NULL" in sql
        for sql in refreeze_sql
    )
