"""P2-M real SDK smoke with an in-process fake authority reader.

This is intentionally outside the default pytest collection. It proves the v2.2
MCP transport and structured internal tool envelope without claiming MySQL or
production algorithm-capacity evidence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from Agent.CausalAgentMCP.app import create_app
from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, McpInvocationContext
from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig
from Agent.deep_agent_tools.mcp_algorithm_executor import McpAlgorithmExecutor


HOST = "127.0.0.1"
PORT = 8792
TOKEN = "p2-smoke-token"
SIGNING_KEY = "p2-smoke-signing-key"


def _request() -> tuple[AlgorithmExecutionCommand, McpInvocationContext, str]:
    issued = datetime.now(timezone.utc)
    context = McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000101",
        job_id="00000000-0000-0000-0000-000000000102",
        session_id="00000000-0000-0000-0000-000000000103",
        user_id=7,
        attempt_count=1,
        lease_epoch=1,
        worker_id="p2-smoke-worker",
        input_snapshot_digest="p2-smoke-file",
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        key_id="current",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=context.invocation_id,
        capability_id=PC_SPEC.capability_id,
        capability_version=PC_SPEC.version,
        spec_digest=PC_SPEC.spec_digest,
        provider_call_id="p2-smoke-call",
        input_identity="p2-smoke-file",
        parameters={"alpha": 0.05},
    )
    return command, context, sign_invocation(context, command, SIGNING_KEY)


async def run() -> dict[str, object]:
    config = McpServerConfig(
        service_token=TOKEN,
        signing_key_current=SIGNING_KEY,
        signing_key_previous=None,
        signing_key_id="current",
        signing_key_previous_id="previous",
        process_workers=1,
        queue_capacity=1,
    )
    app = create_app(
        config,
        authority_reader=lambda _context: "A,B\n1,2\n3,4\n4,5\n",
        database_probe=lambda: True,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=PORT, log_level="error", access_log=False)
    )
    server_task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=httpx2.Timeout(30.0, read=30.0),
            http2=False,
        ) as http_client:
            health = await http_client.get(f"http://{HOST}:{PORT}/health")
            ready = await http_client.get(f"http://{HOST}:{PORT}/ready")
            async with httpx2.AsyncClient(
                timeout=httpx2.Timeout(5.0, read=5.0),
                http2=False,
            ) as unauthenticated_client:
                unauthorized = await unauthenticated_client.get(
                    f"http://{HOST}:{PORT}/mcp"
                )
            transport = streamable_http_client(
                f"http://{HOST}:{PORT}/mcp", http_client=http_client
            )
            client = Client(transport, read_timeout_seconds=30)
            async with client:
                tools = await client.list_tools()
                command, context, signature = _request()
                response = await client.call_tool(
                    "execute_algorithm",
                    {
                        "command": command.model_dump(mode="json"),
                        "trusted_context": context.model_dump(mode="json"),
                        "signature": signature,
                    },
                )
                payload = getattr(response, "structured_content", None)
                assert health.status_code == 200
                assert ready.status_code == 200
                assert unauthorized.status_code == 401
                assert "execute_algorithm" in {tool.name for tool in tools.tools}
                assert "cancel_algorithm" in {tool.name for tool in tools.tools}
                assert isinstance(payload, dict) and payload.get("ok") is True
                bad_signature = await client.call_tool(
                    "execute_algorithm",
                    {
                        "command": command.model_dump(mode="json"),
                        "trusted_context": context.model_dump(mode="json"),
                        "signature": "0" * 64,
                    },
                )
                bad_signature_payload = getattr(
                    bad_signature, "structured_content", None
                )
                assert (
                    isinstance(bad_signature_payload, dict)
                    and bad_signature_payload.get("ok") is False
                    and bad_signature_payload.get("error", {}).get("code")
                    == "MCP_AUTH_FAILED"
                )
            pool = McpClientPool(
                McpClientPoolConfig(
                    url=f"http://{HOST}:{PORT}/mcp",
                    service_token=TOKEN,
                    pool_size=2,
                    max_in_flight_per_client=1,
                    max_connections=4,
                    max_keepalive_connections=4,
                )
            )
            await pool.start()
            try:
                pooled = await pool.call_tool(
                    {
                        "command": command.model_dump(mode="json"),
                        "trusted_context": context.model_dump(mode="json"),
                        "signature": signature,
                    },
                    invocation_id=context.invocation_id,
                )
                pooled_payload = getattr(pooled, "structured_content", None)
                assert isinstance(pooled_payload, dict)
                assert pooled_payload.get("ok") is True
                executor_result = await McpAlgorithmExecutor(
                    pool,
                    signing_key=SIGNING_KEY,
                ).execute(command, context)
                assert executor_result.invocation_id == context.invocation_id
            finally:
                await pool.close()
            return {
                "status": "passed",
                "health": health.status_code,
                "ready": ready.status_code,
                "structured_result": True,
                "hmac_bearer": True,
                "client_pool": True,
                "executor_contract": True,
                "algorithm_status": payload["result"]["status"],
            }
    finally:
        server.should_exit = True
        await server_task


def main() -> None:
    print(json.dumps(asyncio.run(run()), ensure_ascii=False))


if __name__ == "__main__":
    main()
