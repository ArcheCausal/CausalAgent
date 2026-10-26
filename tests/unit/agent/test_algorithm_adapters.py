"""真实本地算法执行、已绑定输入和 raw 完整性；不覆盖 HTTP/MySQL。"""

import asyncio
import json
from dataclasses import replace

import numpy as np
import pytest

from Agent.deep_agent import TrustedJobIdentity
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent_tools import DataProfile, build_default_registry
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.adapters import CDFMAdapter, DirectLiNGAMAdapter, OlcAdapter, PcAdapter
from Agent.deep_agent_tools.adapters.base import AdapterInput, RawResultIntegrityError
from Agent.deep_agent_tools.adapters.normalization import standardize_runner_graph, result_from_runner_payload
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from app.agent.worker.execution_guard import JobExecutionRevoked
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor
from tests.support.bound_analysis_input import bind_analysis_input


IDENTITY = TrustedJobIdentity(
    job_id="00000000-0000-0000-0000-000000000001",
    session_id="00000000-0000-0000-0000-000000000002",
    user_id=7,
    attempt_count=0,
    lease_epoch=1,
    worker_id="worker-1",
    input_identity="input-sha",
)


def _profile(*, rows=250, categorical=(), numeric=("x", "y")) -> DataProfile:
    return DataProfile(
        row_count=rows, column_count=2, column_names=("x", "y"),
        numeric_columns=tuple(numeric), categorical_columns=tuple(categorical),
    )


def _identity(executor):
    return bind_analysis_input(
        IDENTITY, executor.csv_data,
        semantic_kinds=executor.validated_input.semantic_kinds, dataset_id=11,
    )


def _input(executor, **updates):
    return replace(AdapterInput(
        data_profile=executor.data_profile,
        input_identity=_identity(executor).current_input_identity(),
        dataset_authority_available=True,
        missing_values_present=bool(executor.data_profile.missing_columns),
        semantic_kinds=executor.validated_input.semantic_kinds,
    ), **updates)


@pytest.fixture(scope="module")
def pc_execution():
    executor = LocalAlgorithmExecutor()
    backend = build_in_memory_backend(user_id=7)
    adapter = PcAdapter(executor=executor, raw_backend=backend)
    result = asyncio.run(adapter.run(
        parameters={"alpha": 0.05}, adapter_input=_input(executor),
        trusted_context=_identity(executor), provider_call_id="call-1",
        response_identity="response-1",
    ))
    assert result.status == "valid"
    return executor, backend, adapter, result


def test_pc_adapter_uses_local_executor_and_writes_raw_result(pc_execution) -> None:
    executor, backend, _, result = pc_execution
    assert result.raw_result_ref is not None
    raw = json.loads(backend.read(result.raw_result_ref))
    assert raw["success"] is True
    assert result.provenance.source_input_identity == IDENTITY.input_identity
    assert result.provenance.analysis_dataset_id == 11
    assert result.provenance.input_identity == _identity(executor).current_input_identity()
    assert len(executor.calls) == 1


def test_adapter_verifies_written_raw_result_against_backend_bytes(pc_execution) -> None:
    executor, backend, adapter, result = pc_execution
    # 调用实际校验入口，以 backend 字节而非 executor 的内存 payload 为准。
    adapter._verify_raw_result(result=result, command=executor.calls[0].command)
    assert backend.read(result.raw_result_ref) is not None


def test_adapter_verifies_existing_raw_result_in_supplied_backend(pc_execution) -> None:
    executor, backend, adapter, result = pc_execution
    copied_backend = build_in_memory_backend(user_id=7)
    copied_backend.write_trusted(result.raw_result_ref, backend.read(result.raw_result_ref))
    adapter._verify_raw_result(
        result=result, command=executor.calls[0].command, raw_backend=copied_backend,
    )


def test_adapter_rejects_missing_raw_result_file(pc_execution) -> None:
    executor, _, adapter, result = pc_execution
    with pytest.raises(RawResultIntegrityError, match="reference is missing"):
        adapter._verify_raw_result(
            result=result, command=executor.calls[0].command,
            raw_backend=build_in_memory_backend(user_id=7),
        )


@pytest.mark.parametrize("metadata_update", [
    {"raw_result_sha256": "0" * 64},
    {"raw_result_size_bytes": 1},
    {"raw_result_serialization_version": "legacy-json-v0"},
])
def test_adapter_rejects_raw_metadata_mismatch(pc_execution, metadata_update) -> None:
    executor, _, adapter, result = pc_execution
    with pytest.raises(RawResultIntegrityError):
        adapter._verify_raw_result(
            result=result.model_copy(update=metadata_update), command=executor.calls[0].command,
        )


def test_adapter_rejects_unreadable_raw_content(pc_execution) -> None:
    executor, _, adapter, result = pc_execution
    backend = build_in_memory_backend(user_id=7)
    backend.write_trusted(result.raw_result_ref, b"not-json")
    with pytest.raises(RawResultIntegrityError, match="readable canonical JSON"):
        adapter._verify_raw_result(
            result=result, command=executor.calls[0].command, raw_backend=backend,
        )


def test_olc_and_lingam_reject_non_continuous_or_small_inputs_before_executor() -> None:
    executor = LocalAlgorithmExecutor("x,y\n" + "a,1\nb,2\n" * 10)
    input_data = _input(executor)
    results = []
    for adapter, parameters in (
        (OlcAdapter(executor=executor), {"alpha": 0.05, "beta": 0.01}),
        (DirectLiNGAMAdapter(executor=executor), {}),
    ):
        results.append(asyncio.run(adapter.run(
            parameters=parameters, adapter_input=input_data,
            trusted_context=_identity(executor), provider_call_id=adapter.spec.tool_name,
            response_identity="response-1",
        )))
    assert [result.status for result in results] == ["not_applicable", "invalid_input"]
    assert executor.calls == []


def test_cdfm_adapter_preserves_missing_values_without_adapter_imputation() -> None:
    from Agent.causal.cdfm_runner import _parse_csv

    executor = LocalAlgorithmExecutor("x,y\n1,2\n,3\n3,4\n")
    adapter_input = _input(executor)
    adapter = CDFMAdapter(executor=executor)
    assert adapter.spec.assess_applicability(
        profile=adapter_input.data_profile, semantic_kinds=adapter_input.semantic_kinds,
    )[0] == "ready"
    assert adapter.validate_input(adapter_input) is None
    # 真实 parser 生成缺失掩码；没有模型权重时不声称完成 CDFM 推理。
    data, columns, missing = _parse_csv(executor.validated_input.csv_data)
    assert columns == ["x", "y"]
    assert data.shape == (3, 2)
    assert np.isnan(data[1, 0])
    assert missing.sum() == 1 and missing[1, 0]
    assert executor.csv_data == "x,y\n1,2\n,3\n3,4\n"
    assert executor.calls == []


def test_cdfm_adapter_rejects_categorical_and_untrusted_inputs() -> None:
    executor = LocalAlgorithmExecutor()
    adapter = CDFMAdapter(executor=executor)
    categorical = LocalAlgorithmExecutor("x,y\na,1\nb,2\n")
    for current_executor, adapter_input in (
        (categorical, _input(categorical)),
        (executor, _input(executor, dataset_authority_available=False)),
    ):
        result = asyncio.run(adapter.run(
            parameters={}, adapter_input=adapter_input, trusted_context=_identity(current_executor),
            provider_call_id="cdfm-invalid-call", response_identity="response-cdfm-invalid",
        ))
        assert result.status == "invalid_input"
        assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_INPUT_INVALID
    assert executor.calls == []


def test_algorithm_tool_keeps_all_ledger_revisions_for_one_call() -> None:
    executor = LocalAlgorithmExecutor()
    backend = build_in_memory_backend(user_id=7)
    registry = build_default_registry(build_default_adapters(executor=executor, raw_backend=backend))
    runtime = AgentRunContext(
        execution_guard=None, trusted_identity=_identity(executor),
        algorithm_executor=executor, filesystem_backend=backend,
    )
    tools = build_algorithm_tools(registry, runtime_context=runtime, data_profile=executor.data_profile)
    pc_tool = next(tool for tool in tools if tool.name == "causal_pc")
    result, records = asyncio.run(pc_tool.ainvoke_with_ledger(
        {"alpha": 0.05}, provider_call_id="ledger-call", response_identity="response-1",
    ))
    assert result.status == "valid"
    assert [record.attempts[0].revision for record in records] == [0, 1, 2]
    assert records[-1].final_status == "succeeded"
    assert all(record.analysis_dataset_id == 11 for record in records)


def test_runner_normalization_preserves_direction_and_weight_semantics() -> None:
    graph = standardize_runner_graph(
        {
            "success": True,
            "data": {
                "nodes": [{"id": "x"}, {"id": "y"}],
                "edges": [{"from": "x", "to": "y", "arrows": "to", "weight": 0.5}],
            },
        },
        capability_id="causal.direct_lingam",
    )
    assert graph.graph_semantics == "dag_target_to_source"
    assert graph.edges[0].source == "x"
    assert graph.edges[0].target == "y"
    assert graph.edges[0].weight == 0.5

    olc_graph = standardize_runner_graph(
        {
            "success": True,
            "data": {
                "nodes": [{"id": "x"}, {"id": "y"}],
                "edges": [{"from": "x", "to": "y", "arrows": "to", "label": "-0.25"}],
            },
        },
        capability_id="causal.olc",
    )
    assert olc_graph.graph_semantics == "latent_mixed_graph"
    assert olc_graph.edges[0].weight == -0.25

    reversed_graph = standardize_runner_graph(
        {
            "success": True,
            "data": {
                "nodes": [{"id": "x"}, {"id": "y"}],
                "edges": [{"from": "x", "to": "y", "arrows": "from"}],
            },
        },
        capability_id="causal.pc",
    )
    assert reversed_graph.edges[0].edge_type == "directed"
    assert reversed_graph.edges[0].source == "y"
    assert reversed_graph.edges[0].target == "x"

    bidirected_graph = standardize_runner_graph(
        {
            "success": True,
            "data": {
                "nodes": [{"id": "x"}, {"id": "y"}],
                "edges": [{"from": "x", "to": "y", "arrows": "to,from"}],
            },
        },
        capability_id="causal.pc",
    )
    assert bidirected_graph.edges[0].edge_type == "bidirected"


def test_direct_lingam_rejects_invalid_csv_before_executor() -> None:
    # CSV 准入已移至独立校验器，不再向 AdapterInput 传入文件正文。
    report = validate_candidate(
        data_bytes=b"x,x\n1,2\n3,4\n", script_bytes=b"import pandas as pd\n",
        metadata={"summary": "保留数据", "observation_unit": "一条观测", "transformations": ["保留原值"], "variable_semantics": {}},
        analysis_parameters={}, specifications=DEFAULT_ALGORITHM_SPECS, max_bytes=1024,
    )
    assert report["accepted"] is False
    assert any(error["code"] == "csv_invalid" for error in report["errors"])


def test_adapter_normalizes_actual_runner_payload(pc_execution) -> None:
    executor, backend, _, result = pc_execution
    raw = json.loads(backend.read(result.raw_result_ref))
    call = executor.calls[0]
    normalized = result_from_runner_payload(
        raw, command=call.command, context=call.trusted_context,
        capability_version=call.command.capability_version, runner_version=raw["runner_version"],
    )
    assert normalized.status == "valid"
    assert normalized.standardized_graph == result.standardized_graph
    assert normalized.provenance == result.provenance


def test_normalization_accepts_legacy_payload_schema(pc_execution) -> None:
    # 旧格式是纯转换函数的输入样例，不代表实际算法执行结果。
    executor, _, _, _ = pc_execution
    call = executor.calls[0]
    normalized = result_from_runner_payload(
        {"success": True, "runner_version": "legacy-1", "data": {
            "nodes": [{"id": "x"}, {"id": "y"}],
            "edges": [{"from": "x", "to": "y", "arrows": "to"}],
        }},
        command=call.command, context=call.trusted_context,
        capability_version=call.command.capability_version, runner_version="legacy-1",
    )
    assert normalized.status == "valid"
    assert normalized.standardized_graph.edges[0].edge_type == "directed"
    assert normalized.provenance.algorithm_runner_version == "legacy-1"


def test_adapter_normalizes_legacy_runner_diagnostics_keys() -> None:
    from Agent.deep_agent_tools.models import build_diagnostics_from_runner_payload

    # 纯数据转换契约，不伪造算法执行或模型推理成功。
    diagnostics = build_diagnostics_from_runner_payload({
        "diagnostics": {"n_samples": 120, "n_features": 2, "missing_values": 3, "runtime_sec": 0.5},
    })
    assert diagnostics.sample_count == 120
    assert diagnostics.variable_count == 2
    assert diagnostics.metrics["n_samples"] == 120
    assert diagnostics.metrics["missing_values"] == 3
    assert diagnostics.metrics["runtime_sec"] == 0.5


def test_job_revocation_is_not_converted_to_failed_algorithm_result() -> None:
    class RevokingExecutor:
        async def execute(self, command, context):
            raise JobExecutionRevoked("revoked")

    from Agent.deep_agent_tools.adapters.base import AdapterInput

    with __import__("pytest").raises(JobExecutionRevoked):
        asyncio.run(
            PcAdapter(executor=RevokingExecutor()).run(
                parameters={"alpha": 0.05},
                adapter_input=AdapterInput(
                    data_profile=_profile(),
                    input_identity="input-sha",
                ),
                trusted_context=IDENTITY,
                provider_call_id="revoked-call",
                response_identity="response-1",
            )
        )


def test_algorithm_tool_checks_guard_after_adapter_returns() -> None:
    class RevokingGuard:
        def __init__(self):
            self.calls = 0

        async def ensure_active(self):
            self.calls += 1
            if self.calls == 2:
                raise JobExecutionRevoked("revoked after remote return")

    guard = RevokingGuard()
    executor = LocalAlgorithmExecutor()
    backend = build_in_memory_backend(user_id=7)
    context = AgentRunContext(
        execution_guard=guard,
        trusted_identity=_identity(executor),
        algorithm_executor=executor,
        filesystem_backend=backend,
    )
    registry = build_default_registry(
        build_default_adapters(executor=executor, raw_backend=backend)
    )
    pc_tool = next(
        tool
        for tool in build_algorithm_tools(
            registry,
            runtime_context=context,
            data_profile=executor.data_profile,
        )
        if tool.name == "causal_pc"
    )

    with __import__("pytest").raises(JobExecutionRevoked):
        asyncio.run(
            pc_tool.ainvoke(
                {"alpha": 0.05},
                provider_call_id="late-call",
                response_identity="response-1",
            )
        )
    assert guard.calls == 2
