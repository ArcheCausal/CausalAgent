"""已确认方案在 Adapter 与 MCP 复检中的执行边界。"""

from __future__ import annotations

import asyncio

import pytest

from Agent.deep_agent import TrustedJobIdentity
from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent_tools.adapters.base import AdapterInput
from Agent.deep_agent_tools.adapters.pc import PcAdapter
from Agent.deep_agent_tools.algorithm_specs import build_tool_schema_snapshot
from Agent.deep_agent_tools.algorithm_tools import AlgorithmTool
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.fake_algorithm_executor import FakeAlgorithmExecutor
from Agent.deep_agent_tools.models import (
    PreprocessingApproval,
    PreprocessingPlan,
    PreprocessingStep,
)
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS


IDENTITY = TrustedJobIdentity(
    job_id="00000000-0000-0000-0000-000000000701",
    session_id="00000000-0000-0000-0000-000000000702",
    user_id=7,
    attempt_count=1,
    lease_epoch=2,
    worker_id="worker-1",
    input_identity="input-sha",
)

CSV = "age,city\n20,beijing\n,shanghai\n30,beijing\n40,beijing\n"


def _plan() -> PreprocessingPlan:
    return PreprocessingPlan(
        steps=(
            PreprocessingStep(operation="numeric_impute", columns=("age",), method="mean"),
            PreprocessingStep(operation="categorical_encode", columns=("city",), method="ordinal"),
        )
    )


def _run(adapter_input: AdapterInput):
    holder: dict[str, object] = {}

    async def _invoke():
        executor = FakeAlgorithmExecutor()
        adapter = PcAdapter(executor=executor, raw_backend=build_in_memory_backend(user_id=7))
        holder["result"] = await adapter.run(
            parameters={"alpha": 0.05},
            adapter_input=adapter_input,
            trusted_context=IDENTITY,
            provider_call_id="preprocess-call",
            response_identity="preprocess-response",
        )
        holder["executor"] = executor

    asyncio.run(_invoke())
    return holder["result"], holder["executor"]


def test_confirmed_plan_builds_an_isolated_data_view() -> None:
    plan = _plan()
    adapter_input = AdapterInput(
        data_profile=build_data_profile_from_csv(CSV),
        input_identity="input-sha",
        dataset_csv=CSV,
        missing_values_present=True,
        preprocessing=plan,
        approval=PreprocessingApproval(
            option_id="opt_" + "a" * 24,
            question_id="q-1",
            plan_digest=plan.digest,
        ),
    )
    result, executor = _run(adapter_input)

    assert result.status == "valid"
    assert result.data_view is not None
    assert result.data_view.row_count == 4
    assert result.data_view.columns == ("age", "city")
    assert result.data_view.operations == ("numeric_impute", "categorical_encode")
    assert result.provenance.preprocessing_plan_digest == plan.digest
    assert executor.calls[0].command.preprocessing is not None


def test_plan_without_approval_is_rejected_before_executor() -> None:
    plan = _plan()
    adapter_input = AdapterInput(
        data_profile=build_data_profile_from_csv(CSV),
        input_identity="input-sha",
        dataset_csv=CSV,
        preprocessing=plan,
    )
    result, executor = _run(adapter_input)

    assert result.status == "invalid_input"
    assert result.diagnostics.safe_error_code == (
        SafeErrorCode.PREPROCESSING_APPROVAL_REQUIRED
    )
    assert executor.calls == []


def test_unprocessed_missing_values_require_confirmation() -> None:
    adapter_input = AdapterInput(
        data_profile=build_data_profile_from_csv(CSV),
        input_identity="input-sha",
        dataset_csv=CSV,
        missing_values_present=True,
    )
    result, executor = _run(adapter_input)

    assert result.status == "invalid_input"
    assert result.diagnostics.safe_error_code == SafeErrorCode.PREPROCESSING_REQUIRED
    assert executor.calls == []


def test_tool_schema_exposes_plan_only_for_algorithms() -> None:
    snapshot = {item["capability_id"]: item for item in build_tool_schema_snapshot()}
    for item in snapshot.values():
        assert "preprocessing" in item["tool"]["parameters"]["properties"]
        assert "数据要求" in item["tool"]["description"]

def test_evidence_tool_schema_does_not_accept_preprocessing() -> None:
    from Agent.deep_agent_tools.algorithm_specs import build_model_tool_input_schema
    from Agent.deep_agent_tools.rag_evidence_tool import _RagEvidenceInput

    schema = build_model_tool_input_schema(_RagEvidenceInput).model_json_schema()
    assert "preprocessing" not in schema["properties"]


def test_registered_algorithm_tool_uses_spec_public_description() -> None:
    registry = build_default_registry(
        {spec.capability_id: object() for spec in DEFAULT_ALGORITHM_SPECS}
    )
    tool = AlgorithmTool(entry=registry.get("causal.pc"), adapter=object())

    assert tool.description == tool.entry.spec.model_description()
    assert "数据要求" in tool.description
    assert "可确认的处理操作" in tool.description
