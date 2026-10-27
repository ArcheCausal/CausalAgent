"""真实 MCP service、进程池及 ATE runner 的离线组件验证。

输入由进程内 ValidatedAlgorithmInput 提供，不连接数据库或 HTTP，不能作为
Job 数据库绑定、租约 fencing、checkpoint 或生产 MCP 的验收证据。
算法、CSV 画像、共享检查和 ProcessPoolExecutor 均使用实际实现，无替代 runner。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.CausalAgentMCP.executor_pool import BoundedProcessPool
from Agent.CausalAgentMCP.runner_registry import BACKDOOR_OLS_RUNNER_VERSION, _ate, build_default_registry
from Agent.CausalAgentMCP.service import CausalMcpService, McpServiceError, _estimation_result
from Agent.deep_agent_tools.algorithm_specs import CAUSAL_BACKDOOR_OLS_SPEC
from Agent.deep_agent_tools.ate_applicability import ATE_REASON_SUMMARIES, check_ate_graph, check_ate_profile
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand, AlgorithmResult, McpInvocationContext, StandardizedGraph
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
from Agent.measurement_semantics import ValidatedAlgorithmInput


GRAPH = {
    "graph_semantics": "pdag",
    "nodes": ["z", "t", "y"],
    "edges": [
        {"source": "z", "target": "t", "edge_type": "directed"},
        {"source": "z", "target": "y", "edge_type": "directed"},
        {"source": "t", "target": "y", "edge_type": "directed"},
    ],
}
PARAMETERS = {"graph_result_ref": "source-discovery:0", "treatment": "t", "outcome": "y"}


def _frame() -> pd.DataFrame:
    rng = np.random.default_rng(41)
    z = rng.normal(size=320)
    t = 0.8 * z + rng.normal(size=320)
    y = 2.0 * t + 1.5 * z + rng.normal(size=320)
    return pd.DataFrame({"z": z, "t": t, "y": y})


def _config() -> McpServerConfig:
    return McpServerConfig(
        service_token="offline-component-token", signing_key_current="offline-component-key",
        signing_key_previous=None, signing_key_id="current", signing_key_previous_id="previous",
    )


def _request(csv_data: str, *, graph: dict | None = None):
    digest = hashlib.sha256(csv_data.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    context = McpInvocationContext(
        invocation_id=str(uuid4()), job_id=str(uuid4()), session_id=str(uuid4()), user_id=7,
        attempt_count=1, lease_epoch=3, worker_id="component-test:1",
        input_snapshot_digest=digest, source_input_identity="frozen-source-hash",
        analysis_dataset_id=17, issued_at=now, expires_at=now + timedelta(minutes=5), key_id="current",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=context.invocation_id, capability_id=CAUSAL_BACKDOOR_OLS_SPEC.capability_id,
        capability_version=CAUSAL_BACKDOOR_OLS_SPEC.version, spec_digest=CAUSAL_BACKDOOR_OLS_SPEC.spec_digest,
        provider_call_id="ate-call", input_identity=digest, parameters=PARAMETERS,
        artifacts={"standardized_graph": deepcopy(GRAPH if graph is None else graph)},
    )
    payload = {
        "command": command.model_dump(mode="json"), "trusted_context": context.model_dump(mode="json"),
        "signature": sign_invocation(context, command, _config().signing_key_current),
    }
    return command, context, payload


def _execute(frame: pd.DataFrame, *, semantics=None, graph=None, config=None, wrong_identity=False):
    csv_data = frame.to_csv(index=False)
    command, expected_context, payload = _request(csv_data, graph=graph)
    trusted_input = ValidatedAlgorithmInput(csv_data=csv_data, semantic_kinds=semantics or {})
    reads = []

    def read_in_process_input(context):
        """只验证本用例的内存输入绑定，不模拟数据库查询或租约事务。"""
        reads.append(context)
        if wrong_identity or context != expected_context:
            raise McpServiceError(SafeErrorCode.MCP_CONTEXT_INVALID)
        return trusted_input

    async def scenario():
        pool = BoundedProcessPool(process_workers=1)
        service = CausalMcpService(
            config=config or _config(), registry=build_default_registry(), executor_pool=pool,
            authority_reader=read_in_process_input,
        )
        await pool.start()
        try:
            response = await service.execute_payload(payload)
        finally:
            await pool.close()
        assert response["ok"] is True
        return response, AlgorithmResult.model_validate(response["result"])

    response, result = asyncio.run(scenario())
    return response, result, reads


def _assert_not_applicable(result, reason):
    assert result.status == "not_applicable"
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_NOT_APPLICABLE
    assert result.diagnostics.summary == ATE_REASON_SUMMARIES[reason]
    assert result.ate_check.applicability == "not_applicable"
    assert result.ate_check.reason_code == reason
    assert result.standardized_graph is None
    assert result.causal_effect is None


def test_continuous_confounder_runs_real_service_and_process_runner():
    response, result, reads = _execute(_frame())
    assert result.status == "valid"
    assert result.ate_check.applicability == "ready"
    assert result.ate_check.execution_columns == ("t", "y", "z")
    assert result.ate_check.adjustment_set == ("z",)
    assert result.causal_effect.adjustment_set == result.ate_check.adjustment_set
    assert result.causal_effect.graph_result_ref == result.ate_check.graph_result_ref
    assert result.causal_effect.treatment == result.ate_check.treatment
    assert result.causal_effect.outcome == result.ate_check.outcome
    assert abs(result.causal_effect.estimate - 2.0) < 0.2
    assert result.causal_effect.n_rows == len(_frame())
    assert result.implementation.runner_version == BACKDOOR_OLS_RUNNER_VERSION == "backdoor-ols-runner-v2"
    assert result.provenance.analysis_dataset_id == 17
    assert result.provenance.source_input_identity == "frozen-source-hash"
    assert response["trace"]["executor_slot_id"].startswith("process-")
    assert len(reads) == 3


@pytest.mark.parametrize("kind", ["category", "ordinal", "binary"])
def test_saved_adjustment_semantics_block_numeric_codes_before_runner(kind):
    frame = _frame()
    frame["z"] = np.arange(len(frame)) % 3
    response, result, reads = _execute(frame, semantics={"z": kind})
    _assert_not_applicable(result, "ATE_ADJUSTMENT_SEMANTICS_UNSUPPORTED")
    assert result.ate_check.adjustment_set == ("z",)
    assert result.ate_check.execution_columns == ("t", "y", "z")
    assert "executor_slot_id" not in response["trace"]
    assert "raw_payload" not in response
    assert len(reads) == 1


def test_unused_category_and_missing_values_do_not_block_actual_columns():
    frame = _frame()
    frame["unused"] = ["category" if i % 2 else None for i in range(len(frame))]
    _, result, _ = _execute(frame, semantics={"unused": "category"})
    assert result.status == "valid"
    assert result.ate_check.execution_columns == ("t", "y", "z")


def test_undeclared_integer_semantics_are_not_inferred_as_category():
    frame = _frame()
    frame["z"] = np.arange(len(frame)) % 3
    _, result, _ = _execute(frame)
    assert result.status == "valid"


@pytest.mark.parametrize("problem,reason", [
    ("absent", "ATE_EXECUTION_COLUMN_MISSING"),
    ("text", "ATE_EXECUTION_COLUMN_NOT_NUMERIC"),
    ("missing", "ATE_EXECUTION_COLUMN_MISSING_VALUES"),
    ("constant", "ATE_EXECUTION_COLUMN_CONSTANT"),
    ("infinite", "ATE_EXECUTION_COLUMN_NON_FINITE"),
])
def test_all_adjustment_data_constraints_use_trusted_csv(problem, reason):
    frame = _frame()
    if problem == "absent":
        frame = frame.drop(columns="z")
    elif problem == "text":
        frame["z"] = ["a", "b"] * (len(frame) // 2)
    elif problem == "constant":
        frame["z"] = 1
    else:
        frame.loc[0, "z"] = np.nan if problem == "missing" else np.inf
    response, result, _ = _execute(frame)
    _assert_not_applicable(result, reason)
    assert "executor_slot_id" not in response["trace"]


@pytest.mark.parametrize("edge_type,semantics,reason", [
    ("undirected", "pdag", "ATE_GRAPH_HAS_UNDIRECTED_EDGE"),
    ("bidirected", "acyclic_directed_mixed_graph", "ATE_GRAPH_HAS_BIDIRECTED_EDGE"),
])
def test_legal_discovery_graph_unsupported_by_ols_has_specific_reason(edge_type, semantics, reason):
    graph = deepcopy(GRAPH)
    graph["graph_semantics"] = semantics
    graph["edges"] = [
        {"source": "z", "target": "t", "edge_type": edge_type},
        {"source": "t", "target": "y", "edge_type": "directed"},
    ]
    _, result, _ = _execute(_frame(), graph=graph)
    _assert_not_applicable(result, reason)
    assert result.ate_check.adjustment_set is None
    assert result.ate_check.execution_columns is None


@pytest.mark.parametrize("problem", ["illegal_edge", "cycle", "duplicate", "unknown_semantics"])
def test_invalid_discovery_graph_is_invalid_input_without_record(problem):
    graph = deepcopy(GRAPH)
    if problem == "illegal_edge":
        graph["edges"][0]["edge_type"] = "bidirected"
    elif problem == "cycle":
        graph["edges"].append({"source": "y", "target": "z", "edge_type": "directed"})
    elif problem == "duplicate":
        graph["edges"].append(deepcopy(graph["edges"][0]))
    else:
        graph["graph_semantics"] = "unknown"
    response, result, _ = _execute(_frame(), graph=graph)
    assert result.status == "invalid_input"
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_INPUT_INVALID
    assert result.ate_check is None
    assert "executor_slot_id" not in response["trace"]


def test_input_binding_rejection_does_not_claim_completed_check():
    response, result, _ = _execute(_frame(), wrong_identity=True)
    assert result.diagnostics.safe_error_code == SafeErrorCode.MCP_CONTEXT_INVALID
    assert result.ate_check is None
    assert "executor_slot_id" not in response["trace"]


def test_kernel_collinearity_updates_completed_record_and_controlled_diagnostic():
    frame = _frame()
    frame["t"] = 2 * frame["z"]
    response, result, _ = _execute(frame)
    _assert_not_applicable(result, "ATE_DESIGN_MATRIX_SINGULAR")
    assert result.ate_check.adjustment_set == ("z",)
    assert response["raw_payload"]["reason_code"] == result.ate_check.reason_code
    assert "message" not in response["raw_payload"]
    assert result.implementation.runner_version == "backdoor-ols-runner-v2"


def test_insufficient_dof_is_not_applicable_without_changing_rows_or_adjustments():
    _, result, _ = _execute(_frame().iloc[:3])
    _assert_not_applicable(result, "ATE_INSUFFICIENT_DOF")
    assert result.ate_check.execution_columns == ("t", "y", "z")


def test_real_process_deadline_preserves_check_without_graph_or_raw_payload():
    # 实际子进程启动与回归使用 1ms 期限；不替换时钟、进程池或算法。
    response, result, reads = _execute(
        _frame(), config=replace(_config(), algorithm_deadline_seconds=0.001),
    )
    assert result.status == "timed_out"
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_TIMED_OUT
    assert result.ate_check.applicability == "ready"
    assert result.ate_check.execution_columns == ("t", "y", "z")
    assert result.standardized_graph is None
    assert result.raw_result_ref is None
    assert "raw_payload" not in response
    assert len(reads) == 3


def test_real_numeric_execution_failure_preserves_ready_check():
    frame = _frame()
    # 输入仍是有限数值；真实 OLS 的残差方差计算溢出，不能伪称不适用。
    frame["y"] = np.where(np.arange(len(frame)) % 2, 1e308, -1e308)
    response, result, _ = _execute(frame)
    assert result.status == "execution_failed"
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_EXECUTION_FAILED
    assert result.ate_check.applicability == "ready"
    assert result.ate_check.reason_code is None
    assert result.standardized_graph is None
    assert "raw_payload" not in response


@pytest.mark.parametrize("change", [
    {"graph_result_ref": "another-source:0"}, {"treatment": "z"},
    {"outcome": "z"}, {"adjustment_set": []},
])
def test_success_adapter_rejects_effect_mismatching_real_completed_check(change):
    csv_data = _frame().to_csv(index=False)
    command, context, _ = _request(csv_data)
    graph = StandardizedGraph.model_validate(GRAPH)
    check = check_ate_profile(
        check_ate_graph(graph=graph, **PARAMETERS),
        profile=build_data_profile_from_csv(csv_data), semantic_kinds={},
    )
    payload = _ate(csv_data, PARAMETERS, {"standardized_graph": GRAPH})
    payload["causal_effect"].update(change)
    with pytest.raises(ValueError):
        _estimation_result(
            payload, command=command, context=context, capability_version=CAUSAL_BACKDOOR_OLS_SPEC.version,
            graph=graph, data_view=None, ate_check=check,
        )
