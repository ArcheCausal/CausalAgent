"""ASGI entry point for the private Streamable HTTP causal-mcp service."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import logging
import os
from typing import Any, Callable

from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import Receive, Scope, Send

from .config import McpServerConfig
from .executor_pool import BoundedProcessPool
from .health import health_payload, readiness_payload
from .runner_registry import RunnerRegistry, build_default_registry
from .service import CausalMcpService, load_frozen_csv


LOGGER = logging.getLogger(__name__)


def _database_probe() -> bool:
    from app.db import get_read_connection

    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None
        finally:
            cursor.close()


def create_app(
    config: McpServerConfig | None = None,
    *,
    registry: RunnerRegistry | None = None,
    executor_pool: BoundedProcessPool | None = None,
    authority_reader: Callable[..., Any] | None = None,
    database_probe: Callable[[], bool] | None = None,
) -> Callable[[Scope, Receive, Send], Any]:
    """Build the app without touching MySQL or creating a process pool at import time."""

    config = config or McpServerConfig.from_env()
    registry = registry or build_default_registry()
    executor_pool = executor_pool or BoundedProcessPool(
        process_workers=config.process_workers,
        queue_capacity=config.queue_capacity,
        enqueue_timeout_seconds=config.enqueue_timeout_seconds,
        timeout_recycle_threshold=config.timeout_recycle_threshold,
    )
    service = CausalMcpService(
        config=config,
        registry=registry,
        executor_pool=executor_pool,
        authority_reader=authority_reader or load_frozen_csv,
        database_probe=database_probe or _database_probe,
        service_instance_id=os.getenv("HOSTNAME", "causal-mcp"),
    )

    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError("mcp==2.2.0 is required for causal-mcp") from exc

    @asynccontextmanager
    async def lifespan(_server: Any):
        await executor_pool.start()
        try:
            yield
        finally:
            await executor_pool.close()

    mcp = MCPServer(
        "causal-mcp-v2",
        lifespan=lifespan,
    )

    @mcp.tool(name="execute_algorithm", structured_output=True)
    async def execute_algorithm(
        command: dict[str, Any],
        trusted_context: dict[str, Any],
        signature: str,
        retry_ordinal: int = 0,
    ) -> dict[str, Any]:
        """Internal tool; never dynamically exposed to the Deep Agent model."""

        return await service.execute_payload(
            {
                "command": command,
                "trusted_context": trusted_context,
                "signature": signature,
                "retry_ordinal": retry_ordinal,
            }
        )

    @mcp.tool(name="cancel_algorithm", structured_output=True)
    async def cancel_algorithm(
        command: dict[str, Any],
        trusted_context: dict[str, Any],
        signature: str,
        retry_ordinal: int = 0,
    ) -> dict[str, Any]:
        """Internal control tool; cancel only the signed invocation identity."""

        return await service.cancel_payload(
            {
                "command": command,
                "trusted_context": trusted_context,
                "signature": signature,
                "retry_ordinal": retry_ordinal,
            }
        )

    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host=config.host,
    )

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "lifespan":
            await mcp_app(scope, receive, send)
            return
        if scope.get("type") != "http":
            response = PlainTextResponse("not found", status_code=404)
            await response(scope, receive, send)
            return

        path = scope.get("path")
        if path == "/health":
            response = JSONResponse(health_payload())
            await response(scope, receive, send)
            return
        if path == "/ready":
            config_ready, mysql_ready, executor_ready = await service.ready()
            status_code, payload = readiness_payload(
                config_ready=config_ready,
                mysql_strong_read=mysql_ready,
                executor_ready=executor_ready,
            )
            response = JSONResponse(payload, status_code=status_code)
            await response(scope, receive, send)
            return
        if path != "/mcp":
            response = PlainTextResponse("not found", status_code=404)
            await response(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("ascii", "ignore")
        expected = f"Bearer {config.service_token}"
        if not config.service_token or not hmac.compare_digest(authorization, expected):
            response = PlainTextResponse("unauthorized", status_code=401)
            await response(scope, receive, send)
            return
        await mcp_app(scope, receive, send)

    return app


def main() -> None:
    import uvicorn
    from observability.logging_runtime import (
        configure_logging,
        current_environment,
        log_event,
    )

    config = McpServerConfig.from_env()
    configure_logging("mcp", current_environment(), logging.INFO)
    log_event(LOGGER, "mcp.startup.ready")
    try:
        uvicorn.run(
            create_app(config),
            host=config.host,
            port=config.port,
            workers=1,
            log_level="info",
            access_log=False,
        )
    except Exception:
        log_event(
            LOGGER,
            "mcp.startup.failed",
            details={
                "phase": "streamable_http",
                "dependency": "mcp_runtime",
                "reason_code": "runtime_failed",
            },
            exc_info=True,
        )
        raise


if __name__ == "__main__":
    main()
