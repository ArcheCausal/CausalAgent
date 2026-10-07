"""CDFM_MCP_ENGINEERING_SMOKE：独立的真实 CDFM causal-mcp 验收入口。

该入口在包含 CDFM 依赖的 causal-mcp 镜像内运行一个真实 Streamable HTTP
服务和真实 ProcessPool。authority 使用本地冻结 CSV，只证明工程链路，不证明
MySQL strong-read、因果发现准确率或生产图的环路修复能力。
"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from typing import Any

import psutil
import uvicorn

ROOT = Path("/app") if Path("/app/Agent").is_dir() else Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Agent.CausalAgentMCP.app import create_app
from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.CausalAgentMCP.runner_registry import RunnerRegistry, RunnerSpec
from Agent.deep_agent.context import TrustedJobIdentity
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent_tools.adapters import CDFMAdapter
from Agent.deep_agent_tools.adapters.base import AdapterInput
from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC
from Agent.deep_agent_tools.algorithm_executor import (
    AlgorithmExecutionResponse,
    validate_executor_result,
)
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    DataProfile,
    McpInvocationContext,
)
from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig


TOKEN = "cdfm-engineering-smoke-token"
SIGNING_KEY = "cdfm-engineering-smoke-signing-key"
VALID_PORT = 8794
CONTROL_PORT = 8795


def _fixture() -> str:
    rows = ["A,B,C"]
    for index in range(24):
        a = index / 23.0
        b = 0.8 * a + ((index * 7) % 11) / 11.0
        c = 0.5 * b + ((index * 5) % 13) / 13.0
        rows.append(f"{a:.8f},{b:.8f},{c:.8f}")
    return "\n".join(rows) + "\n"


def _runner_contract_smoke(csv_data: str) -> dict[str, Any]:
    from Agent.causal.cdfm_runner import run_cdfm_analysis

    missing_csv = csv_data.replace("0.04347826", "", 1)
    cases = {
        "threshold_auto": (csv_data, None),
        "missing_values": (missing_csv, 0.5),
        "categorical": ("A,B\na,1\nb,2\n", 0.5),
        "invalid_threshold": (csv_data, 1.5),
    }
    results: dict[str, Any] = {}
    for name, (case_csv, threshold) in cases.items():
        output = io.StringIO()
        started = time.perf_counter()
        try:
            with redirect_stdout(output), redirect_stderr(output):
                value = run_cdfm_analysis(case_csv, threshold=threshold)
            result: dict[str, Any] = {
                "success": value.get("success") is True,
                "error_type": value.get("error_type"),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
            if value.get("success") is True:
                raw_results = value.get("raw_results") or {}
                logits = raw_results.get("logits") or []
                result["matrix_shape"] = [len(logits), len(logits[0]) if logits else 0]
                result["threshold"] = raw_results.get("threshold")
                result["missing_values"] = (value.get("diagnostics") or {}).get(
                    "missing_values"
                )
            results[name] = result
        except Exception as exc:
            results[name] = {
                "success": False,
                "exception_type": type(exc).__name__,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }

    passed = (
        results["threshold_auto"].get("success") is True
        and results["threshold_auto"].get("matrix_shape") == [3, 3]
        and isinstance(results["threshold_auto"].get("threshold"), float)
        and results["missing_values"].get("success") is True
        and results["missing_values"].get("missing_values") == 1
        and results["categorical"].get("error_type") == "InputValidationError"
        and results["invalid_threshold"].get("error_type") == "InputValidationError"
    )
    return {"status": "PASS" if passed else "FAIL", "cases": results}


def _config() -> McpServerConfig:
    return McpServerConfig(
        service_token=TOKEN,
        signing_key_current=SIGNING_KEY,
        signing_key_previous=None,
        signing_key_id="current",
        signing_key_previous_id="previous",
        process_workers=1,
        queue_capacity=2,
        enqueue_timeout_seconds=1.0,
        algorithm_deadline_seconds=600,
        slow_log_seconds=60.0,
    )


async def _start_server(
    csv_data: str,
    *,
    port: int,
    registry: RunnerRegistry | None = None,
) -> tuple[uvicorn.Server, asyncio.Task[Any]]:
    app = create_app(
        _config(),
        registry=registry,
        authority_reader=lambda _context: csv_data,
        database_probe=lambda: True,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            return server, task
        await asyncio.sleep(0.05)
    raise RuntimeError("CDFM smoke MCP server did not start")


async def _stop_server(server: uvicorn.Server, task: asyncio.Task[Any]) -> None:
    server.should_exit = True
    await task


def _pool(port: int) -> McpClientPool:
    return McpClientPool(
        McpClientPoolConfig(
            url=f"http://127.0.0.1:{port}/mcp",
            service_token=TOKEN,
            pool_size=1,
            max_in_flight_per_client=1,
            acquire_timeout_seconds=2.0,
            control_pool_size=1,
            control_max_in_flight_per_client=2,
            control_acquire_timeout_seconds=1.0,
            max_connections=4,
            max_keepalive_connections=4,
            read_timeout_seconds=700.0,
        )
    )


class _RecordingPool:
    def __init__(self, pool: McpClientPool) -> None:
        self.pool = pool
        self.last_payload: dict[str, Any] | None = None

    async def call_tool(self, *args: Any, **kwargs: Any) -> Any:
        response = await self.pool.call_tool(*args, **kwargs)
        payload = getattr(response, "structured_content", None)
        self.last_payload = payload if isinstance(payload, dict) else None
        return response

    async def cancel_tool(self, *args: Any, **kwargs: Any) -> Any:
        return await self.pool.cancel_tool(*args, **kwargs)


class _SmokeExecutor:
    """只在 causal-mcp smoke 中复用真实 HTTP pool 和共享结果契约。"""

    def __init__(self, pool: _RecordingPool, signing_key: str) -> None:
        self.pool = pool
        self.signing_key = signing_key

    async def execute(self, command: AlgorithmExecutionCommand, context: McpInvocationContext):
        response = await self.pool.call_tool(
            {
                "command": command.model_dump(mode="json"),
                "trusted_context": context.model_dump(mode="json"),
                "signature": sign_invocation(context, command, self.signing_key),
            },
            invocation_id=command.invocation_id,
        )
        payload = getattr(response, "structured_content", None)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("CDFM smoke MCP response was not successful")
        result = validate_executor_result(
            AlgorithmResult.model_validate(payload.get("result")),
            command=command,
            trusted_context=context,
        )
        raw_payload = payload.get("raw_payload")
        if not isinstance(raw_payload, dict):
            raise RuntimeError("CDFM smoke private raw payload is missing")
        return AlgorithmExecutionResponse(result=result, raw_payload=raw_payload)


def _backend_json(backend: Any, path: str) -> dict[str, Any]:
    stored = backend.read(path)
    if isinstance(stored, (bytes, bytearray, memoryview)):
        content = bytes(stored)
    else:
        file_data = getattr(stored, "file_data", None)
        if isinstance(stored, dict):
            file_data = stored.get("file_data")
        if not isinstance(file_data, dict) or not isinstance(file_data.get("content"), str):
            raise AssertionError("raw artifact cannot be read")
        content = file_data["content"].encode("utf-8")
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("raw artifact is not an object")
    return value


async def _valid_cdfm_call(csv_data: str) -> dict[str, Any]:
    server, server_task = await _start_server(csv_data, port=VALID_PORT)
    pool = _pool(VALID_PORT)
    await pool.start()
    recording_pool = _RecordingPool(pool)
    backend = build_in_memory_backend(user_id=7)
    started = time.perf_counter()
    try:
        identity_digest = hashlib.sha256(csv_data.encode("utf-8")).hexdigest()
        identity = TrustedJobIdentity(
            job_id="00000000-0000-0000-0000-000000000901",
            session_id="00000000-0000-0000-0000-000000000902",
            user_id=7,
            attempt_count=1,
            lease_epoch=1,
            worker_id="cdfm-smoke-worker",
            input_identity=identity_digest,
            input_snapshot_digest=identity_digest,
        )
        adapter = CDFMAdapter(
            executor=_SmokeExecutor(recording_pool, SIGNING_KEY),
            raw_backend=backend,
        )
        result = await adapter.run(
            parameters={"threshold": 0.5},
            adapter_input=AdapterInput(
                data_profile=DataProfile(
                    row_count=24,
                    column_count=3,
                    column_names=("A", "B", "C"),
                    numeric_columns=("A", "B", "C"),
                ),
                input_identity=identity_digest,
                dataset_csv=csv_data,
                dataset_authority_available=True,
            ),
            trusted_context=identity,
            provider_call_id="cdfm-engineering-call",
            response_identity="cdfm-engineering-response",
            raw_backend=backend,
        )
        if result.status != "valid" or result.standardized_graph is None:
            raise AssertionError(f"CDFM result status was {result.status}")
        if result.standardized_graph.graph_semantics != "directed_graph":
            raise AssertionError("CDFM graph semantics were not directed_graph")
        if any(edge.weight is not None for edge in result.standardized_graph.edges):
            raise AssertionError("CDFM edges unexpectedly exposed weights")
        if result.raw_result_ref is None:
            raise AssertionError("CDFM raw artifact reference is missing")

        raw_artifact = _backend_json(backend, result.raw_result_ref)
        raw_results = raw_artifact.get("raw_results")
        if not isinstance(raw_results, dict) or not {
            "logits",
            "probabilities",
            "threshold",
        }.issubset(raw_results):
            raise AssertionError("CDFM raw artifact is missing private fields")
        private_payload = recording_pool.last_payload or {}
        if not isinstance(private_payload.get("raw_payload"), dict):
            raise AssertionError("MCP private raw envelope is missing")
        if "raw_results" in (private_payload.get("result") or {}):
            raise AssertionError("raw results crossed into AlgorithmResult")
        public_result = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        if any(field in public_result for field in ("logits", "probabilities", "raw_results")):
            raise AssertionError("raw CDFM fields crossed the public result boundary")
        trace = private_payload.get("trace") or {}
        return {
            "status": "PASS",
            "graph_semantics": result.standardized_graph.graph_semantics,
            "edge_count": len(result.standardized_graph.edges),
            "raw_fields": sorted(raw_results),
            "raw_artifact_size_bytes": result.raw_result_size_bytes,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "runner_runtime_seconds": (raw_artifact.get("diagnostics") or {}).get(
                "runtime_sec"
            ),
            "peak_rss_bytes": trace.get("peak_rss_bytes"),
            "cpu_seconds": trace.get("cpu_seconds"),
            "public_raw_fields": False,
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "exception_type": type(exc).__name__,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        await pool.close()
        await _stop_server(server, server_task)


def _slow_cdfm_runner(_csv_data: str, _parameters: dict[str, Any]) -> dict[str, Any]:
    time.sleep(2.0)
    return {"success": True, "data": {"nodes": [], "edges": []}}


def _control_request(csv_data: str) -> tuple[dict[str, Any], str]:
    now = datetime.now(timezone.utc)
    digest = hashlib.sha256(csv_data.encode("utf-8")).hexdigest()
    context = McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000911",
        job_id="00000000-0000-0000-0000-000000000912",
        session_id="00000000-0000-0000-0000-000000000913",
        user_id=7,
        attempt_count=1,
        lease_epoch=1,
        worker_id="cdfm-control-worker",
        input_snapshot_digest=digest,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        key_id="current",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=context.invocation_id,
        capability_id=CDFM_SPEC.capability_id,
        capability_version=CDFM_SPEC.version,
        spec_digest=CDFM_SPEC.spec_digest,
        provider_call_id="cdfm-control-call",
        input_identity=digest,
        parameters={"threshold": 0.5},
        timeout_seconds=30,
    )
    arguments = {
        "command": command.model_dump(mode="json"),
        "trusted_context": context.model_dump(mode="json"),
        "signature": sign_invocation(context, command, SIGNING_KEY),
    }
    return arguments, command.invocation_id


async def _cancel_control(csv_data: str) -> dict[str, Any]:
    registry = RunnerRegistry(
        [
            RunnerSpec(
                capability_id=CDFM_SPEC.capability_id,
                version=CDFM_SPEC.version,
                spec_digest=CDFM_SPEC.spec_digest,
                timeout_seconds=600,
                concurrency=1,
                runner=_slow_cdfm_runner,
            )
        ]
    )
    server, server_task = await _start_server(
        csv_data,
        port=CONTROL_PORT,
        registry=registry,
    )
    pool = _pool(CONTROL_PORT)
    await pool.start()
    try:
        arguments, invocation_id = _control_request(csv_data)
        execution_task = asyncio.create_task(
            pool.call_tool(arguments, invocation_id=invocation_id)
        )
        await asyncio.sleep(0.2)
        cancel_response = await pool.cancel_tool(arguments, invocation_id=invocation_id)
        execute_response = await execution_task
        cancel_payload = getattr(cancel_response, "structured_content", None) or {}
        execute_payload = getattr(execute_response, "structured_content", None) or {}
        cancel_status = (cancel_payload.get("cancellation") or {}).get("status")
        execute_code = (execute_payload.get("error") or {}).get("code")
        return {
            "status": "PASS"
            if cancel_status in {"canceled", "cancel_pending"}
            and execute_code == "ALGORITHM_CANCELED"
            else "FAIL",
            "cancel_status": cancel_status,
            "execute_error_code": execute_code,
        }
    except Exception as exc:
        return {"status": "ERROR", "exception_type": type(exc).__name__}
    finally:
        await pool.close()
        await _stop_server(server, server_task)
        await asyncio.sleep(0.1)


async def main() -> None:
    csv_data = _fixture()
    runner_smoke = await asyncio.to_thread(_runner_contract_smoke, csv_data)
    valid = await _valid_cdfm_call(csv_data)
    control = await _cancel_control(csv_data)
    children = psutil.Process().children(recursive=True)
    report = {
        "run_id": "CDFM_MCP_ENGINEERING_SMOKE",
        "status": "PASS"
        if runner_smoke["status"] == "PASS"
        and valid["status"] == "PASS"
        and control["status"] == "PASS"
        and not children
        else "FAIL",
        "authority": "in-process frozen CSV; not MySQL strong-read evidence",
        "runner_contract": runner_smoke,
        "valid_call": valid,
        "cancel_control": control,
        "child_processes_after_shutdown": len(children),
        "capability_claim": "engineering smoke only; no accuracy or production capability conclusion",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
