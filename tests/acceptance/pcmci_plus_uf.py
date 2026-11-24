"""Run the UF baseline and optionally compare it with signed HTTP MCP on isolated MySQL."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from tigramite import data_processing
from tigramite.independence_tests.parcorr import ParCorr
from tigramite.pcmci import PCMCI

PARAMS = {"tau_min": 0, "tau_max": 3, "pc_alpha": 0.05}


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_direct(source):
    frame = pd.read_csv(source, float_precision="round_trip")
    times = pd.to_datetime(frame.pop("DateTime"), errors="raise")
    step = pd.Timedelta(minutes=5)
    _, offsets, sizes = np.unique(times.diff().ne(step).cumsum(), return_index=True, return_counts=True)
    offset, size = int(offsets[sizes.argmax()]), int(sizes.max())
    segment = frame.iloc[offset:offset + size]
    assert segment.shape == (1209, 17)
    config = PARAMS | dict(contemp_collider_rule="majority", conflict_resolution=True, reset_lagged_links=False,
        max_conds_dim=None, max_combinations=1, max_conds_py=None, max_conds_px=None, max_conds_px_lagged=None,
        fdr_method="none")
    parcorr = {"significance": "analytic", "recycle_residuals": False}
    start = time.perf_counter()
    result = PCMCI(dataframe=data_processing.DataFrame(segment.to_numpy(), var_names=list(segment)),
        cond_ind_test=ParCorr(**parcorr), verbosity=0).run_pcmciplus(**config)
    return dict(versions={name: version(name) for name in ("tigramite", "numpy", "scipy", "pandas")},
        python=sys.version.split()[0], start_row=offset + 1, end_row=offset + size, row_count=size,
        start_time=times.iloc[offset].isoformat(), end_time=times.iloc[offset + size - 1].isoformat(),
        sampling_period_seconds=300, tau_max_seconds=900, columns=list(segment), config=config, parcorr=parcorr,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), elapsed_seconds=time.perf_counter() - start,
        marks=Counter(result["graph"].flat)), {key: result[key] for key in ("graph", "p_matrix", "val_matrix")}


def scores(graph, reference):
    truth = np.loadtxt(reference)
    assert truth.shape == graph.shape[:2] and set(np.unique(truth)) <= {0, 1}
    expected = {(int(i), int(j)) for i, j in zip(*np.where(truth != 0)) if i != j}
    directed = {(int(i), int(j)) for i, j, _ in zip(*np.where(graph == "-->")) if i != j}
    adjacent = {tuple(sorted((int(i), int(j)))) for i, j, _ in zip(*np.where(graph != "")) if i != j}
    skeleton = {tuple(sorted(edge)) for edge in expected}
    uncertain = np.count_nonzero(np.triu(np.isin(graph[:, :, 0], ("o-o", "x-x")), 1))

    def measure(found, target):
        tp = len(found & target)
        return dict(found=len(found), reference=len(target), true_positive=tp,
            precision=tp / len(found), recall=tp / len(target), f1=2 * tp / (len(found) + len(target)))

    return dict(skeleton=measure(adjacent, skeleton), direction=measure(directed, expected),
        skeleton_symmetric_difference=len(adjacent ^ skeleton), contemporaneous_uncertain_pairs=int(uncertain),
        opposite_only=sum((j, i) in expected for i, j in directed - expected))


def bind_uf(source_path, output, baseline):
    from Agent.data_processing import persistence
    from Agent.data_processing.contracts import ResearchPlanConfirmationRequest, ResearchPlanSummary
    from Agent.data_processing.parameters import parameter_identity
    from Agent.data_processing.plans import build_processing_plan_version
    from Agent.data_processing.time_axis import TimeSeriesSelection, build_time_series_script, inspect_time_axis, prepare_time_series_plan
    from Agent.data_processing.validation import validate_candidate
    from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
    from Database.analysis_contexts import apply_final_parameters_context
    from app.agent import job_service
    from app.auth.service import find_user
    from tests.acceptance.data_processing_recovery import application, identity_for

    source = source_path.read_bytes()
    digest = baseline["source_sha256"]
    client = application().test_client()
    name, password = "pcmci_" + uuid4().hex[:12], secrets.token_urlsafe(24) + "9"
    for endpoint in ("/api/register", "/api/login"):
        assert client.post(endpoint, json={"username": name, "password": password}).status_code == 200
    user = find_user(name)["id"]
    session = client.post("/api/new_chat").json["new_session_id"]
    upload = client.post("/api/upload_file", data={"file": (BytesIO(source), "uf.csv", "text/csv")})
    assert upload.status_code == 200 and upload.json["success"]
    worker, question = "uf-" + uuid4().hex[:8], "哪些指标与 X17 的后续变化有关？"
    created, replay = job_service.create_job(user, session, question, str(uuid4()),
        input_user_file_id=upload.json["user_file_id"], request_id=str(uuid4()), web_search_enabled=False)
    assert not replay
    job = job_service.claim_next_job(worker)
    assert job and job["job_id"] == created["job_id"] and job["input_file_hash"] == digest
    diagnostic = inspect_time_axis(source, time_column="DateTime", sampling_period_seconds=300.0)
    assert diagnostic["gap_count"] == 160
    save(output / "uf-time-diagnostics.json", diagnostic)
    columns = baseline["columns"]
    metadata = dict(summary="保留确认片段的原始测量值。", observation_unit="一次采样", transformations=["选段并排除时间列"],
        analysis_columns=columns, measurement_columns={"DateTime": None},
        variable_semantics={c: "连续过程测量" for c in columns})
    selection = TimeSeriesSelection(time_column="DateTime", start_row=baseline["start_row"],
        end_row=baseline["end_row"], sampling_period_seconds=300.0)
    window = prepare_time_series_plan(source, selection=selection, metadata=metadata)
    metadata["time_series"] = window.model_dump(mode="json")
    params = dict(discovery_goal="time_series", target="X17", analysis_question=question, effect_request=None,
        measurement_semantics=[{"column": c, "kind": "continuous", "evidence": "UF 发布数据为连续过程测量"} for c in columns])
    semantic = hashlib.sha256(b"uf-pcmci-acceptance").hexdigest()
    plan = build_processing_plan_version(job_id=job["job_id"], source_hash=digest, semantic_identity=semantic, metadata=metadata)
    question_id = str(uuid4())
    interaction = ResearchPlanConfirmationRequest(prompt="确认时序片段", question_id=question_id, plan_version=plan,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        plan=ResearchPlanSummary.model_validate(metadata | {"effect_request": None, "discovery_goal": "time_series"})).public_payload()
    assert job_service.complete_job_with_chat(job, worker, "interrupt", {"type": "interrupt", "interaction": interaction},
        "确认时序片段", None, lease_epoch=job["lease_epoch"], question_id=question_id)
    resumed, replay = job_service.resume_job(user, job["job_id"], question_id,
        {"action": "confirm", "plan_version": plan}, str(uuid4()))
    assert not replay and resumed["status"] == "queued"
    job = job_service.claim_next_job(worker)
    assert job and job["job_id"] == created["job_id"]
    identity = identity_for(job)
    script_path, csv_path = output / "uf-slice.py", output / "uf-bound.csv"
    script = build_time_series_script(window, metadata).encode()
    script_path.write_bytes(script)
    subprocess.run([sys.executable, str(script_path), str(source_path), str(csv_path)], check=True)
    data = csv_path.read_bytes()
    binding = dict(analysis_parameters=params, analysis_parameter_changes=[])
    saved = persistence.save_candidate(identity, source_hash=digest, data_bytes=data, script_bytes=script,
        metadata=metadata, plan_version=plan, semantic_identity=semantic, **binding)
    dataset = dict(dataset_id=saved["dataset_id"], source_hash=digest)
    candidate = persistence.read_candidate(identity, **dataset)
    frozen, frozen_hash = persistence.read_frozen_source(identity)
    validation = dict(script_bytes=script, metadata=candidate["metadata"], analysis_parameters=params,
        specifications=DEFAULT_ALGORITHM_SPECS, max_bytes=20 * 1024 * 1024, source_bytes=frozen,
        source_hash=frozen_hash, expected_parameter_identity=parameter_identity(params))
    report = validate_candidate(data_bytes=candidate["data_bytes"], **validation)
    assert report["accepted"], report["errors"]
    # Replacement preserves the nominal row count and uses real rows from across the first excluded interval.
    shifted = pd.read_csv(source_path, dtype=str)[columns].iloc[window.start_row:window.end_row + 1].to_csv(index=False).encode()
    rejected = validate_candidate(data_bytes=shifted, **validation)
    assert not rejected["accepted"] and any(e["code"] == "time_series_mismatch" for e in rejected["errors"])
    save(output / "uf-validation.json", {"accepted": report, "row_substitution": rejected})
    persistence.record_validation(identity, **dataset, report=report, accepted=True)
    assert apply_final_parameters_context(user_id=user, session_id=session, job_id=job["job_id"], worker_id=worker,
        attempt_count=job["attempt_count"], lease_epoch=job["lease_epoch"], analysis_parameters=params,
        dataset_id=saved["dataset_id"])
    persistence.bind_candidate(identity, **dataset, **binding)
    rebound = persistence.read_bound_dataset_identity(identity)
    assert rebound["data_hash"] == hashlib.sha256(data).hexdigest() and rebound["time_series"] == report["time_series"]
    assert rebound["analysis_parameters"]["discovery_goal"] == "time_series"
    identity.analysis_input.bind(dataset_id=saved["dataset_id"], object_id=rebound["data_object_id"],
        content_hash=rebound["data_hash"], filename="uf-bound.csv", semantic_kinds=rebound["semantic_kinds"],
        research_identity=parameter_identity(params), time_series=rebound["time_series"])
    return identity, report


async def run_mcp(output, identity, report, matrices):
    import httpx2
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from Agent.CausalAgentMCP.app import create_app
    from Agent.CausalAgentMCP.auth import sign_invocation
    from Agent.CausalAgentMCP.config import McpServerConfig
    from Agent.deep_agent_tools.algorithm_specs import PCMCI_PLUS_SPEC
    from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, AlgorithmResult
    from Agent.deep_agent_tools.time_series import VerifiedTimeAxis

    spec, config = PCMCI_PLUS_SPEC, McpServerConfig.from_env()
    server = uvicorn.Server(uvicorn.Config(create_app(config), log_level="warning", access_log=False))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("MCP HTTP server failed to start")
                await asyncio.sleep(0.05)
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {config.service_token}"},
                                      timeout=httpx2.Timeout(330, connect=5), trust_env=False) as http:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=330) as client:
                    await client.initialize()
                    for case, artifacts in (("valid", {}), ("untrusted_axis", {"time_series": report["time_series"]})):
                        context = identity.to_mcp_context(invocation_id=str(uuid4()), key_id=config.signing_key_id)
                        command = AlgorithmExecutionCommand(invocation_id=context.invocation_id,
                            capability_id=spec.capability_id, capability_version=spec.version, spec_digest=spec.spec_digest,
                            provider_call_id=case, input_identity=context.input_snapshot_digest, research_identity=context.research_identity,
                            parameters=PARAMS, artifacts=artifacts, timeout_seconds=spec.default_timeout_seconds)
                        response = (await client.call_tool("execute_algorithm", {"command": command.model_dump(mode="json"),
                            "trusted_context": context.model_dump(mode="json"),
                            "signature": sign_invocation(context, command, config.signing_key_current)})).structured_content
                        assert response and response["ok"], response
                        save(output / f"uf-mcp-{case}.json", response)
                        result = AlgorithmResult.model_validate(response["result"])
                        if case == "valid":
                            assert result.status == "valid", result.diagnostics
                            assert result.time_series.time_axis == VerifiedTimeAxis.model_validate(report["time_series"])
                            assert result.provenance.analysis_dataset_id == identity.analysis_input.dataset_id
                            assert result.standardized_graph is None
                            assert any(e.source == e.target and e.lag_steps > 0 for e in result.time_series.relations)
                            raw = response["raw_payload"]["raw_results"]
                            np.testing.assert_array_equal(raw["graph"], matrices["graph"])
                            for key in ("p_matrix", "val_matrix"):
                                np.testing.assert_allclose(np.asarray(raw[key], dtype=float), matrices[key], rtol=1e-10, atol=1e-13)
                        else:
                            assert result.status == "invalid_input" and "raw_payload" not in response
    finally:
        server.should_exit = True
        await task
        sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mcp", action="store_true")
    args = parser.parse_args()
    if args.mcp and (os.environ.get("MYSQL_HOST") != "pcmci-mysql" or os.environ.get("MYSQL_DATABASE") != "pcmci_acceptance"
                     or os.environ.get("MYSQL_WRITE_HOST", "pcmci-mysql") != "pcmci-mysql"):
        parser.error("--mcp requires isolated pcmci-mysql / pcmci_acceptance")
    args.output.mkdir(parents=True, exist_ok=False)
    baseline, matrices = run_direct(args.source)
    np.savez_compressed(args.output / "uf-direct.npz", **matrices)
    save(args.output / "uf-direct.json", baseline)
    for filename in ("UFGroundTruth.txt", "UFIMDGroundTruth.txt"):
        reference = args.source.with_name(filename)
        if reference.is_file():
            save(args.output / (reference.stem + "-scores.json"), scores(matrices["graph"], reference))
    if args.mcp:
        identity, report = bind_uf(args.source, args.output, baseline)
        asyncio.run(run_mcp(args.output, identity, report, matrices))
    print(json.dumps({"direct": "passed", "mcp": "passed" if args.mcp else "not_run", "output": str(args.output)}))


if __name__ == "__main__":
    main()
