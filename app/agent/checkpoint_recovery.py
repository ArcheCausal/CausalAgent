"""只读检查 LangGraph checkpoint 中尚未恢复的 interrupt。"""

from __future__ import annotations

import math
import json
from typing import Any


PENDING_INTERRUPT_CHANNEL = "__interrupt__"
RESUME_CHANNEL = "__resume__"
ROOT_CHECKPOINT_NS = ""


class CheckpointRecoveryUnavailable(RuntimeError):
    """表示 stale recovery 无法可靠读取 PostgreSQL checkpoint。"""


def checkpoint_identity(job_id: str) -> tuple[str, str]:
    """返回当前 Job 的 LangGraph thread identity 和根 namespace。"""
    normalized_job_id = str(job_id).strip() if job_id is not None else ""
    if not normalized_job_id:
        raise ValueError("checkpoint identity 缺少 job_id")
    return normalized_job_id, ROOT_CHECKPOINT_NS


def _connection_options(timeout_ms: int) -> str:
    """构造只读连接选项，并把单次查询限制在有限时间内。"""
    return (
        "-c default_transaction_read_only=on "
        f"-c statement_timeout={max(1, int(timeout_ms))}"
    )


def _interrupts(value: Any) -> list[Any]:
    """把 saver 解码后的 interrupt 写入规范化为列表。"""
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _interrupt_snapshot(value: Any) -> dict[str, Any] | None:
    """提取不含内部状态的稳定问题 ID 和公开提示。"""
    for item in _interrupts(value):
        if isinstance(item, dict):
            question_id = item.get("id")
            prompt = item.get("value")
        else:
            question_id = getattr(item, "id", None)
            prompt = getattr(item, "value", None)
        if isinstance(prompt, str):
            # 旧 checkpoint 的 interrupt 值是 JSON 文本；只在能解析出公开
            # 交互结构时转换，普通问题文本保持原样。
            try:
                decoded = json.loads(prompt)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, dict):
                prompt = decoded
        if not question_id:
            continue
        interaction = None
        if isinstance(prompt, dict):
            interaction = dict(prompt)
            raw_message = prompt.get("message") or prompt.get("prompt")
            message = (
                raw_message
                if isinstance(raw_message, str) and raw_message.strip()
                else "请补充分析信息"
            )
        elif isinstance(prompt, str):
            message = prompt
        else:
            message = str(prompt) if prompt is not None else "请补充分析信息"
        snapshot: dict[str, Any] = {
            "question_id": str(question_id)[:255],
            "message": message[:20000],
        }
        if interaction is not None:
            snapshot["interaction"] = interaction
        return snapshot
    return None


def get_pending_interrupt(
    *,
    job_id: str,
    timeout_ms: int,
) -> dict[str, Any] | None:
    """读取指定 Job 根 checkpoint 的未恢复 interrupt。

    只解码 checkpoint_writes 中的控制信号，不读取 checkpoint 正文或 BLOB，避免
    stale recovery 检查把文件内容重新载入内存。与 LangGraph 运行时一致，已经有
    同一 task 的 ``__resume__`` 写入时，该 interrupt 不再视为待恢复。
    """
    try:
        # 保持 job_service 的轻量导入链可用；只有 stale recovery 真正发生时才加载
        # PostgreSQL/LangGraph 依赖。
        import psycopg
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from psycopg.rows import dict_row

        from config.checkpoint_settings import CheckpointPostgresConfig

        config = CheckpointPostgresConfig.from_env()
        config.validate(require_credentials=True)
        thread_id, checkpoint_ns = checkpoint_identity(job_id)
        serializer = JsonPlusSerializer(pickle_fallback=True)
        with psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=config.password,
            connect_timeout=max(1, math.ceil(config.connect_timeout_seconds)),
            options=_connection_options(timeout_ms),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT checkpoint_id
                    FROM checkpoints
                    WHERE thread_id = %s AND checkpoint_ns = %s
                    ORDER BY checkpoint_id DESC
                    LIMIT 1
                    """,
                    (str(thread_id), str(checkpoint_ns)),
                )
                checkpoint = cursor.fetchone()
                if not checkpoint:
                    return None

                cursor.execute(
                    """
                    SELECT task_id, channel, type, blob
                    FROM checkpoint_writes
                    WHERE thread_id = %s
                      AND checkpoint_ns = %s
                      AND checkpoint_id = %s
                    ORDER BY task_id, idx
                    """,
                    (
                        str(thread_id),
                        str(checkpoint_ns),
                        checkpoint["checkpoint_id"],
                    ),
                )
                rows = cursor.fetchall()
    except Exception as exc:
        raise CheckpointRecoveryUnavailable(
            "无法读取 PostgreSQL checkpoint 的恢复状态"
        ) from exc

    pending: dict[str, dict[str, Any]] = {}
    resumed_tasks: set[str] = set()
    try:
        for row in rows:
            task_id = str(row["task_id"])
            channel = str(row["channel"])
            if channel not in {PENDING_INTERRUPT_CHANNEL, RESUME_CHANNEL}:
                continue
            value = serializer.loads_typed((str(row["type"]), row["blob"]))
            if channel == RESUME_CHANNEL:
                resumed_tasks.add(task_id)
                continue
            snapshot = _interrupt_snapshot(value)
            if snapshot:
                pending[task_id] = snapshot
    except Exception as exc:
        raise CheckpointRecoveryUnavailable(
            "无法解码 PostgreSQL checkpoint 的恢复状态"
        ) from exc

    for task_id, snapshot in pending.items():
        if task_id not in resumed_tasks:
            return snapshot
    return None
