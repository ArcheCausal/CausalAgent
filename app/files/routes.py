"""用户文件库路由。"""

from __future__ import annotations

import hashlib
import logging
import os

import mysql.connector
from flask import Blueprint, jsonify, request

from app.auth.session_guard import get_current_session_user
from app.db import get_read_connection, get_write_connection, record_database_failure
from app.request_context import log_authorization_denied, log_request_failure
from config.settings import settings


files_bp = Blueprint("files", __name__, url_prefix="/api")
LOGGER = logging.getLogger(__name__)


def _iso(value) -> str | None:
    """把 MySQL 时间值转换为前端稳定可读的 ISO 字符串。"""
    return value.isoformat() if value is not None else None


def _file_payload(row: dict) -> dict:
    """把 user_files 行转换为不暴露 BLOB 的文件库对象。"""
    return {
        "id": int(row["id"]),
        "user_file_id": int(row["id"]),
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "file_size": int(row["file_size"]),
        "uploaded_at": _iso(row.get("uploaded_at")),
        "last_accessed_at": _iso(row.get("last_accessed_at")),
        "access_count": int(row.get("access_count") or 0),
    }


@files_bp.route("/files")
def get_file_list():
    """读取当前用户的逻辑文件库，不读取或返回文件正文。"""
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"error": "用户未登录或会话已过期"}), 401

    try:
        with get_read_connection(consistency="strong") as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, object_id, filename, mime_type, file_size,
                       uploaded_at, last_accessed_at, access_count
                FROM user_files
                WHERE user_id = %s
                ORDER BY last_accessed_at DESC, id DESC
                """,
                (current_user["id"],),
            )
            rows = cursor.fetchall()
        return jsonify([_file_payload(row) for row in rows])
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="file_list_query")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({"error": "读取文件列表时出错"}), 500


@files_bp.route("/upload_file", methods=["POST"])
def upload_file():
    """上传 CSV 到不可变对象库并创建或复用一条逻辑文件记录。"""
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401

    if "file" not in request.files:
        return jsonify({"success": False, "error": "没有文件被上传"}), 400
    upload = request.files["file"]
    filename = (upload.filename or "").strip()
    if not filename:
        return jsonify({"success": False, "error": "没有选择文件"}), 400

    file_ext = os.path.splitext(filename)[1].lower()
    allowed_mimetypes = {"text/csv", "application/vnd.ms-excel"}
    if file_ext != ".csv" or upload.mimetype not in allowed_mimetypes:
        return jsonify({"success": False, "error": "只允许上传 CSV 文件。请检查文件格式和扩展名。"}), 400

    try:
        content = upload.read()
        file_size = len(content)
        if file_size > settings.MAX_UPLOAD_SIZE_BYTES:
            return jsonify({
                "success": False,
                "error": f"文件大小不能超过 {settings.MAX_UPLOAD_SIZE_MB}MB",
            }), 413
        content_hash = hashlib.sha256(content).hexdigest()
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "处理文件内容失败"}), 500

    try:
        with get_write_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            connection.start_transaction()
            cursor.execute(
                "SELECT id FROM users WHERE id = %s FOR UPDATE",
                (current_user["id"],),
            )
            if not cursor.fetchone():
                connection.rollback()
                return jsonify({"success": False, "error": "用户不存在"}), 404

            cursor.execute(
                """
                SELECT id, file_size, mime_type
                FROM file_objects
                WHERE owner_user_id = %s AND content_hash = %s
                FOR UPDATE
                """,
                (current_user["id"], content_hash),
            )
            object_row = cursor.fetchone()
            if object_row:
                object_id = int(object_row["id"])
            else:
                cursor.execute(
                    """
                    INSERT INTO file_objects (
                        owner_user_id, content_hash, file_size, mime_type, file_content
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        current_user["id"],
                        content_hash,
                        file_size,
                        upload.mimetype,
                        content,
                    ),
                )
                object_id = int(cursor.lastrowid)

            cursor.execute(
                """
                SELECT id, object_id, filename, mime_type, file_size,
                       uploaded_at, last_accessed_at, access_count
                FROM user_files
                WHERE user_id = %s AND object_id = %s AND filename = %s
                FOR UPDATE
                """,
                (current_user["id"], object_id, filename),
            )
            user_file = cursor.fetchone()
            created = False
            if not user_file:
                cursor.execute(
                    """
                    INSERT INTO user_files (
                        user_id, object_id, filename, mime_type, file_size
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (current_user["id"], object_id, filename, upload.mimetype, file_size),
                )
                user_file_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    SELECT id, object_id, filename, mime_type, file_size,
                           uploaded_at, last_accessed_at, access_count
                    FROM user_files
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (user_file_id,),
                )
                user_file = cursor.fetchone()
                created = True
            connection.commit()

        payload = _file_payload(user_file)
        message = (
            f'文件 "{filename}" 上传成功！'
            if created
            else f'文件 "{filename}" 已在文件库中，无需重复上传。'
        )
        return jsonify({
            "success": True,
            "message": message,
            "file": payload,
            "user_file_id": payload["user_file_id"],
            "file_hash": content_hash,
        })
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="file_upload_write")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({"success": False, "error": "保存文件到数据库失败"}), 500
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "上传文件时发生服务器内部错误"}), 500


@files_bp.route("/delete_file", methods=["POST"])
def delete_file():
    """删除逻辑文件；无引用且无活动 Job 时再删除不可变 BLOB。"""
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401

    data = request.get_json(silent=True) or {}
    file_id = data.get("file_id")
    if not file_id:
        return jsonify({"success": False, "error": "缺少文件ID"}), 400

    try:
        file_id = int(file_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "文件ID无效"}), 400

    try:
        with get_write_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            connection.start_transaction()
            cursor.execute(
                "SELECT id FROM users WHERE id = %s FOR UPDATE",
                (current_user["id"],),
            )
            if not cursor.fetchone():
                connection.rollback()
                return jsonify({"success": False, "error": "用户不存在"}), 404

            cursor.execute(
                """
                SELECT id, object_id, filename
                FROM user_files
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """,
                (file_id, current_user["id"]),
            )
            file_row = cursor.fetchone()
            if not file_row:
                cursor.execute(
                    "SELECT user_id FROM user_files WHERE id = %s",
                    (file_id,),
                )
                owner = cursor.fetchone()
                connection.rollback()
                if owner and int(owner["user_id"]) != int(current_user["id"]):
                    log_authorization_denied(
                        LOGGER,
                        resource_type="file",
                        action="delete_file",
                    )
                return jsonify({"success": False, "error": "文件不存在或无权访问"}), 404

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
                connection.rollback()
                return jsonify({
                    "success": False,
                    "error": "当前文件仍被活动或 draining 任务使用，请等待执行占用释放后再删除",
                }), 409

            cursor.execute("DELETE FROM user_files WHERE id = %s", (file_id,))
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("逻辑文件删除影响行数异常")

            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM user_files WHERE object_id = %s)
                    +
                    (SELECT COUNT(*) FROM analysis_job_datasets
                     WHERE data_object_id = %s OR script_object_id = %s)
                    AS reference_count
                """,
                (file_row["object_id"], file_row["object_id"], file_row["object_id"]),
            )
            reference_count = int((cursor.fetchone() or {}).get("reference_count") or 0)
            blob_deleted = False
            if reference_count == 0:
                cursor.execute("DELETE FROM file_objects WHERE id = %s", (file_row["object_id"],))
                blob_deleted = cursor.rowcount == 1
            connection.commit()
            return jsonify({
                "success": True,
                "message": "文件已成功删除",
                "user_file_id": file_id,
                "blob_deleted": blob_deleted,
            })
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="file_delete_write")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({"success": False, "error": "删除文件时数据库出错"}), 500
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "删除文件时发生未知错误"}), 500
