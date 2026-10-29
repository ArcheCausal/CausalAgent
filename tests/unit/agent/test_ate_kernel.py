"""后门调整 ATE 内核：门禁、调整集与线性估计。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from Agent.CausalAgentMCP.inference.ate import AteNotApplicable, estimate_ate
from Agent.deep_agent_tools.models import GraphEdge, StandardizedGraph


def _graph(nodes, edges) -> StandardizedGraph:
    return StandardizedGraph(
        graph_semantics="pdag",
        nodes=list(nodes),
        edges=[
            GraphEdge(source=source, target=target, edge_type=edge_type)
            for source, target, edge_type in edges
        ],
    )


def _data() -> pd.DataFrame:
    return pd.DataFrame({"x": [0.0, 1.0, 2.0, 3.0], "y": [1.0, 3.0, 5.0, 7.0]})


def _chain() -> StandardizedGraph:
    return _graph(
        ("z", "x", "y"),
        [("z", "x", "directed"), ("x", "y", "directed"), ("z", "y", "directed")],
    )


def test_recovers_known_linear_coefficient() -> None:
    rng = np.random.default_rng(7)
    n = 4000
    z = rng.normal(size=n)
    x = 0.8 * z + rng.normal(size=n)
    y = 2.0 * x + 1.5 * z + rng.normal(size=n)
    frame = pd.DataFrame({"z": z, "x": x, "y": y})

    result = estimate_ate(graph=_chain(), data=frame, treatment="x", outcome="y")

    assert result["estimand"] == "ATE"
    assert result["adjustment_set"] == ("z",)
    assert abs(result["estimate"] - 2.0) < 0.05
    assert result["ci95"][0] < 2.0 < result["ci95"][1]
    assert result["estimator"] == "backdoor_ols"
    assert result["n_rows"] == n


def test_ci_narrows_with_sample_size() -> None:
    rng = np.random.default_rng(3)
    widths = []
    for n in (500, 8000):
        z = rng.normal(size=n)
        x = 0.8 * z + rng.normal(size=n)
        y = 2.0 * x + 1.5 * z + rng.normal(size=n)
        result = estimate_ate(
            graph=_chain(),
            data=pd.DataFrame({"z": z, "x": x, "y": y}),
            treatment="x",
            outcome="y",
        )
        widths.append(result["ci95"][1] - result["ci95"][0])
    assert widths[1] < widths[0]


def test_rejects_undirected_edge() -> None:
    graph = _graph(("x", "y"), [("x", "y", "undirected")])
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=_data(), treatment="x", outcome="y")


def test_rejects_bidirected_edge() -> None:
    graph = _graph(("x", "y"), [("x", "y", "bidirected")])
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=_data(), treatment="x", outcome="y")


def test_rejects_directed_cycle() -> None:
    graph = _graph(("x", "y"), [("x", "y", "directed"), ("y", "x", "directed")])
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=_data(), treatment="x", outcome="y")


def test_rejects_missing_variable() -> None:
    graph = _graph(("z", "x"), [("x", "z", "directed")])
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=_data(), treatment="x", outcome="y")


def test_rejects_treatment_not_ancestor() -> None:
    graph = _graph(("x", "y"), [("y", "x", "directed")])
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=_data(), treatment="x", outcome="y")


def test_rejects_missing_column() -> None:
    graph = _graph(("x", "y"), [("x", "y", "directed")])
    frame = pd.DataFrame({"x": [0.0, 1.0, 2.0, 3.0]})
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=frame, treatment="x", outcome="y")


def test_rejects_nan_and_non_numeric() -> None:
    graph = _graph(("x", "y"), [("x", "y", "directed")])
    with_nan = pd.DataFrame({"x": [0.0, np.nan, 2.0, 3.0], "y": [1.0, 3.0, 5.0, 7.0]})
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=with_nan, treatment="x", outcome="y")
    with_text = pd.DataFrame({"x": ["a", "b", "c", "d"], "y": [1.0, 3.0, 5.0, 7.0]})
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=with_text, treatment="x", outcome="y")


def test_linear_data_has_low_residual_rank_correlation() -> None:
    rng = np.random.default_rng(7)
    n = 2000
    z = rng.normal(size=n)
    x = 0.8 * z + rng.normal(size=n)
    y = 2.0 * x + 1.5 * z + rng.normal(size=n)
    metrics = estimate_ate(
        graph=_chain(), data=pd.DataFrame({"z": z, "x": x, "y": y}),
        treatment="x", outcome="y",
    )["diagnostics_metrics"]
    assert metrics["residual_spearman_max"] < 0.1
    assert metrics["residual_abs_spearman_max"] < 0.1


def test_quadratic_outcome_raises_residual_rank_correlation() -> None:
    # 右偏处理变量：均匀分布下二次残差对 x 对称，秩相关会抵消。
    rng = np.random.default_rng(13)
    x = rng.exponential(size=2000)
    y = x**2 + 0.1 * rng.normal(size=2000)
    graph = _graph(("x", "y"), [("x", "y", "directed")])
    metrics = estimate_ate(
        graph=graph, data=pd.DataFrame({"x": x, "y": y}),
        treatment="x", outcome="y",
    )["diagnostics_metrics"]
    assert metrics["residual_spearman_max"] > 0.5


def test_heteroscedastic_residuals_raise_absolute_rank_correlation() -> None:
    rng = np.random.default_rng(17)
    x = rng.uniform(0.0, 2.0, size=3000)
    y = x + (0.1 + 0.9 * x) * rng.normal(size=3000)
    graph = _graph(("x", "y"), [("x", "y", "directed")])
    metrics = estimate_ate(
        graph=graph, data=pd.DataFrame({"x": x, "y": y}),
        treatment="x", outcome="y",
    )["diagnostics_metrics"]
    assert metrics["residual_abs_spearman_max"] > 0.2


def test_rejects_collinear_design() -> None:
    graph = _graph(
        ("z", "x", "y"),
        [("z", "x", "directed"), ("x", "y", "directed")],
    )
    frame = pd.DataFrame(
        {"z": [1.0, 1.0, 1.0, 1.0], "x": [0.0, 1.0, 2.0, 3.0], "y": [1.0, 3.0, 5.0, 7.0]}
    )
    with pytest.raises(AteNotApplicable):
        estimate_ate(graph=graph, data=frame, treatment="x", outcome="y")
