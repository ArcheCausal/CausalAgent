"""P0 real MCP 2.2 Streamable HTTP client/server contract spike.

The script starts the sibling server in a subprocess, exercises the real MCP
protocol, and prints only safe structural facts. It is intentionally outside
the default unit-test collection because it opens a local server process.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
import json
import os
import subprocess
import sys
import time
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[2]
SERVER = Path(__file__).with_name("p0_mcp_http_server.py")
PORT = int(os.environ.get("P0_MCP_PORT", "8765"))
TOKEN = os.environ.get("P0_MCP_TOKEN", "p0-test-token")
BASE_URL = f"http://127.0.0.1:{PORT}"
MCP_URL = f"{BASE_URL}/mcp"


def _server_environment() -> dict[str, str]:
    env = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR")
        if os.environ.get(name)
    }
    env.update(
        {
            "P0_MCP_PORT": str(PORT),
            "P0_MCP_TOKEN": TOKEN,
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


async def _wait_for_server(process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    async with httpx2.AsyncClient(timeout=1.0) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("MCP P0 server exited before readiness")
            try:
                response = await client.get(f"{BASE_URL}/health")
            except httpx2.HTTPError:
                await asyncio.sleep(0.1)
                continue
            if response.status_code == 200:
                return
            await asyncio.sleep(0.1)
    raise RuntimeError("MCP P0 server readiness timed out")


async def _unauthorized_request() -> bool:
    async with httpx2.AsyncClient(timeout=5.0) as client:
        response = await client.get(MCP_URL)
    return response.status_code == 401


async def _server_state(http_client: httpx2.AsyncClient) -> dict[str, Any]:
    response = await http_client.get(f"{BASE_URL}/debug/state")
    response.raise_for_status()
    return response.json()


def _new_tasks_pending(baseline: set[asyncio.Task[Any]]) -> bool:
    """Detect any task created by a transport context that survived its close."""

    return any(
        task not in baseline and not task.done() for task in asyncio.all_tasks()
    )


async def _exercise_client() -> dict[str, Any]:
    limits = httpx2.Limits(max_connections=4, max_keepalive_connections=4)
    timeout = httpx2.Timeout(10.0, read=10.0)
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=timeout,
        limits=limits,
        http2=False,
    ) as http_client:
        transport = streamable_http_client(MCP_URL, http_client=http_client)
        client = Client(transport, read_timeout_seconds=10)
        modern_task_baseline = asyncio.all_tasks()
        async with client:
            protocol_version = str(client.protocol_version)
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            echo = await client.call_tool("p0_echo", {"value": "echo"})
            failure = await client.call_tool("p0_fail", {})
            concurrent = await asyncio.gather(
                client.call_tool("p0_echo", {"value": "alpha"}),
                client.call_tool("p0_echo", {"value": "beta"}),
            )
            cancel_task = asyncio.create_task(
                client.call_tool("p0_sleep", {"seconds": 1.0})
            )
            await asyncio.sleep(0.1)
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
            cancel_in_flight_state = await _server_state(http_client)

            echo_text = str(echo.content[0].text) if echo.content else ""
            concurrent_text = [
                str(result.content[0].text) if result.content else ""
                for result in concurrent
            ]
            session_id = getattr(getattr(client, "session", None), "session_id", None)
            modern = {
                "protocol_version": protocol_version,
                "list_tools": {"p0_echo", "p0_fail", "p0_sleep"}.issubset(names),
                "call": echo_text == "echo",
                "error": bool(failure.is_error),
                "concurrent_correlation": concurrent_text == ["alpha", "beta"],
                "stateless_session": session_id is None,
                "cancelled": cancel_task.cancelled(),
            }
        await asyncio.sleep(1.5)
        modern_server_state = await _server_state(http_client)
        modern["server_discover_used"] = "server/discover" in modern_server_state.get(
            "methods", []
        )
        modern["cancel_observed_in_flight"] = (
            cancel_in_flight_state.get("sleep_started", 0) >= 1
            and cancel_in_flight_state.get("sleep_active", 0) >= 1
            and cancel_in_flight_state.get("sleep_completed", 0) == 0
        )
        modern["cancel_cleanup"] = (
            modern_server_state.get("active_requests") == 0
            and modern_server_state.get("sleep_active") == 0
            and modern_server_state.get("sleep_completed", 0) >= 1
        )
        modern["server_mcp_tasks_closed"] = not modern_server_state.get("mcp_tasks")
        modern["transport_tasks_closed"] = not _new_tasks_pending(modern_task_baseline)
        modern_methods = set(modern_server_state.get("methods", []))
        legacy_transport = streamable_http_client(MCP_URL, http_client=http_client)
        legacy = Client(
            legacy_transport,
            mode="legacy",
            read_timeout_seconds=10,
        )
        legacy_task_baseline = asyncio.all_tasks()
        async with legacy:
            legacy_tools = await legacy.list_tools()
            legacy_echo = await legacy.call_tool("p0_echo", {"value": "legacy"})
            legacy_text = (
                str(legacy_echo.content[0].text) if legacy_echo.content else ""
            )
        await asyncio.sleep(0.5)
        legacy_server_state = await _server_state(http_client)
        legacy_methods = set(legacy_server_state.get("methods", []))
        return {
            **modern,
            "legacy_initialize_used": bool(
                {"initialize", "notifications/initialized"}
                & (legacy_methods - modern_methods)
            ),
            "legacy_list_tools": {
                "p0_echo",
                "p0_fail",
                "p0_sleep",
            }.issubset({tool.name for tool in legacy_tools.tools}),
            "legacy_call": legacy_text == "legacy",
            "legacy_cleanup": legacy_server_state.get("active_requests") == 0
            and legacy_server_state.get("sleep_active") == 0,
            "legacy_server_mcp_tasks_closed": not legacy_server_state.get("mcp_tasks"),
            "legacy_transport_tasks_closed": not _new_tasks_pending(legacy_task_baseline),
        }


async def run() -> dict[str, Any]:
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=ROOT,
        env=_server_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        await _wait_for_server(process)
        result = await _exercise_client()
        result["bearer_rejected"] = await _unauthorized_request()
        if not all(result.values()):
            raise RuntimeError("MCP P0 contract assertion failed")
        return result
    finally:
        if process.poll() is None:
            process.terminate()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def main() -> None:
    try:
        result = asyncio.run(run())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
    print(json.dumps({"status": "passed", **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
