"""Agent 持久化清理 outbox 的表路由、聚合和领取顺序规则。"""

from __future__ import annotations

import json

import pytest

from app.agent import persistence_cleanup
from app.agent.persistence_cleanup import (
    CHECKPOINT_TASK,
    USER_MEMORY_TASK,
    _claim_order,
    _table,
    _task_summary,
    _update_operation_aggregate,
    enqueue_user_memory_cleanup,
)


class EnqueueCursor:
    """记录登记语句并按目标状态返回单行。"""

    def __init__(self, status: str):
        self.status = status
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return {"status": self.status}


class AggregateCursor:
    """为管理员操作聚合返回操作行和两张 outbox 的计数。"""

    def __init__(self, operation, counts):
        self.operation = operation
        self.counts = counts
        self.updates: list[tuple[str, tuple]] = []
        self._last_sql = ""

    def execute(self, sql, params=None):
        statement = " ".join(sql.split())
        self._last_sql = statement
        if statement.startswith("UPDATE admin_operations"):
            self.updates.append((statement, params))

    def fetchone(self):
        if "FROM admin_operations" in self._last_sql:
            return self.operation
        for table, counts in self.counts.items():
            if f"FROM {table}" in self._last_sql:
                return {
                    "total_count": counts.get("total", 0),
                    "succeeded_count": counts.get("succeeded", 0),
                    "failed_count": counts.get("failed", 0),
                    "pending_count": counts.get("pending", 0),
                }
        return None


def _operation(target_count: int = 1, result: dict | None = None) -> dict:
    return {
        "status": "running",
        "target_count": target_count,
        "result_json": json.dumps(result or {}, ensure_ascii=False),
    }


def test_unknown_task_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        _table("unknown_task")


def test_claim_order_rotates_start_table() -> None:
    """两类任务轮转领取，保证任何一类都不会长期排在后面。"""
    persistence_cleanup._claim_rotation = 0
    first = _claim_order()
    second = _claim_order()

    assert first == (CHECKPOINT_TASK, USER_MEMORY_TASK)
    assert second == (USER_MEMORY_TASK, CHECKPOINT_TASK)


def test_enqueue_user_memory_cleanup_targets_its_own_table() -> None:
    pending_cursor = EnqueueCursor("pending")
    assert enqueue_user_memory_cleanup(pending_cursor, 42) is True
    insert_sql = pending_cursor.statements[0][0]
    assert "INSERT INTO user_memory_cleanup_outbox" in insert_sql
    assert pending_cursor.statements[0][1] == (42, None)
    assert (
        "SELECT status FROM user_memory_cleanup_outbox WHERE user_id = %s"
        == pending_cursor.statements[1][0]
    )

    assert enqueue_user_memory_cleanup(EnqueueCursor("succeeded"), 42) is False


def test_task_summary_maps_counts_to_status() -> None:
    assert _task_summary({"total": 2, "succeeded": 2, "failed": 0, "pending": 0})["status"] == "succeeded"
    assert _task_summary({"total": 2, "succeeded": 1, "failed": 0, "pending": 1})["status"] == "pending"
    assert _task_summary({"total": 2, "succeeded": 0, "failed": 2, "pending": 0})["status"] == "failed"


def test_operation_succeeds_only_after_both_outboxes_complete() -> None:
    """任一类型仍有 pending 时操作保持 running，不能提前进入成功终态。"""
    cursor = AggregateCursor(
        _operation(),
        {
            "checkpoint_cleanup_outbox": {"total": 3, "succeeded": 3},
            "user_memory_cleanup_outbox": {"total": 1, "succeeded": 0, "pending": 1},
        },
    )

    _update_operation_aggregate(cursor, "operation-1")

    statement, params = cursor.updates[0]
    assert params[0] == "running"
    assert "completed_at = NULL" in statement
    result = json.loads(params[3])
    assert result["status"] == "running"
    assert result["checkpoint_cleanup"] == {
        "status": "succeeded",
        "total": 3,
        "succeeded": 3,
        "failed": 0,
        "pending": 0,
    }
    assert result["user_memory_cleanup"]["pending"] == 1


def test_operation_fails_when_user_memory_cleanup_failed() -> None:
    cursor = AggregateCursor(
        _operation(),
        {
            "checkpoint_cleanup_outbox": {"total": 1, "succeeded": 1},
            "user_memory_cleanup_outbox": {"total": 1, "failed": 1},
        },
    )

    _update_operation_aggregate(cursor, "operation-1")

    statement, params = cursor.updates[0]
    assert params[0] == "failed"
    assert "UTC_TIMESTAMP(6)" in statement
    assert json.loads(params[3])["user_memory_cleanup"]["status"] == "failed"


def test_operation_succeeds_when_every_task_is_done() -> None:
    cursor = AggregateCursor(
        _operation(),
        {
            "checkpoint_cleanup_outbox": {"total": 2, "succeeded": 2},
            "user_memory_cleanup_outbox": {"total": 1, "succeeded": 1},
        },
    )

    _update_operation_aggregate(cursor, "operation-1")

    statement, params = cursor.updates[0]
    assert params[0] == "succeeded"
    assert params[1] == 1
    assert "completed_at = UTC_TIMESTAMP(6)" in statement


def test_operation_without_tasks_is_left_untouched() -> None:
    cursor = AggregateCursor(
        _operation(),
        {
            "checkpoint_cleanup_outbox": {},
            "user_memory_cleanup_outbox": {},
        },
    )

    _update_operation_aggregate(cursor, "operation-1")

    assert cursor.updates == []
