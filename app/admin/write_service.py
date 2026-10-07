"""管理员 3.2 受控用户与文件写入服务。

所有高风险变更都在主库事务内完成，并同时写入幂等操作、逐目标结果和
去敏审计；密码明文、密码哈希、Cookie、Token 和文件正文不会进入结果。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
import hmac
import json
import logging
import re
from typing import Any
from uuid import uuid4

import mysql.connector
from mysql.connector import errorcode

from app.admin.audit_service import insert_admin_audit_event
from app.admin.contracts import AdminApiError
from app.auth.service import (
    hash_password,
    managed_password_error,
    verify_password,
)
from app.auth.rbac import replace_user_roles, role_keys_for_primary_role
from app.agent.persistence_cleanup import (
    enqueue_checkpoint_cleanup_many,
    enqueue_user_memory_cleanup,
)
from app.db import get_read_connection, get_write_connection, record_database_failure
from app.request_context import get_request_id
from config.settings import settings
from observability.logging_runtime import log_context, log_event


USER_OPERATION_TYPES = {"set_active", "set_role", "set_password"}
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
RETRYABLE_DATABASE_ERRORS = {
    errorcode.ER_LOCK_DEADLOCK,
    errorcode.ER_LOCK_WAIT_TIMEOUT,
}
LOGGER = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    """把操作结果中的常见数据库值转换为 JSON。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"无法序列化操作值类型: {type(value).__name__}")


def _json_dumps(value: Any) -> str:
    """编码不含秘密的管理员操作快照。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_loads(value: Any) -> Any:
    """兼容 MySQL JSON 返回的对象、字符串和字节形式。"""
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value)


def _require_body(body: Any) -> dict[str, Any]:
    """要求管理员写请求使用 JSON 对象。"""
    if not isinstance(body, dict):
        raise AdminApiError(
            "invalid_body",
            "请求正文必须是 JSON 对象",
            fields={"body": "必须是 JSON 对象"},
        )
    return body


def _parse_target_ids(raw_ids: Any) -> list[int]:
    """解析、去重并限制批量用户 ID。"""
    if not isinstance(raw_ids, list) or not raw_ids:
        raise AdminApiError(
            "invalid_body",
            "target_ids 必须是非空数组",
            fields={"target_ids": "必须是非空数组"},
        )
    target_ids: list[int] = []
    seen: set[int] = set()
    for value in raw_ids:
        if isinstance(value, bool):
            raise AdminApiError(
                "invalid_body",
                "target_ids 只能包含正整数",
                fields={"target_ids": "只能包含正整数"},
            )
        try:
            target_id = int(value)
        except (TypeError, ValueError) as exc:
            raise AdminApiError(
                "invalid_body",
                "target_ids 只能包含正整数",
                fields={"target_ids": "只能包含正整数"},
            ) from exc
        if target_id <= 0:
            raise AdminApiError(
                "invalid_body",
                "target_ids 只能包含正整数",
                fields={"target_ids": "只能包含正整数"},
            )
        if target_id not in seen:
            seen.add(target_id)
            target_ids.append(target_id)
    if len(target_ids) > settings.ADMIN_BATCH_MAX_TARGETS:
        raise AdminApiError(
            "batch_limit_exceeded",
            f"单次最多操作 {settings.ADMIN_BATCH_MAX_TARGETS} 个用户",
            status=413,
            fields={
                "target_ids": f"不能超过 {settings.ADMIN_BATCH_MAX_TARGETS} 个用户"
            },
        )
    return sorted(target_ids)


def _parse_user_action(body: dict[str, Any]) -> tuple[str, list[int], Any]:
    """解析统一用户批量操作及其目标值。"""
    action = body.get("action")
    if action not in USER_OPERATION_TYPES:
        raise AdminApiError(
            "invalid_body",
            "action 仅支持 set_active、set_role、set_password",
            fields={"action": "操作类型无效"},
        )
    target_ids = _parse_target_ids(body.get("target_ids"))
    value = body.get("value")
    if action == "set_active" and not isinstance(value, bool):
        raise AdminApiError(
            "invalid_body",
            "set_active 的 value 必须是布尔值",
            fields={"value": "必须是布尔值"},
        )
    if action == "set_role" and value not in {"user", "admin"}:
        raise AdminApiError(
            "invalid_body",
            "set_role 的 value 仅支持 user/admin",
            fields={"value": "仅支持 user/admin"},
        )
    if action == "set_password":
        value = body.get("new_password")
        password_error = managed_password_error(value)
        if password_error:
            raise AdminApiError(
                "password_policy_failed",
                password_error,
                fields={"new_password": password_error},
            )
    return action, target_ids, value


def _parse_idempotency_key(value: str | None) -> str:
    """验证写请求头中的幂等键。"""
    normalized = (value or "").strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(normalized):
        raise AdminApiError(
            "idempotency_key_invalid",
            "Idempotency-Key 必须是 16 到 128 位安全字符",
            status=400,
            fields={"Idempotency-Key": "仅支持字母、数字、点、下划线、冒号和短横线"},
        )
    return normalized


def _request_fingerprint(operation_type: str, body: dict[str, Any]) -> str:
    """用服务端密钥 HMAC 请求正文，防止幂等记录泄露低熵密码摘要。"""
    canonical = _json_dumps({
        "operation_type": operation_type,
        "body": body,
    }).encode("utf-8")
    secret = str(settings.SECRET_KEY).encode("utf-8")
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def _log_reauthentication_failure(actor: dict[str, Any], action: str) -> None:
    with log_context(user_id=actor.get("id")):
        log_event(
            LOGGER,
            "security.reauthentication.failed",
            details={
                "action": action,
                "reason_code": "reauthentication_failed",
            },
        )


def _validate_reauthentication(
    actor: dict[str, Any],
    password: Any,
    *,
    action: str,
) -> str:
    """在主库重新校验当前管理员密码，不返回或记录明文。"""
    if not isinstance(password, str) or not password:
        _log_reauthentication_failure(actor, action)
        raise AdminApiError(
            "reauth_required",
            "必须输入当前管理员密码重新认证",
            status=401,
            fields={"reauth_password": "不能为空"},
        )
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, role, is_active, password_hash
            FROM users
            WHERE id = %s
            """,
            (actor["id"],),
        )
        row = cursor.fetchone()
    if (
        not row
        or row.get("role") != "admin"
        or not row.get("is_active")
        or not verify_password(password, row.get("password_hash"))
    ):
        _log_reauthentication_failure(actor, action)
        raise AdminApiError(
            "reauth_failed",
            "当前管理员密码不正确或账号状态已变化",
            status=401,
            fields={"reauth_password": "重新认证失败"},
        )
    return password


def _configure_transaction(connection, cursor) -> None:
    """设置高风险事务的有界锁等待并启动主库事务。"""
    timeout = int(settings.ADMIN_DB_LOCK_WAIT_TIMEOUT_SECONDS)
    cursor.execute(f"SET SESSION innodb_lock_wait_timeout = {timeout}")
    connection.start_transaction(isolation_level="READ COMMITTED")


def _lock_and_reauthenticate_actor(
    cursor,
    *,
    actor: dict[str, Any],
    password: str,
    action: str,
) -> dict[str, Any]:
    """锁定操作者并在事务内再次确认管理员身份和密码。"""
    cursor.execute(
        """
        SELECT id, username, role, is_active, password_hash, auth_version
        FROM users
        WHERE id = %s
        FOR UPDATE
        """,
        (actor["id"],),
    )
    row = cursor.fetchone()
    if (
        not row
        or row.get("role") != "admin"
        or not row.get("is_active")
        or not verify_password(password, row.get("password_hash"))
    ):
        _log_reauthentication_failure(actor, action)
        raise AdminApiError(
            "reauth_failed",
            "当前管理员密码不正确或账号状态已变化",
            status=401,
            fields={"reauth_password": "重新认证失败"},
        )
    return row


def _load_existing_operation(
    *,
    actor_id: int,
    idempotency_key: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    """读取已提交操作；同键异参冲突，同键同参返回原结果。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT operation_id, request_fingerprint, status, result_json
            FROM admin_operations
            WHERE actor_user_id = %s AND idempotency_key = %s
            """,
            (actor_id, idempotency_key),
        )
        row = cursor.fetchone()
    return _decode_existing_operation(row, fingerprint=fingerprint)


def _decode_existing_operation(
    row: dict[str, Any] | None,
    *,
    fingerprint: str,
) -> dict[str, Any] | None:
    """校验并解码一条幂等操作记录，且不暴露请求指纹或秘密字段。"""
    if not row:
        return None
    if not hmac.compare_digest(str(row["request_fingerprint"]), fingerprint):
        raise AdminApiError(
            "idempotency_conflict",
            "该 Idempotency-Key 已用于不同请求",
            status=409,
        )
    if row.get("status") in {"running", "failed"}:
        result = _json_loads(row.get("result_json")) or {}
        result["operation_id"] = row["operation_id"]
        result["status"] = row["status"]
        result["replayed"] = True
        return result
    if row.get("status") != "succeeded" or row.get("result_json") is None:
        raise AdminApiError(
            "operation_incomplete",
            "该幂等操作尚未形成可重放结果，请稍后使用同一请求重试",
            status=409,
        )
    result = _json_loads(row["result_json"]) or {}
    result["operation_id"] = row["operation_id"]
    result["replayed"] = True
    return result


def _load_existing_operation_for_update(
    cursor,
    *,
    actor_id: int,
    idempotency_key: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    """在已锁操作者的事务内回看并发刚提交的同一幂等操作。"""
    cursor.execute(
        """
        SELECT operation_id, request_fingerprint, status, result_json
        FROM admin_operations
        WHERE actor_user_id = %s AND idempotency_key = %s
        """,
        (actor_id, idempotency_key),
    )
    return _decode_existing_operation(
        cursor.fetchone(),
        fingerprint=fingerprint,
    )


def _insert_operation(
    cursor,
    *,
    operation_id: str,
    actor: dict[str, Any],
    operation_type: str,
    idempotency_key: str,
    fingerprint: str,
    target_count: int,
) -> None:
    """在调用方事务中登记幂等操作主记录。"""
    cursor.execute(
        """
        INSERT INTO admin_operations (
            operation_id, actor_user_id, actor_username, operation_type,
            idempotency_key, request_fingerprint, status, target_count,
            request_id
        ) VALUES (%s, %s, %s, %s, %s, %s, 'running', %s, %s)
        """,
        (
            operation_id,
            actor["id"],
            actor["username"],
            operation_type,
            idempotency_key,
            fingerprint,
            target_count,
            get_request_id(),
        ),
    )


def _insert_operation_item(
    cursor,
    *,
    operation_id: str,
    target_type: str,
    target_id: str,
    target_label: str | None,
    old_values: dict[str, Any] | None,
    new_values: dict[str, Any] | None,
) -> None:
    """在调用方事务中登记一条去敏的逐目标成功结果。"""
    cursor.execute(
        """
        INSERT INTO admin_operation_items (
            operation_id, target_type, target_id, target_label, result,
            old_values_json, new_values_json
        ) VALUES (%s, %s, %s, %s, 'success', %s, %s)
        """,
        (
            operation_id,
            target_type,
            target_id,
            target_label,
            _json_dumps(old_values) if old_values is not None else None,
            _json_dumps(new_values) if new_values is not None else None,
        ),
    )


def _complete_operation(
    cursor,
    *,
    operation_id: str,
    result: dict[str, Any],
    target_count: int,
) -> None:
    """把操作主记录原子推进到成功终态。"""
    cursor.execute(
        """
        UPDATE admin_operations
        SET status = 'succeeded',
            succeeded_count = %s,
            failed_count = 0,
            result_json = %s,
            completed_at = UTC_TIMESTAMP(6)
        WHERE operation_id = %s AND status = 'running'
        """,
        (target_count, _json_dumps(result), operation_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("管理员操作终态更新失败")


def _retry_existing_after_duplicate(
    exc: mysql.connector.Error,
    *,
    actor_id: int,
    idempotency_key: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    """唯一键竞争时回读已提交的同一幂等操作。"""
    if exc.errno != errorcode.ER_DUP_ENTRY:
        return None
    return _load_existing_operation(
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


def _raise_database_error(exc: mysql.connector.Error) -> None:
    """把锁超时/死锁标记为可用原幂等键重试的稳定错误。"""
    if exc.errno in RETRYABLE_DATABASE_ERRORS:
        raise AdminApiError(
            "transaction_retryable",
            "事务发生锁冲突，请使用相同 Idempotency-Key 重试",
            status=409,
        ) from exc
    raise exc


def _fetch_users_for_update(cursor, target_ids: list[int]) -> list[dict[str, Any]]:
    """按稳定 ID 顺序锁定全部目标用户，并拒绝部分命中。"""
    placeholders = ", ".join(["%s"] * len(target_ids))
    cursor.execute(
        f"""
        SELECT id, username, role, is_active, auth_version, password_changed_at
        FROM users
        WHERE id IN ({placeholders})
        ORDER BY id
        FOR UPDATE
        """,
        tuple(target_ids),
    )
    rows = cursor.fetchall()
    found = {int(row["id"]) for row in rows}
    missing = [target_id for target_id in target_ids if target_id not in found]
    if missing:
        raise AdminApiError(
            "target_not_found",
            "部分目标用户不存在",
            status=404,
            fields={"target_ids": f"不存在的用户 ID：{', '.join(map(str, missing))}"},
        )
    return rows


def _enabled_admin_ids_for_update(cursor) -> set[int]:
    """锁定所有启用管理员，串行保护最后管理员规则。"""
    cursor.execute(
        """
        SELECT id
        FROM users
        WHERE role = 'admin' AND is_active = TRUE
        ORDER BY id
        FOR UPDATE
        """
    )
    return {int(row["id"]) for row in cursor.fetchall()}


def _assert_admin_safety(
    *,
    action: str,
    value: Any,
    actor_id: int,
    targets: list[dict[str, Any]],
    enabled_admin_ids: set[int],
) -> None:
    """禁止自我禁用/降级，并保证提交后仍有启用管理员。"""
    target_ids = {int(row["id"]) for row in targets}
    if (
        actor_id in target_ids
        and (
            (action == "set_active" and value is False)
            or (action == "set_role" and value == "user")
        )
    ):
        raise AdminApiError(
            "self_protected",
            "不能禁用或降级当前操作者",
            status=409,
        )
    remaining = set(enabled_admin_ids)
    if action == "set_active" and value is False:
        remaining.difference_update(
            int(row["id"])
            for row in targets
            if row["role"] == "admin" and row["is_active"]
        )
    if action == "set_role" and value == "user":
        remaining.difference_update(
            int(row["id"])
            for row in targets
            if row["role"] == "admin" and row["is_active"]
        )
    if not remaining:
        raise AdminApiError(
            "last_admin_protected",
            "操作会移除最后一个启用管理员",
            status=409,
        )


def preview_user_operation(
    body: Any,
    *,
    actor: dict[str, Any],
) -> dict[str, Any]:
    """在主库强一致读取批量变更预览，不返回或接收密码明文。"""
    request_body = _require_body(body)
    preview_body = dict(request_body)
    if preview_body.get("action") == "set_password":
        preview_body["new_password"] = "preview-placeholder-password"
    action, target_ids, value = _parse_user_action(preview_body)
    placeholders = ", ".join(["%s"] * len(target_ids))
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT id, username, role, is_active
            FROM users
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            tuple(target_ids),
        )
        rows = cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) AS count_value FROM users "
            "WHERE role = 'admin' AND is_active = TRUE"
        )
        enabled_admin_count = int((cursor.fetchone() or {}).get("count_value") or 0)
    found = {int(row["id"]) for row in rows}
    missing = [target_id for target_id in target_ids if target_id not in found]
    if missing:
        raise AdminApiError(
            "target_not_found",
            "部分目标用户不存在",
            status=404,
            fields={"target_ids": f"不存在的用户 ID：{', '.join(map(str, missing))}"},
        )

    removed_enabled_admins = 0
    items = []
    for row in rows:
        current = {
            "role": row["role"],
            "is_active": bool(row["is_active"]),
        }
        next_values = dict(current)
        blockers = []
        if action == "set_active":
            next_values["is_active"] = bool(value)
            if int(row["id"]) == int(actor["id"]) and value is False:
                blockers.append("不能禁用当前操作者")
        elif action == "set_role":
            next_values["role"] = value
            if int(row["id"]) == int(actor["id"]) and value == "user":
                blockers.append("不能降级当前操作者")
        else:
            next_values = {"password_changed": True}
        if (
            row["role"] == "admin"
            and bool(row["is_active"])
            and (
                (action == "set_active" and value is False)
                or (action == "set_role" and value == "user")
            )
        ):
            removed_enabled_admins += 1
        items.append({
            "id": row["id"],
            "username": row["username"],
            "current": current,
            "next": next_values,
            "blockers": blockers,
        })
    if enabled_admin_count - removed_enabled_admins < 1:
        for item in items:
            item["blockers"].append("操作会移除最后一个启用管理员")
    return {
        "action": action,
        "target_count": len(items),
        "items": items,
        "can_execute": not any(item["blockers"] for item in items),
        "requires_reauthentication": True,
        "batch_limit": settings.ADMIN_BATCH_MAX_TARGETS,
    }


def execute_user_operation(
    body: Any,
    *,
    actor: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    """原子执行用户启停、角色或批量同密码变更。"""
    request_body = _require_body(body)
    action, target_ids, value = _parse_user_action(request_body)
    if request_body.get("confirmed") is not True:
        raise AdminApiError(
            "confirmation_required",
            "必须先查看预览并明确确认",
            fields={"confirmed": "必须为 true"},
        )
    key = _parse_idempotency_key(idempotency_key)
    operation_type = f"user.{action}"
    fingerprint = _request_fingerprint(operation_type, request_body)
    existing = _load_existing_operation(
        actor_id=int(actor["id"]),
        idempotency_key=key,
        fingerprint=fingerprint,
    )
    if existing is not None:
        return existing
    reauth_password = _validate_reauthentication(
        actor,
        request_body.get("reauth_password"),
        action=operation_type,
    )
    password_hashes = (
        [hash_password(value) for _target_id in target_ids]
        if action == "set_password"
        else []
    )
    operation_id = str(uuid4())
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        _configure_transaction(connection, cursor)
        _lock_and_reauthenticate_actor(
            cursor,
            actor=actor,
            password=reauth_password,
            action=operation_type,
        )
        replay = _load_existing_operation_for_update(
            cursor,
            actor_id=int(actor["id"]),
            idempotency_key=key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            connection.rollback()
            return replay
        enabled_admin_ids = _enabled_admin_ids_for_update(cursor)
        targets = _fetch_users_for_update(cursor, target_ids)
        _assert_admin_safety(
            action=action,
            value=value,
            actor_id=int(actor["id"]),
            targets=targets,
            enabled_admin_ids=enabled_admin_ids,
        )
        _insert_operation(
            cursor,
            operation_id=operation_id,
            actor=actor,
            operation_type=operation_type,
            idempotency_key=key,
            fingerprint=fingerprint,
            target_count=len(targets),
        )

        result_items = []
        for index, target in enumerate(targets):
            old_values = {
                "role": target["role"],
                "is_active": bool(target["is_active"]),
                "auth_version": int(target.get("auth_version") or 1),
            }
            changed = False
            if action == "set_active":
                changed = bool(target["is_active"]) != bool(value)
                if changed:
                    cursor.execute(
                        """
                        UPDATE users
                        SET is_active = %s, auth_version = auth_version + 1
                        WHERE id = %s
                        """,
                        (value, target["id"]),
                    )
                new_values = {
                    **old_values,
                    "is_active": bool(value),
                    "auth_version": old_values["auth_version"] + int(changed),
                    "changed": changed,
                }
            elif action == "set_role":
                changed = target["role"] != value
                if changed:
                    cursor.execute(
                        """
                        UPDATE users
                        SET role = %s, auth_version = auth_version + 1
                        WHERE id = %s
                        """,
                        (value, target["id"]),
                    )
                    replace_user_roles(
                        cursor,
                        int(target["id"]),
                        role_keys_for_primary_role(value),
                    )
                new_values = {
                    **old_values,
                    "role": value,
                    "auth_version": old_values["auth_version"] + int(changed),
                    "changed": changed,
                }
            else:
                changed = True
                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash = %s,
                        password_changed_at = UTC_TIMESTAMP(6),
                        auth_version = auth_version + 1
                    WHERE id = %s
                    """,
                    (password_hashes[index], target["id"]),
                )
                old_values = {
                    "password_changed_at": target.get("password_changed_at"),
                    "auth_version": old_values["auth_version"],
                }
                new_values = {
                    "password_changed": True,
                    "auth_version": old_values["auth_version"] + 1,
                    "changed": True,
                }
            _insert_operation_item(
                cursor,
                operation_id=operation_id,
                target_type="user",
                target_id=str(target["id"]),
                target_label=target["username"],
                old_values=old_values,
                new_values=new_values,
            )
            insert_admin_audit_event(
                cursor,
                actor=actor,
                action=operation_type,
                target_type="user",
                target_id=str(target["id"]),
                old_values=old_values,
                new_values=new_values,
                result="success",
                error_code=None,
                request_id=get_request_id(),
            )
            result_items.append({
                "id": target["id"],
                "username": target["username"],
                "changed": changed,
                "role": new_values.get("role", target["role"]),
                "is_active": new_values.get(
                    "is_active",
                    bool(target["is_active"]),
                ),
                "auth_version": new_values["auth_version"],
            })
        result = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "target_count": len(result_items),
            "items": result_items,
            "replayed": False,
        }
        _complete_operation(
            cursor,
            operation_id=operation_id,
            result=result,
            target_count=len(result_items),
        )
        connection.commit()
        return result
    except mysql.connector.Error as exc:
        connection.rollback()
        replay = _retry_existing_after_duplicate(
            exc,
            actor_id=int(actor["id"]),
            idempotency_key=key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        record_database_failure(exc, operation="admin_user_write")
        _raise_database_error(exc)
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _user_impact(cursor, user: dict[str, Any]) -> dict[str, Any]:
    """计算用户物理删除会影响的全部阶段一业务记录。"""
    user_id = int(user["id"])
    cursor.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM sessions WHERE user_id = %s) AS sessions,
            (SELECT COUNT(*) FROM chat_messages WHERE user_id = %s) AS messages,
            (
                SELECT COUNT(*)
                FROM chat_attachments AS a
                JOIN chat_messages AS m ON m.id = a.message_id
                WHERE m.user_id = %s
            ) AS attachments,
            (SELECT COUNT(*) FROM user_files WHERE user_id = %s) AS files,
            (SELECT COUNT(*) FROM analysis_jobs WHERE user_id = %s) AS jobs,
            (
                SELECT COUNT(*)
                FROM analysis_job_events AS e
                JOIN analysis_jobs AS j ON j.job_id = e.job_id
                WHERE j.user_id = %s
            ) AS events,
            (SELECT COUNT(*) FROM archived_sessions WHERE user_id = %s) AS archived_sessions,
            (
                SELECT COUNT(*)
                FROM checkpoint_cleanup_outbox AS o
                JOIN analysis_jobs AS j ON j.job_id = o.thread_id
                WHERE j.user_id = %s AND o.status <> 'succeeded'
            ) AS checkpoint_cleanup_pending,
            (
                SELECT COUNT(*)
                FROM user_memory_cleanup_outbox
                WHERE user_id = %s AND status <> 'succeeded'
            ) AS user_memory_cleanup_pending,
            (
                SELECT COUNT(*)
                FROM analysis_jobs
                WHERE user_id = %s
                  AND (
                      status IN ('queued', 'running', 'waiting_input')
                      OR execution_state IN ('leased', 'draining')
                  )
            ) AS active_jobs
        """,
        (user_id,) * 10,
    )
    counts = {
        key: int(value or 0)
        for key, value in (cursor.fetchone() or {}).items()
    }
    related_keys = [
        "sessions",
        "messages",
        "attachments",
        "files",
        "jobs",
        "events",
        "archived_sessions",
    ]
    counts["total_related_rows"] = sum(counts.get(key, 0) for key in related_keys)
    return counts


def get_user_delete_impact(
    user_id: int,
    *,
    actor: dict[str, Any],
) -> dict[str, Any]:
    """主库强一致预览用户删除影响和所有安全阻断原因。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, username, role, is_active, created_at, last_login_at
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        )
        user = cursor.fetchone()
        if not user:
            raise AdminApiError("not_found", "用户不存在", 404)
        impact = _user_impact(cursor, user)
        cursor.execute(
            "SELECT COUNT(*) AS count_value FROM users "
            "WHERE role = 'admin' AND is_active = TRUE"
        )
        enabled_admin_count = int((cursor.fetchone() or {}).get("count_value") or 0)
    blockers = []
    if int(user_id) == int(actor["id"]):
        blockers.append("不能删除当前操作者")
    if user["role"] == "admin" and user["is_active"] and enabled_admin_count <= 1:
        blockers.append("不能删除最后一个启用管理员")
    if impact["active_jobs"]:
        blockers.append("目标用户仍有活动或 draining 执行中的分析任务")
    if impact["total_related_rows"] > settings.ADMIN_DELETE_MAX_RELATED_ROWS:
        blockers.append(
            "关联记录超过同步删除安全上限，需在维护窗口使用专用流程处理"
        )
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "is_active": bool(user["is_active"]),
            "created_at": user["created_at"],
            "last_login_at": user["last_login_at"],
        },
        "impact": impact,
        "can_delete": not blockers,
        "blockers": blockers,
        "requires_confirmation": user["username"],
        "requires_reauthentication": True,
        "synchronous_delete_limit": settings.ADMIN_DELETE_MAX_RELATED_ROWS,
    }


def delete_user(
    user_id: int,
    body: Any,
    *,
    actor: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    """在主库事务中删除用户业务数据，并登记 checkpoint 与长期记忆清理。"""
    request_body = _require_body(body)
    if request_body.get("confirmed") is not True:
        raise AdminApiError(
            "confirmation_required",
            "必须先查看影响预览并明确确认",
            fields={"confirmed": "必须为 true"},
        )
    key = _parse_idempotency_key(idempotency_key)
    operation_type = "user.delete"
    fingerprint = _request_fingerprint(
        operation_type,
        {"user_id": user_id, **request_body},
    )
    existing = _load_existing_operation(
        actor_id=int(actor["id"]),
        idempotency_key=key,
        fingerprint=fingerprint,
    )
    if existing is not None:
        return existing
    reauth_password = _validate_reauthentication(
        actor,
        request_body.get("reauth_password"),
        action=operation_type,
    )
    operation_id = str(uuid4())
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        _configure_transaction(connection, cursor)
        _lock_and_reauthenticate_actor(
            cursor,
            actor=actor,
            password=reauth_password,
            action=operation_type,
        )
        replay = _load_existing_operation_for_update(
            cursor,
            actor_id=int(actor["id"]),
            idempotency_key=key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            connection.rollback()
            return replay
        enabled_admin_ids = _enabled_admin_ids_for_update(cursor)
        cursor.execute(
            """
            SELECT id, username, role, is_active, created_at, last_login_at
            FROM users
            WHERE id = %s
            FOR UPDATE
            """,
            (user_id,),
        )
        user = cursor.fetchone()
        if not user:
            raise AdminApiError("not_found", "用户不存在", 404)
        if int(user_id) == int(actor["id"]):
            raise AdminApiError(
                "self_protected",
                "不能删除当前操作者",
                status=409,
            )
        if (
            user["role"] == "admin"
            and user["is_active"]
            and len(enabled_admin_ids) <= 1
        ):
            raise AdminApiError(
                "last_admin_protected",
                "不能删除最后一个启用管理员",
                status=409,
            )
        if request_body.get("confirm_username") != user["username"]:
            raise AdminApiError(
                "confirmation_mismatch",
                "确认用户名与目标用户不一致",
                fields={"confirm_username": "必须完整输入目标用户名"},
            )
        cursor.execute(
            """
            SELECT job_id, status, execution_state
            FROM analysis_jobs
            WHERE user_id = %s
            ORDER BY id
            FOR UPDATE
            """,
            (user_id,),
        )
        job_rows = cursor.fetchall()
        if any(
            row["status"] in {"queued", "running", "waiting_input"}
            or row.get("execution_state") in {"leased", "draining"}
            for row in job_rows
        ):
            raise AdminApiError(
                "active_jobs_block_delete",
                "目标用户仍有活动或 draining 执行中的分析任务",
                status=409,
            )
        impact = _user_impact(cursor, user)
        if impact["total_related_rows"] > settings.ADMIN_DELETE_MAX_RELATED_ROWS:
            raise AdminApiError(
                "delete_too_large",
                "关联记录超过同步删除安全上限",
                status=409,
            )
        _insert_operation(
            cursor,
            operation_id=operation_id,
            actor=actor,
            operation_type=operation_type,
            idempotency_key=key,
            fingerprint=fingerprint,
            target_count=1,
        )
        cleanup_count = enqueue_checkpoint_cleanup_many(
            cursor,
            [str(row["job_id"]) for row in job_rows],
            operation_id=operation_id,
        )
        memory_cleanup_count = (
            1
            if enqueue_user_memory_cleanup(
                cursor,
                user_id,
                operation_id=operation_id,
            )
            else 0
        )
        cleanup_total = cleanup_count + memory_cleanup_count
        persistence_cleanup = {
            "checkpoint_cleanup": {
                "status": "pending" if cleanup_count else "succeeded",
                "total": cleanup_count,
                "succeeded": 0,
                "failed": 0,
                "pending": cleanup_count,
            },
            "user_memory_cleanup": {
                "status": "pending" if memory_cleanup_count else "succeeded",
                "total": memory_cleanup_count,
                "succeeded": 0,
                "failed": 0,
                "pending": memory_cleanup_count,
            },
        }
        cursor.execute(
            "SELECT id FROM file_objects WHERE owner_user_id = %s FOR UPDATE",
            (user_id,),
        )
        object_ids = [int(row["id"]) for row in cursor.fetchall()]
        cursor.execute("DELETE FROM user_files WHERE user_id = %s", (user_id,))
        deleted_user_files = cursor.rowcount
        deleted_file_objects = 0
        for object_id in object_ids:
            cursor.execute(
                "SELECT COUNT(*) AS reference_count FROM user_files WHERE object_id = %s",
                (object_id,),
            )
            if int((cursor.fetchone() or {}).get("reference_count") or 0) == 0:
                cursor.execute("DELETE FROM file_objects WHERE id = %s", (object_id,))
                deleted_file_objects += cursor.rowcount
        cursor.execute(
            "DELETE FROM archived_sessions WHERE user_id = %s",
            (user_id,),
        )
        deleted_archives = cursor.rowcount
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        if cursor.rowcount != 1:
            raise RuntimeError("用户删除影响行数异常")
        old_values = {
            "username": user["username"],
            "role": user["role"],
            "is_active": bool(user["is_active"]),
            "impact": impact,
        }
        new_values = {
            "deleted": True,
            "deleted_archived_sessions": deleted_archives,
            "deleted_user_files": deleted_user_files,
            "deleted_file_objects": deleted_file_objects,
            **persistence_cleanup,
        }
        _insert_operation_item(
            cursor,
            operation_id=operation_id,
            target_type="user",
            target_id=str(user_id),
            target_label=user["username"],
            old_values=old_values,
            new_values=new_values,
        )
        insert_admin_audit_event(
            cursor,
            actor=actor,
            action=operation_type,
            target_type="user",
            target_id=str(user_id),
            old_values=old_values,
            new_values=new_values,
            result="success",
            error_code=None,
            request_id=get_request_id(),
        )
        result = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "target_count": 1,
            "user_id": user_id,
            "username": user["username"],
            "deleted": True,
            "impact": impact,
            "status": "running" if cleanup_total else "succeeded",
            **persistence_cleanup,
            "replayed": False,
        }
        cursor.execute(
            """
            UPDATE admin_operations
            SET result_json = %s
            WHERE operation_id = %s AND status = 'running'
            """,
            (_json_dumps(result), operation_id),
        )
        if not cleanup_total:
            _complete_operation(
                cursor,
                operation_id=operation_id,
                result=result,
                target_count=1,
            )
        connection.commit()
        return result
    except mysql.connector.Error as exc:
        connection.rollback()
        replay = _retry_existing_after_duplicate(
            exc,
            actor_id=int(actor["id"]),
            idempotency_key=key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        record_database_failure(exc, operation="admin_user_delete")
        _raise_database_error(exc)
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_operation(operation_id: str, *, actor: dict[str, Any]) -> dict[str, Any]:
    """读取当前管理员发起的受控操作及其 cleanup 聚合状态。"""
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT operation_id, operation_type, status, target_count,
                   succeeded_count, failed_count, result_json,
                   created_at, completed_at
            FROM admin_operations
            WHERE operation_id = %s AND actor_user_id = %s
            """,
            (operation_id, actor["id"]),
        )
        operation = cursor.fetchone()
        if not operation:
            raise AdminApiError("not_found", "管理员操作不存在", status=404)
        cursor.execute(
            """
            SELECT target_type, target_id, target_label, result,
                   error_code, old_values_json, new_values_json, created_at
            FROM admin_operation_items
            WHERE operation_id = %s
            ORDER BY id ASC
            """,
            (operation_id,),
        )
        items = cursor.fetchall()

    result = _json_loads(operation.get("result_json")) or {}
    result.update(
        {
            "operation_id": operation["operation_id"],
            "operation_type": operation["operation_type"],
            "status": operation["status"],
            "target_count": int(operation["target_count"] or 0),
            "succeeded_count": int(operation["succeeded_count"] or 0),
            "failed_count": int(operation["failed_count"] or 0),
            "created_at": operation["created_at"],
            "completed_at": operation["completed_at"],
            "items": [
                {
                    **item,
                    "old_values": _json_loads(item.pop("old_values_json", None)),
                    "new_values": _json_loads(item.pop("new_values_json", None)),
                }
                for item in items
            ],
            "replayed": True,
        }
    )
    return result


def get_file_delete_impact(file_id: int) -> dict[str, Any]:
    """预览逻辑文件删除影响，并检查冻结输入和共享 BLOB 引用。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT f.id, f.user_id, u.username, f.object_id,
                   f.filename, f.filename AS original_filename,
                   o.content_hash AS file_hash, f.mime_type, f.file_size,
                   f.uploaded_at AS upload_timestamp,
                   f.last_accessed_at, f.access_count,
                   (SELECT COUNT(*) FROM user_files AS refs
                    WHERE refs.object_id = f.object_id) AS object_reference_count
            FROM user_files AS f
            JOIN file_objects AS o ON o.id = f.object_id
            JOIN users AS u ON u.id = f.user_id
            WHERE f.id = %s
            """,
            (file_id,),
        )
        file_row = cursor.fetchone()
        if not file_row:
            raise AdminApiError("not_found", "文件不存在", 404)
        cursor.execute(
            """
            SELECT COUNT(*) AS count_value
            FROM analysis_jobs
            WHERE input_user_file_id = %s
              AND (
                  status IN ('queued', 'running', 'waiting_input')
                  OR execution_state IN ('leased', 'draining')
              )
            """,
            (file_id,),
        )
        active_jobs = int((cursor.fetchone() or {}).get("count_value") or 0)
    blockers = (
        ["文件仍被活动或 draining 任务冻结使用"]
        if active_jobs
        else []
    )
    reference_count = int(file_row.get("object_reference_count") or 0)
    # object_id 只用于本次查询和引用计数，不能进入管理员公开 DTO。
    file_row.pop("object_id", None)
    return {
        "file": file_row,
        "impact": {
            "database_rows": 1,
            "blob_bytes": (
                int(file_row.get("file_size") or 0)
                if reference_count == 1
                else 0
            ),
            "owner_active_jobs": active_jobs,
            "object_reference_count": reference_count,
        },
        "can_delete": not blockers,
        "blockers": blockers,
        "requires_confirmation": file_row["original_filename"],
        "requires_reauthentication": True,
        "recycle_bin": False,
    }


def delete_file(
    file_id: int,
    body: Any,
    *,
    actor: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    """原子删除逻辑文件，并仅在无其他引用时删除不可变 BLOB。"""
    request_body = _require_body(body)
    if request_body.get("confirmed") is not True:
        raise AdminApiError(
            "confirmation_required",
            "必须先查看影响预览并明确确认",
            fields={"confirmed": "必须为 true"},
        )
    key = _parse_idempotency_key(idempotency_key)
    operation_type = "file.delete"
    fingerprint = _request_fingerprint(
        operation_type,
        {"file_id": file_id, **request_body},
    )
    existing = _load_existing_operation(
        actor_id=int(actor["id"]),
        idempotency_key=key,
        fingerprint=fingerprint,
    )
    if existing is not None:
        return existing
    reauth_password = _validate_reauthentication(
        actor,
        request_body.get("reauth_password"),
        action=operation_type,
    )
    operation_id = str(uuid4())
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        _configure_transaction(connection, cursor)
        _lock_and_reauthenticate_actor(
            cursor,
            actor=actor,
            password=reauth_password,
            action=operation_type,
        )
        replay = _load_existing_operation_for_update(
            cursor,
            actor_id=int(actor["id"]),
            idempotency_key=key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            connection.rollback()
            return replay
        cursor.execute(
            "SELECT user_id FROM user_files WHERE id = %s",
            (file_id,),
        )
        owner = cursor.fetchone()
        if not owner:
            raise AdminApiError("not_found", "文件不存在", 404)
        # 先锁用户、再锁文件，与用户删除和任务创建保持同一父子锁顺序。
        cursor.execute(
            "SELECT id FROM users WHERE id = %s FOR UPDATE",
            (owner["user_id"],),
        )
        if not cursor.fetchone():
            raise AdminApiError("owner_not_found", "文件归属用户不存在", 409)
        cursor.execute(
            """
            SELECT f.id, f.user_id, f.object_id, u.username,
                   f.filename, f.filename AS original_filename,
                   o.content_hash AS file_hash, f.mime_type, f.file_size,
                   f.uploaded_at AS upload_timestamp,
                   f.last_accessed_at, f.access_count
            FROM user_files AS f
            JOIN file_objects AS o ON o.id = f.object_id
            JOIN users AS u ON u.id = f.user_id
            WHERE f.id = %s
            FOR UPDATE
            """,
            (file_id,),
        )
        file_row = cursor.fetchone()
        if not file_row:
            raise AdminApiError("not_found", "文件不存在", 404)
        if request_body.get("confirm_filename") != file_row["original_filename"]:
            raise AdminApiError(
                "confirmation_mismatch",
                "确认文件名与目标文件不一致",
                fields={"confirm_filename": "必须完整输入原始文件名"},
            )
        cursor.execute(
            """
            SELECT job_id, execution_state
            FROM analysis_jobs
            WHERE input_user_file_id = %s
              AND (
                  status IN ('queued', 'running', 'waiting_input')
                  OR execution_state IN ('leased', 'draining')
              )
            ORDER BY id
            FOR UPDATE
            """,
            (file_id,),
        )
        if cursor.fetchall():
            raise AdminApiError(
                "active_jobs_block_delete",
                "文件仍被活动或 draining 任务冻结使用",
                status=409,
            )
        cursor.execute(
            "SELECT id FROM file_objects WHERE id = %s FOR UPDATE",
            (file_row["object_id"],),
        )
        if not cursor.fetchone():
            raise AdminApiError("object_not_found", "文件对象不存在", 409)
        _insert_operation(
            cursor,
            operation_id=operation_id,
            actor=actor,
            operation_type=operation_type,
            idempotency_key=key,
            fingerprint=fingerprint,
            target_count=1,
        )
        cursor.execute("DELETE FROM user_files WHERE id = %s", (file_id,))
        if cursor.rowcount != 1:
            raise RuntimeError("文件删除影响行数异常")
        cursor.execute(
            "SELECT COUNT(*) AS reference_count FROM user_files WHERE object_id = %s",
            (file_row["object_id"],),
        )
        reference_count = int((cursor.fetchone() or {}).get("reference_count") or 0)
        blob_deleted = False
        if reference_count == 0:
            cursor.execute(
                "DELETE FROM file_objects WHERE id = %s",
                (file_row["object_id"],),
            )
            blob_deleted = cursor.rowcount == 1
        old_values = {
            "user_id": file_row["user_id"],
            "username": file_row["username"],
            "original_filename": file_row["original_filename"],
            "mime_type": file_row["mime_type"],
            "file_size": int(file_row["file_size"]),
            "upload_timestamp": file_row["upload_timestamp"],
            "access_count": int(file_row["access_count"] or 0),
        }
        new_values = {
            "deleted": True,
            "blob_deleted": blob_deleted,
            "object_reference_count": reference_count,
            "recycle_bin": False,
        }
        _insert_operation_item(
            cursor,
            operation_id=operation_id,
            target_type="user_file",
            target_id=str(file_id),
            target_label=file_row["original_filename"],
            old_values=old_values,
            new_values=new_values,
        )
        insert_admin_audit_event(
            cursor,
            actor=actor,
            action=operation_type,
            target_type="user_file",
            target_id=str(file_id),
            old_values=old_values,
            new_values=new_values,
            result="success",
            error_code=None,
            request_id=get_request_id(),
        )
        result = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "target_count": 1,
            "file_id": file_id,
            "filename": file_row["original_filename"],
            "deleted": True,
            "blob_deleted": blob_deleted,
            "replayed": False,
        }
        _complete_operation(
            cursor,
            operation_id=operation_id,
            result=result,
            target_count=1,
        )
        connection.commit()
        return result
    except mysql.connector.Error as exc:
        connection.rollback()
        replay = _retry_existing_after_duplicate(
            exc,
            actor_id=int(actor["id"]),
            idempotency_key=key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        record_database_failure(exc, operation="admin_file_delete")
        _raise_database_error(exc)
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
