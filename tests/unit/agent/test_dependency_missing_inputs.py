"""缺失输入的纯规划契约；只读取真实 Spec/Adapter，不执行任何算法。"""

import pytest

from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.dependency_planner import (
    DependencyPlanningError,
    ToolCallRequest,
    build_dependency_plan,
)
from Agent.deep_agent_tools.registry import build_default_registry


@pytest.mark.parametrize(
    "artifacts,missing",
    [
        ({"tabular_dataset", "continuous_tabular_dataset"}, {"pc": frozenset(), "cdfm": frozenset()}),
        ({"tabular_dataset"}, {"pc": frozenset(), "cdfm": frozenset({"continuous_tabular_dataset"})}),
        (set(), {"pc": frozenset({"tabular_dataset"}), "cdfm": frozenset({"continuous_tabular_dataset"})}),
    ],
)
def test_missing_input_is_recorded_for_each_call_without_coupling_siblings(artifacts, missing):
    registry = build_default_registry(build_default_adapters(executor=None))
    plan = build_dependency_plan(
        (ToolCallRequest("pc", "causal_pc"), ToolCallRequest("cdfm", "causal_cdfm")),
        registry=registry, initial_artifacts=artifacts,
    )
    assert plan.missing_artifacts_by_call == missing
    assert plan.dependencies == {"pc": frozenset(), "cdfm": frozenset()}
    assert len(plan.batches) == 1
    assert {call.request.call_id for call in plan.calls} == {"pc", "cdfm"}


def test_same_algorithm_calls_keep_distinct_missing_input_entries():
    plan = build_dependency_plan(
        (ToolCallRequest("pc-1", "causal_pc"), ToolCallRequest("pc-2", "causal_pc")),
        registry=build_default_registry(build_default_adapters(executor=None)),
    )
    assert plan.missing_artifacts_by_call == {
        "pc-1": frozenset({"tabular_dataset"}),
        "pc-2": frozenset({"tabular_dataset"}),
    }
    assert len(plan.calls) == 2
    assert plan.dependencies == {"pc-1": frozenset(), "pc-2": frozenset()}


def test_missing_producer_input_keeps_transitive_dependencies_and_independent_call():
    # 明确构造 DAG 元数据来检查分层，不代表存在新的生产算法或执行器。
    specs = [
        PC_SPEC.model_copy(update={"capability_id": "contract.producer", "tool_name": "producer",
                                   "requires": frozenset({"absent"}), "produces": frozenset({"prepared"})}),
        PC_SPEC.model_copy(update={"capability_id": "contract.child", "tool_name": "child",
                                   "requires": frozenset({"prepared"}), "produces": frozenset({"derived"})}),
        PC_SPEC.model_copy(update={"capability_id": "contract.grandchild", "tool_name": "grandchild",
                                   "requires": frozenset({"derived"}), "produces": frozenset({"final"})}),
        PC_SPEC,
    ]
    adapters = build_default_adapters(executor=None)
    registry = build_default_registry({spec.capability_id: adapters["causal.pc"] for spec in specs}, specs=specs)
    plan = build_dependency_plan(
        tuple(ToolCallRequest(name, name) for name in ("producer", "child", "grandchild", "causal_pc")),
        registry=registry, initial_artifacts={"tabular_dataset"},
    )
    assert [{call.request.call_id for call in batch} for batch in plan.batches] == [
        {"producer", "causal_pc"}, {"child"}, {"grandchild"},
    ]
    assert plan.missing_artifacts_by_call == {
        "producer": frozenset({"absent"}), "child": frozenset(),
        "grandchild": frozenset(), "causal_pc": frozenset(),
    }
    assert plan.dependencies["child"] == frozenset({"producer"})
    assert plan.dependencies["grandchild"] == frozenset({"child"})


@pytest.mark.parametrize("requests,match", [
    ((ToolCallRequest("same", "causal_pc"), ToolCallRequest("same", "causal_cdfm")), "duplicate"),
    ((ToolCallRequest("mismatch", "causal_pc", capability_id="causal.cdfm"),), "mismatch"),
])
def test_contract_error_is_not_reclassified_as_missing_input(requests, match):
    with pytest.raises(DependencyPlanningError, match=match):
        build_dependency_plan(requests, registry=build_default_registry(build_default_adapters(executor=None)))
