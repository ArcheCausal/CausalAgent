"""管理员数据库看板使用的统一只读检查服务。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

from Database.checkpoint_inspection import inspect_checkpoint_quick


MAX_SLOW_QUERY_LIMIT = 100


def _observed_at() -> str:
    """返回统一的 UTC ISO 8601 采集时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _result(
    status: str,
    value: Any,
    *,
    source_role: str,
    source_alias: str,
    is_estimate: bool = False,
    warning: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """构造带状态、来源、时间和估算标记的标准只读结果。"""
    return {
        "status": status,
        "value": value,
        "observed_at": observed_at or _observed_at(),
        "source_role": source_role,
        "source_alias": source_alias,
        "is_estimate": is_estimate,
        "warning": warning,
    }


def _failed_result(
    block_name: str,
    exc: Exception,
    *,
    source_role: str,
    source_alias: str,
    warning: str,
) -> dict[str, Any]:
    """向前端返回不含连接细节的失败结果，由快照边界统一记录事件。"""
    return _result(
        "unknown",
        None,
        source_role=source_role,
        source_alias=source_alias,
        warning=warning,
    )


def _timeout_hint(timeout_ms: int) -> str:
    """生成仅约束当前 SELECT 的 MySQL 最大执行时间 hint。"""
    return f"/*+ MAX_EXECUTION_TIME({int(timeout_ms)}) */"


def _int_or_none(value: Any) -> int | None:
    """把数据库数值安全转换为整数，同时保留 NULL。"""
    return int(value) if value is not None else None


def _show_values(cursor, statement: str, names: list[str]) -> dict[str, str]:
    """读取 SHOW VARIABLES/STATUS 结果并按变量名返回。"""
    placeholders = ", ".join(["%s"] * len(names))
    cursor.execute(f"{statement} WHERE Variable_name IN ({placeholders})", names)
    return {row["Variable_name"]: row["Value"] for row in cursor.fetchall()}


def inspect_revision() -> dict[str, Any]:
    """比较仓库 Alembic head 与主库实例 revision。"""
    repository_heads: list[str] | None = None
    instance_revisions: list[str] | None = None
    warnings: list[str] = []

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        project_root = Path(__file__).resolve().parents[1]
        config = Config(str(project_root / "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        repository_heads = sorted(script.get_heads())
    except Exception:
        warnings.append("无法读取仓库 Alembic head")

    try:
        from app.db import get_read_connection_with_source
        from config.settings import settings

        connection, _source = get_read_connection_with_source(consistency="strong")
        with connection as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                f"SELECT {_timeout_hint(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)} "
                "version_num FROM alembic_version ORDER BY version_num"
            )
            instance_revisions = sorted(row["version_num"] for row in cursor.fetchall())
    except Exception:
        warnings.append("无法读取实例 Alembic revision")

    matches = (
        repository_heads is not None
        and instance_revisions is not None
        and repository_heads == instance_revisions
    )
    if repository_heads is None or instance_revisions is None:
        status = "unknown"
    elif matches:
        status = "healthy"
    else:
        status = "error"
        warnings.append("仓库 head 与实例 revision 不一致")

    return _result(
        status,
        {
            "repository_heads": repository_heads,
            "instance_revisions": instance_revisions,
            "matches": matches,
        },
        source_role="mixed",
        source_alias="repository+primary",
        warning="；".join(warnings) or None,
    )


def inspect_primary() -> dict[str, Any]:
    """检查主库只读连接并读取实际 MySQL 版本。"""
    try:
        from app.db import get_read_connection_with_source
        from config.settings import settings

        connection, source = get_read_connection_with_source(consistency="strong")
        with connection as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                f"SELECT {_timeout_hint(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)} "
                "VERSION() AS version, UTC_TIMESTAMP(6) AS database_time"
            )
            row = cursor.fetchone() or {}
        return _result(
            "healthy",
            {
                "connected": True,
                "version": row.get("version"),
                "database_time": row.get("database_time"),
            },
            **source,
        )
    except Exception as exc:
        return _failed_result(
            "primary",
            exc,
            source_role="primary",
            source_alias="primary",
            warning="主库只读连接或版本查询失败",
        )


def inspect_replica() -> dict[str, Any]:
    """读取第一从库复制线程、延迟与最近错误。"""
    try:
        from app.db import get_replica_status
        from config.settings import settings

        if not settings.MYSQL_READ_HOSTS:
            return _result(
                "unknown",
                {"configured": False, "available": False},
                source_role="replica",
                source_alias="replica-1",
                warning="未配置从库",
            )

        row = get_replica_status(settings.MYSQL_READ_HOSTS[0])
        if not row:
            return _result(
                "unknown",
                {"configured": True, "available": False},
                source_role="replica",
                source_alias="replica-1",
                warning="无法读取第一从库复制状态",
            )

        io_running = row.get("Replica_IO_Running")
        sql_running = row.get("Replica_SQL_Running")
        lag_seconds = _int_or_none(row.get("Seconds_Behind_Source"))
        raw_io_error = row.get("Last_IO_Error") or None
        raw_sql_error = row.get("Last_SQL_Error") or None
        last_io_error_code = _int_or_none(row.get("Last_IO_Errno"))
        last_sql_error_code = _int_or_none(row.get("Last_SQL_Errno"))
        last_io_error = (
            f"复制 I/O 错误（错误号 {last_io_error_code or '未知'}）"
            if raw_io_error
            else None
        )
        last_sql_error = (
            f"复制 SQL 错误（错误号 {last_sql_error_code or '未知'}）"
            if raw_sql_error
            else None
        )
        warning = None
        if io_running != "Yes" or sql_running != "Yes" or last_io_error or last_sql_error:
            status = "error"
            warning = "从库复制线程异常或存在复制错误"
        elif lag_seconds is None:
            status = "warning"
            warning = "从库复制延迟未知"
        elif lag_seconds > settings.MYSQL_REPLICA_MAX_LAG_SECONDS:
            status = "warning"
            warning = "从库复制延迟超过应用读取阈值"
        else:
            status = "healthy"

        return _result(
            status,
            {
                "configured": True,
                "available": True,
                "io_running": io_running,
                "sql_running": sql_running,
                "lag_seconds": lag_seconds,
                "last_io_error": last_io_error,
                "last_io_error_code": last_io_error_code,
                "last_sql_error": last_sql_error,
                "last_sql_error_code": last_sql_error_code,
            },
            source_role="replica",
            source_alias="replica-1",
            warning=warning,
        )
    except Exception as exc:
        return _failed_result(
            "replica",
            exc,
            source_role="replica",
            source_alias="replica-1",
            warning="读取第一从库复制状态失败",
        )


def inspect_connections() -> dict[str, Any]:
    """读取主库连接指标并按可配置阈值计算状态。"""
    try:
        from app.db import get_read_connection_with_source
        from config.settings import settings

        connection, source = get_read_connection_with_source(consistency="strong")
        with connection as conn:
            cursor = conn.cursor(dictionary=True)
            values = _show_values(
                cursor,
                "SHOW GLOBAL STATUS",
                ["Threads_connected", "Threads_running", "Max_used_connections"],
            )
            variables = _show_values(cursor, "SHOW GLOBAL VARIABLES", ["max_connections"])

        connected = _int_or_none(values.get("Threads_connected"))
        running = _int_or_none(values.get("Threads_running"))
        max_used = _int_or_none(values.get("Max_used_connections"))
        maximum = _int_or_none(variables.get("max_connections"))
        utilization = round((connected / maximum) * 100, 2) if connected is not None and maximum else None

        warning = None
        if utilization is None:
            status = "unknown"
            warning = "无法计算连接使用率"
        elif utilization >= settings.DB_DASHBOARD_CONNECTION_CRITICAL_PERCENT:
            status = "error"
            warning = "连接使用率达到严重阈值"
        elif utilization >= settings.DB_DASHBOARD_CONNECTION_WARNING_PERCENT:
            status = "warning"
            warning = "连接使用率达到警告阈值"
        else:
            status = "healthy"

        return _result(
            status,
            {
                "threads_connected": connected,
                "threads_running": running,
                "max_used_connections": max_used,
                "max_connections": maximum,
                "utilization_percent": utilization,
                "warning_threshold_percent": settings.DB_DASHBOARD_CONNECTION_WARNING_PERCENT,
                "critical_threshold_percent": settings.DB_DASHBOARD_CONNECTION_CRITICAL_PERCENT,
            },
            **source,
            warning=warning,
        )
    except Exception as exc:
        return _failed_result(
            "connections",
            exc,
            source_role="primary",
            source_alias="primary",
            warning="读取主库连接指标失败",
        )


def inspect_table_capacity() -> dict[str, Any]:
    """从可延迟读取节点获取表容量估算，并明确实际来源。"""
    try:
        from app.db import get_read_connection_with_source
        from config.settings import settings

        connection, source = get_read_connection_with_source(consistency="eventual")
        with connection as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                f"""
                SELECT {_timeout_hint(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)}
                    table_name AS table_name,
                    table_rows AS table_rows,
                    data_length AS data_length,
                    index_length AS index_length,
                    data_length + index_length AS total_length
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                ORDER BY total_length DESC
                LIMIT 200
                """
            )
            rows = cursor.fetchall()

        tables = [
            {
                "table_name": row["table_name"],
                "table_rows": _int_or_none(row.get("table_rows")),
                "data_length": _int_or_none(row.get("data_length")) or 0,
                "index_length": _int_or_none(row.get("index_length")) or 0,
                "total_length": _int_or_none(row.get("total_length")) or 0,
            }
            for row in rows
        ]
        return _result(
            "healthy",
            tables,
            **source,
            is_estimate=True,
            warning="InnoDB 行数为估算值",
        )
    except Exception as exc:
        return _failed_result(
            "tables",
            exc,
            source_role="database",
            source_alias="selected-read-node",
            warning="读取表容量失败",
        )


def _blocking_issues(blocks: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """从核心区块中提取会阻止健康判定的问题。"""
    labels = {
        "revision": "数据库版本不一致",
        "primary": "主库不可用",
        "replica": "从库复制异常",
        "connections": "主库连接使用率严重",
    }
    return [
        {"key": key, "label": labels[key], "message": block.get("warning") or labels[key]}
        for key, block in blocks.items()
        if block.get("status") == "error"
    ]


def _overall_status(results: list[dict[str, Any]]) -> str:
    """按 error、warning/unknown、healthy 的优先级汇总状态。"""
    statuses = {item.get("status") for item in results}
    if "error" in statuses:
        return "error"
    if statuses.intersection({"warning", "unknown"}):
        return "warning"
    return "healthy"


def get_database_overview() -> dict[str, Any]:
    """汇总 revision、节点、连接和表容量，单项失败不拖垮其他区块。"""
    blocks = {
        "revision": inspect_revision(),
        "primary": inspect_primary(),
        "replica": inspect_replica(),
        "connections": inspect_connections(),
        "tables": inspect_table_capacity(),
    }
    core_blocks = {key: value for key, value in blocks.items() if key != "tables"}
    return {
        "status": _overall_status(list(blocks.values())),
        "observed_at": _observed_at(),
        **blocks,
        "blocking_issues": _blocking_issues(core_blocks),
    }


def get_realtime_report() -> dict[str, Any]:
    """采集高频主从、连接和 Worker/Job 状态，不包含容量与完整性扫描。"""
    from app.agent.job_service import get_worker_snapshot_report

    blocks = {
        "primary": inspect_primary(),
        "replica": inspect_replica(),
        "connections": inspect_connections(),
    }
    jobs = get_worker_snapshot_report()
    return {
        "status": _overall_status([*blocks.values(), jobs]),
        "observed_at": _observed_at(),
        **blocks,
        "jobs": jobs,
        "blocking_issues": _blocking_issues(blocks),
    }


def get_capacity_report() -> dict[str, Any]:
    """采集低频 schema revision 与表容量信息。"""
    revision = inspect_revision()
    tables = inspect_table_capacity()
    blocks = {"revision": revision, "tables": tables}
    return {
        "status": _overall_status(list(blocks.values())),
        "observed_at": _observed_at(),
        **blocks,
        "blocking_issues": _blocking_issues({"revision": revision}),
    }


def _previous_sql_value(previous: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """兼容标准检查结果和已展平快照，提取上一轮 SQL 指标。"""
    if not previous:
        return {}, None
    value = previous.get("value") if isinstance(previous.get("value"), dict) else previous
    return value, previous.get("source_alias")


def _server_instance_id(cursor) -> str:
    """读取并散列 MySQL server UUID，作为不暴露主机名的累计指标来源标识。"""
    cursor.execute("SELECT @@GLOBAL.server_uuid AS server_uuid")
    row = cursor.fetchone() or {}
    raw_uuid = row.get("server_uuid") or row.get("SERVER_UUID")
    if not raw_uuid:
        raise RuntimeError("MySQL 未返回 server_uuid")
    return hashlib.sha256(str(raw_uuid).encode("utf-8")).hexdigest()[:24]


def _window_seconds(started_at: str | None, ended_at: str) -> float | None:
    """计算两个 UTC ISO 时间之间的实际采集窗口秒数。"""
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return max(0.0, round((end - start).total_seconds(), 3))


def inspect_slow_queries(
    limit: int = 20,
    previous: dict[str, Any] | None = None,
    warning_threshold: int | None = None,
) -> dict[str, Any]:
    """读取主库慢查询累计状态，并按动态阈值计算同源周期增量。"""
    normalized_limit = max(1, min(int(limit), MAX_SLOW_QUERY_LIMIT))
    try:
        from app.db import get_read_connection_with_source
        from config.settings import settings

        connection, source = get_read_connection_with_source(consistency="strong")
        with connection as conn:
            cursor = conn.cursor(dictionary=True)
            source_instance_id = _server_instance_id(cursor)
            statuses = _show_values(cursor, "SHOW GLOBAL STATUS", ["Slow_queries", "Uptime"])
            variables = _show_values(
                cursor,
                "SHOW GLOBAL VARIABLES",
                ["slow_query_log", "long_query_time"],
            )
            digest_warning = None
            try:
                cursor.execute(
                    f"""
                    SELECT {_timeout_hint(settings.DB_INSPECTION_QUERY_TIMEOUT_MS)}
                        DIGEST_TEXT AS digest_text,
                        COUNT_STAR AS count_star,
                        ROUND(SUM_TIMER_WAIT / 1000000000000, 6) AS total_seconds,
                        ROUND(AVG_TIMER_WAIT / 1000000000000, 6) AS avg_seconds,
                        SUM_ROWS_EXAMINED AS rows_examined,
                        SUM_ROWS_SENT AS rows_sent
                    FROM performance_schema.events_statements_summary_by_digest
                    WHERE SCHEMA_NAME = DATABASE()
                      AND DIGEST_TEXT IS NOT NULL
                    ORDER BY AVG_TIMER_WAIT DESC, SUM_TIMER_WAIT DESC
                    LIMIT %s
                    """,
                    (normalized_limit,),
                )
                high_load_statements = cursor.fetchall()
            except Exception:
                high_load_statements = []
                digest_warning = "performance_schema 摘要不可用（权限不足、未启用或查询超时）"

        observed_at = _observed_at()
        slow_query_log = variables.get("slow_query_log")
        slow_queries_total = _int_or_none(statuses.get("Slow_queries"))
        uptime_seconds = _int_or_none(statuses.get("Uptime"))
        previous_value, previous_source = _previous_sql_value(previous)
        previous_total = _int_or_none(
            previous_value.get("slow_queries_total", previous_value.get("Slow_queries"))
        )
        previous_uptime = _int_or_none(previous_value.get("uptime_seconds"))
        previous_instance_id = previous_value.get("source_instance_id")
        window_started_at = previous_value.get("window_ended_at") or (
            previous.get("observed_at") if previous else None
        )
        source_changed = previous_total is not None and (
            previous_source != source["source_alias"]
            or previous_instance_id != source_instance_id
        )
        counter_reset = (
            previous_total is not None
            and slow_queries_total is not None
            and slow_queries_total < previous_total
        ) or (
            previous_uptime is not None
            and uptime_seconds is not None
            and uptime_seconds < previous_uptime
        )
        baseline_reset = (
            previous_total is None
            or slow_queries_total is None
            or not window_started_at
            or source_changed
            or counter_reset
        )
        slow_queries_delta = (
            None
            if baseline_reset or slow_queries_total is None
            else slow_queries_total - previous_total
        )
        threshold = (
            settings.DB_MONITOR_SLOW_QUERY_WARNING_DELTA
            if warning_threshold is None
            else warning_threshold
        )
        warnings = [message for message in [digest_warning] if message]
        if str(slow_query_log).upper() not in {"ON", "1"}:
            warnings.append("当前节点未开启 slow_query_log")
        if baseline_reset:
            warnings.append("慢查询周期增量正在建立或重置采集基线")
        elif slow_queries_delta is not None and slow_queries_delta >= threshold:
            warnings.append("采集窗口内慢查询增量达到告警阈值")
        return _result(
            "warning" if warnings else "healthy",
            {
                "Slow_queries": slow_queries_total,
                "slow_queries_total": slow_queries_total,
                "slow_queries_delta": slow_queries_delta,
                "window_started_at": None if baseline_reset else window_started_at,
                "window_ended_at": observed_at,
                "window_seconds": None if baseline_reset else _window_seconds(window_started_at, observed_at),
                "baseline_reset": baseline_reset,
                "uptime_seconds": uptime_seconds,
                "source_instance_id": source_instance_id,
                "slow_query_warning_threshold": threshold,
                "slow_query_log": slow_query_log,
                "long_query_time": float(variables["long_query_time"])
                if variables.get("long_query_time") is not None
                else None,
                "high_load_statements": high_load_statements,
                "top_statements": high_load_statements,
                "limit": normalized_limit,
            },
            **source,
            warning="；".join(warnings) or None,
            observed_at=observed_at,
        )
    except Exception as exc:
        return _failed_result(
            "slow-queries",
            exc,
            source_role="primary",
            source_alias="primary",
            warning="读取主库慢查询配置和 SQL 性能摘要失败",
        )


EXPECTED_FOREIGN_KEYS = (
    ("chat_messages", "fk_chat_messages_session", (("session_id", "sessions", "id"),)),
    ("chat_messages", "fk_chat_messages_user", (("user_id", "users", "id"),)),
    ("chat_attachments", "fk_chat_attachments_message", (("message_id", "chat_messages", "id"),)),
    ("analysis_jobs", "fk_analysis_jobs_user", (("user_id", "users", "id"),)),
    ("analysis_jobs", "fk_analysis_jobs_session", (("session_id", "sessions", "id"),)),
    ("analysis_jobs", "fk_analysis_jobs_input_user_file", (("input_user_file_id", "user_files", "id"),)),
    ("analysis_jobs", "fk_analysis_jobs_input_object", (("input_object_id", "file_objects", "id"),)),
    ("analysis_job_events", "fk_analysis_job_events_job", (("job_id", "analysis_jobs", "job_id"),)),
    ("analysis_job_inputs", "fk_analysis_job_inputs_job", (("job_id", "analysis_jobs", "job_id"),)),
    ("file_objects", "fk_file_objects_owner", (("owner_user_id", "users", "id"),)),
    ("user_files", "fk_user_files_user", (("user_id", "users", "id"),)),
    ("user_files", "fk_user_files_object", (("object_id", "file_objects", "id"),)),
    (
        "checkpoint_cleanup_outbox",
        "fk_checkpoint_cleanup_outbox_operation",
        (("operation_id", "admin_operations", "operation_id"),),
    ),
    (
        "user_memory_cleanup_outbox",
        "fk_user_memory_cleanup_outbox_operation",
        (("operation_id", "admin_operations", "operation_id"),),
    ),
)

EXPECTED_UNIQUE_INDEXES = (
    (
        "analysis_jobs",
        "uq_analysis_jobs_user_idempotency",
        ("user_id", "idempotency_key"),
    ),
    (
        "analysis_jobs",
        "uq_analysis_jobs_cancel_idempotency",
        ("job_id", "cancel_idempotency_key"),
    ),
    (
        "user_files",
        "uq_user_files_name_object",
        ("user_id", "object_id", "filename"),
    ),
    (
        "analysis_job_inputs",
        "uq_analysis_job_inputs_sequence",
        ("job_id", "sequence"),
    ),
    (
        "analysis_job_inputs",
        "uq_analysis_job_inputs_idempotency",
        ("job_id", "idempotency_key"),
    ),
    (
        "analysis_job_events",
        "uq_analysis_job_events_event_key",
        ("job_id", "event_key"),
    ),
    (
        "chat_messages",
        "uq_chat_messages_source_event",
        ("source_event_id",),
    ),
)


def _foreign_key_description(
    table_name: str,
    constraint_name: str,
    columns: tuple[tuple[str, str, str], ...],
) -> str:
    """生成外键检查目的，明确关联字段和约束列顺序。"""
    relationships = ", ".join(
        f"{table_name}.{child_column} → {parent_table}.{parent_column}"
        for child_column, parent_table, parent_column in columns
    )
    return (
        f"确认 {relationships} 通过外键 {constraint_name} 关联，"
        "且约束定义和列顺序与预期一致。"
    )


def _unique_index_description(
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
) -> str:
    """生成唯一索引检查目的，明确唯一性和列顺序要求。"""
    column_names = ", ".join(f"{table_name}.{column}" for column in columns)
    return (
        f"确认唯一索引 {index_name} 覆盖 {column_names}，"
        "且唯一性和列顺序与预期一致。"
    )


def _foreign_key_definition_sql(
    timeout_ms: int,
    table_name: str,
    constraint_name: str,
    columns: tuple[tuple[str, str, str], ...],
) -> str:
    """生成精确核对子列、父表、父列及复合列顺序的轻量元数据查询。"""
    expected_rows = " OR ".join(
        "("
        f"ordinal_position = {position} "
        f"AND column_name = '{child_column}' "
        f"AND referenced_table_name = '{parent_table}' "
        f"AND referenced_column_name = '{parent_column}'"
        ")"
        for position, (child_column, parent_table, parent_column) in enumerate(columns, start=1)
    )
    return f"""SELECT {_timeout_hint(timeout_ms)} CASE
        WHEN COUNT(*) = {len(columns)}
         AND SUM(CASE WHEN {expected_rows} THEN 1 ELSE 0 END) = {len(columns)}
        THEN 1 ELSE 0 END AS count_value
        FROM information_schema.key_column_usage
        WHERE constraint_schema = DATABASE()
          AND table_name = '{table_name}'
          AND constraint_name = '{constraint_name}'
          AND referenced_table_name IS NOT NULL"""


def _unique_index_definition_sql(
    timeout_ms: int,
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
) -> str:
    """生成精确核对唯一索引列顺序的轻量元数据查询。"""
    expected_rows = " OR ".join(
        f"(seq_in_index = {position} AND column_name = '{column}')"
        for position, column in enumerate(columns, start=1)
    )
    return f"""SELECT {_timeout_hint(timeout_ms)} CASE
        WHEN COUNT(*) = {len(columns)}
         AND MIN(non_unique) = 0
         AND SUM(CASE WHEN {expected_rows} THEN 1 ELSE 0 END) = {len(columns)}
        THEN 1 ELSE 0 END AS count_value
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = '{table_name}'
          AND index_name = '{index_name}'"""


def _operational_integrity_definitions(timeout_ms: int) -> list[dict[str, Any]]:
    """定义运行期低频审计，避免重复扫描已由外键保证的关系。"""
    hint = _timeout_hint(timeout_ms)
    definitions = [
        {
            "key": f"constraint_{constraint_name}",
            "label": f"约束 {constraint_name}",
            "severity": "blocking",
            "healthy_when": "one",
            "description": _foreign_key_description(
                table_name,
                constraint_name,
                columns,
            ),
            "failure_warning": f"外键 {constraint_name} 不存在或定义与预期不一致",
            "sql": _foreign_key_definition_sql(
                timeout_ms,
                table_name,
                constraint_name,
                columns,
            ),
        }
        for table_name, constraint_name, columns in EXPECTED_FOREIGN_KEYS
    ]
    definitions.extend([
        {
            "key": f"constraint_{index_name}",
            "label": f"唯一约束 {index_name}",
            "severity": "blocking",
            "healthy_when": "one",
            "description": _unique_index_description(
                table_name,
                index_name,
                columns,
            ),
            "failure_warning": f"唯一索引 {index_name} 不存在或定义与预期不一致",
            "sql": _unique_index_definition_sql(
                timeout_ms,
                table_name,
                index_name,
                columns,
            ),
        }
        for table_name, index_name, columns in EXPECTED_UNIQUE_INDEXES
    ])
    definitions.extend([
        {
            "key": "constraint_chat_attachment_type_enum",
            "label": "约束 chat_attachments.attachment_type ENUM",
            "severity": "blocking",
            "healthy_when": "one",
            "description": (
                "确认 chat_attachments.attachment_type 为 ENUM，"
                "且包含 visualization、web_search_references 与 report_document 类型。"
            ),
            "failure_warning": (
                "chat_attachments.attachment_type 不是包含 visualization、web_search_references 与 report_document 的 ENUM"
            ),
            "sql": f"""SELECT {hint} COUNT(*) AS count_value
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'chat_attachments'
                  AND column_name = 'attachment_type'
                  AND data_type = 'enum'
                  AND column_type LIKE '%''visualization''%'
                  AND column_type LIKE '%''web_search_references''%'
                  AND column_type LIKE '%''report_document''%'""",
        },
        {
            "key": "constraint_checkpoint_cleanup_outbox_claim",
            "label": "约束 checkpoint cleanup outbox 领取索引",
            "severity": "blocking",
            "healthy_when": "one",
            "description": (
                "确认 checkpoint_cleanup_outbox 存在"
                " idx_checkpoint_cleanup_outbox_claim 领取索引。"
            ),
            "failure_warning": "checkpoint cleanup outbox 领取索引缺失",
            "sql": f"""SELECT {hint} CASE WHEN COUNT(DISTINCT index_name) = 1
                    THEN 1 ELSE 0 END AS count_value
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'checkpoint_cleanup_outbox'
                  AND index_name = 'idx_checkpoint_cleanup_outbox_claim'""",
        },
        {
            "key": "checkpoint_cleanup_failed",
            "label": "失败的 PostgreSQL checkpoint 清理任务",
            "severity": "warning",
            "healthy_when": "zero",
            "description": (
                "统计 checkpoint_cleanup_outbox 中 status=failed 的 PostgreSQL "
                "父子图 checkpoint 清理任务；数量为 0 时健康。"
            ),
            "failure_warning": "存在失败的 PostgreSQL checkpoint 清理任务",
            "sql": f"""SELECT {hint} COUNT(*) AS count_value
                FROM checkpoint_cleanup_outbox
                WHERE status = 'failed'""",
        },
        {
            "key": "constraint_user_memory_cleanup_outbox_claim",
            "label": "约束用户长期记忆清理 outbox 领取索引",
            "severity": "blocking",
            "healthy_when": "one",
            "description": (
                "确认 user_memory_cleanup_outbox 存在"
                " idx_user_memory_cleanup_outbox_claim 领取索引。"
            ),
            "failure_warning": "用户长期记忆清理 outbox 领取索引缺失",
            "sql": f"""SELECT {hint} CASE WHEN COUNT(DISTINCT index_name) = 1
                    THEN 1 ELSE 0 END AS count_value
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'user_memory_cleanup_outbox'
                  AND index_name = 'idx_user_memory_cleanup_outbox_claim'""",
        },
        {
            "key": "user_memory_cleanup_failed",
            "label": "失败的用户长期记忆清理任务",
            "severity": "warning",
            "healthy_when": "zero",
            "description": (
                "统计 user_memory_cleanup_outbox 中 status=failed 的 Store "
                "长期记忆清理任务；数量为 0 时健康。"
            ),
            "failure_warning": "存在失败的用户长期记忆清理任务",
            "sql": f"""SELECT {hint} COUNT(*) AS count_value
                FROM user_memory_cleanup_outbox
                WHERE status = 'failed'""",
        },
    ])
    return definitions


def _integrity_definitions(timeout_ms: int) -> list[dict[str, Any]]:
    """兼容入口：返回运行期完整性审计定义。"""
    return _operational_integrity_definitions(timeout_ms)


def _is_integrity_count_healthy(count: int, condition: str) -> bool:
    """按定义口径判断完整性计数是否健康。"""
    if condition == "zero":
        return count == 0
    if condition == "one":
        return count == 1
    if condition == "positive":
        return count > 0
    raise ValueError(f"未知完整性条件: {condition}")


def _execute_integrity_definitions(
    connection,
    definitions: list[dict[str, Any]],
    *,
    source_role: str,
    source_alias: str,
) -> list[dict[str, Any]]:
    """逐项执行给定审计定义，并保留单项失败降级。"""
    cursor = connection.cursor(dictionary=True)
    checks: list[dict[str, Any]] = []
    for definition in definitions:
        observed_at = _observed_at()
        try:
            cursor.execute(definition["sql"])
            row = cursor.fetchone() or {}
            count = int(row.get("count_value") or 0)
            healthy = _is_integrity_count_healthy(
                count,
                definition.get("healthy_when", "zero"),
            )
            checks.append({
                "key": definition["key"],
                "label": definition["label"],
                "severity": definition["severity"],
                "applicable": True,
                "description": definition.get("description", definition["label"]),
                **_result(
                    "healthy" if healthy else "error",
                    count,
                    source_role=source_role,
                    source_alias=source_alias,
                    warning=(
                        None
                        if healthy
                        else definition.get("failure_warning", "完整性检查未通过")
                    ),
                    observed_at=observed_at,
                ),
            })
        except Exception:
            checks.append({
                "key": definition["key"],
                "label": definition["label"],
                "severity": definition["severity"],
                "applicable": True,
                "description": definition.get("description", definition["label"]),
                **_result(
                    "unknown",
                    None,
                    source_role=source_role,
                    source_alias=source_alias,
                    warning="检查失败（权限不足、查询超时或节点不可用）",
                    observed_at=observed_at,
                ),
            })
    return checks


def execute_quick_integrity_checks(
    connection,
    *,
    timeout_ms: int,
    source_role: str = "primary",
    source_alias: str = "primary",
) -> list[dict[str, Any]]:
    """在调用方提供的主库连接上执行运行期低频完整性审计。"""
    return _execute_integrity_definitions(
        connection,
        _operational_integrity_definitions(timeout_ms),
        source_role=source_role,
        source_alias=source_alias,
    )


def _not_applicable_check(key: str, label: str, reason: str) -> dict[str, Any]:
    """构造不会阻塞迁移的“不适用”预检结果。"""
    return {
        "key": key,
        "label": label,
        "severity": "blocking",
        "applicable": False,
        **_result(
            "healthy",
            None,
            source_role="primary",
            source_alias="primary",
            warning=reason,
        ),
    }


FK_MIGRATION_REVISION = "f6b8c9d0e1a2"


def _current_schema_revision(cursor, tables: set[str]) -> str | None:
    """在版本表存在时读取当前 Alembic revision；未纳管旧库返回 None。"""
    if "alembic_version" not in tables:
        return None
    cursor.execute("SELECT version_num FROM alembic_version")
    row = cursor.fetchone() or {}
    return row.get("version_num") or row.get("VERSION_NUM")


def _revision_contains_migration(current_revision: str | None, target_revision: str) -> bool:
    """通过 Alembic revision graph 判断当前版本是否已经包含目标迁移。"""
    if not current_revision:
        return False
    if current_revision == target_revision:
        return True
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.script.revision import RangeNotAncestorError
    except Exception:
        return False

    try:
        project_root = Path(__file__).resolve().parents[1]
        config = Config(str(project_root / "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        list(script.iterate_revisions(current_revision, target_revision))
        return True
    except RangeNotAncestorError:
        return False
    except Exception:
        return False


def execute_migration_preflight_checks(
    connection,
    *,
    timeout_ms: int,
    source_role: str = "primary",
    source_alias: str = "primary",
) -> list[dict[str, Any]]:
    """按当前 schema 仅运行待添加外键/唯一约束所需的数据扫描。"""
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
    )
    tables = {row.get("table_name") or row.get("TABLE_NAME") for row in cursor.fetchall()}
    current_revision = _current_schema_revision(cursor, tables)
    fk_migration_pending = not _revision_contains_migration(
        current_revision,
        FK_MIGRATION_REVISION,
    )
    cursor.execute("""
        SELECT table_name, constraint_name
        FROM information_schema.referential_constraints
        WHERE constraint_schema = DATABASE()
    """)
    foreign_keys = {
        (
            row.get("table_name") or row.get("TABLE_NAME"),
            row.get("constraint_name") or row.get("CONSTRAINT_NAME"),
        )
        for row in cursor.fetchall()
    }
    hint = _timeout_hint(timeout_ms)
    candidates = [
        {
            "key": "orphan_chat_messages_session",
            "label": "迁移前孤立 chat_messages.session_id",
            "child": "chat_messages",
            "parent": "sessions",
            "constraint": "fk_chat_messages_session",
            "sql": f"""SELECT {hint} COUNT(*) AS count_value FROM chat_messages cm
                LEFT JOIN sessions s ON s.id = cm.session_id WHERE s.id IS NULL""",
        },
        {
            "key": "orphan_chat_messages_user",
            "label": "迁移前孤立 chat_messages.user_id",
            "child": "chat_messages",
            "parent": "users",
            "constraint": "fk_chat_messages_user",
            "sql": f"""SELECT {hint} COUNT(*) AS count_value FROM chat_messages cm
                LEFT JOIN users u ON u.id = cm.user_id WHERE u.id IS NULL""",
        },
        {
            "key": "orphan_chat_attachments_message",
            "label": "迁移前孤立 chat_attachments.message_id",
            "child": "chat_attachments",
            "parent": "chat_messages",
            "constraint": "fk_chat_attachments_message",
            "sql": f"""SELECT {hint} COUNT(*) AS count_value FROM chat_attachments ca
                LEFT JOIN chat_messages cm ON cm.id = ca.message_id WHERE cm.id IS NULL""",
        },
    ]
    checks: list[dict[str, Any]] = []
    definitions: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (candidate["child"], candidate["constraint"])
        if not fk_migration_pending:
            checks.append(_not_applicable_check(
                candidate["key"],
                candidate["label"],
                f"当前 revision {current_revision} 已包含目标外键迁移，跳过迁移前数据扫描",
            ))
        elif candidate["child"] not in tables or candidate["parent"] not in tables:
            checks.append(_not_applicable_check(
                candidate["key"], candidate["label"], "相关表尚未创建，跳过迁移前扫描"
            ))
        elif key in foreign_keys:
            checks.append(_not_applicable_check(
                candidate["key"], candidate["label"], "目标外键已经存在，跳过重复数据扫描"
            ))
        else:
            definitions.append({
                **candidate,
                "severity": "blocking",
                "healthy_when": "zero",
            })
    checks.extend(_execute_integrity_definitions(
        connection,
        definitions,
        source_role=source_role,
        source_alias=source_alias,
    ))

    chat_fk_missing = (
        fk_migration_pending
        and "chat_messages" in tables
        and ("chat_messages", "fk_chat_messages_session") not in foreign_keys
    )
    if chat_fk_missing:
        checks.extend(_execute_integrity_definitions(
            connection,
            [{
                "key": "chat_messages_preflight_partitions",
                "label": "迁移前 chat_messages 分区结构",
                "severity": "blocking",
                "healthy_when": "positive",
                "sql": f"""SELECT {hint} COUNT(*) AS count_value
                    FROM information_schema.partitions
                    WHERE table_schema = DATABASE()
                      AND table_name = 'chat_messages'
                      AND partition_name IS NOT NULL""",
            }],
            source_role=source_role,
            source_alias=source_alias,
        ))
    else:
        checks.append(_not_applicable_check(
            "chat_messages_preflight_partitions",
            "迁移前 chat_messages 分区结构",
            "生产升级外键已存在或表尚未创建，无需检查旧分区结构",
        ))

    return checks


def get_quick_integrity_report() -> dict[str, Any]:
    """执行 MySQL 运行期审计与 PostgreSQL checkpoint 快速检查。"""
    from app.db import get_read_connection_with_source
    from config.settings import settings

    try:
        connection, source = get_read_connection_with_source(consistency="strong")
        with connection as conn:
            checks = execute_quick_integrity_checks(
                conn,
                timeout_ms=settings.DB_INSPECTION_QUERY_TIMEOUT_MS,
                **source,
            )
    except Exception:
        checks = [{
            "key": "integrity_connection",
            "label": "快速完整性检查连接",
            "severity": "blocking",
            "description": "确认 Quick 审计可以使用只读账号连接 MySQL 主库。",
            **_result(
                "unknown",
                None,
                source_role="primary",
                source_alias="primary",
                warning="无法使用只读账号连接主库",
            ),
        }]

    checks.extend(inspect_checkpoint_quick(
        timeout_ms=settings.DB_INSPECTION_QUERY_TIMEOUT_MS,
    ))

    blocking_count = sum(
        1
        for check in checks
        if check["severity"] == "blocking" and check["status"] == "error"
    )
    blocking_record_count = sum(
        int(check["value"] or 0)
        for check in checks
        if check["severity"] == "blocking" and check["status"] == "error"
    )
    return {
        "mode": "operational",
        "status": _overall_status(checks),
        "observed_at": _observed_at(),
        "blocking_count": blocking_count,
        "blocking_record_count": blocking_record_count,
        "checks": checks,
    }
