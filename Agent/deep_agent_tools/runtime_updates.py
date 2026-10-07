"""LangChain ToolRuntime identity resolution and monotonic State updates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
from typing import Any, Literal

from pydantic import Field

from .identity import (
    build_deep_agent_step_id,
    build_invocation_id,
    current_input_identity,
)
from .models import ActionAttempt, InvocationRecord, PublicDecision, canonical_json_bytes


def _tool_result_summary(status: str | None, safe_error_code: str | None) -> str:
    """把受控工具状态转换为用户可区分的简短文案。"""

    normalized_status = str(status or "").lower()
    normalized_code = str(safe_error_code or "").upper()
    if normalized_status == "succeeded":
        return "调用完成"
    if normalized_status in {"canceled", "cancelled"}:
        return "调用已取消"
    if normalized_status in {"timed_out", "timeout"}:
        return "调用超时"
    if normalized_code == "WEB_SEARCH_DISABLED":
        return "未启用"
    if normalized_status == "not_ready" or normalized_code in {
        "RAG_RETRIEVAL_UNAVAILABLE",
        "WEB_SEARCH_UNAVAILABLE",
    }:
        return "暂不可用"
    return "调用失败"


def scope_evidence_payload(
    payload: Mapping[str, Any],
    *,
    invocation_id: str,
) -> dict[str, Any]:
    """把查询相关 evidence ref 限定到稳定 Tool invocation。

    RAG 的 ``E1`` 等编号只在一次检索结果内唯一，Web 结果的相关性分数和
    ``fetched_at`` 也属于单次检索。并行调用若直接复用来源级 reference，会让
    reducer 把两个不同查询的结果误判为同一条不可变 evidence。
    """

    scoped_payload = dict(payload)
    raw_evidence = payload.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise ValueError("evidence payload must contain a list")

    scoped_evidence: list[dict[str, Any]] = []
    scoped_by_ref: dict[str, dict[str, Any]] = {}
    for raw_item in raw_evidence:
        if not isinstance(raw_item, Mapping):
            raise ValueError("evidence payload item must be a mapping")
        base_ref = str(raw_item.get("evidence_ref") or "").strip()
        if not base_ref:
            raise ValueError("evidence payload item is missing evidence_ref")
        scoped_ref = f"{base_ref}:invocation:{invocation_id}"
        item = dict(raw_item)
        item["evidence_ref"] = scoped_ref
        scoped_evidence.append(item)
        scoped_by_ref[scoped_ref] = item

    scoped_payload["evidence"] = scoped_evidence
    scoped_payload["evidence_by_ref"] = scoped_by_ref
    return scoped_payload


async def emit_tool_lifecycle_event(
    runtime: Any,
    *,
    event_type: str,
    identity: RuntimeInvocationIdentity,
    tool_name: str,
    status: str | None = None,
    safe_error_code: str | None = None,
) -> None:
    """从真实 Tool 边界发出并先持久化安全公共事件。"""

    trusted_identity = identity.runtime_context.trusted_identity
    invocation_id = build_invocation_id(
        job_id=trusted_identity.job_id,
        response_identity=identity.response_identity,
        provider_call_id=identity.provider_call_id,
    )
    event_key = hashlib.sha256(
        f"{invocation_id}:{event_type}".encode("utf-8")
    ).hexdigest()
    step_id = getattr(identity.runtime_context, "deep_agent_step_id", None)
    if not isinstance(step_id, str) or len(step_id) != 24:
        step_id = build_deep_agent_step_id(
            job_id=trusted_identity.job_id,
            attempt_count=int(trusted_identity.attempt_count),
            task_id="deep_agent",
        )
    normalized_status = status
    payload: dict[str, Any] = {
        "type": event_type,
        "step_id": step_id,
        "node_name": "deep_agent",
        "title": "执行 Deep Agent 分析",
        "attempt": int(trusted_identity.attempt_count),
        "tool_name": tool_name,
        "_event_key": f"deep-agent-tool:{event_key}",
    }
    if event_type == "tool_call_start":
        payload["argument_keys"] = []
    elif normalized_status is not None:
        payload["summary"] = _tool_result_summary(
            normalized_status,
            safe_error_code,
        )
        payload["status"] = normalized_status
    if safe_error_code:
        payload["safe_error_code"] = safe_error_code

    # worker 传入的 sink 负责在真实外部调用前完成 fenced 落库；没有 worker
    # sink 的 isolated graph/tool 测试仍可只走 LangGraph custom stream。
    sink = getattr(identity.runtime_context, "event_sink", None)
    if callable(sink):
        result = sink(payload)
        if inspect.isawaitable(result):
            await result

    # 保留 custom stream 供实时适配器使用。它使用相同的 payload/key，后续
    # OrderedEventWriter 会按 event_key 幂等，不会产生第二条公共事件。
    writer = getattr(runtime, "stream_writer", None)
    if callable(writer):
        writer(payload)


async def emit_public_decision_event(
    runtime: Any,
    *,
    identity: RuntimeInvocationIdentity,
    tool_name: str,
    public_decision: Any,
    decision_kind: Literal["algorithm", "evidence"] = "algorithm",
) -> bool:
    """校验并发布同一 Tool Call 携带的公开工具选择说明。"""

    try:
        decision = PublicDecision.model_validate(public_decision)
    except Exception:
        return False
    trusted_identity = identity.runtime_context.trusted_identity
    invocation_id = build_invocation_id(
        job_id=trusted_identity.job_id,
        response_identity=identity.response_identity,
        provider_call_id=identity.provider_call_id,
    )
    step_id = getattr(identity.runtime_context, "deep_agent_step_id", None)
    if not isinstance(step_id, str) or len(step_id) != 24:
        step_id = build_deep_agent_step_id(
            job_id=trusted_identity.job_id,
            attempt_count=int(trusted_identity.attempt_count),
            task_id="deep_agent",
        )
    payload = {
        "type": "decision",
        "decision_kind": decision_kind,
        "step_id": step_id,
        "node_name": "deep_agent",
        "title": "执行 Deep Agent 分析",
        "attempt": int(trusted_identity.attempt_count),
        "tool_name": tool_name,
        "summary": decision.summary,
        "_event_key": f"deep-agent-tool:{invocation_id}:decision",
    }
    sink = getattr(identity.runtime_context, "event_sink", None)
    if callable(sink):
        result = sink(payload)
        if inspect.isawaitable(result):
            await result
    writer = getattr(runtime, "stream_writer", None)
    if callable(writer):
        writer(payload)
    return True


@dataclass(frozen=True)
class RuntimeInvocationIdentity:
    """一次 LangGraph tool call 的可信内部身份。"""

    provider_call_id: str
    response_identity: str
    response_identity_source: Literal[
        "provider_response_id", "message_execution_id"
    ]
    provider_response_id: str | None
    runtime_context: Any


def with_tool_runtime_schema(
    base_schema: type,
    *,
    tool_name: str,
    tool_runtime_type: type,
) -> type:
    """给内部校验 schema 增加 injected runtime，模型可见 schema 会过滤它。"""

    return type(
        f"{tool_name.title().replace('_', '')}RuntimeInput",
        (base_schema,),
        {
            "__annotations__": {"runtime": tool_runtime_type},
            "__module__": base_schema.__module__,
            "model_config": dict(base_schema.model_config)
            | {"arbitrary_types_allowed": True},
            "runtime": Field(exclude=True),
        },
    )


def resolve_runtime_invocation(
    runtime: Any,
    *,
    expected_context: Any | None = None,
) -> RuntimeInvocationIdentity:
    """从 ToolRuntime 提取模型不可伪造的调用、响应和 Job runtime 身份。"""

    provider_call_id = str(getattr(runtime, "tool_call_id", "") or "").strip()
    if not provider_call_id:
        raise RuntimeError("ToolRuntime.tool_call_id is required")

    state = getattr(runtime, "state", None)
    if not isinstance(state, Mapping):
        raise RuntimeError("ToolRuntime.state must be a mapping")

    provider_response_id = str(state.get("provider_response_id") or "").strip() or None
    message_execution_id = str(state.get("message_execution_id") or "").strip() or None
    if provider_response_id is not None:
        response_identity = provider_response_id
        response_identity_source: Literal[
            "provider_response_id", "message_execution_id"
        ] = "provider_response_id"
    elif message_execution_id is not None:
        response_identity = message_execution_id
        response_identity_source = "message_execution_id"
    else:
        raise RuntimeError(
            "graph state must persist provider_response_id or message_execution_id "
            "before executing tools"
        )

    runtime_context = getattr(runtime, "context", None)
    if runtime_context is None:
        raise RuntimeError("ToolRuntime.context is required")
    if expected_context is not None and (
        runtime_context.trusted_identity != expected_context.trusted_identity
    ):
        raise RuntimeError("ToolRuntime context does not match the registered tool context")

    return RuntimeInvocationIdentity(
        provider_call_id=provider_call_id,
        response_identity=response_identity,
        response_identity_source=response_identity_source,
        provider_response_id=provider_response_id,
        runtime_context=runtime_context,
    )


def build_terminal_invocation(
    *,
    identity: RuntimeInvocationIdentity,
    tool_name: str,
    attempt_status: Literal[
        "succeeded", "failed", "timed_out", "not_ready", "canceled", "discarded"
    ],
    started_at: datetime,
    retry_ordinal: int = 0,
    result_ref: str | None = None,
    safe_error_code: str | None = None,
) -> InvocationRecord:
    """生成只写 terminal revision 的单调 Ledger 更新。"""

    invocation_id = build_invocation_id(
        job_id=identity.runtime_context.trusted_identity.job_id,
        response_identity=identity.response_identity,
        provider_call_id=identity.provider_call_id,
    )
    final_status = {
        "succeeded": "succeeded",
        "failed": "failed",
        "timed_out": "timed_out",
        "not_ready": "not_ready",
        "canceled": "canceled",
        "discarded": "discarded",
    }[attempt_status]
    return InvocationRecord(
        invocation_id=invocation_id,
        response_identity=identity.response_identity,
        response_identity_source=identity.response_identity_source,
        provider_response_id=identity.provider_response_id,
        provider_call_id=identity.provider_call_id,
        tool_name=tool_name,
        final_status=final_status,
        result_ref=result_ref,
        job_id=str(identity.runtime_context.trusted_identity.job_id),
        attempt_count=int(identity.runtime_context.trusted_identity.attempt_count),
        lease_epoch=int(identity.runtime_context.trusted_identity.lease_epoch),
        worker_id=identity.runtime_context.trusted_identity.worker_id,
        input_identity=current_input_identity(identity.runtime_context.trusted_identity),
        attempts={
            retry_ordinal: ActionAttempt(
                retry_ordinal=retry_ordinal,
                revision=2,
                status=attempt_status,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                safe_error_code=safe_error_code,
            )
        },
    )


def build_tool_command(
    *,
    identity: RuntimeInvocationIdentity,
    tool_name: str,
    payload: Any,
    ledger_record: InvocationRecord,
    state_updates: Mapping[str, Any] | None = None,
) -> Any:
    """将受控 Tool 输出、Ledger 和领域结果在同一 graph step 写回 State。"""

    try:
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise RuntimeError("LangGraph runtime dependencies are not installed") from exc

    update = dict(state_updates or {})
    update["action_ledger"] = {ledger_record.invocation_id: ledger_record}
    update["messages"] = [
        ToolMessage(
            content=canonical_json_bytes(payload).decode("utf-8"),
            tool_call_id=identity.provider_call_id,
            name=tool_name,
        )
    ]
    return Command(update=update)
