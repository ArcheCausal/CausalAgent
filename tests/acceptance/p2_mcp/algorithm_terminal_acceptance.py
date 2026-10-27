"""真实 MCP 算法终态工程验收（人工入口，不由 pytest 自动执行）。

职责：用真实 HTTP、默认 ProcessPool runner、McpAlgorithmExecutor、AlgorithmTool
和 dependency middleware ToolNode 检查终态、调用身份、公共事件和内存 checkpoint。
范围：显式生成的测试 CSV 是本地冻结输入 authority；不证明数据库授权、MySQL
强读、lease fencing、PostgreSQL 恢复、生产 SSE 持久化或模型报告生成。

在两个新建容器中运行；仓库只读挂载 /app，服务端 --network none，客户端使用
--network container:<本次服务端容器>。服务端镜像 causalagent-causal-mcp，客户端
镜像 causalagent-unit-test。若当前 MCP 模块要求 LangGraph，服务端改用以测试镜像
为基础、从 MCP 镜像复制真实 cdfm 包的临时镜像。无需安装宿主依赖。示例容器内命令：
  python tests/acceptance/p2_mcp/algorithm_terminal_acceptance.py server \
    --fixture /tmp/algorithm-terminal-fixture.csv
  python tests/acceptance/p2_mcp/algorithm_terminal_acceptance.py client \
    --report /tmp/algorithm-terminal-report.json

CDFM 先在真实子进程完成离线预检。缺权重时，CDFM deadline 项明确 BLOCKED；
仍执行 PC 两次成功和真实 PC 外部取消。整体退出码：PASS=0、FAIL=1、BLOCKED=2。
只输出受控元数据，不输出 CSV、raw payload、异常自由文本或服务凭据。
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import csv
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import random
import secrets
import sys
import time
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Public, disposable test credentials; never read deployment credentials or .env.
TEST_TOKEN = "isolated-terminal-acceptance-only"
TEST_SIGNING_KEY = "isolated-terminal-acceptance-signing-only"
SOURCE_FILES = (
    "Agent/causal_agent/graph.py", "Agent/Report/document.py",
    "Agent/deep_agent/graph.py", "Agent/deep_agent/finalization.py",
    "Agent/deep_agent/state.py", "Agent/deep_agent_tools/preprocessing.py",
    "Agent/deep_agent_tools/algorithm_tools.py",
    "Agent/deep_agent_tools/adapters/base.py", "Agent/deep_agent_tools/models.py",
    "Agent/deep_agent_tools/dependency_dispatch.py",
    "Agent/deep_agent_tools/dependency_planner.py",
    "Agent/deep_agent_tools/runtime_updates.py", "app/agent/worker/event_adapter.py",
    "tests/acceptance/p2_mcp/algorithm_terminal_acceptance.py",
)


class AcceptanceFailure(RuntimeError):
    """Only controlled check identifiers are printed to the report."""


def require(condition: Any, check: str) -> None:
    if not condition:
        raise AcceptanceFailure(check)


def source_digest() -> str:
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        digest.update(name.encode())
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()


def generate_fixture(path: Path) -> dict[str, Any]:
    """Create only a new, explicitly named synthetic test CSV, never user input."""
    from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
    from tests.support.local_algorithm_execution import LocalAlgorithmExecutor

    rng = random.Random(20261020)
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("A", "B", "C", "D"))
        for _ in range(160):
            a = rng.gauss(0, 1)
            b = 0.8 * a + rng.gauss(0, 0.6)
            c = 0.6 * b + rng.gauss(0, 0.8)
            writer.writerow((a, b, c, rng.gauss(0, 1)))
    validation_report = LocalAlgorithmExecutor(path.read_text(encoding="utf-8")).validation_report
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": 160, "columns": ["A", "B", "C", "D"],
        "data_profile": build_data_profile_from_csv(path.read_text(encoding="utf-8")).model_dump(mode="json"),
        "authority": "explicit generated local frozen test CSV; no database authority",
        "validation_report": validation_report,
    }


def cdfm_preflight(path: str) -> dict[str, Any]:
    """No replacement runner: load weights and infer with the real CDFM runner."""
    from Agent.causal.cdfm_runner import run_cdfm_analysis

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        result = run_cdfm_analysis(Path(path).read_text(encoding="utf-8"), threshold=0.5)
    return {
        "status": "PASS" if result.get("success") is True else "BLOCKED",
        "error_type": result.get("error_type"),
        "model_error_type": (result.get("diagnostics") or {}).get("error_type"),
        "model_source": (result.get("diagnostics") or {}).get("model_source"),
        "inference_completed": result.get("success") is True,
        "offline": os.getenv("HF_HUB_OFFLINE") == "1",
    }


async def serve(args: argparse.Namespace) -> None:
    import psutil
    import uvicorn
    from starlette.responses import JSONResponse
    from Agent.CausalAgentMCP.app import create_app
    from Agent.CausalAgentMCP.config import McpServerConfig
    from Agent.CausalAgentMCP.executor_pool import BoundedProcessPool
    from Agent.measurement_semantics import ValidatedAlgorithmInput, semantic_kinds_from_validation_report

    # No environment/model/DB configuration is read from the workspace.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    fixture = generate_fixture(Path(args.fixture))
    with ProcessPoolExecutor(max_workers=1) as preflight_pool:
        try:
            preflight = await asyncio.wait_for(
                asyncio.wrap_future(preflight_pool.submit(cdfm_preflight, args.fixture)),
                timeout=90,
            )
        except Exception as exc:
            preflight = {"status": "BLOCKED", "exception_type": type(exc).__name__}

    frozen_path = Path(args.fixture)

    def read_frozen_authority(context: Any) -> ValidatedAlgorithmInput:
        content = frozen_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != fixture["sha256"] or digest != context.input_snapshot_digest:
            raise AcceptanceFailure("frozen_input_digest_changed")
        return ValidatedAlgorithmInput(
            csv_data=content.decode("utf-8"),
            semantic_kinds=semantic_kinds_from_validation_report(fixture["validation_report"]),
        )

    pool = BoundedProcessPool(process_workers=2, queue_capacity=2)
    config = McpServerConfig(
        service_token=TEST_TOKEN, signing_key_current=TEST_SIGNING_KEY,
        signing_key_previous=None, signing_key_id="current",
        signing_key_previous_id="previous", host="127.0.0.1", port=args.port,
        process_workers=2, queue_capacity=2, algorithm_deadline_seconds=600,
    )
    # Leave the real default database probe intact. /ready is deliberately unused.
    mcp_app = create_app(config, executor_pool=pool, authority_reader=read_frozen_authority)
    loaded_source = source_digest()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/acceptance/status":
            # Read-only instrumentation of real pool state, never an executor substitute.
            response = JSONResponse({
                "fixture": fixture, "cdfm_preflight": preflight,
                "source_digest": loaded_source,
                "source_unchanged": loaded_source == source_digest(),
                "active": {key: value.state for key, value in pool._invocations.items()},
                "terminal": {key: value[1] for key, value in pool._terminal.items()},
                "child_process_count": len(psutil.Process().children(recursive=True)),
            })
            await response(scope, receive, send)
            return
        await mcp_app(scope, receive, send)

    await uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=args.port, log_level="critical", access_log=False,
    )).serve()


async def client(args: argparse.Namespace) -> dict[str, Any]:
    # The real executor imports JobExecutionGuard -> job_service -> AppConfig.
    # Satisfy that import with disposable isolated configuration, never host secrets.
    # No model/DB endpoint is called, and this is not evidence of their readiness.
    from config.secret_file import REQUIRED_SECRET_KEYS

    secret_path = Path("/tmp") / ("terminal-acceptance-" + uuid4().hex + ".env")
    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        for key in REQUIRED_SECRET_KEYS:
            output.write(key + "=" + secrets.token_urlsafe(32) + "\n")
    os.environ["CAUSALAGENT_SECRET_FILE"] = str(secret_path)
    os.environ["BASE_URL"] = "http://127.0.0.1:1/v1"
    os.environ["MODEL"] = "unused-no-model-called"
    os.environ["MYSQL_HOST"] = "127.0.0.1"
    os.environ["MYSQL_PORT"] = "1"
    os.environ["MYSQL_USER"] = "unused-no-database-called"
    os.environ["MYSQL_DATABASE"] = "unused_acceptance"
    os.environ["CHECKPOINT_POSTGRES_HOST"] = "127.0.0.1"
    os.environ["CHECKPOINT_POSTGRES_PORT"] = "1"

    import httpx
    from langchain_core.messages import AIMessage, ToolMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.runtime import Runtime
    from langgraph.types import Command
    from Agent.Report.document import ReportDraft, build_report_document
    from Agent.causal_agent.nodes import _report_execution_summary
    from Agent.deep_agent.context import AnalysisInputRef, AgentRunContext, TrustedJobIdentity
    from Agent.measurement_semantics import semantic_kinds_from_validation_report
    from Agent.deep_agent.finalization import FinalizationGate
    from Agent.deep_agent.graph import _build_message_identity_middleware
    from Agent.deep_agent.memory import build_in_memory_backend
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.deep_agent_tools.adapters import build_default_adapters
    from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
    from Agent.deep_agent_tools.dependency_dispatch import build_algorithm_dependency_middleware
    from Agent.deep_agent_tools.dependency_planner import (
        ToolCallRequest, _classify_execution_result, build_dependency_plan,
    )
    from Agent.deep_agent_tools.mcp_algorithm_executor import McpAlgorithmExecutor
    from Agent.deep_agent_tools.models import AlgorithmResult, DataProfile, InvocationRecord
    from Agent.deep_agent_tools.registry import build_default_registry
    from app.agent.worker.event_adapter import LangGraphEventAdapter
    from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig

    base_url = f"http://127.0.0.1:{args.port}"
    http = httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False)

    async def status() -> dict[str, Any]:
        response = await http.get("/acceptance/status")
        response.raise_for_status()
        return response.json()

    initial = await status()
    require(initial["source_unchanged"], "server_source_changed_during_import")
    require(initial["source_digest"] == source_digest(), "client_server_source_mismatch")
    fixture = initial["fixture"]
    report: dict[str, Any] = {
        "status": "FAIL", "scope": "real MCP engineering acceptance only",
        "fixture": fixture, "source_digest": source_digest(),
        "cdfm_preflight": initial["cdfm_preflight"], "cases": {},
        "limitations": [
            "No MySQL strong-read, database authority or lease fencing evidence",
            "InMemorySaver only; no PostgreSQL recovery evidence",
            "Event adapter replay/key checks only; no persistent SSE/history/browser evidence",
            "Deterministic tool-call test inputs; no model or report generation evidence",
            "Deadline includes real process initialization and model loading",
        ],
    }
    pool = McpClientPool(McpClientPoolConfig(
        url=base_url + "/mcp", service_token=TEST_TOKEN,
        pool_size=2, max_in_flight_per_client=1, acquire_timeout_seconds=10,
        control_pool_size=1, control_max_in_flight_per_client=2,
        control_acquire_timeout_seconds=3, max_connections=8,
        max_keepalive_connections=8, read_timeout_seconds=650,
    ))
    await pool.start()
    executor = McpAlgorithmExecutor(pool, signing_key=TEST_SIGNING_KEY, max_retries=0)
    profile = DataProfile.model_validate(fixture["data_profile"])

    def make_run(
        calls: list[dict[str, Any]], deadline: float,
        trusted_identity: TrustedJobIdentity | None = None,
        *, data_profile: DataProfile | None = None,
    ) -> dict[str, Any]:
        analysis_input = AnalysisInputRef()
        analysis_input.bind(
            dataset_id=1, object_id=1, content_hash=fixture["sha256"], filename="local.csv",
            semantic_kinds=semantic_kinds_from_validation_report(fixture["validation_report"]),
        )
        identity = trusted_identity or TrustedJobIdentity(
            job_id=str(uuid4()), session_id=str(uuid4()), user_id=7,
            attempt_count=1, lease_epoch=1, worker_id="isolated-terminal-acceptance",
            input_identity=fixture["sha256"], input_snapshot_digest=fixture["sha256"],
            analysis_input=analysis_input,
        )
        events: list[dict[str, Any]] = []
        commands: list[Any] = []
        handlers: list[str] = []
        backend = build_in_memory_backend(user_id=7)
        context = AgentRunContext(
            execution_guard=None, trusted_identity=identity,
            algorithm_executor=executor, filesystem_backend=backend,
            event_sink=events.append,
        )
        registry = build_default_registry(build_default_adapters(
            executor=executor, raw_backend=backend,
        ))
        tools = [tool.to_langchain_tool() for tool in build_algorithm_tools(
            registry, runtime_context=context,
        )]
        middleware = build_algorithm_dependency_middleware(
            registry=registry, timeout_seconds=deadline,
        )
        identity_hook = _build_message_identity_middleware(registry=registry)
        saver = InMemorySaver()
        config = {"configurable": {"thread_id": str(uuid4())}}
        graph_ref: dict[str, Any] = {}

        async def register(state: Any, runtime: Runtime) -> Any:
            return identity_hook.after_model(state, runtime)

        async def running(state: Any, runtime: Runtime) -> Any:
            return await middleware.aafter_model(state, runtime)

        # These imports are deliberately local to the client image. Resolve annotations
        # explicitly because LangGraph inspects callable globals for Runtime injection.
        register.__annotations__["runtime"] = Runtime
        running.__annotations__["runtime"] = Runtime

        async def dispatch(request: Any, handler: Any) -> Any:
            # Observe the genuine middleware output and the already committed checkpoint.
            snapshot = await graph_ref["graph"].aget_state(config)
            ledger = snapshot.values.get("action_ledger", {})
            require(len(ledger) == len(calls), "registration_missing_before_external_call")
            require(all(InvocationRecord.model_validate(v).attempts[0].status == "running"
                        for v in ledger.values()), "running_not_committed_before_call")
            async def observed_handler(actual_request: Any) -> Any:
                handlers.append(str(actual_request.tool_call["id"]))
                return await handler(actual_request)

            output = await middleware.awrap_tool_call(request, observed_handler)
            commands.append(output)
            return output

        builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
        builder.add_node("register", register)
        builder.add_node("running", running)
        builder.add_node("tools", ToolNode(
            tools, handle_tool_errors=False, awrap_tool_call=dispatch,
        ))
        builder.add_edge(START, "register")
        builder.add_edge("register", "running")
        builder.add_edge("running", "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=saver)
        graph_ref["graph"] = graph
        return {
            "graph": graph, "config": config, "context": context, "registry": registry,
            "events": events, "commands": commands, "backend": backend, "handlers": handlers,
            "input": {"messages": [AIMessage(content="", tool_calls=calls)],
                      "data_profile": data_profile if data_profile is not None else profile,
                      "algorithm_results": {}, "action_ledger": {}},
        }

    async def execute(run: dict[str, Any]) -> dict[str, Any]:
        # Consume the real custom stream as well as the independent direct sink.
        custom = []
        async for mode, payload in run["graph"].astream(
            run["input"], run["config"], context=run["context"],
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                custom.append(payload)
        run["custom"] = custom
        return (await run["graph"].aget_state(run["config"])).values

    def check_events(run: dict[str, Any], state: dict[str, Any]) -> int:
        direct = [e for e in run["events"] if e.get("type") == "tool_call_result"]
        require(len(direct) == len(state["algorithm_results"]), "terminal_event_count")
        require(len({e["_event_key"] for e in direct}) == len(direct), "distinct_call_event_keys")
        aggregate = {"type": "updates", "ns": (), "data": {"deep_agent": {
            "deep_agent_algorithm_results": state["algorithm_results"],
            "deep_agent_action_ledger": state["action_ledger"],
        }}}

        def activated(attempt: int) -> Any:
            value = LangGraphEventAdapter(run["context"].trusted_identity.job_id, attempt)
            value.convert({"type": "tasks", "ns": (), "data": {
                "id": "acceptance-deep-task", "name": "deep_agent", "input": {},
            }})
            return value

        # Direct sink is a recorder, not a database writer. Check key identity, not DB dedup.
        adapter = activated(1)
        custom_results = []
        for event in run["custom"]:
            custom_results.extend(adapter.convert({"type": "custom", "data": event}))
        require(adapter.convert(aggregate) == [], "aggregate_republished_after_custom")
        custom_terminal = [e for e in custom_results if e.get("type") == "tool_call_result"]
        require({e["_event_key"] for e in custom_terminal} ==
                {e["_event_key"] for e in direct}, "custom_direct_key_mismatch")
        for event in run["custom"]:
            require(adapter.convert({"type": "custom", "data": event}) == [],
                    "custom_replay_added_event")
        fallback = activated(2).convert(aggregate)
        fallback = [e for e in fallback if e.get("type") == "tool_call_result"]
        require({e["_event_key"] for e in fallback} == {e["_event_key"] for e in direct},
                "restored_aggregate_keys_changed")
        for event in direct:
            matches = [e for e in fallback if e["_event_key"] == event["_event_key"]]
            require(len(matches) == 1 and matches[0]["status"] == event["status"] and
                    matches[0]["summary"] == event["summary"], "public_status_path_mismatch")
            require(matches[0] == event, "replay_payload_changed")
        return len(direct)

    async def check_checkpoints(run: dict[str, Any], terminal: bool) -> list[int]:
        history = [item async for item in run["graph"].aget_state_history(run["config"])]
        current = await run["graph"].aget_state(run["config"])
        revisions = []
        for invocation_id in current.values["action_ledger"]:
            sequence = []
            for item in reversed(history):
                raw = item.values.get("action_ledger", {}).get(invocation_id)
                if raw is not None:
                    sequence.append(InvocationRecord.model_validate(raw).attempts[0].revision)
            require(sequence == sorted(sequence), "checkpoint_revision_regressed")
            require({0, 1}.issubset(sequence), "queued_running_checkpoint_missing")
            if terminal:
                require(2 in sequence, "terminal_checkpoint_missing")
            revisions = sorted(set(revisions + sequence))
        return revisions

    def call(name: str, call_id: str) -> dict[str, Any]:
        return {"name": name, "id": call_id, "type": "tool_call", "args": {}}

    async def await_running(run: dict[str, Any], task: asyncio.Task[Any]) -> str:
        until = time.monotonic() + 20
        while time.monotonic() < until:
            if task.done():
                state = await task
                outcomes = sorted({
                    f"{result.status}:{result.diagnostics.safe_error_code}"
                    for raw in state.get("algorithm_results", {}).values()
                    for result in (AlgorithmResult.model_validate(raw),)
                })
                raise AcceptanceFailure("algorithm_finished_before_external_cancel:" + ",".join(outcomes))
            snapshot = await run["graph"].aget_state(run["config"])
            remote = await status()
            for invocation_id in snapshot.values.get("action_ledger", {}):
                if remote["active"].get(invocation_id) == "running" and remote["child_process_count"]:
                    return invocation_id
            await asyncio.sleep(0.02)  # Poll real state; never substitute algorithm work.
        raise AcceptanceFailure("real_process_did_not_start")

    async def run_case(name: str, operation: Any) -> None:
        print(json.dumps({"phase": name, "status": "RUNNING"}), flush=True)
        started = time.monotonic()
        try:
            report["cases"][name] = await operation()
        except Exception as exc:
            report["cases"][name] = {"status": "FAIL", "exception_type": type(exc).__name__}
            if isinstance(exc, AcceptanceFailure):
                report["cases"][name]["check"] = str(exc)
        report["cases"][name]["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps({"phase": name, **report["cases"][name]}, ensure_ascii=False), flush=True)

    async def pc_twice() -> dict[str, Any]:
        run = make_run([call("causal_pc", "pc-first"), call("causal_pc", "pc-second")], 90)
        state = await execute(run)
        results = [AlgorithmResult.model_validate(v) for v in state["algorithm_results"].values()]
        require(len(results) == 2 and all(r.status == "valid" for r in results), "pc_two_valid_results")
        require(sorted(run["handlers"]) == ["pc-first", "pc-second"], "pc_two_handler_calls")
        remote = await status()
        require(all(result.invocation_id in remote["terminal"] for result in results), "pc_two_remote_calls_missing")
        require(len({r.invocation_id for r in results}) == 2, "pc_invocation_identity_collapsed")
        require(all(isinstance(c, Command) for c in run["commands"]) and
                len(run["commands"]) == 2, "pc_command_missing")
        require(all(InvocationRecord.model_validate(v).final_status == "succeeded"
                    for v in state["action_ledger"].values()), "pc_ledger_status")
        require(all(e["summary"] == "调用完成" and e["status"] == "succeeded"
                    for e in run["events"] if e["type"] == "tool_call_result"), "pc_public_success")
        messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        require(len(messages) == 2 and all(m.status == "success" and
                json.loads(m.content)["status"] == "valid" for m in messages), "pc_tool_messages")
        return {"status": "PASS", "valid_results": 2, "real_mcp_calls": 2,
                "handler_call_ids": sorted(run["handlers"]), "terminal_events": check_events(run, state),
                "checkpoint_revisions": await check_checkpoints(run, True)}

    async def input_isolation(mode: str) -> dict[str, Any]:
        """同轮真实工具调用；远端进程池终态用于证明 MCP 请求确实被执行。"""
        selected_profile = profile
        expected_handlers = {"isolation-pc", "isolation-cdfm"}
        blocked_ids: set[str] = set()
        pc_arguments: dict[str, Any] = {}
        if mode in {"cdfm_missing", "unapproved_pc", "invalid_pc_parameter"}:
            # 与冻结 CSV 同一规模和列名，但未确认存储类型，模拟历史基础画像。
            selected_profile = DataProfile(
                row_count=profile.row_count, column_count=profile.column_count,
                column_names=profile.column_names,
            )
            expected_handlers = {"isolation-pc"}
            blocked_ids = {"isolation-cdfm"}
        elif mode == "both_missing":
            selected_profile = DataProfile(row_count=0, column_count=0)
            expected_handlers = set()
            blocked_ids = {"isolation-pc", "isolation-cdfm"}
        if mode == "unapproved_pc":
            pc_arguments = {"preprocessing": {"steps": [
                {"operation": "select_columns", "columns": list(profile.column_names[:2])},
            ]}}
        elif mode == "invalid_pc_parameter":
            pc_arguments = {"alpha": 2.0}
        calls = [call("causal_pc", "isolation-pc"), call("causal_cdfm", "isolation-cdfm")]
        calls[0]["args"] = pc_arguments
        before = await status()
        run = make_run(calls, 180, data_profile=selected_profile)
        state = await execute(run)
        results = {result.provider_call_id: result for raw in state["algorithm_results"].values()
                   for result in (AlgorithmResult.model_validate(raw),)}
        ids = {item["id"] for item in calls}
        require(set(results) == ids and len(state["algorithm_results"]) == 2, "isolation_result_ids")
        require(set(run["handlers"]) == expected_handlers and len(run["handlers"]) == len(expected_handlers),
                "isolation_handler_ids")
        records = {record.provider_call_id: record for raw in state["action_ledger"].values()
                   for record in (InvocationRecord.model_validate(raw),)}
        require(set(records) == ids and len({r.invocation_id for r in results.values()}) == 2,
                "isolation_ledger_ids")
        messages = [message for message in state["messages"] if isinstance(message, ToolMessage)]
        require(len(messages) == 2 and {message.tool_call_id for message in messages} == ids,
                "isolation_toolmessage_ids")
        require(len(run["commands"]) == 2, "isolation_command_count")
        command_ids = []
        for command in run["commands"]:
            require(isinstance(command, Command), "isolation_command_type")
            values = command.update.get("algorithm_results", {})
            require(len(values) == 1, "isolation_command_result_count")
            command_ids.append(AlgorithmResult.model_validate(next(iter(values.values()))).provider_call_id)
        require(set(command_ids) == ids, "isolation_command_call_ids")
        remote = await status()
        new_remote_ids = set(remote["terminal"]) - set(before["terminal"])
        expected_remote_ids = {results[call_id].invocation_id for call_id in expected_handlers}
        if mode in {"unapproved_pc", "invalid_pc_parameter"}:
            expected_remote_ids = set()
        require(new_remote_ids == expected_remote_ids, "isolation_remote_invocations")
        for call_id, result in results.items():
            record = records[call_id]
            require(record.invocation_id == result.invocation_id and record.result_ref == result.result_ref,
                    "isolation_result_ledger_identity")
            public_status = {"valid": "succeeded", "invalid_input": "failed", "execution_failed": "failed"}.get(
                result.status, result.status,
            )
            require(record.final_status == record.attempts[0].status == public_status, "isolation_ledger_status")
            message = next(item for item in messages if item.tool_call_id == call_id)
            message_payload = json.loads(message.content)
            if message_payload.get("status") != "needs_confirmation":
                require(message_payload.get("result_ref") == result.result_ref and
                        message_payload.get("provider_call_id") == call_id and
                        message_payload.get("status") == result.status, "isolation_message_result_identity")
            if call_id in blocked_ids:
                require(result.status == record.final_status == "not_ready", "isolation_blocked_status")
                require(result.standardized_graph is None and result.raw_result_ref is None,
                        "isolation_blocked_artifacts")
                expected_summary = (
                    "当前画像未确认全部列为数值类型，未执行算法。" if call_id == "isolation-cdfm"
                    else "当前画像未确认所需表格输入，未执行算法。"
                )
                require(result.diagnostics.summary == expected_summary, "isolation_safe_unexecuted_summary")
        if mode == "unapproved_pc":
            pc_message = next(message for message in messages if message.tool_call_id == "isolation-pc")
            require(json.loads(pc_message.content)["status"] == "needs_confirmation", "isolation_approval_bypassed")
            require(results["isolation-pc"].status == "not_ready", "isolation_approval_terminal")
        elif mode == "invalid_pc_parameter":
            require(results["isolation-pc"].status == "execution_failed", "isolation_parameter_check_bypassed")
        if mode in {"unapproved_pc", "invalid_pc_parameter"}:
            pc_result = results["isolation-pc"]
            require(pc_result.standardized_graph is None and pc_result.raw_result_ref is None,
                    "isolation_rejected_pc_artifacts")
        return {
            "status": "PASS", "handler_call_ids": sorted(run["handlers"]),
            "real_mcp_calls": len(new_remote_ids), "dependency_blocked_call_ids": sorted(blocked_ids),
            "unexecuted_call_ids": sorted(call_id for call_id, result in results.items()
                                          if result.invocation_id not in new_remote_ids),
            "actual_results": {key: value.status for key, value in results.items()},
            "safe_error_codes": {key: value.diagnostics.safe_error_code for key, value in results.items()},
            "terminal_events": check_events(run, state),
            "checkpoint_revisions": await check_checkpoints(run, True),
        }

    async def deadline(algorithm: str = "causal_cdfm") -> dict[str, Any]:
        deadline_seconds = args.deadline if algorithm == "causal_cdfm" else min(args.deadline, 0.5)
        run = make_run([call(algorithm, "controlled-deadline")], deadline_seconds)
        task = asyncio.create_task(execute(run))
        try:
            invocation_id = await await_running(run, task)
            state = await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        require(len(run["commands"]) == 1 and isinstance(run["commands"][0], Command),
                "timeout_command_missing")
        result = AlgorithmResult.model_validate(next(iter(state["algorithm_results"].values())))
        require(result.status == "timed_out", "algorithm_not_timed_out")
        require(result.diagnostics.safe_error_code == "ALGORITHM_TIMED_OUT", "timeout_error_code")
        require(result.standardized_graph is None and result.raw_result_ref is None,
                "timeout_contains_graph_or_raw")
        require(not run["backend"].default.files, "timeout_wrote_raw_artifact")
        ledger = InvocationRecord.model_validate(state["action_ledger"][invocation_id])
        require(ledger.final_status == ledger.attempts[0].status == "timed_out", "timeout_ledger")
        message = next(m for m in state["messages"] if isinstance(m, ToolMessage))
        require(message.status == "error" and json.loads(message.content)["status"] == "timed_out",
                "timeout_tool_message")
        require(all(e["status"] == "timed_out" and e["summary"] == "调用超时"
                    for e in run["events"] if e["type"] == "tool_call_result"), "timeout_public_event")
        plan = build_dependency_plan((ToolCallRequest("controlled-deadline", algorithm),),
                                    registry=run["registry"],
                                    initial_artifacts=frozenset({"continuous_tabular_dataset", "tabular_dataset"}))
        outcome, _, artifacts = _classify_execution_result(plan.calls[0], run["commands"][0])
        require(outcome == "timed_out" and not artifacts, "timeout_published_dependency_artifact")
        remote = await status()
        require(invocation_id not in remote["active"], "timeout_remote_still_active")
        return {"status": "PASS", "algorithm": algorithm,
                "result": result.status, "ledger": ledger.final_status,
                "tool_message": "timed_out", "terminal_events": check_events(run, state),
                "published_artifacts": sorted(artifacts),
                "checkpoint_revisions": await check_checkpoints(run, True),
                "deadline_seconds": deadline_seconds,
                "remote_terminal": remote["terminal"].get(invocation_id)}

    async def external_cancel() -> dict[str, Any]:
        algorithm = "causal_cdfm" if initial["cdfm_preflight"]["status"] == "PASS" else "causal_pc"
        run = make_run([call(algorithm, "external-cancel")], 90)
        task = asyncio.create_task(execute(run))
        try:
            invocation_id = await await_running(run, task)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AcceptanceFailure("external_cancel_was_swallowed")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        state = (await run["graph"].aget_state(run["config"])).values
        require(not state.get("algorithm_results") and not run["commands"], "cancel_wrote_result_command")
        require(not [e for e in run["events"] if e["type"] == "tool_call_result"],
                "cancel_became_ordinary_terminal")
        require(InvocationRecord.model_validate(state["action_ledger"][invocation_id]).final_status == "pending",
                "cancel_lost_pending_registration")
        remote = await status()
        require(invocation_id not in remote["active"], "cancel_remote_still_active")
        require(remote["terminal"].get(invocation_id) == "canceled", "remote_cancel_unconfirmed")
        return {"status": "PASS", "algorithm": algorithm, "control_flow": "CancelledError",
                "remote_terminal": "canceled", "result_commands": 0,
                "checkpoint_revisions": await check_checkpoints(run, False),
                "pending_registration_retained": True}

    async def mixed_finalization() -> dict[str, Any]:
        # Both inputs are genuine MCP executions under one trusted Job identity.
        # Gate decisions and empty report drafts are explicit inputs, not LLM output.
        pc_run = make_run([call("causal_pc", "mixed-pc")], 90)
        pc_state = await execute(pc_run)
        timeout_run = make_run([call("causal_cdfm", "mixed-cdfm")], args.deadline,
                               pc_run["context"].trusted_identity)
        task = asyncio.create_task(execute(timeout_run))
        try:
            await await_running(timeout_run, task)
            timeout_state = await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        pc = AlgorithmResult.model_validate(next(iter(pc_state["algorithm_results"].values())))
        timeout = AlgorithmResult.model_validate(next(iter(timeout_state["algorithm_results"].values())))
        require(pc.status == "valid" and timeout.status == "timed_out", "mixed_real_outcomes")
        results = {**pc_state["algorithm_results"], **timeout_state["algorithm_results"]}
        ledger = {**pc_state["action_ledger"], **timeout_state["action_ledger"]}
        decision = FinalizationGate(registry=pc_run["registry"]).validate(
            decision={
                "outcome": "algorithm_supported", "primary_result_ref": pc.result_ref,
                "result_assessments": [
                    {"result_ref": pc.result_ref, "disposition": "primary", "rationale": "采用有效 PC 结果。"},
                    {"result_ref": timeout.result_ref, "disposition": "discarded", "rationale": "CDFM 超时且无可用图。"},
                ],
                "conflict_status": "none", "selection_rationale": "仅采用已完成的有效结果。",
                "confidence": "medium",
            },
            algorithm_results=results, action_ledger=ledger,
            trusted_identity=pc_run["context"].trusted_identity,
        )
        summary = _report_execution_summary({
            "deep_agent_algorithm_results": results, "deep_agent_decision": decision,
            "finalization_status": "valid",
        })
        # An empty explicit draft tests program assembly only, without an LLM call.
        document = build_report_document(ReportDraft(title="终态装配验收"), execution_summary=summary)
        require(decision.primary_result_ref == pc.result_ref, "mixed_gate_primary_changed")
        require(any("CDFM" in getattr(block, "content", "") and
                    "未完成" in block.content and "未提供可用因果图" in block.content
                    for block in document.blocks), "mixed_report_missing_timeout_limit")
        return {"status": "PASS", "real_results": [pc.status, timeout.status],
                "gate": "valid", "primary": "PC", "program_report_limit": "CDFM timed_out",
                "model_report_generation": "not_run",
                "terminal_events": check_events(pc_run, pc_state) + check_events(timeout_run, timeout_state)}

    try:
        await run_case("pc_two_legal_calls", pc_twice)
        for mode in ("both_ready", "cdfm_missing", "both_missing", "unapproved_pc", "invalid_pc_parameter"):
            async def isolation_case(selected: str = mode) -> dict[str, Any]:
                return await input_isolation(selected)
            await run_case("dispatch_" + mode, isolation_case)
        if initial["cdfm_preflight"]["status"] == "PASS":
            await run_case("cdfm_controlled_deadline", deadline)
            await run_case("real_pc_and_cdfm_timeout_finalization", mixed_finalization)
        else:
            report["cases"]["cdfm_controlled_deadline"] = {
                "status": "BLOCKED", "reason": "real_offline_CDFM_preflight_failed; no replacement runner",
            }
            async def pc_deadline() -> dict[str, Any]:
                return await deadline("causal_pc")
            await run_case("pc_controlled_deadline_engineering", pc_deadline)
        await run_case("external_cancel", external_cancel)
        final = await status()
        require(final["source_unchanged"] and source_digest() == report["source_digest"],
                "source_changed_during_acceptance_rerun_required")
        require(not final["active"] and final["child_process_count"] == 0, "remote_process_leak")
        report["remote_active_after_cases"] = len(final["active"])
        report["remote_children_after_cases"] = final["child_process_count"]
        statuses = {case["status"] for case in report["cases"].values()}
        report["status"] = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    finally:
        await pool.close()
        await http.aclose()
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("server", "client"))
    parser.add_argument("--port", type=int, default=8796)
    parser.add_argument("--fixture", default="/tmp/algorithm-terminal-fixture.csv")
    parser.add_argument("--deadline", type=float, default=2.0)
    parser.add_argument("--report", default="/tmp/algorithm-terminal-report.json")
    args = parser.parse_args()
    require(args.deadline > 0, "deadline_must_be_positive")
    logging.disable(logging.CRITICAL)
    if args.mode == "server":
        await serve(args)
        return
    try:
        report = await client(args)
    except Exception as exc:
        report = {"status": "FAIL", "exception_type": type(exc).__name__}
        frames = []
        frame = exc.__traceback__
        while frame is not None:
            frames.append({"file": Path(frame.tb_frame.f_code.co_filename).name,
                           "line": frame.tb_lineno})
            frame = frame.tb_next
        report["error_location"] = frames[-5:]
        if isinstance(exc, AcceptanceFailure):
            report["check"] = str(exc)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit({"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]])


if __name__ == "__main__":
    asyncio.run(main())
