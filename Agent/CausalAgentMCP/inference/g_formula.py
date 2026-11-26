"""G-formula 的确定性内核：调整后直接标准化、结局模型与 bootstrap 区间。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

from Agent.deep_agent_tools.ate_applicability import (
    ATE_REASON_SUMMARIES,
    check_ate_graph,
    check_ate_profile,
)
from Agent.deep_agent_tools.models import StandardizedGraph
from Agent.deep_agent_tools.preprocessing import build_data_profile
from .interventions import prepare_intervention, sample_identity


class GFormulaNotApplicable(ValueError):
    """当前图或数据无法支撑 g-formula；消息面向模型，不含堆栈。"""


def _estimator(model: str) -> Any:
    if model == "hgb":
        return HistGradientBoostingRegressor(
            max_depth=3,
            learning_rate=0.05,
            random_state=0,
        )
    return LinearRegression()


def _design(frame: pd.DataFrame, adjustment: list[str]) -> np.ndarray:
    """处理变量在前、调整变量在后；对照取值直接替换第一列。"""

    return frame.to_numpy(dtype=float)


def _effect(
    frame: pd.DataFrame,
    *,
    treatment: str,
    outcome: str,
    adjustment: list[str],
    baseline: np.ndarray,
    changed: np.ndarray,
    outcome_model: str,
) -> float:
    """按调整后直接标准化估计条件均值差 E[m(x1,Z)] - E[m(x0,Z)]。"""

    design = _design(frame.loc[:, [treatment, *adjustment]], adjustment)
    target = frame[outcome].to_numpy(dtype=float)
    model = _estimator(outcome_model)
    model.fit(design, target)
    effects: list[float] = []
    for value in (baseline, changed):
        counterfactual = design.copy()
        counterfactual[:, 0] = value
        effects.append(float(model.predict(counterfactual).mean()))
    return effects[1] - effects[0]


def _linear_effect(
    frame: pd.DataFrame,
    *,
    treatment: str,
    outcome: str,
    adjustment: list[str],
    magnitude: float,
) -> float:
    """与 backdoor_ols 同式同列，按对比区间缩放的线性效应。"""

    columns = [treatment, *adjustment]
    values = frame[columns].to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(values)), values])
    if np.linalg.matrix_rank(design) < design.shape[1]:
        raise GFormulaNotApplicable("设计矩阵奇异，不能计算成对线性比较。")
    coefficients, *_ = np.linalg.lstsq(
        design, frame[outcome].to_numpy(dtype=float), rcond=None
    )
    return float(coefficients[1]) * magnitude


def estimate_g_formula(
    *,
    graph: StandardizedGraph,
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    effect_request: dict[str, Any] | None = None,
    execution_context: Mapping[str, Any] | None = None,
    semantic_kinds: Mapping[str, str] | None = None,
    outcome_model: str = "linear",
    bootstrap: int = 200,
) -> dict[str, Any]:
    """按调整后直接标准化估计 contrast 的 ATE；不可识别时抛 GFormulaNotApplicable。"""

    if outcome_model not in ("linear", "hgb"):
        raise GFormulaNotApplicable("不支持的结果方程模型")
    if type(bootstrap) is not int or not 0 <= bootstrap <= 2000 or bootstrap == 1:
        raise GFormulaNotApplicable("bootstrap 次数须为 0 或 2 至 2000 的整数")

    # 调整集沿用后门 OLS 的处理变量父节点规则，适用性检查与内核共用同一份规则。
    check = check_ate_graph(
        graph=graph,
        graph_result_ref="g-formula-kernel:unbound",
        treatment=treatment,
        outcome=outcome,
    )
    if check.applicability == "not_applicable":
        raise GFormulaNotApplicable(
            ATE_REASON_SUMMARIES.get(
                check.reason_code, "当前图不满足效应估计条件。"
            )
        )
    adjustment = list(check.adjustment_set or ())

    columns = [treatment, outcome, *adjustment]
    if not data.columns.is_unique:
        raise GFormulaNotApplicable("估计数据列名称重复")
    missing = sorted(set(columns) - set(data.columns))
    if missing:
        raise GFormulaNotApplicable("数据缺少估计所需列：" + "、".join(missing))
    if any(
        not pd.api.types.is_numeric_dtype(data[column].dtype)
        or pd.api.types.is_complex_dtype(data[column].dtype) for column in columns
    ):
        raise GFormulaNotApplicable("估计所用列必须为实数数值存储")
    check = check_ate_profile(
        check, profile=build_data_profile(data.loc[:, columns]),
        semantic_kinds=semantic_kinds,
    )
    if check.applicability == "not_applicable":
        raise GFormulaNotApplicable(ATE_REASON_SUMMARIES[check.reason_code])
    frame = data.loc[:, columns].astype(float)
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise GFormulaNotApplicable("估计所用列包含缺失值、非数值或非有限值")
    request, baseline, changed, support = prepare_intervention(
        treatment_values=frame[treatment].to_numpy(dtype=float),
        treatment=treatment, outcome=outcome,
        effect_request=effect_request, execution_context=execution_context,
    )
    magnitude = float(changed[0] - baseline[0])

    estimate = _effect(
        frame,
        treatment=treatment,
        outcome=outcome,
        adjustment=adjustment,
        baseline=baseline, changed=changed,
        outcome_model=outcome_model,
    )
    if not np.isfinite(estimate):
        raise ValueError("估计结果不是有限值")

    linear_estimate = _linear_effect(
        frame,
        treatment=treatment,
        outcome=outcome,
        adjustment=adjustment,
        magnitude=magnitude,
    )
    diagnostics_metrics: dict[str, float] = {
        "linear_estimate": linear_estimate,
        "effect_difference": linear_estimate - estimate,
    }

    standard_error: float | None = None
    ci95: tuple[float, float] | None = None
    difference_se: float | None = None
    if bootstrap >= 2:
        rng = np.random.default_rng(0)
        effects: list[float] = []
        differences: list[float] = []
        for _ in range(bootstrap):
            indices = rng.integers(0, len(frame), len(frame))
            sample = frame.iloc[indices].reset_index(drop=True)
            flexible = _effect(
                sample,
                treatment=treatment,
                outcome=outcome,
                adjustment=adjustment,
                baseline=baseline[indices], changed=changed[indices],
                outcome_model=outcome_model,
            )
            effects.append(flexible)
            differences.append(
                _linear_effect(
                    sample,
                    treatment=treatment,
                    outcome=outcome,
                    adjustment=adjustment,
                    magnitude=magnitude,
                ) - flexible
            )
        if not np.isfinite(effects).all() or not np.isfinite(differences).all():
            raise ValueError("bootstrap 结果包含非有限值，未删除重采样记录")
        standard_error = float(np.std(effects, ddof=1))
        low, high = np.percentile(effects, [2.5, 97.5])
        ci95 = (float(low), float(high))
        difference_se = float(np.std(differences, ddof=1))
        diff_low, diff_high = np.percentile(differences, [2.5, 97.5])
        diagnostics_metrics["difference_ci95_low"] = float(diff_low)
        diagnostics_metrics["difference_ci95_high"] = float(diff_high)
        diagnostics_metrics["paired_bootstrap_n"] = bootstrap
        diagnostics_metrics["bootstrap_seed"] = 0
    if difference_se is not None:
        diagnostics_metrics["difference_se"] = difference_se
        diagnostics_metrics["difference_direction"] = "linear_minus_g_formula"

    return {
        "estimand": "ATE",
        "treatment": treatment,
        "outcome": outcome,
        "adjustment_set": tuple(adjustment),
        "estimate": estimate,
        "standard_error": standard_error,
        "ci95": ci95,
        "estimator": "g_formula",
        "n_rows": int(len(frame)),
        "contrast": (
            (request["intervention"]["from"], request["intervention"]["to"])
            if request["intervention"]["type"] == "fixed_contrast"
            and request["intervention"]["from"] != request["intervention"]["to"] else None
        ),
        "effect_request": request,
        "sample_identity": sample_identity(frame),
        "adjustment_rule": "parents_of_treatment",
        "support": support,
        "outcome_model": outcome_model,
        "bootstrap_n": int(bootstrap),
        "diagnostics_metrics": diagnostics_metrics,
    }
