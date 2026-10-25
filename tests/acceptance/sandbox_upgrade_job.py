"""真实模型、正式 Worker、MCP HTTP 和沙盒控制服务的隔离 Job 验收。

文档职责：执行上传、模型澄清与确认、真实处理、独立校验、算法及报告。
适用范围：仅复用 data_processing_recovery 的专用数据库，模型客户端须允许外联。
控制服务另行启动；本入口不持有 OpenSandbox 上游 key，不修改业务配置。
验收答复是显式测试输入，脚本、模型输出、算法和报告均由正式组件生成。
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import random
import secrets
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ARTIFACTS = Path("/tmp/sandbox-upgrade-job")


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def open_private_log(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def configure(model_file):
    from tests.acceptance.data_processing_recovery import configure as restore

    restore(resume=True)
    require(os.environ.get("MYSQL_WRITE_HOST") == "recovery-mysql"
            and os.environ.get("CHECKPOINT_POSTGRES_HOST") == "recovery-postgres"
            and not os.environ.get("MYSQL_READ_HOSTS"), "dedicated_databases_required")
    models = json.loads(model_file.read_text())
    require(set(models) == {"outer", "deep", "context_window_tokens"}, "model_only_configuration_required")
    secret_path = ARTIFACTS / "worker-secrets.env"
    source = Path(os.environ["CAUSALAGENT_SECRET_FILE"]).read_text()
    values = dict(line.split("=", 1) for line in source.splitlines() if line and not line.startswith("#"))
    values.update(API_KEY=models["outer"]["api_key"], DEEP_AGENT_API_KEY=models["deep"]["api_key"])
    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
    os.environ.update({
        "CAUSALAGENT_SECRET_FILE": str(secret_path),
        "MODEL": models["outer"]["model"], "BASE_URL": models["outer"]["base_url"],
        "DEEP_AGENT_MODEL": models["deep"]["model"], "DEEP_AGENT_BASE_URL": models["deep"]["base_url"],
        "DEEP_AGENT_CONTEXT_WINDOW_TOKENS": str(models["context_window_tokens"]),
        "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(ARTIFACTS / "rag" / "active_index.json"),
        "MULTIMODAL_INDEX_ROOT": str(ARTIFACTS / "rag" / "indexes"),
        "CAUSAL_MCP_URL": "http://127.0.0.1:8080/mcp",
        "CAUSAL_MCP_PROCESS_WORKERS": "1", "JOB_WORKER_SLOTS": "1",
        "CAUSALAGENT_SANDBOX_CONTROLLER_URL": os.environ["FULL_JOB_SANDBOX_CONTROLLER_URL"],
        "CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN_FILE": "/tmp/full-job-controller.token",
    })


def serve_mcp():
    import uvicorn
    from Agent.CausalAgentMCP.app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=8080, access_log=False, log_level="critical")


async def verify(report, timeout):
    import httpx
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool
    from app.agent import job_service
    from app.agent.worker.bootstrap import run_slot
    from app.agent.worker.runtime import initialize_production_runtime
    from app.db import get_read_connection
    from tests.acceptance.data_processing_recovery import application

    async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
        for _ in range(40):
            try:
                response = await client.get("http://127.0.0.1:8080/ready")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("actual_mcp_http_not_ready")
        response = await client.get(os.environ["CAUSALAGENT_SANDBOX_CONTROLLER_URL"] + "/healthz")
        require(response.status_code == 200, "actual_sandbox_controller_not_ready")
    report["dependencies_ready"] = True
    client = application().test_client()
    name, password = "full_job_" + uuid4().hex[:10], secrets.token_urlsafe(24) + "9"
    require(client.post("/api/register", json={"username": name, "password": password}).status_code == 200, "registration_failed")
    require(client.post("/api/login", json={"username": name, "password": password}).status_code == 200, "login_failed")
    from app.auth.service import find_user
    user_id = find_user(name)["id"]
    session_id = client.post("/api/new_chat").json["new_session_id"]
    rng = random.Random(20261025)
    rows = []
    for _ in range(240):
        x = rng.expovariate(1)
        rows.append(f"{x},{1.8*x+rng.gauss(0,0.3)},{rng.gauss(0,1)}")
    source = ("x,y,z\n" + "\n".join(rows) + "\n").encode()
    upload = client.post("/api/upload_file", data={"file": (BytesIO(source), "device-measurements.csv", "text/csv")})
    require(upload.status_code == 200 and upload.json["success"], "actual_upload_failed")
    prompt = ("请对上传数据做真实因果分析并生成完整报告，先使用 PC（fisherz）算法。"
              "x、y、z 均为连续测量值，研究 x 对 y 的作用，z 为另一连续测量变量。"
              "请先向我澄清每一行的观察单位，再记录保留全部行和三个原始列的数据处理方案并请求确认。"
              "不聚合、不标准化、不增加字段；请依据真实算法结果保守说明因果识别限制。")
    job, replay = job_service.create_job(user_id, session_id, prompt, str(uuid4()),
        input_user_file_id=upload.json["user_file_id"], request_id=str(uuid4()), web_search_enabled=False)
    require(not replay, "unexpected_job_replay")
    job_id = job["job_id"]
    report.update(job_id=job_id, upload_sha256=hashlib.sha256(source).hexdigest(), interactions=[])
    stop = asyncio.Event()
    slot = None
    worker_process = None
    try:
        async with open_checkpoint_pool() as pool, AsyncExitStack() as stack:
            runtime = await initialize_production_runtime(pool, stack)
            report["production_runtime_initialized"] = True
            report["rag_status"] = runtime.rag_status
            slot = asyncio.create_task(run_slot(0, pool, runtime, stop))
            deadline = asyncio.get_running_loop().time() + timeout
            modified_plan = False
            while asyncio.get_running_loop().time() < deadline:
                if slot is not None and slot.done():
                    await slot
                    raise AssertionError("worker_slot_ended_before_job")
                if worker_process is not None:
                    require(worker_process.poll() is None, "restarted_worker_exited_early")
                job = job_service.get_job_by_id(job_id)
                if job["status"] in {"succeeded", "failed", "canceled"}:
                    break
                if job["status"] == "waiting_input":
                    require(len(report["interactions"]) < 6, "interaction_budget_exhausted")
                    interaction = job["current_interaction"]
                    kind = interaction["interaction_type"]
                    if kind == "data_processing_question":
                        answer = "每一行是一台不同设备的一次独立测量。保留所有设备和原始连续测量值，不聚合。"
                    elif kind == "data_processing_plan_confirmation":
                        if not modified_plan:
                            answer = "补充研究参数：时间窗口为2026年10月1日至7日，文件已包含完整范围，不筛选任何行；请保留原处理范围并重新确认。"
                            modified_plan = True
                        else:
                            answer = json.dumps({"action": "confirm", "plan_version": interaction["plan_version"]})
                    else:
                        raise AssertionError("unexpected_interaction_type")
                    if len(report["interactions"]) == 2:
                        stop.set()
                        await asyncio.wait_for(slot, timeout=30)
                        slot = None
                        with open_private_log(ARTIFACTS / "restarted-worker.log") as log:
                            worker_process = subprocess.Popen([sys.executable, "-m", "app.agent.worker"],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                        report["actual_worker_process_restart"] = True
                    job_service.resume_job(user_id, job_id, job["current_question_id"], answer, str(uuid4()))
                    report["interactions"].append({"type": kind, "answer_kind": "confirmation" if answer.startswith("{") else "research_parameters"})
                    print(json.dumps({"phase": "resumed", "interaction_count": len(report["interactions"])}), flush=True)
                await asyncio.sleep(0.5)
            else:
                job_service.cancel_job(user_id, job_id, str(uuid4()))
                stop.set()
                if slot is not None:
                    await asyncio.wait_for(slot, timeout=30)
                raise AssertionError("full_job_deadline_exceeded")
            stop.set()
            if slot is not None:
                await asyncio.wait_for(slot, timeout=30)
            report["job_status"] = job["status"]
            events = job_service.read_events_after(job_id, limit=10000)
            report["event_count"] = len(events)
            report["event_types"] = sorted(set(event["event_type"] for event in events))
            require(job["status"] == "succeeded", "full_job_not_succeeded")
            require(job.get("prepared_dataset_id"), "actual_dataset_not_bound")
            snapshot = await runtime.graph.aget_state({"configurable": {"thread_id": job_id}})
            values = snapshot.values
            report["parameter_revision_count"] = len(values.get("analysis_parameter_changes") or [])
            require(report["parameter_revision_count"] >= 2, "research_revisions_not_retained")
            report["dataset_validated"] = values.get("dataset_validation_status") == "accepted"
            report["report_present"] = bool(values.get("report_document"))
            results = list((values.get("deep_agent_algorithm_results") or {}).values())
            report["algorithm_results"] = [{"capability_id": item.capability_id, "status": item.status}
                                           for item in results]
            require(any(item.capability_id == "causal.pc" and item.status == "valid" for item in results),
                    "no_valid_real_pc_result")
            require(report["report_present"], "structured_report_not_present")
            with get_read_connection(consistency="strong") as connection:
                cursor = connection.cursor(dictionary=True)
                cursor.execute("SELECT status, operation_status FROM analysis_sandbox_sessions WHERE job_id=%s", (job_id,))
                sessions = cursor.fetchall()
                report["sandbox_sessions"] = [{"status": row["status"], "operation_status": row["operation_status"]} for row in sessions]
            require(sessions and any(row["operation_status"] == "succeeded" for row in sessions), "no_actual_sandbox_execution")
            require(all(row["status"] == "closed" for row in sessions), "remote_resources_not_closed")
            require(report["dataset_validated"], "independent_validation_not_accepted")
            report["checkpoint_keys"] = sorted(values)
            report["status"] = "PASS"
    finally:
        current = job_service.get_job_by_id(job_id)
        if current and current["status"] in {"queued", "running", "waiting_input"}:
            job_service.cancel_job(user_id, job_id, str(uuid4()))
        if worker_process is not None and worker_process.poll() is None:
            worker_process.terminate()
            try:
                await asyncio.to_thread(worker_process.wait, 35)
            except subprocess.TimeoutExpired:
                worker_process.kill()
                await asyncio.to_thread(worker_process.wait, 10)
        stop.set()
        if slot is not None:
            try:
                await asyncio.wait_for(slot, timeout=30)
            except (TimeoutError, asyncio.CancelledError):
                slot.cancel()
                await asyncio.gather(slot, return_exceptions=True)


def main():
    global ARTIFACTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("run", "mcp"), default="run")
    parser.add_argument("--model-config", type=Path, default=Path("/tmp/full-job-models.json"))
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    require(os.environ.get("FULL_JOB_SANDBOX_ACCEPTANCE") == "1", "explicit_isolated_acceptance_required")
    logging.disable(logging.CRITICAL)
    if args.role == "mcp":
        serve_mcp()
        return
    ARTIFACTS = ARTIFACTS / uuid4().hex
    ARTIFACTS.mkdir(parents=True)
    private_log = ARTIFACTS / "worker.log"
    descriptor = os.open(private_log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    logging.getLogger().handlers = [logging.FileHandler(private_log)]
    logging.getLogger().setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    report = {"status": "FAIL", "started_at": datetime.now(timezone.utc).isoformat(),
        "limitations": ["synthetic data and explicit tester answers", "Flask upload routes and Job service; no browser",
                        "real production Worker, models, controller and MCP HTTP", "RAG unavailable and Web disabled"]}
    sources = ("Agent/causal_agent/nodes.py", "Agent/data_processing/graph.py",
               "Agent/data_processing/parameters.py", "Agent/data_processing/agent.py",
               "app/agent/worker/runtime.py", "tests/acceptance/sandbox_upgrade_job.py")
    report["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    process = None
    try:
        configure(args.model_config)
        with open_private_log(ARTIFACTS / "mcp.log") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--role", "mcp"],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        asyncio.run(verify(report, args.timeout_seconds))
    except BaseException as exc:
        report["failure_types"] = [type(exc).__name__]
        report["failure_code"] = str(exc) if isinstance(exc, AssertionError) else "see_private_logs"
        with (ARTIFACTS / "failure.log").open("w") as log:
            import traceback
            traceback.print_exception(exc, file=log)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["source_unchanged_during_run"] = all(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            for name, digest in report["source_sha256"].items())
        path = ARTIFACTS / ("report-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".json")
        report["report_path"] = str(path)
        path.write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
