"""后门调整 ATE 的确定性内核：门禁、调整集、OLS 与模型内区间。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from Agent.deep_agent_tools.ate_applicability import (
    ATE_REASON_SUMMARIES,
    check_ate_graph,
    check_ate_profile,
)
from Agent.deep_agent_tools.models import StandardizedGraph
from Agent.deep_agent_tools.preprocessing import build_data_profile


class AteNotApplicable(ValueError):
    """当前图或数据无法支撑后门调整估计；消息面向模型，不含堆栈。"""

    def __init__(self, reason_code: str) -> None:
        if reason_code not in ATE_REASON_SUMMARIES:
            raise ValueError("unknown ATE reason_code")
        super().__init__(ATE_REASON_SUMMARIES[reason_code])
        self.reason_code = reason_code


def estimate_ate(
    *,
    graph: StandardizedGraph,
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    semantic_kinds: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """估计 treatment 对 outcome 的线性总效应；不可识别时抛 AteNotApplicable。"""

    # 内核没有结果引用参数；这里只用内部标签运行共享规则，不发布引用证明。
    check = check_ate_graph(
        graph=graph, graph_result_ref="ate-kernel:unbound",
        treatment=treatment, outcome=outcome,
    )
    if check.applicability == "not_applicable":
        raise AteNotApplicable(check.reason_code)
    if not data.columns.is_unique:
        raise AteNotApplicable("ATE_EXECUTION_COLUMNS_INVALID")
    columns = list(check.execution_columns)
    existing = [column for column in columns if column in data.columns]
    if any(pd.api.types.is_complex_dtype(data[column].dtype) for column in existing):
        raise AteNotApplicable("ATE_EXECUTION_COLUMN_NOT_NUMERIC")
    profile = build_data_profile(data.loc[:, existing])
    check = check_ate_profile(check, profile=profile, semantic_kinds=semantic_kinds)
    if check.applicability == "not_applicable":
        raise AteNotApplicable(check.reason_code)
    adjustment = check.adjustment_set
    values = data.loc[:, columns].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise AteNotApplicable("ATE_EXECUTION_COLUMN_NON_FINITE")

    treatment_values = values[:, 0]
    outcome_values = values[:, 1]
    design = np.column_stack(
        [np.ones(len(values)), treatment_values, values[:, 2:]]
    )
    if np.linalg.matrix_rank(design) < design.shape[1]:
        raise AteNotApplicable("ATE_DESIGN_MATRIX_SINGULAR")

    coefficients, *_ = np.linalg.lstsq(design, outcome_values, rcond=None)
    residuals = outcome_values - design @ coefficients
    n_rows, n_params = design.shape
    dof = n_rows - n_params
    if dof <= 0:
        raise AteNotApplicable("ATE_INSUFFICIENT_DOF")

    residual_variance = float(residuals @ residuals) / dof
    covariance = residual_variance * np.linalg.inv(design.T @ design)
    estimate = float(coefficients[1])
    standard_error = float(np.sqrt(max(covariance[1, 1], 0.0)))
    if (
        not np.isfinite(coefficients).all()
        or not np.isfinite(residual_variance)
        or not np.isfinite(covariance).all()
        or covariance[1, 1] < 0
        or not np.isfinite(estimate)
        or not np.isfinite(standard_error)
    ):
        # 数值溢出属于执行失败，不是「这张图算不了」，因此不用 AteNotApplicable。
        raise ValueError("估计结果不是有限值")

    critical = float(stats.t.ppf(0.975, dof))
    ci95 = (estimate - critical * standard_error, estimate + critical * standard_error)
    if not np.isfinite(ci95).all():
        raise ValueError("估计区间不是有限值")
    return {
        "estimand": "ATE",
        "treatment": treatment,
        "outcome": outcome,
        "adjustment_set": tuple(adjustment),
        "estimate": estimate,
        "standard_error": standard_error,
        "ci95": ci95,
        "estimator": "backdoor_ols",
        "n_rows": int(n_rows),
    }
