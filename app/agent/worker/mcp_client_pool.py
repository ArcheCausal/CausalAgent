"""Process-local, long-lived MCP client pool for the new causal-mcp service."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
import logging
import os
import traceback
from typing import Any, Callable, Literal

from Agent.deep_agent_tools.error_codes import SafeErrorCode


class _ExpectedMcpCleanupLogFilter(logging.Filter):
    """Suppress only MCP SDK v2.2 teardown noise already mapped to safe events."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "asyncio" or "asynchronous generator" not in record.getMessage():
            return True
        if not record.exc_info:
            return True
        details = "".join(traceback.format_exception(*record.exc_info))
        expected_transport = (
            "mcp/client/streamable_http.py" in details
            and (
                "ReadError" in details
                or "RemoteProtocolError" in details
                or "cancel scope" in details
            )
        )
        return not expected_transport


logging.getLogger("asyncio").addFilter(_ExpectedMcpCleanupLogFilter())


class McpPoolError(RuntimeError):
    def __init__(self, code: SafeErrorCode, *, lane: str | None = None) -> None:
        super().__init__(code.value)
        self.safe_error_code = code
        self.lane = lane


class McpTransportError(McpPoolError):
    def __init__(self, member_id: str, generation: int) -> None:
        super().__init__(SafeErrorCode.MCP_TRANSPORT_FAILED)
        self.member_id = member_id
        self.generation = generation


def _expected_transport_cleanup_error(error: BaseException) -> bool:
    """Accept only known SDK/HTTP teardown failures after a broken transport."""

    if isinstance(error, asyncio.CancelledError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(
            _expected_transport_cleanup_error(child) for child in error.exceptions
        )
    error_type = type(error).__name__
    if error_type in {
        "ReadError",
        "RemoteProtocolError",
        "ConnectError",
        "ClosedResourceError",
    }:
        return True
    return isinstance(error, RuntimeError) and "cancel scope" in str(error).lower()


def _record_transport_cleanup() -> None:
    try:
        from observability.logging_runtime import log_event
        import logging

        log_event(
            logging.getLogger(__name__),
            "mcp.transport.failed",
            details={
                "reason_code": "transport_error",
                "final_attempt": 1,
                "duration_ms": 0,
            },
        )
    except Exception:
        pass


async def _close_context_safely(context: AsyncExitStack) -> None:
    """Close one SDK context while filtering only expected broken-transport noise."""

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def exception_handler(current_loop: asyncio.AbstractEventLoop, context_data: dict[str, Any]) -> None:
        error = context_data.get("exception")
        if isinstance(error, BaseException) and _expected_transport_cleanup_error(error):
            _record_transport_cleanup()
            return
        if previous_handler is not None:
            previous_handler(current_loop, context_data)
        else:
            current_loop.default_exception_handler(context_data)

    loop.set_exception_handler(exception_handler)
    try:
        await context.aclose()
        # AnyIO/async-generator finalizers may schedule the loop exception
        # callback one turn after ``aclose`` returns. Keep the narrow handler
        # installed for that turn before restoring the caller's handler.
        await asyncio.sleep(0.1)
    except BaseException as error:
        if not _expected_transport_cleanup_error(error):
            raise
        _record_transport_cleanup()
    finally:
        loop.set_exception_handler(previous_handler)


@dataclass(frozen=True)
class McpClientPoolConfig:
    url: str
    service_token: str
    pool_size: int = 2
    max_in_flight_per_client: int = 1
    acquire_timeout_seconds: float = 5.0
    control_pool_size: int = 1
    control_max_in_flight_per_client: int = 2
    control_acquire_timeout_seconds: float = 1.0
    max_connections: int = 8
    max_keepalive_connections: int = 4
    read_timeout_seconds: float = 660.0

    @classmethod
    def from_env(cls) -> "McpClientPoolConfig":
        def integer(name: str, default: int) -> int:
            return int(os.getenv(name, str(default)))

        return cls(
            url=os.getenv("CAUSAL_MCP_URL", "http://causal-mcp:8080/mcp"),
            service_token=os.getenv("CAUSAL_MCP_SERVICE_TOKEN", ""),
            pool_size=integer("CAUSAL_MCP_CLIENT_POOL_SIZE", 2),
            max_in_flight_per_client=integer(
                "CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT", 1
            ),
            acquire_timeout_seconds=float(
                os.getenv("CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS", "5")
            ),
            control_pool_size=integer("CAUSAL_MCP_CONTROL_POOL_SIZE", 1),
            control_max_in_flight_per_client=integer(
                "CAUSAL_MCP_CONTROL_MAX_IN_FLIGHT_PER_CLIENT", 2
            ),
            control_acquire_timeout_seconds=float(
                os.getenv("CAUSAL_MCP_CONTROL_ACQUIRE_TIMEOUT_SECONDS", "1")
            ),
            max_connections=integer("CAUSAL_MCP_HTTP_MAX_CONNECTIONS", 8),
            max_keepalive_connections=integer(
                "CAUSAL_MCP_HTTP_MAX_KEEPALIVE_CONNECTIONS", 4
            ),
            read_timeout_seconds=float(
                os.getenv("CAUSAL_MCP_HTTP_READ_TIMEOUT_SECONDS", "660")
            ),
        )

    def validate(self) -> None:
        if not self.url or not self.service_token:
            raise ValueError("CAUSAL_MCP_URL and CAUSAL_MCP_SERVICE_TOKEN are required")
        if (
            self.pool_size <= 0
            or self.max_in_flight_per_client <= 0
            or self.control_pool_size <= 0
            or self.control_max_in_flight_per_client <= 0
        ):
            raise ValueError("MCP client pool sizes must be positive")
        if self.acquire_timeout_seconds <= 0 or self.control_acquire_timeout_seconds <= 0:
            raise ValueError("MCP client pool acquire timeouts must be positive")
        execute_capacity = self.pool_size * self.max_in_flight_per_client
        control_capacity = self.control_pool_size * self.control_max_in_flight_per_client
        if control_capacity < execute_capacity:
            raise ValueError("MCP control capacity must be at least execute capacity")
        total_capacity = execute_capacity + control_capacity
        if self.max_connections < total_capacity:
            raise ValueError("HTTP connection pool is smaller than MCP in-flight capacity")
        if self.max_keepalive_connections < total_capacity:
            raise ValueError("HTTP keepalive pool is smaller than MCP in-flight capacity")


@dataclass
class McpClientMember:
    member_id: str
    lane: Literal["execute", "control"]
    generation: int
    mcp_session_id: str | None
    max_in_flight: int
    inflight: int
    healthy: bool
    draining: bool
    client: Any
    client_context: AsyncExitStack
    reconnect_lock: asyncio.Lock
    stop_event: asyncio.Event
    ready_event: asyncio.Event
    owner_task: asyncio.Task[Any] | None = None
    startup_error: BaseException | None = None


class _MemberLease:
    def __init__(self, pool: "McpClientPool", member: McpClientMember) -> None:
        self.pool = pool
        self.member = member
        self.released = False

    async def release(self) -> None:
        if not self.released:
            self.released = True
            await self.pool.release(self.member)

    async def __aenter__(self) -> McpClientMember:
        return self.member

    async def __aexit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        await self.release()


class McpClientPool:
    """One pool per worker OS process; it never stores Job/checkpoint state."""

    def __init__(
        self,
        config: McpClientPoolConfig,
        *,
        http_client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
        transport_factory: Callable[..., Any] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._http_client = http_client
        self._client_factory = client_factory
        self._transport_factory = transport_factory
        self._members: dict[str, McpClientMember] = {}
        self._condition = asyncio.Condition()
        self._round_robin: dict[str, int] = {"execute": 0, "control": 0}
        self._started = False
        self._closed = False

    @property
    def members(self) -> tuple[McpClientMember, ...]:
        return tuple(self._members.values())

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("MCP client pool is closed")
        if self._started:
            return
        if self._http_client is None:
            import httpx2

            self._http_client = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {self.config.service_token}"},
                timeout=httpx2.Timeout(
                    self.config.read_timeout_seconds,
                    connect=5.0,
                    pool=self.config.acquire_timeout_seconds,
                ),
                limits=httpx2.Limits(
                    max_connections=self.config.max_connections,
                    max_keepalive_connections=self.config.max_keepalive_connections,
                    keepalive_expiry=30.0,
                ),
                http2=False,
            )
            await self._http_client.__aenter__()
        for index in range(self.config.pool_size):
            member = await self._create_member(
                f"mcp-execute-{index + 1}", generation=0, lane="execute"
            )
            self._members[member.member_id] = member
        for index in range(self.config.control_pool_size):
            member = await self._create_member(
                f"mcp-control-{index + 1}", generation=0, lane="control"
            )
            self._members[member.member_id] = member
        self._started = True

    async def _create_member(
        self,
        member_id: str,
        *,
        generation: int,
        lane: Literal["execute", "control"],
    ) -> McpClientMember:
        from mcp.client import Client
        from mcp.client.streamable_http import streamable_http_client

        context = AsyncExitStack()
        if self._transport_factory is None:
            transport = streamable_http_client(
                self.config.url,
                http_client=self._http_client,
                # causal-mcp is stateless HTTP; a failed transport must not
                # issue a best-effort DELETE against an already-dead server.
                terminate_on_close=False,
            )
        else:
            transport = self._transport_factory(
                self.config.url,
                http_client=self._http_client,
            )
        client = (
            self._client_factory(
                transport,
                read_timeout_seconds=self.config.read_timeout_seconds,
            )
            if self._client_factory is not None
            else Client(transport, read_timeout_seconds=self.config.read_timeout_seconds)
        )
        member = McpClientMember(
            member_id=member_id,
            generation=generation,
            mcp_session_id=None,
            lane=lane,
            max_in_flight=(
                self.config.max_in_flight_per_client
                if lane == "execute"
                else self.config.control_max_in_flight_per_client
            ),
            inflight=0,
            healthy=False,
            draining=False,
            client=client,
            client_context=context,
            reconnect_lock=asyncio.Lock(),
            stop_event=asyncio.Event(),
            ready_event=asyncio.Event(),
        )

        async def own_context() -> None:
            try:
                await context.__aenter__()
                await context.enter_async_context(client)
                # Handshake/readiness only; never use this list to register model tools.
                tools = await client.list_tools()
                advertised = {
                    getattr(tool, "name", None)
                    for tool in getattr(tools, "tools", ())
                }
                if not {"execute_algorithm", "cancel_algorithm"}.issubset(advertised):
                    raise RuntimeError("causal-mcp capability handshake failed")
                session = getattr(client, "session", None)
                member.mcp_session_id = getattr(session, "session_id", None)
                member.healthy = True
            except BaseException as exc:
                member.startup_error = exc
            finally:
                member.ready_event.set()
            if member.startup_error is None:
                await member.stop_event.wait()
            await _close_context_safely(context)

        member.owner_task = asyncio.create_task(own_context())
        await asyncio.wait_for(member.ready_event.wait(), self.config.read_timeout_seconds)
        if member.startup_error is not None:
            await asyncio.gather(member.owner_task, return_exceptions=True)
            raise member.startup_error
        return member

    @staticmethod
    async def _stop_member(member: McpClientMember) -> None:
        member.stop_event.set()
        if member.owner_task is not None:
            await asyncio.gather(member.owner_task, return_exceptions=True)
        else:
            await _close_context_safely(member.client_context)

    async def acquire(self, lane: Literal["execute", "control"] = "execute") -> _MemberLease:
        if not self._started or self._closed:
            raise McpPoolError(SafeErrorCode.MCP_TRANSPORT_FAILED)
        if lane not in {"execute", "control"}:
            raise ValueError(f"unknown MCP pool lane: {lane}")
        acquire_timeout = (
            self.config.acquire_timeout_seconds
            if lane == "execute"
            else self.config.control_acquire_timeout_seconds
        )

        async def wait_for_member() -> McpClientMember:
            async with self._condition:
                while True:
                    candidates = [
                        member
                        for member in self._members.values()
                        if member.lane == lane
                        if member.healthy
                        and not member.draining
                        and member.inflight < member.max_in_flight
                    ]
                    if candidates:
                        lowest_load = min(
                            member.inflight / member.max_in_flight
                            for member in candidates
                        )
                        loaded = [
                            member
                            for member in candidates
                            if member.inflight / member.max_in_flight == lowest_load
                        ]
                        member = loaded[self._round_robin[lane] % len(loaded)]
                        self._round_robin[lane] += 1
                        member.inflight += 1
                        return member
                    await self._condition.wait()

        try:
            member = await asyncio.wait_for(
                wait_for_member(), acquire_timeout
            )
        except asyncio.TimeoutError as exc:
            raise McpPoolError(SafeErrorCode.MCP_CAPACITY_EXHAUSTED, lane=lane) from exc
        return _MemberLease(self, member)

    async def release(self, member: McpClientMember) -> None:
        async with self._condition:
            if member.inflight > 0:
                member.inflight -= 1
            self._condition.notify_all()

    async def call_tool(
        self,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
        retry_ordinal: int = 0,
    ) -> Any:
        return await self._call_named_tool("execute_algorithm", arguments)

    async def cancel_tool(
        self,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
        retry_ordinal: int = 0,
    ) -> Any:
        """通过同一受认证连接池发送 invocation 级控制面取消请求。"""

        return await self._call_named_tool("cancel_algorithm", arguments)

    async def _call_named_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        lane: Literal["execute", "control"] = (
            "control" if tool_name == "cancel_algorithm" else "execute"
        )
        lease = await self.acquire(lane=lane)
        member = lease.member
        request_task = asyncio.create_task(
            member.client.call_tool(tool_name, arguments)
        )
        try:
            return await asyncio.shield(request_task)
        except asyncio.CancelledError:
            # The caller may be canceled while the SDK still owns a POST task.
            # Give that task a bounded chance to consume the cancellation/断流;
            # only then cancel and join it so pool drain never closes a live
            # StreamableHTTP task group.
            try:
                await asyncio.wait_for(
                    asyncio.shield(request_task),
                    timeout=min(self.config.read_timeout_seconds, 1.0),
                )
            except asyncio.TimeoutError:
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            except BaseException as cleanup_error:
                if not _expected_transport_cleanup_error(cleanup_error):
                    raise
                _record_transport_cleanup()
            raise
        except Exception as exc:
            if not request_task.done():
                await asyncio.gather(request_task, return_exceptions=True)
            await lease.release()
            try:
                await self.ensure_reconnected(member.member_id, member.generation)
            except BaseException as cleanup_error:
                if not _expected_transport_cleanup_error(cleanup_error):
                    raise
                _record_transport_cleanup()
            raise McpTransportError(member.member_id, member.generation) from exc
        finally:
            await lease.release()

    async def ensure_reconnected(self, member_id: str, observed_generation: int) -> None:
        member = self._members.get(member_id)
        if member is None:
            raise McpPoolError(SafeErrorCode.MCP_TRANSPORT_FAILED)
        async with member.reconnect_lock:
            if member.generation != observed_generation and member.healthy:
                return
            async with self._condition:
                member.healthy = False
                member.draining = True
                self._condition.notify_all()
                while member.inflight:
                    await self._condition.wait()
            await self._stop_member(member)
            replacement = await self._create_member(
                member.member_id,
                generation=member.generation + 1,
                lane=member.lane,
            )
            async with self._condition:
                self._members[member.member_id] = replacement
                self._condition.notify_all()
            try:
                from observability.logging_runtime import log_event
                import logging

                log_event(
                    logging.getLogger(__name__),
                    "mcp.client.reconnected",
                    details={
                        "generation": replacement.generation,
                        "pool_lane": replacement.lane,
                    },
                )
            except Exception:
                pass

    async def close(self) -> None:
        self._closed = True
        async with self._condition:
            members = list(self._members.values())
            for member in members:
                member.healthy = False
                member.draining = True
            self._condition.notify_all()
        for member in members:
            await self._stop_member(member)
        self._members.clear()
        if self._http_client is not None:
            await self._http_client.__aexit__(None, None, None)
            self._http_client = None
