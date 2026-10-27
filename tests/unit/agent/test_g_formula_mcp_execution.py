"""g_formula runner、spec 与估计结果组装。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from Agent.CausalAgentMCP.runner_registry import _g_formula
from Agent.CausalAgentMCP.service import _estimation_result
from Agent.deep_agent_tools.algorithm_specs import (
    CAUSAL_G_FORMULA_SPEC,
    get_algorithm_spec,
)
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    McpInvocationContext,
    StandardizedGraph,
)

GRAPH_PAYLOAD = {
    "graph_semantics": "pdag",
    "nodes": ["x", "y"],
    "edges": [{"source": "x", "target": "y", "edge_type": "directed"}],
}
PARAMETERS = {
    "graph_result_ref": "inv-1:0",
    "treatment": "x",
    "outcome": "y",
    "contrast": [0.0, 1.0],
    "outcome_model": "linear",
    "bootstrap": 0,
}


def _frame() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    x = rng.normal(size=1000)
    return pd.DataFrame({"x": x, "y": 2.0 * x + rng.normal(size=1000)})


def _command() -> AlgorithmExecutionCommand:
    return AlgorithmExecutionCommand(
        invocation_id="00000000-0000-0000-0000-000000000011",
        capability_id="causal.g_formula",
        capability_version="1.0",
        spec_digest=CAUSAL_G_FORMULA_SPEC.spec_digest,
        provider_call_id="call-gf-1",
        input_identity="input-sha",
        parameters=PARAMETERS,
        artifacts={"standardized_graph": GRAPH_PAYLOAD},
    )


def _context(command: AlgorithmExecutionCommand) -> McpInvocationContext:
    now = datetime.now(timezone.utc)
    return McpInvocationContext(
        invocation_id=command.invocation_id,
        job_id="00000000-0000-0000-0000-000000000012",
        session_id="00000000-0000-0000-0000-000000000013",
        user_id=7,
        attempt_count=0,
        lease_epoch=1,
        worker_id="worker-1",
        input_snapshot_digest="input-sha",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        key_id="key-1",
    )


def test_spec_is_registered_with_estimation_kind() -> None:
    assert get_algorithm_spec("causal.g_formula") is CAUSAL_G_FORMULA_SPEC
    assert CAUSAL_G_FORMULA_SPEC.kind == "causal_estimation"
    assert CAUSAL_G_FORMULA_SPEC.default_timeout_seconds == 600
    assert CAUSAL_G_FORMULA_SPEC.default_concurrency == 1


def test_runner_estimates_from_artifact_graph() -> None:
    payload = _g_formula(
        _frame().to_csv(index=False),
        PARAMETERS,
        {"standardized_graph": GRAPH_PAYLOAD},
    )
    assert payload["success"] is True
    assert payload["algorithm"] == "g_formula"
    assert abs(payload["causal_effect"]["estimate"] - 2.0) < 0.1
    assert payload["causal_effect"]["bootstrap_n"] == 0


def test_runner_reports_not_applicable() -> None:
    payload = _g_formula(
        _frame().to_csv(index=False),
        PARAMETERS,
        {
            "standardized_graph": {
                "graph_semantics": "pdag",
                "nodes": ["x", "y"],
                "edges": [{"source": "x", "target": "y", "edge_type": "undirected"}],
            }
        },
    )
    assert payload["success"] is False
    assert payload["error_type"] == "NotApplicable"


def test_estimation_result_injects_source_graph_ref_and_controls() -> None:
    command = _command()
    graph = StandardizedGraph.model_validate(GRAPH_PAYLOAD)
    payload = {
        "success": True,
        "algorithm": "g_formula",
        "runner_version": "g-formula-runner-v1",
        "causal_effect": {
            "estimand": "ATE",
            "treatment": "x",
            "outcome": "y",
            "estimate": 2.0,
            "standard_error": 0.1,
            "ci95": [1.8, 2.2],
            "estimator": "g_formula",
            "n_rows": 1000,
            "contrast": [0.0, 1.0],
            "outcome_model": "linear",
            "bootstrap_n": 200,
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
    assert result.causal_effect is not None
    assert result.causal_effect.graph_result_ref == "inv-1:0"
    assert result.causal_effect.contrast == (0.0, 1.0)
    assert result.causal_effect.outcome_model == "linear"
    assert result.causal_effect.bootstrap_n == 200
