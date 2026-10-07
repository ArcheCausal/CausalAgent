"""验证 worker runtime 的依赖显式性和 slot 隔离边界。"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.agent.worker.runtime import (
    McpClientResources,
    ProcessRuntime,
    RagReadiness,
    SlotRuntime,
    create_deep_agent_model,
    create_process_runtime,
    create_slot_runtime,
    inspect_rag_readiness,
    initialize_production_runtime,
)


def test_worker_package_entrypoint_calls_bootstrap_main():
    """包入口应继续把 ``python -m app.agent.worker`` 转交给 bootstrap。"""
    with patch("app.agent.worker.bootstrap.main") as main:
        runpy.run_module("app.agent.worker", run_name="__main__")

    main.assert_called_once_with()


def test_core_facade_does_not_hold_runtime_globals():
    """兼容门面可以转发纯接口，但不能重新成为可变运行时容器。"""
    from app.agent import core

    assert not hasattr(core, "llm")
    assert not hasattr(core, "agent_graph")
    assert not hasattr(core, "mcp_session")


def test_process_runtime_returns_explicit_llm_and_rag_state():
    """进程初始化应返回值对象，而不是写入 core 模块全局变量。"""
    llm = Mock(name="llm")
    with (
        patch("app.agent.worker.runtime.create_llm", return_value=llm),
        patch(
            "app.agent.worker.runtime.inspect_rag_readiness",
            return_value=RagReadiness("rag_unavailable", error_code="active_release_missing"),
        ),
    ):
        runtime = create_process_runtime()

    assert runtime.llm is llm
    assert runtime.rag_available is False
    assert runtime.rag_status == "rag_unavailable"
    assert runtime.rag_error_code == "active_release_missing"


def test_deep_agent_model_uses_validated_responses_profile(monkeypatch):
    """生产模型必须沿用 P0 验证过的无 reasoning Responses 配置。"""
    monkeypatch.setenv("DEEP_AGENT_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEP_AGENT_BASE_URL", "https://provider.example")
    monkeypatch.setenv("DEEP_AGENT_API_KEY", "test-only")
    configured = Mock(name="deep-agent-model")

    with patch("app.agent.worker.runtime.ChatOpenAI", return_value=configured) as model_cls:
        model = create_deep_agent_model()

    assert model is configured
    model_cls.assert_called_once_with(
        model="deepseek-v4-flash",
        base_url="https://provider.example",
        api_key="test-only",
        streaming=True,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={"effort": "none"},
    )


def test_rag_readiness_returns_release_identity_without_heavy_runtime_creation():
    """readiness 只解析轻量配置，不创建向量库或 embedding。"""
    config = SimpleNamespace(
        vector_db_dir="/tmp/release/chroma",
        release_id="mm_" + "a" * 20,
        embedding_config={"status": "ready"},
    )
    with (
        patch("app.agent.worker.runtime._load_rag_runtime_config", return_value=config),
        patch("app.agent.worker.runtime.Path.is_dir", return_value=True),
    ):
        readiness = inspect_rag_readiness()

    assert readiness == RagReadiness("available", release_id="mm_" + "a" * 20)


def test_rag_readiness_rejects_unavailable_embedding():
    """embedding resolver 非 ready 时，启动不能误报 RAG 可用。"""
    config = SimpleNamespace(
        vector_db_dir="/tmp/release/chroma",
        release_id="mm_" + "a" * 20,
        embedding_config={"status": "missing"},
    )
    with patch("app.agent.worker.runtime._load_rag_runtime_config", return_value=config):
        readiness = inspect_rag_readiness()

    assert readiness.status == "rag_unavailable"
    assert readiness.error_code == "active_release_invalid"


def test_rag_readiness_rejects_unsafe_release_identity():
    """release id 不符合版本格式时不能进入安全诊断日志。"""
    config = SimpleNamespace(
        vector_db_dir="/tmp/release/chroma",
        release_id="../../private-path",
        embedding_config={"status": "ready"},
    )
    with patch("app.agent.worker.runtime._load_rag_runtime_config", return_value=config):
        readiness = inspect_rag_readiness()

    assert readiness.status == "rag_unavailable"
    assert readiness.error_code == "active_release_invalid"


def test_rag_readiness_hides_invalid_release_details():
    """失败状态只暴露稳定错误码，不泄露异常正文。"""
    with patch(
        "app.agent.worker.runtime._load_rag_runtime_config",
        side_effect=ValueError("/secret/manifest.json contains a private detail"),
    ):
        readiness = inspect_rag_readiness()

    assert readiness.status == "rag_unavailable"
    assert readiness.error_code == "active_release_invalid"
    assert readiness.release_id is None


def test_slot_runtime_builds_graph_from_explicit_dependencies():
    """slot graph 应由传入 LLM、该 slot tools 与 checkpoint 共同构建。"""
    llm = Mock(name="llm")
    tools = [SimpleNamespace(name="causal_test")]
    graph = Mock(name="graph")
    checkpointer = Mock(name="checkpointer")
    resources = McpClientResources(
        client=Mock(name="client"),
        session=Mock(name="session"),
        tools=tools,
    )
    process_runtime = ProcessRuntime(llm=llm, rag_available=True)

    with (
        patch(
            "app.agent.worker.runtime.open_mcp_client_resources",
            new=AsyncMock(return_value=resources),
        ),
        patch(
            "app.agent.worker.runtime.build_checkpointer",
            return_value=checkpointer,
        ) as build_checkpointer,
        patch(
            "Agent.causal_agent.graph.create_graph_from_tools",
            return_value=graph,
        ) as create_graph,
    ):
        slot_runtime = asyncio.run(
            create_slot_runtime(
                process_runtime,
                AsyncExitStack(),
                Mock(name="checkpoint_pool"),
            )
        )

    build_checkpointer.assert_called_once()
    create_graph.assert_called_once_with(
        llm,
        tools,
        checkpointer,
        rag_available=True,
    )
    assert slot_runtime.llm is llm
    assert slot_runtime.mcp_resources is resources
    assert slot_runtime.mcp_tools is tools
    assert slot_runtime.graph is graph


def test_production_slot_reuses_process_graph_without_opening_stdio_mcp():
    """生产新路径复用进程级 HTTP pool/graph，不再创建 slot stdio session。"""
    llm = Mock(name="llm")
    graph = Mock(name="compiled-parent-graph")
    domain_tools = (SimpleNamespace(name="causal_pc"),)
    process_runtime = ProcessRuntime(
        llm=llm,
        rag_available=True,
        domain_tools=domain_tools,
        graph=graph,
    )

    with patch(
        "app.agent.worker.runtime.open_mcp_client_resources",
        new=AsyncMock(side_effect=AssertionError("stdio path must be unreachable")),
    ):
        slot_runtime = asyncio.run(
            create_slot_runtime(
                process_runtime,
                AsyncExitStack(),
                Mock(name="checkpoint_pool"),
            )
        )

    assert slot_runtime.graph is graph
    assert slot_runtime.mcp_resources is None
    assert slot_runtime.mcp_tools == list(domain_tools)
    assert slot_runtime.process_runtime is process_runtime


def test_slot_context_binds_job_lease_and_web_switch():
    """每次 slot invocation 都从 claim 结果构造可信 identity。"""
    from Agent.deep_agent import TrustedJobIdentity

    process_runtime = ProcessRuntime(
        llm=Mock(name="llm"),
        rag_available=True,
        algorithm_executor=Mock(name="executor"),
        filesystem_backend=Mock(name="backend"),
    )
    slot_runtime = SlotRuntime(
        llm=process_runtime.llm,
        process_runtime=process_runtime,
    )
    context = slot_runtime.build_run_context(
        job={
            "job_id": "00000000-0000-0000-0000-000000000301",
            "session_id": "00000000-0000-0000-0000-000000000302",
            "user_id": 7,
            "attempt_count": 2,
            "lease_epoch": 4,
            "input_file_hash": "input-sha",
            "web_search_enabled": True,
        },
        execution_guard=Mock(name="guard"),
        worker_id="worker-1",
    )

    assert isinstance(context.trusted_identity, TrustedJobIdentity)
    assert context.trusted_identity.attempt_count == 2
    assert context.trusted_identity.lease_epoch == 4
    assert context.web_search_enabled is True
    assert context.algorithm_executor is process_runtime.algorithm_executor


def test_drain_timeout_cancels_local_slot_without_terminal_job_mutation():
    """超时只取消本地 slot，未完成 Job 留给 stale recovery。"""
    from app.agent.worker.bootstrap import _run_slots_until_shutdown

    async def scenario() -> None:
        """在超时后验证 slot 被取消且没有业务终态写入。"""
        stop_event = asyncio.Event()
        local_job = asyncio.Event()

        async def running_slot() -> None:
            """模拟仍在执行的 slot。"""
            await local_job.wait()

        slot_task = asyncio.create_task(running_slot())
        stop_event.set()
        with patch("config.settings.settings.JOB_DRAIN_TIMEOUT_SECONDS", 0.01):
            await _run_slots_until_shutdown([slot_task], stop_event)

        assert slot_task.done()
        assert not local_job.is_set()

    asyncio.run(scenario())


def test_shutdown_waits_for_slots_before_timeout():
    """停止信号后，仍在运行的 slot 可以在时限内自然完成。"""
    from app.agent.worker.bootstrap import _run_slots_until_shutdown

    async def scenario() -> None:
        """在 drain 时限内验证 slot 可以自然结束。"""
        stop_event = asyncio.Event()
        finished = asyncio.Event()

        async def finishing_slot() -> None:
            """模拟可在 drain 窗口内完成的 slot。"""
            await asyncio.sleep(0.01)
            finished.set()

        slot_task = asyncio.create_task(finishing_slot())
        stop_event.set()
        with patch("config.settings.settings.JOB_DRAIN_TIMEOUT_SECONDS", 1):
            await _run_slots_until_shutdown([slot_task], stop_event)

        assert finished.is_set()

    asyncio.run(scenario())


def test_stop_event_prevents_idle_slot_from_claiming_more_jobs():
    """slot 进入 drain 后不再调用 claim_next_job。"""
    from app.agent.worker import bootstrap

    async def scenario() -> None:
        """在停止事件已设置时验证 slot 不再领取 Job。"""
        stop_event = asyncio.Event()
        stop_event.set()
        slot_runtime = SimpleNamespace(mcp_tools=[])
        process_runtime = ProcessRuntime(llm=Mock(name="llm"), rag_available=True)
        with (
            patch.object(bootstrap, "create_slot_runtime", new=AsyncMock(return_value=slot_runtime)),
            patch.object(bootstrap.job_service, "claim_next_job") as claim_next_job,
        ):
            await bootstrap.run_slot(1, Mock(name="checkpoint_pool"), process_runtime, stop_event)

        claim_next_job.assert_not_called()

    asyncio.run(scenario())


def test_production_runtime_waits_for_both_mcp_lanes_before_executor(monkeypatch):
    """单一 pool 的 start 完成两类成员握手后才允许创建执行器。"""

    order = []
    pool = SimpleNamespace(close=AsyncMock())

    async def start_pool():
        order.extend(["execute_ready", "control_ready"])

    pool.start = start_pool

    def build_executor(received_pool, **_kwargs):
        assert received_pool is pool
        assert order == ["execute_ready", "control_ready"]
        order.append("executor_created")
        return Mock(name="executor")

    @asynccontextmanager
    async def store_context():
        yield Mock(name="store")

    adapter = SimpleNamespace(raw_backend=Mock(name="backend"))
    monkeypatch.setenv("CAUSAL_MCP_SIGNING_KEY_CURRENT", "test-signing-key")
    with (
        patch("app.agent.worker.runtime.create_llm", return_value=Mock(name="llm")),
        patch("app.agent.worker.runtime.open_async_postgres_store", return_value=store_context()),
        patch("app.agent.worker.runtime.McpClientPoolConfig.from_env", return_value=Mock()),
        patch("app.agent.worker.runtime.McpClientPool", return_value=pool) as pool_cls,
        patch("app.agent.worker.runtime.McpAlgorithmExecutor", side_effect=build_executor),
        patch("app.agent.worker.runtime.build_default_adapters", return_value={"pc": adapter}),
        patch("app.agent.worker.runtime.build_default_registry", return_value=Mock(name="registry")),
        patch("app.agent.worker.runtime.build_algorithm_tools", return_value=()),
        patch("app.agent.worker.runtime.build_default_rag_evidence_tool", return_value=Mock()),
        patch("app.agent.worker.runtime.build_default_web_evidence_tool", return_value=Mock()),
        patch("app.agent.worker.runtime.inspect_rag_readiness", return_value=RagReadiness("rag_unavailable")),
        patch("app.agent.worker.runtime.create_deep_agent_model", return_value=Mock()),
        patch("app.agent.worker.runtime._required_positive_environment_integer", return_value=1000),
        patch("app.agent.worker.runtime.build_checkpointer", return_value=Mock()),
        patch("app.agent.worker.runtime.build_deep_agent", return_value=Mock()),
        patch("Agent.causal_agent.graph.build_deep_agent_parent_graph", return_value=Mock()),
    ):
        async def scenario():
            async with AsyncExitStack() as stack:
                return await initialize_production_runtime(Mock(), stack)

        runtime = asyncio.run(scenario())

    pool_cls.assert_called_once()
    assert runtime.mcp_pool is pool
    assert order == ["execute_ready", "control_ready", "executor_created"]
