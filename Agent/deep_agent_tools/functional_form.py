"""效应执行身份与方法比较：输出诊断事实，不裁定科学结论。"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any
import json

from Agent.deep_agent_tools.models import EffectQuery

SIGNAL_FLOOR = 0.1
RATIO_LINE = 1.0

OLS_CAPABILITY = "causal.backdoor_ols"
FLEX_CAPABILITY = "causal.g_formula"
FLEX_TOOL_NAME = "causal_g_formula"
OLS_TOOL_NAME = "causal_backdoor_ols"
CAPABILITY_TOOL_NAMES = {
    OLS_CAPABILITY: OLS_TOOL_NAME,
    FLEX_CAPABILITY: FLEX_TOOL_NAME,
}
_FLEX_TERMINAL_REASONS = frozenset({"not_applicable", "timed_out", "failed"})


def build_effect_query(
    *, capability_id: str, parameters: Mapping[str, Any] | None,
) -> Any | None:
    """从已校验的工具参数生成效应请求；只有估计与诊断调用带该信息。"""
    if capability_id not in CAPABILITY_TOOL_NAMES or not isinstance(parameters, Mapping):
        return None
    from Agent.deep_agent_tools.algorithm_specs import get_algorithm_spec
    try:
        parameters = get_algorithm_spec(capability_id).model_input_schema.model_validate(
            {key: value for key, value in parameters.items() if key != "public_decision"}
        ).model_dump(mode="json")
    except ValueError:
        return None
    contrast = None
    outcome_model = None
    if capability_id == FLEX_CAPABILITY:
        raw_contrast = parameters.get("contrast")
        if isinstance(raw_contrast, (list, tuple)) and len(raw_contrast) == 2:
            try:
                contrast = (float(raw_contrast[0]), float(raw_contrast[1]))
            except (TypeError, ValueError):
                return None
        model = parameters.get("outcome_model")
        outcome_model = str(model) if isinstance(model, str) and model else "linear"
    return build_effect_request(
        graph_result_ref=parameters.get("graph_result_ref"),
        treatment=parameters.get("treatment"),
        outcome=parameters.get("outcome"),
        contrast=contrast,
        outcome_model=outcome_model,
        effect_request=parameters.get("effect_request"),
        estimator_config={key: value for key, value in parameters.items() if key not in {"graph_result_ref", "treatment", "outcome", "effect_request", "public_decision"}},
    )


def build_effect_request(
    *,
    graph_result_ref: Any,
    treatment: Any,
    outcome: Any,
    contrast: tuple[float, float] | None = None,
    outcome_model: str | None = None,
    effect_request: Mapping[str, Any] | None = None,
    estimator_config: Mapping[str, Any] | None = None,
) -> Any | None:
    """校验字符串身份后构造效应请求；调用方只传入程序解析出的值。"""

    if not all(
        isinstance(value, str) and value.strip() and value == value.strip()
        for value in (graph_result_ref, treatment, outcome)
    ):
        return None
    try:
        return EffectQuery(
            graph_result_ref=graph_result_ref,
            treatment=treatment,
            outcome=outcome,
            contrast=contrast,
            outcome_model=outcome_model,
            effect_request=dict(effect_request) if isinstance(effect_request, Mapping) else None,
            estimator_config=dict(estimator_config or {}),
        )
    except ValueError:
        return None


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
    if not isinstance(parameters.get("effect_request"), Mapping):
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
    # 多个执行可能来自不同图或不同样本；没有明确身份时不能替 Agent 选一个。
    return candidates[0] if len(candidates) == 1 else None


def comparison_identity(result: Any) -> str | None:
    """比较身份不含估计器配置，但包含实际样本、源图、定义与调整规则。"""
    effect = getattr(result, "causal_effect", None)
    provenance = getattr(result, "provenance", None)
    if effect is None or provenance is None or not effect.effect_request or not effect.sample_identity or not effect.adjustment_rule:
        return None
    return json.dumps({
        "input_identity": provenance.input_identity, "dataset_id": provenance.analysis_dataset_id,
        "research_identity": provenance.research_identity,
        "sample_identity": effect.sample_identity, "n_rows": effect.n_rows,
        "graph_result_ref": effect.graph_result_ref, "treatment": effect.treatment,
        "outcome": effect.outcome, "effect_request": effect.effect_request,
        "adjustment_set": sorted(effect.adjustment_set), "adjustment_rule": effect.adjustment_rule,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compare_effect_results(results: Iterable[Any]) -> list[dict[str, Any]]:
    """逐组提供方法比较事实；不同源图只作为结构敏感性结果。"""
    from itertools import combinations
    valid = [item for item in results if getattr(item, "status", None) == "valid" and getattr(item, "causal_effect", None) is not None]
    comparisons = []
    for left, right in combinations(valid, 2):
        left_id, right_id = comparison_identity(left), comparison_identity(right)
        comparable = left_id is not None and left_id == right_id
        metrics = right.diagnostics.metrics if right.capability_id == FLEX_CAPABILITY else left.diagnostics.metrics
        linear = left if left.capability_id == OLS_CAPABILITY else right
        paired = (comparable and {left.capability_id, right.capability_id} == {OLS_CAPABILITY, FLEX_CAPABILITY}
                  and _metric(metrics, "linear_estimate") is not None
                  and math.isclose(float(metrics["linear_estimate"]), linear.causal_effect.estimate, rel_tol=1e-7, abs_tol=1e-10))
        comparisons.append({
            "left_result_ref": left.result_ref, "right_result_ref": right.result_ref,
            "left_algorithm": left.capability_id, "right_algorithm": right.capability_id,
            "comparable": comparable, "reason": None if comparable else "source_sample_definition_or_adjustment_differs",
            "effect_difference": right.causal_effect.estimate - left.causal_effect.estimate if comparable else None,
            "difference_direction": "right_minus_left",
            "paired_difference_direction": "linear_minus_flexible",
            "paired_effect_difference": _metric(metrics, "effect_difference") if paired else None,
            "difference_se": _metric(metrics, "difference_se") if paired else None,
            "difference_ci95": [_metric(metrics, "difference_ci95_low"), _metric(metrics, "difference_ci95_high")] if paired else None,
            "left_support": left.causal_effect.support, "right_support": right.causal_effect.support,
            "scientific_choice": "agent",
        })
    return comparisons


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
            comparison["difference_ci95"] = [
                _metric(flex_metrics, "difference_ci95_low"),
                _metric(flex_metrics, "difference_ci95_high"),
            ]
            verdict = "compared"
    elif flex_status == "valid":
        verdict = "available_without_comparison"
    elif flex_status in _FLEX_TERMINAL_REASONS:
        verdict, reason = "undetermined", f"flex_{flex_status}"
    elif signal_triggered(signals):
        verdict, reason = "undetermined", "flex_not_attempted"
    else:
        verdict = "not_detected"
    return {"verdict": verdict, "signals": signals, "comparison": comparison, "reason": reason}
