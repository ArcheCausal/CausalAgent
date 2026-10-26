"""后门调整 ATE 的确定性内核：门禁、调整集、OLS 与模型内区间。"""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from Agent.deep_agent_tools.models import StandardizedGraph


class AteNotApplicable(ValueError):
    """当前图或数据无法支撑后门调整估计；消息面向模型，不含堆栈。"""


def _children(graph: StandardizedGraph) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {node: [] for node in graph.nodes}
    for edge in graph.edges:
        children[edge.source].append(edge.target)
    return children


def _topological_order(graph: StandardizedGraph) -> list[str]:
    children = _children(graph)
    incoming = dict.fromkeys(graph.nodes, 0)
    for targets in children.values():
        for target in targets:
            incoming[target] += 1
    ready = deque(node for node, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for child in children[node]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if len(order) != len(graph.nodes):
        raise AteNotApplicable("因果图包含有向环，无法按拓扑序估计效应")
    return order


def _is_ancestor(graph: StandardizedGraph, ancestor: str, target: str) -> bool:
    children = _children(graph)
    seen: set[str] = set()
    stack = [ancestor]
    while stack:
        node = stack.pop()
        for child in children[node]:
            if child == target:
                return True
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return False


def estimate_ate(
    *,
    graph: StandardizedGraph,
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
) -> dict[str, Any]:
    """估计 treatment 对 outcome 的线性总效应；不可识别时抛 AteNotApplicable。"""

    if any(edge.edge_type != "directed" for edge in graph.edges):
        raise AteNotApplicable("图中存在未定向或双箭头边，无法确定后门调整集")
    _topological_order(graph)
    if treatment not in graph.nodes or outcome not in graph.nodes:
        raise AteNotApplicable("图中缺少处理变量或结果变量")
    if treatment == outcome:
        raise AteNotApplicable("处理变量与结果变量不能相同")
    if not _is_ancestor(graph, treatment, outcome):
        raise AteNotApplicable("处理变量不是结果变量的祖先，效应不可识别")

    adjustment = sorted({edge.source for edge in graph.edges if edge.target == treatment})
    columns = [treatment, outcome, *adjustment]
    missing = [column for column in columns if column not in data.columns]
    if missing:
        raise AteNotApplicable("数据缺少估计所需列：" + "、".join(sorted(missing)))

    values = data[columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise AteNotApplicable("估计所用列包含缺失值、非数值或非有限值")

    treatment_values = values[:, 0]
    outcome_values = values[:, 1]
    design = np.column_stack(
        [np.ones(len(values)), treatment_values, values[:, 2:]]
    )
    if np.linalg.matrix_rank(design) < design.shape[1]:
        raise AteNotApplicable("设计矩阵奇异：处理变量或调整变量存在共线/常数列")

    coefficients, *_ = np.linalg.lstsq(design, outcome_values, rcond=None)
    residuals = outcome_values - design @ coefficients
    n_rows, n_params = design.shape
    dof = n_rows - n_params
    if dof <= 0:
        raise AteNotApplicable("样本量不足以估计标准误")

    residual_variance = float(residuals @ residuals) / dof
    covariance = residual_variance * np.linalg.inv(design.T @ design)
    estimate = float(coefficients[1])
    standard_error = float(np.sqrt(max(covariance[1, 1], 0.0)))
    if not np.isfinite(estimate) or not np.isfinite(standard_error):
        # 数值溢出属于执行失败，不是「这张图算不了」，因此不用 AteNotApplicable。
        raise ValueError("估计结果不是有限值")

    critical = float(stats.t.ppf(0.975, dof))
    return {
        "estimand": "ATE",
        "treatment": treatment,
        "outcome": outcome,
        "adjustment_set": tuple(adjustment),
        "estimate": estimate,
        "standard_error": standard_error,
        "ci95": (estimate - critical * standard_error, estimate + critical * standard_error),
        "estimator": "backdoor_ols",
        "n_rows": int(n_rows),
    }
