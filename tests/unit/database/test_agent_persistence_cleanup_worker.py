"""Agent 持久化清理 worker 的父子图删除和长期记忆清理边界。"""

from __future__ import annotations

import asyncio

import pytest

from Agent.deep_agent.memory import memory_namespace_for_user
from Agent.deep_agent.postgres_store import purge_store_namespace
from Agent.deep_agent_tools.identity import build_deep_agent_run_id
from Database.agent_persistence_cleanup_worker import (
    _cleanup_checkpoint_task,
    _cleanup_user_memory_task,
)


JOB_ID = "6f1d5f2a-4c9b-4d0e-9f0d-2b7c8a1e5d33"


class RecordingSaver:
    """记录被删除的 thread，并按需在指定次序失败。"""

    def __init__(self, fail_on: int | None = None):
        self.threads: list[str] = []
        self.fail_on = fail_on

    async def adelete_thread(self, thread_id: str) -> None:
        self.threads.append(thread_id)
        if self.fail_on is not None and len(self.threads) == self.fail_on:
            raise RuntimeError("delete failed")


class MemoryItem:
    """官方 SearchItem 的最小只读替身。"""

    def __init__(self, namespace: tuple[str, ...], key: str):
        self.namespace = namespace
        self.key = key


class RecordingStore:
    """按 namespace 保存条目的 Store 替身，支持枚举和按条目删除。"""

    def __init__(self, entries: dict[tuple[str, ...], set[str]]):
        self.entries = entries
        self.deleted: list[tuple[tuple[str, ...], str]] = []

    async def asearch(self, namespace_prefix, *, limit=10, offset=0):
        prefix = tuple(namespace_prefix)
        matched: list[MemoryItem] = []
        for namespace, keys in self.entries.items():
            if namespace[: len(prefix)] != prefix:
                continue
            for key in sorted(keys):
                matched.append(MemoryItem(namespace, key))
        return matched[offset : offset + limit]

    async def adelete(self, namespace, key):
        self.deleted.append((tuple(namespace), key))
        self.entries.get(tuple(namespace), set()).discard(key)


class StubbornStore(RecordingStore):
    """删除不生效的 Store 替身，用于验证收敛上限。"""

    async def adelete(self, namespace, key):
        self.deleted.append((tuple(namespace), key))


def test_checkpoint_task_deletes_parent_and_child_threads() -> None:
    saver = RecordingSaver()

    asyncio.run(_cleanup_checkpoint_task(saver, {"task_type": "checkpoint", "job_id": JOB_ID}))

    assert saver.threads == [JOB_ID, build_deep_agent_run_id(job_id=JOB_ID)]
    assert saver.threads[1].startswith("deep-agent:")


def test_checkpoint_task_partial_failure_is_reported() -> None:
    """子图删除失败时整项视为失败，交给 outbox 重试。"""
    saver = RecordingSaver(fail_on=2)

    with pytest.raises(RuntimeError):
        asyncio.run(
            _cleanup_checkpoint_task(saver, {"task_type": "checkpoint", "job_id": JOB_ID})
        )

    assert saver.threads == [JOB_ID, build_deep_agent_run_id(job_id=JOB_ID)]


def test_user_memory_task_clears_only_requested_user_namespace() -> None:
    user_namespace = memory_namespace_for_user(42)
    other_namespace = memory_namespace_for_user(43)
    nested_namespace = (*user_namespace, "archive")
    store = RecordingStore(
        {
            user_namespace: {"/memories/preferences.md", "/memories/research_background.md"},
            nested_namespace: {"/memories/legacy.md"},
            other_namespace: {"/memories/preferences.md"},
        }
    )

    deleted = asyncio.run(
        _cleanup_user_memory_task(
            store,
            {"task_type": "user_memory", "user_id": 42},
        )
    )

    assert deleted == 3
    assert store.entries[user_namespace] == set()
    assert store.entries[nested_namespace] == set()
    assert store.entries[other_namespace] == {"/memories/preferences.md"}
    assert (nested_namespace, "/memories/legacy.md") in store.deleted


def test_user_memory_task_returns_zero_for_unknown_user() -> None:
    store = RecordingStore({memory_namespace_for_user(43): {"/memories/preferences.md"}})

    deleted = asyncio.run(
        _cleanup_user_memory_task(store, {"task_type": "user_memory", "user_id": 42})
    )

    assert deleted == 0
    assert store.deleted == []


def test_purge_namespace_fails_when_store_does_not_converge() -> None:
    store = StubbornStore({memory_namespace_for_user(42): {"/memories/preferences.md"}})

    with pytest.raises(RuntimeError):
        asyncio.run(purge_store_namespace(store, memory_namespace_for_user(42)))


def test_purge_namespace_rejects_blank_namespace() -> None:
    store = RecordingStore({})

    with pytest.raises(ValueError):
        asyncio.run(purge_store_namespace(store, ("causalagent", "", "42")))


def test_memory_namespace_requires_positive_user_id() -> None:
    assert memory_namespace_for_user(7) == ("causalagent", "memory", "7")
    with pytest.raises(ValueError):
        memory_namespace_for_user(0)
    with pytest.raises(ValueError):
        memory_namespace_for_user(True)
