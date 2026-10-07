"""按 AlgorithmSpec requires/produces 对同轮 Tool calls 做依赖调度。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from Agent.execution_control import JobExecutionRevoked

from .error_codes import SafeErrorCode
from .models import AlgorithmResult
from .registry import AlgorithmRegistry, UnknownCapabilityError


class DependencyPlanningError(ValueError):
    """未知 Tool、重复 call id、缺失 artifact 或环。"""

    def __init__(self, message: str, *, safe_error_code: SafeErrorCode | str) -> None:
        super().__init__(message)
        self.safe_error_code = safe_error_code


@dataclass(frozen=True)
class ToolCallRequest:
    """模型同一响应中的一个独立 function call。"""

    call_id: str
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    capability_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("call_id", "tool_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a non-blank string")
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True)
class PlannedToolCall:
    request: ToolCallRequest
    capability_id: str
    requires: frozenset[str]
    produces: frozenset[str]
    timeout_seconds: int
    concurrency_key: str
    default_concurrency: int


@dataclass(frozen=True)
class DependencyPlan:
    """拓扑排序后的分层计划；每个 call 都保留一次。"""

    batches: tuple[tuple[PlannedToolCall, ...], ...]
    dependencies: Mapping[str, frozenset[str]]
    initial_artifacts: frozenset[str]
    concurrency_limits: Mapping[str, int] = field(default_factory=dict)

    @property
    def calls(self) -> tuple[PlannedToolCall, ...]:
        return tuple(call for batch in self.batches for call in batch)


@dataclass(frozen=True)
class ToolExecutionOutcome:
    request: ToolCallRequest
    status: str
    value: Any = None
    safe_error_code: SafeErrorCode | str | None = None
    published_artifacts: frozenset[str] = frozenset()


def _resolve_entry(registry: AlgorithmRegistry, request: ToolCallRequest):
    try:
        entry = registry.get_by_tool_name(request.tool_name)
    except UnknownCapabilityError as exc:
        raise DependencyPlanningError(
            f"unknown tool: {request.tool_name}",
            safe_error_code=SafeErrorCode.UNKNOWN_CAPABILITY,
        ) from exc
    if request.capability_id is not None and request.capability_id != entry.spec.capability_id:
        raise DependencyPlanningError(
            f"tool/capability mismatch: {request.tool_name}",
            safe_error_code=SafeErrorCode.UNKNOWN_CAPABILITY,
        )
    return entry


def build_dependency_plan(
    requests: list[ToolCallRequest] | tuple[ToolCallRequest, ...],
    *,
    registry: AlgorithmRegistry,
    initial_artifacts: set[str] | frozenset[str] = frozenset(),
) -> DependencyPlan:
    """构建 DAG；同轮独立调用进入同一 batch，有依赖调用按层进入后续 batch。"""

    calls = list(requests)
    by_id: dict[str, PlannedToolCall] = {}
    concurrency_limits: dict[str, int] = {}
    for request in calls:
        if request.call_id in by_id:
            raise DependencyPlanningError(
                f"duplicate tool call id: {request.call_id}",
                safe_error_code=SafeErrorCode.DUPLICATE_INVOCATION,
            )
        entry = _resolve_entry(registry, request)
        by_id[request.call_id] = PlannedToolCall(
            request=request,
            capability_id=entry.spec.capability_id,
            requires=frozenset(entry.spec.requires),
            produces=frozenset(entry.spec.produces),
            timeout_seconds=entry.spec.default_timeout_seconds,
            concurrency_key=entry.spec.concurrency_key,
            default_concurrency=entry.spec.default_concurrency,
        )
        # 一个 concurrency_key 代表共享资源。若同一计划中有多个 Spec
        # 复用该资源，所有 Spec 的限制都必须成立，因此取最严格的值；
        # 这里没有额外的算法默认值。
        concurrency_limits[entry.spec.concurrency_key] = min(
            concurrency_limits.get(
                entry.spec.concurrency_key,
                entry.spec.default_concurrency,
            ),
            entry.spec.default_concurrency,
        )

    available = set(initial_artifacts)
    producers: dict[str, list[str]] = defaultdict(list)
    for call in by_id.values():
        for artifact in call.produces:
            producers[artifact].append(call.request.call_id)

    dependencies: dict[str, set[str]] = {call_id: set() for call_id in by_id}
    for call_id, call in by_id.items():
        for artifact in call.requires:
            if artifact in available:
                continue
            candidates = [candidate for candidate in producers.get(artifact, []) if candidate != call_id]
            if not candidates:
                raise DependencyPlanningError(
                    f"artifact is not ready: {artifact}",
                    safe_error_code=SafeErrorCode.ALGORITHM_NOT_READY,
                )
            dependencies[call_id].update(candidates)

    remaining = set(by_id)
    batches: list[tuple[PlannedToolCall, ...]] = []
    completed: set[str] = set()
    while remaining:
        ready = sorted(
            call_id
            for call_id in remaining
            if dependencies[call_id].issubset(completed)
        )
        if not ready:
            raise DependencyPlanningError(
                "tool dependency graph contains a cycle",
                safe_error_code=SafeErrorCode.ALGORITHM_NOT_READY,
            )
        batch = tuple(by_id[call_id] for call_id in ready)
        batches.append(batch)
        completed.update(ready)
        remaining.difference_update(ready)

    return DependencyPlan(
        batches=tuple(batches),
        dependencies={key: frozenset(value) for key, value in dependencies.items()},
        initial_artifacts=frozenset(initial_artifacts),
        concurrency_limits=concurrency_limits,
    )


async def execute_dependency_plan(
    plan: DependencyPlan,
    execute: Callable[[ToolCallRequest], Awaitable[Any]],
    *,
    max_parallel_tools_per_job: int = 2,
    timeout_seconds: float = 720,
    max_parallel_by_concurrency_key: Mapping[str, int] | None = None,
) -> tuple[ToolExecutionOutcome, ...]:
    """执行分层计划。

    同批调用使用 ``gather`` 且收集异常，不因兄弟失败互相取消；依赖某个失败
    调用的节点返回 ``not_ready``，但仍保留为结果，供 Agent 重规划和 Ledger
    记录使用。
    """

    if max_parallel_tools_per_job <= 0 or timeout_seconds <= 0:
        raise ValueError("dependency execution limits must be positive")
    per_key_limits = dict(max_parallel_by_concurrency_key or {})
    if any(limit <= 0 for limit in per_key_limits.values()):
        raise ValueError("concurrency-key limits must be positive")
    job_semaphore = asyncio.Semaphore(max_parallel_tools_per_job)
    key_semaphores: dict[str, asyncio.Semaphore] = {}
    calls_by_id = {
        call.request.call_id: call
        for call in plan.calls
    }
    key_limits: dict[str, int] = {}
    for call in plan.calls:
        spec_limit = plan.concurrency_limits.get(
            call.concurrency_key,
            call.default_concurrency,
        )
        # ``max_parallel_by_concurrency_key`` 保留为兼容性参数，但它只是
        # Job 级资源 ceiling，不能提高 AlgorithmSpec 的默认并发。
        job_key_limit = per_key_limits.get(
            call.concurrency_key,
            max_parallel_tools_per_job,
        )
        effective_limit = min(
            call.default_concurrency,
            spec_limit,
            max_parallel_tools_per_job,
            job_key_limit,
        )
        key_limits[call.concurrency_key] = min(
            key_limits.get(call.concurrency_key, effective_limit),
            effective_limit,
        )
    outcomes: dict[str, ToolExecutionOutcome] = {}
    for batch in plan.batches:
        runnable: list[PlannedToolCall] = []
        for call in batch:
            prerequisites = plan.dependencies.get(call.request.call_id, frozenset())
            failed: list[str] = []
            missing_artifacts: set[str] = set()
            for dependency in prerequisites:
                dependency_outcome = outcomes.get(dependency)
                if dependency_outcome is None or dependency_outcome.status != "succeeded":
                    failed.append(dependency)
                    continue
                dependency_call = calls_by_id[dependency]
                required_from_dependency = dependency_call.produces & call.requires
                missing_artifacts.update(
                    required_from_dependency
                    - dependency_outcome.published_artifacts
                )
            if failed or missing_artifacts:
                outcomes[call.request.call_id] = ToolExecutionOutcome(
                    request=call.request,
                    status="not_ready",
                    safe_error_code=SafeErrorCode.ALGORITHM_NOT_READY,
                )
            else:
                runnable.append(call)

        async def invoke(call: PlannedToolCall) -> ToolExecutionOutcome:
            key_semaphore = key_semaphores.setdefault(
                call.concurrency_key,
                asyncio.Semaphore(key_limits[call.concurrency_key]),
            )
            try:
                async with job_semaphore, key_semaphore:
                    value = await asyncio.wait_for(
                        execute(call.request),
                        timeout=min(call.timeout_seconds, timeout_seconds),
                    )
            except asyncio.CancelledError:
                raise
            except JobExecutionRevoked:
                raise
            except TimeoutError:
                return ToolExecutionOutcome(
                    request=call.request,
                    status="timed_out",
                    safe_error_code=SafeErrorCode.ALGORITHM_TIMED_OUT,
                )
            except Exception:
                return ToolExecutionOutcome(
                    request=call.request,
                    status="failed",
                    safe_error_code=SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
                )
            status, safe_error_code, published_artifacts = _classify_execution_result(
                call,
                value,
            )
            return ToolExecutionOutcome(
                request=call.request,
                status=status,
                value=value,
                safe_error_code=safe_error_code,
                published_artifacts=published_artifacts,
            )

        if runnable:
            tasks = [
                asyncio.create_task(
                    invoke(call),
                    name=f"algorithm-tool:{call.request.call_id}",
                )
                for call in runnable
            ]
            try:
                # gather 在一个子任务抛出普通异常时不会取消其他子任务；撤销和
                # 协程取消必须显式取消并等待整批，避免 sibling 在失去 lease 后
                # 继续触发外部调用。
                completed = await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            outcomes.update({outcome.request.call_id: outcome for outcome in completed})

    return tuple(outcomes[call.request.call_id] for call in plan.calls)


def _classify_execution_result(
    call: PlannedToolCall,
    value: Any,
) -> tuple[str, SafeErrorCode | str | None, frozenset[str]]:
    """将 executor 返回值转换成调度器状态和已发布 artifact 快照。

    旧 executor 返回任意值时继续沿用“正常返回即成功且发布 Spec 声明的
    artifact”的兼容语义。新的结构化返回可以通过 ``status`` 和可选的
    ``published_artifacts`` 明确表达失败或未发布结果；这样 producer 正常
    返回但没有发布下游所需 artifact 时，不会错误放行依赖节点。
    """

    if isinstance(value, AlgorithmResult):
        if value.status == "valid":
            return "succeeded", None, frozenset(call.produces)
        return (
            _outcome_status(value.status),
            value.diagnostics.safe_error_code or _default_error_code(value.status),
            frozenset(),
        )

    if not isinstance(value, Mapping) or "status" not in value:
        return "succeeded", None, frozenset(call.produces)

    raw_status = value.get("status")
    status = getattr(raw_status, "value", raw_status)
    if not isinstance(status, str):
        return (
            "failed",
            SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
            frozenset(),
        )

    if status in {"succeeded", "valid"}:
        published_artifacts, artifact_error = _published_artifacts(
            call,
            value,
        )
        if artifact_error is not None:
            return "failed", artifact_error, frozenset()
        return "succeeded", None, published_artifacts

    if status in {
        "failed",
        "execution_failed",
        "invalid_input",
        "not_applicable",
        "not_ready",
        "timed_out",
        "canceled",
    }:
        raw_error_code = value.get("safe_error_code")
        return (
            _outcome_status(status),
            raw_error_code or _default_error_code(status),
            frozenset(),
        )

    return (
        "failed",
        SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
        frozenset(),
    )


def _published_artifacts(
    call: PlannedToolCall,
    value: Mapping[str, Any],
) -> tuple[frozenset[str], SafeErrorCode | None]:
    """读取结构化返回中可选的 artifact publication 声明。"""

    if "published_artifacts" not in value:
        return frozenset(call.produces), None

    raw_artifacts = value["published_artifacts"]
    if isinstance(raw_artifacts, (str, bytes)):
        return frozenset(), SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
    try:
        published = frozenset(raw_artifacts)
    except TypeError:
        return frozenset(), SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
    if any(
        not isinstance(artifact, str)
        or not artifact.strip()
        or artifact not in call.produces
        for artifact in published
    ):
        return frozenset(), SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
    return published, None


def _outcome_status(status: str) -> str:
    if status == "timed_out":
        return "timed_out"
    if status == "not_ready":
        return "not_ready"
    return "failed"


def _default_error_code(status: str) -> SafeErrorCode:
    if status == "timed_out":
        return SafeErrorCode.ALGORITHM_TIMED_OUT
    if status == "not_ready":
        return SafeErrorCode.ALGORITHM_NOT_READY
    return SafeErrorCode.ALGORITHM_EXECUTION_FAILED
