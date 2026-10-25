"""纯函数、真实 ToolRuntime 和本地真实算法验证测量语义快照。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from langgraph.prebuilt import ToolRuntime

from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent.context import AnalysisInputRef, AgentRunContext, TrustedJobIdentity
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS, DIRECT_LINGAM_SPEC, PC_SPEC
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.identity import build_invocation_id
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.measurement_semantics import (
    MEASUREMENT_KINDS,
    semantic_kinds_from_validation_report,
)
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor, PC_CSV


def _report(declarations: object) -> dict:
    return validate_candidate(
        data_bytes=PC_CSV.encode(), script_bytes=b"import pandas as pd\n",
        metadata={
            "summary": "保留观测", "observation_unit": "每行一条观测",
            "transformations": ["保留行列"], "variable_semantics": {},
        },
        analysis_parameters={"measurement_semantics": declarations},
        specifications=DEFAULT_ALGORITHM_SPECS, max_bytes=100_000,
    )


@pytest.mark.parametrize("kind", sorted(MEASUREMENT_KINDS))
def test_validation_persists_normalized_semantics(kind: str) -> None:
    declaration = {"column": "x", "kind": kind, "evidence": f"x: {kind}"}
    report = _report([declaration, dict(declaration)])
    assert report["accepted"] is True
    assert semantic_kinds_from_validation_report(report) == {"x": kind}
    assert semantic_kinds_from_validation_report(json.dumps(report)) == {"x": kind}
    compatibility = {item["capability_id"]: item["status"] for item in report["algorithm_compatibility"]}
    assert compatibility[DIRECT_LINGAM_SPEC.capability_id] == (
        "incompatible" if kind in {"category", "ordinal", "binary"} else "compatible"
    )


@pytest.mark.parametrize("declarations", [
    None, {}, "continuous", [None],
    [{"column": "x", "kind": "Continuous", "evidence": "x"}],
    [{"column": "x", "kind": None, "evidence": "x"}],
    [{"column": [], "kind": "continuous", "evidence": "x"}],
    [{"column": "absent", "kind": "continuous", "evidence": "x"}],
    [
        {"column": "x", "kind": "category", "evidence": "x"},
        {"column": "x", "kind": "continuous", "evidence": "x"},
    ],
])
def test_validation_rejects_invalid_or_conflicting_declarations(declarations: object) -> None:
    report = _report(declarations)
    assert report["accepted"] is False
    assert any(issue["code"] == "measurement_semantics_invalid" for issue in report["errors"])
    with pytest.raises(ValueError):
        semantic_kinds_from_validation_report(report)


@pytest.mark.parametrize("report", [
    None, {}, "{",
    {"accepted": True, "errors": [], "columns": ["x"]},
    {"accepted": False, "errors": [], "columns": ["x"], "semantic_kinds": {}},
    {"accepted": True, "errors": ["error"], "columns": ["x"], "semantic_kinds": {}},
    {"accepted": True, "errors": [], "columns": ["x"], "semantic_kinds": {"x": "bad"}},
    {"accepted": True, "errors": [], "columns": ["x"], "semantic_kinds": {"y": "count"}},
    '{"accepted":true,"errors":[],"columns":["x"],"semantic_kinds":{"x":"category","x":"continuous"}}',
])
def test_report_parser_fails_closed(report: object) -> None:
    with pytest.raises(ValueError):
        semantic_kinds_from_validation_report(report)


def _identity(kinds: dict[str, str] | None) -> TrustedJobIdentity:
    ref = AnalysisInputRef()
    ref.bind(
        dataset_id=1, object_id=1, content_hash="prepared-sha",
        filename="prepared.csv", semantic_kinds=kinds,
    )
    return TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000001",
        session_id="00000000-0000-0000-0000-000000000002",
        user_id=7, attempt_count=0, lease_epoch=1, worker_id="worker-1",
        input_identity="source-sha", analysis_input=ref,
    )


def test_bind_copies_snapshot_and_rejects_invalid_update_atomically() -> None:
    kinds = {"x": "category"}
    identity = _identity(kinds)
    ref = identity.analysis_input
    kinds["x"] = "continuous"
    assert ref.semantic_kinds == {"x": "category"}
    with pytest.raises(TypeError):
        ref.semantic_kinds["x"] = "continuous"
    with pytest.raises(ValueError):
        ref.bind(dataset_id=2, object_id=2, content_hash="other-sha", filename="other.csv", semantic_kinds={"x": "bad"})
    assert ref.dataset_id == 1
    assert ref.semantic_kinds == {"x": "category"}


def _tool(executor: LocalAlgorithmExecutor, identity: TrustedJobIdentity, name: str):
    context = AgentRunContext(execution_guard=None, trusted_identity=identity, algorithm_executor=executor)
    registry = build_default_registry(build_default_adapters(executor=executor))
    return context, next(tool for tool in build_algorithm_tools(
        registry, runtime_context=context, data_profile=executor.data_profile,
    ) if tool.name == name)


def test_runtime_state_cannot_override_bound_semantics_and_rebind_is_visible() -> None:
    executor = LocalAlgorithmExecutor(semantic_kinds={"x": "category"})
    identity = _identity(semantic_kinds_from_validation_report(executor.validation_report))
    context, tool = _tool(executor, identity, "causal_direct_lingam")
    runtime = ToolRuntime(
        tool_call_id="call", state={
            "data_profile": executor.data_profile,
            "semantic_kinds": {"x": "continuous"},
            "analysis_parameters": {"measurement_semantics": []},
        }, context=context, config={}, stream_writer=None, store=None,
    )
    assert tool._adapter_input_from_runtime(runtime).semantic_kinds == {"x": "category"}
    forged = replace(tool.adapter_input, semantic_kinds={"x": "continuous"})
    result = asyncio.run(tool.ainvoke(
        {}, provider_call_id="call", response_identity="response", adapter_input=forged,
    ))
    assert result.status == "not_applicable"
    assert executor.calls == []
    rebound_report = _report([{"column": "x", "kind": "binary", "evidence": "x: binary"}])
    identity.analysis_input.bind(
        dataset_id=2, object_id=2, content_hash="next-sha", filename="next.csv",
        semantic_kinds=semantic_kinds_from_validation_report(rebound_report),
    )
    assert tool._adapter_input_from_runtime(runtime).semantic_kinds == {"x": "binary"}
    schema = tool.to_langchain_tool().tool_call_schema.model_json_schema()["properties"]
    assert "semantic_kinds" not in schema


def test_missing_snapshot_is_rejected_instead_of_assuming_continuous() -> None:
    executor = LocalAlgorithmExecutor()
    with pytest.raises(ValueError):
        _tool(executor, _identity(None), "causal_pc")


@pytest.mark.parametrize("spec,kind,expected", [
    (PC_SPEC, "category", "valid"),
    (DIRECT_LINGAM_SPEC, "category", "not_applicable"),
    (DIRECT_LINGAM_SPEC, "continuous", "valid"),
])
def test_real_mcp_and_adapter_share_validation_snapshot(spec, kind: str, expected: str) -> None:
    executor = LocalAlgorithmExecutor(semantic_kinds={"x": kind})
    kinds = semantic_kinds_from_validation_report(executor.validation_report)
    identity = _identity(kinds)
    context, tool = _tool(executor, identity, spec.tool_name)
    result = asyncio.run(tool.ainvoke(
        {}, provider_call_id="adapter-call", response_identity="response",
    ))
    assert result.status == expected
    invocation_id = build_invocation_id(
        job_id=identity.job_id, response_identity="response", provider_call_id="mcp-call",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=invocation_id, capability_id=spec.capability_id,
        capability_version=spec.version, spec_digest=spec.spec_digest,
        provider_call_id="mcp-call", input_identity=identity.current_input_identity(),
        parameters={}, timeout_seconds=60,
    )
    response = asyncio.run(executor.execute(command, identity.to_mcp_context(invocation_id=invocation_id)))
    assert response.result.status == expected
    assert kinds == executor.validated_input.semantic_kinds == tool.adapter_input.semantic_kinds
