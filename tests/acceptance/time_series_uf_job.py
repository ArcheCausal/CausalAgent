"""Run two real UF Jobs: time-series analysis, then report regeneration.

Use the worker's existing environment and mounted secret file. The MySQL database,
PostgreSQL checkpointer, MCP and OpenSandbox controller must already be running.
The driver submits explicit user replies via the real Flask routes; no browser is used.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import tempfile
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

QUESTION = ("请对上传的生产记录做因果发现：哪些指标的较早变化可能与 X17 的后续变化有关？"
            "相隔多久？请生成报告，并说明哪些关系的方向还不能确定。")
CLARIFICATION = ("这是同一生产过程按记录顺序保存的采样数据，测量指标是连续数值。"
                 "重点解释与 X17 有关的时序关系，可保留其他测量指标来检查条件关系。"
                 "请根据文件建议分析范围、说明理由，再让我确认。")
REGENERATE = "请仅根据当前分析上下文重新生成完整报告，沿用已保存的结论、方法和分析范围，不要重做数据处理或运行算法。"


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def analysis_checkpoint_config(job_id):
    from Agent.deep_agent_tools.identity import build_deep_agent_run_id

    return {"configurable": {"thread_id": build_deep_agent_run_id(job_id=job_id)}}


async def analysis_files(saver, job_id):
    """Materialize the production filesystem's delta channel from PostgreSQL."""
    from deepagents.middleware.filesystem import FilesystemState
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(FilesystemState)
    builder.add_node("inspect_only", lambda state: {})
    builder.add_edge(START, "inspect_only")
    builder.add_edge("inspect_only", END)
    graph = builder.compile(checkpointer=saver)
    return (await graph.aget_state(analysis_checkpoint_config(job_id))).values.get("files", {})


def verified_findings(job, state):
    document = (job.get("result_json") or {}).get("document") or {}
    independent = state["report_document"]
    if hasattr(independent, "model_dump"):
        independent = independent.model_dump(mode="json")
    require(independent == document, "stored_report_differs_from_checkpoint")
    summary = document.get("execution_summary") or {}
    require(document.get("report_mode") == "analysis"
            and summary.get("report_completeness") == "complete"
            and not summary.get("degradation_code"), "full_time_series_report_missing")
    blocks = [block for block in document.get("blocks", [])
              if block.get("id", "").startswith("time_series_findings")
              and "PCMCI+ 使用 ParCorr" in block.get("content", "")]
    require(len(blocks) == 1, "time_series_findings_or_method_missing")
    return document["report_id"], blocks[0]["content"]


async def verify_analysis(job, runtime, state, user_id, selected, source_hash, evidence):
    from Agent.causal_agent.analysis_context import report_primary_result
    from Agent.data_processing.graph import DATA_PROCESSING_CHECKPOINT_NAMESPACE
    from Agent.data_processing.time_axis import TimeSeriesWindow
    from Agent.deep_agent_tools.models import AlgorithmResult
    from Agent.deep_agent_tools.time_series import TimeSeriesResult
    from Database.analysis_contexts import load_context
    from app.db import get_read_connection
    from deepagents.backends.utils import file_data_to_string
    from langchain_core.messages import ToolMessage
    from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER

    job_id = job["job_id"]
    dataset_id = job.get("prepared_dataset_id")
    require(dataset_id and state.get("analysis_parameters", {}).get("discovery_goal") == "time_series",
            "time_series_goal_or_prepared_dataset_missing")
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT status, source_hash, data_hash, validation_report FROM analysis_job_datasets WHERE id=%s AND job_id=%s",
                       (dataset_id, job_id))
        dataset = cursor.fetchone()
        cursor.execute("SELECT status, operation_status FROM analysis_sandbox_sessions WHERE job_id=%s", (job_id,))
        sessions = cursor.fetchall()
    require(dataset and dataset["status"] == "validated" and dataset["source_hash"] == source_hash,
            "validated_frozen_dataset_missing")
    report = dataset["validation_report"]
    if not isinstance(report, dict):
        report = json.loads(report)
    axis = TimeSeriesWindow.model_validate(selected).verified_axis(source_hash)
    require(report.get("accepted") is True and report.get("time_series") == axis
            and state["dataset_validation_report"]["time_series"] == axis and axis["time_column"] not in report["columns"],
            "independent_axis_differs_from_confirmed_slice")
    require(any(row["operation_status"] == "succeeded" for row in sessions)
            and all(row["status"] == "closed" for row in sessions), "sandbox_did_not_finish_and_close")

    suffix = state["data_processing_run_id"].partition(":")[2]
    processing = await runtime.data_processing_agent.aget_state({"configurable": {
        "thread_id": job_id, "checkpoint_ns": f"{DATA_PROCESSING_CHECKPOINT_NAMESPACE}:{suffix}",
        CONFIG_KEY_CHECKPOINTER: runtime.checkpointer,
    }})
    tool_messages = [message for message in processing.values.get("messages", [])
                     if isinstance(message, ToolMessage) and message.name == "inspect_frozen_time_axis"]
    diagnoses = [json.loads(message.content) for message in tool_messages if message.status == "success"]
    require(any(item.get("status") == "inspected" and item.get("time_column") == axis["time_column"]
                and item.get("row_count") == 23_132
                and item.get("gap_count", 0) > 0 for item in diagnoses), "model_did_not_scan_full_uf_and_detect_gaps")
    context_id = job.get("analysis_context_id")
    context = load_context(user_id, job["session_id"], context_id)
    facts = context["latest_algorithm_summary"]["analysis_facts"]
    primary = report_primary_result(facts)
    require(primary and primary["algorithm"] == "causal.pcmci_plus", "pcmci_plus_not_adopted_as_primary")
    result = AlgorithmResult.model_validate(state["deep_agent_algorithm_results"][primary["result_ref"]])
    require(result.status == "valid" and result.time_series is not None
            and result.provenance.job_id == job_id and result.provenance.analysis_dataset_id == dataset_id
            and result.provenance.input_identity == dataset["data_hash"]
            and result.provenance.source_input_identity == source_hash
            and result.time_series == TimeSeriesResult.model_validate(primary["time_series"]),
            "accepted_algorithm_result_identity_or_facts_mismatch")
    file_data = (await analysis_files(runtime.checkpointer, job_id)).get(result.raw_result_ref)
    require(file_data is not None, "protected_raw_result_missing")
    payload = file_data_to_string(file_data).encode("utf-8")
    require(hashlib.sha256(payload).hexdigest() == result.raw_result_sha256 == primary["raw_result"]["sha256"]
            and len(payload) == result.raw_result_size_bytes, "raw_result_integrity_mismatch")
    raw = json.loads(payload)
    matrices = raw["raw_results"]
    names = {name: index for index, name in enumerate(matrices["columns"])}
    series = result.time_series
    require(raw.get("time_axis") == axis and series.time_axis.model_dump(mode="json") == axis
            and raw.get("parcorr", {}).get("significance") == "analytic"
            and all(raw["configuration"][key] == getattr(series, key) for key in ("tau_min", "tau_max", "pc_alpha"))
            and matrices["columns"] == report["columns"], "raw_method_axis_or_variable_order_mismatch")
    for relation in series.relations:
        i, j, lag = names[relation.source], names[relation.target], relation.lag_steps
        require(matrices["graph"][i][j][lag] == relation.mark
                and matrices["p_matrix"][i][j][lag] == relation.p_value
                and matrices["val_matrix"][i][j][lag] == relation.value
                and relation.lag_seconds == lag * axis["sampling_period_seconds"], "raw_relation_mismatch")
    graph = matrices["graph"]
    raw_edge_count = sum(bool(mark) for row in graph for pair in row for mark in pair[1:]) + sum(
        bool(graph[i][j][0]) for i in range(len(names)) for j in range(i + 1, len(names))
    )
    require(len(series.relations) == raw_edge_count, "raw_relationships_were_omitted")
    report_id, findings = verified_findings(job, state)
    require(series.time_axis.start_time.isoformat() in findings and series.time_axis.end_time.isoformat() in findings
            and f"{axis['sampling_period_seconds']} 秒" in findings, "verified_axis_missing_from_report")
    evidence.update(dataset_id=dataset_id, time_series=axis, method=primary["algorithm"],
                    configuration=raw["configuration"], relation_count=len(series.relations),
                    x17_incoming_lagged=sum(r.target == "X17" and r.source != "X17" and r.lag_steps > 0 for r in series.relations),
                    raw_result_sha256=result.raw_result_sha256,
                    initial_report_id=report_id, analysis_context_id=context_id)
    return facts, findings


async def run(source, timeout, evidence):
    from Agent.causal_agent.analysis_context import build_report_analysis_context
    from Agent.causal_agent.postgres_checkpointer import open_checkpoint_pool
    from Agent.data_processing.time_axis import TimeSeriesWindow
    from app import create_app
    from app.agent import job_service
    from app.agent.public_events import public_event_payload
    from app.agent.worker.bootstrap import run_slot
    from app.agent.worker.runtime import initialize_production_runtime
    from app.auth.service import find_user
    client = create_app().test_client()
    credentials = {"username": "uf_job_" + uuid4().hex[:12], "password": secrets.token_urlsafe(24) + "9"}
    require(client.post("/api/register", json=credentials).status_code == 200, "registration_failed")
    login = client.post("/api/login", json=credentials)
    require(login.status_code == 200, "login_failed")
    client.environ_base["HTTP_X_CSRF_TOKEN"] = login.json["csrf_token"]
    user_id = find_user(credentials["username"])["id"]
    session = client.post("/api/new_chat")
    require(session.status_code == 200, "session_creation_failed")
    session_id = session.json["new_session_id"]
    upload = client.post("/api/upload_file", data={"file": (BytesIO(source), "DatasetUF.csv", "text/csv")})
    require(upload.status_code == 200 and upload.json["success"], "real_uf_upload_failed")
    source_hash = hashlib.sha256(source).hexdigest()
    job_ids = []
    stop = asyncio.Event()

    async def finish(slot, prompt, *, analysis):
        response = client.post("/api/agent/jobs", json={"session_id": session_id,
            "input_user_file_id": upload.json["user_file_id"], "message": prompt, "web_search_enabled": False},
            headers={"Idempotency-Key": str(uuid4())})
        require(response.status_code == 202, "job_submission_failed")
        job_id = response.json["job_id"]
        job_ids.append(job_id)
        evidence["analysis_job_id" if analysis else "regeneration_job_id"] = job_id
        print(json.dumps({"phase": "analysis" if analysis else "regeneration", "job_id": job_id}), flush=True)
        selected = None
        confirmed_attempt = None
        clarification_count = 0
        confirmed_versions = set()
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if slot.done():
                await slot
                raise AssertionError("worker_slot_ended_early")
            job = job_service.get_job_for_user(job_id, user_id)
            if job["status"] in {"succeeded", "failed", "canceled"}:
                evidence["analysis_job_status" if analysis else "regeneration_job_status"] = job["status"]
                require(job["status"] == "succeeded", "analysis_job_failed" if analysis else "regeneration_job_failed")
                if analysis:
                    require(selected is not None and int(job["attempt_count"]) > confirmed_attempt,
                            "analysis_did_not_resume_same_job_after_confirmation")
                return job, selected
            if job["status"] == "waiting_input":
                require(analysis, "regeneration_requested_unexpected_input")
                interaction = public_event_payload("interrupt", {"interaction": job["current_interaction"]})["interaction"]
                kind = interaction["interaction_type"]
                if kind == "data_processing_question":
                    clarification_count += 1
                    require(clarification_count <= 2, "unexpected_repeated_clarification")
                    answer = CLARIFICATION
                else:
                    require(kind == "data_processing_plan_confirmation", "missing_time_series_proposal")
                    plan = interaction["plan"]
                    require(plan.get("discovery_goal") == "time_series", "model_did_not_propose_a_time_series_plan")
                    version = interaction["plan_version"]
                    require(version not in confirmed_versions and len(confirmed_versions) < 3,
                            "repeated_or_excessive_time_series_proposals")
                    confirmed_versions.add(version)
                    selected = TimeSeriesWindow.model_validate(plan["time_series"]).model_dump(mode="json")
                    confirmed_attempt = int(job["attempt_count"])
                    evidence["confirmation_count"] = len(confirmed_versions)
                    answer = {"action": "confirm", "plan_version": version}
                resumed = client.post(f"/api/agent/jobs/{job_id}/resume",
                    json={"question_id": job["current_question_id"], "answer": answer},
                    headers={"Idempotency-Key": str(uuid4())})
                require(resumed.status_code == 202 and resumed.json["job_id"] == job_id, "job_resume_failed")
                print(json.dumps({"phase": "resumed", "job_id": job_id, "interaction_type": kind}), flush=True)
            await asyncio.sleep(0.5)
        raise AssertionError("job_deadline_exceeded")

    async with open_checkpoint_pool() as pool, AsyncExitStack() as stack:
        runtime = await initialize_production_runtime(pool, stack)
        slot = asyncio.create_task(run_slot(0, pool, runtime, stop))
        try:
            job, selected = await finish(slot, QUESTION, analysis=True)
            state = (await runtime.graph.aget_state({"configurable": {"thread_id": job["job_id"]}})).values
            baseline, findings = await verify_analysis(job, runtime, state, user_id, selected, source_hash, evidence)
            regenerated, _ = await finish(slot, REGENERATE, analysis=False)
            restored = (await runtime.graph.aget_state({"configurable": {"thread_id": regenerated["job_id"]}})).values
            report_id, repeated = verified_findings(regenerated, restored)
            require(regenerated["analysis_context_id"] == job["analysis_context_id"]
                    and regenerated["input_file_hash"] == source_hash
                    and restored.get("report_revision_mode") == "full_regeneration_from_context"
                    and build_report_analysis_context(restored) == baseline
                    and repeated == findings and report_id != evidence["initial_report_id"]
                    and await runtime.checkpointer.aget_tuple(analysis_checkpoint_config(regenerated["job_id"])) is None,
                    "regeneration_recomputed_or_changed_time_series_facts")
            evidence.update(regenerated_report_id=report_id, status="PASS")
        finally:
            for job_id in job_ids:
                current = job_service.get_job_for_user(job_id, user_id)
                if current and current["status"] in {"queued", "running", "waiting_input"}:
                    job_service.cancel_job(user_id, job_id, str(uuid4()))
            stop.set()
            await asyncio.wait_for(slot, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uf-csv", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=900, help="timeout for each Job")
    parser.add_argument("--confirm-isolated", required=True, action="store_true")
    parser.add_argument("--artifacts-dir", type=Path, help="parent directory for private run logs")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    required_env = ("MYSQL_DATABASE", "CHECKPOINT_POSTGRES_HOST", "CAUSAL_MCP_URL", "CAUSALAGENT_SANDBOX_CONTROLLER_URL")
    if not all(os.getenv(name) for name in required_env) or not (os.getenv("MYSQL_WRITE_HOST") or os.getenv("MYSQL_HOST")):
        parser.error("set the isolated MySQL, PostgreSQL, MCP and controller environment before starting")
    artifacts = Path(tempfile.mkdtemp(prefix="causalagent-uf-job-", dir=args.artifacts_dir))
    log_path = artifacts / "private.log"
    os.close(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    logging.basicConfig(filename=log_path, level=logging.INFO, force=True)
    evidence = {"status": "FAIL", "private_log": str(log_path)}
    try:
        asyncio.run(run(args.uf_csv.read_bytes(), args.timeout_seconds, evidence))
    except BaseException as exc:
        evidence.update(status="FAIL", failure_type=type(exc).__name__,
                        failure_code=str(exc) if isinstance(exc, AssertionError) else "see_private_log")
        with log_path.open("a", encoding="utf-8") as log:
            traceback.print_exception(exc, file=log)
    print(json.dumps(evidence), flush=True)
    raise SystemExit(0 if evidence["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
