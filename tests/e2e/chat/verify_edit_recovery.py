"""Stage loss of a claimed historical branch, then verify recovery by the real Worker."""

import argparse
import asyncio
from uuid import uuid4

from Agent.causal_agent.postgres_checkpointer import build_checkpointer, open_checkpoint_pool
from app.agent import job_service as jobs
from app.agent.message_edit import EditConflict, edit_message
from app.agent.worker.checkpoint_fork import copy_interrupt_checkpoint
from app.chat.services import get_job_chat_history
from app.db import get_read_connection, get_write_connection
from config.database_settings import database_settings

RECOVERY_MARKER = "RECOVERED_BRANCH_77"


def read_boundary(session_id):
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, conversation_version FROM sessions WHERE id = %s",
            (session_id,),
        )
        session = cursor.fetchone()
        cursor.execute(
            """
            SELECT m.id FROM chat_messages m JOIN analysis_job_inputs i ON i.chat_message_id = m.id
            WHERE m.session_id = %s AND m.replaced_by_job_id IS NULL AND i.fork_from IS NOT NULL
            ORDER BY m.id DESC LIMIT 1
            """,
            (session_id,),
        )
        target = cursor.fetchone()["id"]
        cursor.execute(
            """
            SELECT id, checkpoint_id FROM chat_messages
            WHERE session_id = %s AND message_type = 'ai' AND id < %s AND replaced_by_job_id IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (session_id, target),
        )
        return session, target, cursor.fetchone()


async def stage(session_id):
    session, target, question = read_boundary(session_id)
    result = edit_message(
        user_id=session["user_id"], session_id=session_id, message_id=target,
        message=f"谢谢，请用一句话解释什么是混杂因素。{RECOVERY_MARKER}",
        version=session["conversation_version"], checkpoint_id=question["checkpoint_id"], reanalyze=False,
        idempotency_key=str(uuid4()), request_id=str(uuid4()),
    )
    assert result["execution_mode"] == "resume_checkpoint"
    job_id = result["job_id"]
    claimed = jobs.claim_next_job("abandoned-edit-acceptance")
    assert claimed["job_id"] == job_id
    fork = jobs.get_initial_input_value(job_id)["fork_from"]
    async with open_checkpoint_pool() as pool:
        await copy_interrupt_checkpoint(
            build_checkpointer(pool), source=fork["source_job_id"], anchor=fork["checkpoint_id"], target=job_id,
        )
    with get_write_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE analysis_jobs SET heartbeat_at = UTC_TIMESTAMP(6) - INTERVAL 1 DAY
            WHERE job_id = %s AND worker_id = 'abandoned-edit-acceptance'
            """,
            (job_id,),
        )
        assert cursor.rowcount == 1
        connection.commit()
    print(job_id)


async def check(job_id):
    for _ in range(120):
        job = jobs.get_job_by_id(job_id)
        if job["status"] in jobs.TERMINAL_STATUSES:
            break
        await asyncio.sleep(1)
    assert job["status"] == "succeeded" and job["recovery_count"] == 1
    assert job["attempt_count"] == 2 and job["lease_epoch"] == 2
    async with open_checkpoint_pool() as pool:
        row = await build_checkpointer(pool).aget_tuple({"configurable": {"thread_id": job_id}})
        state = row.checkpoint["channel_values"]
        human = [str(message.content) for message in state["messages"] if message.type == "human"]
        assert sum(RECOVERY_MARKER in message for message in human) == 1
        text = "\n".join(str(message.content) for message in state["messages"])
        assert all(marker not in text for marker in ("ORIGINAL_ANSWER_77", "BRANCH_A_77", "BRANCH_B_77"))
    print("Worker restarted from the branch's copied interrupt and consumed the submitted answer once.")


async def legacy(session_id):
    session, target, question = read_boundary(session_id)
    with get_write_connection() as connection:
        connection.cursor().execute(
            """
            UPDATE chat_messages SET checkpoint_id = NULL, checkpoint_namespace = NULL,
                checkpoint_question_id = NULL WHERE session_id = %s AND id = %s
            """,
            (session_id, question["id"]),
        )
        connection.commit()
    request = {
        "user_id": session["user_id"], "session_id": session_id, "message_id": target,
        "version": session["conversation_version"], "checkpoint_id": None,
        "message": "请用一句话解释什么是因果关系。",
        "request_id": str(uuid4()), "idempotency_key": str(uuid4()),
    }
    try:
        edit_message(**request, reanalyze=False)
    except EditConflict as error:
        assert error.code == "edit_reanalysis_required"
    else:
        raise AssertionError("A legacy question branch unexpectedly started without confirmation")
    assert read_boundary(session_id)[0]["conversation_version"] == session["conversation_version"]
    result = edit_message(**{**request, "idempotency_key": str(uuid4())}, reanalyze=True)
    assert result["execution_mode"] == "reanalyze"
    assert not jobs.get_initial_input_value(result["job_id"])["fork_from"]
    messages = get_job_chat_history(session_id, session["user_id"], result["message_id"], 20)
    assert len(messages) == 3 and messages[-1].content == request["message"]
    for _ in range(90):
        job = jobs.get_job_by_id(result["job_id"])
        if job["status"] in jobs.TERMINAL_STATUSES:
            break
        await asyncio.sleep(1)
    assert job["status"] == "succeeded"
    print("Legacy history: explicit consent started reanalysis with the retained prefix.")


if __name__ == "__main__":
    if not database_settings.MYSQL_DATABASE.startswith("causalagent_edit"):
        raise SystemExit("This acceptance test requires a disposable causalagent_edit* database.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "check", "legacy"))
    parser.add_argument("identifier")
    args = parser.parse_args()
    action = {"stage": stage, "check": check, "legacy": legacy}[args.action]
    asyncio.run(action(args.identifier))
