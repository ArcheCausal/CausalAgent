"""验证会话历史事件查询使用正确的 MySQL 复合索引。

本测试需要真实 MySQL 主从，不属于默认 pytest 与容器 unit 测试范围。
设置 CAUSALAGENT_RUN_DB_INTEGRATION=1 并从可访问数据库的环境执行。
"""

from __future__ import annotations

import os

import pytest


if os.environ.get("CAUSALAGENT_RUN_DB_INTEGRATION") != "1":
    pytestmark = pytest.mark.skip(
        reason="设置 CAUSALAGENT_RUN_DB_INTEGRATION=1 后运行 MySQL 集成测试"
    )
else:
    pytest.importorskip("mysql.connector")


EVENTS_INDEX = "idx_analysis_job_events_job_id"
EVENTS_QUERY = """
    SELECT id, job_id, event_type, payload_json, created_at
    FROM analysis_job_events FORCE INDEX (idx_analysis_job_events_job_id)
    WHERE job_id IN (%s, %s)
    ORDER BY job_id, id
"""


def test_session_event_history_index_and_query_plan():
    """真实 MySQL 中应存在目标索引，且会话历史查询应选中它。"""
    from app.db import get_read_connection

    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT column_name AS column_name, seq_in_index AS seq_in_index
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'analysis_job_events'
              AND index_name = %s
            ORDER BY seq_in_index
            """,
            (EVENTS_INDEX,),
        )
        index_columns = [
            (row["column_name"], int(row["seq_in_index"]))
            for row in cursor.fetchall()
        ]
        assert index_columns == [("job_id", 1), ("id", 2)]

        cursor.execute(
            "EXPLAIN " + EVENTS_QUERY,
            (
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ),
        )
        plan = cursor.fetchone()

    assert plan is not None
    assert plan["key"] == EVENTS_INDEX
    assert "Using filesort" not in (plan.get("Extra") or "")
