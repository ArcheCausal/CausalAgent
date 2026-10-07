"""阶段一数据生命周期与 Agent 持久化清理 outbox 修复 CLI。

默认只生成有限修复清单；只有同时提供 ``--apply`` 和精确数据库名确认时，
才会在单个主库事务中删除本批已再次验证仍然孤立的记录。migration 不调用
此脚本，也不会静默清理历史数据。
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import get_read_connection, get_write_connection
from config.settings import settings


DEFAULT_BATCH_LIMIT = 100
MAX_BATCH_LIMIT = 1000
OUTBOX_TABLES = ("checkpoint_cleanup_outbox", "user_memory_cleanup_outbox")


def _parse_args() -> argparse.Namespace:
    """解析 dry-run、有限批次和显式数据库确认参数。"""
    parser = argparse.ArgumentParser(
    description="列出 archived session 孤立记录或重置失败的 Agent 持久化清理 outbox",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_LIMIT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行本批物理删除；默认仅 dry-run",
    )
    parser.add_argument(
        "--confirm-database",
        default="",
        help="--apply 时必须精确填写 MYSQL_DATABASE",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_BATCH_LIMIT:
        parser.error(f"--limit 必须在 1 到 {MAX_BATCH_LIMIT} 之间")
    if args.apply and args.confirm_database != settings.MYSQL_DATABASE:
        parser.error("--apply 需要 --confirm-database 精确匹配当前 MYSQL_DATABASE")
    return args


def _scan(cursor, *, limit: int, for_update: bool) -> dict[str, list[Any]]:
    """扫描 archived session 孤立记录和失败/过期的 cleanup outbox 主键。"""
    lock_clause = " FOR UPDATE" if for_update else ""
    cursor.execute(
        f"""
        SELECT a.id
        FROM archived_sessions AS a
        LEFT JOIN users AS u ON u.id = a.user_id
        WHERE u.id IS NULL
        ORDER BY a.id
        LIMIT %s{lock_clause}
        """,
        (limit,),
    )
    archived_sessions = [row["id"] for row in cursor.fetchall()]
    candidates: dict[str, list[Any]] = {"archived_sessions": archived_sessions}
    for table in OUTBOX_TABLES:
        cursor.execute(
            f"""
            SELECT id
            FROM {table}
            WHERE status = 'failed'
               OR (status = 'processing'
                   AND lease_expires_at IS NOT NULL
                   AND lease_expires_at < UTC_TIMESTAMP(6))
            ORDER BY id
            LIMIT %s{lock_clause}
            """,
            (limit,),
        )
        candidates[table] = [int(row["id"]) for row in cursor.fetchall()]
    return candidates


def build_repair_plan(limit: int) -> dict[str, Any]:
    """通过主库强一致读生成不含正文的有限 dry-run 修复清单。"""
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        candidates = _scan(cursor, limit=limit, for_update=False)
    return {
        "mode": "dry-run",
        "limit_per_category": limit,
        "candidate_counts": {
            key: len(values)
            for key, values in candidates.items()
        },
        "candidates": candidates,
    }


def apply_repair_plan(limit: int) -> dict[str, Any]:
    """在主库事务中再次锁定并删除本批仍然孤立的记录。"""
    connection = get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction(isolation_level="READ COMMITTED")
        candidates = _scan(cursor, limit=limit, for_update=True)

        reset_cleanup: dict[str, int] = {}
        for table in OUTBOX_TABLES:
            reset_cleanup[table] = 0
            for outbox_id in candidates[table]:
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET status = 'pending', attempts = 0,
                        available_at = UTC_TIMESTAMP(6),
                        lease_expires_at = NULL, last_error = NULL,
                        completed_at = NULL
                    WHERE id = %s
                      AND (status = 'failed'
                           OR (status = 'processing'
                               AND lease_expires_at IS NOT NULL
                               AND lease_expires_at < UTC_TIMESTAMP(6)))
                    """,
                    (outbox_id,),
                )
                reset_cleanup[table] += cursor.rowcount

        deleted_archives = 0
        for archived_id in candidates["archived_sessions"]:
            cursor.execute(
                """
                DELETE a
                FROM archived_sessions AS a
                LEFT JOIN users AS u ON u.id = a.user_id
                WHERE a.id = %s AND u.id IS NULL
                """,
                (archived_id,),
            )
            deleted_archives += cursor.rowcount

        connection.commit()
        return {
            "mode": "apply",
            "limit_per_category": limit,
            "deleted": {
                "archived_sessions": deleted_archives,
                "outbox_reset": {
                    table: reset_cleanup[table] for table in OUTBOX_TABLES
                },
            },
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    """输出 JSON 修复清单或已确认批次的删除计数。"""
    args = _parse_args()
    result = (
        apply_repair_plan(args.limit)
        if args.apply
        else build_repair_plan(args.limit)
    )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
