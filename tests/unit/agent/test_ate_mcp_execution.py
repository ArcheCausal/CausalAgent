"""MCP 侧 ATE：runner 透传、内核接线与结果组装。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from Agent.CausalAgentMCP.executor_pool import _run_runner
from Agent.CausalAgentMCP.runner_registry import _ate
from Agent.CausalAgentMCP.service import _estimation_result
from Agent.deep_agent_tools.algorithm_specs import CAUSAL_ATE_SPEC
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    McpInvocationContext,
    StandardizedGraph,
)


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
        capability_id="causal.ate",
        capability_version="1.0",
        spec_digest=CAUSAL_ATE_SPEC.spec_digest,
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
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        key_id="key-1",
    )


def test_run_runner_passes_artifacts() -> None:
    captured: dict[str, object] = {}

    def runner(csv_data, parameters, artifacts):
        captured["csv"] = csv_data
        captured["parameters"] = dict(parameters)
        captured["artifacts"] = dict(artifacts)
        return {"success": True}

    envelope = _run_runner(
        runner, "x,y\n1,2\n", {"alpha": 0.05}, {"standardized_graph": GRAPH_PAYLOAD}
    )
    assert envelope.value == {"success": True}
    assert captured["artifacts"] == {"standardized_graph": GRAPH_PAYLOAD}


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


def test_estimation_result_reuses_input_graph_and_effect() -> None:
    command = _command()
    graph = StandardizedGraph.model_validate(GRAPH_PAYLOAD)
    payload = {
        "success": True,
        "algorithm": "backdoor_ols",
        "runner_version": "ate-runner-v1",
        "causal_effect": {
            "estimand": "ATE",
            "treatment": "x",
            "outcome": "y",
            "adjustment_set": ["z"],
            "estimate": 2.0,
            "standard_error": 0.1,
            "ci95": [1.8, 2.2],
            "estimator": "backdoor_ols",
            "n_rows": 500,
        },
    }
    result = _estimation_result(
        payload,
        command=command,
        context=_context(command),
        capability_version="1.0",
        graph=graph,
        data_view=None,
    )
    assert result.status == "valid"
    assert result.standardized_graph == graph
    assert result.causal_effect is not None
    assert result.causal_effect.graph_result_ref == "inv-1:0"
    assert result.causal_effect.estimate == 2.0
    assert result.causal_effect.n_rows == 500


def test_estimation_result_rejects_mismatched_pair() -> None:
    command = _command()
    graph = StandardizedGraph.model_validate(GRAPH_PAYLOAD)
    payload = {
        "success": True,
        "causal_effect": {
            "estimand": "ATE",
            "treatment": "z",
            "outcome": "y",
            "adjustment_set": [],
            "estimate": 1.0,
            "standard_error": 0.1,
            "ci95": [0.9, 1.1],
            "estimator": "backdoor_ols",
            "n_rows": 500,
        },
    }
    try:
        _estimation_result(
            payload,
            command=command,
            context=_context(command),
            capability_version="1.0",
            graph=graph,
            data_view=None,
        )
    except ValueError:
        return
    raise AssertionError("mismatched treatment/outcome must be rejected")
