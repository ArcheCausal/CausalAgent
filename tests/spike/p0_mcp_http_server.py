"""P0 MCP 2.2 Streamable HTTP server used by the real protocol spike."""

from __future__ import annotations

import asyncio
import json
import os

from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import Receive, Scope, Send
import uvicorn

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent


TOKEN = os.environ.get("P0_MCP_TOKEN", "p0-test-token")
PORT = int(os.environ.get("P0_MCP_PORT", "8765"))

REQUESTED_METHODS: set[str] = set()
ACTIVE_REQUESTS: dict[int, str] = {}
SLEEP_STARTED = 0
SLEEP_COMPLETED = 0
SLEEP_ACTIVE = 0


def _pending_mcp_tasks() -> list[str]:
    """Return active MCP server task names, excluding the debug request itself."""

    current = asyncio.current_task()
    pending: list[str] = []
    for task in asyncio.all_tasks():
        if task is current or task.done():
            continue
        coroutine = task.get_coro()
        module = getattr(coroutine, "__module__", "")
        if module.startswith("mcp.server"):
            pending.append(
                f"{module}.{getattr(coroutine, '__qualname__', '')}"
            )
    return sorted(pending)


server = MCPServer(name="p0-causal-mcp", version="p0")


@server.tool(name="p0_echo")
def p0_echo(value: str) -> str:
    """Return a value so the client can verify response correlation."""

    return value


@server.tool(name="p0_fail")
def p0_fail() -> CallToolResult:
    """Return a protocol-level error so the client can verify is_error."""

    return CallToolResult(
        content=[TextContent(text="p0 expected tool failure")],
        isError=True,
    )


@server.tool(name="p0_sleep")
async def p0_sleep(seconds: float) -> str:
    """Sleep long enough for the client cancellation probe."""

    global SLEEP_ACTIVE, SLEEP_COMPLETED, SLEEP_STARTED
    SLEEP_STARTED += 1
    SLEEP_ACTIVE += 1
    try:
        await asyncio.sleep(seconds)
    finally:
        SLEEP_ACTIVE -= 1
    SLEEP_COMPLETED += 1
    return "completed"


mcp_app = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    host="127.0.0.1",
)


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Expose an unauthenticated health endpoint and protect MCP traffic."""

    if scope.get("type") == "lifespan":
        await mcp_app(scope, receive, send)
        return

    if scope.get("type") == "http" and scope.get("path") == "/health":
        response = JSONResponse({"status": "ok"})
        await response(scope, receive, send)
        return

    headers = dict(scope.get("headers", []))
    if headers.get(b"authorization", b"") != f"Bearer {TOKEN}".encode("ascii"):
        response = PlainTextResponse("unauthorized", status_code=401)
        await response(scope, receive, send)
        return

    if scope.get("type") == "http" and scope.get("path") == "/debug/state":
        response = JSONResponse(
            {
                "methods": sorted(REQUESTED_METHODS),
                "active_requests": len(ACTIVE_REQUESTS),
                "active_paths": sorted(ACTIVE_REQUESTS.values()),
                "sleep_started": SLEEP_STARTED,
                "sleep_completed": SLEEP_COMPLETED,
                "sleep_active": SLEEP_ACTIVE,
                "mcp_tasks": _pending_mcp_tasks(),
            }
        )
        await response(scope, receive, send)
        return

    if scope.get("type") != "http" or scope.get("path") != "/mcp":
        response = PlainTextResponse("not found", status_code=404)
        await response(scope, receive, send)
        return

    body = bytearray()

    async def tracked_receive() -> dict[str, object]:
        message = await receive()
        if message.get("type") == "http.request":
            body.extend(message.get("body", b""))
            if not message.get("more_body", False) and body:
                try:
                    payload = json.loads(bytes(body))
                except (ValueError, UnicodeDecodeError):
                    payload = None
                if isinstance(payload, dict) and isinstance(
                    payload.get("method"), str
                ):
                    REQUESTED_METHODS.add(payload["method"])
        return message

    request_key = id(asyncio.current_task())
    ACTIVE_REQUESTS[request_key] = str(scope.get("method", ""))
    try:
        await mcp_app(scope, tracked_receive, send)
    finally:
        ACTIVE_REQUESTS.pop(request_key, None)


def main() -> None:
    """Run the isolated P0 server."""

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="error",
        access_log=False,
    )


if __name__ == "__main__":
    main()
