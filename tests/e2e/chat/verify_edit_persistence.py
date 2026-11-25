"""Read-only MySQL/PostgreSQL checks after the real browser interrupt acceptance."""

import argparse
import asyncio
import json

from Agent.causal_agent.postgres_checkpointer import build_checkpointer, open_checkpoint_pool
from Agent.deep_agent.memory import MEMORY_PATHS, memory_namespace_for_user
from Agent.deep_agent.postgres_store import build_async_postgres_store
from Agent.deep_agent_tools.identity import build_deep_agent_run_id
from app.chat.services import get_job_chat_history
from app.db import get_read_connection


def inspect_mysql(session_id: str):
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT s.user_id, s.message_count, c.latest_preprocess_version_id, c.latest_report_message_id
            FROM sessions s JOIN analysis_contexts c ON c.analysis_context_id = s.active_analysis_context_id
            WHERE s.id = %s
            """,
            (session_id,),
        )
        session = cursor.fetchone()
        assert session and session["message_count"] == 4
        cursor.execute(
            """
            SELECT j.job_id, i.sequence, i.fork_from, i.chat_message_id, m.replaced_by_job_id,
                   p.id AS version_id, p.source_file_hash, p.recipe, p.call_id, p.output,
                   p.parent_version_id
            FROM analysis_jobs j JOIN analysis_job_inputs i ON i.job_id = j.job_id
            JOIN chat_messages m ON m.id = i.chat_message_id
            JOIN analysis_preprocess_versions p ON p.job_id = j.job_id AND p.input_id = i.input_id
            WHERE j.session_id = %s ORDER BY p.id
            """,
            (session_id,),
        )
        source, branch_a, branch_b = cursor.fetchall()
        assert source["sequence"] == 1 and branch_a["sequence"] == branch_b["sequence"] == 0
        assert source["replaced_by_job_id"] == branch_a["job_id"]
        assert branch_a["replaced_by_job_id"] == branch_b["job_id"]
        assert branch_b["replaced_by_job_id"] is None
        assert len({row["version_id"] for row in (source, branch_a, branch_b)}) == 3
        assert len({row["source_file_hash"] for row in (source, branch_a, branch_b)}) == 1
        assert all(row["call_id"] and json.loads(row["output"])["chart_assets"] for row in (source, branch_a, branch_b))
        assert [json.loads(row["recipe"])["target"] for row in (source, branch_a, branch_b)] == ["pmek", "PKA", "P38"]
        assert session["latest_preprocess_version_id"] == branch_b["version_id"]
        cursor.execute(
            "SELECT analysis_job_id, replaced_by_job_id FROM chat_messages WHERE id = %s",
            (session["latest_report_message_id"],),
        )
        report = cursor.fetchone()
        assert report["analysis_job_id"] == branch_b["job_id"] and report["replaced_by_job_id"] is None
        cursor.execute(
            """
            SELECT COUNT(*) AS visible FROM analysis_contexts
            WHERE session_id = %s AND status = 'active' AND target IN ('pmek', 'PKA')
            """,
            (session_id,),
        )
        assert cursor.fetchone()["visible"] == 0
    history = get_job_chat_history(session_id, session["user_id"], branch_b["chat_message_id"], 50)
    assert len(history) == 3
    assert "BRANCH_B_77" in history[-1].content
    assert not any("ORIGINAL_ANSWER_77" in item.content or "BRANCH_A_77" in item.content for item in history)
    return session["user_id"], source, branch_a, branch_b


async def inspect_postgres(user_id, source, branch_a, branch_b):
    async with open_checkpoint_pool() as pool:
        saver = build_checkpointer(pool)
        store = build_async_postgres_store(pool=pool)
        for path in MEMORY_PATHS:
            assert await store.aget(memory_namespace_for_user(user_id), path)
        for row, expected, excluded in (
            (source, "ORIGINAL_ANSWER_77", {"BRANCH_A_77", "BRANCH_B_77"}),
            (branch_a, "BRANCH_A_77", {"ORIGINAL_ANSWER_77", "BRANCH_B_77"}),
            (branch_b, "BRANCH_B_77", {"ORIGINAL_ANSWER_77", "BRANCH_A_77"}),
        ):
            job_id = row["job_id"]
            root = await saver.aget_tuple({"configurable": {"thread_id": job_id, "checkpoint_ns": ""}})
            assert root.metadata["job_id"] == job_id
            values = root.checkpoint["channel_values"]
            assert values["job_id"] == job_id
            contents = "\n".join(str(message.content) for message in values["messages"])
            assert expected in contents and not any(marker in contents for marker in excluded)
            child_id = build_deep_agent_run_id(job_id=job_id)
            child = await saver.aget_tuple({"configurable": {"thread_id": child_id, "checkpoint_ns": ""}})
            assert child and child.checkpoint["channel_values"]["deep_agent_run_id"] == child_id
            child_values = child.checkpoint["channel_values"]
            child_messages = "\n".join(str(message.content) for message in child_values.get("messages", []))
            assert not any(marker in child_messages for marker in excluded)
            if row["fork_from"]:
                fork = json.loads(row["fork_from"])
                assert fork["source_job_id"] == source["job_id"]
                anchor = await saver.aget_tuple({
                    "configurable": {
                        "thread_id": job_id, "checkpoint_ns": "",
                        "checkpoint_id": fork["checkpoint_id"],
                    },
                })
                assert anchor and anchor.metadata["job_id"] == job_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    args = parser.parse_args()
    asyncio.run(inspect_postgres(*inspect_mysql(args.session_id)))
    print("MySQL and PostgreSQL: immutable A/B versions, current report and context, model prefix and independent parent/child threads passed.")


if __name__ == "__main__":
    main()
