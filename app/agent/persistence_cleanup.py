"""MySQL cleanup outbox 与 PostgreSQL Agent 持久化数据的桥接服务。

两张 outbox 表分别登记两类只存在于 PostgreSQL 的 Agent 持久化数据：

- `checkpoint_cleanup_outbox`：某个 Job 的父图 checkpoint 和 Deep Agent 子图
  checkpoint。
- `user_memory_cleanup_outbox`：某个用户长期记忆 Store 的 namespace 内容。

两张表都由同一个 worker 消费，状态机、租约、退避和错误结论保持一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Iterable

from app.db import get_write_connection


MAX_CLEANUP_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (10, 30)
CLEANUP_RUNTIME_SNAPSHOT_KEY = "agent_persistence_cleanup_runtime"
WORKER_ALIAS = "agent-persistence-cleanup"

CHECKPOINT_TASK = "checkpoint"
USER_MEMORY_TASK = "user_memory"
TASK_TYPES = (CHECKPOINT_TASK, USER_MEMORY_TASK)

_TABLES = {
    CHECKPOINT_TASK: "checkpoint_cleanup_outbox",
    USER_MEMORY_TASK: "user_memory_cleanup_outbox",
}
_TARGET_COLUMNS = {
    CHECKPOINT_TASK: "thread_id",
    USER_MEMORY_TASK: "user_id",
}

# 两类任务轮转领取的起点，避免其中一类长期得不到执行机会。
_claim_rotation = 0


def _table(task_type: str) -> str:
    """返回任务类型对应的 outbox 表名。"""
    try:
        return _TABLES[task_type]
    except KeyError as exc:
        raise ValueError(f"未知的清理任务类型: {task_type}") from exc


def _json_loads(value: Any) -> dict[str, Any]:
    """读取管理员操作结果 JSON，兼容 MySQL 驱动的字符串和对象返回值。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value)


def _json_dumps(value: Any) -> str:
    """编码不含敏感内容的 cleanup 聚合结果。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_cleanup_runtime_snapshot(payload: dict[str, Any]) -> None:
    """写入 cleanup worker 的安全运行快照，不暴露主机、账号或连接信息。"""
    allowed = {
        "worker_alias",
        "worker_status",
        "current_task_type",
        "started_at",
        "heartbeat_at",
        "current_outbox_id",
        "run_success_count",
        "run_failure_count",
        "startup_success_count",
        "startup_failure_count",
        "heartbeat_interval_seconds",
        "processing_started_at",
        "processing_duration_seconds",
        "current_processing_started_at",
        "current_processing_duration_seconds",
        "last_failure_at",
        "last_error_present",
    }
    safe_payload = {key: value for key, value in payload.items() if key in allowed}
    with get_write_connection() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO database_monitor_snapshots (
                snapshot_key, payload_json, observed_at
            ) VALUES (%s, %s, UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE
                payload_json = VALUES(payload_json),
                observed_at = VALUES(observed_at)
            """,
            (
                CLEANUP_RUNTIME_SNAPSHOT_KEY,
                _json_dumps({
                    **safe_payload,
                    "status": "healthy",
                    "source_role": "cleanup-worker",
                    "source_alias": WORKER_ALIAS,
                    "is_estimate": False,
                    "warning": None,
                }),
            ),
        )
        connection.commit()


def _enqueue(
    cursor,
    task_type: str,
    target_value: Any,
    *,
    operation_id: str | None,
) -> bool:
    """登记一条清理任务，并返回该目标当前是否仍需清理。"""
    table = _table(task_type)
    target_column = _TARGET_COLUMNS[task_type]
    cursor.execute(
        f"""
        INSERT INTO {table} (
            {target_column}, operation_id, status, attempts, available_at
        ) VALUES (%s, %s, 'pending', 0, UTC_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE
            operation_id = COALESCE({table}.operation_id, VALUES(operation_id)),
            status = IF({table}.status = 'succeeded',
                        {table}.status, 'pending'),
            available_at = IF({table}.status = 'succeeded',
                              {table}.available_at, UTC_TIMESTAMP(6)),
            lease_expires_at = NULL,
            last_error = NULL
        """,
        (target_value, operation_id),
    )
    cursor.execute(
        f"SELECT status FROM {table} WHERE {target_column} = %s",
        (target_value,),
    )
    row = cursor.fetchone()
    status = row.get("status") if isinstance(row, dict) else (row[0] if row else None)
    return status != "succeeded"


def enqueue_checkpoint_cleanup(
    cursor,
    job_id: str,
    *,
    operation_id: str | None = None,
) -> bool:
    """登记一个 Job 的父子图 checkpoint 清理任务，并返回是否仍需清理。"""
    return _enqueue(cursor, CHECKPOINT_TASK, job_id, operation_id=operation_id)


def enqueue_checkpoint_cleanup_many(
    cursor,
    job_ids: Iterable[str],
    *,
    operation_id: str | None = None,
) -> int:
    """批量登记删除涉及的 Job checkpoint，并返回仍需处理的数量。"""
    count = 0
    for job_id in job_ids:
        if enqueue_checkpoint_cleanup(cursor, str(job_id), operation_id=operation_id):
            count += 1
    return count


def enqueue_user_memory_cleanup(
    cursor,
    user_id: int,
    *,
    operation_id: str | None = None,
) -> bool:
    """登记用户长期记忆清理任务，并返回是否仍需清理。"""
    return _enqueue(cursor, USER_MEMORY_TASK, int(user_id), operation_id=operation_id)


def _operation_task_counts(cursor, table: str, operation_id: str) -> dict[str, int]:
    """统计某个管理员操作在某张 outbox 上的任务状态分布。"""
    cursor.execute(
        f"""
        SELECT
            COUNT(*) AS total_count,
            SUM(status = 'succeeded') AS succeeded_count,
            SUM(status = 'failed') AS failed_count,
            SUM(status IN ('pending', 'processing')) AS pending_count
        FROM {table}
        WHERE operation_id = %s
        """,
        (operation_id,),
    )
    counts = cursor.fetchone() or {}
    return {
        "total": int(counts.get("total_count") or 0),
        "succeeded": int(counts.get("succeeded_count") or 0),
        "failed": int(counts.get("failed_count") or 0),
        "pending": int(counts.get("pending_count") or 0),
    }


def _task_summary(counts: dict[str, int]) -> dict[str, Any]:
    """把单类任务计数转换成稳定的聚合摘要。"""
    if counts["failed"]:
        status = "failed"
    elif counts["pending"]:
        status = "pending"
    else:
        status = "succeeded"
    return {
        "status": status,
        "total": counts["total"],
        "succeeded": counts["succeeded"],
        "failed": counts["failed"],
        "pending": counts["pending"],
    }


def _update_operation_aggregate(cursor, operation_id: str | None) -> None:
    """按两类 outbox 的当前状态推进管理员操作的 running/succeeded/failed。"""
    if not operation_id:
        return
    cursor.execute(
        """
        SELECT status, target_count, result_json
        FROM admin_operations
        WHERE operation_id = %s
        FOR UPDATE
        """,
        (operation_id,),
    )
    operation = cursor.fetchone()
    if not operation:
        return

    summaries = {
        task_type: _task_summary(
            _operation_task_counts(cursor, _table(task_type), operation_id)
        )
        for task_type in TASK_TYPES
    }
    total = sum(summary["total"] for summary in summaries.values())
    if total == 0:
        return

    failed = sum(summary["failed"] for summary in summaries.values())
    pending = sum(summary["pending"] for summary in summaries.values())
    if failed:
        operation_status = "failed"
    elif pending:
        operation_status = "running"
    else:
        operation_status = "succeeded"

    result = _json_loads(operation.get("result_json"))
    result["checkpoint_cleanup"] = summaries[CHECKPOINT_TASK]
    result["user_memory_cleanup"] = summaries[USER_MEMORY_TASK]
    result["status"] = operation_status
    completed_sql = (
        ", completed_at = UTC_TIMESTAMP(6)"
        if operation_status in {"succeeded", "failed"}
        else ", completed_at = NULL"
    )
    cursor.execute(
        f"""
        UPDATE admin_operations
        SET status = %s,
            succeeded_count = %s,
            failed_count = %s,
            result_json = %s
            {completed_sql}
        WHERE operation_id = %s
        """,
        (
            operation_status,
            int(operation.get("target_count") or 0) if operation_status == "succeeded" else 0,
            failed,
            _json_dumps(result),
            operation_id,
        ),
    )


def _claim_order() -> tuple[str, ...]:
    """返回本次领取的任务类型顺序，按调用次数在两类任务之间轮转。"""
    global _claim_rotation
    offset = _claim_rotation % len(TASK_TYPES)
    _claim_rotation = (_claim_rotation + 1) % len(TASK_TYPES)
    return TASK_TYPES[offset:] + TASK_TYPES[:offset]


def _release_expired_leases(cursor) -> None:
    """把租约过期的 processing 任务退回 pending 或推进为终态失败。"""
    expired_operations: set[str] = set()
    for task_type in TASK_TYPES:
        table = _table(task_type)
        cursor.execute(
            f"""
            SELECT id, operation_id, attempts
            FROM {table}
            WHERE status = 'processing'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < UTC_TIMESTAMP(6)
            FOR UPDATE SKIP LOCKED
            """
        )
        for expired in cursor.fetchall():
            expired_status = (
                "failed"
                if int(expired.get("attempts") or 0) >= MAX_CLEANUP_ATTEMPTS
                else "pending"
            )
            cursor.execute(
                f"""
                UPDATE {table}
                SET status = %s, lease_expires_at = NULL,
                    completed_at = IF(%s = 'failed', UTC_TIMESTAMP(6), NULL)
                WHERE id = %s AND status = 'processing'
                """,
                (expired_status, expired_status, expired["id"]),
            )
            if expired.get("operation_id"):
                expired_operations.add(str(expired["operation_id"]))
    for operation_id in expired_operations:
        _update_operation_aggregate(cursor, operation_id)


def _claim_one(cursor, task_type: str, lease_seconds: int) -> dict[str, Any] | None:
    """领取指定类型的一条到期任务。"""
    table = _table(task_type)
    target_column = _TARGET_COLUMNS[task_type]
    cursor.execute(
        f"""
        SELECT id, {target_column} AS target, operation_id, attempts
        FROM {table}
        WHERE status = 'pending'
          AND attempts < %s
          AND available_at <= UTC_TIMESTAMP(6)
        ORDER BY id ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """,
        (MAX_CLEANUP_ATTEMPTS,),
    )
    item = cursor.fetchone()
    if not item:
        return None
    cursor.execute(
        f"""
        UPDATE {table}
        SET status = 'processing',
            attempts = attempts + 1,
            lease_expires_at = %s,
            last_error = NULL
        WHERE id = %s AND status = 'pending'
        """,
        (
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(seconds=lease_seconds),
            item["id"],
        ),
    )
    claimed: dict[str, Any] = {
        "task_type": task_type,
        "id": int(item["id"]),
        "operation_id": item.get("operation_id"),
        "attempts": int(item.get("attempts") or 0) + 1,
    }
    if task_type == CHECKPOINT_TASK:
        claimed["job_id"] = str(item["target"])
    else:
        claimed["user_id"] = int(item["target"])
    return claimed


def claim_cleanup_item(
    worker_id: str,
    *,
    lease_seconds: int = 300,
) -> dict[str, Any] | None:
    """使用行锁和 SKIP LOCKED 领取一条到期可执行的清理任务。"""
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()
        _release_expired_leases(cursor)
        item = None
        for task_type in _claim_order():
            item = _claim_one(cursor, task_type, lease_seconds)
            if item:
                break
        connection.commit()
        return item
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_cleanup_succeeded(task_type: str, item_id: int) -> None:
    """把一条清理任务标记为成功，并刷新管理员操作聚合状态。"""
    table = _table(task_type)
    with get_write_connection() as connection:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()
        cursor.execute(
            f"""
            SELECT operation_id
            FROM {table}
            WHERE id = %s
            FOR UPDATE
            """,
            (item_id,),
        )
        item = cursor.fetchone()
        if not item:
            connection.rollback()
            return
        cursor.execute(
            f"""
            UPDATE {table}
            SET status = 'succeeded',
                lease_expires_at = NULL,
                last_error = NULL,
                completed_at = UTC_TIMESTAMP(6)
            WHERE id = %s AND status = 'processing'
            """,
            (item_id,),
        )
        _update_operation_aggregate(cursor, item.get("operation_id"))
        connection.commit()


def mark_cleanup_failed(task_type: str, item_id: int, message: str) -> None:
    """记录一次 PostgreSQL 清理失败，并按 10/30 秒策略安排有限重试。"""
    table = _table(task_type)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_write_connection() as connection:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()
        cursor.execute(
            f"""
            SELECT operation_id, attempts
            FROM {table}
            WHERE id = %s
            FOR UPDATE
            """,
            (item_id,),
        )
        item = cursor.fetchone()
        if not item:
            connection.rollback()
            return
        attempts = int(item.get("attempts") or 0)
        terminal = attempts >= MAX_CLEANUP_ATTEMPTS
        retry_delay = RETRY_DELAYS_SECONDS[min(attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        available_at = now + timedelta(seconds=retry_delay)
        cursor.execute(
            f"""
            UPDATE {table}
            SET status = %s,
                available_at = %s,
                lease_expires_at = NULL,
                last_error = %s,
                completed_at = %s
            WHERE id = %s AND status = 'processing'
            """,
            (
                "failed" if terminal else "pending",
                available_at,
                message[:4000],
                now if terminal else None,
                item_id,
            ),
        )
        _update_operation_aggregate(cursor, item.get("operation_id"))
        connection.commit()
