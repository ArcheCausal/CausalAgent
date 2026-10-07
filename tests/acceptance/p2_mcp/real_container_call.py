"""Call the running causal-mcp container through real MCP HTTP and MySQL authority."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys

ROOT = Path("/app") if Path("/app/Agent").is_dir() else Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, McpInvocationContext


async def main() -> None:
    issued = datetime.now(timezone.utc)
    context = McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000203",
        job_id="00000000-0000-0000-0000-000000000201",
        session_id="00000000-0000-0000-0000-000000000202",
        user_id=7,
        attempt_count=1,
        lease_epoch=4,
        worker_id="worker-p2m",
        input_snapshot_digest="p2m-file-hash",
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        key_id="current",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=context.invocation_id,
        capability_id=PC_SPEC.capability_id,
        capability_version=PC_SPEC.version,
        spec_digest=PC_SPEC.spec_digest,
        provider_call_id="p2m-real-container-call",
        input_identity="p2m-file-hash",
        parameters={"alpha": 0.05},
    )
    url = os.getenv("P2M_MCP_URL", "http://host.docker.internal:18080/mcp")
    token = os.getenv("P2M_MCP_TOKEN", "p2m-service-token")
    signing_key = os.getenv("P2M_MCP_SIGNING_KEY", "p2m-signing-key")
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=30.0),
        http2=False,
    ) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        client = Client(transport, read_timeout_seconds=30)
        async with client:
            response = await client.call_tool(
                "execute_algorithm",
                {
                    "command": command.model_dump(mode="json"),
                    "trusted_context": context.model_dump(mode="json"),
                    "signature": sign_invocation(context, command, signing_key),
                },
            )
            payload = getattr(response, "structured_content", None)
            assert isinstance(payload, dict)
            result = payload.get("result") or {}
            print(
                {
                    "ok": payload.get("ok"),
                    "status": result.get("status"),
                    "safe_error_code": (result.get("diagnostics") or {}).get(
                        "safe_error_code"
                    ),
                }
            )


if __name__ == "__main__":
    asyncio.run(main())
