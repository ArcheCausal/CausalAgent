"""Exercise PostgreSQL copy and worker recovery with a small real LangGraph."""

import argparse
import asyncio
from typing import TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from Agent.causal_agent.postgres_checkpointer import build_checkpointer, open_checkpoint_pool
from Agent.deep_agent_tools.identity import build_deep_agent_run_id
from app.agent.checkpoint_recovery import get_pending_interrupt
from app.agent.worker.checkpoint_fork import copy_interrupt_checkpoint
from app.db import get_read_connection
from Database.agent_persistence_cleanup_worker import _cleanup_checkpoint_task


class State(TypedDict, total=False):
    job_id: str
    prefix: list[str]
    answer: str
    result: str


def ask(_: State):
    return {"answer": interrupt("目标变量？")}


def summarize(state: State):
    return {"result": state["prefix"][0] + state["answer"]}


def config(thread_id, checkpoint_id=None):
    return {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": checkpoint_id},
        "metadata": {"job_id": thread_id},
    }


async def main():
    builder = StateGraph(State)
    builder.add_node("fold", ask)
    builder.add_node("summarize", summarize)
    builder.add_edge(START, "fold")
    builder.add_edge("fold", "summarize")
    builder.add_edge("summarize", END)
    source, first, second = (str(uuid4()) for _ in range(3))
    async with open_checkpoint_pool() as pool:
        saver = build_checkpointer(pool)
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"job_id": source, "prefix": ["prefix-"]}, config(source), durability="sync")
        snapshot = await graph.aget_state(config(source))
        anchor = snapshot.config["configurable"]["checkpoint_id"]
        question = snapshot.interrupts[0]
        await graph.ainvoke(Command(resume="original"), config(source), durability="sync")
        assert (await graph.aget_state(config(source))).values["result"] == "prefix-original"
        checked = await asyncio.to_thread(
            get_pending_interrupt, job_id=source, checkpoint_id=anchor, timeout_ms=3000,
        )
        assert checked["question_id"] == question.id
        historical = await graph.aget_state(config(source, anchor))
        assert historical.next == ("fold",) and historical.interrupts[0].id == question.id
        await asyncio.gather(*[
            copy_interrupt_checkpoint(saver, source=source, anchor=anchor, target=first)
            for _ in range(2)
        ])
        fork = await graph.aget_state(config(first))
        assert fork.values["job_id"] == first
        assert fork.interrupts[0].id == question.id and "answer" not in fork.values
        paused = builder.compile(checkpointer=saver, interrupt_before=["summarize"])
        await paused.ainvoke(Command(resume={question.id: "A"}), config(first), durability="sync")
        assert (await graph.aget_state(config(first))).next == ("summarize",)

    async with open_checkpoint_pool() as pool:
        saver = build_checkpointer(pool)
        graph = builder.compile(checkpointer=saver)
        await copy_interrupt_checkpoint(saver, source=source, anchor=anchor, target=first)
        assert (await graph.ainvoke(None, config(first), durability="sync"))["result"] == "prefix-A"
        await copy_interrupt_checkpoint(saver, source=source, anchor=anchor, target=second)
        child_builder = StateGraph(State)
        child_builder.add_node("summarize", summarize)
        child_builder.add_edge(START, "summarize")
        child_builder.add_edge("summarize", END)
        child = child_builder.compile(checkpointer=saver)
        for job in (source, first, second):
            child_id = build_deep_agent_run_id(job_id=job)
            await child.ainvoke({"prefix": ["child-"], "answer": job}, config(child_id), durability="sync")
        await _cleanup_checkpoint_task(saver, {"job_id": source})
        await _cleanup_checkpoint_task(saver, {"job_id": first})
        assert not (await graph.aget_state(config(source))).values
        assert not (await child.aget_state(config(build_deep_agent_run_id(job_id=source)))).values
        assert (await child.aget_state(config(build_deep_agent_run_id(job_id=second)))).values["answer"] == second
        answer = await graph.ainvoke(Command(resume={question.id: "B"}), config(second), durability="sync")
        assert answer["result"] == "prefix-B" and answer["job_id"] == second
        await _cleanup_checkpoint_task(saver, {"job_id": second})
        assert not (await graph.aget_state(config(second))).values

    print("PostgreSQL: verified interrupted root, concurrent fork creation, restart and cleanup.")


async def verify_cleanup(job_ids):
    threads = [str(UUID(job_id)) for job_id in job_ids]
    placeholders = ", ".join(["%s"] * len(threads))
    for _ in range(45):
        with get_read_connection(consistency="strong") as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                f"SELECT status FROM checkpoint_cleanup_outbox WHERE thread_id IN ({placeholders})",
                tuple(threads),
            )
            rows = cursor.fetchall()
        if len(rows) == len(threads) and all(row["status"] == "succeeded" for row in rows):
            break
        await asyncio.sleep(1)
    else:
        raise AssertionError("Checkpoint cleanup outbox did not converge")
    threads += [build_deep_agent_run_id(job_id=job_id) for job_id in threads]
    async with open_checkpoint_pool() as pool:
        async with pool.connection() as connection:
            async with connection.cursor() as cursor:
                for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                    await cursor.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE thread_id = ANY(%s)", (threads,))
                    assert (await cursor.fetchone())["count"] == 0
    print("Cleanup worker consumed all deleted session Jobs and removed their PostgreSQL checkpoint rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", nargs="+", metavar="JOB_ID")
    args = parser.parse_args()
    asyncio.run(verify_cleanup(args.cleanup) if args.cleanup else main())
