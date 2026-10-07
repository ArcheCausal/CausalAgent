"""构建 worker 进程级与 slot 级运行时依赖。"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

from langchain_openai import ChatOpenAI

from Agent.causal_agent.postgres_checkpointer import build_checkpointer
from Agent.deep_agent.context import AgentRunContext, FrozenInputRef, TrustedJobIdentity
from Agent.deep_agent.graph import (
    DeepAgentGraphConfig,
    build_deep_agent,
)
from Agent.deep_agent.memory import build_official_backend, trusted_memory_namespace
from Agent.deep_agent.postgres_store import open_async_postgres_store
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.mcp_algorithm_executor import McpAlgorithmExecutor
from Agent.deep_agent_tools.rag_evidence_tool import build_default_rag_evidence_tool
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.deep_agent_tools.web_evidence_tool import build_default_web_evidence_tool
from app.agent.worker.mcp_client_pool import McpClientPool, McpClientPoolConfig
from config.settings import settings
from observability.logging_runtime import log_event


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MCP_SERVER_PATH = PROJECT_ROOT / "Agent" / "CausalAgentMCP" / "mcp_server.py"
RELEASE_ID_PATTERN = re.compile(r"^mm_[0-9a-f]{20}(?:[0-9a-f]{44})?$")


@dataclass(frozen=True)
class RagReadiness:
    """保存 worker 启动时的轻量 RAG release 检查结果。"""

    status: str
    release_id: str | None = None
    error_code: str | None = None

    @property
    def available(self) -> bool:
        """只有明确的 available 状态才允许启用 RAG。"""
        return self.status == "available"


@dataclass(frozen=True)
class ProcessRuntime:
    """保存 worker 进程内共享、且不依赖 slot 生命周期的对象。"""

    llm: ChatOpenAI
    rag_available: bool
    rag_status: str | None = None
    rag_release_id: str | None = None
    rag_error_code: str | None = None
    mcp_pool: Any | None = None
    algorithm_executor: Any | None = None
    algorithm_registry: Any | None = None
    domain_tools: tuple[Any, ...] = ()
    filesystem_backend: Any | None = None
    store: Any | None = None
    checkpointer: Any | None = None
    graph: Any | None = None

    def __post_init__(self) -> None:
        """为旧的显式构造调用补齐稳定的 RAG 状态名称。"""
        if self.rag_status is None:
            object.__setattr__(
                self,
                "rag_status",
                "available" if self.rag_available else "rag_unavailable",
            )


@dataclass(frozen=True)
class McpClientResources:
    """保存由 slot 的 ``AsyncExitStack`` 管理的 MCP 资源。"""

    client: Any
    session: Any
    tools: list[Any]


@dataclass(frozen=True)
class SlotRuntime:
    """显式保存一个 slot 的 LLM、MCP 生命周期资源、tools 与 graph。"""

    llm: ChatOpenAI
    mcp_resources: McpClientResources | None = None
    mcp_tools: list[Any] = field(default_factory=list)
    graph: Any = None
    process_runtime: ProcessRuntime | None = None

    def build_run_context(
        self,
        *,
        job: dict[str, Any],
        execution_guard: Any,
        worker_id: str,
        event_sink: Any | None = None,
    ) -> AgentRunContext:
        """从已 claim 的 Job 构造一次 invocation 的可信 runtime context。"""

        process_runtime = self.process_runtime
        if process_runtime is None:
            raise RuntimeError("slot process runtime is unavailable")
        job_id = str(job["job_id"])
        input_identity = str(job.get("input_file_hash") or f"job-input:{job_id}")
        # 冻结输入引用在本 invocation 内可刷新：用户用自然语言切换分析上下文时，
        # 服务端在同一事务里重写 Job 冻结快照，MCP 摘要与 provenance 校验随后
        # 读取这里的新 hash，不需要重建整个 run context。
        frozen_input = FrozenInputRef(
            user_file_id=(
                int(job["input_user_file_id"]) if job.get("input_user_file_id") else None
            ),
            object_id=int(job["input_object_id"]) if job.get("input_object_id") else None,
            content_hash=input_identity,
            filename=job.get("input_filename"),
        )
        identity = TrustedJobIdentity(
            job_id=job_id,
            session_id=str(job["session_id"]),
            user_id=int(job["user_id"]),
            attempt_count=int(job["attempt_count"]),
            lease_epoch=int(job.get("lease_epoch") or 0),
            worker_id=worker_id,
            input_identity=input_identity,
            input_snapshot_digest=str(job.get("input_file_hash") or input_identity),
            frozen_input=frozen_input,
        )
        return AgentRunContext(
            execution_guard=execution_guard,
            trusted_identity=identity,
            algorithm_executor=process_runtime.algorithm_executor,
            filesystem_backend=process_runtime.filesystem_backend,
            web_search_enabled=bool(job.get("web_search_enabled")),
            rag_available=process_runtime.rag_available,
            rag_release_id=process_runtime.rag_release_id,
            rag_error_code=process_runtime.rag_error_code,
            event_sink=event_sink,
        )


def create_llm() -> ChatOpenAI:
    """根据当前配置创建 LLM；配置缺失时立即失败。"""
    if not all([settings.MODEL, settings.BASE_URL, settings.API_KEY]):
        raise RuntimeError("LLM 配置不完整，无法初始化")

    llm = ChatOpenAI(
        model=settings.MODEL,
        base_url=settings.BASE_URL,
        api_key=settings.API_KEY,
        streaming=False,
    )
    return llm


def _load_rag_runtime_config() -> Any:
    """读取 active release 的轻量 Runtime 配置，不初始化向量库。"""
    from Agent.knowledge_base.rag_runtime import RagRuntimeConfig

    return RagRuntimeConfig.from_environment()


def inspect_rag_readiness() -> RagReadiness:
    """检查 active pointer、manifest、embedding 指纹和索引目录。"""
    try:
        config = _load_rag_runtime_config()
        if config.embedding_config.get("status") != "ready":
            raise ValueError("active release embedding is unavailable")
        release_id = str(config.release_id)
        if RELEASE_ID_PATTERN.fullmatch(release_id) is None:
            raise ValueError("active release id is invalid")
        vector_db_dir = Path(config.vector_db_dir)
        if not vector_db_dir.is_dir():
            raise FileNotFoundError("active release vector directory is unavailable")
    except (FileNotFoundError, ValueError, TypeError, KeyError) as exc:
        try:
            from Agent.knowledge_base.rag_runtime import recover_invalid_active_release

            recovered = recover_invalid_active_release()
        except Exception:
            recovered = None
        if isinstance(recovered, dict) and recovered.get("status") == "rolled_back":
            fallback = recovered.get("active") or {}
            readiness = RagReadiness(
                status="rag_unavailable",
                release_id=str(fallback.get("release_id") or fallback.get("index_version") or "") or None,
                error_code="active_release_invalid",
            )
            return readiness
        if isinstance(exc, FileNotFoundError):
            readiness = RagReadiness(
                status="rag_unavailable",
                error_code="active_release_missing",
            )
        else:
            readiness = RagReadiness(
                status="rag_unavailable",
                error_code="active_release_invalid",
            )
    except ImportError:
        readiness = RagReadiness(
            status="rag_unavailable",
            error_code="rag_dependency_missing",
        )
    except OSError:
        readiness = RagReadiness(
            status="rag_unavailable",
            error_code="active_release_invalid",
        )
    except Exception:
        readiness = RagReadiness(
            status="rag_unavailable",
            error_code="active_release_check_failed",
        )
    else:
        readiness = RagReadiness(
            status="available",
            release_id=release_id,
        )

    return readiness


def check_rag_availability() -> bool:
    """兼容旧调用方，返回 active release 是否可用。"""
    return inspect_rag_readiness().available


def create_process_runtime() -> ProcessRuntime:
    """创建一次进程级运行时，消除对 ``app.agent.core`` 全局变量的依赖。"""
    readiness = inspect_rag_readiness()
    return ProcessRuntime(
        llm=create_llm(),
        rag_available=readiness.available,
        rag_status=readiness.status,
        rag_release_id=readiness.release_id,
        rag_error_code=readiness.error_code,
    )


def _required_positive_environment_integer(name: str) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        raise RuntimeError(f"{name} must be configured")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def create_deep_agent_model() -> ChatOpenAI:
    """创建带 Responses API 和显式 profile 的 Deep Agent 模型。"""

    model_name = os.getenv("DEEP_AGENT_MODEL", "deepseek-v4-flash").strip()
    base_url = (os.getenv("DEEP_AGENT_BASE_URL") or settings.BASE_URL or "").strip()
    api_key = (os.getenv("DEEP_AGENT_API_KEY") or settings.API_KEY or "").strip()
    if not model_name or not base_url or not api_key:
        raise RuntimeError("Deep Agent 模型配置不完整，无法初始化")
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        streaming=True,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={"effort": "none"},
    )


async def initialize_production_runtime(
    checkpoint_pool: Any,
    process_stack: AsyncExitStack,
) -> ProcessRuntime:
    """按计划的 fail-fast 顺序创建进程级 Store、MCP pool 和 compiled graphs。"""

    # 外层旧图仍负责 fold/report；新内层使用独立、显式的 Deep Agent model。
    outer_llm = create_llm()
    store = await process_stack.enter_async_context(open_async_postgres_store())

    mcp_config = McpClientPoolConfig.from_env()
    mcp_pool = McpClientPool(mcp_config)
    process_stack.push_async_callback(mcp_pool.close)
    await mcp_pool.start()

    signing_key = (os.getenv("CAUSAL_MCP_SIGNING_KEY_CURRENT") or "").strip()
    if not signing_key:
        raise RuntimeError("CAUSAL_MCP_SIGNING_KEY_CURRENT is required")
    algorithm_executor = McpAlgorithmExecutor(
        mcp_pool,
        signing_key=signing_key,
    )
    adapters = build_default_adapters(
        executor=algorithm_executor,
        raw_backend=build_official_backend(
            store=store,
            namespace=trusted_memory_namespace,
        ),
    )
    registry = build_default_registry(adapters)
    readiness = inspect_rag_readiness()
    domain_tools = (
        *build_algorithm_tools(registry),
        build_default_rag_evidence_tool(readiness=readiness),
        build_default_web_evidence_tool(web_search_enabled=True),
    )

    deep_model = create_deep_agent_model()
    context_window_tokens = _required_positive_environment_integer(
        "DEEP_AGENT_CONTEXT_WINDOW_TOKENS"
    )
    filesystem_backend = adapters[next(iter(adapters))].raw_backend
    checkpointer = build_checkpointer(checkpoint_pool)
    deep_graph = build_deep_agent(
        model=deep_model,
        domain_tools=domain_tools,
        backend=filesystem_backend,
        store=store,
        config=DeepAgentGraphConfig(
            model_name=os.getenv("DEEP_AGENT_MODEL", "deepseek-v4-flash"),
            context_window_tokens=context_window_tokens,
        ),
        # Deep Agent 使用与父图相同的 PostgreSQL saver，但以稳定 child
        # thread_id 隔离 checkpoint namespace；父图只保存 child 引用和最终投影。
        checkpointer=checkpointer,
        registry=registry,
    )
    from Agent.causal_agent.graph import build_deep_agent_parent_graph

    parent_graph = build_deep_agent_parent_graph(
        llm=outer_llm,
        deep_agent=deep_graph,
        registry=registry,
        checkpointer=checkpointer,
        store=store,
    )
    return ProcessRuntime(
        llm=outer_llm,
        rag_available=readiness.available,
        rag_status=readiness.status,
        rag_release_id=readiness.release_id,
        rag_error_code=readiness.error_code,
        mcp_pool=mcp_pool,
        algorithm_executor=algorithm_executor,
        algorithm_registry=registry,
        domain_tools=tuple(domain_tools),
        filesystem_backend=filesystem_backend,
        store=store,
        checkpointer=checkpointer,
        graph=parent_graph,
    )


async def open_mcp_client_resources(
    process_stack: AsyncExitStack,
) -> McpClientResources:
    """为一个 slot 打开持久 MCP session 并加载 LangChain tools。"""
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools
    except ImportError as exc:
        raise RuntimeError(
            "缺少 langchain-mcp-adapters，无法初始化 LangChain MCP adapter。"
        ) from exc

    client = MultiServerMCPClient(
        {
            "causal": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(MCP_SERVER_PATH)],
            }
        }
    )
    session = await process_stack.enter_async_context(client.session("causal"))
    tools = await load_mcp_tools(session)
    return McpClientResources(client=client, session=session, tools=tools)


async def create_slot_runtime(
    process_runtime: ProcessRuntime,
    process_stack: AsyncExitStack,
    checkpoint_pool: Any,
) -> SlotRuntime:
    """创建 slot invocation 视图；新运行时复用进程级 pool/compiled graph。"""
    if process_runtime.graph is not None:
        return SlotRuntime(
            llm=process_runtime.llm,
            mcp_resources=None,
            mcp_tools=list(process_runtime.domain_tools),
            graph=process_runtime.graph,
            process_runtime=process_runtime,
        )

    from Agent.causal_agent.graph import create_graph_from_tools

    mcp_resources = await open_mcp_client_resources(process_stack)
    checkpointer = build_checkpointer(checkpoint_pool)
    graph = create_graph_from_tools(
        process_runtime.llm,
        mcp_resources.tools,
        checkpointer,
        rag_available=process_runtime.rag_available,
    )
    return SlotRuntime(
        llm=process_runtime.llm,
        mcp_resources=mcp_resources,
        mcp_tools=mcp_resources.tools,
        graph=graph,
        process_runtime=process_runtime,
    )
