"""算法终态的真实持久化验收；人工入口，不由 pytest 自动执行。

职责：在本次新建的隔离 MySQL/PostgreSQL 中执行正式 bootstrap、Job 租约、
LangGraph checkpoint 恢复、真实本地 PC、事件幂等及真实 Flask SSE 路由。
范围：不访问部署数据库，不替换认证或数据库函数，不运行模型或浏览器。
仅适用于空的临时数据库；凭据在容器 /tmp 内生成，报告不包含凭据或用户正文。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def configure_isolated_databases() -> None:
    import mysql.connector
    from config.secret_file import REQUIRED_SECRET_KEYS

    mysql_host = os.environ["TERMINAL_ACCEPTANCE_MYSQL_HOST"]
    postgres_host = os.environ["TERMINAL_ACCEPTANCE_POSTGRES_HOST"]
    password = secrets.token_urlsafe(32)
    until = time.monotonic() + 60
    while True:
        try:
            connection = mysql.connector.connect(host=mysql_host, user="root", password="")
            break
        except mysql.connector.Error:
            if time.monotonic() >= until:
                raise RuntimeError("isolated_mysql_not_ready") from None
            time.sleep(1)
    # This database is explicitly provisioned empty for this one acceptance run.
    cursor = connection.cursor()
    cursor.execute("SHOW DATABASES LIKE 'terminal_acceptance'")
    assert cursor.fetchone(), "isolated_database_missing"
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='terminal_acceptance'")
    assert cursor.fetchone()[0] == 0, "refuse_nonempty_database"
    cursor.execute("ALTER USER CURRENT_USER() IDENTIFIED BY %s", (password,))
    connection.close()
    secret_path = Path("/tmp/terminal-persistence-secrets.env")
    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        for key in REQUIRED_SECRET_KEYS:
            value = password if key.startswith("MYSQL_") else secrets.token_urlsafe(32)
            output.write(f"{key}={value}\n")
    os.environ.update({
        "CAUSALAGENT_SECRET_FILE": str(secret_path),
        "MYSQL_HOST": mysql_host, "MYSQL_USER": "root", "MYSQL_DATABASE": "terminal_acceptance",
        "MYSQL_PORT": "3306", "MYSQL_READ_HOSTS": "",
        "CHECKPOINT_POSTGRES_HOST": postgres_host,
        "CHECKPOINT_POSTGRES_USER": "terminal_acceptance",
        "CHECKPOINT_POSTGRES_DATABASE": "terminal_acceptance",
        "BASE_URL": "http://127.0.0.1:1/v1", "MODEL": "unused-no-model-called",
        "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
    })


async def verify(client, user_id: int, session_id: str) -> dict:
    from langchain.tools import ToolRuntime, tool
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.runtime import Runtime
    from Agent.causal.causalachieve import run_pc_analysis
    from Agent.causal_agent.postgres_checkpointer import build_checkpointer, open_checkpoint_pool
    from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
    from Agent.deep_agent.graph import _build_message_identity_middleware
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.deep_agent_tools.adapters import build_default_adapters
    from Agent.deep_agent_tools.adapters.normalization import result_from_runner_payload
    from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
    from Agent.deep_agent_tools.dependency_dispatch import build_algorithm_dependency_middleware
    from Agent.deep_agent_tools.identity import build_invocation_id
    from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, DataProfile, InvocationRecord
    from Agent.deep_agent_tools.registry import build_default_registry
    from Agent.deep_agent_tools.runtime_updates import (
        algorithm_terminal_command, emit_tool_lifecycle_event, resolve_runtime_invocation,
    )
    from app.agent import job_service
    from app.agent.worker.event_adapter import LangGraphEventAdapter
    from app.agent.worker.event_writer import OrderedEventWriter
    from app.agent.worker.execution_guard import JobExecutionGuard, JobExecutionRevoked

    job, replayed = job_service.create_job(
        user_id, session_id, "isolated terminal persistence acceptance", str(uuid4()),
        request_id=str(uuid4()),
    )
    assert not replayed and job_service.get_initial_input_value(job["job_id"])
    job = job_service.claim_next_job("isolated-terminal-worker")
    assert job and job["status"] == "running"
    trusted = TrustedJobIdentity(
        job_id=job["job_id"], session_id=session_id, user_id=user_id,
        attempt_count=job["attempt_count"], lease_epoch=job["lease_epoch"],
        worker_id=job["worker_id"], input_identity="isolated-local-pc-data",
    )
    guard = JobExecutionGuard(job["job_id"], job["worker_id"], job["attempt_count"], job["lease_epoch"])
    writer = OrderedEventWriter(job, job["worker_id"], guard)
    context = AgentRunContext(trusted_identity=trusted, execution_guard=guard, event_sink=writer.submit)
    registry = build_default_registry(build_default_adapters(executor=None))
    identity_hook = _build_message_identity_middleware(registry=registry)
    dispatch = build_algorithm_dependency_middleware(registry=registry, timeout_seconds=90)
    calls = []
    captured_events = []
    import random
    rng = random.Random(20261020)
    csv = "x,y,z\n" + "\n".join(",".join(str(rng.gauss(0, 1)) for _ in range(3)) for _ in range(160))

    async def pc(runtime):
        """真实本地 PC runner；通过正式结果转换与 Command 写回。"""
        calls.append(runtime.tool_call_id)
        identity = resolve_runtime_invocation(runtime)
        started_at = datetime.now(timezone.utc)
        await emit_tool_lifecycle_event(runtime, event_type="tool_call_start", identity=identity, tool_name="causal_pc")
        payload = run_pc_analysis(csv, alpha=0.05, test="fisherz")
        assert payload["success"] is True
        invocation_id = build_invocation_id(job_id=trusted.job_id,
            response_identity=identity.response_identity, provider_call_id=identity.provider_call_id)
        command = AlgorithmExecutionCommand(
            invocation_id=invocation_id, capability_id=PC_SPEC.capability_id,
            capability_version=PC_SPEC.version, spec_digest=PC_SPEC.spec_digest,
            provider_call_id=identity.provider_call_id, input_identity=trusted.input_identity,
        )
        result = result_from_runner_payload(payload, command=command,
            context=trusted.to_mcp_context(invocation_id=invocation_id), capability_version=PC_SPEC.version)
        return await algorithm_terminal_command(runtime, identity=identity,
            tool_name="causal_pc", result=result, started_at=started_at)

    # Resolve the injected annotation before LangChain builds its tool schema.
    pc.__annotations__["runtime"] = ToolRuntime[AgentRunContext]
    pc = tool("causal_pc")(pc)

    def register(state, runtime):
        return identity_hook.after_model(state, runtime)

    async def running(state, runtime):
        return await dispatch.aafter_model(state, runtime)

    register.__annotations__["runtime"] = Runtime
    running.__annotations__["runtime"] = Runtime
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("register", register)
    builder.add_node("running", running)
    builder.add_node("tools", ToolNode([pc], handle_tool_errors=False, awrap_tool_call=dispatch.awrap_tool_call))
    builder.add_edge(START, "register")
    builder.add_edge("register", "running")
    builder.add_edge("running", "tools")
    builder.add_edge("tools", END)
    config = {"configurable": {"thread_id": job["job_id"]}}
    initial = {"messages": [AIMessage(content="", tool_calls=[
        {"id": "pc-one", "name": "causal_pc", "args": {}},
        {"id": "pc-two", "name": "causal_pc", "args": {}},
    ])], "data_profile": DataProfile(row_count=160, column_count=3,
        column_names=("x", "y", "z"), numeric_columns=("x", "y", "z")),
        "algorithm_results": {}, "action_ledger": {}}
    try:
        # Closing the pool and creating a new saver demonstrates real DB recovery.
        for stage, expected_revision in (("queued", 0), ("running", 1), ("terminal", 2)):
            async with open_checkpoint_pool() as pool:
                graph = builder.compile(checkpointer=build_checkpointer(pool),
                    interrupt_after=["register", "running"])
                async for mode, payload in graph.astream(initial if stage == "queued" else None,
                    config, context=context, stream_mode=["custom", "updates"], durability="sync"):
                    if mode == "custom":
                        captured_events.append(payload)
                snapshot = await graph.aget_state(config)
                records = {k: InvocationRecord.model_validate(v) for k, v in snapshot.values["action_ledger"].items()}
                assert len(records) == 2
                assert all(r.attempts[0].revision == expected_revision for r in records.values())
                if stage != "terminal":
                    assert not calls and all(r.final_status == "pending" for r in records.values())
                else:
                    state = snapshot.values
                    assert all(r.final_status == "succeeded" for r in records.values())
                    assert all(r.status == "valid" for r in state["algorithm_results"].values())
                    # Replay older revisions through the actual persisted State reducer.
                    await graph.aupdate_state(config, {"action_ledger": queued_records})
                    restored = await graph.aget_state(config)
                    assert restored.values["action_ledger"] == snapshot.values["action_ledger"]
                if stage == "queued":
                    queued_records = snapshot.values["action_ledger"]
        assert sorted(calls) == ["pc-one", "pc-two"]
        adapter = LangGraphEventAdapter(job["job_id"], job["attempt_count"])
        adapter.convert({"type": "tasks", "ns": (), "data": {"id": "deep", "name": "deep_agent", "input": {}}})
        for event in captured_events:
            for public in adapter.convert({"type": "custom", "data": event}):
                await writer.submit(public)
        fallback = LangGraphEventAdapter(job["job_id"], job["attempt_count"] + 1)
        fallback.convert({"type": "tasks", "ns": (), "data": {"id": "deep", "name": "deep_agent", "input": {}}})
        for public in fallback.convert({"type": "updates", "ns": (), "data": {"deep_agent": {
            "deep_agent_algorithm_results": state["algorithm_results"],
            "deep_agent_action_ledger": state["action_ledger"],
        }}}):
            await writer.submit(public)
        await writer.close()
        events = job_service.read_events_after(job["job_id"])
        terminals = [e for e in events if e["event_type"] == "tool_call_result"]
        assert len(terminals) == 2 and len({e["event_key"] for e in terminals}) == 2
        assert all(e["payload_json"]["summary"] == "调用完成" for e in terminals)
        # Revoke through the real Job service; guards and SQL fencing must reject late writes.
        canceled, _ = job_service.cancel_job(user_id, job["job_id"], str(uuid4()))
        assert canceled["status"] == "canceled"
        try:
            await guard.ensure_active()
        except JobExecutionRevoked:
            pass
        else:
            raise AssertionError("revoked_guard_allowed_progress")
        assert job_service.write_event(job["job_id"], job["worker_id"], job["attempt_count"],
            "tool_call_result", {"type": "tool_call_result"}, lease_epoch=job["lease_epoch"],
            event_key="late-result-must-be-fenced") is None
        all_events = job_service.read_events_after(job["job_id"])
        response = client.get(f"/api/agent/jobs/{job['job_id']}/events")
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert text.count("event: tool_call_result\n") == 2
        first_id = terminals[0]["id"]
        reconnect = client.get(f"/api/agent/jobs/{job['job_id']}/events", headers={"Last-Event-ID": str(first_id)})
        assert reconnect.status_code == 200
        assert reconnect.get_data(as_text=True).count("event: tool_call_result\n") == 1
        refresh = client.get(f"/api/agent/jobs/{job['job_id']}/events")
        assert refresh.get_data(as_text=True) == text
        assert all(field not in text for field in ("_event_key", "attempt", "invocation_id", "lease_epoch"))
        assert len(job_service.read_events_after(job["job_id"])) == len(all_events)
        return {"status": "PASS", "postgres_revisions": [0, 1, 2],
            "new_pool_recovery": True, "revision_replay_monotonic": True,
            "real_pc_executions": len(calls), "persisted_terminal_events": len(terminals),
            "custom_and_fallback_replay_deduplicated": True, "sse_last_event_id": True,
            "sse_refresh_stable": True, "public_payload_sanitized": True,
            "user_cancel_and_late_write_fenced": True,
            "limitations": ["local PC, not MCP/CDFM", "Flask routes in process, no browser",
                "same-attempt checkpoint recovery, not a worker crash or lease takeover", "no model/report run"]}
    finally:
        if not writer.task.done():
            await writer.abort()


def main() -> None:
    logging.disable(logging.CRITICAL)
    configure_isolated_databases()
    from Database.bootstrap import run_bootstrap
    from flask import Flask
    from config.settings import settings
    from app.auth.routes import auth_bp
    from app.chat.routes import chat_bp
    from app.agent.routes import agent_bp
    from app.auth.service import find_user
    run_bootstrap()
    app = Flask(__name__)
    app.secret_key = settings.SECRET_KEY
    for blueprint in (auth_bp, chat_bp, agent_bp):
        app.register_blueprint(blueprint)
    client = app.test_client()
    username = "terminal_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(24) + "9"
    assert client.post("/api/register", json={"username": username, "password": password}).status_code == 200
    assert client.post("/api/login", json={"username": username, "password": password}).status_code == 200
    response = client.post("/api/new_chat")
    assert response.status_code == 200
    report = asyncio.run(verify(client, find_user(username)["id"], response.json["new_session_id"]))
    Path("/tmp/terminal-persistence-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
