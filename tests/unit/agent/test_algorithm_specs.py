"""AlgorithmSpec 单一事实源和 Tool schema 测试。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from Agent.deep_agent_tools.algorithm_specs import (
    AlgorithmSpec,
    CausalCdfmInput,
    CausalPcInput,
    CDFM_SPEC,
    DEFAULT_ALGORITHM_SPECS,
    OLC_SPEC,
    PC_SPEC,
    build_tool_schema_snapshot,
)
from Agent.deep_agent_tools.models import StandardizedGraph


def test_default_specs_only_enable_reviewed_runtime_capabilities() -> None:
    assert [spec.capability_id for spec in DEFAULT_ALGORITHM_SPECS] == [
        "causal.pc",
        "causal.direct_lingam",
        "causal.cdfm",
    ]
    assert OLC_SPEC.capability_id == "causal.olc"
    assert OLC_SPEC not in DEFAULT_ALGORITHM_SPECS
    assert PC_SPEC.tool_name == "causal_pc"
    assert PC_SPEC.requires == frozenset({"tabular_dataset"})
    assert PC_SPEC.produces == frozenset({"standardized_graph", "diagnostics"})
    assert PC_SPEC.default_timeout_seconds == 300
    assert PC_SPEC.default_concurrency == 2
    assert len(PC_SPEC.spec_digest) == 64
    assert CDFM_SPEC.tool_name == "causal_cdfm"
    assert CDFM_SPEC.requires == frozenset({"continuous_tabular_dataset"})
    assert CDFM_SPEC.default_timeout_seconds == 600
    assert CDFM_SPEC.default_concurrency == 1


def test_spec_digest_and_tool_schema_are_stable() -> None:
    first_digest = PC_SPEC.spec_digest
    second_digest = PC_SPEC.model_copy(deep=True).spec_digest
    assert first_digest == second_digest

    schema = PC_SPEC.build_tool_schema()
    assert PC_SPEC.spec_digest == first_digest
    assert set(schema) == {"name", "description", "parameters"}
    assert schema["name"] == "causal_pc"
    assert "public_decision" in schema["parameters"]["properties"]
    assert "public_decision" not in CausalPcInput.model_fields
    assert "user_id" not in json.dumps(schema, ensure_ascii=False)
    assert "job_id" not in json.dumps(schema, ensure_ascii=False)
    assert schema["parameters"]["additionalProperties"] is False


def test_tool_snapshot_contains_capability_and_spec_identity() -> None:
    snapshot = build_tool_schema_snapshot()
    assert [item["capability_id"] for item in snapshot] == [
        "causal.cdfm",
        "causal.direct_lingam",
        "causal.pc",
    ]
    assert all(len(item["spec_digest"]) == 64 for item in snapshot)


def test_model_input_schema_rejects_out_of_range_alpha() -> None:
    with pytest.raises(ValidationError):
        CausalPcInput(alpha=1.0)


@pytest.mark.parametrize("value", [-0.1, 1.1, "0.5", True])
def test_cdfm_threshold_rejects_invalid_values(value) -> None:
    with pytest.raises(ValidationError):
        CausalCdfmInput(threshold=value)


def test_cdfm_threshold_accepts_none_and_boundary_values() -> None:
    assert CausalCdfmInput().threshold is None
    assert CausalCdfmInput(threshold=0.0).threshold == 0.0
    assert CausalCdfmInput(threshold=0.5).threshold == 0.5


def test_spec_requires_an_assumption_and_stable_result_schema() -> None:
    with pytest.raises(ValidationError, match="assumption"):
        AlgorithmSpec(
            capability_id="test.capability",
            version="1.0",
            tool_name="test_tool",
            public_name="测试",
            description="测试能力",
            model_input_schema=CausalPcInput,
            requires=frozenset({"input"}),
            produces=frozenset({"output"}),
            assumptions=(),
            result_contract=StandardizedGraph,
            default_timeout_seconds=10,
            concurrency_key="test",
        )

