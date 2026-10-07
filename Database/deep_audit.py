"""管理员 3.1/3.2 手动 deep 数据库事实审计。

该模块只执行有超时、结果有上限的只读查询；返回值仅包含逻辑结论，
不返回真实数据库账号、host、grants、连接串或业务正文。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from Database.checkpoint_inspection import (
    CHECKPOINT_DATA_TABLES,
    collect_checkpoint_deep_facts,
)
from Database.inspection import inspect_revision
from app.db import get_read_connection, get_replica_status
from config.settings import settings


ANOMALY_SAMPLE_LIMIT = 20

EXPECTED_COLUMNS = {
    "users": {
        "id", "username", "role", "is_active", "created_at", "last_login_at",
        "auth_version", "password_changed_at",
    },
    "sessions": {
        "id", "user_id", "title", "created_at", "last_activity_at",
        "message_count", "is_archived", "archived_at",
    },
    "chat_messages": {
        "id", "session_id", "user_id", "message_type", "content",
        "has_attachment", "analysis_job_id", "analysis_job_input_id",
        "source_event_id", "created_at",
    },
    "chat_attachments": {
        "id", "message_id", "attachment_type", "content",
        "content_size", "created_at",
    },
    "file_objects": {
        "id", "owner_user_id", "content_hash", "file_size", "mime_type",
        "file_content", "created_at",
    },
    "user_files": {
        "id", "user_id", "object_id", "filename", "mime_type", "file_size",
        "uploaded_at", "last_accessed_at", "access_count",
    },
    "archived_sessions": {
        "id", "user_id", "original_session_data", "message_count", "archived_at",
    },
    "checkpoint_cleanup_outbox": {
        "id", "thread_id", "operation_id", "status", "attempts",
        "available_at", "lease_expires_at", "last_error", "created_at",
        "completed_at",
    },
    "user_memory_cleanup_outbox": {
        "id", "user_id", "operation_id", "status", "attempts",
        "available_at", "lease_expires_at", "last_error", "created_at",
        "completed_at",
    },
    "analysis_jobs": {
        "id", "job_id", "user_id", "session_id", "status", "worker_id",
        "lease_epoch", "execution_state", "locked_at", "heartbeat_at",
        "execution_released_at", "execution_release_reason", "attempt_count",
        "recovery_count", "resume_count", "finished_at", "created_at",
        "active_session_key", "request_id", "idempotency_key", "request_fingerprint",
        "input_user_file_id", "input_object_id", "input_file_hash",
        "input_filename", "current_question_id", "current_waiting_prompt",
        "cancel_idempotency_key", "cancel_request_fingerprint",
        "analysis_context_id",
    },
    "analysis_contexts": {
        "analysis_context_id", "session_id", "user_id", "status",
        "input_user_file_id", "file_object_id", "file_hash", "filename",
        "target", "treatment", "analysis_question", "latest_algorithm_summary",
        "latest_rag_evidence", "latest_web_evidence",
        "latest_report_message_id", "latest_report_id",
        "created_at", "updated_at",
    },
    "analysis_job_events": {
        "id", "job_id", "event_type", "event_key", "payload_json", "created_at",
    },
    "analysis_job_inputs": {
        "input_id", "job_id", "sequence", "input_type", "input_text",
        "question_id", "idempotency_key", "request_fingerprint",
        "chat_message_id", "created_at", "analysis_context_id",
    },
    "database_monitor_snapshots": {
        "snapshot_key", "payload_json", "observed_at",
        "refresh_requested_at", "updated_at",
    },
    "database_monitor_settings": {"id", "version", "updated_by_user_id", "updated_at"},
    "admin_audit_events": {
        "id", "actor_user_id", "actor_username", "action", "target_type",
        "target_id", "result", "request_id", "created_at",
    },
    "admin_operations": {
        "id", "operation_id", "actor_user_id", "actor_username",
        "operation_type", "idempotency_key", "request_fingerprint", "status",
        "target_count", "succeeded_count", "failed_count", "result_json",
        "request_id", "created_at", "completed_at",
    },
    "admin_operation_items": {
        "id", "operation_id", "target_type", "target_id", "target_label",
        "result", "error_code", "old_values_json", "new_values_json",
        "created_at",
    },
}

EXPECTED_INDEXES = {
    "users": {"PRIMARY", "idx_users_admin_role_active"},
    "sessions": {"PRIMARY", "idx_sessions_admin_activity"},
    "analysis_jobs": {
        "PRIMARY",
        "idx_analysis_jobs_admin_created",
        "idx_analysis_jobs_input_user_file_status",
        "idx_analysis_jobs_execution_state_heartbeat",
        "idx_analysis_jobs_analysis_context",
        "uq_analysis_jobs_user_idempotency", "uq_analysis_jobs_cancel_idempotency",
    },
    "analysis_contexts": {
        "PRIMARY",
        "idx_analysis_contexts_session_updated",
        "idx_analysis_contexts_user_session_status",
        "idx_analysis_contexts_input_user_file",
    },
    "analysis_job_events": {"PRIMARY", "uq_analysis_job_events_event_key"},
    "analysis_job_inputs": {
        "PRIMARY",
        "uq_analysis_job_inputs_sequence",
        "uq_analysis_job_inputs_idempotency",
        "idx_analysis_job_inputs_analysis_context",
    },
    "file_objects": {
        "PRIMARY", "uq_file_objects_owner_hash", "idx_file_objects_owner_created",
    },
    "user_files": {
        "PRIMARY", "uq_user_files_name_object", "idx_user_files_user_accessed",
        "idx_user_files_user_filename", "idx_user_files_object",
    },
    "chat_messages": {"PRIMARY", "uq_chat_messages_source_event"},
    "admin_audit_events": {"PRIMARY", "idx_admin_audit_target_created"},
    "checkpoint_cleanup_outbox": {
        "PRIMARY",
        "uq_checkpoint_cleanup_outbox_thread",
        "idx_checkpoint_cleanup_outbox_claim",
    },
    "user_memory_cleanup_outbox": {
        "PRIMARY",
        "uq_user_memory_cleanup_outbox_user",
        "idx_user_memory_cleanup_outbox_claim",
    },
    "admin_operations": {
        "PRIMARY",
        "uq_admin_operations_operation_id",
        "uq_admin_operations_actor_idempotency",
    },
    "admin_operation_items": {"PRIMARY", "uq_admin_operation_items_target"},
}

EXPECTED_FOREIGN_KEYS = {
    "fk_sessions_user",
    "fk_chat_messages_session",
    "fk_chat_messages_user",
    "fk_chat_attachments_message",
    "fk_file_objects_owner",
    "fk_user_files_user",
    "fk_user_files_object",
    "fk_analysis_jobs_user",
    "fk_analysis_jobs_session",
    "fk_analysis_jobs_input_user_file",
    "fk_analysis_jobs_input_object",
    "fk_analysis_job_events_job",
    "fk_analysis_job_inputs_job",
    "fk_checkpoint_cleanup_outbox_operation",
    "fk_user_memory_cleanup_outbox_operation",
    "fk_admin_operations_actor",
    "fk_admin_operation_items_operation",
    "fk_analysis_contexts_session",
    "fk_analysis_contexts_user",
    "fk_analysis_contexts_input_user_file",
    "fk_analysis_contexts_file_object",
    "fk_analysis_jobs_analysis_context",
    "fk_analysis_job_inputs_analysis_context",
}


def _observed_at() -> str:
    """返回毫秒精度 UTC 采集时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _check(
    key: str,
    label: str,
    status: str,
    summary: str,
    details: Any = None,
) -> dict[str, Any]:
    """构造不含内部连接信息的 deep 审计检查项。"""
    return {
        "key": key,
        "label": label,
        "status": status,
        "summary": summary,
        "details": details,
    }


def _overall_status(checks: list[dict[str, Any]]) -> str:
    """按 error、warning/unknown、healthy 优先级汇总。"""
    statuses = {item["status"] for item in checks}
    if "error" in statuses:
        return "error"
    if statuses.intersection({"warning", "unknown"}):
        return "warning"
    return "healthy"


def _safe_block(
    key: str,
    label: str,
    collector: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """隔离单项失败，避免一个检查阻断其他 deep 事实。"""
    try:
        return collector()
    except Exception:
        return _check(key, label, "unknown", "检查失败或查询超时")


def _revision_check() -> dict[str, Any]:
    """复用现有 revision 检查并转换为 deep 审计结构。"""
    result = inspect_revision()
    value = result.get("value") or {}
    matches = value.get("matches")
    if matches is True:
        summary = "仓库 head 与实例 revision 一致"
    elif matches is False:
        summary = "仓库 head 与实例 revision 不一致"
    else:
        summary = "无法完整确认 revision"
    return _check(
        "revision",
        "Alembic revision",
        result.get("status", "unknown"),
        summary,
        {
            "repository_heads": value.get("repository_heads"),
            "instance_revisions": value.get("instance_revisions"),
        },
    )


def _schema_check() -> dict[str, Any]:
    """检查关键字段、管理员列表索引和外键是否存在。"""
    timeout = int(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT /*+ MAX_EXECUTION_TIME({timeout}) */
                   TABLE_NAME AS table_name, COLUMN_NAME AS column_name
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN ({', '.join(['%s'] * len(EXPECTED_COLUMNS))})
            """,
            tuple(EXPECTED_COLUMNS),
        )
        actual_columns: dict[str, set[str]] = {}
        for row in cursor.fetchall():
            actual_columns.setdefault(row["table_name"], set()).add(row["column_name"])

        cursor.execute(
            f"""
            SELECT /*+ MAX_EXECUTION_TIME({timeout}) */
                   TABLE_NAME AS table_name, INDEX_NAME AS index_name
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN ({', '.join(['%s'] * len(EXPECTED_INDEXES))})
            """,
            tuple(EXPECTED_INDEXES),
        )
        actual_indexes: dict[str, set[str]] = {}
        for row in cursor.fetchall():
            actual_indexes.setdefault(row["table_name"], set()).add(row["index_name"])

        cursor.execute(
            f"""
            SELECT /*+ MAX_EXECUTION_TIME({timeout}) */
                   CONSTRAINT_NAME AS constraint_name
            FROM information_schema.REFERENTIAL_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
            """
        )
        actual_foreign_keys = {row["constraint_name"] for row in cursor.fetchall()}

    missing_columns = {
        table: sorted(columns - actual_columns.get(table, set()))
        for table, columns in EXPECTED_COLUMNS.items()
        if columns - actual_columns.get(table, set())
    }
    missing_indexes = {
        table: sorted(indexes - actual_indexes.get(table, set()))
        for table, indexes in EXPECTED_INDEXES.items()
        if indexes - actual_indexes.get(table, set())
    }
    missing_foreign_keys = sorted(EXPECTED_FOREIGN_KEYS - actual_foreign_keys)
    healthy = not missing_columns and not missing_indexes and not missing_foreign_keys
    return _check(
        "schema",
        "关键 schema",
        "healthy" if healthy else "error",
        "关键字段、索引和外键完整" if healthy else "关键 schema 存在缺失",
        {
            "missing_columns": missing_columns,
            "missing_indexes": missing_indexes,
            "missing_foreign_keys": missing_foreign_keys,
        },
    )


def _runtime_facts_check() -> dict[str, Any]:
    """检查字符集、UTC 偏移和事务隔离级别。"""
    timeout = int(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT /*+ MAX_EXECUTION_TIME({timeout}) */
                   @@character_set_database AS character_set,
                   @@collation_database AS collation_name,
                   @@session.time_zone AS session_time_zone,
                   @@global.time_zone AS global_time_zone,
                   @@transaction_isolation AS isolation_level,
                   TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW()) AS utc_offset_seconds
            """
        )
        row = cursor.fetchone() or {}
    character_set_ok = str(row.get("character_set") or "").lower() == "utf8mb4"
    utc_offset = int(row.get("utc_offset_seconds") or 0)
    utc_ok = utc_offset == 0
    status = "healthy" if character_set_ok and utc_ok else "warning"
    summary = (
        "字符集、数据库时钟和隔离级别已确认"
        if status == "healthy"
        else "字符集或数据库时钟需要复核"
    )
    return _check(
        "runtime_facts",
        "字符集、时区与隔离级别",
        status,
        summary,
        {
            "character_set": row.get("character_set"),
            "collation": row.get("collation_name"),
            "session_time_zone": row.get("session_time_zone"),
            "global_time_zone": row.get("global_time_zone"),
            "utc_offset_seconds": utc_offset,
            "isolation_level": row.get("isolation_level"),
        },
    )


def _account_boundary_check() -> dict[str, Any]:
    """检查账号职责配置与读账号权限结论，不返回账号和 grants。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor()
        cursor.execute("SHOW GRANTS")
        grants = [
            str(value)
            for row in cursor.fetchall()
            for value in row
        ]
    normalized = "\n".join(grants).upper()
    dangerous_markers = (
        "GRANT OPTION",
        "CREATE USER",
        "SYSTEM_USER",
        "SHUTDOWN",
        "SUPER",
        "RELOAD",
        "ALL PRIVILEGES ON *.*",
    )
    read_has_dangerous_global = any(marker in normalized for marker in dangerous_markers)
    read_has_select = "SELECT" in normalized
    accounts_separated = (
        bool(settings.MYSQL_WRITE_USER)
        and bool(settings.MYSQL_READ_USER)
        and settings.MYSQL_WRITE_USER != settings.MYSQL_READ_USER
    )
    replica_status_configured = bool(
        settings.MYSQL_REPLICA_STATUS_USER
        and settings.MYSQL_REPLICA_STATUS_PASSWORD
    )
    healthy = (
        accounts_separated
        and replica_status_configured
        and read_has_select
        and not read_has_dangerous_global
    )
    return _check(
        "account_boundaries",
        "应用账号职责",
        "healthy" if healthy else "warning",
        "应用账号职责边界符合预期" if healthy else "应用账号职责边界需要复核",
        {
            "write_read_separated": accounts_separated,
            "replica_status_account_configured": replica_status_configured,
            "read_select_available": read_has_select,
            "read_has_dangerous_global_privilege": read_has_dangerous_global,
        },
    )


def _relationship_check() -> dict[str, Any]:
    """检查无外键保证关系和 active_session_key 语义。"""
    timeout = int(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)
    checks = {
        "job_event_without_job": """
            SELECT e.id AS sample_id
            FROM analysis_job_events AS e
            LEFT JOIN analysis_jobs AS j ON j.job_id = e.job_id
            WHERE j.job_id IS NULL
            LIMIT %s
        """,
        "checkpoint_cleanup_failed": """
            SELECT id AS sample_id
            FROM checkpoint_cleanup_outbox
            WHERE status = 'failed'
            ORDER BY id
            LIMIT %s
        """,
        "checkpoint_cleanup_expired_lease": """
            SELECT id AS sample_id
            FROM checkpoint_cleanup_outbox
            WHERE status = 'processing'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < UTC_TIMESTAMP(6)
            ORDER BY id
            LIMIT %s
        """,
        "user_memory_cleanup_failed": """
            SELECT id AS sample_id
            FROM user_memory_cleanup_outbox
            WHERE status = 'failed'
            ORDER BY id
            LIMIT %s
        """,
        "user_memory_cleanup_expired_lease": """
            SELECT id AS sample_id
            FROM user_memory_cleanup_outbox
            WHERE status = 'processing'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < UTC_TIMESTAMP(6)
            ORDER BY id
            LIMIT %s
        """,
        "archived_session_without_user": """
            SELECT a.id AS sample_id
            FROM archived_sessions AS a
            LEFT JOIN users AS u ON u.id = a.user_id
            WHERE u.id IS NULL
            LIMIT %s
        """,
        "archived_session_still_active": """
            SELECT a.id AS sample_id
            FROM archived_sessions AS a
            JOIN sessions AS s ON s.id = a.id
            LIMIT %s
        """,
        "invalid_active_session_key": """
            SELECT job_id AS sample_id
            FROM analysis_jobs
            WHERE (
                status IN ('queued', 'running', 'waiting_input')
                AND (
                    active_session_key IS NULL
                    OR active_session_key <> CONCAT(user_id, ':', session_id)
                )
            ) OR (
                status NOT IN ('queued', 'running', 'waiting_input')
                AND active_session_key IS NOT NULL
            )
            LIMIT %s
        """,
        "waiting_input_with_lease": """
            SELECT job_id AS sample_id
            FROM analysis_jobs
            WHERE status = 'waiting_input'
              AND (worker_id IS NOT NULL OR locked_at IS NOT NULL OR heartbeat_at IS NOT NULL)
            LIMIT %s
        """,
        "invalid_execution_state": """
            SELECT job_id AS sample_id
            FROM analysis_jobs
            WHERE (status = 'running' AND (execution_state IS NULL OR execution_state <> 'leased'))
               OR (status <> 'running' AND execution_state = 'leased')
               OR (execution_state = 'draining' AND status <> 'canceled')
               OR (execution_state IN ('leased', 'draining')
                   AND (worker_id IS NULL OR locked_at IS NULL OR heartbeat_at IS NULL))
               OR (execution_state IS NULL
                   AND (worker_id IS NOT NULL OR locked_at IS NOT NULL))
               OR (execution_state IS NOT NULL
                   AND (execution_released_at IS NOT NULL OR execution_release_reason IS NOT NULL))
               OR ((execution_released_at IS NULL) <> (execution_release_reason IS NULL))
            LIMIT %s
        """,
    }
    samples: dict[str, list[str]] = {}
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        for key, sql in checks.items():
            hinted = sql.replace(
                "SELECT",
                f"SELECT /*+ MAX_EXECUTION_TIME({timeout}) */",
                1,
            )
            cursor.execute(hinted, (ANOMALY_SAMPLE_LIMIT,))
            samples[key] = [str(row["sample_id"]) for row in cursor.fetchall()]
    anomaly_count = sum(len(items) for items in samples.values())
    return _check(
        "relationships",
        "业务关系与任务键",
        "healthy" if anomaly_count == 0 else "error",
        "未发现关系异常" if anomaly_count == 0 else "发现关系异常样本",
        {
            "sample_limit": ANOMALY_SAMPLE_LIMIT,
            "samples": samples,
            "sample_count": anomaly_count,
        },
    )


def _checkpoint_postgres_checks() -> list[dict[str, Any]]:
    """检查 PostgreSQL checkpoint schema、统计与 Job 关系样本。"""
    try:
        facts = collect_checkpoint_deep_facts(
            timeout_ms=int(settings.DB_INSPECTION_QUERY_TIMEOUT_MS),
            sample_limit=ANOMALY_SAMPLE_LIMIT,
        )
    except Exception:
        return [
            _check(
                "checkpoint_postgres_schema",
                "PostgreSQL checkpoint schema",
                "unknown",
                "检查失败或查询超时",
            ),
            _check(
                "checkpoint_postgres_stats",
                "PostgreSQL checkpoint 有界统计",
                "unknown",
                "检查失败或查询超时",
            ),
            _check(
                "checkpoint_job_relationships",
                "Checkpoint thread 与 analysis_jobs Job 关系样本",
                "unknown",
                "检查失败或查询超时",
            ),
        ]

    schema = facts["schema"]
    schema_healthy = (
        not schema["missing_columns"]
        and not schema["invalid_primary_keys"]
        and not schema["invalid_indexes"]
        and schema["migration_current"]
    )
    schema_check = _check(
        "checkpoint_postgres_schema",
        "PostgreSQL checkpoint schema",
        "healthy" if schema_healthy else "error",
        (
            "官方字段、主键和 migration 版本完整"
            if schema_healthy
            else "PostgreSQL checkpoint schema 与锁定版本不一致"
        ),
        schema,
    )

    stats = facts["stats"]
    reported_tables = {row["table_name"] for row in stats}
    stats_complete = reported_tables == set(CHECKPOINT_DATA_TABLES)
    stats_check = _check(
        "checkpoint_postgres_stats",
        "PostgreSQL checkpoint 有界统计",
        "healthy" if stats_complete else "warning",
        (
            "已读取三张 checkpoint 数据表的估算行数"
            if stats_complete
            else "部分 checkpoint 数据表没有统计信息"
        ),
        {
            "is_estimate": True,
            "tables": stats,
        },
    )

    thread_ids = facts["thread_ids"]
    existing_jobs: set[str] = set()
    cleanup_jobs: set[str] = set()
    try:
        if thread_ids:
            placeholders = ", ".join(["%s"] * len(thread_ids))
            with get_read_connection(consistency="strong") as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    f"""
                    SELECT /*+ MAX_EXECUTION_TIME({int(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)}) */
                           job_id
                    FROM analysis_jobs
                    WHERE job_id IN ({placeholders})
                    """,
                    tuple(thread_ids),
                )
                existing_jobs = {str(row["job_id"]) for row in cursor.fetchall()}
                cursor.execute(
                    f"""
                    SELECT /*+ MAX_EXECUTION_TIME({int(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)}) */
                           thread_id
                    FROM checkpoint_cleanup_outbox
                    WHERE thread_id IN ({placeholders})
                      AND status <> 'succeeded'
                    """,
                    tuple(thread_ids),
                )
                cleanup_jobs = {str(row["thread_id"]) for row in cursor.fetchall()}
    except Exception:
        return [
            schema_check,
            stats_check,
            _check(
                "checkpoint_job_relationships",
                "Checkpoint thread 与 analysis_jobs Job 关系样本",
                "unknown",
                "检查失败或查询超时",
            ),
        ]
    samples = [
        {
            "thread_id": thread_id,
            "job_exists": thread_id in existing_jobs,
            "cleanup_pending": thread_id in cleanup_jobs,
        }
        for thread_id in thread_ids
    ]
    missing_count = sum(
        1
        for item in samples
        if not item["job_exists"] and not item["cleanup_pending"]
    )
    relationship_check = _check(
        "checkpoint_job_relationships",
        "Checkpoint thread 与 analysis_jobs Job 关系样本",
        "healthy" if missing_count == 0 else "error",
        (
            "抽样 thread 均可关联 analysis_jobs Job 或处于 cleanup outbox 过渡"
            if missing_count == 0
            else "发现无法关联 analysis_jobs Job 且不在 cleanup outbox 中的 checkpoint thread 样本"
        ),
        {
            "sample_limit": ANOMALY_SAMPLE_LIMIT,
            "sample_count": len(samples),
            "missing_job_count": missing_count,
            "samples": samples,
        },
    )
    return [schema_check, stats_check, relationship_check]


def _replica_check() -> dict[str, Any]:
    """逐个读取配置从库状态并仅返回逻辑别名和健康结论。"""
    if not settings.MYSQL_READ_HOSTS:
        return _check(
            "replicas",
            "从库状态",
            "unknown",
            "未配置从库",
            {"replicas": []},
        )
    replicas = []
    for index, host in enumerate(settings.MYSQL_READ_HOSTS, start=1):
        row = get_replica_status(host)
        if not row:
            replicas.append({
                "source_alias": f"replica-{index}",
                "status": "unknown",
                "io_running": None,
                "sql_running": None,
                "lag_seconds": None,
                "has_io_error": None,
                "has_sql_error": None,
            })
            continue
        io_running = row.get("Replica_IO_Running") == "Yes"
        sql_running = row.get("Replica_SQL_Running") == "Yes"
        lag = row.get("Seconds_Behind_Source")
        replicas.append({
            "source_alias": f"replica-{index}",
            "status": "healthy" if io_running and sql_running and lag is not None else "warning",
            "io_running": io_running,
            "sql_running": sql_running,
            "lag_seconds": int(lag) if lag is not None else None,
            "has_io_error": bool(row.get("Last_IO_Error")),
            "has_sql_error": bool(row.get("Last_SQL_Error")),
        })
    statuses = {item["status"] for item in replicas}
    status = "healthy" if statuses == {"healthy"} else (
        "warning" if "warning" in statuses else "unknown"
    )
    return _check(
        "replicas",
        "从库状态",
        status,
        "全部从库复制状态正常" if status == "healthy" else "部分从库状态需要复核",
        {"replicas": replicas},
    )


def get_deep_audit_report() -> dict[str, Any]:
    """执行全部手动 deep 检查并返回可持久化脱敏报告。"""
    checks = [
        _safe_block("revision", "Alembic revision", _revision_check),
        _safe_block("schema", "关键 schema", _schema_check),
        _safe_block(
            "runtime_facts",
            "字符集、时区与隔离级别",
            _runtime_facts_check,
        ),
        _safe_block(
            "account_boundaries",
            "应用账号职责",
            _account_boundary_check,
        ),
        _safe_block(
            "relationships",
            "业务关系与任务键",
            _relationship_check,
        ),
        _safe_block("replicas", "从库状态", _replica_check),
    ]
    checks.extend(_checkpoint_postgres_checks())
    return {
        "status": _overall_status(checks),
        "observed_at": _observed_at(),
        "source_role": "monitor",
        "source_alias": "deep-audit-shared-snapshot",
        "is_estimate": False,
        "warning": None,
        "mode": "deep",
        "auto_scheduled": False,
        "sample_limit": ANOMALY_SAMPLE_LIMIT,
        "query_timeout_ms": int(settings.DB_INSPECTION_QUERY_TIMEOUT_MS),
        "checks": checks,
    }
