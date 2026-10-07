"""数据库看板共享快照仓储、分层采集和旧接口兼容层。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import math
import os
import time
from typing import Any, Iterable

from Database.inspection import (
    get_capacity_report,
    get_quick_integrity_report,
    get_realtime_report,
    inspect_slow_queries,
)
from Database.monitor_settings import get_monitor_settings
from app.agent.persistence_cleanup import (
    CHECKPOINT_TASK,
    MAX_CLEANUP_ATTEMPTS,
    USER_MEMORY_TASK,
)
from app.db import get_read_connection_with_source, get_write_connection
from observability.logging_runtime import log_event
from observability.noise_control import FailureTransitionTracker


LOGGER = logging.getLogger(__name__)
DASHBOARD_SNAPSHOT_KEYS = ("realtime", "sql_performance", "capacity", "integrity")
CLEANUP_RUNTIME_SNAPSHOT_KEY = "agent_persistence_cleanup_runtime"
CLEANUP_OUTBOX_SNAPSHOT_KEY = "agent_persistence_cleanup_outbox"
CLEANUP_SNAPSHOT_KEYS = (CLEANUP_RUNTIME_SNAPSHOT_KEY, CLEANUP_OUTBOX_SNAPSHOT_KEY)
SNAPSHOT_KEYS = (*DASHBOARD_SNAPSHOT_KEYS, "deep_audit", CLEANUP_OUTBOX_SNAPSHOT_KEY)
CLEANUP_OUTBOX_TABLES = {
    CHECKPOINT_TASK: ("checkpoint_cleanup_outbox", "thread_id"),
    USER_MEMORY_TASK: ("user_memory_cleanup_outbox", "user_id"),
}
CLEANUP_OUTBOX_ITEM_LIMIT = 100
DEFAULT_REFRESH_GROUPS = ("realtime", "sql_performance", "capacity")
SLOW_QUERY_LIMIT = 20
_SNAPSHOT_FAILURES = FailureTransitionTracker()
_LOCK_FAILURES = FailureTransitionTracker()


class SnapshotCollectionDegraded(RuntimeError):
    """表示采集结果中存在无法取得的子项，不携带原始异常。"""


def _safe_exc_info(exc: BaseException):
    return type(exc), exc, exc.__traceback__


def _record_snapshot_failure(
    snapshot_key: str,
    exc: BaseException,
    *,
    duration_ms: int = 0,
) -> None:
    decision = _SNAPSHOT_FAILURES.record_failure(snapshot_key, type(exc).__name__)
    if decision.emit:
        log_event(
            LOGGER,
            "monitor.snapshot.failed",
            details={
                "snapshot_key": snapshot_key,
                "reason_code": "collection_failed",
                "duration_ms": max(0, duration_ms),
                "suppressed_count": decision.suppressed_count,
            },
            exc_info=_safe_exc_info(exc),
        )


def _record_snapshot_success(snapshot_key: str) -> None:
    recovery = _SNAPSHOT_FAILURES.record_success(snapshot_key)
    if recovery is not None:
        log_event(
            LOGGER,
            "monitor.snapshot.recovered",
            details={
                "snapshot_key": snapshot_key,
                "downtime_ms": recovery.downtime_ms,
                "failure_count": recovery.failure_count,
            },
        )


def _record_lock_failure(
    snapshot_key: str,
    exc: BaseException,
    *,
    operation: str,
) -> None:
    decision = _LOCK_FAILURES.record_failure(
        (snapshot_key, operation),
        type(exc).__name__,
    )
    if decision.emit:
        log_event(
            LOGGER,
            "monitor.lock.failed",
            details={
                "snapshot_key": snapshot_key,
                "reason_code": "lock_operation_failed",
                "suppressed_count": decision.suppressed_count,
            },
            exc_info=_safe_exc_info(exc),
        )


def _record_lock_success(snapshot_key: str, *, operation: str) -> None:
    recovery = _LOCK_FAILURES.record_success((snapshot_key, operation))
    if recovery is not None:
        log_event(
            LOGGER,
            "monitor.lock.recovered",
            details={
                "snapshot_key": snapshot_key,
                "downtime_ms": recovery.downtime_ms,
                "failure_count": recovery.failure_count,
            },
        )


def _utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime | None = None) -> str:
    """把时间统一序列化为毫秒精度 UTC ISO 8601。"""
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    """解析数据库时间或 ISO 字符串，无法识别时返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _json_default(value: Any) -> Any:
    """序列化数据库游标可能返回的时间、Decimal 和二进制值。"""
    if isinstance(value, datetime):
        return _iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"无法序列化监控快照类型: {type(value).__name__}")


def _decode_payload(value: Any) -> dict[str, Any] | None:
    """兼容连接器返回 JSON 字符串或字典的两种形式。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else None


def _coerce_interval_seconds(value: Any) -> int | None:
    """把可信配置转换为正整数秒，非法或非有限值返回 None。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return max(1, math.ceil(seconds))


def _interval_seconds(
    snapshot_key: str,
    policy: dict[str, Any] | None = None,
) -> int:
    """从统一配置取得某类快照的采集周期。"""
    effective = policy or get_monitor_settings()["effective"]
    heartbeat_interval = _coerce_interval_seconds(
        os.getenv("AGENT_PERSISTENCE_CLEANUP_HEARTBEAT_INTERVAL_SECONDS")
    ) or 10
    intervals = {
        "realtime": effective["realtime_interval_seconds"],
        "sql_performance": effective["sql_interval_seconds"],
        "capacity": effective["table_capacity_interval_seconds"],
        "integrity": effective["integrity_interval_seconds"],
        CLEANUP_RUNTIME_SNAPSHOT_KEY: heartbeat_interval,
        CLEANUP_OUTBOX_SNAPSHOT_KEY: effective["realtime_interval_seconds"],
    }
    return int(intervals[snapshot_key])


def _scheduled(
    snapshot_key: str,
    policy: dict[str, Any] | None = None,
) -> bool:
    """判断某类快照是否允许自动定时采集。"""
    if snapshot_key == "deep_audit":
        return False
    effective = policy or get_monitor_settings()["effective"]
    if not effective["auto_refresh_enabled"]:
        return False
    return snapshot_key != "integrity" or effective["integrity_enabled"]


def get_refresh_policy() -> dict[str, Any]:
    """返回供采集器和前端共同使用的有效刷新策略。"""
    resolved = get_monitor_settings()
    effective = resolved["effective"]
    return {
        **effective,
        "configuration_version": resolved["version"],
        "configuration_state": resolved["state"],
        "configuration_warning": resolved["warning"],
    }


def _read_snapshot_records() -> dict[str, dict[str, Any]]:
    """通过主库强一致读一次取得全部共享快照行。"""
    connection, _source = get_read_connection_with_source(consistency="strong")
    with connection as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT snapshot_key, payload_json, observed_at, refresh_requested_at, updated_at,
                   UTC_TIMESTAMP(6) AS database_now
            FROM database_monitor_snapshots
            WHERE snapshot_key IN (
                'realtime', 'sql_performance', 'capacity', 'integrity', 'deep_audit',
                'agent_persistence_cleanup_runtime', 'agent_persistence_cleanup_outbox'
            )
        """)
        rows = cursor.fetchall()
    return {row["snapshot_key"]: row for row in rows}


def _empty_snapshot(snapshot_key: str) -> dict[str, Any]:
    """构造尚未采集时的统一降级快照。"""
    return {
        "status": "unknown",
        "observed_at": None,
        "source_role": "monitor",
        "source_alias": "shared-snapshot",
        "is_estimate": snapshot_key == "capacity",
        "warning": "尚未生成共享监控快照",
    }


def _enrich_snapshot(
    snapshot_key: str,
    row: dict[str, Any] | None,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为持久化 payload 补充过期、调度和手动请求状态。"""
    payload = _decode_payload(row.get("payload_json")) if row else None
    result = dict(payload or _empty_snapshot(snapshot_key))
    observed_at = _parse_utc((row or {}).get("observed_at") or result.get("observed_at"))
    requested_at = _parse_utc((row or {}).get("refresh_requested_at"))
    effective = policy or get_monitor_settings()["effective"]
    interval = _interval_seconds(snapshot_key, effective)
    if snapshot_key == CLEANUP_RUNTIME_SNAPSHOT_KEY:
        interval = _coerce_interval_seconds(
            result.get("heartbeat_interval_seconds")
        ) or interval
    now = _parse_utc((row or {}).get("database_now")) or _utc_now()
    result["observed_at"] = _iso_utc(observed_at) if observed_at else None
    result["refresh_requested_at"] = _iso_utc(requested_at) if requested_at else None
    result["refresh_pending"] = bool(requested_at and (not observed_at or requested_at > observed_at))
    result["scheduled"] = _scheduled(snapshot_key, effective)
    result["interval_seconds"] = interval
    result["is_stale"] = not observed_at or now - observed_at > timedelta(seconds=interval * 2)
    result["next_due_at"] = (
        _iso_utc(observed_at + timedelta(seconds=interval))
        if observed_at and result["scheduled"]
        else None
    )
    return result


def get_dashboard_snapshots() -> dict[str, Any]:
    """返回全部共享快照；该函数绝不触发现场数据库采集。"""
    resolved = get_monitor_settings()
    effective = resolved["effective"]
    try:
        records = _read_snapshot_records()
    except Exception as exc:
        _record_snapshot_failure("dashboard_read", exc)
        records = {}
    else:
        _record_snapshot_success("dashboard_read")
    dashboard = {
        key: _enrich_snapshot(key, records.get(key), policy=effective)
        for key in DASHBOARD_SNAPSHOT_KEYS
    }
    # 旧环境在 monitor/cleanup worker 首次写入前没有这两行；保持旧 dashboard
    # 响应的键集合，前端对缺失快照按未知状态渲染。快照一旦产生便稳定返回。
    if any(key in records for key in CLEANUP_SNAPSHOT_KEYS):
        dashboard[CLEANUP_RUNTIME_SNAPSHOT_KEY] = _enrich_snapshot(
            CLEANUP_RUNTIME_SNAPSHOT_KEY,
            records.get(CLEANUP_RUNTIME_SNAPSHOT_KEY),
            policy=effective,
        )
        dashboard[CLEANUP_OUTBOX_SNAPSHOT_KEY] = _enrich_snapshot(
            CLEANUP_OUTBOX_SNAPSHOT_KEY,
            records.get(CLEANUP_OUTBOX_SNAPSHOT_KEY),
            policy=effective,
        )
    return dashboard | {
        "refresh_policy": {
            **effective,
            "configuration_version": resolved["version"],
            "configuration_state": resolved["state"],
            "configuration_warning": resolved["warning"],
        }
    }


def get_deep_audit_snapshot() -> dict[str, Any]:
    """返回最近一次手动 deep 审计快照且绝不现场执行审计。"""
    try:
        records = _read_snapshot_records()
    except Exception as exc:
        _record_snapshot_failure("deep_audit_read", exc)
        records = {}
    else:
        _record_snapshot_success("deep_audit_read")
    row = records.get("deep_audit")
    payload = _decode_payload((row or {}).get("payload_json")) or _empty_snapshot(
        "deep_audit"
    )
    observed_at = _parse_utc((row or {}).get("observed_at") or payload.get("observed_at"))
    requested_at = _parse_utc((row or {}).get("refresh_requested_at"))
    result = dict(payload)
    result["observed_at"] = _iso_utc(observed_at) if observed_at else None
    result["refresh_requested_at"] = _iso_utc(requested_at) if requested_at else None
    result["refresh_pending"] = bool(
        requested_at and (not observed_at or requested_at > observed_at)
    )
    result["scheduled"] = False
    result["interval_seconds"] = None
    result["is_stale"] = False
    result["next_due_at"] = None
    result.setdefault("mode", "deep")
    result.setdefault("checks", [])
    return result


def request_snapshot_refresh(groups: Iterable[str]) -> dict[str, Any]:
    """登记共享刷新请求；真正采集由独立 monitor 进程执行。"""
    normalized = tuple(dict.fromkeys(groups))
    invalid = [group for group in normalized if group not in SNAPSHOT_KEYS]
    if invalid or not normalized:
        raise ValueError(f"不支持的监控快照类型: {invalid or list(normalized)}")
    with get_write_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        for group in normalized:
            cursor.execute("""
                INSERT INTO database_monitor_snapshots (snapshot_key, refresh_requested_at)
                VALUES (%s, UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE refresh_requested_at = CASE
                    WHEN refresh_requested_at IS NULL THEN VALUES(refresh_requested_at)
                    ELSE GREATEST(refresh_requested_at, VALUES(refresh_requested_at))
                END
            """, (group,))
        placeholders = ", ".join(["%s"] * len(normalized))
        cursor.execute(
            f"""SELECT MAX(refresh_requested_at) AS requested_at
                FROM database_monitor_snapshots
                WHERE snapshot_key IN ({placeholders})""",
            normalized,
        )
        row = cursor.fetchone() or {}
        requested_at = _parse_utc(row.get("requested_at"))
        conn.commit()
    if requested_at is None:
        raise RuntimeError("数据库未返回共享刷新请求时间")
    return {"groups": list(normalized), "requested_at": _iso_utc(requested_at)}


def _result_status(results: Iterable[dict[str, Any]]) -> str:
    """按 error、warning/unknown、healthy 优先级汇总状态。"""
    statuses = {result.get("status") for result in results}
    if "error" in statuses:
        return "error"
    if statuses.intersection({"warning", "unknown"}):
        return "warning"
    return "healthy"


def get_slow_query_summary(
    limit: int = SLOW_QUERY_LIMIT,
    previous: dict[str, Any] | None = None,
    warning_threshold: int | None = None,
) -> dict[str, Any]:
    """采集并展平 SQL 性能结果，同时保留旧慢查询字段。"""
    result = inspect_slow_queries(
        limit=limit,
        previous=previous,
        warning_threshold=warning_threshold,
    )
    value = result.get("value") or {}
    statements = value.get("high_load_statements", value.get("top_statements") or [])
    return {
        **value,
        "Slow_queries": value.get("slow_queries_total", value.get("Slow_queries")),
        "slow_queries_total": value.get("slow_queries_total", value.get("Slow_queries")),
        "slow_queries_delta": value.get("slow_queries_delta"),
        "high_load_statements": statements,
        "top_statements": statements,
        "status": result["status"],
        "observed_at": result["observed_at"],
        "source_role": result["source_role"],
        "source_alias": result["source_alias"],
        "is_estimate": result["is_estimate"],
        "warning": result["warning"],
    }


def _collect_payload(
    snapshot_key: str,
    previous: dict[str, Any] | None,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用指定分层采集器，路由和 SQL 均不包含调度策略。"""
    effective = policy or get_monitor_settings()["effective"]
    if snapshot_key == "realtime":
        return get_realtime_report()
    if snapshot_key == "sql_performance":
        return get_slow_query_summary(
            previous=previous,
            warning_threshold=effective["slow_query_warning_delta"],
        )
    if snapshot_key == "capacity":
        return get_capacity_report()
    if snapshot_key == "integrity":
        return get_quick_integrity_report()
    if snapshot_key == "deep_audit":
        from Database.deep_audit import get_deep_audit_report

        return get_deep_audit_report()
    if snapshot_key == CLEANUP_OUTBOX_SNAPSHOT_KEY:
        return get_cleanup_outbox_report()
    raise ValueError(f"未知监控快照类型: {snapshot_key}")


def _collection_failure_payload(
    snapshot_key: str,
    exc: Exception,
    *,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """把单层采集器的未处理异常转换为可持久化的降级快照。"""
    payload: dict[str, Any] = {
        "status": "unknown",
        "observed_at": _iso_utc(),
        "source_role": "monitor",
        "source_alias": "shared-snapshot",
        "is_estimate": snapshot_key == "capacity",
        "warning": f"{snapshot_key} 采集失败，已记录降级快照",
    }
    if snapshot_key == "sql_performance":
        payload.update({
            "Slow_queries": None,
            "slow_queries_total": None,
            "slow_queries_delta": None,
            "high_load_statements": [],
            "top_statements": [],
            "baseline_reset": True,
        })
    if snapshot_key == "integrity":
        payload.update({"blocking_count": 0, "blocking_record_count": 0, "checks": []})
    if snapshot_key == "deep_audit":
        payload.update({
            "mode": "deep",
            "auto_scheduled": False,
            "checks": [],
        })
    if snapshot_key == CLEANUP_OUTBOX_SNAPSHOT_KEY:
        payload.update({
            "summary": {
                task_type: _empty_cleanup_summary()
                for task_type in CLEANUP_OUTBOX_TABLES
            },
            "items": [],
        })
    return payload


def _payload_has_unknown_status(value: Any) -> bool:
    """识别采集器已安全吞并、但仍代表采集失败的 unknown 子结果。"""
    if isinstance(value, dict):
        if value.get("status") == "unknown":
            return True
        return any(_payload_has_unknown_status(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_payload_has_unknown_status(item) for item in value)
    return False


def collect_snapshot(snapshot_key: str, *, require_due: bool = False) -> bool:
    """在 MySQL 命名锁保护下复核调度状态并采集一个共享快照。"""
    if snapshot_key not in SNAPSHOT_KEYS:
        raise ValueError(f"未知监控快照类型: {snapshot_key}")
    started_at = time.perf_counter()
    lock_name = f"causalagent:db-monitor:{snapshot_key}"
    try:
        lock_connection_context = get_write_connection()
    except Exception as exc:
        _record_snapshot_failure(snapshot_key, exc)
        raise
    with lock_connection_context as lock_connection:
        cursor = lock_connection.cursor(dictionary=True)
        try:
            cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (lock_name,))
            lock_row = cursor.fetchone() or {}
            lock_value = lock_row.get("acquired")
            if lock_value is None:
                _record_lock_failure(
                    snapshot_key,
                    RuntimeError("named lock result unavailable"),
                    operation="acquire",
                )
                return False
            acquired = int(lock_value) == 1
        except Exception as exc:
            _record_lock_failure(snapshot_key, exc, operation="acquire")
            raise
        _record_lock_success(snapshot_key, operation="acquire")
        if not acquired:
            return False
        try:
            collection_failure: Exception | None = None
            try:
                records = _read_snapshot_records()
                effective = get_monitor_settings()["effective"]
                if require_due and not _record_is_due(
                    snapshot_key,
                    policy=effective,
                    row=records.get(snapshot_key),
                ):
                    return False
                previous = _decode_payload((records.get(snapshot_key) or {}).get("payload_json"))
                try:
                    payload = _collect_payload(
                        snapshot_key,
                        previous,
                        policy=effective,
                    )
                except Exception as exc:
                    collection_failure = exc
                    payload = _collection_failure_payload(
                        snapshot_key,
                        exc,
                        duration_ms=int((time.perf_counter() - started_at) * 1000),
                    )
                cursor.execute("""
                    INSERT INTO database_monitor_snapshots (
                        snapshot_key, payload_json, observed_at
                    ) VALUES (%s, %s, UTC_TIMESTAMP(6))
                    ON DUPLICATE KEY UPDATE
                        payload_json = VALUES(payload_json),
                        observed_at = VALUES(observed_at)
                """, (
                    snapshot_key,
                    json.dumps(payload, ensure_ascii=False, default=_json_default),
                ))
                lock_connection.commit()
            except Exception as exc:
                _record_snapshot_failure(
                    snapshot_key,
                    exc,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
                raise
            if collection_failure is not None:
                _record_snapshot_failure(
                    snapshot_key,
                    collection_failure,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
            elif _payload_has_unknown_status(payload):
                _record_snapshot_failure(
                    snapshot_key,
                    SnapshotCollectionDegraded(),
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
            else:
                _record_snapshot_success(snapshot_key)
            return True
        finally:
            try:
                cursor.execute(
                    "SELECT RELEASE_LOCK(%s) AS released",
                    (lock_name,),
                )
                released = (cursor.fetchone() or {}).get("released")
                if released is None or int(released) != 1:
                    raise RuntimeError("named lock release was not confirmed")
            except Exception as exc:
                _record_lock_failure(snapshot_key, exc, operation="release")
            else:
                _record_lock_success(snapshot_key, operation="release")


def _record_is_due(
    snapshot_key: str,
    row: dict[str, Any] | None,
    *,
    policy: dict[str, Any] | None = None,
) -> bool:
    """判断快照是否因手动请求或自动周期到期而需要采集。"""
    effective = policy or get_monitor_settings()["effective"]
    observed_at = _parse_utc((row or {}).get("observed_at"))
    requested_at = _parse_utc((row or {}).get("refresh_requested_at"))
    if requested_at and (not observed_at or requested_at > observed_at):
        return True
    if not _scheduled(snapshot_key, effective):
        return False
    now = _parse_utc((row or {}).get("database_now")) or _utc_now()
    return not observed_at or now - observed_at >= timedelta(
        seconds=_interval_seconds(snapshot_key, effective)
    )


def get_due_snapshot_keys() -> tuple[str, ...]:
    """读取一次调度状态并返回当前到期或收到手动请求的快照类型。"""
    try:
        records = _read_snapshot_records()
    except Exception as exc:
        _record_snapshot_failure("scheduler", exc)
        return ()
    _record_snapshot_success("scheduler")
    effective = get_monitor_settings()["effective"]
    return tuple(
        snapshot_key
        for snapshot_key in SNAPSHOT_KEYS
        if _record_is_due(
            snapshot_key,
            records.get(snapshot_key),
            policy=effective,
        )
    )


def collect_due_snapshots() -> dict[str, bool]:
    """同步采集所有到期类型；常驻 monitor 使用独立执行单元避免跨层阻塞。"""
    results: dict[str, bool] = {}
    for snapshot_key in get_due_snapshot_keys():
        try:
            results[snapshot_key] = collect_snapshot(snapshot_key, require_due=True)
        except Exception:
            results[snapshot_key] = False
    return results


def _overview_from_dashboard(dashboard: dict[str, Any]) -> dict[str, Any]:
    """从一次 dashboard 读取结果组装旧 overview 数据结构。"""
    realtime = dashboard["realtime"]
    capacity = dashboard["capacity"]
    primary = realtime.get("primary") or _empty_snapshot("realtime")
    replica = realtime.get("replica") or _empty_snapshot("realtime")
    connections = realtime.get("connections") or _empty_snapshot("realtime")
    revision = capacity.get("revision") or _empty_snapshot("capacity")
    tables = capacity.get("tables") or _empty_snapshot("capacity")
    blocks = [revision, primary, replica, connections, tables]
    return {
        "status": _result_status(blocks),
        "observed_at": max(
            (item.get("observed_at") for item in blocks if item.get("observed_at")),
            default=None,
        ),
        "revision": revision,
        "primary": primary,
        "replica": replica,
        "connections": connections,
        "tables": tables,
        "blocking_issues": [
            *(realtime.get("blocking_issues") or []),
            *(capacity.get("blocking_issues") or []),
        ],
    }


def get_database_overview_snapshot() -> dict[str, Any]:
    """从共享快照组装旧 overview 数据结构。"""
    return _overview_from_dashboard(get_dashboard_snapshots())


def _legacy_replica_value(replica: dict[str, Any]) -> dict[str, Any] | None:
    """把标准从库检查结果映射回旧接口字段。"""
    value = replica.get("value")
    if not value or not value.get("available"):
        return None
    return {
        "Replica_IO_Running": value.get("io_running"),
        "Replica_SQL_Running": value.get("sql_running"),
        "Seconds_Behind_Source": value.get("lag_seconds"),
        "Last_IO_Error": value.get("last_io_error"),
        "Last_SQL_Error": value.get("last_sql_error"),
    }


def get_db_health() -> dict[str, Any]:
    """从共享快照返回兼容旧字段的数据库健康摘要。"""
    dashboard = get_dashboard_snapshots()
    overview = _overview_from_dashboard(dashboard)
    connection_value = overview["connections"].get("value") or {}
    table_value = overview["tables"].get("value") or []
    sql_snapshot = dashboard["sql_performance"]
    return {
        "connections": {
            "Threads_connected": connection_value.get("threads_connected"),
            "Threads_running": connection_value.get("threads_running"),
            "max_connections": connection_value.get("max_connections"),
        },
        "slow_queries": sql_snapshot.get("slow_queries_total", sql_snapshot.get("Slow_queries")),
        "replica": _legacy_replica_value(overview["replica"]),
        "tables": table_value,
        "status": overview["status"],
        "observed_at": overview["observed_at"],
        "sources": {
            key: {
                "source_role": overview[key].get("source_role"),
                "source_alias": overview[key].get("source_alias"),
                "observed_at": overview[key].get("observed_at"),
                "is_estimate": overview[key].get("is_estimate", False),
                "warning": overview[key].get("warning"),
            }
            for key in ("connections", "replica", "tables")
        },
    }


def get_integrity_snapshot() -> dict[str, Any]:
    """读取最近一次运行期完整性审计快照。"""
    return get_dashboard_snapshots()["integrity"]


def get_sql_performance_snapshot(limit: int = SLOW_QUERY_LIMIT) -> dict[str, Any]:
    """读取 SQL 性能快照，并在兼容接口上限制返回行数。"""
    result = dict(get_dashboard_snapshots()["sql_performance"])
    statements = result.get("high_load_statements") or result.get("top_statements") or []
    result["high_load_statements"] = statements[:limit]
    result["top_statements"] = statements[:limit]
    result["Slow_queries"] = result.get("slow_queries_total", result.get("Slow_queries"))
    result["limit"] = limit
    return result


def get_worker_snapshot_from_cache() -> dict[str, Any]:
    """读取实时快照内的 Worker/Job 区块。"""
    realtime = get_dashboard_snapshots()["realtime"]
    return realtime.get("jobs") or {
        "jobs": [],
        "summary": {"queued": None, "running": None, "stale": None, "max_attempts_running": None},
        "status": "unknown",
        "observed_at": realtime.get("observed_at"),
        "source_role": "primary",
        "source_alias": "primary",
        "is_estimate": False,
        "warning": realtime.get("warning") or "尚未采集 Worker/Job 快照",
    }


def get_cleanup_runtime_snapshot() -> dict[str, Any]:
    """读取 Agent 持久化清理 worker 最近一次心跳共享快照。"""
    dashboard = get_dashboard_snapshots()
    return dashboard.get(CLEANUP_RUNTIME_SNAPSHOT_KEY) or _empty_snapshot(
        CLEANUP_RUNTIME_SNAPSHOT_KEY
    )


def _empty_cleanup_summary() -> dict[str, Any]:
    """构造单类 cleanup outbox 尚未采集时的空汇总。"""
    return {
        "pending": None,
        "due_pending": None,
        "processing": None,
        "expired_processing": None,
        "failed": None,
        "latest_completed_at": None,
        "earliest_pending_at": None,
    }


def _cleanup_item_rank(row: dict[str, Any]) -> int:
    """按失败、租约过期和到期顺序排列 outbox 管理列表。"""
    if row.get("status") == "failed":
        return 0
    if row.get("lease_expired"):
        return 1
    if row.get("status") == "processing":
        return 2
    if row.get("status") == "pending" and row.get("is_due"):
        return 3
    return 4


def get_cleanup_outbox_report() -> dict[str, Any]:
    """采集两类 cleanup outbox 的脱敏汇总和最多 100 条待处理或异常条目。"""
    try:
        connection, source = get_read_connection_with_source(consistency="strong")
        with connection as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT UTC_TIMESTAMP(6) AS database_now")
            database_now = (
                _parse_utc((cursor.fetchone() or {}).get("database_now")) or _utc_now()
            )
            summaries: dict[str, dict[str, Any]] = {}
            items: list[dict[str, Any]] = []
            for task_type, (table, target_column) in CLEANUP_OUTBOX_TABLES.items():
                summaries[task_type] = _collect_cleanup_summary(cursor, table)
                items.extend(
                    _collect_cleanup_items(
                        cursor,
                        task_type=task_type,
                        table=table,
                        target_column=target_column,
                        database_now=database_now,
                    )
                )
        items.sort(key=lambda item: (item["priority"], item["task_type"], item["outbox_id"]))
        items = items[:CLEANUP_OUTBOX_ITEM_LIMIT]
        status = "error" if any(
            item["status"] == "failed" or item["lease_expired"] for item in items
        ) else "warning" if items else "healthy"
        return {
            "status": status,
            "observed_at": _iso_utc(),
            "source_role": source.get("source_role", "primary"),
            "source_alias": source.get("source_alias", "primary"),
            "is_estimate": False,
            "warning": "存在失败或租约过期的 cleanup 任务" if status == "error" else None,
            "summary": summaries,
            "items": items,
        }
    except Exception as exc:
        return _collection_failure_payload(CLEANUP_OUTBOX_SNAPSHOT_KEY, exc)


def _collect_cleanup_summary(cursor, table: str) -> dict[str, Any]:
    """采集单张 cleanup outbox 表的状态汇总。"""
    cursor.execute(
        f"""
        SELECT
            SUM(status = 'pending') AS pending,
            SUM(status = 'pending' AND available_at <= UTC_TIMESTAMP(6)) AS due_pending,
            SUM(status = 'processing') AS processing,
            SUM(status = 'processing' AND lease_expires_at IS NOT NULL
                AND lease_expires_at < UTC_TIMESTAMP(6)) AS expired_processing,
            SUM(status = 'failed') AS failed,
            MAX(completed_at) AS latest_completed_at,
            MIN(CASE WHEN status = 'pending' THEN available_at END) AS earliest_pending_at
        FROM {table}
        """
    )
    summary = cursor.fetchone() or {}
    latest_completed_at = _parse_utc(summary.get("latest_completed_at"))
    earliest_pending_at = _parse_utc(summary.get("earliest_pending_at"))
    return {
        "pending": int(summary.get("pending") or 0),
        "due_pending": int(summary.get("due_pending") or 0),
        "processing": int(summary.get("processing") or 0),
        "expired_processing": int(summary.get("expired_processing") or 0),
        "failed": int(summary.get("failed") or 0),
        "latest_completed_at": _iso_utc(latest_completed_at) if latest_completed_at else None,
        "earliest_pending_at": _iso_utc(earliest_pending_at) if earliest_pending_at else None,
    }


def _collect_cleanup_items(
    cursor,
    *,
    task_type: str,
    table: str,
    target_column: str,
    database_now: datetime,
) -> list[dict[str, Any]]:
    """采集单张 cleanup outbox 表里待处理或异常的脱敏条目。"""
    cursor.execute(
        f"""
        SELECT
            id, {target_column} AS target, operation_id, status, attempts,
            available_at, lease_expires_at, created_at, completed_at,
            (last_error IS NOT NULL AND last_error <> '') AS has_error
        FROM {table}
        WHERE status IN ('pending', 'processing', 'failed')
        ORDER BY
            CASE
                WHEN status = 'failed' THEN 0
                WHEN status = 'processing' AND lease_expires_at < UTC_TIMESTAMP(6) THEN 1
                WHEN status = 'processing' THEN 2
                WHEN status = 'pending' AND available_at <= UTC_TIMESTAMP(6) THEN 3
                ELSE 4
            END,
            id ASC
        LIMIT {CLEANUP_OUTBOX_ITEM_LIMIT}
        """
    )
    items: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        lease_expires = _parse_utc(row.get("lease_expires_at"))
        available_at = _parse_utc(row.get("available_at"))
        created_at = _parse_utc(row.get("created_at"))
        completed_at = _parse_utc(row.get("completed_at"))
        item = {
            "task_type": task_type,
            "outbox_id": int(row.get("id") or 0),
            "id": int(row.get("id") or 0),
            "thread_id": (
                str(row.get("target")) if task_type == CHECKPOINT_TASK else None
            ),
            "user_id": (
                int(row.get("target")) if task_type == USER_MEMORY_TASK else None
            ),
            "operation_id": row.get("operation_id"),
            "status": row.get("status"),
            "attempts": int(row.get("attempts") or 0),
            "max_attempts": MAX_CLEANUP_ATTEMPTS,
            "available_at": _iso_utc(available_at) if available_at else None,
            "lease_expires_at": _iso_utc(lease_expires) if lease_expires else None,
            "created_at": _iso_utc(created_at) if created_at else None,
            "completed_at": _iso_utc(completed_at) if completed_at else None,
            "has_error": bool(row.get("has_error")),
            "is_due": bool(available_at and available_at <= database_now),
            "lease_expired": bool(
                row.get("status") == "processing"
                and lease_expires
                and lease_expires < database_now
            ),
        }
        item["error_state"] = (
            "failed" if item["status"] == "failed"
            else "lease_expired" if item["lease_expired"]
            else None
        )
        item["priority"] = _cleanup_item_rank(item)
        items.append(item)
    return items
