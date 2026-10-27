"""MCP 侧 ATE：runner 透传、内核接线与结果组装。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from Agent.CausalAgentMCP.executor_pool import _run_runner
from Agent.CausalAgentMCP.runner_registry import BACKDOOR_OLS_RUNNER_VERSION, _ate
from Agent.CausalAgentMCP.service import _estimation_result
from Agent.deep_agent_tools.algorithm_specs import CAUSAL_BACKDOOR_OLS_SPEC
from Agent.deep_agent_tools.ate_applicability import check_ate_graph, check_ate_profile
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    McpInvocationContext,
    StandardizedGraph,
)
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv


GRAPH_PAYLOAD = {
    "graph_semantics": "pdag",
    "nodes": ["z", "x", "y"],
    "edges": [
        {"source": "z", "target": "x", "edge_type": "directed"},
        {"source": "x", "target": "y", "edge_type": "directed"},
        {"source": "z", "target": "y", "edge_type": "directed"},
    ],
}

PARAMETERS = {"graph_result_ref": "inv-1:0", "treatment": "x", "outcome": "y"}


def _frame() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    n = 2000
    z = rng.normal(size=n)
    x = 0.8 * z + rng.normal(size=n)
    y = 2.0 * x + 1.5 * z + rng.normal(size=n)
    return pd.DataFrame({"z": z, "x": x, "y": y})


def _command() -> AlgorithmExecutionCommand:
    return AlgorithmExecutionCommand(
        invocation_id="00000000-0000-0000-0000-000000000001",
        capability_id="causal.backdoor_ols",
        capability_version=CAUSAL_BACKDOOR_OLS_SPEC.version,
        spec_digest=CAUSAL_BACKDOOR_OLS_SPEC.spec_digest,
        provider_call_id="call-1",
        input_identity="input-sha",
        parameters=PARAMETERS,
        artifacts={"standardized_graph": GRAPH_PAYLOAD},
    )


def _context(command: AlgorithmExecutionCommand) -> McpInvocationContext:
    now = datetime.now(timezone.utc)
    return McpInvocationContext(
        invocation_id=command.invocation_id,
        job_id="00000000-0000-0000-0000-000000000001",
        session_id="00000000-0000-0000-0000-000000000002",
        user_id=7,
        attempt_count=0,
        lease_epoch=1,
        worker_id="worker-1",
        input_snapshot_digest="input-sha",
        source_input_identity="source-sha",
        analysis_dataset_id=17,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        key_id="key-1",
    )


def test_run_runner_passes_artifacts() -> None:
    envelope = _run_runner(
        _ate, _frame().to_csv(index=False), PARAMETERS, {"standardized_graph": GRAPH_PAYLOAD}
    )
    assert envelope.value["success"] is True
    assert envelope.value["runner_version"] == BACKDOOR_OLS_RUNNER_VERSION == "backdoor-ols-runner-v2"
    assert envelope.value["causal_effect"]["adjustment_set"] == ("z",)
    assert abs(envelope.value["causal_effect"]["estimate"] - 2.0) < 0.1


def test_ate_runner_estimates_from_artifact_graph() -> None:
    payload = _ate(
        _frame().to_csv(index=False),
        PARAMETERS,
        {"standardized_graph": GRAPH_PAYLOAD},
    )
    assert payload["success"] is True
    assert abs(payload["causal_effect"]["estimate"] - 2.0) < 0.1


def test_ate_runner_reports_not_applicable() -> None:
    payload = _ate(
        _frame().to_csv(index=False),
        PARAMETERS,
        {
            "standardized_graph": {
                "graph_semantics": "pdag",
                "nodes": ["z", "x", "y"],
                "edges": [{"source": "z", "target": "x", "edge_type": "undirected"}],
            }
        },
    )
    assert payload["success"] is False
    assert payload["error_type"] == "NotApplicable"
    assert payload["reason_code"] == "ATE_GRAPH_HAS_UNDIRECTED_EDGE"
    assert "message" not in payload


def _completed_check(csv_data: str, graph: StandardizedGraph):
    return check_ate_profile(
        check_ate_graph(graph=graph, **PARAMETERS),
        profile=build_data_profile_from_csv(csv_data), semantic_kinds={},
    )


def test_estimation_result_reuses_input_graph_and_effect() -> None:
    command = _command()
    graph = StandardizedGraph.model_validate(GRAPH_PAYLOAD)
    csv_data = _frame().to_csv(index=False)
    check = _completed_check(csv_data, graph)
    payload = _ate(csv_data, PARAMETERS, {"standardized_graph": GRAPH_PAYLOAD})
    result = _estimation_result(
        payload,
        command=command,
        context=_context(command),
        capability_version=CAUSAL_BACKDOOR_OLS_SPEC.version,
        graph=graph,
        data_view=None,
        ate_check=check,
    )
    assert result.status == "valid"
    assert result.standardized_graph == graph
    assert result.causal_effect is not None
    assert result.causal_effect.graph_result_ref == "inv-1:0"
    assert result.causal_effect.estimate == payload["causal_effect"]["estimate"]
    assert result.causal_effect.n_rows == len(_frame())
    assert result.ate_check == check
    assert result.implementation.runner_version == "backdoor-ols-runner-v2"
    assert result.provenance.algorithm_runner_version == "backdoor-ols-runner-v2"


def test_estimation_result_rejects_mismatched_pair() -> None:
    command = _command()
    graph = StandardizedGraph.model_validate(GRAPH_PAYLOAD)
    csv_data = _frame().to_csv(index=False)
    payload = _ate(csv_data, PARAMETERS, {"standardized_graph": GRAPH_PAYLOAD})
    payload["causal_effect"]["treatment"] = "z"
    try:
        _estimation_result(
            payload,
            command=command,
            context=_context(command),
            capability_version=CAUSAL_BACKDOOR_OLS_SPEC.version,
            graph=graph,
            data_view=None,
            ate_check=_completed_check(csv_data, graph),
        )
    except ValueError:
        return
    raise AssertionError("mismatched treatment/outcome must be rejected")
