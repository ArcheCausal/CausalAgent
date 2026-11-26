"""Exercise the configured LangChain compaction middleware with real branch inputs and model."""

import argparse
import asyncio

from langchain_core.messages import convert_to_messages
from langgraph.runtime import Runtime

from Agent.causal_agent.postgres_checkpointer import build_checkpointer, open_checkpoint_pool
from Agent.deep_agent.graph import _build_summarization_middleware, _configure_model_profile
from Agent.deep_agent.profile import DeepAgentProfile
from Agent.deep_agent.state import to_deep_agent_input
from app.agent.worker.runtime import create_deep_agent_model
from app.db import get_read_connection


async def main(session_id):
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT job_id FROM analysis_jobs WHERE session_id = %s ORDER BY id LIMIT 3", (session_id,),
        )
        source, _, replacement = [row["job_id"] for row in cursor.fetchall()]
    model = create_deep_agent_model()
    profile = DeepAgentProfile(model_name=model.model_name, context_window_tokens=1024)
    middleware = _build_summarization_middleware(_configure_model_profile(model, profile), profile)
    async with open_checkpoint_pool() as pool:
        saver = build_checkpointer(pool)
        for job_id in (source, replacement):
            root = await saver.aget_tuple({"configurable": {"thread_id": job_id}})
            projected = to_deep_agent_input(root.checkpoint["channel_values"], reset_execution_artifacts=True)
            messages = convert_to_messages(projected["messages"])
            history = "\n".join(str(message.content) for message in messages)
            if job_id == source:
                assert "ORIGINAL_ANSWER_77" in history
            else:
                assert "BRANCH_B_77" in history
                assert "ORIGINAL_ANSWER_77" not in history and "BRANCH_A_77" not in history
            assert middleware.token_counter(messages) >= 1024 * profile.summary_trigger_ratio
            update = await middleware.abefore_model({"messages": messages}, Runtime())
            assert update is not None
            if job_id == replacement:
                compressed = "\n".join(str(message.content) for message in update["messages"])
                assert "ORIGINAL_ANSWER_77" not in compressed and "BRANCH_A_77" not in compressed
    print("Real model: token-triggered compression of branch inputs excludes replaced answer markers.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    asyncio.run(main(parser.parse_args().session_id))
