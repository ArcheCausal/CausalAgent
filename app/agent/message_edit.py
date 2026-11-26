"""Create a new Job from a visible user message and preserve the old suffix for audit."""

from hashlib import sha256
from uuid import UUID, uuid4

import mysql.connector
from mysql.connector import errorcode

from app.agent import job_service as jobs
from app.chat.session_title import build_session_title
from app.db import get_write_connection
from Database.analysis_contexts import (
    capture_context_version,
    create_or_reuse_context_for_frozen_file,
    restore_context_version,
)


class EditConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _replay(cursor, *, user_id: int, key: str, fingerprint: str):
    cursor.execute(
        """
        SELECT j.job_id, j.status, i.chat_message_id, i.conversation_version, i.fork_from
        FROM analysis_jobs j JOIN analysis_job_inputs i ON i.job_id = j.job_id AND i.sequence = 0
        WHERE j.user_id = %s AND j.idempotency_key = %s AND j.request_fingerprint = %s
        """,
        (user_id, key, fingerprint),
    )
    row = cursor.fetchone()
    if row:
        return _result(row, existing=True)
    cursor.execute(
        "SELECT 1 FROM analysis_jobs WHERE user_id = %s AND idempotency_key = %s", (user_id, key)
    )
    if cursor.fetchone():
        raise EditConflict("idempotency_conflict", "幂等键已用于其他请求")
    return None


def _result(row, *, existing):
    return {
        "success": True,
        "job_id": str(row["job_id"]),
        "status": row["status"],
        "message_id": int(row["chat_message_id"]),
        "conversation_version": int(row["conversation_version"]),
        "execution_mode": "resume_checkpoint" if row["fork_from"] else "reanalyze",
        "existing": existing,
    }


def _require_supported_edit(target, *, reanalyze: bool) -> None:
    # The active graph retired fold. Native data-processing interrupts also
    # depend on the source Job's child state, input ledger and candidate identity.
    if target["input_type"] == "resume" and not reanalyze:
        raise EditConflict(
            "edit_reanalysis_required",
            "当前版本无法直接恢复此历史追问，请确认是否按保留历史重新分析",
        )


def _freeze_file(cursor, *, user_id, target):
    original = jobs._json_loads(target["file_at_submit"]) or {}
    user_file_id = original.get("input_user_file_id")
    if user_file_id is None:
        return dict.fromkeys(jobs.FILE_SNAPSHOT_COLUMNS)
    try:
        snapshot = jobs._load_file_snapshot(cursor, user_id, user_file_id)
    except PermissionError:
        snapshot = None
    if (
        not snapshot
        or snapshot["input_object_id"] != original["input_object_id"]
        or snapshot["input_file_hash"] != original["input_file_hash"]
    ):
        raise EditConflict("edit_file_unavailable", "原消息的冻结文件已不可用")
    return {column: original[column] for column in jobs.FILE_SNAPSHOT_COLUMNS}


def edit_message(
    *, user_id: int, session_id: str, message_id: int, message: str,
    version: int, checkpoint_id: str | None, reanalyze: bool,
    idempotency_key: str, request_id: str,
) -> dict:
    normalized = jobs._validate_input_text(message)
    key = jobs.normalize_idempotency_key(idempotency_key)
    request_id = jobs._validate_request_id(request_id)
    if not 0 < message_id < 2**63:
        raise ValueError("消息 ID 无效")
    if type(version) is not int or not 0 <= version < 2**64:
        raise ValueError("会话版本无效")
    if not isinstance(reanalyze, bool):
        raise ValueError("allow_reanalysis 必须是布尔值")
    if checkpoint_id is not None:
        try:
            if not isinstance(checkpoint_id, str) or str(UUID(checkpoint_id)) != checkpoint_id:
                raise ValueError
        except ValueError:
            raise ValueError("checkpoint_id 无效") from None
    fingerprint = sha256(jobs._json_dumps([
        "edit", session_id, message_id, normalized, version, checkpoint_id, reanalyze,
    ]).encode("utf-8")).hexdigest()
    for attempt in range(3):
        with get_write_connection() as connection:
            try:
                connection.start_transaction(isolation_level="READ COMMITTED")
                result = _edit_in_transaction(
                    connection.cursor(dictionary=True), user_id=user_id, session_id=session_id,
                    message_id=message_id, message=normalized, version=version,
                    checkpoint_id=checkpoint_id, reanalyze=reanalyze, key=key,
                    fingerprint=fingerprint, request_id=request_id,
                )
                connection.commit()
                return result
            except mysql.connector.Error as exc:
                connection.rollback()
                if attempt == 2 or exc.errno not in {
                    errorcode.ER_LOCK_DEADLOCK, errorcode.ER_LOCK_WAIT_TIMEOUT, errorcode.ER_DUP_ENTRY,
                }:
                    raise
            except Exception:
                connection.rollback()
                raise


def _edit_in_transaction(
    cursor, *, user_id, session_id, message_id, message, version,
    checkpoint_id, reanalyze, key, fingerprint, request_id,
):
    activity_query = """
        SELECT job_id, status, execution_state FROM analysis_jobs
        WHERE user_id = %s AND session_id = %s
          AND (status IN ('queued','running','waiting_input') OR execution_state = 'draining')
        ORDER BY id LIMIT 1
    """
    cursor.execute(activity_query + " FOR UPDATE", (user_id, session_id))
    cursor.fetchone()
    cursor.execute(
        "SELECT conversation_version, title FROM sessions WHERE user_id = %s AND id = %s FOR UPDATE",
        (user_id, session_id),
    )
    session = cursor.fetchone()
    if session is None:
        raise PermissionError("会话不存在或无权访问")
    if replay := _replay(cursor, user_id=user_id, key=key, fingerprint=fingerprint):
        return replay
    if session["conversation_version"] != version:
        raise EditConflict("conversation_version_conflict", "会话已更新，请刷新后核对修改")
    cursor.execute(activity_query, (user_id, session_id))
    activity = cursor.fetchone()
    if activity:
        if activity["execution_state"] == "draining":
            raise EditConflict("job_draining", "任务正在释放执行占用，请稍后重试")
        raise jobs.ActiveJobConflictError(activity)
    cursor.execute(
        """
        SELECT m.content, i.input_type, i.context_before, i.file_at_submit, j.web_search_enabled
        FROM chat_messages m
        LEFT JOIN analysis_jobs j ON j.job_id = m.analysis_job_id
            AND j.user_id = m.user_id AND j.session_id = m.session_id
        LEFT JOIN analysis_job_inputs i ON i.input_id = m.analysis_job_input_id
            AND i.chat_message_id = m.id AND i.job_id = j.job_id
        WHERE m.id = %s AND m.user_id = %s AND m.session_id = %s
          AND m.message_type = 'user' AND m.replaced_by_job_id IS NULL
        """,
        (message_id, user_id, session_id),
    )
    target = cursor.fetchone()
    if target is None:
        raise PermissionError("消息不存在或无权访问")
    cursor.execute(
        """
        SELECT j.job_id AS analysis_job_id, m.checkpoint_id, m.checkpoint_namespace, m.checkpoint_question_id,
               e.event_type, e.payload_json,
               input_message.id AS phase_message_id
        FROM chat_messages m
        LEFT JOIN analysis_jobs j ON j.job_id = m.analysis_job_id
            AND j.user_id = m.user_id AND j.session_id = m.session_id
        LEFT JOIN analysis_job_events e ON e.id = m.source_event_id AND e.job_id = j.job_id
        LEFT JOIN analysis_job_inputs i ON i.input_id = m.analysis_job_input_id AND i.job_id = e.job_id
        LEFT JOIN chat_messages input_message ON input_message.id = i.chat_message_id
            AND input_message.analysis_job_id = j.job_id AND input_message.analysis_job_input_id = i.input_id
            AND input_message.user_id = m.user_id AND input_message.session_id = m.session_id
            AND input_message.message_type = 'user' AND input_message.replaced_by_job_id IS NULL
        WHERE m.user_id = %s AND m.session_id = %s AND m.id < %s
          AND m.message_type = 'ai' AND m.replaced_by_job_id IS NULL
        ORDER BY m.id DESC LIMIT 1
        """,
        (user_id, session_id, message_id),
    )
    previous_ai = cursor.fetchone()
    if (previous_ai or {}).get("checkpoint_id") != checkpoint_id:
        raise EditConflict("edit_checkpoint_conflict", "上一条助手消息的恢复点已变化，请刷新会话")
    if checkpoint_id and target["input_type"] != "resume" and (
        not previous_ai["analysis_job_id"]
        or previous_ai["checkpoint_namespace"] != ""
        or not previous_ai["phase_message_id"]
        or previous_ai["event_type"] not in {"final_result", "interrupt"}
        or (
            previous_ai["event_type"] == "interrupt"
            and previous_ai["checkpoint_question_id"]
            != (jobs._json_loads(previous_ai["payload_json"]) or {}).get("question_id")
        )
    ):
        raise EditConflict("edit_checkpoint_conflict", "上一条助手消息的执行阶段无法验证")
    _require_supported_edit(target, reanalyze=reanalyze)
    snapshot = _freeze_file(cursor, user_id=user_id, target=target)

    job_id = str(uuid4())
    replaced_context = capture_context_version(cursor, user_id=user_id, session_id=session_id)
    restore_context_version(cursor, user_id=user_id, session_id=session_id, version=target["context_before"])
    context_before = capture_context_version(cursor, user_id=user_id, session_id=session_id)
    context_id = create_or_reuse_context_for_frozen_file(
        cursor, user_id=user_id, session_id=session_id, snapshot=snapshot,
    )
    cursor.execute(
        """
        UPDATE chat_messages SET replaced_by_job_id = %s
        WHERE user_id = %s AND session_id = %s AND id >= %s AND replaced_by_job_id IS NULL
        """,
        (job_id, user_id, session_id, message_id),
    )
    cursor.execute(
        """
        UPDATE sessions SET message_count = (
            SELECT COUNT(*) FROM chat_messages
            WHERE user_id = %s AND session_id = %s AND replaced_by_job_id IS NULL
        ) WHERE user_id = %s AND id = %s
        """,
        (user_id, session_id, user_id, session_id),
    )
    input_id, replacement = jobs.insert_queued_job(
        cursor, job_id=job_id, user_id=user_id, session_id=session_id, request_id=request_id,
        normalized_message=message, idempotency_key=key, fingerprint=fingerprint,
        snapshot_values=snapshot, web_search_enabled=bool(target["web_search_enabled"]),
        analysis_context_id=context_id, context_before=context_before,
        update_initial_title=session["title"] == build_session_title(target["content"]),
    )
    new_version = version + 1
    cursor.execute(
        """
        UPDATE analysis_job_inputs
        SET replaces_message_id = %s, conversation_version = %s, replaced_context = %s
        WHERE input_id = %s
        """,
        (message_id, new_version, replaced_context, input_id),
    )
    return _result({
        "job_id": job_id, "status": "queued", "chat_message_id": replacement,
        "conversation_version": new_version, "fork_from": None,
    }, existing=False)
