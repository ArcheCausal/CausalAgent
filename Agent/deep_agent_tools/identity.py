"""Deep Agent function call 的稳定身份规则。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any
from uuid import UUID, uuid4, uuid5

from .models import McpInvocationContext, canonical_json_bytes


# 固定项目命名空间，不是 secret；改变它会破坏 checkpoint replay 的身份稳定性。
CAUSAL_INVOCATION_NAMESPACE = UUID("5f4b8f6b-0f3b-4c37-9e40-9d18a97a2e6f")
DEEP_AGENT_RUN_NAMESPACE = UUID("7c1b3a10-5f2e-4e75-8e8c-1e9f8d4f1a2b")


@dataclass(frozen=True)
class ResponseIdentity:
    """供应商 response 身份或已持久化的本地 fallback。"""

    value: str
    source: str


def _non_blank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")
    return value


def new_message_execution_id() -> str:
    """为模型节点生成需在 checkpoint 提交前持久化的本地 fallback。"""

    return str(uuid4())


def current_input_identity(identity: Any) -> str:
    """读取可信身份当前有效的冻结输入摘要。

    优先使用 invocation 内可刷新的引用；对只提供固定摘要的兼容身份对象回退到旧字段，
    因此调用方不需要区分两种身份实现。
    """

    resolver = getattr(identity, "current_input_identity", None)
    if callable(resolver):
        return str(resolver())
    return str(
        getattr(identity, "input_snapshot_digest", None)
        or getattr(identity, "input_identity", "")
    )


def resolve_response_identity(
    *, provider_response_id: str | None, message_execution_id: str | None
) -> ResponseIdentity:
    """优先采用供应商 response.id，否则采用本地持久化 execution id。

    本函数不会按消息下标或静态字符串推导 fallback；调用方必须提供那个
    将随 checkpoint 一起保存的本地身份。
    """

    if provider_response_id is not None and provider_response_id.strip():
        return ResponseIdentity(
            value=_non_blank(provider_response_id, field_name="provider_response_id"),
            source="provider_response_id",
        )
    if message_execution_id is not None and message_execution_id.strip():
        return ResponseIdentity(
            value=_non_blank(message_execution_id, field_name="message_execution_id"),
            source="message_execution_id",
        )
    raise ValueError(
        "provider_response_id or persisted message_execution_id is required"
    )


def build_logical_call_key(
    *, job_id: str | UUID, response_identity: str, provider_call_id: str
) -> str:
    """构造规范中用于 UUIDv5 的逻辑调用键。"""

    if isinstance(job_id, UUID):
        canonical_job_id = str(job_id)
    else:
        if not isinstance(job_id, str) or not job_id or job_id != job_id.strip():
            raise ValueError("job_id must be a UUID string without surrounding whitespace")
        try:
            canonical_job_id = str(UUID(job_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("job_id must be a UUID string") from exc
    return ":".join(
        (
            canonical_job_id,
            _non_blank(response_identity, field_name="response_identity"),
            _non_blank(provider_call_id, field_name="provider_call_id"),
        )
    )


def build_invocation_id(
    *, job_id: str | UUID, response_identity: str, provider_call_id: str
) -> str:
    """按 ``uuid5(namespace, job_id:response_identity:provider_call_id)`` 生成 ID。"""

    return str(
        uuid5(
            CAUSAL_INVOCATION_NAMESPACE,
            build_logical_call_key(
                job_id=job_id,
                response_identity=response_identity,
                provider_call_id=provider_call_id,
            ),
        )
    )


def build_deep_agent_run_id(*, job_id: str | UUID) -> str:
    """为同一 Job 生成跨 worker 重启稳定的 Deep Agent child thread ID。"""

    if isinstance(job_id, UUID):
        canonical_job_id = str(job_id)
    else:
        try:
            canonical_job_id = str(UUID(job_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("job_id must be a UUID string") from exc
    return f"deep-agent:{uuid5(DEEP_AGENT_RUN_NAMESPACE, canonical_job_id)}"


def build_deep_agent_step_id(
    *, job_id: str | UUID, attempt_count: int, task_id: str
) -> str:
    """为父图 Deep Agent task 生成与工具事件共享的 opaque step ID。"""

    if attempt_count < 0:
        raise ValueError("attempt_count must be non-negative")
    payload = {
        "job_id": str(job_id),
        "attempt_count": int(attempt_count),
        "task_id": _non_blank(task_id, field_name="task_id"),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:24]


def build_deep_agent_execution_scope(
    *,
    job_id: str | UUID,
    attempt_count: int,
    lease_epoch: int,
    input_identity: str,
) -> str:
    """生成绑定当前 Job attempt/lease/输入快照的 child checkpoint scope。"""

    if attempt_count < 0 or lease_epoch < 0:
        raise ValueError("attempt_count and lease_epoch must be non-negative")
    canonical_job_id = str(UUID(str(job_id)))
    payload = {
        "job_id": canonical_job_id,
        "attempt_count": int(attempt_count),
        "lease_epoch": int(lease_epoch),
        "input_identity": _non_blank(input_identity, field_name="input_identity"),
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"deep-agent-scope:{digest}"


def build_result_ref(*, invocation_id: str | UUID, result_index: int = 0) -> str:
    """生成不暴露裸 Job ID 或供应商 response ID 的结果引用。"""

    if result_index < 0:
        raise ValueError("result_index must be non-negative")
    if isinstance(invocation_id, UUID):
        canonical_invocation_id = str(invocation_id)
    else:
        try:
            canonical_invocation_id = str(UUID(invocation_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("invocation_id must be a UUID string") from exc
    return f"{canonical_invocation_id}:{result_index}"


def canonical_mcp_context_payload(context: McpInvocationContext) -> bytes:
    """生成供未来 HMAC-SHA256 使用的稳定上下文 bytes。"""

    return canonical_json_bytes(context.model_dump(mode="json"))


# 兼容调用方更直观的别名；它们共享同一实现和命名空间。
invocation_id_for_call = build_invocation_id
result_ref_for_invocation = build_result_ref

