"""函数形式判定的纯逻辑：只消费已测得的指标，不做 IO，不抛异常。"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

SIGNAL_FLOOR = 0.1
RATIO_LINE = 1.0
DIFF_RATIO = 0.5

OLS_CAPABILITY = "causal.backdoor_ols"
FLEX_CAPABILITY = "causal.g_formula"
FLEX_TOOL_NAME = "causal_g_formula"
_FLEX_TERMINAL_REASONS = frozenset({"not_applicable", "timed_out", "failed"})


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _metric(metrics: Mapping[str, Any] | None, key: str) -> float | None:
    if not isinstance(metrics, Mapping):
        return None
    value = metrics.get(key)
    return float(value) if _finite(value) else None


def estimation_pair(parameters: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """返回 (treatment, outcome)；目标或处理变量缺失/相同则 None。"""

    if not isinstance(parameters, Mapping):
        return None
    target = parameters.get("target")
    treatment = parameters.get("treatment")
    if not (
        isinstance(target, str) and target.strip()
        and isinstance(treatment, str) and treatment.strip()
    ):
        return None
    return None if target == treatment else (treatment, target)


def signal_triggered(signals: Mapping[str, Any]) -> bool:
    ratio = signals.get("dataset_ratio")
    magnitudes = [
        abs(value)
        for value in (signals.get("residual_spearman"), signals.get("residual_abs_spearman"))
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return (
        (isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and ratio >= RATIO_LINE)
        or (bool(magnitudes) and max(magnitudes) >= SIGNAL_FLOOR)
    )


def select_result(
    results: Iterable[Any], *, capability: str, treatment: str, outcome: str,
) -> Any | None:
    candidates = [
        result for result in results
        if getattr(result, "status", None) == "valid"
        and getattr(result, "capability_id", None) == capability
        and getattr(result, "causal_effect", None) is not None
        and (
            result.causal_effect.treatment,
            result.causal_effect.outcome,
        ) == (treatment, outcome)
    ]
    return min(candidates, key=lambda item: (item.capability_id, item.result_ref), default=None)


def flex_status_from_results(
    results: Iterable[Any], *, treatment: str, outcome: str,
) -> str | None:
    """按结果终态推断灵活路状态；失败结果不携带变量对，按整批判定。"""

    all_results = list(results)
    if select_result(
        all_results, capability=FLEX_CAPABILITY, treatment=treatment, outcome=outcome,
    ) is not None:
        return "valid"
    for status in sorted(
        str(getattr(result, "status", ""))
        for result in all_results
        if getattr(result, "capability_id", None) == FLEX_CAPABILITY
    ):
        if status == "skipped":
            continue
        return status if status in {"not_applicable", "timed_out"} else "failed"
    return None


def assess_functional_form(
    *,
    nonlinearity: Mapping[str, Any] | None = None,
    ols_metrics: Mapping[str, Any] | None = None,
    flex_metrics: Mapping[str, Any] | None = None,
    flex_status: str | None = None,
) -> dict[str, Any]:
    """按设计矩阵返回 verdict/signals/comparison/reason；任何输入都不抛异常。"""

    ratio = None
    if isinstance(nonlinearity, Mapping) and nonlinearity.get("verdict") != "insufficient":
        if _finite(nonlinearity.get("ratio")):
            ratio = float(nonlinearity["ratio"])
    residual = _metric(ols_metrics, "residual_spearman_max")
    residual_abs = _metric(ols_metrics, "residual_abs_spearman_max")
    signals = {
        "dataset_ratio": ratio,
        "residual_spearman": residual,
        "residual_abs_spearman": residual_abs,
    }
    if flex_status not in {None, "valid", "skipped"} and flex_status not in _FLEX_TERMINAL_REASONS:
        flex_status = "failed"

    comparison = None
    reason = None
    if flex_status == "valid" and ols_metrics is not None:
        linear = _metric(flex_metrics, "linear_estimate")
        difference = _metric(flex_metrics, "effect_difference")
        difference_se = _metric(flex_metrics, "difference_se")
        if linear is None or difference is None or difference_se is None or difference_se < 0:
            verdict, reason = "undetermined", "difference_se_unavailable"
        else:
            comparison = {
                "linear_estimate": linear,
                "effect_difference": difference,
                "difference_se": difference_se,
            }
            consistent = (
                abs(difference) < DIFF_RATIO * difference_se
                if difference_se > 0 else difference == 0.0
            )
            verdict = "linear" if consistent else "nonlinear"
    elif flex_status == "valid":
        verdict = "flexible_primary"
    elif flex_status in _FLEX_TERMINAL_REASONS:
        verdict, reason = "undetermined", f"flex_{flex_status}"
    elif signal_triggered(signals):
        verdict, reason = "undetermined", "flex_not_attempted"
    else:
        verdict = "not_detected"
    return {"verdict": verdict, "signals": signals, "comparison": comparison, "reason": reason}
