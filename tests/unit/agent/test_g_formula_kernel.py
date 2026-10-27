"""g-computation 内核：线性一致性、非线性差异、bootstrap 与拒绝路径。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from Agent.CausalAgentMCP.inference.ate import estimate_ate
from Agent.CausalAgentMCP.inference.g_formula import (
    GFormulaNotApplicable,
    estimate_g_formula,
)
from Agent.deep_agent_tools.models import GraphEdge, StandardizedGraph


def _graph(nodes, edges) -> StandardizedGraph:
    return StandardizedGraph(
        graph_semantics="causal_estimation_input",
        nodes=nodes,
        edges=[GraphEdge(source=source, target=target) for source, target in edges],
    )


def _linear_dag() -> tuple[StandardizedGraph, pd.DataFrame]:
    rng = np.random.default_rng(11)
    n = 10_000
    z = rng.normal(size=n)
    x = 0.8 * z + rng.normal(size=n)
    y = 2.0 * x + 1.5 * z + rng.normal(size=n)
    return (
        _graph(["z", "x", "y"], [("z", "x"), ("z", "y"), ("x", "y")]),
        pd.DataFrame({"z": z, "x": x, "y": y}),
    )


def test_linear_g_formula_matches_backdoor_ols() -> None:
    graph, frame = _linear_dag()
    g_formula = estimate_g_formula(
        graph=graph,
        data=frame,
        treatment="x",
        outcome="y",
        contrast=(0.0, 1.0),
        bootstrap=0,
    )
    ols = estimate_ate(graph=graph, data=frame, treatment="x", outcome="y")
    assert abs(g_formula["estimate"] - ols["estimate"]) < 0.02
    assert g_formula["estimator"] == "g_formula"
    assert g_formula["standard_error"] is None
    assert g_formula["ci95"] is None
    assert g_formula["bootstrap_n"] == 0


def test_flexible_model_captures_nonlinear_effect() -> None:
    rng = np.random.default_rng(23)
    x = rng.normal(size=4000)
    y = 3.0 * x**2 + 0.5 * rng.normal(size=4000)
    graph = _graph(["x", "y"], [("x", "y")])
    frame = pd.DataFrame({"x": x, "y": y})

    linear = estimate_g_formula(
        graph=graph, data=frame, treatment="x", outcome="y",
        contrast=(0.0, 1.0), outcome_model="linear", bootstrap=200,
    )
    flexible = estimate_g_formula(
        graph=graph, data=frame, treatment="x", outcome="y",
        contrast=(0.0, 1.0), outcome_model="hgb", bootstrap=0,
    )

    low, high = linear["ci95"]
    assert not (low <= flexible["estimate"] <= high)


def test_bootstrap_is_reproducible_and_contains_point_estimate() -> None:
    graph, frame = _linear_dag()
    first = estimate_g_formula(
        graph=graph, data=frame.head(400), treatment="x", outcome="y",
        contrast=(0.0, 1.0), bootstrap=20,
    )
    second = estimate_g_formula(
        graph=graph, data=frame.head(400), treatment="x", outcome="y",
        contrast=(0.0, 1.0), bootstrap=20,
    )
    assert first == second
    low, high = first["ci95"]
    assert low < high
    assert low <= first["estimate"] <= high


@pytest.mark.parametrize(
    "graph,treatment,outcome,frame,message",
    [
        (
            _graph(["x", "y"], []),
            "x", "y", pd.DataFrame({"x": [0.0, 1.0], "y": [1.0, 2.0]}),
            "祖先",
        ),
        (
            StandardizedGraph(
                graph_semantics="causal_estimation_input",
                nodes=["x", "y"],
                edges=[GraphEdge(source="x", target="y", edge_type="undirected")],
            ),
            "x", "y", pd.DataFrame({"x": [0.0, 1.0], "y": [1.0, 2.0]}),
            "未定向",
        ),
        (
            _graph(["x", "y"], [("x", "y"), ("y", "x")]),
            "x", "y", pd.DataFrame({"x": [0.0, 1.0], "y": [1.0, 2.0]}),
            "环",
        ),
        (
            _graph(["x", "y"], [("x", "y")]),
            "x", "y", pd.DataFrame({"x": [0.0, 1.0]}),
            "缺少",
        ),
    ],
)
def test_g_formula_rejects_invalid_input(graph, treatment, outcome, frame, message) -> None:
    with pytest.raises(GFormulaNotApplicable, match=message):
        estimate_g_formula(
            graph=graph, data=frame, treatment=treatment, outcome=outcome,
            contrast=(0.0, 1.0), bootstrap=0,
        )
