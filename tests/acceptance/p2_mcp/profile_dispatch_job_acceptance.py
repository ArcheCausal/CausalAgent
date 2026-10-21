"""真实模型、正式 worker/MCP 与隔离数据库的人工 Job 验收入口。

从宿主机执行 ``python <本文件> launch --cdfm-weights-volume <固定权重卷>``。
默认保留全部新容器与证据；run/verify 退出码为 PASS=0、FAIL=1、BLOCKED=2。
只读取现有 worker 配置/模型密钥及应用前端产物，不连接原业务数据库。
不自动批准数据处理，不修改 checkpoint，不预设模型或算法结果。
报告仅包含计数、状态和一致性结论；凭据和文件正文只留在容器内。
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
ENTRY = "tests/acceptance/p2_mcp/profile_dispatch_job_acceptance.py"
SHARED = Path("/acceptance")
SOURCE_FILES = ("Agent/deep_agent/state.py", "Agent/deep_agent_tools/dependency_planner.py",
                "Agent/deep_agent_tools/dependency_dispatch.py")


def source_fingerprint() -> dict:
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    return {"files": hashes, "digest": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def docker(*args: str, payload: str | None = None) -> str:
    result = subprocess.run(["docker", *args], input=payload, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("docker_operation_failed_" + args[0])
    return result.stdout.strip()


def launch(args) -> None:
    prefix = "profile-job-" + uuid4().hex[:10]
    evidence = ROOT / "tmp" / prefix
    evidence.mkdir(parents=True)
    source = json.loads(docker("inspect", args.source_worker))[0]
    source_env = dict(item.split("=", 1) for item in source["Config"]["Env"])
    # Copy non-secret deployment settings without rendering values to stdout.
    secret_names = {"SECRET_KEY", "API_KEY", "CAUSAL_MCP_SIGNING_KEY_CURRENT",
                    "CAUSAL_MCP_SIGNING_KEY_PREVIOUS"}
    inherited = {key: value for key, value in source_env.items()
                 if key not in secret_names
                 and not key.endswith(("_PASSWORD", "_API_KEY", "_TOKEN", "_SECRET"))
                 and not key.startswith(("MYSQL_", "CHECKPOINT_POSTGRES_"))
                 and key not in {"HOSTNAME", "HOME", "PATH", "PYTHONPATH"}}
    inherited.update({
        "MYSQL_HOST": prefix + "-mysql", "MYSQL_WRITE_HOST": prefix + "-mysql",
        "MYSQL_USER": "root", "MYSQL_WRITE_USER": "root", "MYSQL_READ_USER": "root",
        "MYSQL_DATABASE": "terminal_acceptance", "MYSQL_PORT": "3306", "MYSQL_READ_HOSTS": "",
        "CHECKPOINT_POSTGRES_HOST": prefix + "-postgres", "CHECKPOINT_POSTGRES_PORT": "5432",
        "CHECKPOINT_POSTGRES_USER": "terminal_acceptance",
        "CHECKPOINT_POSTGRES_DATABASE": "terminal_acceptance",
        "CAUSALAGENT_SECRET_FILE": "/acceptance/secrets.env",
        "CAUSAL_MCP_URL": "http://" + prefix + "-mcp:8080/mcp",
        "CHAT_FRONTEND_DIST_DIR": "/frontend", "WEBSITE_FRONTEND_DIST_DIR": "/website",
        "JOB_WORKER_SLOTS": "1",
        "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "ACCEPTANCE_PREFIX": prefix,
    })
    docker("network", "create", "--internal", prefix + "-db")
    docker("network", "create", prefix + "-egress")
    docker("volume", "create", prefix + "-evidence")
    docker("run", "-d", "--name", prefix + "-mysql", "--network", prefix + "-db",
           "-e", "MYSQL_ALLOW_EMPTY_PASSWORD=yes", "-e", "MYSQL_DATABASE=terminal_acceptance", "mysql:8.0")
    docker("run", "-d", "--name", prefix + "-postgres", "--network", prefix + "-db",
           "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", "POSTGRES_USER=terminal_acceptance",
           "-e", "POSTGRES_DB=terminal_acceptance", "postgres:16-alpine")
    docker("cp", args.source_app + ":/app/chat-frontend/dist", str(evidence / "frontend"))
    docker("cp", args.source_app + ":/app/website-frontend/dist", str(evidence / "website"))
    secret_mount = next(m for m in source["Mounts"] if m["Destination"] == "/run/secrets")
    secret_source = secret_mount.get("Name") or secret_mount["Source"]

    def container(role: str, *, initialize: bool = False) -> str:
        name = prefix + "-" + role
        primary_network = prefix + ("-egress" if role == "web" else "-db")
        command = ["create", "--name", name, "--network", primary_network,
                   "-w", "/app", "-v", str(ROOT) + ":/app:ro",
                   "-v", prefix + "-evidence:/acceptance",
                   "-v", str(Path(args.dataset).resolve()) + ":/dataset/input.csv:ro"]
        if initialize:
            command += ["-v", secret_source + ":/source-secrets:ro"]
        if role == "mcp" and args.cdfm_weights_volume:
            command += ["-v", args.cdfm_weights_volume + ":/weights:ro",
                        "-e", "CDFM_MODEL_PATH=/weights", "-e", "HF_HUB_OFFLINE=1",
                        "-e", "TRANSFORMERS_OFFLINE=1"]
        if role == "web":
            command += ["-p", f"127.0.0.1:{args.port}:5001",
                        "-v", str(evidence / "frontend") + ":/frontend:ro",
                        "-v", str(evidence / "website") + ":/website:ro"]
        # Environment values here are non-secret; model keys stay in the volume.
        for key, value in inherited.items():
            command += ["-e", key + "=" + value]
        command += ["--entrypoint", "python", args.image, ENTRY, role]
        docker(*command)
        if role == "web":
            docker("network", "connect", prefix + "-db", name)
        elif role in {"worker", "run"}:
            docker("network", "connect", prefix + "-egress", name)
        docker("start", name)
        return name

    bootstrap = container("bootstrap", initialize=True)
    code = docker("wait", bootstrap)
    if code != "0":
        print(docker("logs", bootstrap), flush=True)
        raise RuntimeError("isolated_bootstrap_failed")
    for role in ("mcp", "web", "worker"):
        container(role)
    runner = container("run")
    manifest = {"prefix": prefix, "runner": runner, "web_url": f"http://127.0.0.1:{args.port}",
                "evidence_directory": str(evidence), "retained": True}
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest), flush=True)
    print("Isolated acceptance started; inspect runner logs and retained report.json.", flush=True)


def isolated_bootstrap() -> None:
    import mysql.connector
    import psycopg
    from config.secret_file import REQUIRED_SECRET_KEYS, load_secrets

    # Read model credentials from the existing volume; never alter that volume.
    original = load_secrets(path=Path("/source-secrets/causalagent.env"))
    until = time.monotonic() + 90
    while True:
        try:
            connection = mysql.connector.connect(host=os.environ["MYSQL_HOST"], user="root", password="")
            break
        except mysql.connector.Error:
            if time.monotonic() > until:
                raise RuntimeError("new_mysql_not_ready") from None
            time.sleep(1)
    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='terminal_acceptance'")
    assert cursor.fetchone()[0] == 0, "refuse_nonempty_database"
    password = secrets.token_urlsafe(32)
    cursor.execute("ALTER USER CURRENT_USER() IDENTIFIED BY %s", (password,))
    cursor.close()
    connection.close()
    values = {key: password if key.startswith("MYSQL_") else secrets.token_urlsafe(32)
              for key in REQUIRED_SECRET_KEYS}
    for key in ("API_KEY", "DEEP_AGENT_API_KEY"):
        values[key] = original[key]
    descriptor = os.open(SHARED / "secrets.env", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
    load_secrets(reload=True)
    until = time.monotonic() + 60
    while True:
        try:
            with psycopg.connect(host=os.environ["CHECKPOINT_POSTGRES_HOST"],
                                 user="terminal_acceptance", dbname="terminal_acceptance"):
                break
        except psycopg.OperationalError:
            if time.monotonic() > until:
                raise RuntimeError("new_postgres_not_ready") from None
            time.sleep(1)
    from Database.bootstrap import run_bootstrap
    run_bootstrap()
    print(json.dumps({"bootstrap": "PASS", "empty_databases": True}), flush=True)


def serve(role: str) -> None:
    if role == "mcp":
        import uvicorn
        from Agent.CausalAgentMCP.app import create_app
        uvicorn.run(create_app(), host="0.0.0.0", port=8080, log_level="critical")
    elif role == "web":
        from app import create_app
        from gunicorn.app.base import BaseApplication

        class AcceptanceWeb(BaseApplication):
            def load_config(self):
                for key, value in {"bind": "0.0.0.0:5001", "workers": 1, "threads": 12,
                                   "timeout": 120, "loglevel": "critical"}.items():
                    self.cfg.set(key, value)

            def load(self):
                return create_app()

        AcceptanceWeb().run()
    elif role == "worker":
        (SHARED / "worker-source.json").write_text(json.dumps(source_fingerprint(), indent=2), encoding="utf-8")
        from app.agent.worker.bootstrap import main_async
        asyncio.run(main_async())


def emit(report: dict) -> None:
    (SHARED / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    raise SystemExit({"PASS": 0, "FAIL": 1, "BLOCKED": 2}.get(report["status"], 1))


async def inspect_checkpoint(job_id: str) -> dict:
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool, build_checkpointer
    from Agent.deep_agent.state import ProjectDeepAgentState, to_deep_agent_input
    from Agent.deep_agent_tools.identity import build_deep_agent_run_id
    from Agent.deep_agent_tools.models import DataProfile, AlgorithmResult, InvocationRecord
    from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
    from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
    from Agent.deep_agent_tools.runtime_updates import normalize_tool_status, tool_lifecycle_event_key
    from deepagents.middleware.filesystem import FilesystemState
    from deepagents.backends.utils import file_data_to_string
    from langgraph.graph import StateGraph, START, END

    class InspectionState(FilesystemState, ProjectDeepAgentState):
        pass

    fresh = build_data_profile_from_csv(Path("/dataset/input.csv").read_text(encoding="utf-8-sig"))
    async with open_checkpoint_pool() as pool:
        saver = build_checkpointer(pool)
        parent = await saver.aget_tuple({"configurable": {"thread_id": job_id, "checkpoint_ns": ""}})
        child_thread = build_deep_agent_run_id(job_id=job_id)
        # Current LangGraph persists this independently invoked child at root ns.
        # Report the observed namespace explicitly, rather than infer from docs.
        child = await saver.aget_tuple({"configurable": {
            "thread_id": child_thread, "checkpoint_ns": ""}})
        parent_values = parent.checkpoint["channel_values"] if parent else {}
        child_values = child.checkpoint["channel_values"] if child else {}
        # DeltaChannel messages/files must be materialized by the official State
        # reader. This graph is only read with aget_state: never run or updated.
        builder = StateGraph(InspectionState)
        builder.add_node("inspection_only", lambda state: {})
        builder.add_edge(START, "inspection_only")
        builder.add_edge("inspection_only", END)
        reader = builder.compile(checkpointer=saver)
        if child:
            materialized = await reader.aget_state(child.config)
            child_values = {**child_values, **materialized.values}
        profile = child_values.get("data_profile")
        canonical = DataProfile.model_validate(profile) if profile is not None else None
        converted = to_deep_agent_input(parent_values)["data_profile"] if parent else None
        results = [AlgorithmResult.model_validate(value) for value in child_values.get("algorithm_results", {}).values()]
        records = [InvocationRecord.model_validate(value) for value in child_values.get("action_ledger", {}).values()]
        calls = [call for msg in child_values.get("messages", []) for call in getattr(msg, "tool_calls", [])
                 if call.get("name", "").startswith("causal_")]
        tool_ids = [getattr(msg, "tool_call_id", None) for msg in child_values.get("messages", [])
                    if getattr(msg, "type", None) == "tool"]
        call_ids = [call["id"] for call in calls]
        algorithm_records = [record for record in records if record.tool_name.startswith("causal_")]
        by_call = {result.provider_call_id: result for result in results}
        ledger_by_call = {record.provider_call_id: record for record in algorithm_records}
        capability_by_tool = {spec.tool_name: spec.capability_id for spec in DEFAULT_ALGORITHM_SPECS}
        identity_matches = bool(calls) and len(calls) == len(results) == len(algorithm_records) == len(set(call_ids))
        identity_matches = identity_matches and set(call_ids) == set(by_call) == set(ledger_by_call)
        identity_matches = identity_matches and all(
            by_call[call_id].invocation_id == ledger_by_call[call_id].invocation_id
            and by_call[call_id].result_ref == ledger_by_call[call_id].result_ref
            and by_call[call_id].provenance.job_id == job_id
            and ledger_by_call[call_id].job_id == job_id
            and by_call[call_id].provenance.input_identity == ledger_by_call[call_id].input_identity
            and normalize_tool_status(by_call[call_id].status) == ledger_by_call[call_id].final_status
            for call_id in call_ids)
        identity_matches = identity_matches and all(
            by_call[call["id"]].capability_id == capability_by_tool[call["name"]]
            and ledger_by_call[call["id"]].tool_name == call["name"] for call in calls)
        tool_payloads_match = bool(calls) and all(
            AlgorithmResult.model_validate(json.loads(msg.content)) == by_call[msg.tool_call_id]
            for msg in child_values.get("messages", [])
            if getattr(msg, "type", None) == "tool" and msg.tool_call_id in by_call)
        files = child_values.get("files", {})
        raw_verified = {}
        for result in results:
            file_data = files.get(result.raw_result_ref)
            payload = file_data_to_string(file_data).encode("utf-8") if file_data else None
            raw_verified[result.result_ref] = bool(payload is not None
                and len(payload) == result.raw_result_size_bytes
                and hashlib.sha256(payload).hexdigest() == result.raw_result_sha256)
        pc = [result for result in results if result.capability_id == "causal.pc"]
        cdfm = [result for result in results if result.capability_id == "causal.cdfm"]
        decision = child_values.get("structured_response")
        pc_primary = bool(decision and any(result.status == "valid"
            and result.result_ref == decision.primary_result_ref for result in pc))
        requested_primary = bool(decision and any(result.status == "valid"
            and result.result_ref == decision.primary_result_ref for result in [*pc, *cdfm]))
        messages = child_values.get("messages", [])
        last_algorithm_tool = max((i for i, msg in enumerate(messages)
            if getattr(msg, "type", None) == "tool" and getattr(msg, "tool_call_id", None) in call_ids), default=len(messages))
        model_after_results = any(getattr(msg, "type", None) == "ai" for msg in messages[last_algorithm_tool + 1:])
        terminal_statuses = {"succeeded", "failed", "timed_out", "not_applicable", "skipped", "canceled", "not_ready", "discarded"}
        both_terminal = bool(pc and cdfm and identity_matches and all(
            record.final_status in terminal_statuses for record in algorithm_records))
        from app.agent import job_service
        persisted = job_service.read_events_after(job_id)
        terminals_match = bool(algorithm_records) and all(
            sum(event["event_type"] == "tool_call_result"
                and event["event_key"] == tool_lifecycle_event_key(
                    invocation_id=record.invocation_id, attempt_count=record.attempt_count,
                    retry_ordinal=max(record.attempts), event_type="tool_call_result")
                and event["payload_json"].get("status") == record.final_status
                for event in persisted) == 1 for record in algorithm_records)
        return {"fresh_numeric_columns": len(fresh.numeric_columns), "fresh_rows": fresh.row_count,
                "parent_checkpoint": parent is not None, "child_checkpoint": child is not None,
                "child_checkpoint_namespace": child.config["configurable"]["checkpoint_ns"] if child else None,
                "parent_projection_matches_fresh": converted == fresh if converted else False,
                "child_profile_matches_fresh": canonical == fresh if canonical else False,
                "child_numeric_columns": len(canonical.numeric_columns) if canonical else None,
                "quality_fields_preserved": canonical == fresh if canonical else False,
                "result_statuses": dict(Counter(result.status for result in results)),
                "capabilities": [result.capability_id for result in results],
                "ledger_statuses": dict(Counter(record.final_status for record in records)),
                "algorithm_call_count": len(calls), "distinct_call_ids": len({c['id'] for c in calls}),
                "algorithm_tool_messages_once": bool(calls) and all(tool_ids.count(c["id"]) == 1 for c in calls),
                "result_count": len(results), "ledger_count": len(records),
                "call_result_ledger_bijection": identity_matches,
                "tool_message_payloads_match_results": tool_payloads_match,
                "algorithm_terminal_events_match_calls": terminals_match,
                "both_requested_capabilities_terminal": both_terminal,
                "raw_result_integrity": all(raw_verified.values()) if raw_verified else False,
                "pc_valid_with_verified_raw": any(result.status == "valid" and raw_verified[result.result_ref] for result in pc),
                "cdfm_result_statuses": [result.status for result in cdfm],
                "cdfm_inference": "PASS" if any(result.status == "valid" and raw_verified[result.result_ref] for result in cdfm) else "BLOCKED",
                "structured_decision_exists": decision is not None,
                "decision_primary_is_valid_pc": pc_primary,
                "decision_primary_is_valid_requested_algorithm": requested_primary,
                "thread_model_call_count": child_values.get("thread_model_call_count", 0),
                "model_response_after_algorithm_results": model_after_results,
                "finalization_status": parent_values.get("finalization_status"),
                "raw_result_count": sum(result.raw_result_ref is not None for result in results)}


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "id" in fields:
            events.append({"id": int(fields["id"]), "event": fields["event"], "data": json.loads(fields["data"])})
    return events


def run_job_acceptance() -> None:
    import requests
    from app.agent import job_service
    from app.auth.service import find_user

    prefix = os.environ["ACCEPTANCE_PREFIX"]
    base = os.getenv("ACCEPTANCE_BASE_URL", "http://" + prefix + "-web:5001")
    client = requests.Session()
    until = time.monotonic() + 120
    while True:
        try:
            if client.get(base + "/api/sessions", timeout=3).status_code == 401:
                break
        except requests.RequestException:
            pass
        if time.monotonic() > until:
            raise RuntimeError("isolated_web_not_ready")
        time.sleep(1)
    username = "profile_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(24) + "9"
    assert client.post(base + "/api/register", json={"username": username, "password": password}).status_code == 200
    login = client.post(base + "/api/login", json={"username": username, "password": password})
    assert login.status_code == 200
    if login.json().get("csrf_token"):
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    session_response = client.post(base + "/api/new_chat")
    assert session_response.status_code == 200
    session_id = session_response.json()["new_session_id"]
    with Path("/dataset/input.csv").open("rb") as data:
        uploaded = client.post(base + "/api/upload_file", files={"file": ("Alarm-window-counts-300.csv", data, "text/csv")})
    assert uploaded.status_code == 200
    payload = {"session_id": session_id, "input_user_file_id": uploaded.json()["user_file_id"],
               "web_search_enabled": False,
               "message": "请对上传的 Alarm 窗口计数数据进行因果分析。请使用 PC（fisherz）和 CDFM 分别分析并比较；两者没有依赖，请尽量在同一轮调用。必须依据实际数据画像检查输入，不要仅凭统计标签推断数值类型。不得跳过数据处理确认，也不要假定任何算法成功。请使用实际返回结果完成最终决策和报告。"}
    key = str(uuid4())
    created = client.post(base + "/api/agent/jobs", json=payload, headers={"Idempotency-Key": key})
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    replay = client.post(base + "/api/agent/jobs", json=payload, headers={"Idempotency-Key": key})
    assert replay.status_code == 200 and replay.json()["job_id"] == job_id
    # Only container-local browser credentials; stdout/report never contains them.
    browser = {"username": username, "password": password,
                "cookies": client.cookies.get_dict(), "session_id": session_id, "job_id": job_id}
    browser_filename = os.getenv("ACCEPTANCE_BROWSER_FILENAME", "browser.json")
    assert Path(browser_filename).name == browser_filename
    descriptor = os.open(SHARED / browser_filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(browser, output)
    user_id = find_user(username)["id"]
    print(json.dumps({"new_job_id": job_id, "creation_idempotent": True}), flush=True)
    until = time.monotonic() + 1200
    while True:
        job = job_service.get_job_for_user(job_id, user_id)
        events = job_service.read_events_after(job_id)
        print(json.dumps({"status": job["status"], "events": len(events),
                          "event_types": dict(Counter(event["event_type"] for event in events))}), flush=True)
        if job["status"] in job_service.TERMINAL_STATUSES or job["status"] == "waiting_input":
            break
        if time.monotonic() > until:
            emit({"status": "BLOCKED", "reason": "new_job_wait_timeout", "job_id": job_id})
            return
        time.sleep(15)
    finalize_verification(client, base, job_id, job, events)


def finalize_verification(client, base: str, job_id: str, job: dict, events: list) -> None:
    from app.agent import job_service

    checkpoint = asyncio.run(inspect_checkpoint(job_id))
    url = base + "/api/agent/jobs/" + job_id + "/events"
    first = client.get(url, timeout=30)
    refresh = client.get(url, timeout=30)
    assert first.status_code == refresh.status_code == 200
    replayed = parse_sse(first.text)
    midpoint = replayed[len(replayed) // 2]["id"] if replayed else 0
    reconnect = client.get(url, headers={"Last-Event-ID": str(midpoint)}, timeout=30)
    resumed = parse_sse(reconnect.text)
    before = len(events)
    after = job_service.read_events_after(job_id)
    keys = [event["event_key"] for event in after if event["event_key"]]
    terminal_tools = [event for event in after if event["event_type"] == "tool_call_result"]
    public_text = first.text
    safe = all(field not in public_text for field in
               ("invocation_id", "lease_epoch", "provider_call_id", "_event_key", "file_content", "raw_artifacts"))
    report = {"status": "PASS" if job["status"] == "succeeded" else "BLOCKED",
              "verification_scope": "real_model_job_databases_mcp_http_sse",
              "job_id": job_id, "job_status": job["status"], "creation_idempotent": True,
              "frozen_input": bool(job.get("input_file_hash") and job.get("input_object_id")),
              "frozen_input_matches_source": job.get("input_file_hash") == hashlib.sha256(Path("/dataset/input.csv").read_bytes()).hexdigest(),
              **checkpoint, "persisted_events": len(after), "terminal_tool_events": len(terminal_tools),
              "worker_source": json.loads((SHARED / "worker-source.json").read_text(encoding="utf-8")),
              "current_source": source_fingerprint(),
              "event_keys_unique": len(keys) == len(set(keys)),
              "sse_refresh_stable": first.text == refresh.text,
              "sse_ids_unique": len(replayed) == len({event["id"] for event in replayed}),
              "sse_resume_exact": resumed == [event for event in replayed if event["id"] > midpoint],
              "sse_read_does_not_append_events": before == len(after), "public_payload_sanitized": safe,
              "browser": "pending", "limitations": ["no crash takeover or lease-loss injection",
              "no automatic processing approval", "Web search disabled; RAG/Web acceptance not performed",
              "child persisted at root namespace; formal interrupt/crash continuation not verified"]}
    checks = ("parent_projection_matches_fresh", "child_profile_matches_fresh", "quality_fields_preserved",
              "algorithm_tool_messages_once", "event_keys_unique", "sse_refresh_stable", "sse_ids_unique",
              "sse_resume_exact", "sse_read_does_not_append_events", "public_payload_sanitized",
              "call_result_ledger_bijection", "both_requested_capabilities_terminal",
              "tool_message_payloads_match_results", "algorithm_terminal_events_match_calls", "frozen_input_matches_source",
              "pc_valid_with_verified_raw", "structured_decision_exists",
              "decision_primary_is_valid_requested_algorithm", "model_response_after_algorithm_results")
    if not all(report[key] for key in checks) or report["fresh_numeric_columns"] != 11:
        report["status"] = "FAIL"
    if not checkpoint["both_requested_capabilities_terminal"] or not checkpoint["pc_valid_with_verified_raw"]:
        report["status"] = "BLOCKED"
    if report["finalization_status"] != "valid" or report["thread_model_call_count"] < 2:
        report["status"] = "BLOCKED"
    if report["worker_source"] != report["current_source"]:
        report["status"] = "BLOCKED"
    emit(report)


def verify_existing() -> None:
    import requests
    from app.agent import job_service
    from app.auth.service import find_user

    browser = json.loads((SHARED / os.getenv("ACCEPTANCE_BROWSER_FILENAME", "browser.json")).read_text(encoding="utf-8"))
    client = requests.Session()
    client.cookies.update(browser["cookies"])
    job_id = browser["job_id"]
    user_id = find_user(browser["username"])["id"]
    job = job_service.get_job_for_user(job_id, user_id)
    assert job["status"] in job_service.TERMINAL_STATUSES or job["status"] == "waiting_input"
    base = os.getenv("ACCEPTANCE_VERIFY_URL", "http://" + os.environ["ACCEPTANCE_PREFIX"] + "-web:5001")
    finalize_verification(client, base, job_id, job, job_service.read_events_after(job_id))


async def verify_fencing() -> None:
    """隔离 worker 停止后，用正式服务领取、接管和取消新 Job。"""
    import requests
    from app.agent import job_service
    from app.agent.worker.execution_guard import JobExecutionGuard
    from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig
    from app.auth.service import find_user
    from Agent.deep_agent.context import TrustedJobIdentity
    from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
    from Agent.deep_agent_tools.identity import build_invocation_id
    from Agent.deep_agent_tools.mcp_algorithm_executor import McpAlgorithmExecutor
    from Agent.deep_agent_tools.models import AlgorithmExecutionCommand
    from Agent.execution_control import JobExecutionRevoked
    from config.secret_file import load_secrets

    prefix = os.environ["ACCEPTANCE_PREFIX"]
    assert prefix.startswith("profile-job-")
    assert os.environ["MYSQL_HOST"] == prefix + "-mysql"
    browser = json.loads((SHARED / os.getenv("ACCEPTANCE_BROWSER_FILENAME", "browser.json")).read_text())
    user_id = int(find_user(browser["username"])["id"])
    assert not job_service.get_active_jobs(user_id), "refuse_active_user_jobs"
    original = job_service.get_job_by_id(browser["job_id"])
    original_events = job_service.read_events_after(browser["job_id"])
    assert original["status"] == "succeeded"
    client = requests.Session()
    base = os.getenv("ACCEPTANCE_VERIFY_URL", "http://" + prefix + "-web:5001")
    login = client.post(base + "/api/login", json={"username": browser["username"],
                                                             "password": browser["password"]}, timeout=10)
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    created = client.post(base + "/api/new_chat", timeout=10)
    assert created.status_code == 200
    session_id = created.json()["new_session_id"]
    job, duplicate = job_service.create_job(
        user_id, session_id, "隔离环境执行资格验收", str(uuid4()),
        original["input_user_file_id"], request_id=str(uuid4()),
    )
    assert not duplicate
    job_id = job["job_id"]
    current = None
    report = {"status": "FAIL", "scope": "real_mysql_lease_mcp_and_cancel", "job_id": job_id}
    pool = McpClientPool(McpClientPoolConfig(
        url=os.environ["CAUSAL_MCP_URL"], service_token=load_secrets()["CAUSAL_MCP_SERVICE_TOKEN"],
        pool_size=1, control_pool_size=1, read_timeout_seconds=90,
    ))

    def identity(claim):
        return TrustedJobIdentity(
            job_id=job_id, session_id=session_id, user_id=user_id,
            worker_id=claim["worker_id"], attempt_count=claim["attempt_count"],
            lease_epoch=claim["lease_epoch"], input_identity=claim["input_file_hash"],
        )

    def guard(claim):
        return JobExecutionGuard(job_id, claim["worker_id"], claim["attempt_count"], claim["lease_epoch"])

    async def revoked(operation):
        try:
            await operation
        except JobExecutionRevoked:
            return True
        raise AssertionError("revoked_execution_was_accepted")

    async def execute(claim, call_id):
        trusted = identity(claim)
        invocation_id = build_invocation_id(job_id=job_id, response_identity="fencing", provider_call_id=call_id)
        command = AlgorithmExecutionCommand(
            invocation_id=invocation_id, provider_call_id=call_id, capability_id=PC_SPEC.capability_id,
            capability_version=PC_SPEC.version, spec_digest=PC_SPEC.spec_digest,
            input_identity=trusted.input_identity, parameters={"alpha": 0.05, "test": "fisherz"},
        )
        return await executor.execute(command, trusted.to_mcp_context(invocation_id=invocation_id))

    def reject_event(claim, label):
        before = job_service.read_events_after(job_id)
        written = job_service.write_event(
            job_id, claim["worker_id"], claim["attempt_count"], "phase",
            {"type": "phase", "message": "执行资格检查"}, lease_epoch=claim["lease_epoch"],
            event_key="acceptance:revoked:" + label,
        )
        return written is None and job_service.read_events_after(job_id) == before

    def reject_terminal(claim, result):
        before = job_service.get_job_by_id(job_id)
        events = job_service.read_events_after(job_id)
        accepted = job_service.complete_job(
            job_id, claim["worker_id"], claim["attempt_count"],
            result.model_dump(mode="json"), lease_epoch=claim["lease_epoch"],
        )
        return (not accepted and job_service.get_job_by_id(job_id) == before
                and job_service.read_events_after(job_id) == events)

    try:
        current = job_service.claim_next_job("acceptance-old-worker")
        assert current and current["job_id"] == job_id
        old = current
        old_guard = guard(old)
        await old_guard.ensure_active()
        await pool.start()
        executor = McpAlgorithmExecutor(pool, signing_key=load_secrets()["CAUSAL_MCP_SIGNING_KEY_CURRENT"], max_retries=0)
        initial = await execute(old, "before-revocation")
        initial_result = getattr(initial, "result", initial)
        assert initial_result.status == "valid"
        # 真实等待 heartbeat 过期，不改 SQL 时间或租约字段。
        await asyncio.sleep(2)
        reclaimed = job_service.claim_next_job("acceptance-new-worker", stale_after_seconds=1)
        assert reclaimed and reclaimed["job_id"] == job_id and reclaimed["claim_kind"] == "stale_recovery"
        current = reclaimed
        assert current["lease_epoch"] > old["lease_epoch"] and current["attempt_count"] > old["attempt_count"]
        new_guard = guard(current)
        await new_guard.ensure_active()
        report.update({"initial_real_pc_valid": True, "real_stale_reclaim": True,
                       "old_guard_revoked": await revoked(old_guard.check_after_call()),
                       "old_signed_mcp_revoked": await revoked(execute(old, "after-takeover")),
                       "old_event_rejected": reject_event(old, "old"),
                       "old_terminal_rejected": reject_terminal(old, initial_result)})
        cancel_key = str(uuid4())
        canceled, replay = job_service.cancel_job(user_id, job_id, cancel_key)
        assert not replay and canceled["status"] == "canceled" and canceled["execution_state"] == "draining"
        report.update({"cancel_guard_revoked": await revoked(new_guard.check_after_call()),
                       "cancel_signed_mcp_revoked": await revoked(execute(current, "after-cancel")),
                       "cancel_event_rejected": reject_event(current, "canceled"),
                       "cancel_terminal_rejected": reject_terminal(current, initial_result)})
        _, replay = job_service.cancel_job(user_id, job_id, cancel_key)
        report["cancel_idempotent"] = replay
        report["cancel_event_once"] = sum(e["event_type"] == "canceled" for e in job_service.read_events_after(job_id)) == 1
        assert job_service.release_execution_ownership(job_id, current["worker_id"], current["attempt_count"], current["lease_epoch"])
        report["execution_released"] = job_service.get_job_by_id(job_id)["execution_state"] is None
        queued, _ = job_service.create_job(
            user_id, session_id, "隔离环境排队取消验收", str(uuid4()),
            original["input_user_file_id"], request_id=str(uuid4()),
        )
        queued_canceled, _ = job_service.cancel_job(user_id, queued["job_id"], str(uuid4()))
        report["queued_cancel_released"] = (queued_canceled["status"] == "canceled"
                                            and queued_canceled["execution_state"] is None
                                            and queued_canceled["worker_id"] is None)
        report["original_job_unchanged"] = (job_service.get_job_by_id(original["job_id"]) == original
                                             and job_service.read_events_after(original["job_id"]) == original_events)
        assert all(value is True for key, value in report.items() if key not in {"status", "scope", "job_id"})
        report["status"] = "PASS"
        report["cancel_service_sha256"] = hashlib.sha256((ROOT / "app/agent/job_service.py").read_bytes()).hexdigest()
        report["limitations"] = ["No running worker crash/checkpoint continuation",
                                    "Revocation requests tested after reclaim/cancel, not during an in-flight algorithm"]
    finally:
        await pool.close()
        remaining = job_service.get_job_by_id(job_id)
        if remaining["status"] in job_service.ACTIVE_STATUSES:
            job_service.cancel_job(user_id, job_id, str(uuid4()))
            remaining = job_service.get_job_by_id(job_id)
        if current and remaining.get("execution_state") == "draining":
            job_service.release_execution_ownership(job_id, current["worker_id"], current["attempt_count"], current["lease_epoch"])
        (SHARED / "fencing-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["launch", "bootstrap", "mcp", "web", "worker", "run", "verify", "fencing"])
    parser.add_argument("--source-worker", default="causalagent_worker")
    parser.add_argument("--source-app", default="causalagent_app")
    parser.add_argument("--image", default="causalagent-terminal-acceptance:20261020")
    parser.add_argument("--dataset", default=r"D:\Computer\Projects\CausalAgent\tests\dataset\Alarm-window-counts-300.csv")
    parser.add_argument("--port", type=int, default=5187)
    parser.add_argument("--cdfm-weights-volume", help="Existing pinned weights volume, mounted read-only")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    logging.disable(logging.CRITICAL)
    if args.role == "launch":
        launch(args)
    elif args.role == "bootstrap":
        isolated_bootstrap()
    elif args.role == "run":
        run_job_acceptance()
    elif args.role == "verify":
        verify_existing()
    elif args.role == "fencing":
        asyncio.run(verify_fencing())
    else:
        serve(args.role)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Frame locations aid diagnosis; error messages may contain data/secrets.
        frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                  for frame in traceback.extract_tb(error.__traceback__)]
        diagnostic = {"status": "ERROR", "error_type": type(error).__name__, "frames": frames}
        if SHARED.is_dir():
            (SHARED / "error.json").write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")
        print(json.dumps(diagnostic), flush=True)
        raise SystemExit(1) from None
