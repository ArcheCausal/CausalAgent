"""Deep Agent 主线的状态、运行上下文和可选图组装入口。

本包的纯状态/权限/预算模块不依赖 Deep Agents 或 LangGraph。真实图组装
通过 :func:`build_deep_agent` 惰性加载可选依赖，避免 P2-U 的 fake executor
测试把真实运行时依赖伪装成已安装或已验收。
"""

from .context import AgentRunContext, TrustedJobIdentity
from .finalization import (
    FinalizationGate,
    FinalizationRetryController,
    StructuredResponseError,
    validate_structured_response,
)
from .graph import (
    DeepAgentDependencyError,
    DeepAgentGraphConfig,
    build_deep_agent,
    build_tool_strategy,
    deep_agent_invoke_config,
)
from .memory import (
    CompositeBackend,
    MemoryConflictError,
    MemoryPermissionError,
    StateBackend,
    StoreBackend,
    build_in_memory_backend,
    initialize_memory_files,
    memory_namespace_for_user,
    trusted_memory_namespace,
)
from .profile import (
    DEFAULT_FILESYSTEM_PERMISSIONS,
    DeepAgentBudget,
    DeepAgentProfile,
    FilesystemPermission,
    build_filesystem_permissions,
    is_filesystem_write_allowed,
)
from .state import (
    DeepAgentState,
    ParentStateUpdate,
    ProjectDeepAgentState,
    from_deep_agent_output,
    initial_deep_agent_state,
    retry_deep_agent_from_checkpoint,
    to_deep_agent_input,
)

__all__ = [
    "AgentRunContext",
    "DEFAULT_FILESYSTEM_PERMISSIONS",
    "DeepAgentBudget",
    "DeepAgentDependencyError",
    "DeepAgentGraphConfig",
    "DeepAgentProfile",
    "DeepAgentState",
    "FilesystemPermission",
    "FinalizationRetryController",
    "FinalizationGate",
    "CompositeBackend",
    "MemoryConflictError",
    "MemoryPermissionError",
    "ParentStateUpdate",
    "ProjectDeepAgentState",
    "StateBackend",
    "StoreBackend",
    "StructuredResponseError",
    "TrustedJobIdentity",
    "build_filesystem_permissions",
    "build_deep_agent",
    "build_in_memory_backend",
    "build_tool_strategy",
    "deep_agent_invoke_config",
    "from_deep_agent_output",
    "initial_deep_agent_state",
    "retry_deep_agent_from_checkpoint",
    "initialize_memory_files",
    "is_filesystem_write_allowed",
    "memory_namespace_for_user",
    "to_deep_agent_input",
    "trusted_memory_namespace",
    "validate_structured_response",
]
