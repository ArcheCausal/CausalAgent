"""AnalysisContext 的 MySQL 访问边界。

analysis_contexts 保存跨 Job 的业务事实：文件快照、分析参数、最新结构化算法摘要、
RAG/Web 证据摘要和最新报告引用。报告正文仍保存在 chat_attachments，执行恢复仍由
PostgreSQL checkpoint 负责。

本模块不负责意图识别、路由和提示词投影。所有跨 Job 的写入必须满足下面两个前提之一：
调用方已经持有 analysis_jobs 行锁（例如 Job 创建或终态事务），或者通过
worker_id + attempt_count + lease_epoch fencing 校验。锁顺序固定为
analysis_jobs -> sessions -> analysis_contexts，与 Job 服务保持一致。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import mysql.connector

from app.db import (
    get_read_connection,
    get_write_connection,
    record_database_failure,
)


DEFAULT_CONTEXT_INDEX_LIMIT = 20
MAX_CONTEXT_INDEX_LIMIT = 20
JSON_COLUMNS = (
    "latest_algorithm_summary",
    "latest_rag_evidence",
    "latest_web_evidence",
)


class AnalysisContextFencedError(RuntimeError):
    """表示当前 invocation 已经失去 Job 执行资格，禁止继续写入上下文。"""


def _json_loads(value: Any) -> Any:
    """把 MySQL JSON 列的文本值还原为 Python 对象。"""
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _json_dumps(value: Any) -> str:
    """把内部结构化摘要序列化为可写入 JSON 列的文本。"""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _lock_running_job(
    cursor,
    *,
    job_id: str,
    worker_id: str,
    attempt_count: int,
    lease_epoch: int,
) -> dict[str, Any] | None:
    """锁定 Job 并确认 worker、attempt 和 lease epoch 仍然匹配。"""
    cursor.execute(
        """
        SELECT job_id, user_id, session_id, status, worker_id, attempt_count,
               lease_epoch, execution_state, input_user_file_id,
               input_object_id, input_file_hash, input_filename,
               analysis_context_id
        FROM analysis_jobs
        WHERE job_id = %s
        FOR UPDATE
        """,
        (job_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    if row.get("status") != "running" or row.get("execution_state") != "leased":
        return None
    if row.get("worker_id") != worker_id:
        return None
    if int(row.get("attempt_count") or 0) != int(attempt_count):
        return None
    if int(row.get("lease_epoch") or 0) != int(lease_epoch):
        return None
    return row


def _lock_session(cursor, *, user_id: int, session_id: str) -> dict[str, Any]:
    """按 user_id 与 session_id 锁定 Session 并返回当前上下文指针。"""
    cursor.execute(
        """
        SELECT id, active_analysis_context_id
        FROM sessions
        WHERE id = %s AND user_id = %s
        FOR UPDATE
        """,
        (session_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        raise PermissionError("会话不存在或不属于当前用户")
    return row


def _lock_context(
    cursor,
    *,
    user_id: int,
    session_id: str,
    context_id: str,
) -> dict[str, Any]:
    """锁定指定上下文本行，越权或不存在时抛出权限错误。"""
    cursor.execute(
        """
        SELECT *
        FROM analysis_contexts
        WHERE analysis_context_id = %s
        FOR UPDATE
        """,
        (context_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise PermissionError("分析上下文不存在")
    if int(row["user_id"]) != int(user_id) or str(row["session_id"]) != str(session_id):
        raise PermissionError("分析上下文不属于当前会话")
    return row


def _activate_context(
    cursor,
    *,
    job_id: str,
    user_id: int,
    session_id: str,
    context_id: str,
) -> None:
    """把 Session 默认指针、当前 Job 和当前输入指向目标上下文。

    这里只做幂等写入，不用 rowcount 判断权限：MySQL 在写入值与现值相同时上报的
    rowcount 是 0，而“切到当前上下文”是完全正常的路径。归属校验由调用方在
    _lock_session 与 _lock_context 里以 FOR UPDATE 完成。
    """
    cursor.execute(
        """
        UPDATE sessions
        SET active_analysis_context_id = %s
        WHERE id = %s AND user_id = %s
        """,
        (context_id, session_id, user_id),
    )
    cursor.execute(
        """
        UPDATE analysis_jobs
        SET analysis_context_id = %s
        WHERE job_id = %s
        """,
        (context_id, job_id),
    )
    cursor.execute(
        """
        UPDATE analysis_job_inputs
        SET analysis_context_id = %s
        WHERE job_id = %s
        ORDER BY sequence DESC
        LIMIT 1
        """,
        (context_id, job_id),
    )


def create_or_reuse_context_for_frozen_file(
    cursor,
    *,
    user_id: int,
    session_id: str,
    snapshot: dict[str, Any],
) -> str | None:
    """在 Job 创建事务内按冻结文件创建或复用上下文。

    同一 Session 的当前 active 上下文如果指向同一个文件对象就继续复用；否则新建一个
    参数为空的上下文并把它设为 active。target 与 treatment 由 fold 节点确定后回填。
    """
    file_object_id = snapshot.get("input_object_id")
    if not file_object_id:
        return None
    session_row = _lock_session(cursor, user_id=user_id, session_id=session_id)
    active_context_id = session_row.get("active_analysis_context_id")
    if active_context_id:
        cursor.execute(
            """
            SELECT analysis_context_id
            FROM analysis_contexts
            WHERE analysis_context_id = %s
              AND user_id = %s AND session_id = %s
              AND status = 'active' AND file_object_id = %s
            """,
            (active_context_id, user_id, session_id, file_object_id),
        )
        if cursor.fetchone():
            return str(active_context_id)

    context_id = str(uuid4())
    cursor.execute(
        """
        INSERT INTO analysis_contexts (
            analysis_context_id, session_id, user_id, status,
            input_user_file_id, file_object_id, file_hash, filename
        ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s)
        """,
        (
            context_id,
            session_id,
            user_id,
            snapshot.get("input_user_file_id"),
            file_object_id,
            snapshot.get("input_file_hash"),
            snapshot.get("input_filename"),
        ),
    )
    cursor.execute(
        """
        UPDATE sessions
        SET active_analysis_context_id = %s
        WHERE id = %s AND user_id = %s
        """,
        (context_id, session_id, user_id),
    )
    return context_id


def _serialize_context_row(
    row: dict[str, Any],
    *,
    latest_report_title: str | None = None,
) -> dict[str, Any]:
    """把上下文行投影成不含原始 Tool Call 的内部字典。"""
    updated_at = row.get("updated_at")
    return {
        "analysis_context_id": str(row["analysis_context_id"]),
        "session_id": str(row["session_id"]),
        "user_id": int(row["user_id"]),
        "status": str(row["status"]),
        "input_user_file_id": row.get("input_user_file_id"),
        "file_object_id": row.get("file_object_id"),
        "file_hash": row.get("file_hash"),
        "filename": row.get("filename"),
        "target": row.get("target"),
        "treatment": row.get("treatment"),
        "analysis_question": row.get("analysis_question"),
        "latest_algorithm_summary": _json_loads(row.get("latest_algorithm_summary")),
        "latest_rag_evidence": _json_loads(row.get("latest_rag_evidence")),
        "latest_web_evidence": _json_loads(row.get("latest_web_evidence")),
        "latest_report_message_id": row.get("latest_report_message_id"),
        "latest_report_id": row.get("latest_report_id"),
        "latest_report_title": latest_report_title,
        "updated_at": (
            updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at
        ),
    }


def load_active_context(user_id: int, session_id: str) -> dict[str, Any] | None:
    """按 Session 的默认指针读取当前上下文；指针缺失或失效时返回 None。"""
    try:
        with get_read_connection(consistency="strong") as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT c.*, cm.content AS latest_report_title
                FROM sessions AS s
                JOIN analysis_contexts AS c
                  ON c.analysis_context_id = s.active_analysis_context_id
                 AND c.user_id = s.user_id
                 AND c.session_id = s.id
                LEFT JOIN chat_messages AS cm
                  ON cm.id = c.latest_report_message_id
                WHERE s.id = %s AND s.user_id = %s
                """,
                (session_id, user_id),
            )
            row = cursor.fetchone()
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="analysis_context_active_read")
        raise
    if not row:
        return None
    return _serialize_context_row(row, latest_report_title=row.get("latest_report_title"))


def load_context_index(
    user_id: int,
    session_id: str,
    *,
    exclude_context_id: str | None = None,
    limit: int = DEFAULT_CONTEXT_INDEX_LIMIT,
) -> list[dict[str, Any]]:
    """读取同一 Session 其他历史上下文的简要索引，按最近更新时间排序。"""
    normalized_limit = max(1, min(int(limit), MAX_CONTEXT_INDEX_LIMIT))
    try:
        with get_read_connection(consistency="strong") as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT c.*, cm.content AS latest_report_title
                FROM analysis_contexts AS c
                LEFT JOIN chat_messages AS cm
                  ON cm.id = c.latest_report_message_id
                WHERE c.user_id = %s AND c.session_id = %s
                  AND c.status = 'active'
                  AND (%s IS NULL OR c.analysis_context_id <> %s)
                ORDER BY c.updated_at DESC, c.analysis_context_id DESC
                LIMIT %s
                """,
                (
                    user_id,
                    session_id,
                    exclude_context_id,
                    exclude_context_id,
                    normalized_limit,
                ),
            )
            rows = cursor.fetchall()
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="analysis_context_index_read")
        raise
    return [
        _serialize_context_row(row, latest_report_title=row.get("latest_report_title"))
        for row in rows
    ]


def load_context(
    user_id: int,
    session_id: str,
    context_id: str,
) -> dict[str, Any] | None:
    """按归属读取单个上下文；越权或不存在时返回 None。"""
    try:
        with get_read_connection(consistency="strong") as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT c.*, cm.content AS latest_report_title
                FROM analysis_contexts AS c
                LEFT JOIN chat_messages AS cm
                  ON cm.id = c.latest_report_message_id
                WHERE c.analysis_context_id = %s
                  AND c.user_id = %s AND c.session_id = %s
                """,
                (context_id, user_id, session_id),
            )
            row = cursor.fetchone()
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="analysis_context_read")
        raise
    if not row:
        return None
    return _serialize_context_row(row, latest_report_title=row.get("latest_report_title"))


def _find_context_by_parameters(
    cursor,
    *,
    user_id: int,
    session_id: str,
    file_object_id: int,
    target: str | None,
    treatment: str | None,
) -> str | None:
    """按文件和分析参数查找已有的活动上下文。"""
    cursor.execute(
        """
        SELECT analysis_context_id
        FROM analysis_contexts
        WHERE user_id = %s AND session_id = %s AND status = 'active'
          AND file_object_id = %s
          AND ((target IS NULL AND %s IS NULL) OR target = %s)
          AND ((treatment IS NULL AND %s IS NULL) OR treatment = %s)
        ORDER BY updated_at DESC, analysis_context_id DESC
        LIMIT 1
        """,
        (user_id, session_id, file_object_id, target, target, treatment, treatment),
    )
    row = cursor.fetchone()
    return str(row["analysis_context_id"]) if row else None


def apply_fold_context(
    *,
    user_id: int,
    session_id: str,
    job_id: str,
    worker_id: str,
    attempt_count: int,
    lease_epoch: int,
    target: str | None,
    treatment: str | None,
    analysis_question: str | None,
) -> dict[str, Any] | None:
    """在 fold 确定分析参数后把 Job 绑定到正确的上下文。

    规则：绑定上下文还没有参数时直接回填；参数一致时保持不动；参数不同时优先复用同一
    文件上参数一致的历史上下文，没有才新建。整个过程在一个事务内完成，节点重复执行
    得到同一个结果。
    """
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()
        job = _lock_running_job(
            cursor,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            lease_epoch=lease_epoch,
        )
        if job is None:
            raise AnalysisContextFencedError("Job 执行资格已失效")
        if int(job["user_id"]) != int(user_id) or str(job["session_id"]) != str(session_id):
            raise PermissionError("Job 归属与上下文字段不一致")
        file_object_id = job.get("input_object_id")
        if not file_object_id:
            connection.rollback()
            return None

        _lock_session(cursor, user_id=user_id, session_id=session_id)
        bound_context_id = job.get("analysis_context_id")
        target_context_id = str(bound_context_id) if bound_context_id else None
        action = "created"
        if target_context_id:
            bound = _lock_context(
                cursor,
                user_id=user_id,
                session_id=session_id,
                context_id=target_context_id,
            )
            bound_target = bound.get("target")
            bound_treatment = bound.get("treatment")
            if bound_target is None and bound_treatment is None:
                cursor.execute(
                    """
                    UPDATE analysis_contexts
                    SET target = %s, treatment = %s,
                        analysis_question = COALESCE(analysis_question, %s)
                    WHERE analysis_context_id = %s
                    """,
                    (target, treatment, analysis_question, target_context_id),
                )
                action = "backfilled"
            elif bound_target == target and bound_treatment == treatment:
                action = "unchanged"
            else:
                target_context_id = None

        if target_context_id is None:
            existing = _find_context_by_parameters(
                cursor,
                user_id=user_id,
                session_id=session_id,
                file_object_id=int(file_object_id),
                target=target,
                treatment=treatment,
            )
            if existing:
                target_context_id = existing
                action = "reused"
            else:
                target_context_id = str(uuid4())
                cursor.execute(
                    """
                    INSERT INTO analysis_contexts (
                        analysis_context_id, session_id, user_id, status,
                        input_user_file_id, file_object_id, file_hash, filename,
                        target, treatment, analysis_question
                    ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        target_context_id,
                        session_id,
                        user_id,
                        job.get("input_user_file_id"),
                        file_object_id,
                        job.get("input_file_hash"),
                        job.get("input_filename"),
                        target,
                        treatment,
                        analysis_question,
                    ),
                )

        if str(target_context_id) != str(bound_context_id or ""):
            _activate_context(
                cursor,
                job_id=job_id,
                user_id=user_id,
                session_id=session_id,
                context_id=target_context_id,
            )
        cursor.execute(
            """
            SELECT c.*, cm.content AS latest_report_title
            FROM analysis_contexts AS c
            LEFT JOIN chat_messages AS cm
              ON cm.id = c.latest_report_message_id
            WHERE c.analysis_context_id = %s
            """,
            (target_context_id,),
        )
        final_row = cursor.fetchone()
        connection.commit()
        return {
            "analysis_context_id": str(target_context_id),
            "action": action,
            "context": (
                _serialize_context_row(
                    final_row,
                    latest_report_title=final_row.get("latest_report_title"),
                )
                if final_row
                else None
            ),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_analysis_results(
    cursor,
    *,
    context_id: str,
    algorithm_summary: Any = None,
    rag_evidence: Any = None,
    web_evidence: Any = None,
    report_message_id: int | None = None,
    report_id: str | None = None,
) -> None:
    """在调用方已持有的 Job 终态事务内写入本次执行确认的事实。

    只覆盖非空字段，因此失败或澄清路径不会抹掉已有的有效结果。
    """
    assignments: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("latest_algorithm_summary", algorithm_summary),
        ("latest_rag_evidence", rag_evidence),
        ("latest_web_evidence", web_evidence),
        ("latest_report_message_id", report_message_id),
        ("latest_report_id", report_id),
    ):
        if value is None:
            continue
        assignments.append(f"{column} = %s")
        params.append(_json_dumps(value) if column in JSON_COLUMNS else value)
    if not assignments:
        return
    params.append(context_id)
    cursor.execute(
        f"""
        UPDATE analysis_contexts
        SET {", ".join(assignments)}
        WHERE analysis_context_id = %s
        """,
        tuple(params),
    )
def _read_valid_file_snapshot(
    cursor,
    *,
    user_id: int,
    user_file_id: Any,
    file_object_id: Any,
) -> dict[str, Any] | None:
    """按用户归属锁定并返回仍然有效的文件快照。"""
    if not user_file_id or not file_object_id:
        return None
    cursor.execute(
        """
        SELECT uf.id AS user_file_id,
               fo.id AS object_id,
               fo.content_hash AS file_hash,
               uf.filename AS filename
        FROM user_files AS uf
        JOIN file_objects AS fo
          ON fo.id = uf.object_id
         AND fo.owner_user_id = uf.user_id
        WHERE uf.id = %s
          AND uf.user_id = %s
          AND uf.object_id = %s
        FOR UPDATE
        """,
        (user_file_id, user_id, file_object_id),
    )
    return cursor.fetchone()


def _refreeze_job_input(cursor, *, job: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """按目标上下文的文件快照重写 Job 冻结输入，返回是否发生了变化。"""
    changed = (
        job.get("input_user_file_id") != snapshot["user_file_id"]
        or job.get("input_object_id") != snapshot["object_id"]
        or job.get("input_file_hash") != snapshot["file_hash"]
        or job.get("input_filename") != snapshot["filename"]
    )
    if not changed:
        return False
    cursor.execute(
        """
        UPDATE analysis_jobs
        SET input_user_file_id = %s, input_object_id = %s,
            input_file_hash = %s, input_filename = %s
        WHERE job_id = %s
        """,
        (
            snapshot["user_file_id"],
            snapshot["object_id"],
            snapshot["file_hash"],
            snapshot["filename"],
            job["job_id"],
        ),
    )
    return True


def switch_to_context(
    *,
    user_id: int,
    session_id: str,
    job_id: str,
    worker_id: str,
    attempt_count: int,
    lease_epoch: int,
    context_id: str,
) -> dict[str, Any]:
    """切换 Session 默认上下文，并在文件不同时重新冻结 Job 输入。

    返回 status 为 switched 或 file_missing 的结果。文件快照来自 user_files 与
    file_objects 的当前记录，因此上下文引用的文件被删除时不会写入失效指针。
    """
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()
        job = _lock_running_job(
            cursor,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            lease_epoch=lease_epoch,
        )
        if job is None:
            raise AnalysisContextFencedError("Job 执行资格已失效")
        if int(job["user_id"]) != int(user_id) or str(job["session_id"]) != str(session_id):
            raise PermissionError("Job 归属与上下文字段不一致")
        _lock_session(cursor, user_id=user_id, session_id=session_id)
        target = _lock_context(
            cursor,
            user_id=user_id,
            session_id=session_id,
            context_id=context_id,
        )
        if str(target["status"]) != "active":
            connection.rollback()
            return {"status": "inactive"}
        snapshot = _read_valid_file_snapshot(
            cursor,
            user_id=user_id,
            user_file_id=target.get("input_user_file_id"),
            file_object_id=target.get("file_object_id"),
        )
        if snapshot is None:
            connection.rollback()
            return {"status": "file_missing"}
        file_changed = _refreeze_job_input(cursor, job=job, snapshot=snapshot)
        _activate_context(
            cursor,
            job_id=job_id,
            user_id=user_id,
            session_id=session_id,
            context_id=str(target["analysis_context_id"]),
        )
        connection.commit()
        return {
            "status": "switched",
            "context": _serialize_context_row(target),
            "file_snapshot": {
                "input_user_file_id": int(snapshot["user_file_id"]),
                "input_object_id": int(snapshot["object_id"]),
                "input_file_hash": snapshot["file_hash"],
                "input_filename": snapshot["filename"],
            },
            "file_changed": file_changed,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_context_for_new_file(
    *,
    user_id: int,
    session_id: str,
    job_id: str,
    worker_id: str,
    attempt_count: int,
    lease_epoch: int,
    user_file_id: int,
) -> dict[str, Any]:
    """为同一会话内新指定的用户文件建立上下文并重新冻结 Job 输入。

    只接受属于当前用户的文件；新建的上下文参数为空，由后续 fold 节点回填。
    """
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()
        job = _lock_running_job(
            cursor,
            job_id=job_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            lease_epoch=lease_epoch,
        )
        if job is None:
            raise AnalysisContextFencedError("Job 执行资格已失效")
        if int(job["user_id"]) != int(user_id) or str(job["session_id"]) != str(session_id):
            raise PermissionError("Job 归属与上下文字段不一致")
        _lock_session(cursor, user_id=user_id, session_id=session_id)
        cursor.execute(
            """
            SELECT uf.id AS user_file_id,
                   fo.id AS object_id,
                   fo.content_hash AS file_hash,
                   uf.filename AS filename
            FROM user_files AS uf
            JOIN file_objects AS fo
              ON fo.id = uf.object_id
             AND fo.owner_user_id = uf.user_id
            WHERE uf.id = %s AND uf.user_id = %s
            FOR UPDATE
            """,
            (user_file_id, user_id),
        )
        snapshot = cursor.fetchone()
        if snapshot is None:
            connection.rollback()
            return {"status": "file_missing"}

        # 同一个文件在本会话里已经有 active 上下文时复用它，避免因为历史索引
        # 有上限而给同一个文件重复建上下文；参数不一致仍由 fold 阶段纠正。
        cursor.execute(
            """
            SELECT analysis_context_id
            FROM analysis_contexts
            WHERE user_id = %s AND session_id = %s AND status = 'active'
              AND file_object_id = %s
            ORDER BY updated_at DESC, analysis_context_id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (user_id, session_id, int(snapshot["object_id"])),
        )
        existing = cursor.fetchone()
        if existing:
            existing_id = str(existing["analysis_context_id"])
            _refreeze_job_input(cursor, job=job, snapshot=snapshot)
            _activate_context(
                cursor,
                job_id=job_id,
                user_id=user_id,
                session_id=session_id,
                context_id=existing_id,
            )
            cursor.execute(
                """
                SELECT c.*, cm.content AS latest_report_title
                FROM analysis_contexts AS c
                LEFT JOIN chat_messages AS cm
                  ON cm.id = c.latest_report_message_id
                WHERE c.analysis_context_id = %s
                """,
                (existing_id,),
            )
            existing_row = cursor.fetchone()
            connection.commit()
            return {
                "status": "reused",
                "analysis_context_id": existing_id,
                "context": (
                    _serialize_context_row(
                        existing_row,
                        latest_report_title=existing_row.get("latest_report_title"),
                    )
                    if existing_row
                    else None
                ),
                "file_snapshot": {
                    "input_user_file_id": int(snapshot["user_file_id"]),
                    "input_object_id": int(snapshot["object_id"]),
                    "input_file_hash": snapshot["file_hash"],
                    "input_filename": snapshot["filename"],
                },
            }

        context_id = str(uuid4())
        cursor.execute(
            """
            INSERT INTO analysis_contexts (
                analysis_context_id, session_id, user_id, status,
                input_user_file_id, file_object_id, file_hash, filename
            ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s)
            """,
            (
                context_id,
                session_id,
                user_id,
                int(snapshot["user_file_id"]),
                int(snapshot["object_id"]),
                snapshot["file_hash"],
                snapshot["filename"],
            ),
        )
        _refreeze_job_input(cursor, job=job, snapshot=snapshot)
        _activate_context(
            cursor,
            job_id=job_id,
            user_id=user_id,
            session_id=session_id,
            context_id=context_id,
        )
        connection.commit()
        return {
            "status": "created",
            "analysis_context_id": context_id,
            "file_snapshot": {
                "input_user_file_id": int(snapshot["user_file_id"]),
                "input_object_id": int(snapshot["object_id"]),
                "input_file_hash": snapshot["file_hash"],
                "input_filename": snapshot["filename"],
            },
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def find_user_file_by_name(
    user_id: int,
    filename: str,
) -> list[dict[str, Any]]:
    """按文件名在用户文件库中查找候选文件，重名时返回多条。"""
    normalized = " ".join(str(filename or "").split())
    if not normalized:
        return []
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT uf.id AS user_file_id, uf.filename, uf.file_size, uf.uploaded_at
            FROM user_files AS uf
            WHERE uf.user_id = %s AND uf.filename = %s
            ORDER BY uf.uploaded_at DESC, uf.id DESC
            LIMIT 5
            """,
            (user_id, normalized),
        )
        rows = cursor.fetchall()
    return [
        {
            "user_file_id": int(row["user_file_id"]),
            "filename": row["filename"],
            "file_size": row["file_size"],
            "uploaded_at": (
                row["uploaded_at"].isoformat()
                if hasattr(row.get("uploaded_at"), "isoformat")
                else row.get("uploaded_at")
            ),
        }
        for row in rows
    ]
