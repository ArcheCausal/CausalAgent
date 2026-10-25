"""MySQL-backed global capacity leases for data-processing sandboxes."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app.db import get_write_connection


SANDBOX_LEASE_SECONDS = 60
_CAPACITY_ROW_ID = 1


@contextmanager
def _write_transaction() -> Iterator[tuple[Any, Any]]:
    """Yield a primary-database cursor and commit or roll back as one unit."""
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            yield connection, cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
    finally:
        connection.close()


def _lock_capacity(cursor: Any) -> int:
    """Serialize capacity changes and return the configured global limit."""
    cursor.execute(
        """
        SELECT max_active_sandboxes
        FROM analysis_sandbox_capacity
        WHERE id = %s
        FOR UPDATE
        """,
        (_CAPACITY_ROW_ID,),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("analysis_sandbox_capacity singleton row is missing")
    capacity = int(row["max_active_sandboxes"])
    if capacity <= 0:
        raise RuntimeError("analysis_sandbox_capacity must be positive")
    return capacity


def _owns_running_job(
    cursor: Any,
    job_id: str,
    worker_id: str,
    lease_epoch: int,
) -> bool:
    """Lock and verify that this worker still owns the current Job attempt."""
    cursor.execute(
        """
        SELECT status, execution_state, worker_id, lease_epoch
        FROM analysis_jobs
        WHERE job_id = %s
        FOR UPDATE
        """,
        (job_id,),
    )
    job = cursor.fetchone()
    return bool(
        job
        and job["status"] == "running"
        and job["execution_state"] == "leased"
        and job["worker_id"] == worker_id
        and int(job["lease_epoch"]) == int(lease_epoch)
    )


def try_acquire(
    job_id: str,
    worker_id: str,
    lease_epoch: int,
    lease_token: str,
    lease_seconds: int = SANDBOX_LEASE_SECONDS,
) -> bool:
    """Acquire one global sandbox slot, or return False when unavailable."""
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or lease_seconds <= 0
    ):
        raise ValueError("sandbox lease duration must be a positive integer")
    with _write_transaction() as (_, cursor):
        capacity = _lock_capacity(cursor)
        if not _owns_running_job(cursor, job_id, worker_id, lease_epoch):
            return False
        cursor.execute(
            """
            DELETE FROM analysis_sandbox_leases
            WHERE expires_at <= UTC_TIMESTAMP(6)
            """
        )

        cursor.execute(
            """
            SELECT worker_id, lease_epoch, lease_token
            FROM analysis_sandbox_leases
            WHERE job_id = %s AND expires_at > UTC_TIMESTAMP(6)
            FOR UPDATE
            """,
            (job_id,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            return bool(
                existing["worker_id"] == worker_id
                and int(existing["lease_epoch"]) == int(lease_epoch)
                and existing["lease_token"] == lease_token
            )

        cursor.execute(
            """
            SELECT COUNT(*) AS active_count
            FROM analysis_sandbox_leases
            WHERE expires_at > UTC_TIMESTAMP(6)
            """
        )
        active_count = int(cursor.fetchone()["active_count"])
        if active_count >= capacity:
            return False

        cursor.execute(
            """
            INSERT INTO analysis_sandbox_leases
                (job_id, worker_id, lease_epoch, lease_token, expires_at)
            VALUES
                (%s, %s, %s, %s,
                 DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND))
            """,
            (job_id, worker_id, int(lease_epoch), lease_token, lease_seconds),
        )
        return cursor.rowcount == 1


def renew(
    job_id: str,
    worker_id: str,
    lease_epoch: int,
    lease_token: str,
    lease_seconds: int = SANDBOX_LEASE_SECONDS,
) -> bool:
    """Extend a live lease only while its worker still owns the Job lease."""
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or lease_seconds <= 0
    ):
        raise ValueError("sandbox lease duration must be a positive integer")
    with _write_transaction() as (_, cursor):
        _lock_capacity(cursor)
        if not _owns_running_job(cursor, job_id, worker_id, lease_epoch):
            return False
        cursor.execute(
            """
            UPDATE analysis_sandbox_leases
            SET expires_at = DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND)
            WHERE job_id = %s
              AND worker_id = %s
              AND lease_epoch = %s
              AND lease_token = %s
              AND expires_at > UTC_TIMESTAMP(6)
            """,
            (lease_seconds, job_id, worker_id, int(lease_epoch), lease_token),
        )
        return cursor.rowcount == 1


def release(
    job_id: str,
    worker_id: str,
    lease_epoch: int,
    lease_token: str,
) -> None:
    """Release only the exact lease identified by all supplied fencing values."""
    with _write_transaction() as (_, cursor):
        _lock_capacity(cursor)
        cursor.execute(
            """
            DELETE FROM analysis_sandbox_leases
            WHERE job_id = %s
              AND lease_token = %s
              AND worker_id = %s
              AND lease_epoch = %s
            """,
            (job_id, lease_token, worker_id, int(lease_epoch)),
        )
