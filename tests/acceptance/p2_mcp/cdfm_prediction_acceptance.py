"""真实 CDFM 推理及报告投影验收，可选正式 MCP HTTP 传输。

在含真实 CDFM 依赖和权重的容器中显式运行，传入 --dataset 和 --output-dir。
输入为现存 CSV，不替换 runner；完整原始结果只写入本地验收目录。
MCP 模式使用本地冻结 CSV authority，不证明 MySQL 授权、租约或完整模型会话。
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
from secrets import token_hex
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from Agent.causal.cdfm_runner import run_cdfm_analysis
from Agent.causal_agent.analysis_context import normalize_algorithm_summary
from Agent.Report.assets import build_causal_graph_model
from Agent.Report.document import CausalGraphModel
from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, AlgorithmResult, McpInvocationContext
from Agent.deep_agent_tools.normalization import result_from_runner_payload
from Agent.deep_agent_tools.mcp_algorithm_executor import McpAlgorithmExecutor
from Agent.CausalAgentMCP.app import create_app
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.measurement_semantics import ValidatedAlgorithmInput
from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig

import uvicorn


async def execute_over_http(csv_data, command, context):
    """启动正式服务、进程池和客户端；本地文件内容充当明确的输入 authority。"""
    token, signing_key = token_hex(32), token_hex(32)
    frozen = ValidatedAlgorithmInput(csv_data=csv_data, semantic_kinds={})

    def read_frozen_input(actual):
        assert actual == context
        assert hashlib.sha256(csv_data.encode()).hexdigest() == context.input_snapshot_digest
        return frozen

    app = create_app(
        McpServerConfig(service_token=token, signing_key_current=signing_key,
            signing_key_previous=None, signing_key_id="current",
            signing_key_previous_id="previous", process_workers=1, algorithm_deadline_seconds=600),
        authority_reader=read_frozen_input,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8796, log_level="critical"))
    task = asyncio.create_task(server.serve())
    pool = McpClientPool(McpClientPoolConfig(
        url="http://127.0.0.1:8796/mcp", service_token=token,
        pool_size=1, control_pool_size=1, read_timeout_seconds=700,
    ))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
                raise RuntimeError("MCP HTTP server stopped before startup")
            await asyncio.sleep(0.05)
        assert server.started
        await pool.start()
        response = await McpAlgorithmExecutor(pool, signing_key=signing_key, max_retries=0).execute(command, context)
        assert response.raw_payload is not None
        return response.result, response.raw_payload
    finally:
        await pool.close()
        server.should_exit = True
        await task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--transport", choices=["local", "mcp"], default="local")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_data = args.dataset.read_text(encoding="utf-8-sig")
    digest = hashlib.sha256(csv_data.encode()).hexdigest()
    invocation = str(uuid4())
    now = datetime.now(timezone.utc)
    context = McpInvocationContext(
        invocation_id=invocation, job_id=str(uuid4()), session_id=str(uuid4()),
        user_id=1, attempt_count=1, lease_epoch=1, worker_id="local-prediction-acceptance",
        input_snapshot_digest=digest, issued_at=now, expires_at=now + timedelta(minutes=15), key_id="current",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=invocation, capability_id=CDFM_SPEC.capability_id,
        capability_version=CDFM_SPEC.version, spec_digest=CDFM_SPEC.spec_digest,
        provider_call_id="local-prediction-acceptance", input_identity=digest,
    )
    # 第三方推理日志可能包含底层自由文本，仅在容器内捕获。
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        if args.transport == "mcp":
            result, raw = asyncio.run(execute_over_http(csv_data, command, context))
        else:
            raw = run_cdfm_analysis(csv_data)
            assert raw.get("success") is True, raw.get("error_type")
            result = result_from_runner_payload(raw, command=command, context=context, capability_version=CDFM_SPEC.version)
    assert result.standardized_graph is not None
    matrix = raw["raw_results"]
    positions = {name: index for index, name in enumerate(matrix["variable_order"])}
    for edge in result.standardized_graph.edges:
        source, target = positions[edge.source], positions[edge.target]
        assert edge.weight is None and edge.prediction is not None
        assert edge.prediction.forward_probability == matrix["probabilities"][source][target]
        assert edge.prediction.reverse_probability == matrix["probabilities"][target][source]
    restored_result = AlgorithmResult.model_validate_json(result.model_dump_json())
    facts = normalize_algorithm_summary(restored_result)
    assert facts["diagnostics"]["metrics"]["threshold"] == matrix["threshold"]
    asset = build_causal_graph_model(
        dict(facts["graph"], diagnostics=facts["diagnostics"]),
        graph_id="cdfm_prediction_acceptance", algorithm=CDFM_SPEC.capability_id,
    )
    restored_asset = CausalGraphModel.model_validate_json(asset.model_dump_json())
    assert [edge.prediction for edge in restored_asset.edges] == [edge.prediction for edge in result.standardized_graph.edges]
    public = json.dumps([result.model_dump(mode="json"), facts, restored_asset.model_dump(mode="json")])
    assert all(f'"{field}"' not in public for field in ("logits", "probabilities", "raw_results"))
    for name, value in (("raw.json", raw), ("result.json", result.model_dump(mode="json")),
                        ("facts.json", facts), ("graph.json", asset.model_dump(mode="json"))):
        (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "status": "PASS", "scope": f"real_{args.transport}_cdfm_and_projection_json_roundtrip",
        "algorithm_result_status": result.status,
        "graph_violations": result.graph_validation.violations,
        "edge_count": len(asset.edges), "bidirected_count": sum(edge.edge_type == "bidirected" for edge in asset.edges),
        "probabilities_match_raw": True, "threshold_matches_raw": True,
        "public_matrices_absent": True, "json_roundtrip": True,
        "contract_version": CDFM_SPEC.version,
        "limitations": ["No database persistence or model-generated report"],
    }
    (args.output_dir / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
