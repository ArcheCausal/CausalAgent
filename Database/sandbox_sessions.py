"""沙盒控制器的主库生命周期存储。

控制器必须先持有其 singleton.GET_LOCK，并完成启动核查后才开放 reserve。
reserve 在本进程串行完成计数和登记；没有容量策略表，也不会因超时删除记录。
用户操作锁顺序为 Job -> sandbox session -> latest Job input。清理仅锁 session，
不反向取得 Job 锁。所有事务都在远端调用前结束；回执通过 version CAS 保存。
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime
import json
import os
import re
import threading
from typing import Any, Iterator

from Agent.data_processing.plans import plan_confirmation_version
from app.db import get_write_connection, record_database_failure


_ADMISSION_LOCK = threading.Lock()
_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_OWNER_FIELDS = ("job_id", "worker_id", "attempt_count", "lease_epoch")
_JSON_FIELDS = ("metadata", "operation_result")
_EXECUTE_PLAN_VERSION_KEY = "execute_plan_version"
_TRANSITIONS = {
    "creating": {"creating", "active", "uncertain", "deleting", "closed"},
    "active": {"active", "uncertain", "deleting", "closed"},
    "uncertain": {"uncertain", "active", "deleting", "closed"},
    "deleting": {"deleting", "uncertain", "closed"},
    "closed": {"closed"},
}


class SandboxStorageError(RuntimeError):
    """可安全映射为控制 API 错误的存储异常。"""


class SandboxFencedError(SandboxStorageError):
    """执行身份、文件或最新方案确认已失效。"""


class SandboxConflictError(SandboxStorageError):
    """幂等请求、操作或生命周期发生冲突。"""


class SandboxCapacityError(SandboxStorageError):
    """所有非 closed 记录已达到配置容量。"""


class SandboxVersionError(SandboxConflictError):
    """回执版本已过期，必须重新读取后核查。"""


class SandboxNotFoundError(SandboxStorageError):
    """找不到生命周期记录。"""


def _positive(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded ASCII identifier")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _identity(identity: Mapping) -> tuple[str, str, int, int]:
    if not isinstance(identity, Mapping):
        raise ValueError("identity must be a mapping")
    job_id = _identifier(identity.get("job_id"), "job_id")
    if len(job_id) > 36:
        raise ValueError("job_id is too long")
    return (
        job_id,
        _identifier(identity.get("worker_id"), "worker_id"),
        _positive(identity.get("attempt_count"), "attempt_count"),
        _positive(identity.get("lease_epoch"), "lease_epoch"),
    )


def _json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ValueError("sandbox JSON result exceeds the storage limit")
    return encoded


@contextmanager
def _transaction() -> Iterator[Any]:
    connection = get_write_connection()
    cursor = None
    try:
        connection.start_transaction(isolation_level="READ COMMITTED")
        cursor = connection.cursor(dictionary=True)
        yield cursor
        connection.commit()
    except BaseException as exc:
        connection.rollback()
        record_database_failure(exc, operation="sandbox_lifecycle")
        raise
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def _row(cursor: Any, request_id: str, *, lock: bool = False) -> dict:
    _identifier(request_id, "request_id")
    cursor.execute(
        "SELECT *, expires_at <= UTC_TIMESTAMP(6) AS expired "
        "FROM analysis_sandbox_sessions WHERE request_id = %s"
        + (" FOR UPDATE" if lock else ""),
        (request_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise SandboxNotFoundError("sandbox session not found")
    for name in _JSON_FIELDS:
        if isinstance(row[name], (str, bytes, bytearray)):
            row[name] = json.loads(row[name])
    row.pop("open_job_id", None)
    row["expired"] = bool(row["expired"])
    return row


def _lock_owner(cursor: Any, identity: Mapping, source_hash: str | None = None) -> dict:
    expected = _identity(identity)
    # analysis_jobs has no physical lease_expires_at. Use the same configured
    # stale threshold as claim_next_job, evaluated against the primary's clock.
    lease_seconds = int(os.environ.get("JOB_STALE_AFTER_SECONDS", "120"))
    _positive(lease_seconds, "JOB_STALE_AFTER_SECONDS")
    cursor.execute(
        """
        SELECT job_id, user_id, session_id, worker_id, attempt_count, lease_epoch,
               status, execution_state, input_file_hash,
               TIMESTAMPADD(SECOND, %s, heartbeat_at) AS lease_expires_at,
               heartbeat_at IS NOT NULL AND
               TIMESTAMPADD(SECOND, %s, heartbeat_at) > UTC_TIMESTAMP(6) AS lease_valid
        FROM analysis_jobs WHERE job_id = %s FOR UPDATE
        """,
        (lease_seconds, lease_seconds, expected[0]),
    )
    job = cursor.fetchone()
    if (
        job is None
        or tuple(job[name] for name in _OWNER_FIELDS) != expected
        or job["status"] != "running"
        or job["execution_state"] != "leased"
        or not job["lease_valid"]
        or ("user_id" in identity and identity["user_id"] != job["user_id"])
        or ("session_id" in identity and identity["session_id"] != job["session_id"])
        or (source_hash is not None and job["input_file_hash"] != _digest(source_hash, "source_hash"))
    ):
        raise SandboxFencedError("current Job execution is no longer authorized")
    return job


def _owned_row(cursor: Any, request_id: str, identity: Mapping) -> dict:
    job = _lock_owner(cursor, identity)
    row = _row(cursor, request_id, lock=True)
    if (
        tuple(row[name] for name in _OWNER_FIELDS) != _identity(identity)
        or row["source_hash"] != job["input_file_hash"]
    ):
        raise SandboxFencedError("sandbox belongs to a different execution or input")
    return row


def _confirmed(cursor: Any, row: dict, identity: Mapping) -> None:
    plan_version = identity.get("plan_version")
    if not isinstance(plan_version, str) or _HASH_PATTERN.fullmatch(plan_version) is None:
        raise SandboxFencedError("execute requires a confirmed plan version")
    cursor.execute(
        """
        SELECT input_type, input_text FROM analysis_job_inputs
        WHERE job_id = %s ORDER BY sequence DESC LIMIT 1 FOR UPDATE
        """,
        (row["job_id"],),
    )
    latest = cursor.fetchone()
    if (
        not latest
        or latest["input_type"] != "resume"
        or plan_confirmation_version(latest["input_text"]) != plan_version
    ):
        raise SandboxFencedError("latest Job input does not confirm this plan")


def reserve_session(
    identity: Mapping, request_id: str, request_digest: str, source_hash: str,
    capacity: int, ttl_seconds: int, *, max_lifetime_seconds: int | None = None,
) -> tuple[dict, bool]:
    """登记创建意图。仅 created=True 允许首次调用远端；重放绝不重新创建。"""
    owner = _identity(identity)
    _identifier(request_id, "request_id")
    _digest(request_digest, "request_digest")
    _digest(source_hash, "source_hash")
    _positive(capacity, "capacity")
    _positive(ttl_seconds, "ttl_seconds")
    maximum = ttl_seconds if max_lifetime_seconds is None else max_lifetime_seconds
    _positive(maximum, "max_lifetime_seconds")
    if ttl_seconds > maximum:
        raise ValueError("ttl_seconds exceeds maximum lifetime")
    with _ADMISSION_LOCK, _transaction() as cursor:
        _lock_owner(cursor, identity, source_hash)
        try:
            existing = _row(cursor, request_id, lock=True)
        except SandboxNotFoundError:
            existing = None
        if existing is not None:
            if (
                existing["request_digest"] != request_digest
                or existing["source_hash"] != source_hash
                or tuple(existing[name] for name in _OWNER_FIELDS) != owner
            ):
                raise SandboxConflictError("request_id conflicts with its original request")
            return existing, False
        cursor.execute(
            "SELECT id FROM analysis_sandbox_sessions WHERE open_job_id = %s FOR UPDATE",
            (owner[0],),
        )
        if cursor.fetchone() is not None:
            raise SandboxConflictError("Job already has an unclosed sandbox")
        cursor.execute(
            "SELECT COUNT(*) AS occupied FROM analysis_sandbox_sessions WHERE status <> 'closed'"
        )
        if int(cursor.fetchone()["occupied"]) >= capacity:
            raise SandboxCapacityError("sandbox capacity is exhausted")
        cursor.execute(
            """
            INSERT INTO analysis_sandbox_sessions
                (request_id, request_digest, source_hash, job_id, worker_id,
                 attempt_count, lease_epoch, metadata, expires_at, max_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, JSON_OBJECT(),
                    TIMESTAMPADD(SECOND, %s, UTC_TIMESTAMP(6)),
                    TIMESTAMPADD(SECOND, %s, UTC_TIMESTAMP(6)))
            """,
            (request_id, request_digest, source_hash, *owner, ttl_seconds, maximum),
        )
        return _row(cursor, request_id), True


def get_session(request_id: str) -> dict | None:
    """控制器内部主库查询；公开 API 必须另行 require_owner。"""
    with _transaction() as cursor:
        try:
            return _row(cursor, request_id)
        except SandboxNotFoundError:
            return None


def require_current_owner(identity: Mapping, source_hash: str | None = None) -> dict:
    with _transaction() as cursor:
        return _lock_owner(cursor, identity, source_hash)


def require_owner(request_id: str, identity: Mapping) -> dict:
    with _transaction() as cursor:
        return _owned_row(cursor, request_id, identity)


def require_plan_confirmation(request_id: str, identity: Mapping) -> dict:
    with _transaction() as cursor:
        row = _owned_row(cursor, request_id, identity)
        _confirmed(cursor, row, identity)
        return row


def _changes(row: dict, changes: Mapping, remote_absent_confirmed: bool) -> dict:
    allowed = {"status", "remote_sandbox_id", "metadata", "next_retry_at", "expires_at"}
    if not isinstance(changes, Mapping) or set(changes) - allowed:
        raise ValueError("unsupported sandbox session changes")
    updated = dict(changes)
    status = updated.get("status", row["status"])
    if status not in _TRANSITIONS[row["status"]]:
        raise SandboxConflictError("invalid sandbox lifecycle transition")
    if row["status"] == "closed":
        raise SandboxConflictError("closed sandbox session is immutable")
    if status == "closed" and not remote_absent_confirmed:
        raise SandboxConflictError("closing requires explicit remote absence confirmation")
    remote_id = updated.get("remote_sandbox_id", row["remote_sandbox_id"])
    if "remote_sandbox_id" in updated:
        if not isinstance(remote_id, str) or not remote_id or len(remote_id) > 255 or remote_id != remote_id.strip():
            raise ValueError("invalid remote_sandbox_id")
        if row["remote_sandbox_id"] is not None and row["remote_sandbox_id"] != remote_id:
            raise SandboxConflictError("remote sandbox identity is immutable")
    if status == "active" and not remote_id:
        raise SandboxConflictError("active requires a known initialized remote sandbox")
    if "metadata" in updated:
        if not isinstance(updated["metadata"], Mapping):
            raise ValueError("metadata must be a JSON object")
        if row["operation_id"] is not None and (
            updated["metadata"].get(_EXECUTE_PLAN_VERSION_KEY)
            != row["metadata"].get(_EXECUTE_PLAN_VERSION_KEY)
        ):
            raise SandboxConflictError("execute plan binding is immutable")
        updated["metadata"] = _json(dict(updated["metadata"]))
    for field in ("expires_at", "next_retry_at"):
        value = updated.get(field)
        if value is not None and (not isinstance(value, datetime) or value.tzinfo is not None):
            raise ValueError(f"{field} must be a naive UTC datetime")
    expiry = updated.get("expires_at", row["expires_at"])
    if expiry is None or not row["created_at"] < expiry <= row["max_expires_at"]:
        raise SandboxConflictError("sandbox expiry exceeds absolute lifetime")
    return updated


def _save(cursor: Any, row: dict, expected_version: int, updates: dict) -> dict:
    _positive(expected_version, "expected_version")
    if row["version"] != expected_version:
        raise SandboxVersionError("sandbox session version changed")
    assignments = [f"{name} = %s" for name in updates]
    assignments.extend(["version = version + 1", "updated_at = UTC_TIMESTAMP(6)"])
    if updates.get("status") == "closed":
        assignments.append("closed_at = UTC_TIMESTAMP(6)")
    cursor.execute(
        "UPDATE analysis_sandbox_sessions SET " + ", ".join(assignments)
        + " WHERE request_id = %s AND version = %s",
        (*updates.values(), row["request_id"], expected_version),
    )
    if cursor.rowcount != 1:
        raise SandboxVersionError("sandbox session version changed")
    return _row(cursor, row["request_id"])


def update_session(
    request_id: str, *, expected_version: int, changes: Mapping,
    remote_absent_confirmed: bool = False,
) -> dict:
    """控制器回执/核查入口，允许 Job 已被删除；不授予用户执行权限。"""
    with _transaction() as cursor:
        row = _row(cursor, request_id, lock=True)
        return _save(cursor, row, expected_version, _changes(row, changes, remote_absent_confirmed))


def begin_operation(
    request_id: str, identity: Mapping, *, operation_id: str,
    operation_type: str, operation_digest: str,
) -> tuple[dict, bool]:
    """仅第一次登记 execute 返回 started=True；未知/失败结果绝不授权重跑。"""
    _identifier(operation_id, "operation_id")
    _digest(operation_digest, "operation_digest")
    if operation_type != "execute":
        raise ValueError("only execute is persisted as a sandbox operation")
    with _transaction() as cursor:
        row = _owned_row(cursor, request_id, identity)
        _confirmed(cursor, row, identity)
        if row["operation_id"] is not None:
            if (
                (row["operation_id"], row["operation_digest"]) != (operation_id, operation_digest)
                or row["metadata"].get(_EXECUTE_PLAN_VERSION_KEY) != identity["plan_version"]
            ):
                raise SandboxConflictError("sandbox permits only its original execute operation")
            return row, False
        if row["status"] != "active" or row["expired"]:
            raise SandboxConflictError("execute requires a live initialized sandbox")
        updates = {
            "operation_id": operation_id, "operation_type": operation_type,
            "operation_digest": operation_digest, "operation_status": "pending",
            "metadata": _json({**row["metadata"], _EXECUTE_PLAN_VERSION_KEY: identity["plan_version"]}),
        }
        return _save(cursor, row, row["version"], updates), True


def finish_operation(
    request_id: str, *, operation_id: str, operation_digest: str,
    expected_version: int, operation_status: str, operation_result: dict | None,
    changes: Mapping | None = None, remote_absent_confirmed: bool = False,
) -> dict:
    """按 version + operation id/digest 保存回执，旧 Worker 无法借此获得执行权限。"""
    _identifier(operation_id, "operation_id")
    _digest(operation_digest, "operation_digest")
    if operation_status not in {"succeeded", "uncertain"}:
        raise ValueError("execute completion must be succeeded or uncertain")
    if operation_result is not None and not isinstance(operation_result, dict):
        raise ValueError("operation_result must be a JSON object or None")
    with _transaction() as cursor:
        row = _row(cursor, request_id, lock=True)
        if (row["operation_id"], row["operation_digest"]) != (operation_id, operation_digest):
            raise SandboxConflictError("execute completion identity does not match")
        if row["status"] == "closed":
            raise SandboxConflictError("closed sandbox session is immutable")
        if row["operation_status"] == "succeeded":
            raise SandboxConflictError("completed execute result is immutable")
        updates = _changes(row, changes or {}, remote_absent_confirmed)
        updates.update(operation_status=operation_status, operation_result=_json(operation_result))
        return _save(cursor, row, expected_version, updates)


def heartbeat_session(request_id: str, identity: Mapping) -> dict:
    """只更新使用方心跳与版本；不续期、不覆盖执行操作和结果。"""
    with _transaction() as cursor:
        row = _owned_row(cursor, request_id, identity)
        if row["status"] not in {"creating", "active"} or row["expired"]:
            raise SandboxConflictError("sandbox no longer accepts heartbeats")
        cursor.execute(
            """UPDATE analysis_sandbox_sessions
               SET heartbeat_at = UTC_TIMESTAMP(6), updated_at = UTC_TIMESTAMP(6), version = version + 1
               WHERE request_id = %s AND version = %s""",
            (request_id, row["version"]),
        )
        return _row(cursor, request_id)


def count_non_closed_sessions() -> int:
    with _transaction() as cursor:
        cursor.execute("SELECT COUNT(*) AS occupied FROM analysis_sandbox_sessions WHERE status <> 'closed'")
        return int(cursor.fetchone()["occupied"])


def list_open_sessions(*, after_id: int = 0, limit: int = 100) -> list[dict]:
    """启动恢复用主库 keyset 分页，包含所有尚未确认关闭的状态。"""
    if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
        raise ValueError("after_id must be a nonnegative integer")
    _positive(limit, "limit")
    if limit > 1000:
        raise ValueError("limit exceeds 1000")
    with _transaction() as cursor:
        cursor.execute(
            "SELECT request_id FROM analysis_sandbox_sessions WHERE status <> 'closed' AND id > %s ORDER BY id LIMIT %s",
            (after_id, limit),
        )
        request_ids = [row["request_id"] for row in cursor.fetchall()]
        return [_row(cursor, request_id) for request_id in request_ids]


def list_reconciliation_candidates(*, heartbeat_timeout_seconds: int, limit: int = 100) -> list[dict]:
    """列出到期、失联、未完成操作或已失去 Job 所有权的待核查资源。"""
    _positive(heartbeat_timeout_seconds, "heartbeat_timeout_seconds")
    _positive(limit, "limit")
    if limit > 1000:
        raise ValueError("limit exceeds 1000")
    with _transaction() as cursor:
        cursor.execute(
            """
            SELECT s.request_id FROM analysis_sandbox_sessions AS s
            LEFT JOIN analysis_jobs AS j ON j.job_id COLLATE utf8mb4_bin = s.job_id
            WHERE s.status <> 'closed'
              AND (s.next_retry_at IS NULL OR s.next_retry_at <= UTC_TIMESTAMP(6))
              AND (s.status IN ('creating', 'deleting', 'uncertain')
                   OR s.operation_status IN ('pending', 'uncertain')
                   OR s.expires_at <= UTC_TIMESTAMP(6)
                   OR s.heartbeat_at <= TIMESTAMPADD(SECOND, -%s, UTC_TIMESTAMP(6))
                   OR j.job_id IS NULL OR j.status <> 'running' OR j.execution_state IS NULL
                   OR j.execution_state <> 'leased'
                   OR j.worker_id IS NULL OR j.worker_id COLLATE utf8mb4_bin <> s.worker_id
                   OR j.attempt_count <> s.attempt_count OR j.lease_epoch <> s.lease_epoch
                   OR j.input_file_hash IS NULL OR j.input_file_hash <> s.source_hash
                   OR j.heartbeat_at IS NULL
                   OR j.heartbeat_at <= TIMESTAMPADD(SECOND, -%s, UTC_TIMESTAMP(6)))
            ORDER BY s.id LIMIT %s
            """,
            (heartbeat_timeout_seconds, int(os.environ.get("JOB_STALE_AFTER_SECONDS", "120")), limit),
        )
        request_ids = [row["request_id"] for row in cursor.fetchall()]
        return [_row(cursor, request_id) for request_id in request_ids]
