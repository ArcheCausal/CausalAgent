"""验证 invocation Guard 在 Graph、router 和 error handler 边界的传播。"""

from __future__ import annotations

import asyncio
import os
from typing import TypedDict
from unittest.mock import patch

import pytest


for _key, _value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "test-database",
}.items():
    os.environ.setdefault(_key, _value)

from langgraph.errors import NodeError  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.runtime import Runtime  # noqa: E402
from langgraph.types import RetryPolicy  # noqa: E402

from Agent.causal_agent.graph_utils import (  # noqa: E402
    bind_node,
    guarded_error_handler,
    guarded_router,
)
from Agent.deep_agent.context import AgentRunContext  # noqa: E402
from Agent.causal_agent.fault_tolerance import retry_transient_errors  # noqa: E402
from app.agent.worker.execution_guard import (  # noqa: E402
    JobExecutionGuard,
    JobExecutionRevoked,
)


class GuardState(TypedDict):
    value: int


class FakeGuard:
    """只记录边界检查的运行时 Guard 替身。"""

    def __init__(self) -> None:
        self.revoked = False
        self.calls: list[str] = []

    async def ensure_active(self) -> None:
        self.calls.append("ensure")
        if self.revoked:
            raise JobExecutionRevoked("revoked")

    async def check_after_call(self) -> None:
        self.calls.append("after")
        if self.revoked:
            raise JobExecutionRevoked("revoked")


def test_bound_node_delegates_checks_through_agent_run_context():
    """生产 AgentRunContext 必须把节点前后检查委托给 execution guard。"""
    async def scenario():
        guard = FakeGuard()

        async def increment(state: GuardState) -> dict[str, int]:
            return {"value": state["value"] + 1}

        graph = StateGraph(GuardState)
        graph.add_node("increment", bind_node(increment))
        graph.set_entry_point("increment")

        result = await graph.compile().ainvoke(
            {"value": 0},
            context=AgentRunContext(execution_guard=guard),
        )

        assert result["value"] == 1
        assert guard.calls == ["ensure", "after"]

    asyncio.run(scenario())


def test_conditional_router_receives_runtime_context_and_checks_before_target():
    """router 必须能拿到父图 context，并在返回目标前再次检查。"""
    async def scenario():
        guard = FakeGuard()
        route_called = False

        async def start(state: GuardState, runtime: Runtime):
            runtime.context.revoked = True
            return {"value": state["value"] + 1}

        def route(state: GuardState) -> str:
            nonlocal route_called
            route_called = True
            return "end"

        graph = StateGraph(GuardState)
        graph.add_node("start", start)
        graph.add_node("end", lambda state: {"value": state["value"] + 1})
        graph.set_entry_point("start")
        graph.add_conditional_edges("start", guarded_router(route), {"end": "end"})

        with pytest.raises(JobExecutionRevoked):
            await graph.compile().ainvoke({"value": 0}, context=guard)

        assert route_called is False
        assert guard.calls[-1] == "ensure"

    asyncio.run(scenario())


def test_error_handler_cannot_build_fallback_after_revocation():
    """error handler 开始前撤销时，不能生成 fallback State 或 Command。"""
    async def scenario():
        guard = FakeGuard()
        handler_called = False

        async def boom(state: GuardState, runtime: Runtime):
            runtime.context.revoked = True
            raise ValueError("late failure")

        def handler(state: GuardState, error: NodeError) -> dict[str, int]:
            nonlocal handler_called
            handler_called = True
            return {"value": 9}

        graph = StateGraph(GuardState)
        graph.add_node(
            "boom",
            boom,
            error_handler=guarded_error_handler(handler, event_node_name="boom"),
        )
        graph.set_entry_point("boom")

        with pytest.raises(JobExecutionRevoked):
            await graph.compile().ainvoke({"value": 0}, context=guard)

        assert handler_called is False

    asyncio.run(scenario())


def test_retry_predicate_excludes_revoked_invocation():
    """撤销异常和已撤销 Guard 都不能进入下一次 retry attempt。"""
    guard = JobExecutionGuard("job-1", "worker-a", 1, 1, revoked=True)
    token = guard.install()
    try:
        assert retry_transient_errors(JobExecutionRevoked("revoked")) is False
        assert retry_transient_errors(RuntimeError("temporary")) is False
    finally:
        JobExecutionGuard.reset(token)


def test_retry_backoff_does_not_enter_next_attempt_after_revocation():
    """撤销发生在 retry backoff 期间时，下一次 attempt 只触发 Guard，不调用节点。"""
    async def scenario():
        guard = JobExecutionGuard("job-1", "worker-a", 1, 1)
        calls = 0

        async def fake_ensure_active():
            if guard.revoked:
                raise JobExecutionRevoked("revoked during retry backoff")

        guard.ensure_active = fake_ensure_active
        guard.check_after_call = fake_ensure_active

        async def flaky(state: GuardState) -> dict[str, int]:
            nonlocal calls
            calls += 1
            raise ConnectionError("temporary")

        graph = StateGraph(GuardState)
        graph.add_node(
            "flaky",
            bind_node(flaky, event_node_name="flaky"),
            retry_policy=RetryPolicy(
                max_attempts=2,
                initial_interval=0.05,
                backoff_factor=1.0,
                max_interval=0.05,
                jitter=False,
                retry_on=retry_transient_errors,
            ),
        )
        graph.set_entry_point("flaky")
        revoke_task = asyncio.create_task(_revoke_after(guard, 0.01))
        token = guard.install()
        try:
            with pytest.raises(JobExecutionRevoked):
                await graph.compile().ainvoke({"value": 0}, context=guard)
        finally:
            JobExecutionGuard.reset(token)
            await revoke_task

        assert calls == 1

    asyncio.run(scenario())


def test_retry_attempts_are_silent_and_final_degradation_logs_once():
    """单次 attempt 失败只走 custom 事件，重试耗尽后才写一次运行事件。"""
    async def scenario():
        guard = FakeGuard()
        calls = 0

        async def flaky(state: GuardState) -> dict[str, int]:
            nonlocal calls
            calls += 1
            raise ConnectionError("sensitive dependency text")

        def fallback(state: GuardState, error: NodeError) -> dict[str, int]:
            return {"value": 9}

        graph = StateGraph(GuardState)
        graph.add_node(
            "flaky",
            bind_node(flaky, event_node_name="flaky"),
            retry_policy=RetryPolicy(
                max_attempts=2,
                initial_interval=0.001,
                backoff_factor=1.0,
                max_interval=0.001,
                jitter=False,
                retry_on=lambda _exc: True,
            ),
            error_handler=guarded_error_handler(
                fallback,
                event_node_name="flaky",
            ),
        )
        graph.set_entry_point("flaky")
        with patch("Agent.causal_agent.graph_utils.log_event") as log_event:
            result = await graph.compile().ainvoke({"value": 0}, context=guard)

        assert result["value"] == 9
        assert calls == 2
        log_event.assert_called_once()
        assert log_event.call_args.args[1] == "job.node.degraded"
        assert log_event.call_args.kwargs["details"]["final_attempt"] >= 1
        assert "sensitive dependency text" not in repr(
            log_event.call_args.kwargs["details"]
        )

    asyncio.run(scenario())


def test_mcp_transport_final_failure_uses_only_specialized_parent_event():
    async def scenario():
        guard = FakeGuard()

        async def transport_node(state: GuardState):
            raise ConnectionError("transport content must stay private")

        def fallback(state: GuardState, error: NodeError):
            return {"value": 3}

        graph = StateGraph(GuardState)
        graph.add_node(
            "mcp_tool_node",
            bind_node(transport_node, event_node_name="mcp_tool_node"),
            error_handler=guarded_error_handler(
                fallback,
                event_node_name="mcp_tool_node",
                timeout_ms=360_000,
                mcp_transport=True,
            ),
        )
        graph.set_entry_point("mcp_tool_node")
        with patch("Agent.causal_agent.graph_utils.log_event") as log_event:
            result = await graph.compile().ainvoke({"value": 0}, context=guard)

        assert result["value"] == 3
        log_event.assert_called_once()
        assert log_event.call_args.args[1] == "mcp.transport.failed"
        assert "transport content" not in repr(log_event.call_args.kwargs["details"])

    asyncio.run(scenario())


async def _revoke_after(guard: JobExecutionGuard, delay: float) -> None:
    """在 retry sleep 中撤销 Guard。"""
    await asyncio.sleep(delay)
    guard.mark_revoked()


def test_guard_revocation_wakes_long_running_dependency_waiter() -> None:
    async def scenario():
        guard = JobExecutionGuard("job-1", "worker-a", 1, 1)
        waiter = asyncio.create_task(guard.wait_revoked())
        await asyncio.sleep(0)
        assert waiter.done() is False
        guard.mark_revoked(status="canceled", execution_state="draining")
        await asyncio.wait_for(waiter, timeout=0.1)
        assert guard.revoked is True
        assert guard.last_status == "canceled"

    asyncio.run(scenario())


def test_degraded_log_carries_structured_output_diagnostics_without_model_text() -> None:
    """结构化输出降级日志只带 schema、尝试次数和字段路径，不带模型取值。"""
    from pydantic import BaseModel, ConfigDict, ValidationError

    from Agent.llm_structured_output import StructuredOutputError
    from observability.event_catalog import validate_event_details

    class Draft(BaseModel):
        model_config = ConfigDict(extra="forbid")

        title: str

    try:
        Draft.model_validate({"title": "ok", "leaked_key_name": "secret-model-value"})
    except ValidationError as exc:
        cause = exc

    async def scenario():
        guard = FakeGuard()

        async def report_node(state: GuardState) -> dict[str, int]:
            raise StructuredOutputError(
                node_name="report",
                schema_name="Draft",
                cause=cause,
                attempts=2,
            )

        def fallback(state: GuardState, error: NodeError) -> dict[str, int]:
            return {"value": 5}

        graph = StateGraph(GuardState)
        graph.add_node(
            "report",
            bind_node(report_node, event_node_name="report"),
            error_handler=guarded_error_handler(fallback, event_node_name="report"),
        )
        graph.set_entry_point("report")
        with patch("Agent.causal_agent.graph_utils.log_event") as log_event:
            result = await graph.compile().ainvoke({"value": 0}, context=guard)

        assert result["value"] == 5
        assert log_event.call_args.args[1] == "job.node.degraded"
        details = log_event.call_args.kwargs["details"]
        assert details["cause_code"] == "schema_invalid"
        assert details["failure_kind"] == "node_error"
        assert details["schema_name"] == "Draft"
        assert details["structured_attempts"] == 2
        assert details["validation_error_count"] == 1
        assert details["validation_first_type"] == "extra_forbidden"
        assert details["validation_first_loc"] == "extra"
        assert "leaked_key_name" not in repr(details)
        assert "secret-model-value" not in repr(details)

        resolved, safe, violation = validate_event_details("job.node.degraded", details)
        assert resolved is not None
        assert violation is None
        assert safe == details

    asyncio.run(scenario())
