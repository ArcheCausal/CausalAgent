'''
app.chat.routes - 聊天路由
'''
from flask import Blueprint, request, jsonify
from app.auth.session_guard import get_current_session_user
import logging
import json
from app.chat.response_storage import project_causal_graph, render_summary_for_display
from Agent.Report.document import ReportSchemaError, parse_report_document
from app.chat.execution_phases import assemble_execution_phases
from app.agent.persistence_cleanup import enqueue_checkpoint_cleanup_many
from app.db import record_database_failure
from app.request_context import (
    bind_request_log_context,
    log_authorization_denied,
    log_request_failure,
)
from observability.logging_runtime import log_event

chat_bp = Blueprint('chat', __name__, url_prefix='/api')
LOGGER = logging.getLogger(__name__)
import uuid

# 新对话
@chat_bp.route('/new_chat',methods=['POST'])
def new_chat():
    """生成会话 ID，并在返回前把会话元数据持久化到主库。"""
    import mysql.connector
    from app.db import get_write_connection

    current_user = get_current_session_user()
    if not current_user:
        return jsonify({'success': False, 'error': '用户未登录或会话已过期'}), 401
    
    user_id = current_user['id']
    new_session_id = str(uuid.uuid4())

    try:
        with get_write_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sessions (
                    id, user_id, title, created_at, last_activity_at, message_count
                ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)
                """,
                (new_session_id, user_id, "新对话"),
            )
            conn.commit()
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="session_create_write")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({'success': False, 'error': '创建新对话失败'}), 500

    bind_request_log_context(session_id=new_session_id)
    return jsonify({'success': True, 'new_session_id': new_session_id})

# 会话管理接口,获取会话
@chat_bp.route('/sessions')
def get_sessions():
    import mysql.connector
    from app.db import get_read_connection

    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"error": "用户未登录或会话已过期"}), 401
    
    user_id = current_user['id']
    try:
        with get_read_connection(consistency="eventual") as conn:
            cursor = conn.cursor(dictionary=True)
            # 高效地直接从 sessions 表查询
            cursor.execute("""
                SELECT id, title, last_activity_at
                FROM sessions
                WHERE user_id = %s AND is_archived = FALSE
                ORDER BY last_activity_at DESC
            """, (user_id,)) 
            session_rows = cursor.fetchall()

        if not session_rows:
            return jsonify([])

        # 格式化以适应前端期望的 (id, {preview, last_time}) 结构
        session_list_for_frontend = [
            (
                row["id"], 
                {
                    "preview": row["title"], 
                    "last_time": row["last_activity_at"].strftime("%m-%d %H:%M")
                }
            )
            for row in session_rows
        ]

    except mysql.connector.Error as e:
        record_database_failure(e, operation="session_list_query")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({"error": f"读取历史记录时出错: {e}"}), 500

    return jsonify(session_list_for_frontend)

# 加载特定会话内容 
@chat_bp.route('/load_session')
def load_session_content():
    import mysql.connector
    from app.db import get_read_connection

    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401
    
    user_id = current_user['id']
    session_id = request.args.get('session')

    if not session_id:
        return jsonify({"success": False, "error": "缺少 session ID"}), 400

    messages = []
    try:
        with get_read_connection(consistency="strong") as conn:
            conn.start_transaction(readonly=True)
            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                "SELECT id, UTC_TIMESTAMP(6) AS snapshot_at FROM sessions WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            session_exists = cursor.fetchone()

            if not session_exists:
                cursor.execute(
                    "SELECT user_id FROM sessions WHERE id = %s",
                    (session_id,),
                )
                owner = cursor.fetchone()
                conn.rollback()
                if owner and int(owner["user_id"]) != int(user_id):
                    log_authorization_denied(
                        LOGGER,
                        resource_type="session",
                        action="load_session",
                    )
                return jsonify({"success": False, "error": "会话不存在或无权访问"}), 404
            bind_request_log_context(session_id=session_id)

            cursor.execute("""
                SELECT
                    id, message_type, content, has_attachment,
                    analysis_job_id, analysis_job_input_id, source_event_id,
                    created_at
                FROM chat_messages
                WHERE session_id = %s AND user_id = %s
                ORDER BY created_at ASC, id ASC
            """, (session_id, user_id))
            chat_rows = cursor.fetchall()

            attachments_by_message = {}
            message_ids = [int(row["id"]) for row in chat_rows if row["has_attachment"]]
            if message_ids:
                placeholders = ", ".join(["%s"] * len(message_ids))
                cursor.execute(
                    f"""
                    SELECT message_id, attachment_type, content
                    FROM chat_attachments
                    WHERE message_id IN ({placeholders})
                    ORDER BY message_id, id
                    """,
                    tuple(message_ids),
                )
                for attachment in cursor.fetchall():
                    attachments_by_message.setdefault(int(attachment["message_id"]), []).append(attachment)

            cursor.execute(
                """
                SELECT job_id, status, created_at, finished_at, input_filename
                FROM analysis_jobs
                WHERE session_id = %s AND user_id = %s
                ORDER BY created_at, id
                """,
                (session_id, user_id),
            )
            job_rows = cursor.fetchall()
            input_rows = []
            event_rows = []
            job_ids = [str(row["job_id"]) for row in job_rows]
            if job_ids:
                placeholders = ", ".join(["%s"] * len(job_ids))
                cursor.execute(
                    f"""
                    SELECT input_id, job_id, sequence, input_type, chat_message_id, created_at
                    FROM analysis_job_inputs
                    WHERE job_id IN ({placeholders})
                    ORDER BY job_id, sequence, input_id
                    """,
                    tuple(job_ids),
                )
                input_rows = cursor.fetchall()
                cursor.execute(
                    f"""
                    SELECT id, job_id, event_type, payload_json, created_at
                    FROM analysis_job_events FORCE INDEX (idx_analysis_job_events_job_id)
                    WHERE job_id IN ({placeholders})
                    ORDER BY job_id, id
                    """,
                    tuple(job_ids),
                )
                event_rows = cursor.fetchall()

            jobs_by_id = {str(row["job_id"]): row for row in job_rows}
            inputs_by_id = {int(row["input_id"]): row for row in input_rows}

            phases_by_message_id = assemble_execution_phases(
                messages=chat_rows,
                jobs=job_rows,
                inputs=input_rows,
                events=event_rows,
                snapshot_at=session_exists["snapshot_at"],
            )

            for row in chat_rows:
                sender = "user" if row["message_type"] == 'user' else "ai"
                message = {
                    "sender": sender,
                    "text": row["content"],
                    "analysis_job_id": row.get("analysis_job_id"),
                    "analysis_job_input_id": row.get("analysis_job_input_id"),
                }

                if sender == "user" and row.get("analysis_job_input_id") is not None:
                    input_row = inputs_by_id.get(int(row["analysis_job_input_id"]))
                    job_id = row.get("analysis_job_id")
                    if (
                        input_row
                        and input_row.get("input_type") == "initial"
                        and int(input_row.get("chat_message_id") or 0) == int(row["id"])
                        and job_id
                        and str(input_row.get("job_id")) == str(job_id)
                    ):
                        filename = jobs_by_id.get(str(job_id), {}).get("input_filename")
                        if isinstance(filename, str) and filename.strip():
                            message["file_attachment"] = {"filename": filename}

                # 如果是AI消息，且有附件，则优先使用附件内容
                if sender == "ai" and row["has_attachment"]:
                    causal_graph_data = None
                    visualization_mapping = None
                    report_document = None

                    ## attachment格式：{"type": "causal_graph", "content": {...}}
                    for attachment in attachments_by_message.get(int(row["id"]), []):
                        if attachment["attachment_type"] == "causal_graph":
                            try:
                                causal_graph_data = json.loads(attachment["content"])
                            except json.JSONDecodeError:
                                pass

                        elif attachment["attachment_type"] == "visualization":
                            try:
                                visualization_mapping = json.loads(attachment["content"])
                            except json.JSONDecodeError:
                                pass

                        elif attachment["attachment_type"] == "web_search_references":
                            try:
                                message["references"] = json.loads(attachment["content"])
                            except json.JSONDecodeError:
                                log_event(
                                    LOGGER,
                                    "chat.attachment.degraded",
                                    details={
                                        "attachment_type": "web_search_references",
                                        "reason_code": "protocol_error",
                                    },
                                )

                        elif attachment["attachment_type"] == "report_document":
                            try:
                                report_document = parse_report_document(
                                    json.loads(attachment["content"])
                                )
                            except (json.JSONDecodeError, ReportSchemaError):
                                log_event(
                                    LOGGER,
                                    "chat.attachment.degraded",
                                    details={
                                        "attachment_type": "report_document",
                                        "reason_code": "protocol_error",
                                    },
                                )

                    if report_document is not None:
                        # 结构化报告历史恢复：附件 JSON 通过后端 schema 校验后
                        # 包装成前端使用的报告载荷，非法内容只回退到消息预览正文。
                        message["text"] = {
                            "type": "report",
                            "layout": "report",
                            "render_mode": "structured",
                            "document": report_document,
                        }
                    elif causal_graph_data:
                        message_content = causal_graph_data

                        # 早期版本把 Agent 内部图格式写进了附件，读取历史会话时
                        # 同样投影成前端可渲染的 vis-network 载荷。
                        if isinstance(message_content, dict):
                            projected_graph = project_causal_graph(
                                message_content.get("data")
                            )
                            if projected_graph is not None:
                                message_content["data"] = projected_graph

                        if visualization_mapping and "summary" in message_content:
                            message_content["summary"] = render_summary_for_display(
                                message_content["summary"],
                                visualization_mapping,
                            )
                        message["text"] = message_content
                    else:
                        message_text = row["content"]

                        if visualization_mapping:
                            message_text = render_summary_for_display(message_text, visualization_mapping)
                        message["text"] = message_text

                phase = phases_by_message_id.get(int(row["id"]))
                if sender == "user" and phase:
                    message["thinking_after"] = phase
                messages.append(message)

            conn.commit()

        return jsonify({"success": True, "messages": messages})

    except mysql.connector.Error as e:
        record_database_failure(e, operation="session_history_query")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({"success": False, "error": f"加载会话时出错: {e}"}), 500
    except Exception as e:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": f"加载会话时出错: {e}"}), 500

## 更改会话
@chat_bp.route('/change_session', methods=['POST'])
def change_session():
    import mysql.connector
    from app.db import get_write_connection

    #  用户认证检查 
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401
    
    user_id = current_user['id']
    
    #  修改：从 POST 请求的 JSON body 中获取数据 
    data = request.json
    title = data.get('title')
    session_id = data.get('session_id')

    if not title or not session_id:
        return jsonify({"success": False, "error": "缺少标题或会话ID"}), 400

    try:
        with get_write_connection() as conn:
            # 增加 user_id 条件以确保安全，并锁定已存在的 session。
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id FROM sessions WHERE id = %s AND user_id = %s FOR UPDATE",
                (session_id, user_id),
            )
            session_row = cursor.fetchone()
            if not session_row:
                cursor.execute(
                    "SELECT user_id FROM sessions WHERE id = %s",
                    (session_id,),
                )
                owner = cursor.fetchone()
                conn.rollback()
                if owner and int(owner["user_id"]) != int(user_id):
                    log_authorization_denied(
                        LOGGER,
                        resource_type="session",
                        action="change_session",
                    )
                return jsonify({"success": False, "error": "会话不存在或无权访问"}), 404
            bind_request_log_context(session_id=session_id)

            cursor.execute(
                "UPDATE sessions SET title = %s WHERE id = %s AND user_id = %s",
                (title, session_id, user_id),
            )
            conn.commit()
            
        return jsonify({"success": True, "message": "会话标题已更新"})
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="session_title_write")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({"success": False, "error": "更新会话标题时数据库出错"}), 500

## 删除会话
@chat_bp.route('/delete_session', methods=['POST'])
def delete_session():
    """删除会话业务数据，并在同一事务中登记 PostgreSQL checkpoint 清理。"""
    import mysql.connector
    from app.db import get_write_connection

    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401
    
    user_id = current_user['id']
    data = request.json
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"success": False, "error": "缺少会话ID"}), 400

    try:
        with get_write_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # 开启事务
            conn.start_transaction()
            
            # 与创建、恢复、取消 Job 保持一致：先锁 Job，再锁 session，避免并发
            # 删除和任务生命周期操作形成 InnoDB 锁环。
            cursor.execute("""
                SELECT job_id, status, execution_state
                FROM analysis_jobs
                WHERE session_id = %s
                  AND user_id = %s
                ORDER BY id
                FOR UPDATE
            """, (session_id, user_id))
            session_jobs = cursor.fetchall()

            # 锁定目标会话，阻止删除过程中并发创建引用该会话的新任务。
            cursor.execute(
                "SELECT id FROM sessions WHERE id = %s AND user_id = %s FOR UPDATE",
                (session_id, user_id),
            )
            session_exists = cursor.fetchone()

            if not session_exists:
                cursor.execute(
                    "SELECT user_id FROM sessions WHERE id = %s",
                    (session_id,),
                )
                owner = cursor.fetchone()
                conn.rollback()
                if owner and int(owner["user_id"]) != int(user_id):
                    log_authorization_denied(
                        LOGGER,
                        resource_type="session",
                        action="delete_session",
                    )
                return jsonify({"success": False, "error": "会话不存在或无权访问"}), 404
            bind_request_log_context(session_id=session_id)

            if any(
                row["status"] in {"queued", "running", "waiting_input"}
                or row.get("execution_state") in {"leased", "draining"}
                for row in session_jobs
            ):
                conn.rollback()
                return jsonify({"success": False, "error": "当前会话仍有活动或 draining 任务，请等待执行占用释放后再删除"}), 409

            try:
                # 跨库删除不能加入 MySQL 事务；outbox 与会话删除同事务提交。
                cleanup_count = enqueue_checkpoint_cleanup_many(
                    cursor,
                    [str(row["job_id"]) for row in session_jobs],
                )

                # 2. 删除与该会话相关的附件 (通过连接 chat_messages)
                # 这是为了处理 chat_attachments 和 chat_messages 之间没有直接外键的情况
                sql_delete_attachments = """
                    DELETE ca FROM chat_attachments ca
                    JOIN chat_messages cm ON ca.message_id = cm.id
                    WHERE cm.session_id = %s AND cm.user_id = %s
                """
                cursor.execute(sql_delete_attachments, (session_id, user_id))

                # 3. 删除该会话的所有聊天记录
                cursor.execute("DELETE FROM chat_messages WHERE session_id = %s AND user_id = %s", (session_id, user_id))

                # 4. 删除会话本身（如果存在）
                if session_exists:
                    cursor.execute("DELETE FROM sessions WHERE id = %s AND user_id = %s", (session_id, user_id))

                conn.commit()
                return jsonify({
                    "success": True,
                    "message": "会话已删除，checkpoint 正在后台清理",
                    "checkpoint_cleanup": "pending" if cleanup_count else "succeeded",
                }), 202
            except Exception:
                conn.rollback()
                raise

    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="session_delete_write")
        log_request_failure(LOGGER, reason_code="database_unavailable")
        return jsonify({"success": False, "error": "删除会话时数据库出错"}), 500
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "删除会话时发生未知错误"}), 500
