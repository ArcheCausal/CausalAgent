"""真实 Alarm、Sachs 与 causal-learn/CDFM 的人工验收入口。

通过真实 Streamable HTTP MCP 传输、签名校验和进程池执行 PC。
输入权限来自本次创建的本地冻结清单，不连接或伪装 MySQL Job/lease。
CDFM 直接运行正式 runner，全量使用同一冻结 CSV；保留生产 MCP 超时配置。
运行：python tests/acceptance/pc_measurement_semantics_real.py --output /tmp/pc-semantics --cdfm
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import secrets
import socket
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx2
import numpy as np
import pandas as pd
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from Agent.CausalAgentMCP.app import create_app
from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS, PC_SPEC
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, McpInvocationContext
from Agent.measurement_semantics import ValidatedAlgorithmInput, semantic_kinds_from_validation_report


def save(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def prepare(output: Path) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(ROOT / "tests/dataset/Alarm.csv")
    ordered = raw.sort_values("start_timestamp", kind="stable")
    starts = ordered.start_timestamp.to_numpy()
    labels = ordered.alarm_id.to_numpy()
    columns = [f"window_alarm_{i}" for i in range(11)]
    # 独立按每类告警时间二分检索，保持原先全局 (t,t+100] 定义。
    values = np.column_stack([
        np.searchsorted(starts[labels == k], starts + 100, side="right")
        > np.searchsorted(starts[labels == k], starts, side="right")
        for k in range(11)
    ]).astype(np.int8)
    frame = pd.DataFrame(values, columns=columns)
    assert frame.shape == (38830, 11), frame.shape
    metadata = {
        "summary": "全局每事件后100时间单位窗口内的告警是否出现",
        "observation_unit": "按开始时间稳定排序的一条原始事件",
        "transformations": ["全局 (t,t+100] 每类告警是否出现", "仅输出11个分析变量"],
        "analysis_columns": columns,
        "variable_semantics": {column: "窗口内是否出现对应类型告警" for column in columns},
        "measurement_columns": {column: None for column in raw.columns},
        "derived_measurement_semantics": [
            {"column": column, "kind": "binary", "evidence": "窗口内至少出现一次为1，否则为0"}
            for column in columns
        ],
    }
    csv_data = frame.to_csv(index=False)
    report = validate_candidate(
        data_bytes=csv_data.encode(), script_bytes=Path(__file__).read_bytes(),
        metadata=metadata, analysis_parameters={"effect_request": None, "measurement_semantics": [
            {"column": "alarm_id", "kind": "category", "evidence": "原始告警类型编号"}
        ]}, specifications=DEFAULT_ALGORITHM_SPECS, max_bytes=20 * 1024 * 1024,
    )
    assert report["accepted"], report["errors"]
    assert set(report["semantic_kinds"].values()) == {"binary"}
    (output / "prepared.csv").write_text(csv_data, encoding="utf-8")
    save(output / "plan.json", metadata)
    save(output / "validation.json", report)
    return frame, report


async def run_pc(output: Path, frame: pd.DataFrame, report: dict) -> None:
    config = McpServerConfig(
        service_token=secrets.token_hex(32), signing_key_current=secrets.token_hex(32),
        signing_key_previous=None, signing_key_id="acceptance", signing_key_previous_id="unused",
        process_workers=1, host="127.0.0.1",
    )
    # 本地冻结清单的真实授权记录，身份只在本次验收内有效。
    records = {}

    def authority(context):
        record = records[context.job_id]
        assert context == record["context"]
        data = record["path"].read_bytes()
        assert hashlib.sha256(data).hexdigest() == context.input_snapshot_digest
        kinds = semantic_kinds_from_validation_report(record["report"])
        return ValidatedAlgorithmInput(data.decode(), kinds)

    server = uvicorn.Server(uvicorn.Config(create_app(config, authority_reader=authority),
                            host="127.0.0.1", log_level="error", access_log=False))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("MCP server did not start")
            await asyncio.sleep(0.05)
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {config.service_token}"}) as http:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=600) as client:
                    await client.initialize()
                    continuous = pd.read_csv(ROOT / "tests/dataset/sachs.csv").iloc[:, :3]
                    assert all(pd.api.types.is_numeric_dtype(continuous[c]) for c in continuous)
                    mixed = continuous.copy()
                    first = mixed.columns[0]
                    mixed[first] = (mixed[first] > mixed[first].median()).astype(int)
                    scenarios = [
                        ("alarm_auto", frame, report["semantic_kinds"], "auto", "valid", "chisq"),
                        ("alarm_fisherz", frame, report["semantic_kinds"], "fisherz", "not_applicable", None),
                        ("continuous_auto", continuous, {c: "continuous" for c in continuous}, "auto", "valid", "fisherz"),
                        ("continuous_chisq", continuous, {c: "continuous" for c in continuous}, "chisq", "not_applicable", None),
                        ("mixed_auto", mixed, {c: ("binary" if c == first else "continuous") for c in mixed}, "auto", "not_applicable", None),
                        ("unknown_auto", continuous, {}, "auto", "not_applicable", None),
                    ]
                    for name, data, kinds, requested, status, selected in scenarios:
                        path = output / ("prepared.csv" if name == "alarm_auto" else f"{name}.csv")
                        if name != "alarm_auto":
                            path.write_text(data.to_csv(index=False), encoding="utf-8")
                        csv_bytes = path.read_bytes()
                        validation = validate_candidate(
                            data_bytes=csv_bytes, script_bytes=Path(__file__).read_bytes(),
                            metadata={"summary": "真实数据检验选择验收", "observation_unit": "一次观测",
                                "transformations": ["保留数据；mixed场景仅将首列按本数据中位数二值化"],
                                "analysis_columns": list(data.columns), "variable_semantics": {},},
                            analysis_parameters={"effect_request": None, "measurement_semantics": [
                                {"column": c, "kind": k, "evidence": "Alarm窗口定义或Sachs连续测量；混合场景按中位数二值化首列"}
                                for c, k in kinds.items()]},
                            specifications=DEFAULT_ALGORITHM_SPECS, max_bytes=20 * 1024 * 1024,
                        )
                        assert validation["accepted"], validation["errors"]
                        invocation = str(uuid4())
                        now = datetime.now(timezone.utc)
                        digest = hashlib.sha256(csv_bytes).hexdigest()
                        context = McpInvocationContext(
                            invocation_id=invocation, job_id=str(uuid4()), session_id=str(uuid4()),
                            user_id=1, attempt_count=1, lease_epoch=1, worker_id="local-acceptance",
                            input_snapshot_digest=digest, analysis_dataset_id=1,
                            issued_at=now, expires_at=now + timedelta(minutes=10), key_id=config.signing_key_id,
                        )
                        command = AlgorithmExecutionCommand(
                            invocation_id=invocation, capability_id=PC_SPEC.capability_id,
                            capability_version=PC_SPEC.version, spec_digest=PC_SPEC.spec_digest,
                            provider_call_id=name, input_identity=digest, parameters={"test": requested},
                        )
                        records[context.job_id] = {"context": context, "path": path, "report": validation}
                        started = time.perf_counter()
                        response = await client.call_tool("execute_algorithm", {
                            "command": command.model_dump(mode="json"),
                            "trusted_context": context.model_dump(mode="json"),
                            "signature": sign_invocation(context, command, config.signing_key_current),
                        })
                        payload = response.structured_content
                        assert payload and payload["ok"], response
                        result = payload["result"]
                        save(output / f"{name}.json", {"input_sha256": digest,
                             "seconds": time.perf_counter() - started, "response": payload})
                        # 真实 PC 可在有限样本上产生有向环；保留契约失败，禁止修图或伪装成功。
                        cycle_rejection = (name == "alarm_auto" and result["status"] == "execution_failed"
                            and result["graph_validation"]["violations"] == ["directed_cycle"])
                        assert result["status"] == status or cycle_rejection, result
                        assert result["diagnostics"]["metrics"]["independence_test"] == selected, result
                        if status == "valid":
                            assert result["standardized_graph"]["nodes"] == list(data.columns)
                        print(json.dumps({"scenario": name, "status": result["status"], "test": selected}), flush=True)
    finally:
        server.should_exit = True
        await task
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cdfm", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.ERROR)
    frame, report = prepare(args.output)
    asyncio.run(run_pc(args.output, frame, report))
    if args.cdfm:
        import torch
        from Agent.causal.cdfm_runner import run_cdfm_analysis

        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        torch.manual_seed(20261102)
        np.random.seed(20261102)
        path = args.output / "prepared.csv"
        data = path.read_bytes()
        print("CDFM full frozen input started", flush=True)
        start = time.perf_counter()
        result = run_cdfm_analysis(data.decode())
        save(args.output / "cdfm.json", {"input_sha256": hashlib.sha256(data).hexdigest(),
             "seconds": time.perf_counter() - start, "result": result})
        assert result["success"], result.get("message")
        assert [n["id"] for n in result["data"]["nodes"]] == list(frame.columns)
        print("CDFM complete", flush=True)


if __name__ == "__main__":
    main()
