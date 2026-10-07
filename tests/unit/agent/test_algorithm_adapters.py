"""P2-U 三个算法 Adapter 的输入边界、provenance 和 raw snapshot 测试。"""

import asyncio

from Agent.deep_agent import TrustedJobIdentity
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent_tools import DataProfile, FakeAlgorithmExecutor, build_default_registry
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.adapters import (
    CDFMAdapter,
    DirectLiNGAMAdapter,
    OlcAdapter,
    PcAdapter,
)
from Agent.deep_agent_tools.adapters.normalization import standardize_runner_graph
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    AlgorithmResultProvenance,
    StandardizedGraph,
    build_raw_result_metadata,
)
from app.agent.worker.execution_guard import JobExecutionRevoked


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
        row_count=rows,
        column_count=2,
        column_names=("x", "y"),
        numeric_columns=tuple(numeric),
        categorical_columns=tuple(categorical),
    )


def test_pc_adapter_uses_fake_executor_and_writes_raw_result() -> None:
    executor = FakeAlgorithmExecutor()
    backend = build_in_memory_backend(user_id=7)
    adapter = PcAdapter(executor=executor, raw_backend=backend)
    result = asyncio.run(
        adapter.run(
            parameters={"alpha": 0.05},
            adapter_input=__import__(
                "Agent.deep_agent_tools.adapters.base", fromlist=["AdapterInput"]
            ).AdapterInput(data_profile=_profile(), input_identity="input-sha"),
            trusted_context=IDENTITY,
            provider_call_id="call-1",
            response_identity="response-1",
        )
    )
    assert result.status == "valid"
    assert result.raw_result_ref is not None
    assert backend.read(result.raw_result_ref) is not None
    assert result.provenance.preprocessing_recipe_digest
    assert len(executor.calls) == 1


class _ReadTrackingBackend:
    def __init__(self, backend) -> None:
        self.backend = backend
        self.read_refs: list[str] = []

    def read(self, path: str):
        self.read_refs.append(path)
        return self.backend.read(path)

    def write_raw_result(self, **kwargs):
        return self.backend.write_raw_result(**kwargs)


def test_adapter_reads_back_its_written_raw_result_before_returning() -> None:
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    tracking_backend = _ReadTrackingBackend(build_in_memory_backend(user_id=7))
    result = asyncio.run(
        PcAdapter(
            executor=FakeAlgorithmExecutor(),
            raw_backend=tracking_backend,
        ).run(
            parameters={"alpha": 0.05},
            adapter_input=AdapterInput(data_profile=_profile(), input_identity="input-sha"),
            trusted_context=IDENTITY,
            provider_call_id="read-back-call",
            response_identity="response-1",
        )
    )

    assert result.status == "valid"
    assert tracking_backend.read_refs == [result.raw_result_ref]


class _ProvidedRawResultExecutor:
    def __init__(
        self,
        backend,
        *,
        write_raw: bool = True,
        metadata_update=None,
        stored_content: bytes | None = None,
    ) -> None:
        self.backend = backend
        self.write_raw = write_raw
        self.metadata_update = metadata_update or {}
        self.stored_content = stored_content

    async def execute(self, command, context):
        raw_result_ref = (
            f"/raw_algorithm_results/{command.invocation_id}/{command.result_index}.json"
        )
        raw_payload = {"runner": "mcp", "nodes": [{"id": "x"}], "edges": []}
        metadata = build_raw_result_metadata(
            raw_result_ref=raw_result_ref,
            raw_result=raw_payload,
        )
        if self.write_raw:
            self.backend.write_raw_result(
                raw_result_ref=raw_result_ref,
                raw_result=raw_payload,
            )
            if self.stored_content is not None:
                self.backend.write_trusted(
                    raw_result_ref,
                    self.stored_content,
                    overwrite=True,
                )
        metadata = metadata.model_copy(update=self.metadata_update)
        return AlgorithmResult(
            result_ref=f"{command.invocation_id}:{command.result_index}",
            invocation_id=command.invocation_id,
            provider_call_id=command.provider_call_id,
            capability_id=command.capability_id,
            capability_version=command.capability_version,
            status="valid",
            standardized_graph=StandardizedGraph(
                graph_semantics="dag",
                nodes=[],
                edges=[],
            ),
            raw_result_ref=metadata.raw_result_ref,
            raw_result_sha256=metadata.raw_result_sha256,
            raw_result_size_bytes=metadata.raw_result_size_bytes,
            raw_result_serialization_version=metadata.raw_result_serialization_version,
            provenance=AlgorithmResultProvenance(
                job_id=context.job_id,
                attempt_count=context.attempt_count,
                lease_epoch=context.lease_epoch,
                input_identity=command.input_identity,
                spec_digest=command.spec_digest,
                invocation_id=command.invocation_id,
                capability_id=command.capability_id,
                capability_version=command.capability_version,
            ),
        )


def _run_provided_raw_result_executor(
    *,
    backend,
    write_raw: bool = True,
    metadata_update=None,
    stored_content: bytes | None = None,
):
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    return asyncio.run(
        PcAdapter(
            executor=_ProvidedRawResultExecutor(
                backend,
                write_raw=write_raw,
                metadata_update=metadata_update,
                stored_content=stored_content,
            ),
            raw_backend=backend,
        ).run(
            parameters={"alpha": 0.05},
            adapter_input=AdapterInput(data_profile=_profile(), input_identity="input-sha"),
            trusted_context=IDENTITY,
            provider_call_id="provided-raw-call",
            response_identity="response-raw",
        )
    )


def test_adapter_reads_back_executor_provided_raw_result() -> None:
    backend = _ReadTrackingBackend(build_in_memory_backend(user_id=7))

    result = _run_provided_raw_result_executor(backend=backend)

    assert result.status == "valid"
    assert result.raw_result_ref is not None
    assert backend.read_refs == [result.raw_result_ref]


def test_adapter_rejects_executor_provided_missing_raw_result_reference() -> None:
    backend = build_in_memory_backend(user_id=7)

    result = _run_provided_raw_result_executor(backend=backend, write_raw=False)

    assert result.status == "execution_failed"
    assert result.raw_result_ref is None
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID


def test_adapter_rejects_executor_provided_raw_metadata_mismatch() -> None:
    mismatch_cases = (
        {"raw_result_sha256": "0" * 64},
        {"raw_result_size_bytes": 1},
        {"raw_result_serialization_version": "legacy-json-v0"},
    )

    for metadata_update in mismatch_cases:
        backend = build_in_memory_backend(user_id=7)
        result = _run_provided_raw_result_executor(
            backend=backend,
            metadata_update=metadata_update,
        )

        assert result.status == "execution_failed"
        assert result.raw_result_ref is None
        assert (
            result.diagnostics.safe_error_code
            == SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID
        )


def test_adapter_rejects_executor_provided_unreadable_raw_content() -> None:
    backend = build_in_memory_backend(user_id=7)

    result = _run_provided_raw_result_executor(
        backend=backend,
        stored_content=b"not-json",
    )

    assert result.status == "execution_failed"
    assert result.raw_result_ref is None
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID


def test_olc_and_lingam_reject_non_continuous_or_small_inputs_before_executor() -> None:
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    executor = FakeAlgorithmExecutor()
    input_data = AdapterInput(
        data_profile=_profile(rows=20, categorical=("x",), numeric=("y",)),
        input_identity="input-sha",
    )
    olc = OlcAdapter(executor=executor)
    lingam = DirectLiNGAMAdapter(executor=executor)
    olc_result = asyncio.run(
        olc.run(
            parameters={"alpha": 0.05, "beta": 0.01},
            adapter_input=input_data,
            trusted_context=IDENTITY,
            provider_call_id="olc-call",
            response_identity="response-1",
        )
    )
    lingam_result = asyncio.run(
        lingam.run(
            parameters={},
            adapter_input=input_data,
            trusted_context=IDENTITY,
            provider_call_id="lingam-call",
            response_identity="response-1",
        )
    )
    assert olc_result.status == "not_applicable"
    assert lingam_result.status == "invalid_input"
    assert executor.calls == []


def test_cdfm_adapter_preserves_missing_values_without_adapter_imputation() -> None:
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    executor = FakeAlgorithmExecutor()
    result = asyncio.run(
        CDFMAdapter(executor=executor).run(
            parameters={"threshold": 0.5},
            adapter_input=AdapterInput(
                data_profile=_profile(rows=2),
                input_identity="input-sha",
                dataset_csv="x,y\n1,2\n,3\n",
                missing_values_present=True,
            ),
            trusted_context=IDENTITY,
            provider_call_id="cdfm-call",
            response_identity="response-cdfm",
        )
    )

    assert result.status == "valid"
    assert len(executor.calls) == 1


def test_cdfm_adapter_rejects_categorical_and_untrusted_inputs() -> None:
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    executor = FakeAlgorithmExecutor()
    adapter = CDFMAdapter(executor=executor)
    for adapter_input in (
        AdapterInput(
            data_profile=_profile(rows=2, categorical=("x",), numeric=("y",)),
            input_identity="input-sha",
            dataset_csv="x,y\na,1\nb,2\n",
        ),
        AdapterInput(
            data_profile=_profile(rows=2),
            input_identity="input-sha",
        ),
    ):
        result = asyncio.run(
            adapter.run(
                parameters={},
                adapter_input=adapter_input,
                trusted_context=IDENTITY,
                provider_call_id="cdfm-invalid-call",
                response_identity="response-cdfm-invalid",
            )
        )
        assert result.status == "invalid_input"
    assert executor.calls == []


def test_algorithm_tool_keeps_all_ledger_revisions_for_one_call() -> None:
    executor = FakeAlgorithmExecutor()
    backend = build_in_memory_backend(user_id=7)
    registry = build_default_registry(
        build_default_adapters(executor=executor, raw_backend=backend)
    )
    runtime = AgentRunContext(
        execution_guard=None,
        trusted_identity=IDENTITY,
        algorithm_executor=executor,
    )
    tools = build_algorithm_tools(
        registry,
        runtime_context=runtime,
        data_profile=_profile(),
    )
    pc_tool = next(tool for tool in tools if tool.name == "causal_pc")
    result, records = asyncio.run(
        pc_tool.ainvoke_with_ledger(
            {"alpha": 0.05},
            provider_call_id="ledger-call",
            response_identity="response-1",
        )
    )
    assert result.status == "valid"
    assert [record.attempts[0].revision for record in records] == [0, 1, 2]
    assert records[-1].final_status == "succeeded"


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
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    executor = FakeAlgorithmExecutor()
    adapter = DirectLiNGAMAdapter(executor=executor)
    result = asyncio.run(
        adapter.run(
            parameters={},
            adapter_input=AdapterInput(
                data_profile=_profile(),
                input_identity="input-sha",
                dataset_csv="x,x\n1,2\n3,4\n",
            ),
            trusted_context=IDENTITY,
            provider_call_id="invalid-csv",
            response_identity="response-1",
        )
    )
    assert result.status == "invalid_input"
    assert executor.calls == []


def test_adapter_normalizes_legacy_runner_payload_in_execution_path() -> None:
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    class LegacyRunnerExecutor:
        async def execute(self, command, context):
            return {
                "success": True,
                "runner_version": "legacy-1",
                "data": {
                    "nodes": [{"id": "x"}, {"id": "y"}],
                    "edges": [{"from": "x", "to": "y", "arrows": "to"}],
                },
            }

    result = asyncio.run(
        PcAdapter(executor=LegacyRunnerExecutor()).run(
            parameters={"alpha": 0.05},
            adapter_input=AdapterInput(
                data_profile=_profile(),
                input_identity="input-sha",
            ),
            trusted_context=IDENTITY,
            provider_call_id="legacy-runner",
            response_identity="response-1",
        )
    )
    assert result.status == "valid"
    assert result.standardized_graph.edges[0].edge_type == "directed"
    assert result.provenance.algorithm_runner_version == "legacy-1"


def test_adapter_normalizes_legacy_runner_diagnostics_keys() -> None:
    from Agent.deep_agent_tools.adapters.base import AdapterInput

    class LegacyCdfmExecutor:
        async def execute(self, command, context):
            return {
                "success": True,
                "runner_version": "cdfm-runner-v1",
                "data": {
                    "nodes": [{"id": "x"}, {"id": "y"}],
                    "edges": [{"from": "x", "to": "y", "arrows": "to"}],
                },
                "diagnostics": {
                    "n_samples": 120,
                    "n_features": 2,
                    "missing_values": 3,
                    "runtime_sec": 0.5,
                },
            }

    result = asyncio.run(
        CDFMAdapter(executor=LegacyCdfmExecutor()).run(
            parameters={},
            adapter_input=AdapterInput(
                data_profile=_profile(rows=2),
                input_identity="input-sha",
                dataset_csv="x,y\n1,2\n3,4\n",
            ),
            trusted_context=IDENTITY,
            provider_call_id="legacy-cdfm",
            response_identity="response-1",
        )
    )
    assert result.status == "valid"
    assert result.diagnostics.sample_count == 120
    assert result.diagnostics.variable_count == 2
    assert result.diagnostics.metrics["n_samples"] == 120
    assert result.diagnostics.metrics["missing_values"] == 3
    assert result.diagnostics.metrics["runtime_sec"] == 0.5


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
    executor = FakeAlgorithmExecutor()
    backend = build_in_memory_backend(user_id=7)
    context = AgentRunContext(
        execution_guard=guard,
        trusted_identity=IDENTITY,
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
            data_profile=_profile(),
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
