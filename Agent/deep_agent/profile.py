"""Deep Agent 工具裁剪、filesystem 权限和有界预算。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FilesystemPermission:
    """与 Deep Agents first-match 规则等价的最小权限描述。"""

    operations: tuple[str, ...]
    paths: tuple[str, ...]
    mode: str

    def __post_init__(self) -> None:
        if self.mode not in {"allow", "deny"}:
            raise ValueError("filesystem permission mode must be allow or deny")
        if not self.operations or not self.paths:
            raise ValueError("filesystem permission must declare operations and paths")
        if any(not operation.strip() for operation in self.operations):
            raise ValueError("filesystem permission operations must be non-blank")
        if any(not path.startswith("/") for path in self.paths):
            raise ValueError("filesystem permission paths must be absolute virtual paths")

    def matches(self, operation: str, path: str) -> bool:
        normalized = normalize_virtual_path(path)
        if operation not in self.operations:
            return False
        for pattern in self.paths:
            if pattern == "/**":
                return True
            if pattern.endswith("/**"):
                prefix = pattern[:-3].rstrip("/") or "/"
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    return True
            elif normalized == pattern:
                return True
        return False


def normalize_virtual_path(path: str) -> str:
    """规范化虚拟路径并拒绝路径穿越。"""

    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("virtual path must be absolute")
    parts: list[str] = []
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("virtual path must not contain parent traversal")
        parts.append(part)
    return "/" + "/".join(parts)


DEFAULT_FILESYSTEM_PERMISSIONS: tuple[FilesystemPermission, ...] = (
    FilesystemPermission(
        operations=("write",),
        paths=(
            "/memories/preferences.md",
            "/memories/research_background.md",
        ),
        mode="allow",
    ),
    FilesystemPermission(
        operations=("write",),
        paths=("/**",),
        mode="deny",
    ),
)


def build_filesystem_permissions() -> tuple[FilesystemPermission, ...]:
    """返回每次构造均独立、顺序固定的权限规则。"""

    return tuple(DEFAULT_FILESYSTEM_PERMISSIONS)


def is_filesystem_write_allowed(
    path: str,
    *,
    permissions: Iterable[FilesystemPermission] = DEFAULT_FILESYSTEM_PERMISSIONS,
) -> bool:
    """按 first-match 规则判断模型是否能写虚拟文件。"""

    normalized = normalize_virtual_path(path)
    for permission in permissions:
        if permission.matches("write", normalized):
            return permission.mode == "allow"
    # 0.7.13 的未命中规则默认允许，所以调用方必须传入兜底 deny；这里选择
    # fail closed，防止新路径在权限规则遗漏时意外可写。
    return False


@dataclass(frozen=True)
class DeepAgentBudget:
    """P2-U 固定的系统级有界预算。"""

    model_call_limit: int = 12
    tool_call_limit: int = 8
    recursion_limit: int = 32
    tool_node_timeout_seconds: int = 720
    finalization_retry_limit: int = 1
    max_parallel_tools_per_job: int = 2

    def __post_init__(self) -> None:
        for name in (
            "model_call_limit",
            "tool_call_limit",
            "recursion_limit",
            "tool_node_timeout_seconds",
            "max_parallel_tools_per_job",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.finalization_retry_limit < 0:
            raise ValueError("finalization_retry_limit must be non-negative")

    @classmethod
    def from_environment(cls) -> "DeepAgentBudget":
        def read(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or raw == "":
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc

        return cls(
            model_call_limit=read("DEEP_AGENT_MODEL_CALL_LIMIT", 12),
            tool_call_limit=read("DEEP_AGENT_TOOL_CALL_LIMIT", 8),
            recursion_limit=read("DEEP_AGENT_RECURSION_LIMIT", 32),
            tool_node_timeout_seconds=read("DEEP_AGENT_TOOL_NODE_TIMEOUT_SECONDS", 720),
            finalization_retry_limit=read("DEEP_AGENT_FINALIZATION_RETRY_LIMIT", 1),
            max_parallel_tools_per_job=read("DEEP_AGENT_MAX_PARALLEL_TOOLS_PER_JOB", 2),
        )


@dataclass(frozen=True)
class DeepAgentProfile:
    """模型 profile 与摘要阈值的显式快照。"""

    model_name: str
    context_window_tokens: int
    summary_trigger_ratio: float = 0.85
    summary_keep_ratio: float = 0.10
    trim_tokens_to_summarize: int = 4000
    filesystem_tools: tuple[str, ...] = ("read_file", "edit_file")
    budget: DeepAgentBudget = DeepAgentBudget()

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("model_name must be non-blank")
        if self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        if not 0 < self.summary_trigger_ratio < 1:
            raise ValueError("summary_trigger_ratio must be between 0 and 1")
        if not 0 < self.summary_keep_ratio < 1:
            raise ValueError("summary_keep_ratio must be between 0 and 1")
        if self.trim_tokens_to_summarize <= 0:
            raise ValueError("trim_tokens_to_summarize must be positive")
        if self.filesystem_tools != ("read_file", "edit_file"):
            raise ValueError("P2-U only enables read_file and edit_file")

    def model_profile(self) -> dict[str, int]:
        return {"max_input_tokens": self.context_window_tokens}

    @classmethod
    def from_environment(
        cls,
        *,
        model_name: str | None = None,
        budget: DeepAgentBudget | None = None,
    ) -> "DeepAgentProfile":
        """要求部署显式提供 context window，避免模型 profile 静默回退。"""

        raw = os.getenv("DEEP_AGENT_CONTEXT_WINDOW_TOKENS")
        if raw is None or raw == "":
            raise ValueError("DEEP_AGENT_CONTEXT_WINDOW_TOKENS must be configured")
        try:
            context_window_tokens = int(raw)
        except ValueError as exc:
            raise ValueError("DEEP_AGENT_CONTEXT_WINDOW_TOKENS must be an integer") from exc
        return cls(
            model_name=model_name or os.getenv("DEEP_AGENT_MODEL", "deepseek-v4-flash"),
            context_window_tokens=context_window_tokens,
            budget=budget or DeepAgentBudget.from_environment(),
        )


def expected_model_tool_names(domain_tool_names: Iterable[str]) -> tuple[str, ...]:
    """生成启动快照应包含的最小工具集合。"""

    return tuple(sorted({*domain_tool_names, "read_file", "edit_file"}))
