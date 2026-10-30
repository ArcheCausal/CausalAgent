"""Deep Agent invocation 级运行上下文。

State 会进入 checkpoint，而本模块中的对象只在一次 graph invocation 内存活。
因此 execution guard、executor、backend 和服务连接只能通过这里注入，不能被
模型参数构造，也不能写进 ``ProjectDeepAgentState``。
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from types import MappingProxyType
from uuid import UUID

from Agent.deep_agent_tools.models import McpInvocationContext
from Agent.measurement_semantics import normalize_semantic_kinds


def _canonical_uuid(value: str | UUID, *, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a canonical UUID value") from exc


@dataclass(eq=False)
class FrozenInputRef:
    """本次 invocation 内可刷新的冻结输入引用。

    正常情况下 Job 冻结的文件在生命周期内不变，该引用只是 Job claim 时快照的副本。
    当用户在会话里用自然语言切换到另一个分析上下文时，服务端会在同一个 MySQL 事务里
    重写 Job 的冻结文件快照；本引用用于让 MCP 输入摘要、适配器校验、FinalizationGate
    的 provenance 校验和 Deep Agent execution scope 跟随同一次写入变化，不必重建整个
    AgentRunContext。它只在一次 invocation 内存活，绝不进入 State 或 checkpoint。
    """

    user_file_id: int | None
    object_id: int | None
    content_hash: str
    filename: str | None = None
    revision: int = 0

    @property
    def digest(self) -> str:
        """返回当前冻结内容的稳定摘要，作为 MCP 与 provenance 的输入身份。"""
        return self.content_hash

    def apply_snapshot(
        self,
        *,
        user_file_id: int | None,
        object_id: int | None,
        content_hash: str,
        filename: str | None,
    ) -> bool:
        """按服务端已提交的新冻结快照刷新引用；内容未变化时返回 False。"""

        normalized_hash = str(content_hash or "")
        if not normalized_hash:
            raise ValueError("frozen input content hash must not be blank")
        unchanged = (
            self.user_file_id == user_file_id
            and self.object_id == object_id
            and self.content_hash == normalized_hash
            and self.filename == filename
        )
        if unchanged:
            return False
        self.user_file_id = user_file_id
        self.object_id = object_id
        self.content_hash = normalized_hash
        self.filename = filename
        self.revision += 1
        return True


@dataclass(eq=False)
class AnalysisInputRef:
    """一次 Job 当前选中的已校验分析数据集身份。"""

    dataset_id: int | None = None
    object_id: int | None = None
    content_hash: str | None = None
    filename: str | None = None
    research_identity: str | None = None
    _semantic_kinds: tuple[tuple[str, str], ...] | None = field(
        default=None, init=False, repr=False,
    )

    @property
    def semantic_kinds(self) -> Mapping[str, str] | None:
        if self._semantic_kinds is None:
            return None
        return MappingProxyType(dict(self._semantic_kinds))

    def bind(
        self, *, dataset_id: int, object_id: int, content_hash: str, filename: str,
        semantic_kinds: Mapping[str, str] | None = None,
        research_identity: str | None = None,
    ) -> None:
        if dataset_id <= 0 or object_id <= 0:
            raise ValueError("prepared dataset and object IDs must be positive")
        if not content_hash or content_hash != content_hash.strip():
            raise ValueError("prepared dataset hash must be a non-blank string")
        if not filename or filename != filename.strip():
            raise ValueError("prepared dataset filename must be a non-blank string")
        snapshot = (
            tuple(normalize_semantic_kinds(semantic_kinds).items())
            if semantic_kinds is not None else None
        )
        self.dataset_id = dataset_id
        self.object_id = object_id
        self.content_hash = content_hash
        self.filename = filename
        self.research_identity = research_identity
        self._semantic_kinds = snapshot


@dataclass(frozen=True)
class TrustedJobIdentity:
    """由 worker claim/lease 绑定的可信 Job 身份。

    ``input_identity`` 是冻结输入快照的摘要，不是文件正文。它可以进入内部
    command/provenance，但不会出现在模型可见的参数 schema 中。
    """

    job_id: str | UUID
    session_id: str | UUID
    user_id: int
    attempt_count: int
    lease_epoch: int
    worker_id: str
    input_identity: str
    input_snapshot_digest: str | None = None
    # 可刷新的冻结输入引用；存在时 input_snapshot_digest 跟随它变化。
    frozen_input: FrozenInputRef | None = None
    # 与用户原始文件身份分离；只在独立校验和 MySQL 绑定成功后写入。
    analysis_input: AnalysisInputRef | None = None

    def source_input_identity(self) -> str:
        """返回用户最初冻结的上传文件摘要。"""
        if self.frozen_input is not None:
            return self.frozen_input.digest
        return self.input_snapshot_digest or self.input_identity

    def current_input_identity(self) -> str:
        """返回当前有效的冻结输入摘要。

        worker claim 之后如果通过自然语言切换分析上下文并重新冻结文件，本方法返回
        服务端最新提交的 hash，使 MCP 上下文、适配器校验和 provenance 校验保持一致。
        """

        if self.analysis_input is not None and self.analysis_input.content_hash is not None:
            return self.analysis_input.content_hash
        if self.frozen_input is not None:
            return self.frozen_input.digest
        return self.input_snapshot_digest or self.input_identity

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _canonical_uuid(self.job_id, field_name="job_id"))
        object.__setattr__(
            self,
            "session_id",
            _canonical_uuid(self.session_id, field_name="session_id"),
        )
        if self.user_id <= 0:
            raise ValueError("user_id must be positive")
        if self.attempt_count < 0 or self.lease_epoch < 0:
            raise ValueError("attempt_count and lease_epoch must be non-negative")
        for name in ("worker_id", "input_identity"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a non-blank string")
        if self.input_snapshot_digest is not None and (
            not self.input_snapshot_digest
            or self.input_snapshot_digest != self.input_snapshot_digest.strip()
        ):
            raise ValueError("input_snapshot_digest must be blank or a trimmed string")
        if self.input_snapshot_digest is not None and (
            self.input_snapshot_digest != self.input_identity
        ):
            raise ValueError(
                "input_snapshot_digest must equal input_identity for the frozen input hash"
            )

    def to_mcp_context(
        self,
        *,
        invocation_id: str,
        key_id: str = "current",
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> McpInvocationContext:
        """把可信身份投影为内部 MCP executor 使用的短时上下文。"""

        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        issued_at = now or datetime.now(timezone.utc)
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        digest = self.current_input_identity()
        return McpInvocationContext(
            research_identity=self.analysis_input.research_identity if self.analysis_input is not None else None,
            invocation_id=invocation_id,
            job_id=self.job_id,
            session_id=self.session_id,
            user_id=self.user_id,
            attempt_count=self.attempt_count,
            lease_epoch=self.lease_epoch,
            worker_id=self.worker_id,
            input_snapshot_digest=digest,
            source_input_identity=self.source_input_identity(),
            analysis_dataset_id=(
                self.analysis_input.dataset_id
                if self.analysis_input is not None
                else None
            ),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=ttl_seconds),
            key_id=key_id,
        )


@dataclass(frozen=True)
class AgentRunContext:
    """一次 Deep Agent 调用的只读依赖集合。

    这些字段故意使用协议/对象类型而不使用可序列化 State 类型。调用方应在
    worker claim 后创建它，并在 graph invocation 结束时释放连接与 semaphore。
    """

    execution_guard: Any | None
    trusted_identity: TrustedJobIdentity | None = None
    algorithm_executor: Any | None = None
    rag_executor: Any | None = None
    web_executor: Any | None = None
    filesystem_backend: Any | None = None
    # ``None`` means the caller did not provide a per-Job decision.  Production
    # worker invocations always bind a concrete bool; keeping the compatibility
    # default tri-state prevents isolated Tool tests from silently changing
    # their explicitly constructed WebEvidenceTool behavior.
    web_search_enabled: bool | None = None
    rag_available: bool | None = None
    rag_release_id: str | None = None
    rag_error_code: str | None = None
    # 仅由 worker 注入的持久化事件边界；绝不进入 State/checkpoint。
    event_sink: Any | None = None
    # 父图 Deep Agent task 的 opaque step ID；只用于把 child tool 事件关联到
    # 已持久化的父阶段，不进入 State/checkpoint。
    deep_agent_step_id: str | None = None
    # OpenSandbox client and data-preparation graph are process-scoped services.
    # They are injected by the worker and never stored in State/checkpoints.
    sandbox_provider: Any | None = None
    data_processing_agent: Any | None = None
    algorithm_registry: Any | None = None
    # 由父图按当前分析目标与算法输入要求计算，只在本次调用内用于方案版本。
    data_processing_goal_identity: str | None = None

    async def ensure_active(self) -> None:
        """在跨边界调用前复用已有 JobExecutionGuard 的资格检查。"""

        guard = self.execution_guard
        if guard is None:
            return
        ensure_active = getattr(guard, "ensure_active", None)
        if ensure_active is None:
            return
        result = ensure_active()
        if inspect.isawaitable(result):
            await result

    async def check_after_call(self) -> None:
        """在跨边界调用返回后复用已有 JobExecutionGuard 的资格检查。"""

        guard = self.execution_guard
        if guard is None:
            return
        check_after_call = getattr(guard, "check_after_call", None)
        if check_after_call is None:
            return
        result = check_after_call()
        if inspect.isawaitable(result):
            await result

    def assert_state_safe(self, state: object) -> None:
        """拒绝把本 runtime context 直接放入 State。"""

        if state is self or (
            self.algorithm_executor is not None and state is self.algorithm_executor
        ):
            raise TypeError("runtime context objects must not enter graph state")
        if isinstance(state, dict):
            forbidden = {
                "execution_guard",
                "algorithm_executor",
                "rag_executor",
                "web_executor",
                "filesystem_backend",
                "trusted_identity",
                "event_sink",
                "deep_agent_step_id",
                "sandbox_provider",
                "data_processing_agent",
                "algorithm_registry",
                "analysis_input",
            }
            leaked = forbidden.intersection(state)
            if leaked:
                raise TypeError(
                    "runtime-only fields must not enter graph state: "
                    + ", ".join(sorted(leaked))
                )
