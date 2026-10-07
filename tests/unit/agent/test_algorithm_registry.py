"""Runtime Registry 的静态绑定和冲突门禁测试。"""

from __future__ import annotations

import pytest

from Agent.deep_agent_tools import (
    AlgorithmRegistry,
    CDFM_SPEC,
    DIRECT_LINGAM_SPEC,
    PC_SPEC,
    RegistryConflictError,
    UnknownCapabilityError,
    build_default_registry,
)
from Agent.deep_agent_tools.algorithm_specs import CausalPcInput
from Agent.deep_agent_tools.models import StandardizedGraph
from Agent.deep_agent_tools.algorithm_specs import AlgorithmSpec


def _spec(*, capability_id: str = "test.capability", tool_name: str = "test_tool") -> AlgorithmSpec:
    return AlgorithmSpec(
        capability_id=capability_id,
        version="1.0",
        tool_name=tool_name,
        public_name="测试能力",
        description="测试能力描述",
        model_input_schema=CausalPcInput,
        requires=frozenset({"input"}),
        produces=frozenset({"output"}),
        assumptions=("测试假设",),
        result_contract=StandardizedGraph,
        default_timeout_seconds=10,
        concurrency_key="test",
    )


def test_registry_binds_one_spec_to_one_explicit_adapter() -> None:
    adapter = object()
    registry = AlgorithmRegistry()
    entry = registry.register(spec=PC_SPEC, adapter=adapter)

    assert entry.adapter is adapter
    assert registry.get("causal.pc") is entry
    assert registry.get_by_tool_name("causal_pc") is entry
    assert registry.spec_digests()["causal.pc"] == PC_SPEC.spec_digest


def test_registry_rejects_duplicate_capability_or_tool_name() -> None:
    registry = AlgorithmRegistry()
    registry.register(spec=_spec(), adapter=object())
    with pytest.raises(RegistryConflictError, match="capability_id"):
        registry.register(spec=_spec(), adapter=object())

    with pytest.raises(RegistryConflictError, match="tool_name"):
        registry.register(
            spec=_spec(capability_id="another.capability", tool_name="test_tool"),
            adapter=object(),
        )


def test_registry_unknown_capability_is_not_dynamic_registration() -> None:
    registry = AlgorithmRegistry()
    with pytest.raises(UnknownCapabilityError):
        registry.get("remote.tool.from.mcp")


def test_default_registry_requires_all_static_bindings() -> None:
    with pytest.raises(UnknownCapabilityError, match="causal.direct_lingam"):
        build_default_registry({"causal.pc": object()})


def test_default_registry_does_not_expose_disabled_olc() -> None:
    registry = build_default_registry(
        {
            "causal.pc": object(),
            "causal.direct_lingam": object(),
            "causal.cdfm": object(),
        }
    )

    assert [entry.spec for entry in registry.entries] == [
        CDFM_SPEC,
        DIRECT_LINGAM_SPEC,
        PC_SPEC,
    ]
    with pytest.raises(UnknownCapabilityError, match="causal.olc"):
        registry.get("causal.olc")
