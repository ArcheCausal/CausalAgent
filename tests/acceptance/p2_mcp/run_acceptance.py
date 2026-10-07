"""P2-M bounded real acceptance runner.

This runner is intentionally outside pytest collection.  It uses the real
runner registry, real MCP 2.2 HTTP server/client pool, and a real process pool.
The local-file authority cases are reported separately from MySQL authority
acceptance; they never claim strong-read or lease-fencing evidence.
"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import importlib.metadata
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import sys
import time
from typing import Any

import psutil
import resource
import uvicorn

ROOT = Path("/app") if Path("/app/Agent").is_dir() else Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.CausalAgentMCP.executor_pool import (
    BoundedProcessPool,
    PoolExecutionError,
)
from Agent.CausalAgentMCP.runner_registry import (
    RunnerRegistry,
    RunnerSpec,
    build_default_registry,
)
from Agent.CausalAgentMCP.service import CausalMcpService
from Agent.CausalAgentMCP.app import create_app
from Agent.deep_agent_tools.algorithm_specs import (
    CDFM_SPEC,
    DIRECT_LINGAM_SPEC,
    OLC_SPEC,
    PC_SPEC,
)
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, McpInvocationContext
from Agent.CausalAgentMCP.auth import sign_invocation
from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig


TOKEN = "p2-acceptance-token"
SIGNING_KEY = "p2-acceptance-signing-key"
PORT = 8793


def _sleep_runner(_csv_data: str, parameters: dict[str, Any]) -> dict[str, Any]:
    time.sleep(float(parameters["seconds"]))
    return {"success": True, "data": {"nodes": [], "edges": []}}


def _quick_runner(_csv_data: str, _parameters: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": {"nodes": [], "edges": []}}


def _slow_pc_runner(_csv_data: str, _parameters: dict[str, Any]) -> dict[str, Any]:
    time.sleep(2.0)
    return {"success": True, "data": {"nodes": [], "edges": []}}


def _fixture() -> str:
    fixture_root = Path("/tests") if Path("/tests").is_dir() else ROOT / "tests"
    return (fixture_root / "sachs.csv").read_text(encoding="utf-8")


def _resource_fixture() -> str:
    """Small deterministic continuous fixture for bounded resource observation."""

    rows = ["A,B,C"]
    for index in range(120):
        a = (index % 17) / 17.0
        b = 0.8 * a + ((index * 7) % 13) / 13.0
        c = 0.4 * b + ((index * 11) % 19) / 19.0
        rows.append(f"{a:.8f},{b:.8f},{c:.8f}")
    return "\n".join(rows) + "\n"


def _config(**overrides: Any) -> McpServerConfig:
    values: dict[str, Any] = {
        "service_token": TOKEN,
        "signing_key_current": SIGNING_KEY,
        "signing_key_previous": None,
        "signing_key_id": "current",
        "signing_key_previous_id": "previous",
        "process_workers": 1,
        "queue_capacity": 4,
        "enqueue_timeout_seconds": 0.2,
        "algorithm_deadline_seconds": 30,
    }
    values.update(overrides)
    return McpServerConfig(**values)


def _request() -> tuple[AlgorithmExecutionCommand, McpInvocationContext, str]:
    now = datetime.now(timezone.utc)
    context = McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000301",
        job_id="00000000-0000-0000-0000-000000000302",
        session_id="00000000-0000-0000-0000-000000000303",
        user_id=7,
        attempt_count=1,
        lease_epoch=1,
        worker_id="p2-acceptance-worker",
        input_snapshot_digest=hashlib.sha256(_fixture().encode()).hexdigest(),
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        key_id="current",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=context.invocation_id,
        capability_id=PC_SPEC.capability_id,
        capability_version=PC_SPEC.version,
        spec_digest=PC_SPEC.spec_digest,
        provider_call_id="p2-acceptance-call",
        input_identity=context.input_snapshot_digest,
        parameters={"alpha": 0.05},
        timeout_seconds=30,
    )
    return command, context, sign_invocation(context, command, SIGNING_KEY)


def _request_for(index: int) -> tuple[AlgorithmExecutionCommand, McpInvocationContext, str]:
    """Build a distinct signed invocation while preserving one worker identity."""

    command, context, _signature = _request()
    invocation_id = f"00000000-0000-0000-0000-{index:012d}"
    context = context.model_copy(update={"invocation_id": invocation_id})
    command = command.model_copy(
        update={
            "invocation_id": invocation_id,
            "provider_call_id": f"p2-acceptance-call-{index}",
        }
    )
    return command, context, sign_invocation(context, command, SIGNING_KEY)


def _arguments_for(index: int) -> tuple[dict[str, Any], AlgorithmExecutionCommand]:
    command, context, signature = _request_for(index)
    return (
        {
            "command": command.model_dump(mode="json"),
            "trusted_context": context.model_dump(mode="json"),
            "signature": signature,
        },
        command,
    )


def _safe_algorithm_call(fn: Any, csv_data: str) -> dict[str, Any]:
    output = io.StringIO()
    started = time.perf_counter()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            value = fn(csv_data)
        result = {
            "status": "PASS" if value.get("success") is True else "FAIL",
            "success": value.get("success"),
            "error_type": value.get("error_type"),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "captured_output_bytes": len(output.getvalue().encode()),
        }
        if value.get("success") is True:
            result["node_count"] = len((value.get("data") or {}).get("nodes", []))
            result["edge_count"] = len((value.get("data") or {}).get("edges", []))
        return result
    except Exception as exc:  # evidence must retain unexpected runner failures
        return {
            "status": "ERROR",
            "exception_type": type(exc).__name__,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "captured_output_bytes": len(output.getvalue().encode()),
        }


def real_algorithm_calls(csv_data: str) -> dict[str, Any]:
    from Agent.causal.causalachieve import (
        run_direct_lingam_analysis,
        run_olc_analysis,
        run_pc_analysis,
    )

    olc_data = _resource_fixture()
    results = {
        "causal.pc": _safe_algorithm_call(run_pc_analysis, csv_data),
        "causal.olc": _safe_algorithm_call(run_olc_analysis, olc_data),
        "causal.direct_lingam": _safe_algorithm_call(run_direct_lingam_analysis, csv_data),
    }
    results["causal.pc"]["fixture"] = "tests/sachs.csv"
    results["causal.olc"]["fixture"] = "small-continuous-3-column"
    results["causal.direct_lingam"]["fixture"] = "tests/sachs.csv"
    if results["causal.olc"].get("error_type") is None and results["causal.olc"].get("success") is False:
        results["causal.olc"]["status"] = "NOT_RUN"
        results["causal.olc"]["reason"] = "runner reported unavailable dependency or input"
    return {
        "status": "PARTIAL" if results["causal.olc"]["status"] == "NOT_RUN" else "PASS",
        "authority": "local fixture file; not MySQL strong-read evidence",
        "fixture": "tests/sachs.csv",
        "algorithms": results,
    }


async def capacity_window() -> dict[str, Any]:
    pool = BoundedProcessPool(
        process_workers=2,
        queue_capacity=4,
        enqueue_timeout_seconds=0.15,
        timeout_recycle_threshold=10,
        executor_factory=ProcessPoolExecutor,
    )
    await pool.start()
    try:
        started = [
            asyncio.create_task(
                pool.execute(
                    capability_id=f"capacity.{index}",
                    runner=_sleep_runner,
                    csv_data="A\n1\n",
                    parameters={"seconds": 0.8},
                    timeout_seconds=3,
                    concurrency=1,
                )
            )
            for index in range(7)
        ]
        outcomes = await asyncio.gather(*started, return_exceptions=True)
        rejected = sum(
            isinstance(value, PoolExecutionError)
            and value.safe_error_code.value == "MCP_CAPACITY_EXHAUSTED"
            for value in outcomes
        )
        return {
            "status": "PASS" if rejected == 1 else "FAIL",
            "expected": "2 running + 4 queued; seventh rejected",
            "capacity_rejected": rejected,
            "completed": len(outcomes) - rejected,
            "scope": "real bounded process pool with controlled sleep runner; not causal algorithm throughput",
        }
    finally:
        await pool.close()


async def timeout_and_late_result() -> dict[str, Any]:
    pool = BoundedProcessPool(
        process_workers=1,
        queue_capacity=0,
        enqueue_timeout_seconds=0.2,
        timeout_recycle_threshold=10,
        executor_factory=ProcessPoolExecutor,
    )
    await pool.start()
    try:
        started = time.perf_counter()
        try:
            await pool.execute(
                capability_id="deadline.test",
                runner=_sleep_runner,
                csv_data="A\n1\n",
                parameters={"seconds": 0.4},
                timeout_seconds=0.05,
                concurrency=1,
            )
        except PoolExecutionError as exc:
            timeout_code = exc.safe_error_code.value
        else:
            timeout_code = "NO_ERROR"
        await asyncio.sleep(0.5)
        follow_up = await pool.execute(
            capability_id="deadline.test",
            runner=_quick_runner,
            csv_data="A\n1\n",
            parameters={},
            timeout_seconds=1,
            concurrency=1,
        )
        return {
            "status": "PASS" if timeout_code == "ALGORITHM_TIMED_OUT" and follow_up.value["success"] else "FAIL",
            "timeout_code": timeout_code,
            "late_result_wait_seconds": round(time.perf_counter() - started, 3),
            "follow_up_completed": follow_up.value.get("success") is True,
        }
    finally:
        await pool.close()


async def resource_sample(csv_data: str) -> dict[str, Any]:
    registry = build_default_registry()
    samples: dict[str, dict[str, Any]] = {}
    skipped: dict[str, dict[str, str]] = {}
    resource_inputs = {
        "causal.pc": csv_data,
        "causal.olc": _resource_fixture(),
        "causal.direct_lingam": csv_data,
    }
    for entry in registry._entries.values():  # fixed registry, only for acceptance observation
        if entry.capability_id == CDFM_SPEC.capability_id:
            skipped[entry.capability_id] = {
                "status": "NOT_RUN",
                "reason": "CDFM requires the explicit cdfm_acceptance.py smoke entry",
            }
            continue
        pool = BoundedProcessPool(
            process_workers=1,
            queue_capacity=1,
            enqueue_timeout_seconds=1,
            timeout_recycle_threshold=10,
            executor_factory=ProcessPoolExecutor,
        )
        await pool.start()
        try:
            usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            task = asyncio.create_task(
                pool.execute(
                    capability_id=entry.capability_id,
                    runner=entry.runner,
                    csv_data=resource_inputs[entry.capability_id],
                    parameters={},
                    timeout_seconds=30,
                    concurrency=1,
                )
            )
            peak_rss = 0
            peak_cpu = 0.0
            while not task.done():
                children = psutil.Process().children(recursive=True)
                rss = 0
                cpu = 0.0
                for child in children:
                    try:
                        info = child.memory_info()
                        times = child.cpu_times()
                        rss += info.rss
                        cpu += times.user + times.system
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                peak_rss = max(peak_rss, rss)
                peak_cpu = max(peak_cpu, cpu)
                await asyncio.sleep(0.05)
            try:
                outcome = await task
                status = "PASS" if outcome.value.get("success") is True else "NOT_RUN"
            except PoolExecutionError as exc:
                status = "NOT_RUN"
                outcome = None
                error_code = exc.safe_error_code.value
            else:
                error_code = None
            if outcome is not None:
                peak_rss = max(peak_rss, outcome.peak_rss_bytes)
                peak_cpu = max(peak_cpu, outcome.cpu_seconds)
            usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
            # Linux ru_maxrss is KiB; this covers short-lived children that
            # finish before the psutil sampling interval.
            peak_rss = max(peak_rss, int(usage_after.ru_maxrss) * 1024)
            peak_cpu = max(
                peak_cpu,
                (usage_after.ru_utime + usage_after.ru_stime)
                - (usage_before.ru_utime + usage_before.ru_stime),
            )
            samples[entry.capability_id] = {
                "status": status,
                "error_code": error_code,
                "peak_child_rss_bytes": peak_rss,
                "peak_child_cpu_seconds_observed": round(peak_cpu, 3),
                "runner_version": "causalachieve-v1",
            }
        finally:
            await pool.close()
    return {
        "status": "PARTIAL" if any(v["status"] == "NOT_RUN" for v in samples.values()) else "PASS",
        "algorithms": samples,
        "skipped": skipped,
    }


async def start_server(
    csv_data: str,
    *,
    registry: RunnerRegistry | None = None,
    process_workers: int = 1,
) -> tuple[uvicorn.Server, asyncio.Task[Any]]:
    app = create_app(
        _config(process_workers=process_workers, queue_capacity=4),
        registry=registry,
        authority_reader=lambda _context: csv_data,
        database_probe=lambda: True,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            return server, task
        await asyncio.sleep(0.05)
    raise RuntimeError("local MCP server did not start")


async def remote_cancel_control(csv_data: str) -> dict[str, Any]:
    """通过真实 HTTP 控制面终止目标进程，并验证幂等取消结果。"""

    command, context, signature = _request()
    args = {
        "command": command.model_dump(mode="json"),
        "trusted_context": context.model_dump(mode="json"),
        "signature": signature,
    }
    registry = RunnerRegistry(
        [
            RunnerSpec(
                capability_id=PC_SPEC.capability_id,
                version=PC_SPEC.version,
                spec_digest=PC_SPEC.spec_digest,
                timeout_seconds=30,
                concurrency=1,
                runner=_slow_pc_runner,
            )
        ]
    )
    server, server_task = await start_server(csv_data, registry=registry)
    pool = McpClientPool(
        McpClientPoolConfig(
            url=f"http://127.0.0.1:{PORT}/mcp",
            service_token=TOKEN,
            pool_size=2,
            max_in_flight_per_client=1,
            acquire_timeout_seconds=1,
            max_connections=4,
            max_keepalive_connections=4,
            read_timeout_seconds=30,
        )
    )
    try:
        await pool.start()
        execute_task = asyncio.create_task(
            pool.call_tool(args, invocation_id=context.invocation_id)
        )
        await asyncio.sleep(0.2)
        cancel_response = await pool.cancel_tool(
            args,
            invocation_id=context.invocation_id,
        )
        execute_response = await execute_task
        cancel_payload = getattr(cancel_response, "structured_content", None) or {}
        execute_payload = getattr(execute_response, "structured_content", None) or {}
        repeated_response = await pool.cancel_tool(
            args,
            invocation_id=context.invocation_id,
        )
        repeated_payload = getattr(repeated_response, "structured_content", None) or {}
        cancel_status = (cancel_payload.get("cancellation") or {}).get("status")
        repeated_status = (repeated_payload.get("cancellation") or {}).get("status")
        execute_code = (execute_payload.get("error") or {}).get("code")
        passed = (
            cancel_status == "canceled"
            and repeated_status == "already_canceled"
            and execute_code == "ALGORITHM_CANCELED"
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "cancel_status": cancel_status,
            "repeated_cancel_status": repeated_status,
            "execute_error_code": execute_code,
            "scope": "real MCP HTTP control tool and process termination; synthetic slow runner",
        }
    finally:
        await pool.close()
        server.should_exit = True
        await server_task


async def saturated_execute_lane_control_acceptance(csv_data: str) -> dict[str, Any]:
    """Prove real HTTP cancellation bypasses a saturated execute lane."""

    registry = RunnerRegistry(
        [
            RunnerSpec(
                capability_id=PC_SPEC.capability_id,
                version=PC_SPEC.version,
                spec_digest=PC_SPEC.spec_digest,
                timeout_seconds=30,
                concurrency=2,
                runner=_slow_pc_runner,
            )
        ]
    )
    server, server_task = await start_server(
        csv_data, registry=registry, process_workers=2
    )
    pool = McpClientPool(
        McpClientPoolConfig(
            url=f"http://127.0.0.1:{PORT}/mcp",
            service_token=TOKEN,
            pool_size=2,
            max_in_flight_per_client=1,
            acquire_timeout_seconds=5,
            control_pool_size=1,
            control_max_in_flight_per_client=2,
            control_acquire_timeout_seconds=1,
            max_connections=4,
            max_keepalive_connections=4,
            read_timeout_seconds=30,
        )
    )
    try:
        await pool.start()
        args_a, command_a = _arguments_for(401)
        args_b, command_b = _arguments_for(402)
        args_c, command_c = _arguments_for(403)
        call_a = asyncio.create_task(
            pool.call_tool(args_a, invocation_id=command_a.invocation_id)
        )
        call_b = asyncio.create_task(
            pool.call_tool(args_b, invocation_id=command_b.invocation_id)
        )
        await asyncio.sleep(0.2)
        call_c = asyncio.create_task(
            pool.call_tool(args_c, invocation_id=command_c.invocation_id)
        )
        await asyncio.sleep(0.05)
        c_waited_for_execute = not call_c.done()

        cancel_started = time.perf_counter()
        cancel_a = await pool.cancel_tool(
            args_a, invocation_id=command_a.invocation_id
        )
        cancel_elapsed = time.perf_counter() - cancel_started
        cancel_a_status = (
            getattr(cancel_a, "structured_content", None) or {}
        ).get("cancellation", {}).get("status")
        a_payload, b_payload, c_payload = [
            getattr(value, "structured_content", None) or {}
            for value in await asyncio.gather(call_a, call_b, call_c)
        ]
        repeated = await pool.cancel_tool(
            args_a, invocation_id=command_a.invocation_id
        )
        repeated_status = (
            getattr(repeated, "structured_content", None) or {}
        ).get("cancellation", {}).get("status")

        args_d, command_d = _arguments_for(404)
        args_e, command_e = _arguments_for(405)
        call_d = asyncio.create_task(
            pool.call_tool(args_d, invocation_id=command_d.invocation_id)
        )
        call_e = asyncio.create_task(
            pool.call_tool(args_e, invocation_id=command_e.invocation_id)
        )
        await asyncio.sleep(0.2)
        cancel_d, cancel_e = await asyncio.gather(
            pool.cancel_tool(args_d, invocation_id=command_d.invocation_id),
            pool.cancel_tool(args_e, invocation_id=command_e.invocation_id),
        )
        simultaneous_statuses = [
            (getattr(value, "structured_content", None) or {})
            .get("cancellation", {})
            .get("status")
            for value in (cancel_d, cancel_e)
        ]
        d_payload, e_payload = [
            getattr(value, "structured_content", None) or {}
            for value in await asyncio.gather(call_d, call_e)
        ]

        execute_errors = [
            payload.get("error", {}).get("code")
            for payload in (a_payload, d_payload, e_payload)
        ]
        sibling_ok = b_payload.get("ok") is True
        queued_ok = c_payload.get("ok") is True
        passed = (
            c_waited_for_execute
            and cancel_a_status in {"canceled", "cancel_pending"}
            and cancel_elapsed < 1.5
            and repeated_status == "already_canceled"
            and sibling_ok
            and queued_ok
            and all(status in {"canceled", "cancel_pending"} for status in simultaneous_statuses)
            and all(code == "ALGORITHM_CANCELED" for code in execute_errors)
            and all(member.inflight == 0 for member in pool.members)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "c_waited_for_execute_lane": c_waited_for_execute,
            "cancel_a_status": cancel_a_status,
            "cancel_a_elapsed_seconds": round(cancel_elapsed, 3),
            "repeated_cancel_status": repeated_status,
            "sibling_b_completed": sibling_ok,
            "queued_c_completed": queued_ok,
            "simultaneous_cancel_statuses": simultaneous_statuses,
            "execute_cancel_error_codes": execute_errors,
            "members_drained_before_close": all(
                member.inflight == 0 for member in pool.members
            ),
            "scope": "real MCP HTTP execute/control lanes and one control member with two in-flight requests",
        }
    finally:
        await pool.close()
        server.should_exit = True
        await server_task


async def client_pool_modes_and_fault(csv_data: str) -> dict[str, Any]:
    command, context, signature = _request()
    args = {
        "command": command.model_dump(mode="json"),
        "trusted_context": context.model_dump(mode="json"),
        "signature": signature,
    }
    server, server_task = await start_server(csv_data)
    result: dict[str, Any] = {}
    try:
        for label, max_in_flight, expected_capacity in (("A_Nx1", 1, 2), ("B_NxK", 2, 4)):
            pool = McpClientPool(
                McpClientPoolConfig(
                    url=f"http://127.0.0.1:{PORT}/mcp",
                    service_token=TOKEN,
                    pool_size=2,
                    max_in_flight_per_client=max_in_flight,
                    acquire_timeout_seconds=0.2,
                    control_pool_size=1,
                    control_max_in_flight_per_client=expected_capacity,
                    max_connections=8,
                    max_keepalive_connections=8,
                    read_timeout_seconds=30,
                )
            )
            await pool.start()
            leases = [await pool.acquire() for _ in range(expected_capacity)]
            try:
                try:
                    await pool.acquire()
                except Exception as exc:
                    capacity_status = type(exc).__name__
                else:
                    capacity_status = "NO_ERROR"
            finally:
                await asyncio.gather(*(lease.release() for lease in leases))
            successful_call = await pool.call_tool(args, invocation_id=context.invocation_id)
            healthy_before = [member.healthy for member in pool.members]
            generations_before = [member.generation for member in pool.members]
            await pool.close()
            result[label] = {
                "status": "PASS" if capacity_status == "McpPoolError" and successful_call is not None else "FAIL",
                "capacity_slots": expected_capacity,
                "capacity_error": capacity_status,
                "healthy_before_close": healthy_before,
                "generation_before_fault": generations_before,
                "drained_members_after_close": len(pool.members) == 0,
            }

        fault_pool = McpClientPool(
            McpClientPoolConfig(
                url=f"http://127.0.0.1:{PORT}/mcp",
                service_token=TOKEN,
                pool_size=2,
                max_in_flight_per_client=1,
                acquire_timeout_seconds=0.2,
                max_connections=4,
                max_keepalive_connections=4,
                read_timeout_seconds=30,
            )
        )
        await fault_pool.start()
        member_id = fault_pool.members[0].member_id
        old_generation = fault_pool.members[0].generation
        server.should_exit = True
        await server_task
        try:
            await fault_pool.call_tool(args, invocation_id=context.invocation_id)
        except Exception as exc:
            transport_error = type(exc).__name__
        else:
            transport_error = "NO_ERROR"
        server, server_task = await start_server(csv_data)
        await fault_pool.ensure_reconnected(member_id, old_generation)
        new_generation = next(member.generation for member in fault_pool.members if member.member_id == member_id)
        cancel_task = asyncio.create_task(
            fault_pool.call_tool(args, invocation_id=context.invocation_id)
        )
        await asyncio.sleep(0.05)
        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.15)
        cancel_inflight = sum(member.inflight for member in fault_pool.members)
        await fault_pool.close()
        result["fault_generation_drain_cancel"] = {
            "status": "PASS" if transport_error != "NO_ERROR" and new_generation > old_generation and cancel_inflight == 0 else "FAIL",
            "transport_error": transport_error,
            "old_generation": old_generation,
            "new_generation": new_generation,
            "cancel_inflight_after_cleanup": cancel_inflight,
            "drained_members_after_close": len(fault_pool.members) == 0,
        }
        return {"status": "PASS" if all(item["status"] == "PASS" for item in result.values()) else "FAIL", "modes": result}
    finally:
        if not server.should_exit:
            server.should_exit = True
        await server_task


async def main() -> None:
    csv_data = _fixture()
    report = {
        "runner": "tests/acceptance/p2_mcp/run_acceptance.py",
        "mcp_version": importlib.metadata.version("mcp"),
        "httpx2_version": importlib.metadata.version("httpx2"),
        "real_algorithms": real_algorithm_calls(csv_data),
        "cdfm": {
            "status": "NOT_RUN",
            "reason": "Run tests/acceptance/p2_mcp/cdfm_acceptance.py explicitly",
        },
        "capacity_window": await capacity_window(),
        "deadline_and_late_result": await timeout_and_late_result(),
        "rss_cpu": await resource_sample(_resource_fixture()),
        "remote_cancel_control": await remote_cancel_control(csv_data),
        "saturated_execute_lane_control": await saturated_execute_lane_control_acceptance(
            csv_data
        ),
        "client_pool": await client_pool_modes_and_fault(csv_data),
        "mysql_strong_read_and_old_lease": {
            "status": "NOT_RUN",
            "reason": "No running isolated MySQL primary/Compose fixture was available; no fake authority used.",
        },
        "stderr_sensitive_zero_hit": {
            "status": "NOT_RUN",
            "reason": "This one-shot runner does not retain a named causal-mcp container for post-run Docker log scanning.",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
