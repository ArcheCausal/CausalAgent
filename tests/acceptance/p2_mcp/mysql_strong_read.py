"""Run the strong-read/old-lease contract against an isolated MySQL primary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys

ROOT = Path("/app") if Path("/app/Agent").is_dir() else Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql.connector

from Agent.CausalAgentMCP.service import (
    McpServiceError,
    load_frozen_csv_from_connection,
)
from Agent.deep_agent_tools.models import McpInvocationContext


JOB_ID = "00000000-0000-0000-0000-000000000201"
SESSION_ID = "00000000-0000-0000-0000-000000000202"


def _context(*, lease_epoch: int = 4, worker_id: str = "worker-p2m") -> McpInvocationContext:
    issued = datetime.now(timezone.utc)
    return McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000203",
        job_id=JOB_ID,
        session_id=SESSION_ID,
        user_id=7,
        attempt_count=1,
        lease_epoch=lease_epoch,
        worker_id=worker_id,
        input_snapshot_digest="p2m-file-hash",
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        key_id="current",
    )


def _connect():
    return mysql.connector.connect(
        host=os.getenv("P2M_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("P2M_MYSQL_PORT", "33306")),
        user=os.getenv("P2M_MYSQL_USER", "root"),
        password=os.getenv("P2M_MYSQL_PASSWORD", "p2m-root-password"),
        database=os.getenv("P2M_MYSQL_DATABASE", "p2m_acceptance"),
    )


def main() -> None:
    connection = _connect()
    cursor = connection.cursor()
    keep_fixture = os.getenv("P2M_KEEP_FIXTURE", "0") == "1"
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                job_id VARCHAR(36) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                session_id VARCHAR(36) NOT NULL,
                worker_id VARCHAR(128) NOT NULL,
                attempt_count INT NOT NULL,
                lease_epoch BIGINT NOT NULL,
                execution_state VARCHAR(32) NOT NULL,
                input_user_file_id BIGINT NOT NULL,
                input_object_id BIGINT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_files (
                id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                object_id BIGINT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS file_objects (
                id BIGINT PRIMARY KEY,
                owner_user_id BIGINT NOT NULL,
                content_hash VARCHAR(128) NOT NULL,
                file_content LONGBLOB NOT NULL
            )
            """
        )
        cursor.execute("DELETE FROM analysis_jobs WHERE job_id = %s", (JOB_ID,))
        cursor.execute("DELETE FROM user_files WHERE id = 201")
        cursor.execute("DELETE FROM file_objects WHERE id = 301")
        cursor.execute(
            "INSERT INTO file_objects VALUES (301, 7, 'p2m-file-hash', %s)",
            (b"A,B\n1,2\n3,4\n",),
        )
        cursor.execute("INSERT INTO user_files VALUES (201, 7, 301)")
        cursor.execute(
            "INSERT INTO analysis_jobs VALUES (%s, 7, %s, 'worker-p2m', 1, 4, 'leased', 201, 301)",
            (JOB_ID, SESSION_ID),
        )
        connection.commit()

        accepted = load_frozen_csv_from_connection(_context(), connection)
        assert accepted.startswith("A,B")

        for stale_context in (
            _context(lease_epoch=3),
            _context(worker_id="worker-other"),
        ):
            try:
                load_frozen_csv_from_connection(stale_context, connection)
            except McpServiceError as exc:
                assert exc.safe_error_code.value in {
                    "MCP_LEASE_STALE",
                    "MCP_CONTEXT_INVALID",
                }
            else:
                raise AssertionError("stale authority was accepted")
        print("mysql_strong_read=passed old_lease_rejected=passed")
    finally:
        if not keep_fixture:
            cursor.execute("DELETE FROM analysis_jobs WHERE job_id = %s", (JOB_ID,))
            cursor.execute("DELETE FROM user_files WHERE id = 201")
            cursor.execute("DELETE FROM file_objects WHERE id = 301")
            connection.commit()
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()
