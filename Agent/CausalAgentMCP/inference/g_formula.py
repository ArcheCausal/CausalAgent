"""G-computation 的确定性内核：结构前置、逐方程拟合、do 模拟与 bootstrap。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

from Agent.deep_agent_tools.models import StandardizedGraph


class GFormulaNotApplicable(ValueError):
    """当前图或数据无法支撑 g-formula；消息面向模型，不含堆栈。"""


def _parents(graph: StandardizedGraph) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = {node: [] for node in graph.nodes}
    for edge in graph.edges:
        parents[edge.target].append(edge.source)
    return {node: sorted(values) for node, values in parents.items()}


def _topological_order(parents: dict[str, list[str]]) -> list[str]:
    children: dict[str, list[str]] = {node: [] for node in parents}
    pending = {node: set(values) for node, values in parents.items()}
    for node, values in parents.items():
        for parent in values:
            children[parent].append(node)
    ready = [node for node, values in pending.items() if not values]
    order: list[str] = []
    while ready:
        node = ready.pop()
        order.append(node)
        for child in children[node]:
            pending[child].discard(node)
            if not pending[child]:
                ready.append(child)
    if len(order) != len(parents):
        raise GFormulaNotApplicable("因果图包含有向环，无法确定 g-formula 的因果顺序")
    return order


def _ancestors(parents: dict[str, list[str]], target: str) -> set[str]:
    seen = {target}
    stack = [target]
    while stack:
        node = stack.pop()
        for parent in parents[node]:
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def _affected(
    order: list[str], parents: dict[str, list[str]], treatment: str
) -> set[str]:
    affected = {treatment}
    for node in order:
        if node == treatment:
            continue
        if any(parent in affected for parent in parents[node]):
            affected.add(node)
    return affected


def _estimator(model: str) -> Any:
    if model == "hgb":
        return HistGradientBoostingRegressor(
            max_depth=3,
            learning_rate=0.05,
            random_state=0,
        )
    return LinearRegression()


def _fit_models(
    frame: pd.DataFrame,
    *,
    order: list[str],
    parents: dict[str, list[str]],
    affected: set[str],
    treatment: str,
    outcome: str,
    outcome_model: str,
) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for node in order:
        if node == treatment or node not in affected:
            continue
        estimator = _estimator(outcome_model if node == outcome else "linear")
        estimator.fit(
            frame[parents[node]].to_numpy(dtype=float),
            frame[node].to_numpy(dtype=float),
        )
        models[node] = estimator
    return models


def _do_mean(
    frame: pd.DataFrame,
    *,
    models: dict[str, Any],
    order: list[str],
    parents: dict[str, list[str]],
    treatment: str,
    outcome: str,
    value: float,
) -> float:
    simulated = frame.copy()
    simulated[treatment] = value
    for node in order:
        if node == treatment or node not in models:
            continue
        simulated[node] = models[node].predict(
            simulated[parents[node]].to_numpy(dtype=float)
        )
    return float(simulated[outcome].mean())


def _effect(
    frame: pd.DataFrame,
    *,
    order: list[str],
    parents: dict[str, list[str]],
    treatment: str,
    outcome: str,
    contrast: tuple[float, float],
    outcome_model: str,
) -> float:
    models = _fit_models(
        frame,
        order=order,
        parents=parents,
        affected=_affected(order, parents, treatment),
        treatment=treatment,
        outcome=outcome,
        outcome_model=outcome_model,
    )
    low = _do_mean(
        frame, models=models, order=order, parents=parents,
        treatment=treatment, outcome=outcome, value=contrast[0],
    )
    high = _do_mean(
        frame, models=models, order=order, parents=parents,
        treatment=treatment, outcome=outcome, value=contrast[1],
    )
    return high - low


def estimate_g_formula(
    *,
    graph: StandardizedGraph,
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    contrast: tuple[float, float],
    outcome_model: str = "linear",
    bootstrap: int = 200,
) -> dict[str, Any]:
    """按 g-computation 估计 contrast 的 ATE；不可识别时抛 GFormulaNotApplicable。"""

    if any(edge.edge_type != "directed" for edge in graph.edges):
        raise GFormulaNotApplicable(
            "图中存在未定向或双箭头边，无法确定 g-formula 的因果顺序"
        )
    if treatment not in graph.nodes or outcome not in graph.nodes:
        raise GFormulaNotApplicable("图中缺少处理变量或结果变量")
    if treatment == outcome:
        raise GFormulaNotApplicable("处理变量与结果变量不能相同")
    parents = _parents(graph)
    order = _topological_order(parents)
    if treatment not in _ancestors(parents, outcome):
        raise GFormulaNotApplicable("处理变量不是结果变量的祖先，效应不可识别")
    if outcome_model not in ("linear", "hgb"):
        raise GFormulaNotApplicable("不支持的结果方程模型")
    if bootstrap < 0:
        raise GFormulaNotApplicable("bootstrap 次数不能为负")

    contrast_values = (float(contrast[0]), float(contrast[1]))
    if not all(np.isfinite(value) for value in contrast_values):
        raise GFormulaNotApplicable("对比取值必须是有限数值")

    relevant = _ancestors(parents, outcome)
    missing = sorted(relevant - set(data.columns))
    if missing:
        raise GFormulaNotApplicable("数据缺少估计所需列：" + "、".join(missing))
    frame = data[sorted(relevant)].apply(pd.to_numeric, errors="coerce").astype(float)
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise GFormulaNotApplicable("估计所用列包含缺失值、非数值或非有限值")
    relevant_order = [node for node in order if node in relevant]

    estimate = _effect(
        frame,
        order=relevant_order,
        parents=parents,
        treatment=treatment,
        outcome=outcome,
        contrast=contrast_values,
        outcome_model=outcome_model,
    )
    if not np.isfinite(estimate):
        raise ValueError("估计结果不是有限值")

    standard_error: float | None = None
    ci95: tuple[float, float] | None = None
    if bootstrap >= 2:
        rng = np.random.default_rng(0)
        effects = [
            _effect(
                frame.iloc[rng.integers(0, len(frame), len(frame))].reset_index(drop=True),
                order=relevant_order,
                parents=parents,
                treatment=treatment,
                outcome=outcome,
                contrast=contrast_values,
                outcome_model=outcome_model,
            )
            for _ in range(bootstrap)
        ]
        standard_error = float(np.std(effects, ddof=1))
        low, high = np.percentile(effects, [2.5, 97.5])
        ci95 = (float(low), float(high))

    return {
        "estimand": "ATE",
        "treatment": treatment,
        "outcome": outcome,
        "estimate": estimate,
        "standard_error": standard_error,
        "ci95": ci95,
        "estimator": "g_formula",
        "n_rows": int(len(frame)),
        "contrast": contrast_values,
        "outcome_model": outcome_model,
        "bootstrap_n": int(bootstrap),
    }
