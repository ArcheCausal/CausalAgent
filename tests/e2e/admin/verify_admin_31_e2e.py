"""核对 3.1/3.2 隔离主从、受控写入、生命周期与审计结果。"""

from __future__ import annotations

import json
import os
import time

import bcrypt
import mysql.connector
import psycopg

from app.db import get_read_connection, get_replica_status
from config.checkpoint_settings import CheckpointPostgresConfig
from config.secret_file import require_secret

from tests.e2e.admin.seed_admin_31_e2e import (
    ATTACHMENT_ID,
    CONTROL_FILE_ID,
    CONTROL_USER_A_ID,
    CONTROL_USER_B_ID,
    DELETE_ARCHIVED_SESSION_ID,
    DELETE_ATTACHMENT_ID,
    DELETE_FILE_ID,
    DELETE_JOB_ID,
    DELETE_MESSAGE_ID,
    DELETE_SESSION_ID,
    DELETE_USER_ID,
    FILE_ID,
    JOB_ID,
    USER_ID,
    USER_MESSAGE_ID,
    USER_SESSION_ID,
)


SENSITIVE_MARKERS = (
    "E2E_MESSAGE_BODY_MARKER_31",
    "E2E_JOB_INPUT_MARKER_31",
    "E2E_JOB_RESULT_MARKER_31",
    "E2E_ATTACHMENT_RESULT_MARKER_31",
)


def _decode_json(value):
    """兼容 mysql-connector 的 JSON 字符串和原生对象。"""
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value)


def verify() -> None:
    """从主库和真实从库核对已知主键、访问副作用和脱敏结果。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT version_num FROM alembic_version")
        assert (cursor.fetchone() or {}).get("version_num") == "c3d4e5f6a7b8"
        cursor.execute(
            """
            SELECT INDEX_NAME AS index_name
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND INDEX_NAME IN (
                  'idx_users_admin_role_active',
                  'idx_sessions_admin_activity',
                  'idx_analysis_jobs_admin_created',
                  'idx_user_files_user_accessed',
                  'idx_admin_audit_target_created'
              )
            """
        )
        assert len({row["index_name"] for row in cursor.fetchall()}) == 5
        cursor.execute(
            """
            SELECT id, username, role, is_active, auth_version, password_hash
            FROM users
            WHERE id IN (%s, %s)
            ORDER BY id
            """,
            (CONTROL_USER_A_ID, CONTROL_USER_B_ID),
        )
        controlled_users = cursor.fetchall()
        assert len(controlled_users) == 2
        assert controlled_users[0]["role"] == "user"
        assert bool(controlled_users[0]["is_active"]) is True
        assert int(controlled_users[0]["auth_version"]) >= 6
        assert int(controlled_users[1]["auth_version"]) >= 2
        assert controlled_users[0]["password_hash"] != controlled_users[1]["password_hash"]
        for row in controlled_users:
            assert bcrypt.checkpw(
                b"Batch-control-password-32",
                row["password_hash"].encode("utf-8"),
            )

        cursor.execute(
            "SELECT COUNT(*) AS total FROM user_files WHERE id = %s",
            (CONTROL_FILE_ID,),
        )
        assert int((cursor.fetchone() or {}).get("total") or 0) == 0
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM users WHERE id = %s) AS users,
                (SELECT COUNT(*) FROM sessions WHERE id = %s) AS sessions,
                (SELECT COUNT(*) FROM chat_messages WHERE id = %s) AS messages,
                (SELECT COUNT(*) FROM chat_attachments WHERE id = %s) AS attachments,
                (SELECT COUNT(*) FROM user_files WHERE id = %s) AS files,
                (SELECT COUNT(*) FROM analysis_jobs WHERE job_id = %s) AS jobs,
                (
                    SELECT COUNT(*) FROM analysis_job_events
                    WHERE job_id = %s
                ) AS events,
                (
                    SELECT COUNT(*) FROM archived_sessions
                    WHERE id = %s
                ) AS archived_sessions,
                0 AS checkpoint_placeholder
            """,
            (
                DELETE_USER_ID,
                DELETE_SESSION_ID,
                DELETE_MESSAGE_ID,
                DELETE_ATTACHMENT_ID,
                DELETE_FILE_ID,
                DELETE_JOB_ID,
                DELETE_JOB_ID,
                DELETE_ARCHIVED_SESSION_ID,
            ),
        )
        assert all(int(value or 0) == 0 for value in (cursor.fetchone() or {}).values())

        cursor.execute(
            """
            SELECT operation_type, status, result_json
            FROM admin_operations
            WHERE actor_user_id = 3101
            ORDER BY id
            """
        )
        operations = cursor.fetchall()
        assert len(operations) >= 8
        operation_types = {row["operation_type"] for row in operations}
        assert {
            "user.set_active",
            "user.set_role",
            "user.set_password",
            "user.delete",
            "file.delete",
        }.issubset(operation_types)
        assert all(row["status"] == "succeeded" for row in operations)
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM admin_operation_items AS i
            JOIN admin_operations AS o ON o.operation_id = i.operation_id
            WHERE o.actor_user_id = 3101
            """
        )
        assert int((cursor.fetchone() or {}).get("total") or 0) >= 9
        operation_text = json.dumps(operations, ensure_ascii=False, default=str)
        assert "Batch-control-password-32" not in operation_text
        assert os.environ["E2E_ADMIN_PASSWORD"] not in operation_text

        cursor.execute(
            "SELECT access_count, last_accessed_at FROM user_files WHERE id = %s",
            (FILE_ID,),
        )
        file_row = cursor.fetchone() or {}
        assert int(file_row.get("access_count") or 0) >= 2
        assert file_row.get("last_accessed_at") is not None

        cursor.execute(
            """
            SELECT action, target_type, target_id, old_values_json,
                   new_values_json, result, error_code, request_id
            FROM admin_audit_events
            WHERE target_id IN (%s, %s, %s, %s)
               OR target_type = 'database_audit'
               OR action IN (
                   'user.set_active',
                   'user.set_role',
                   'user.set_password',
                   'user.delete',
                   'file.delete'
               )
            ORDER BY id
            """,
            (
                str(FILE_ID),
                str(USER_MESSAGE_ID),
                str(ATTACHMENT_ID),
                JOB_ID,
            ),
        )
        audits = cursor.fetchall()
        actions = {row["action"] for row in audits}
        assert "business.file.preview" in actions
        assert "business.file.download" in actions
        assert "business.message.content.view" in actions
        assert "business.attachment.content.view" in actions
        assert "business.job.result.view" in actions
        assert "database.audit.run" in actions
        assert {
            "user.set_active",
            "user.set_role",
            "user.set_password",
            "user.delete",
            "file.delete",
        }.issubset(actions)
        serialized_audits = json.dumps(audits, ensure_ascii=False, default=str)
        for marker in SENSITIVE_MARKERS:
            assert marker not in serialized_audits
        assert "Batch-control-password-32" not in serialized_audits
        assert os.environ["E2E_ADMIN_PASSWORD"] not in serialized_audits
        assert all(row.get("request_id") for row in audits)

        cursor.execute(
            """
            SELECT payload_json, observed_at, refresh_requested_at
            FROM database_monitor_snapshots
            WHERE snapshot_key = 'deep_audit'
            """
        )
        deep_row = cursor.fetchone() or {}
        deep = _decode_json(deep_row.get("payload_json")) or {}
        assert deep.get("mode") == "deep"
        assert deep.get("auto_scheduled") is False
        assert deep_row.get("observed_at") is not None
        assert (
            deep_row.get("refresh_requested_at") is None
            or deep_row["observed_at"] >= deep_row["refresh_requested_at"]
        )
        deep_text = json.dumps(deep, ensure_ascii=False, default=str)
        assert "@" not in deep_text
        assert "GRANT " not in deep_text.upper()

        cursor.execute(
            "SELECT COUNT(*) AS total FROM analysis_job_events WHERE job_id = %s",
            (JOB_ID,),
        )
        assert int((cursor.fetchone() or {}).get("total") or 0) == 2

    checkpoint_config = CheckpointPostgresConfig.from_env()
    checkpoint_config.validate(require_credentials=True)
    with psycopg.connect(
        host=checkpoint_config.host,
        port=checkpoint_config.port,
        dbname=checkpoint_config.database,
        user=checkpoint_config.user,
        password=checkpoint_config.password,
        connect_timeout=max(1, int(checkpoint_config.connect_timeout_seconds)),
        autocommit=True,
    ) as checkpoint_connection:
        with checkpoint_connection.cursor() as checkpoint_cursor:
            for table_name in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                checkpoint_cursor.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE thread_id = %s",
                    (DELETE_SESSION_ID,),
                )
                assert int(checkpoint_cursor.fetchone()[0]) == 0
            checkpoint_cursor.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = %s",
                (USER_SESSION_ID,),
            )
            assert int(checkpoint_cursor.fetchone()[0]) >= 1

    replica_row = None
    replica_control = None
    replica_deleted_user_count = None
    replica_deleted_file_count = None
    replica_host = next(
        host.strip()
        for host in os.environ["MYSQL_READ_HOSTS"].split(",")
        if host.strip()
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with mysql.connector.connect(
                host=replica_host,
                port=int(os.environ["MYSQL_PORT"]),
                user=os.environ["MYSQL_READ_USER"],
                password=require_secret("MYSQL_READ_PASSWORD"),
                database=os.environ["MYSQL_DATABASE"],
            ) as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT id, role FROM users WHERE id = %s",
                    (USER_ID,),
                )
                replica_row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT id, role, is_active, auth_version
                    FROM users
                    WHERE id = %s
                    """,
                    (CONTROL_USER_A_ID,),
                )
                replica_control = cursor.fetchone()
                cursor.execute(
                    "SELECT COUNT(*) AS total FROM users WHERE id = %s",
                    (DELETE_USER_ID,),
                )
                replica_deleted_user_count = int(
                    (cursor.fetchone() or {}).get("total") or 0
                )
                cursor.execute(
                    "SELECT COUNT(*) AS total FROM user_files WHERE id = %s",
                    (DELETE_FILE_ID,),
                )
                replica_deleted_file_count = int(
                    (cursor.fetchone() or {}).get("total") or 0
                )
            if (
                replica_row
                and replica_control
                and replica_control.get("role") == "user"
                and int(replica_control.get("auth_version") or 0) >= 6
                and replica_deleted_user_count == 0
                and replica_deleted_file_count == 0
            ):
                break
        except Exception:
            pass
        time.sleep(1)
    assert replica_row == {"id": USER_ID, "role": "user"}
    assert replica_control is not None
    assert replica_control["role"] == "user"
    assert bool(replica_control["is_active"]) is True
    assert int(replica_control["auth_version"]) >= 6
    assert replica_deleted_user_count == 0
    assert replica_deleted_file_count == 0

    status = get_replica_status(replica_host) or {}
    assert status.get("Replica_IO_Running") == "Yes"
    assert status.get("Replica_SQL_Running") == "Yes"


if __name__ == "__main__":
    verify()
    print("3.1/3.2 隔离主从、受控写入与审计副作用核对通过。")
