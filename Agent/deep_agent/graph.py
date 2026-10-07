"""Deep Agents 图的惰性组装和父子 State 投影。

P2-U 测试不要求真实 deepagents/langgraph 包已安装。只有显式调用
``build_deep_agent`` 时才加载这些依赖；缺包会返回稳定的 dependency error，
不会静默退回一个假图。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import importlib
import inspect
from typing import Any

from Agent.deep_agent_tools.identity import new_message_execution_id
from Agent.deep_agent_tools.models import FinalAnalysisDecision
from Agent.deep_agent_tools.registry import AlgorithmRegistry
from Agent.deep_agent_tools.dependency_dispatch import (
    build_algorithm_dependency_middleware,
)

from .context import AgentRunContext
from .memory import MEMORY_PATHS
from .profile import DeepAgentBudget, DeepAgentProfile, build_filesystem_permissions
from .prompts import build_deep_agent_system_prompt
from .state import ProjectDeepAgentState


class DeepAgentDependencyError(RuntimeError):
    """真实 Deep Agent graph 依赖或 API 不可用。"""


@dataclass(frozen=True)
class DeepAgentGraphConfig:
    model_name: str = "deepseek-v4-flash"
    context_window_tokens: int | None = None
    system_prompt: str = build_deep_agent_system_prompt()
    budget: DeepAgentBudget | None = None
    harness_profile_key: str = "openai"

    def profile(self) -> DeepAgentProfile:
        budget = self.budget or DeepAgentBudget.from_environment()
        if self.context_window_tokens is None:
            return DeepAgentProfile.from_environment(
                model_name=self.model_name,
                budget=budget,
            )
        return DeepAgentProfile(
            model_name=self.model_name,
            context_window_tokens=self.context_window_tokens,
            budget=budget,
        )


def build_tool_strategy() -> Any:
    """惰性创建 ``ToolStrategy(FinalAnalysisDecision)``。"""

    try:
        from langchain.agents.structured_output import ToolStrategy
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise DeepAgentDependencyError("LangChain ToolStrategy is not installed") from exc
    return ToolStrategy(FinalAnalysisDecision)


def build_budget_middlewares(profile: DeepAgentProfile) -> list[Any]:
    """构造官方模型/Tool limit middleware；缺失时 fail closed。"""

    try:
        module = importlib.import_module("langchain.agents.middleware")
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - 真实版本门禁
        raise DeepAgentDependencyError("LangChain call-limit middleware is unavailable") from exc
    classes = {
        name: getattr(module, name, None)
        for name in ("ModelCallLimitMiddleware", "ToolCallLimitMiddleware")
    }
    if any(candidate is None for candidate in classes.values()):  # pragma: no cover
        raise DeepAgentDependencyError("LangChain call-limit middleware is unavailable")

    def construct(cls: type, limit: int) -> Any:
        parameters = inspect.signature(cls).parameters
        kwargs: dict[str, Any] = {}
        if "run_limit" in parameters:
            kwargs["run_limit"] = limit
        elif "limit" in parameters:
            kwargs["limit"] = limit
        else:  # pragma: no cover - 真实版本门禁
            raise DeepAgentDependencyError("call-limit middleware has no run limit parameter")
        if "exit_behavior" in parameters:
            kwargs["exit_behavior"] = "end"
        return cls(**kwargs)

    return [
        construct(classes["ModelCallLimitMiddleware"], profile.budget.model_call_limit),
        construct(classes["ToolCallLimitMiddleware"], profile.budget.tool_call_limit),
    ]


def _configure_model_profile(model: Any, profile: DeepAgentProfile) -> Any:
    """把冻结的 context window 写入模型 profile，并立即回读验证。"""

    if isinstance(model, str):
        raise DeepAgentDependencyError(
            "Deep Agent requires a preconfigured model object so its profile can be verified"
        )
    actual_name = getattr(model, "model_name", None) or getattr(model, "model", None)
    if actual_name is not None and str(actual_name) != profile.model_name:
        raise DeepAgentDependencyError("configured model id does not match the Deep Agent profile")

    model_profile = dict(getattr(model, "profile", None) or {})
    model_profile.update(profile.model_profile())
    copier = getattr(model, "model_copy", None)
    if callable(copier):
        configured_model = copier(update={"profile": model_profile})
    else:
        try:
            setattr(model, "profile", model_profile)
        except (AttributeError, TypeError, ValueError) as exc:
            raise DeepAgentDependencyError("model profile cannot be configured") from exc
        configured_model = model

    configured_profile = getattr(configured_model, "profile", None)
    if not isinstance(configured_profile, dict) or configured_profile.get(
        "max_input_tokens"
    ) != profile.context_window_tokens:
        raise DeepAgentDependencyError("model max_input_tokens profile was not applied")
    return configured_model


def _build_summarization_middleware(model: Any, profile: DeepAgentProfile) -> Any:
    """显式替换 Deep Agents 默认摘要参数，避免 profile 只停留在配置对象。"""

    try:
        from langchain.agents.middleware import SummarizationMiddleware
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - 真实版本门禁
        raise DeepAgentDependencyError("LangChain summarization middleware is unavailable") from exc
    return SummarizationMiddleware(
        model=model,
        trigger=("fraction", profile.summary_trigger_ratio),
        keep=("fraction", profile.summary_keep_ratio),
        trim_tokens_to_summarize=profile.trim_tokens_to_summarize,
    )


@dataclass(frozen=True)
class VerifiedProviderResponseId:
    """由当前响应适配层显式产生的、尚未猜测字段含义的 provider ID 标记。"""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value != self.value.strip():
            raise ValueError("verified provider response ID must be a non-blank trimmed string")


ProviderResponseIdExtractor = Callable[
    [Any, Any], VerifiedProviderResponseId | None
]


def _extract_verified_provider_response_id(
    state: Any,
    runtime: Any,
    extractor: ProviderResponseIdExtractor | None = None,
) -> str | None:
    """只接受当前响应适配层提供的显式 identity marker。

    P0 DeepSeek spike 完成前，默认没有 extractor，因此 State 中上一轮残留的
    ``provider_response_id``、``AIMessage.id``、``tool_calls``、``call_id`` 和
    metadata 都不会被当作当前响应的 provider identity。适配层必须在当前
    response scope 内返回 ``VerifiedProviderResponseId``；返回普通字符串或
    ``None`` 都会 fail closed。
    """

    if extractor is None:
        return None
    marker = extractor(state, runtime)
    if not isinstance(marker, VerifiedProviderResponseId):
        return None
    return marker.value


def _build_message_identity_middleware(
    *, provider_response_id_extractor: ProviderResponseIdExtractor | None = None
) -> Any:
    """在每次模型响应后持久化 Tool 调用使用的 response fallback identity。"""

    try:
        from langchain.agents.middleware import AgentMiddleware
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise DeepAgentDependencyError("LangChain AgentMiddleware is not installed") from exc

    class MessageExecutionIdentityMiddleware(AgentMiddleware):
        def after_model(self, state: Any, runtime: Any) -> dict[str, Any]:
            # fallback 每轮都重新生成；provider ID 缺失时显式写 None，清除
            # 可能来自上一轮的旧值，而不是让 State reducer 沿用 stale identity。
            return {
                "message_execution_id": new_message_execution_id(),
                "provider_response_id": _extract_verified_provider_response_id(
                    state, runtime, provider_response_id_extractor
                ),
            }

    return MessageExecutionIdentityMiddleware()


def _register_harness_profile(profile_key: str) -> None:
    """关闭 0.7.13 自动注入的 general-purpose subagent 和 ``task`` 工具。"""

    if not profile_key.strip():
        raise DeepAgentDependencyError("harness profile key must be non-blank")
    try:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - 真实版本门禁
        raise DeepAgentDependencyError("Deep Agents harness profile API is unavailable") from exc
    register_harness_profile(
        profile_key,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )


def _official_filesystem_permissions() -> list[Any]:
    """把项目内可测试规则转换为 Deep Agents 真实权限对象。"""

    try:
        from deepagents.middleware import FilesystemPermission
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - 真实版本门禁
        raise DeepAgentDependencyError("Deep Agents filesystem permissions are unavailable") from exc
    return [
        FilesystemPermission(
            operations=list(rule.operations),
            paths=list(rule.paths),
            mode=rule.mode,
        )
        for rule in build_filesystem_permissions()
    ]


def deep_agent_invoke_config(profile: DeepAgentProfile) -> dict[str, int]:
    """返回调用 Deep Agent 时交给 LangGraph 的递归保险上限。"""

    return {"recursion_limit": profile.budget.recursion_limit}


def _materialize_domain_tools(domain_tools: Iterable[Any]) -> list[Any]:
    """把项目 Tool 描述对象转换为真正使用 ToolRuntime 的 LangChain Tool。"""

    materialized: list[Any] = []
    for domain_tool in domain_tools:
        factory = getattr(domain_tool, "to_langchain_tool", None)
        materialized.append(factory() if callable(factory) else domain_tool)
    return materialized


def build_deep_agent(
    *,
    model: Any,
    domain_tools: Iterable[Any],
    backend: Any,
    store: Any,
    config: DeepAgentGraphConfig | None = None,
    checkpointer: Any | None = None,
    registry: AlgorithmRegistry | None = None,
) -> Any:
    """按技术设计组装真实 Deep Agent graph。

    真实版本号/API 兼容由 P0 clean install 和后续 integration gate 证明；本
    函数不在导入时吞掉错误，也不提供 fake graph 作为生产 fallback。
    """

    active_config = config or DeepAgentGraphConfig()
    try:
        from deepagents import create_deep_agent
        from deepagents.middleware import FilesystemMiddleware
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise DeepAgentDependencyError("Deep Agents is not installed") from exc

    try:
        from langchain.agents.structured_output import ToolStrategy
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise DeepAgentDependencyError("LangChain ToolStrategy is not installed") from exc

    profile = active_config.profile()
    configured_model = _configure_model_profile(model, profile)
    permissions = _official_filesystem_permissions()
    _register_harness_profile(active_config.harness_profile_key)
    filesystem_middleware = FilesystemMiddleware(
        backend=backend,
        tools=["read_file", "edit_file"],
        _permissions=permissions,
    )
    middleware = [
        filesystem_middleware,
        _build_summarization_middleware(configured_model, profile),
        *build_budget_middlewares(profile),
        _build_message_identity_middleware(),
    ]
    if registry is not None:
        middleware.append(
            build_algorithm_dependency_middleware(
                registry=registry,
                max_parallel_tools_per_job=profile.budget.max_parallel_tools_per_job,
                timeout_seconds=profile.budget.tool_node_timeout_seconds,
            )
        )
    kwargs: dict[str, Any] = {
        "model": configured_model,
        "tools": _materialize_domain_tools(domain_tools),
        "subagents": [],
        "memory": list(MEMORY_PATHS),
        "system_prompt": active_config.system_prompt,
        "response_format": ToolStrategy(
            FinalAnalysisDecision
        ),
        "backend": backend,
        "store": store,
        "middleware": middleware,
        "permissions": permissions,
        "checkpointer": checkpointer,
        "state_schema": ProjectDeepAgentState,
        "context_schema": AgentRunContext,
    }
    try:
        return create_deep_agent(**kwargs)
    except TypeError as exc:  # pragma: no cover - 真实版本门禁
        raise DeepAgentDependencyError("installed Deep Agents API does not match the frozen profile") from exc
