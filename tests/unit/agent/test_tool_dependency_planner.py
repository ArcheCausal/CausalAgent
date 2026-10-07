"""P2-U Tool dependency DAG、同轮并行和失败传播测试。"""

import asyncio

import pytest

from Agent.deep_agent_tools import (
    AlgorithmRegistry,
    AlgorithmResult,
    AlgorithmResultProvenance,
    DependencyPlanningError,
    Diagnostics,
    PC_SPEC,
    SafeErrorCode,
    ToolCallRequest,
    build_dependency_plan,
    execute_dependency_plan,
)
from Agent.deep_agent_tools.algorithm_specs import AlgorithmSpec, CausalPcInput
from Agent.deep_agent_tools.models import StandardizedGraph
from app.agent.worker.execution_guard import JobExecutionRevoked


def _registry() -> AlgorithmRegistry:
    registry = AlgorithmRegistry()
    registry.register(spec=PC_SPEC, adapter=object())
    return registry


def _spec(
    *,
    capability_id: str,
    tool_name: str,
    requires: frozenset[str],
    produces: frozenset[str],
    timeout_seconds: int = 10,
    concurrency_key: str = "test",
    default_concurrency: int = 1,
) -> AlgorithmSpec:
    return AlgorithmSpec(
        capability_id=capability_id,
        version="1.0",
        tool_name=tool_name,
        public_name=tool_name,
        description=tool_name,
        model_input_schema=CausalPcInput,
        requires=requires,
        produces=produces,
        assumptions=("test",),
        result_contract=StandardizedGraph,
        default_timeout_seconds=timeout_seconds,
        concurrency_key=concurrency_key,
        default_concurrency=default_concurrency,
    )


def _failed_algorithm_result(*, call_id: str) -> AlgorithmResult:
    return AlgorithmResult(
        result_ref=f"{call_id}:0",
        invocation_id=call_id,
        provider_call_id=call_id,
        capability_id="test.producer",
        capability_version="1.0",
        status="execution_failed",
        diagnostics=Diagnostics(
            safe_error_code=SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
        ),
        provenance=AlgorithmResultProvenance(
            job_id="job-1",
            attempt_count=0,
            lease_epoch=0,
            input_identity="input-1",
            spec_digest="spec-digest",
            invocation_id=call_id,
            capability_id="test.producer",
            capability_version="1.0",
        ),
    )


def test_independent_calls_share_a_batch_and_all_calls_are_retained() -> None:
    plan = build_dependency_plan(
        [
            ToolCallRequest(call_id="a", tool_name="causal_pc"),
            ToolCallRequest(call_id="b", tool_name="causal_pc"),
        ],
        registry=_registry(),
        initial_artifacts={"tabular_dataset"},
    )
    assert [[call.request.call_id for call in batch] for batch in plan.batches] == [["a", "b"]]


def test_unknown_duplicate_and_missing_dependency_fail_closed() -> None:
    with pytest.raises(DependencyPlanningError):
        build_dependency_plan(
            [ToolCallRequest(call_id="a", tool_name="missing")],
            registry=_registry(),
        )
    with pytest.raises(DependencyPlanningError, match="duplicate"):
        build_dependency_plan(
            [
                ToolCallRequest(call_id="a", tool_name="causal_pc"),
                ToolCallRequest(call_id="a", tool_name="causal_pc"),
            ],
            registry=_registry(),
        )


def test_cycle_is_rejected_without_dropping_any_call() -> None:
    registry = AlgorithmRegistry()
    registry.register(
        spec=_spec(
            capability_id="test.a",
            tool_name="a",
            requires=frozenset({"b_artifact"}),
            produces=frozenset({"a_artifact"}),
        ),
        adapter=object(),
    )
    registry.register(
        spec=_spec(
            capability_id="test.b",
            tool_name="b",
            requires=frozenset({"a_artifact"}),
            produces=frozenset({"b_artifact"}),
        ),
        adapter=object(),
    )

    with pytest.raises(DependencyPlanningError, match="cycle"):
        build_dependency_plan(
            [
                ToolCallRequest(call_id="a-call", tool_name="a"),
                ToolCallRequest(call_id="b-call", tool_name="b"),
            ],
            registry=registry,
        )


def test_planned_call_snapshots_algorithm_spec_limits() -> None:
    spec = _spec(
        capability_id="test.limited",
        tool_name="limited",
        requires=frozenset({"tabular_dataset"}),
        produces=frozenset({"prepared"}),
        timeout_seconds=37,
        concurrency_key="shared-cpu",
        default_concurrency=3,
    )
    registry = AlgorithmRegistry()
    registry.register(spec=spec, adapter=object())

    plan = build_dependency_plan(
        [ToolCallRequest(call_id="call", tool_name="limited")],
        registry=registry,
        initial_artifacts={"tabular_dataset"},
    )

    planned = plan.batches[0][0]
    assert planned.timeout_seconds == spec.default_timeout_seconds
    assert planned.concurrency_key == spec.concurrency_key
    assert planned.default_concurrency == spec.default_concurrency
    assert plan.concurrency_limits == {"shared-cpu": 3}


def test_effective_timeout_uses_spec_timeout_and_job_ceiling(monkeypatch) -> None:
    spec = _spec(
        capability_id="test.timeout",
        tool_name="timeout_tool",
        requires=frozenset({"tabular_dataset"}),
        produces=frozenset({"prepared"}),
        timeout_seconds=7,
    )
    registry = AlgorithmRegistry()
    registry.register(spec=spec, adapter=object())
    plan = build_dependency_plan(
        [ToolCallRequest(call_id="call", tool_name="timeout_tool")],
        registry=registry,
        initial_artifacts={"tabular_dataset"},
    )
    observed_timeouts: list[float] = []
    original_wait_for = asyncio.wait_for

    async def record_timeout(awaitable, *, timeout):
        observed_timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", record_timeout)

    async def scenario():
        return await execute_dependency_plan(
            plan,
            lambda request: asyncio.sleep(0, result=request.call_id),
            timeout_seconds=11,
        )

    outcomes = asyncio.run(scenario())
    assert outcomes[0].status == "succeeded"
    assert observed_timeouts == [7]


def test_spec_default_concurrency_cannot_be_raised_by_job_limit() -> None:
    async def scenario():
        spec = _spec(
            capability_id="test.serial",
            tool_name="serial_tool",
            requires=frozenset({"tabular_dataset"}),
            produces=frozenset({"prepared"}),
            concurrency_key="serial-resource",
            default_concurrency=1,
        )
        registry = AlgorithmRegistry()
        registry.register(spec=spec, adapter=object())
        plan = build_dependency_plan(
            [
                ToolCallRequest(call_id="one", tool_name="serial_tool"),
                ToolCallRequest(call_id="two", tool_name="serial_tool"),
            ],
            registry=registry,
            initial_artifacts={"tabular_dataset"},
        )
        active = 0
        peak = 0

        async def execute(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                return request.call_id
            finally:
                active -= 1

        await execute_dependency_plan(
            plan,
            execute,
            max_parallel_tools_per_job=4,
        )
        return peak

    assert asyncio.run(scenario()) == 1


def test_sibling_failure_does_not_cancel_and_dependent_call_is_not_ready() -> None:
    async def scenario():
        registry = AlgorithmRegistry()
        producer_spec = AlgorithmSpec(
            capability_id="test.producer",
            version="1.0",
            tool_name="producer",
            public_name="producer",
            description="producer",
            model_input_schema=CausalPcInput,
            requires=frozenset({"tabular_dataset"}),
            produces=frozenset({"prepared"}),
            assumptions=("test",),
            result_contract=StandardizedGraph,
            default_timeout_seconds=10,
            concurrency_key="producer",
            default_concurrency=2,
        )
        consumer_spec = producer_spec.model_copy(
            update={
                "capability_id": "test.consumer",
                "tool_name": "consumer",
                "produces": frozenset({"final"}),
                "requires": frozenset({"prepared"}),
            }
        )
        registry.register(spec=producer_spec, adapter=object())
        registry.register(spec=consumer_spec, adapter=object())
        plan = build_dependency_plan(
            [
                ToolCallRequest(call_id="bad", tool_name="producer"),
                ToolCallRequest(call_id="good", tool_name="producer"),
                ToolCallRequest(call_id="consumer", tool_name="consumer"),
            ],
            registry=registry,
            initial_artifacts={"tabular_dataset"},
        )

        async def execute(request):
            if request.call_id == "bad":
                raise RuntimeError("expected fake failure")
            return request.call_id

        outcomes = await execute_dependency_plan(plan, execute)
        return {outcome.request.call_id: outcome for outcome in outcomes}

    outcomes = asyncio.run(scenario())
    assert outcomes["bad"].status == "failed"
    assert outcomes["good"].status == "succeeded"
    assert outcomes["consumer"].status == "not_ready"


def test_structured_algorithm_result_failure_propagates_not_ready() -> None:
    async def scenario():
        registry = AlgorithmRegistry()
        producer_spec = _spec(
            capability_id="test.producer",
            tool_name="producer",
            requires=frozenset({"tabular_dataset"}),
            produces=frozenset({"prepared"}),
        )
        consumer_spec = _spec(
            capability_id="test.consumer",
            tool_name="consumer",
            requires=frozenset({"prepared"}),
            produces=frozenset({"final"}),
        )
        registry.register(spec=producer_spec, adapter=object())
        registry.register(spec=consumer_spec, adapter=object())
        plan = build_dependency_plan(
            [
                ToolCallRequest(call_id="producer-call", tool_name="producer"),
                ToolCallRequest(call_id="consumer-call", tool_name="consumer"),
            ],
            registry=registry,
            initial_artifacts={"tabular_dataset"},
        )

        async def execute(request):
            if request.call_id == "producer-call":
                return _failed_algorithm_result(call_id=request.call_id)
            pytest.fail("consumer must not run after structured producer failure")

        outcomes = await execute_dependency_plan(plan, execute)
        return {outcome.request.call_id: outcome for outcome in outcomes}

    outcomes = asyncio.run(scenario())
    assert outcomes["producer-call"].status == "failed"
    assert outcomes["producer-call"].safe_error_code == SafeErrorCode.ALGORITHM_EXECUTION_FAILED
    assert outcomes["consumer-call"].status == "not_ready"


def test_structured_mapping_failure_and_timeout_propagate_to_dependents() -> None:
    async def scenario():
        registry = AlgorithmRegistry()
        producer_spec = _spec(
            capability_id="test.producer",
            tool_name="producer",
            requires=frozenset({"tabular_dataset"}),
            produces=frozenset({"prepared"}),
        )
        consumer_spec = _spec(
            capability_id="test.consumer",
            tool_name="consumer",
            requires=frozenset({"prepared"}),
            produces=frozenset({"final"}),
        )
        registry.register(spec=producer_spec, adapter=object())
        registry.register(spec=consumer_spec, adapter=object())
        plan = build_dependency_plan(
            [
                ToolCallRequest(call_id="failed", tool_name="producer"),
                ToolCallRequest(call_id="timed-out", tool_name="producer"),
                ToolCallRequest(call_id="consumer", tool_name="consumer"),
            ],
            registry=registry,
            initial_artifacts={"tabular_dataset"},
        )

        async def execute(request):
            if request.call_id == "failed":
                return {
                    "status": "execution_failed",
                    "safe_error_code": SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
                }
            if request.call_id == "timed-out":
                await asyncio.sleep(0.05)
                return request.call_id
            pytest.fail("consumer must not run after failed producer calls")

        outcomes = await execute_dependency_plan(
            plan,
            execute,
            timeout_seconds=0.01,
        )
        return {outcome.request.call_id: outcome for outcome in outcomes}

    outcomes = asyncio.run(scenario())
    assert outcomes["failed"].status == "failed"
    assert outcomes["timed-out"].status == "timed_out"
    assert outcomes["consumer"].status == "not_ready"


def test_success_without_published_required_artifact_blocks_dependent_call() -> None:
    async def scenario():
        registry = AlgorithmRegistry()
        producer_spec = _spec(
            capability_id="test.producer",
            tool_name="producer",
            requires=frozenset({"tabular_dataset"}),
            produces=frozenset({"prepared"}),
        )
        consumer_spec = _spec(
            capability_id="test.consumer",
            tool_name="consumer",
            requires=frozenset({"prepared"}),
            produces=frozenset({"final"}),
        )
        registry.register(spec=producer_spec, adapter=object())
        registry.register(spec=consumer_spec, adapter=object())
        plan = build_dependency_plan(
            [
                ToolCallRequest(call_id="producer", tool_name="producer"),
                ToolCallRequest(call_id="consumer", tool_name="consumer"),
            ],
            registry=registry,
            initial_artifacts={"tabular_dataset"},
        )

        async def execute(request):
            if request.call_id == "producer":
                return {"status": "succeeded", "published_artifacts": []}
            pytest.fail("consumer must not run without its required artifact")

        outcomes = await execute_dependency_plan(plan, execute)
        return {outcome.request.call_id: outcome for outcome in outcomes}

    outcomes = asyncio.run(scenario())
    assert outcomes["producer"].status == "succeeded"
    assert outcomes["producer"].published_artifacts == frozenset()
    assert outcomes["consumer"].status == "not_ready"


def test_execution_enforces_job_concurrency_and_timeout() -> None:
    async def scenario():
        plan = build_dependency_plan(
            [ToolCallRequest(call_id=str(index), tool_name="causal_pc") for index in range(3)],
            registry=_registry(),
            initial_artifacts={"tabular_dataset"},
        )
        active = 0
        peak = 0

        async def execute(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02 if request.call_id != "2" else 0.2)
                return request.call_id
            finally:
                active -= 1

        outcomes = await execute_dependency_plan(
            plan,
            execute,
            max_parallel_tools_per_job=2,
            timeout_seconds=0.05,
        )
        return peak, {outcome.request.call_id: outcome.status for outcome in outcomes}

    peak, statuses = asyncio.run(scenario())
    assert peak == 2
    assert statuses == {"0": "succeeded", "1": "succeeded", "2": "timed_out"}


@pytest.mark.parametrize("control_flow", [JobExecutionRevoked("revoked"), asyncio.CancelledError()])
def test_control_flow_cancels_and_awaits_parallel_siblings(control_flow) -> None:
    async def scenario():
        registry = AlgorithmRegistry()
        registry.register(
            spec=_spec(
                capability_id="test.a",
                tool_name="a",
                requires=frozenset({"tabular_dataset"}),
                produces=frozenset({"a_result"}),
                default_concurrency=2,
            ),
            adapter=object(),
        )
        registry.register(
            spec=_spec(
                capability_id="test.b",
                tool_name="b",
                requires=frozenset({"tabular_dataset"}),
                produces=frozenset({"b_result"}),
                default_concurrency=2,
            ),
            adapter=object(),
        )
        plan = build_dependency_plan(
            [
                ToolCallRequest(call_id="a", tool_name="a"),
                ToolCallRequest(call_id="b", tool_name="b"),
            ],
            registry=registry,
            initial_artifacts={"tabular_dataset"},
        )
        sibling_started = asyncio.Event()
        sibling_cancelled = asyncio.Event()

        async def execute(request):
            if request.call_id == "a":
                await sibling_started.wait()
                raise control_flow
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cancelled.set()

        with pytest.raises(type(control_flow)):
            await execute_dependency_plan(plan, execute)
        assert sibling_cancelled.is_set()

    asyncio.run(scenario())
