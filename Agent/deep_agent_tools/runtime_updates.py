"""LangChain ToolRuntime identity resolution and monotonic State updates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
from typing import Annotated, Any, Literal

from langchain_core.tools import InjectedToolArg
from pydantic import Field

from .identity import (
    build_deep_agent_step_id,
    build_invocation_id,
    current_input_identity,
)
from .models import ActionAttempt, AteApplicabilityCheck, InvocationRecord, PublicDecision, canonical_json_bytes


_defer_algorithm_terminal = ContextVar("defer_algorithm_terminal", default=False)
_ate_check_sink: ContextVar[Callable[[str, AteApplicabilityCheck], None] | None] = ContextVar(
    "ate_check_sink", default=None,
)


@contextmanager
def capture_ate_checks(sink: Callable[[str, AteApplicabilityCheck], None]):
    """只在当前调度任务内收集程序已完成的检查，不写入模型参数或 State。"""
    token = _ate_check_sink.set(sink)
    try:
        yield
    finally:
        _ate_check_sink.reset(token)


def publish_ate_check(invocation_id: str, check: AteApplicabilityCheck) -> None:
    sink = _ate_check_sink.get()
    if sink is not None:
        sink(invocation_id, check)


@contextmanager
def defer_algorithm_terminal_events():
    """调度器拥有 deadline 时，工具只准备 Command，不提前发布终态。"""

    token = _defer_algorithm_terminal.set(True)
    try:
        yield
    finally:
        _defer_algorithm_terminal.reset(token)


def normalize_tool_status(status: str | None) -> str:
    """在公共事件与账本边界统一算法和工具状态。"""

    value = str(getattr(status, "value", status) or "").strip().lower()
    normalized = {
        "valid": "succeeded",
        "available": "succeeded",
        "no_results": "succeeded",
        "no_relevant_evidence": "succeeded",
        "disabled": "not_ready",
        "unavailable": "not_ready",
        "protocol_error": "failed",
        "invalid_input": "failed",
        "execution_failed": "failed",
        "cancelled": "canceled",
        "timeout": "timed_out",
    }.get(value, value)
    return normalized if normalized in {
        "succeeded", "failed", "timed_out", "canceled", "not_ready",
        "not_applicable", "skipped", "discarded",
    } else "failed"


def tool_lifecycle_event_key(
    *, invocation_id: str, attempt_count: int, retry_ordinal: int = 0,
    event_type: str,
) -> str:
    """实时、custom 与恢复兜底共用同一次执行的稳定事件键。"""

    digest = hashlib.sha256(canonical_json_bytes(
        [invocation_id, attempt_count, retry_ordinal, event_type]
    )).hexdigest()
    return f"deep-agent-tool:{digest}"


def tool_lifecycle_step_id(identity: RuntimeInvocationIdentity) -> str:
    """保存事件原属阶段，恢复时不使用新阶段重写旧事件。"""

    step_id = getattr(identity.runtime_context, "deep_agent_step_id", None)
    if isinstance(step_id, str) and len(step_id) == 24:
        return step_id
    trusted = identity.runtime_context.trusted_identity
    return build_deep_agent_step_id(
        job_id=trusted.job_id, attempt_count=int(trusted.attempt_count),
        task_id="deep_agent",
    )


def _tool_result_summary(status: str | None, safe_error_code: str | None) -> str:
    """把受控工具状态转换为用户可区分的简短文案。"""

    normalized_status = normalize_tool_status(status)
    normalized_code = str(safe_error_code or "").upper()
    if normalized_status == "succeeded":
        return "调用完成"
    if normalized_status == "not_applicable":
        return "不适用"
    if normalized_status == "skipped":
        return "未执行"
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
    retry_ordinal: int = 0,
) -> None:
    """从真实 Tool 边界发出并先持久化安全公共事件。"""

    trusted_identity = identity.runtime_context.trusted_identity
    invocation_id = build_invocation_id(
        job_id=trusted_identity.job_id,
        response_identity=identity.response_identity,
        provider_call_id=identity.provider_call_id,
    )
    event_key = tool_lifecycle_event_key(
        invocation_id=invocation_id,
        attempt_count=int(trusted_identity.attempt_count),
        retry_ordinal=retry_ordinal,
        event_type=event_type,
    )
    step_id = tool_lifecycle_step_id(identity)
    normalized_status = normalize_tool_status(status) if status is not None else None
    payload: dict[str, Any] = {
        "type": event_type,
        "step_id": step_id,
        "node_name": "deep_agent",
        "title": "执行 Deep Agent 分析",
        "attempt": int(trusted_identity.attempt_count),
        "tool_name": tool_name,
        "_event_key": event_key,
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
    """给内部校验 schema 增加 injected runtime，模型可见 schema 会过滤它。

    参数校验仍会收到 ``runtime``，因此这里把它标注为 ``InjectedToolArg``。
    否则 pydantic 无法为运行时对象生成 JSON Schema。
    """

    return type(
        f"{tool_name.title().replace('_', '')}RuntimeInput",
        (base_schema,),
        {
            "__annotations__": {
                "runtime": Annotated[tool_runtime_type, InjectedToolArg]
            },
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
        "succeeded", "failed", "timed_out", "not_ready", "canceled", "discarded",
        "not_applicable", "skipped",
    ],
    started_at: datetime,
    retry_ordinal: int = 0,
    result_ref: str | None = None,
    subject_ref: str | None = None,
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
        "not_applicable": "not_applicable",
        "skipped": "skipped",
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
        subject_ref=subject_ref,
        job_id=str(identity.runtime_context.trusted_identity.job_id),
        attempt_count=int(identity.runtime_context.trusted_identity.attempt_count),
        lease_epoch=int(identity.runtime_context.trusted_identity.lease_epoch),
        worker_id=identity.runtime_context.trusted_identity.worker_id,
        input_identity=current_input_identity(identity.runtime_context.trusted_identity),
        source_input_identity=identity.runtime_context.trusted_identity.source_input_identity(),
        analysis_dataset_id=(
            identity.runtime_context.trusted_identity.analysis_input.dataset_id
            if identity.runtime_context.trusted_identity.analysis_input is not None
            else None
        ),
        event_step_id=tool_lifecycle_step_id(identity),
        attempts={
            retry_ordinal: ActionAttempt(
                retry_ordinal=retry_ordinal,
                revision=2,
                status=attempt_status,
                started_at=started_at,
                # 容器与宿主时钟可能在校验前后小幅回拨；终态时间不得早于开始
                # 时间，否则账本契约会拒绝一条本该成功的调用。
                finished_at=max(datetime.now(timezone.utc), started_at),
                safe_error_code=safe_error_code,
            )
        },
    )


def build_pending_invocation(
    *, identity: RuntimeInvocationIdentity, tool_name: str,
) -> InvocationRecord:
    """模型响应节点之后、工具节点之前提交的调用登记。"""

    trusted = identity.runtime_context.trusted_identity
    invocation_id = build_invocation_id(
        job_id=trusted.job_id,
        response_identity=identity.response_identity,
        provider_call_id=identity.provider_call_id,
    )
    return InvocationRecord(
        invocation_id=invocation_id,
        response_identity=identity.response_identity,
        response_identity_source=identity.response_identity_source,
        provider_response_id=identity.provider_response_id,
        provider_call_id=identity.provider_call_id,
        tool_name=tool_name,
        final_status="pending",
        job_id=str(trusted.job_id),
        attempt_count=int(trusted.attempt_count),
        lease_epoch=int(trusted.lease_epoch),
        worker_id=trusted.worker_id,
        input_identity=current_input_identity(trusted),
        source_input_identity=trusted.source_input_identity(),
        analysis_dataset_id=(
            trusted.analysis_input.dataset_id
            if trusted.analysis_input is not None
            else None
        ),
        event_step_id=tool_lifecycle_step_id(identity),
        attempts={0: ActionAttempt(retry_ordinal=0, revision=0, status="queued")},
    )


async def algorithm_terminal_command(
    runtime: Any, *, identity: RuntimeInvocationIdentity, tool_name: str,
    result: Any, started_at: datetime,
) -> Any:
    """所有普通算法终态共用 Command、账本和公共事件转换。"""

    await identity.runtime_context.ensure_active()
    status = normalize_tool_status(result.status)
    code = result.diagnostics.safe_error_code
    code = getattr(code, "value", code)
    terminal = build_terminal_invocation(
        identity=identity, tool_name=tool_name, attempt_status=status,
        started_at=started_at, result_ref=result.result_ref, safe_error_code=code,
    )
    command = build_tool_command(
        identity=identity, tool_name=tool_name,
        payload=result.model_dump(mode="json"), ledger_record=terminal,
        state_updates={"algorithm_results": {result.result_ref: result}},
    )
    if not _defer_algorithm_terminal.get():
        await emit_tool_lifecycle_event(
            runtime, event_type="tool_call_result", identity=identity,
            tool_name=tool_name, status=status, safe_error_code=code,
        )
    return command


def build_tool_command(
    *,
    identity: RuntimeInvocationIdentity,
    tool_name: str,
    payload: Any,
    ledger_record: InvocationRecord,
    state_updates: Mapping[str, Any] | None = None,
) -> Any:
    """将受控 Tool 输出、Ledger 和领域结果在同一 graph step 写回 State。"""

    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    update = dict(state_updates or {})
    update["action_ledger"] = {ledger_record.invocation_id: ledger_record}
    update["messages"] = [
        ToolMessage(
            content=canonical_json_bytes(payload).decode("utf-8"),
            tool_call_id=identity.provider_call_id,
            name=tool_name,
            status=("success" if ledger_record.final_status == "succeeded" else "error"),
        )
    ]
    return Command(update=update)
