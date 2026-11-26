"""历史重复 Alembic revision ID 的安全映射合同。"""

from __future__ import annotations

import unittest

from Database.migration_version_repair import (
    EXECUTION_RELEASE_REVISION,
    LEGACY_EXECUTION_RELEASE_REVISION,
    LegacyMigrationVersionError,
    repair_legacy_revision_ids,
)


class _Cursor:
    def __init__(
        self,
        tables: set[str],
        revisions: list[str],
        columns: set[str] = set(),
        uppercase_keys: bool = False,
    ) -> None:
        self.tables = tables
        self.revisions = revisions
        self.columns = columns
        self.uppercase_keys = uppercase_keys
        self.rows: list[dict[str, str]] = []
        self.updates: list[tuple[str, str]] = []

    def _row(self, key: str, value: str) -> dict[str, str]:
        return {key.upper() if self.uppercase_keys else key: value}

    def execute(self, statement: str, params=None) -> None:
        if "information_schema.tables" in statement:
            self.rows = [self._row("table_name", table) for table in self.tables]
        elif "SELECT version_num" in statement:
            self.rows = [self._row("version_num", revision) for revision in self.revisions]
        elif "information_schema.columns" in statement:
            self.rows = [self._row("column_name", column) for column in self.columns]
        elif statement.startswith("UPDATE alembic_version"):
            new_revision, old_revision = params
            self.updates.append((old_revision, new_revision))
            self.revisions = [new_revision if revision == old_revision else revision for revision in self.revisions]

    def fetchall(self):
        return list(self.rows)

    def close(self) -> None:
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self, **_kwargs):
        return self._cursor

    def commit(self) -> None:
        self.committed = True


class MigrationVersionRepairTests(unittest.TestCase):
    def test_accepts_mysql_connector_metadata_key_case(self) -> None:
        cursor = _Cursor(
            {"alembic_version", "analysis_jobs", "file_objects", "user_files", "analysis_job_inputs"},
            [LEGACY_EXECUTION_RELEASE_REVISION],
            {"execution_state", "execution_released_at", "execution_release_reason"},
            uppercase_keys=True,
        )
        connection = _Connection(cursor)

        repaired = repair_legacy_revision_ids(connection)

        self.assertEqual(repaired, [f"{LEGACY_EXECUTION_RELEASE_REVISION}->{EXECUTION_RELEASE_REVISION}"])

    def test_maps_legacy_agent_execution_revision_after_schema_verification(self) -> None:
        cursor = _Cursor(
            {"alembic_version", "analysis_jobs", "file_objects", "user_files", "analysis_job_inputs"},
            [LEGACY_EXECUTION_RELEASE_REVISION],
            {"execution_state", "execution_released_at", "execution_release_reason"},
        )
        connection = _Connection(cursor)

        repaired = repair_legacy_revision_ids(connection)

        self.assertEqual(repaired, [f"{LEGACY_EXECUTION_RELEASE_REVISION}->{EXECUTION_RELEASE_REVISION}"])
        self.assertEqual(cursor.updates, [(LEGACY_EXECUTION_RELEASE_REVISION, EXECUTION_RELEASE_REVISION)])
        self.assertTrue(connection.committed)

    def test_refuses_legacy_revision_when_schema_is_ambiguous(self) -> None:
        cursor = _Cursor(
            {"alembic_version", "analysis_jobs", "file_objects", "user_files", "analysis_job_inputs", "rag_eval_jobs"},
            [LEGACY_EXECUTION_RELEASE_REVISION],
            {"execution_state", "execution_released_at", "execution_release_reason"},
        )

        with self.assertRaises(LegacyMigrationVersionError):
            repair_legacy_revision_ids(_Connection(cursor))
        self.assertEqual(cursor.updates, [])
