'''

app.chat.services - 聊天服务

- 获取聊天记录
'''
from app.db import get_read_connection, get_write_connection, record_database_failure
from app.chat.response_storage import prepare_ai_response_for_storage
from app.chat.session_title import build_session_title
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
import mysql.connector
from datetime import datetime
def get_chat_history(session_id: str, user_id: int, limit: int) -> list:
    """从数据库获取指定会话的最近聊天记录。"""
    history = []
    try:
        with get_read_connection(consistency="strong") as conn:
            cursor = conn.cursor(dictionary=True)
            # 获取最近的 'limit' 条记录
            # 为什么这里需要先反转，再反转排序呢？
            # 我需要获取一个子集，也就是所有记录中的最新的子集，然后在从老到新进行排序
            # 最后通过一个append,从老到新进行添加
            
            cursor.execute("""
                SELECT message_type, content FROM chat_messages
                WHERE session_id = %s AND user_id = %s
                  AND replaced_by_job_id IS NULL
                ORDER BY created_at DESC
                LIMIT %s
            """, (session_id, user_id, limit))
            recent_chats = cursor.fetchall()

            # 按时间倒序获取，所以要反转回来才是正确的对话顺序
            for row in reversed(recent_chats):
                role = "user" if row['message_type'] == 'user' else "assistant"
                history.append({"role": role, "content": row['content']})
            
            return history
            
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="chat_history_query")
        return []
    except Exception:
        return []



def get_job_chat_history(
    session_id: str,
    user_id: int,
    chat_message_id: int,
    limit: int,
) -> list[BaseMessage]:
    """读取 Job 创建时可见的有界消息窗口，不吞掉数据库异常。"""
    normalized_limit = max(1, int(limit))
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, message_type, content
            FROM chat_messages
            WHERE session_id = %s
              AND user_id = %s
              AND replaced_by_job_id IS NULL
              AND id <= %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (session_id, user_id, int(chat_message_id), normalized_limit),
        )
        recent_rows = cursor.fetchall()

    messages: list[BaseMessage] = []
    for row in reversed(recent_rows):
        content = row.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        if row.get("message_type") == "user":
            messages.append(HumanMessage(content=content))
        elif row.get("message_type") == "ai":
            messages.append(AIMessage(content=content))
    return messages


## 保存历史文件     
class SessionNotFoundError(ValueError):
    """表示写入请求引用了不存在或不属于当前用户的会话。"""


def _save_chat_rows(cursor, user_id, session_id, user_msg, ai_response, timestamp_dt):
    """使用已有事务游标写入聊天消息、附件和会话元数据，不提交事务。"""
    cursor.execute(
        "SELECT message_count, title FROM sessions WHERE id = %s AND user_id = %s FOR UPDATE",
        (session_id, user_id),
    )
    session_data = cursor.fetchone()

    if not session_data:
        raise SessionNotFoundError("会话不存在或无权访问")

    is_first_message = session_data["message_count"] == 0
    cursor.execute(
        """
        INSERT INTO chat_messages (session_id, user_id, message_type, content, created_at)
        VALUES (%s, %s, 'user', %s, %s)
        """,
        (session_id, user_id, user_msg, timestamp_dt),
    )

    ai_content, attachment_to_save = prepare_ai_response_for_storage(ai_response)
    has_attachment = len(attachment_to_save) > 0
    cursor.execute(
        """
        INSERT INTO chat_messages (session_id, user_id, message_type, content, has_attachment, created_at)
        VALUES (%s, %s, 'ai', %s, %s, %s)
        """,
        (session_id, user_id, ai_content, has_attachment, timestamp_dt),
    )
    ai_message_id = cursor.lastrowid

    if has_attachment and attachment_to_save:
        for attachment in attachment_to_save:
            cursor.execute(
                """
                INSERT INTO chat_attachments (message_id, attachment_type, content, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                (ai_message_id, attachment["type"], attachment["content"], timestamp_dt),
            )

    if is_first_message:
        new_title = build_session_title(user_msg)
        cursor.execute(
            """
            UPDATE sessions
            SET title = %s, last_activity_at = %s, message_count = message_count + 2,
                conversation_version = conversation_version + 1
            WHERE id = %s AND user_id = %s
            """,
            (new_title, timestamp_dt, session_id, user_id),
        )
    else:
        cursor.execute(
            """
            UPDATE sessions
            SET last_activity_at = %s, message_count = message_count + 2,
                conversation_version = conversation_version + 1
            WHERE id = %s AND user_id = %s
            """,
            (timestamp_dt, session_id, user_id),
        )


def save_chat_for_job_in_transaction(
    cursor,
    job_id,
    user_id,
    session_id,
    user_msg,
    ai_response,
) -> bool:
    """在 worker 的终态事务中幂等保存聊天，不提交调用方事务。"""
    cursor.execute(
        """
        SELECT chat_saved_at
        FROM analysis_jobs
        WHERE job_id = %s AND user_id = %s
        FOR UPDATE
        """,
        (job_id, user_id),
    )
    job_data = cursor.fetchone()
    if not job_data:
        raise ValueError("任务不存在或不属于当前用户")
    if job_data["chat_saved_at"] is not None:
        return False

    cursor.execute(
        """
        UPDATE analysis_jobs
        SET chat_saved_at = UTC_TIMESTAMP(6)
        WHERE job_id = %s AND user_id = %s AND chat_saved_at IS NULL
        """,
        (job_id, user_id),
    )
    if cursor.rowcount != 1:
        return False

    _save_chat_rows(cursor, user_id, session_id, user_msg, ai_response, datetime.now())
    return True


def save_user_input_for_job_in_transaction(
    cursor,
    *,
    job_id: str,
    input_id: int,
    user_id: int,
    session_id: str,
    text: str,
    update_initial_title: bool = True,
) -> int:
    """在创建或恢复 Job 事务中写入一条带输入账本关联的用户消息。"""
    cursor.execute(
        """
        SELECT message_count, title
        FROM sessions
        WHERE id = %s AND user_id = %s
        FOR UPDATE
        """,
        (session_id, user_id),
    )
    session_data = cursor.fetchone()
    if not session_data:
        raise SessionNotFoundError("会话不存在或无权访问")

    cursor.execute(
        """
        INSERT INTO chat_messages (
            session_id, user_id, analysis_job_id, analysis_job_input_id,
            message_type, content, created_at
        ) VALUES (%s, %s, %s, %s, 'user', %s, UTC_TIMESTAMP(6))
        """,
        (session_id, user_id, job_id, input_id, text),
    )
    message_id = int(cursor.lastrowid)
    if update_initial_title and int(session_data["message_count"] or 0) == 0:
        cursor.execute(
            """
            UPDATE sessions
            SET title = %s, last_activity_at = UTC_TIMESTAMP(6),
                message_count = message_count + 1, conversation_version = conversation_version + 1
            WHERE id = %s AND user_id = %s
            """,
            (build_session_title(text), session_id, user_id),
        )
    else:
        cursor.execute(
            """
            UPDATE sessions
            SET last_activity_at = UTC_TIMESTAMP(6),
                message_count = message_count + 1, conversation_version = conversation_version + 1
            WHERE id = %s AND user_id = %s
            """,
            (session_id, user_id),
        )
    return message_id


def save_assistant_for_job_in_transaction(
    cursor,
    *,
    job_id: str,
    user_id: int,
    session_id: str,
    ai_response,
    source_event_id: int,
    analysis_job_input_id: int | None = None,
    checkpoint_id: str | None = None,
    checkpoint_question_id: str | None = None,
) -> int | None:
    """在生命周期事务中写入一条由稳定事件唯一保护的 assistant 消息。"""
    cursor.execute(
        """
        SELECT id
        FROM chat_messages
        WHERE source_event_id = %s
        FOR UPDATE
        """,
        (source_event_id,),
    )
    existing = cursor.fetchone()
    if existing:
        return int(existing["id"] if isinstance(existing, dict) else existing[0])

    cursor.execute(
        """
        SELECT id
        FROM sessions
        WHERE id = %s AND user_id = %s
        FOR UPDATE
        """,
        (session_id, user_id),
    )
    if not cursor.fetchone():
        raise SessionNotFoundError("会话不存在或无权访问")

    ai_content, attachment_to_save = prepare_ai_response_for_storage(ai_response)
    cursor.execute(
        """
        INSERT INTO chat_messages (
            session_id, user_id, analysis_job_id, analysis_job_input_id,
            source_event_id, message_type, content, has_attachment, created_at,
            checkpoint_id, checkpoint_namespace, checkpoint_question_id
        ) VALUES (%s, %s, %s, %s, %s, 'ai', %s, %s, UTC_TIMESTAMP(6), %s, %s, %s)
        """,
        (
            session_id,
            user_id,
            job_id,
            analysis_job_input_id,
            source_event_id,
            ai_content,
            bool(attachment_to_save),
            checkpoint_id,
            "" if checkpoint_id else None,
            checkpoint_question_id if checkpoint_id else None,
        ),
    )
    message_id = int(cursor.lastrowid)
    for attachment in attachment_to_save:
        cursor.execute(
            """
            INSERT INTO chat_attachments (message_id, attachment_type, content, created_at)
            VALUES (%s, %s, %s, UTC_TIMESTAMP(6))
            """,
            (message_id, attachment["type"], attachment["content"]),
        )
    cursor.execute(
        """
        UPDATE sessions
        SET last_activity_at = UTC_TIMESTAMP(6), message_count = message_count + 1,
            conversation_version = conversation_version + 1
        WHERE id = %s AND user_id = %s
        """,
        (session_id, user_id),
    )
    return message_id


def save_chat(user_id, session_id, user_msg, ai_response):
    """独立事务保存用户和 AI 的聊天消息、附件及会话元数据。"""
    timestamp_dt = datetime.now()

    try:
        with get_write_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                _save_chat_rows(cursor, user_id, session_id, user_msg, ai_response, timestamp_dt)
            except SessionNotFoundError:
                conn.rollback()
                raise
            conn.commit()
            return True
    except SessionNotFoundError:
        raise
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="chat_save_write")
        return False
    except Exception:
        return False

