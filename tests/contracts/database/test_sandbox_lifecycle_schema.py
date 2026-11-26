"""无外部替代依赖的沙盒生命周期 schema 一致性契约。"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "Database/migrations/versions/x1y2z3a4b5c6_add_analysis_job_datasets.py"


def _sql_literals(function: str) -> list[str]:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
    return [node.value for node in ast.walk(node) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_migration_creates_exactly_two_tables_and_retains_job_pointer() -> None:
    statements = _sql_literals("upgrade")
    creates = [sql for sql in statements if "CREATE TABLE" in sql]
    assert len(creates) == 2
    assert any("CREATE TABLE analysis_job_datasets" in sql for sql in creates)
    assert any("CREATE TABLE analysis_sandbox_sessions" in sql for sql in creates)
    assert any("ADD COLUMN prepared_dataset_id" in sql for sql in statements)
    assert not any("analysis_sandbox_capacity" in sql or "analysis_sandbox_leases" in sql for sql in statements)


def test_lifecycle_has_no_business_foreign_key_and_enforces_single_open_job() -> None:
    sql = next(sql for sql in _sql_literals("upgrade") if "CREATE TABLE analysis_sandbox_sessions" in sql)
    assert "FOREIGN KEY" not in sql
    assert "ON DELETE" not in sql
    assert "CASE WHEN status <> 'closed' THEN job_id END" in sql
    assert "uq_analysis_sandbox_sessions_open_job (open_job_id)" in sql
    assert "CHECK (expires_at <= max_expires_at)" in sql
    assert "COLLATE utf8mb4_bin" in sql
    assert "ENUM('pending', 'succeeded', 'uncertain')" in sql


def test_downgrade_refuses_any_retained_lifecycle_record() -> None:
    statements = _sql_literals("downgrade")
    assert "SELECT COUNT(*) FROM analysis_sandbox_sessions" in statements
    assert "SELECT COUNT(*) FROM analysis_job_datasets" in statements
    assert not any("DELETE FROM" in sql for sql in statements)
