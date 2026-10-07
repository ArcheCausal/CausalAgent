"""P0 parent-graph checkpoint recovery against a real PostgreSQL saver."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.spike.p0_deep_agent import (
    BindableFakeChatModel,
    RuntimeContext,
    SpikeState,
    _filesystem,
    _register_profile,
    _valid_decision_args,
)
from deepagents import create_deep_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from Agent.deep_agent_tools.models import FinalAnalysisDecision


def _connection_kwargs() -> dict[str, object]:
    return {
        "host": os.environ.get("CHECKPOINT_POSTGRES_HOST", "127.0.0.1"),
        "port": int(os.environ.get("CHECKPOINT_POSTGRES_PORT", "5432")),
        "dbname": os.environ.get("CHECKPOINT_POSTGRES_DATABASE", "causalagent_checkpoints"),
        "user": os.environ.get("CHECKPOINT_POSTGRES_USER", "causalagent_checkpoint"),
        "password": os.environ.get("CHECKPOINT_POSTGRES_PASSWORD", ""),
        "connect_timeout": 5,
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
    }


def _saver(pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    return AsyncPostgresSaver(
        conn=pool,
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("Agent.deep_agent_tools.models", "FinalAnalysisDecision")
            ]
        ),
    )


def _parent_builder() -> StateGraph:
    child = create_deep_agent(
        model=BindableFakeChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "FinalAnalysisDecision",
                            "args": _valid_decision_args(),
                            "id": "p0-postgres-structured",
                        }
                    ],
                )
            ]
        ),
        tools=[],
        subagents=[],
        middleware=[_filesystem()],
        state_schema=SpikeState,
        response_format=ToolStrategy(FinalAnalysisDecision),
        checkpointer=None,
    )
    builder = StateGraph(SpikeState, context_schema=RuntimeContext)
    builder.add_node("deep_agent", child)
    builder.add_edge(START, "deep_agent")
    builder.add_edge("deep_agent", END)
    return builder


async def run() -> dict[str, bool]:
    _register_profile()
    thread_id = f"p0-postgres-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    pool = AsyncConnectionPool(
        conninfo=None,
        kwargs=_connection_kwargs(),
        min_size=1,
        max_size=2,
        timeout=5,
        open=False,
    )
    await pool.open(wait=True)
    try:
        saver = _saver(pool)
        await saver.setup()
        builder = _parent_builder()
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke(
            {
                "messages": [HumanMessage(content="return the structured decision")],
                "marker": "postgres-state",
                "files": {"p0.txt": "postgres-file"},
            },
            config=config,
            context={"runtime_marker": "runtime-only"},
        )
        state = await graph.aget_state(config)
    finally:
        await pool.close()

    recovery_pool = AsyncConnectionPool(
        conninfo=None,
        kwargs=_connection_kwargs(),
        min_size=1,
        max_size=2,
        timeout=5,
        open=False,
    )
    await recovery_pool.open(wait=True)
    try:
        recovery_graph = builder.compile(checkpointer=_saver(recovery_pool))
        recovered = await recovery_graph.aget_state(config)
    finally:
        await recovery_pool.close()

    checks = {
        "postgres_checkpoint": state.values.get("marker") == "postgres-state"
        and state.values.get("files") == {"p0.txt": "postgres-file"}
        and isinstance(state.values.get("structured_response"), FinalAnalysisDecision),
        "recovered_state": recovered.values.get("marker") == "postgres-state"
        and recovered.values.get("files") == {"p0.txt": "postgres-file"}
        and isinstance(recovered.values.get("structured_response"), FinalAnalysisDecision),
        "runtime_context_excluded": "runtime_marker" not in recovered.values,
        "tool_message_recovered": isinstance(recovered.values["messages"][-1], ToolMessage)
        and recovered.values["messages"][-1].tool_call_id
        == "p0-postgres-structured",
    }
    if not all(checks.values()):
        raise RuntimeError("PostgreSQL checkpoint P0 assertion failed")
    return checks


def main() -> None:
    try:
        result = asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"status": "passed", **result}))


if __name__ == "__main__":
    main()
