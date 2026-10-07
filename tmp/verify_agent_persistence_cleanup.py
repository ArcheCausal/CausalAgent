"""在真实 MySQL 与 PostgreSQL 上验证 Agent 持久化清理流程。

该脚本只写入自己创建的用户、Session、Job、checkpoint thread 和 Store
namespace，不读取也不修改其他业务数据。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from typing import Any, TypedDict

sys.path.insert(0, "/app")

from flask import Flask

from Agent.causal_agent.postgres_checkpointer import (
    build_checkpointer,
    open_checkpoint_pool,
    setup_checkpoint_schema,
)
from Agent.deep_agent.memory import memory_namespace_for_user
from Agent.deep_agent.postgres_store import (
    build_async_postgres_store,
    setup_postgres_store,
)
from Database.agent_persistence_cleanup_worker import (
    _cleanup_checkpoint_task,
    _cleanup_user_memory_task,
)
from app.agent.persistence_cleanup import (
    CHECKPOINT_TASK,
    USER_MEMORY_TASK,
    claim_cleanup_item,
    enqueue_checkpoint_cleanup_many,
    enqueue_user_memory_cleanup,
    mark_cleanup_succeeded,
)
from app.auth.service import hash_password
from app.db import get_write_connection

from langgraph.graph import END, START, StateGraph


ADMIN_PASSWORD = "verify-admin-password-2026"
USER_ID_BASE = 900001
REPORT: dict[str, Any] = {}


class CounterState(TypedDict):
    value: int


def _bump(state: CounterState) -> CounterState:
    return {"value": state["value"] + 1}


def write(sql: str, params: tuple = ()) -> None:
    with get_write_connection() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, params)
        connection.commit()


def rows(sql: str, params: tuple = ()) -> list[dict]:
    with get_write_connection() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def seed_user(user_id: int, username: str, role: str = "user") -> int:
    write(
        """
        INSERT INTO users (id, username, password_hash, role, is_active, auth_version)
        VALUES (%s, %s, %s, %s, TRUE, 1)
        """,
        (user_id, username, hash_password(ADMIN_PASSWORD), role),
    )
    return user_id


def seed_session(user_id: int) -> str:
    session_id = str(uuid.uuid4())
    write(
        "INSERT INTO sessions (id, user_id, title, message_count) VALUES (%s, %s, %s, 0)",
        (session_id, user_id, "verify"),
    )
    return session_id


def seed_job(user_id: int, session_id: str) -> str:
    job_id = str(uuid.uuid4())
    write(
        """
        INSERT INTO analysis_jobs (
            job_id, user_id, session_id, message, status, lease_epoch,
            attempt_count, recovery_count, resume_count, max_attempts,
            web_search_enabled, created_at, finished_at
        ) VALUES (%s, %s, %s, %s, 'succeeded', 0, 1, 0, 0, 3, FALSE,
                  UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
        """,
        (job_id, user_id, session_id, "verify job"),
    )
    return job_id


async def seed_threads(graph, job_id: str) -> list[str]:
    """用真实 LangGraph 运行写入父图与 Deep Agent 子图 checkpoint。"""
    from Agent.deep_agent_tools.identity import build_deep_agent_run_id

    child_thread = build_deep_agent_run_id(job_id=job_id)
    await graph.ainvoke(
        {"value": 0},
        {"configurable": {"thread_id": job_id}},
    )
    await graph.ainvoke(
        {"value": 0},
        {
            "configurable": {
                "thread_id": child_thread,
                "checkpoint_ns": "deep_agent_v1",
            }
        },
    )
    return [job_id, child_thread]


async def thread_row_count(pool, thread_id: str) -> int:
    async with pool.connection() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) AS total FROM checkpoints WHERE thread_id = %s",
                (thread_id,),
            )
            row = await cursor.fetchone()
    return int(row["total"])


async def seed_memory(store, user_id: int) -> None:
    namespace = memory_namespace_for_user(user_id)
    await store.aput(namespace, "/memories/preferences.md", {"content": "verify"})
    await store.aput(
        namespace,
        "/memories/research_background.md",
        {"content": "verify"},
    )


async def memory_keys(store, user_id: int) -> list[str]:
    items = await store.asearch(memory_namespace_for_user(user_id), limit=100)
    return sorted(item.key for item in items)


async def claim_until(saver, store, worker_id: str, task_type: str) -> dict | None:
    """领取指定类型的任务，把途中领到的其他类型放回 pending。"""
    for _ in range(20):
        item = claim_cleanup_item(worker_id, lease_seconds=60)
        if item is None:
            return None
        if item["task_type"] == task_type:
            return item
        table = (
            "checkpoint_cleanup_outbox"
            if item["task_type"] == CHECKPOINT_TASK
            else "user_memory_cleanup_outbox"
        )
        write(
            f"UPDATE {table} SET status = 'pending', lease_expires_at = NULL WHERE id = %s",
            (item["id"],),
        )
    raise AssertionError("未能领取到目标任务")


async def drain(saver, store, worker_id: str) -> list[dict]:
    processed: list[dict] = []
    while True:
        item = claim_cleanup_item(worker_id, lease_seconds=60)
        if item is None:
            return processed
        if item["task_type"] == CHECKPOINT_TASK:
            await _cleanup_checkpoint_task(saver, item)
        else:
            await _cleanup_user_memory_task(store, item)
        mark_cleanup_succeeded(item["task_type"], item["id"])
        processed.append(item)


def operation_status(operation_id: str) -> str:
    row = rows(
        "SELECT status FROM admin_operations WHERE operation_id = %s",
        (operation_id,),
    )
    return row[0]["status"] if row else "missing"


def operation_result(operation_id: str) -> dict:
    row = rows(
        "SELECT result_json FROM admin_operations WHERE operation_id = %s",
        (operation_id,),
    )
    if not row:
        return {}
    value = row[0]["result_json"]
    return json.loads(value) if isinstance(value, str) else value


async def scenario_session_delete(saver, store, pool, worker_id: str) -> None:
    """删除会话只登记 checkpoint 清理，用户长期记忆保持不变。"""
    from unittest.mock import patch

    from app.chat.routes import chat_bp

    user_id = USER_ID_BASE
    other_user_id = USER_ID_BASE + 1
    username = f"verify-session-{user_id}"
    seed_user(user_id, username)
    seed_user(other_user_id, f"verify-other-{other_user_id}")
    session_id = seed_session(user_id)
    job_ids = [seed_job(user_id, session_id) for _ in range(2)]
    threads: list[str] = []
    for job_id in job_ids:
        threads.extend(await seed_threads(graph, job_id))
    await seed_memory(store, user_id)
    await seed_memory(store, other_user_id)
    before = {
        thread: await thread_row_count(pool, thread) for thread in threads
    }
    assert all(count > 0 for count in before.values()), before

    app = Flask(__name__)
    app.secret_key = "verify-session-delete"
    app.register_blueprint(chat_bp)
    with patch(
        "app.chat.routes.get_current_session_user",
        return_value={"id": user_id, "username": username},
    ):
        with app.test_client() as client:
            response = client.post(
                "/api/delete_session",
                json={"session_id": session_id},
            )
    assert response.status_code == 202, response.data
    assert response.get_json()["checkpoint_cleanup"] == "pending"

    queued = rows(
        "SELECT thread_id FROM checkpoint_cleanup_outbox WHERE thread_id IN (%s, %s)",
        tuple(job_ids),
    )
    assert len(queued) == 2, queued
    memory_pending = rows(
        "SELECT id FROM user_memory_cleanup_outbox WHERE user_id = %s",
        (user_id,),
    )
    assert memory_pending == [], memory_pending
    assert rows("SELECT id FROM users WHERE id = %s", (user_id,))

    processed = await drain(saver, store, worker_id)
    assert {item["task_type"] for item in processed} == {CHECKPOINT_TASK}, processed
    for thread in threads:
        assert await thread_row_count(pool, thread) == 0, thread
    assert await memory_keys(store, user_id) == [
        "/memories/preferences.md",
        "/memories/research_background.md",
    ]
    assert len(await memory_keys(store, other_user_id)) == 2

    REPORT["session_delete"] = {
        "jobs": len(job_ids),
        "threads_cleared": len(threads),
        "memory_preserved": True,
    }


async def scenario_user_delete(saver, store, pool, worker_id: str) -> None:
    """用户删除同时清理父子图 checkpoint 和长期记忆 namespace。"""
    from app.admin.write_service import delete_user

    actor_id = USER_ID_BASE + 10
    actor_username = f"verify-admin-{actor_id}"
    seed_user(actor_id, actor_username, role="admin")
    other_user_id = USER_ID_BASE + 1
    user_id = USER_ID_BASE + 2
    username = f"verify-user-{user_id}"
    seed_user(user_id, username)
    session_id = seed_session(user_id)
    job_id = seed_job(user_id, session_id)
    threads = await seed_threads(graph, job_id)
    await seed_memory(store, user_id)
    assert len(await memory_keys(store, user_id)) == 2

    actor = {
        "id": actor_id,
        "username": actor_username,
        "role": "admin",
        "is_active": True,
    }
    result = delete_user(
        user_id,
        {
            "confirmed": True,
            "confirm_username": username,
            "reauth_password": ADMIN_PASSWORD,
        },
        actor=actor,
        idempotency_key=str(uuid.uuid4()),
    )
    operation_id = result["operation_id"]
    assert result["status"] == "running", result
    assert result["checkpoint_cleanup"] == {
        "status": "pending",
        "total": 1,
        "succeeded": 0,
        "failed": 0,
        "pending": 1,
    }, result
    assert result["user_memory_cleanup"]["pending"] == 1, result
    assert rows("SELECT id FROM users WHERE id = %s", (user_id,)) == []

    checkpoint_item = await claim_until(saver, store, worker_id, CHECKPOINT_TASK)
    assert checkpoint_item is not None
    await _cleanup_checkpoint_task(saver, checkpoint_item)
    mark_cleanup_succeeded(CHECKPOINT_TASK, checkpoint_item["id"])
    assert operation_status(operation_id) == "running"
    for thread in threads:
        assert await thread_row_count(pool, thread) == 0, thread
    assert len(await memory_keys(store, user_id)) == 2

    memory_item = await claim_until(saver, store, worker_id, USER_MEMORY_TASK)
    assert memory_item is not None
    deleted = await _cleanup_user_memory_task(store, memory_item)
    mark_cleanup_succeeded(USER_MEMORY_TASK, memory_item["id"])
    assert deleted == 2, deleted
    assert operation_status(operation_id) == "succeeded"
    summary = operation_result(operation_id)
    assert summary["checkpoint_cleanup"]["status"] == "succeeded"
    assert summary["user_memory_cleanup"]["status"] == "succeeded"
    assert await memory_keys(store, user_id) == []
    assert len(await memory_keys(store, other_user_id)) == 2

    REPORT["user_delete"] = {
        "operation_id": operation_id,
        "checkpoint_tasks": 1,
        "memory_tasks": 1,
        "deleted_memory_items": deleted,
        "other_user_memory_preserved": True,
    }


async def scenario_rollback(saver, store, pool, worker_id: str) -> None:
    """任一登记失败时整个用户删除事务回滚。"""
    import app.admin.write_service as write_service

    actor_id = USER_ID_BASE + 10
    actor = {
        "id": actor_id,
        "username": f"verify-admin-{actor_id}",
        "role": "admin",
        "is_active": True,
    }
    user_id = USER_ID_BASE + 3
    username = f"verify-rollback-{user_id}"
    seed_user(user_id, username)
    session_id = seed_session(user_id)
    job_id = seed_job(user_id, session_id)
    idempotency_key = str(uuid.uuid4())

    def explode(*args, **kwargs):
        raise RuntimeError("injected enqueue failure")

    original = write_service.enqueue_user_memory_cleanup
    write_service.enqueue_user_memory_cleanup = explode
    try:
        write_service.delete_user(
            user_id,
            {
                "confirmed": True,
                "confirm_username": username,
                "reauth_password": ADMIN_PASSWORD,
            },
            actor=actor,
            idempotency_key=idempotency_key,
        )
    except RuntimeError as exc:
        assert "injected enqueue failure" in str(exc)
    else:
        raise AssertionError("注入的登记失败没有中断删除")
    finally:
        write_service.enqueue_user_memory_cleanup = original

    assert rows("SELECT id FROM users WHERE id = %s", (user_id,)) != []
    assert rows(
        "SELECT id FROM checkpoint_cleanup_outbox WHERE thread_id = %s",
        (job_id,),
    ) == []
    assert rows(
        "SELECT id FROM user_memory_cleanup_outbox WHERE user_id = %s",
        (user_id,),
    ) == []
    assert rows(
        "SELECT operation_id FROM admin_operations WHERE idempotency_key = %s",
        (idempotency_key,),
    ) == []
    REPORT["rollback"] = {"rolled_back": True}


async def scenario_lease_recovery(saver, store, pool, worker_id: str) -> None:
    """租约过期可以重新领取，重复执行父子图删除保持幂等。"""
    user_id = USER_ID_BASE + 4
    seed_user(user_id, f"verify-lease-{user_id}")
    session_id = seed_session(user_id)
    job_id = seed_job(user_id, session_id)
    threads = await seed_threads(graph, job_id)
    with get_write_connection() as connection:
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()
        enqueue_checkpoint_cleanup_many(cursor, [job_id])
        connection.commit()

    first = claim_cleanup_item(worker_id, lease_seconds=1)
    assert first is not None and first["job_id"] == job_id, first
    assert first["attempts"] == 1, first
    time.sleep(2)
    second = claim_cleanup_item(worker_id, lease_seconds=60)
    assert second is not None and second["id"] == first["id"], (first, second)
    assert second["attempts"] == 2, second

    await _cleanup_checkpoint_task(saver, second)
    await _cleanup_checkpoint_task(saver, second)
    mark_cleanup_succeeded(CHECKPOINT_TASK, second["id"])
    for thread in threads:
        assert await thread_row_count(pool, thread) == 0, thread
    REPORT["lease_recovery"] = {
        "reclaimed_attempts": second["attempts"],
        "idempotent_repeat": True,
    }


async def main_async() -> None:
    global graph
    async with open_checkpoint_pool() as pool:
        await setup_checkpoint_schema(pool)
        saver = build_checkpointer(pool)
        store = build_async_postgres_store(pool=pool)
        await setup_postgres_store(store)
        graph = _build_graph(saver)
        worker_id = f"verify:{uuid.uuid4()}"
        await scenario_session_delete(saver, store, pool, worker_id)
        await scenario_user_delete(saver, store, pool, worker_id)
        await scenario_rollback(saver, store, pool, worker_id)
        await scenario_lease_recovery(saver, store, pool, worker_id)


def _build_graph(saver):
    builder = StateGraph(CounterState)
    builder.add_node("bump", _bump)
    builder.add_edge(START, "bump")
    builder.add_edge("bump", END)
    return builder.compile(checkpointer=saver)


def main() -> int:
    flask_app = Flask(__name__)
    with flask_app.app_context():
        asyncio.run(main_async())
    print(json.dumps(REPORT, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
