"""方法比较纯契约：诊断事实、完整身份和无自动估计器选择。"""

from __future__ import annotations

from hashlib import sha256

import pytest

from Agent.deep_agent_tools.functional_form import (
    FLEX_CAPABILITY,
    OLS_CAPABILITY,
    RATIO_LINE,
    SIGNAL_FLOOR,
    assess_functional_form,
    build_effect_query,
    compare_effect_results,
    comparison_identity,
    estimation_pair,
    flex_status_from_results,
    select_result,
    signal_triggered,
)
from Agent.data_processing.parameters import parameter_identity
from Agent.deep_agent_tools.algorithm_specs import get_algorithm_spec
from Agent.deep_agent_tools.models import (
    AlgorithmResult, AlgorithmResultProvenance, CausalEffect, Diagnostics,
    EffectQuery, StandardizedGraph,
)
from Agent.deep_agent_tools.error_codes import SafeErrorCode


REQUEST = {"effect": "average_total_effect", "intervention": {
    "type": "fixed_contrast", "from": 6.0, "to": 8.0, "unit": "L/min",
}}


def test_constants_match_the_design() -> None:
    assert (SIGNAL_FLOOR, RATIO_LINE) == (0.1, 1.0)


def test_estimation_pair_requires_distinct_target_and_treatment() -> None:
    assert estimation_pair({"target": "y", "treatment": "x", "effect_request": REQUEST}) == ("x", "y")
    assert estimation_pair({"target": "y", "treatment": "x"}) is None
    assert estimation_pair({"target": "y", "treatment": "x", "effect_request": None}) is None
    assert estimation_pair({"target": "y"}) is None
    assert estimation_pair({"target": "x", "treatment": "x", "effect_request": REQUEST}) is None
    assert estimation_pair(None) is None


def test_dataset_ratio_triggers_without_ols_metrics() -> None:
    result = assess_functional_form(
        nonlinearity={"ratio": 1.2, "verdict": "nonlinear"},
    )
    assert result["verdict"] == "undetermined"
    assert result["reason"] == "flex_not_attempted"
    assert result["signals"] == {
        "dataset_ratio": 1.2,
        "residual_spearman": None,
        "residual_abs_spearman": None,
    }


def test_ratio_below_line_and_small_residuals_is_not_detected() -> None:
    result = assess_functional_form(
        nonlinearity={"ratio": 0.5, "verdict": "linear"},
        ols_metrics={"residual_spearman_max": 0.05, "residual_abs_spearman_max": -0.04},
    )
    assert result["verdict"] == "not_detected"
    assert result["reason"] is None
    assert result["comparison"] is None


def test_insufficient_ratio_is_not_a_dataset_signal() -> None:
    result = assess_functional_form(
        nonlinearity={"ratio": 9.0, "verdict": "insufficient"},
    )
    assert result["signals"]["dataset_ratio"] is None
    assert result["verdict"] == "not_detected"


@pytest.mark.parametrize("value", [0.1, -0.25])
def test_residual_magnitude_at_floor_triggers(value) -> None:
    result = assess_functional_form(
        ols_metrics={"residual_spearman_max": value},
    )
    assert result["signals"]["residual_spearman"] == value
    assert result["verdict"] == "undetermined"
    assert result["reason"] == "flex_not_attempted"


def test_small_difference_is_comparison_without_linearity_verdict() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 1.0, "difference_se": 3.0},
        flex_status="valid",
    )
    assert result["verdict"] == "compared"
    assert result["comparison"] == {
        "linear_estimate": 2.0,
        "effect_difference": 1.0,
        "difference_se": 3.0,
        "difference_ci95": [None, None],
    }


def test_large_difference_does_not_establish_nonlinearity() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 0.1, "effect_difference": -2.9, "difference_se": 0.2},
        flex_status="valid",
    )
    assert result["verdict"] == "compared"
    assert result["comparison"]["effect_difference"] == -2.9


def test_zero_se_and_difference_do_not_establish_linearity() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 0.0, "difference_se": 0.0},
        flex_status="valid",
    )
    assert result["verdict"] == "compared"


def test_missing_difference_se_is_undetermined() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 0.1},
        flex_status="valid",
    )
    assert result["verdict"] == "undetermined"
    assert result["reason"] == "difference_se_unavailable"
    assert result["comparison"] is None


def test_flexible_only_is_available_without_automatic_adoption() -> None:
    result = assess_functional_form(
        ols_metrics=None,
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 0.1},
        flex_status="valid",
    )
    assert result["verdict"] == "available_without_comparison"
    assert result["comparison"] is None


@pytest.mark.parametrize(
    "status, reason",
    [
        ("not_applicable", "flex_not_applicable"),
        ("timed_out", "flex_timed_out"),
        ("failed", "flex_failed"),
    ],
)
def test_failed_flex_attempt_is_undetermined_with_real_reason(status, reason) -> None:
    result = assess_functional_form(
        ols_metrics={}, flex_status=status,
    )
    assert result["verdict"] == "undetermined"
    assert result["reason"] == reason


def test_illegal_values_do_not_raise() -> None:
    result = assess_functional_form(
        nonlinearity={"ratio": "not-a-number", "verdict": "nonlinear"},
        ols_metrics={"residual_spearman_max": float("nan"), "residual_abs_spearman_max": True},
        flex_metrics={"linear_estimate": "x", "effect_difference": None, "difference_se": -1.0},
        flex_status="valid",
    )
    assert result["verdict"] == "undetermined"
    assert result["reason"] == "difference_se_unavailable"
    assert result["signals"]["dataset_ratio"] is None


def test_skipped_flex_is_not_an_attempt() -> None:
    result = assess_functional_form(
        nonlinearity={"ratio": 1.5, "verdict": "nonlinear"},
        ols_metrics={},
        flex_status="skipped",
    )
    assert result["verdict"] == "undetermined"
    assert result["reason"] == "flex_not_attempted"


def _result(capability, invocation, status, *, treatment="x", outcome="y",
            estimate=4.0, metrics=None, effect_changes=None, provenance_changes=None):
    """直接构造被测纯函数的领域输入，不替换算法或外部服务。"""
    spec = get_algorithm_spec(capability)
    effect_data = dict(
        estimand="ATE", graph_result_ref="graph:0", treatment=treatment, outcome=outcome,
        adjustment_set=("z",), estimate=estimate, estimator=spec.tool_name, n_rows=3,
        effect_request=REQUEST, sample_identity=sha256(b"x,y,z\n6,12,0\n7,14,1\n8,16,2\n").hexdigest(),
        adjustment_rule="parents_of_treatment",
        support={"status": "supported", "conditional_overlap": "not_assessed"},
    )
    provenance = dict(job_id="comparison-contract", attempt_count=0, lease_epoch=1,
        input_identity="dataset-content", analysis_dataset_id=1, spec_digest=spec.spec_digest,
        research_identity=parameter_identity({"target": outcome, "treatment": treatment, "effect_request": REQUEST}))
    effect_data.update(effect_changes or {})
    provenance.update(provenance_changes or {})
    return AlgorithmResult(
        result_ref=f"{invocation}:0", invocation_id=invocation, provider_call_id=f"call-{invocation}",
        capability_id=capability,
        capability_version=spec.version,
        status=status,
        standardized_graph=StandardizedGraph(graph_semantics="pdag", nodes=["x", "y", "z"]) if status == "valid" else None,
        causal_effect=CausalEffect(**effect_data) if status == "valid" else None,
        diagnostics=Diagnostics(metrics=metrics or {}, safe_error_code=None if status == "valid" else {
            "not_applicable": SafeErrorCode.ALGORITHM_NOT_APPLICABLE,
            "timed_out": SafeErrorCode.ALGORITHM_TIMED_OUT,
            "execution_failed": SafeErrorCode.ALGORITHM_EXECUTION_FAILED,
            "skipped": SafeErrorCode.ALGORITHM_BUDGET_EXHAUSTED,
        }[status]),
        provenance=AlgorithmResultProvenance(**provenance),
    )


def test_select_result_requires_unique_pair_scoped_result() -> None:
    results = [
        _result(OLS_CAPABILITY, "ols-2", "valid"),
        _result(OLS_CAPABILITY, "ols-1", "valid"),
        _result(OLS_CAPABILITY, "ols-0", "valid", treatment="z"),
    ]
    assert select_result(results, capability=OLS_CAPABILITY, treatment="x", outcome="y") is None
    assert select_result(results[1:], capability=OLS_CAPABILITY, treatment="x", outcome="y") is results[1]
    assert select_result(results, capability=FLEX_CAPABILITY, treatment="x", outcome="y") is None


def test_flex_status_prefers_valid_then_first_terminal() -> None:
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex", "valid")], treatment="x", outcome="y",
    ) == "valid"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex", "not_applicable")], treatment="x", outcome="y",
    ) == "not_applicable"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex", "timed_out")], treatment="x", outcome="y",
    ) == "timed_out"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex", "execution_failed")], treatment="x", outcome="y",
    ) == "failed"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex", "skipped")], treatment="x", outcome="y",
    ) is None


@pytest.mark.parametrize("reverse_order", [False, True])
def test_paired_difference_retains_its_own_direction(reverse_order) -> None:
    ols = _result(OLS_CAPABILITY, "ols", "valid", estimate=4.0)
    flex = _result(FLEX_CAPABILITY, "flex", "valid", estimate=7.0, metrics={
        "linear_estimate": 4.0, "effect_difference": -3.0, "difference_se": 0.2,
        "difference_ci95_low": -3.4, "difference_ci95_high": -2.6,
    })
    ordered = [flex, ols] if reverse_order else [ols, flex]
    comparison, = compare_effect_results(ordered)
    assert comparison["comparable"]
    assert comparison["effect_difference"] == (-3.0 if reverse_order else 3.0)
    assert comparison["difference_direction"] == "right_minus_left"
    assert comparison["paired_difference_direction"] == "linear_minus_flexible"
    assert comparison["paired_effect_difference"] == -3.0
    assert comparison["difference_ci95"] == [-3.4, -2.6]
    assert comparison["scientific_choice"] == "agent"


@pytest.mark.parametrize("effect_changes,provenance_changes", [
    ({"graph_result_ref": "other-graph:0"}, {}),
    ({"sample_identity": "different-sample"}, {}),
    ({"n_rows": 4}, {}),
    ({"adjustment_set": ()}, {}),
    ({"adjustment_rule": "another_rule"}, {}),
    ({"effect_request": {**REQUEST, "intervention": {**REQUEST["intervention"], "unit": "mL/min"}}}, {}),
    ({"effect_request": {**REQUEST, "target_population": "age >= 18"}}, {}),
    ({}, {"research_identity": "another-research"}),
    ({}, {"input_identity": "different-content"}),
    ({}, {"analysis_dataset_id": 2}),
])
def test_different_comparison_identity_has_no_difference_interval(effect_changes, provenance_changes) -> None:
    ols = _result(OLS_CAPABILITY, "ols", "valid")
    flex = _result(FLEX_CAPABILITY, "flex", "valid", effect_changes=effect_changes, provenance_changes=provenance_changes)
    assert comparison_identity(ols) != comparison_identity(flex)
    comparison, = compare_effect_results([ols, flex])
    assert comparison["comparable"] is False
    assert comparison["effect_difference"] is None
    assert comparison["difference_ci95"] is None


def test_query_records_effective_defaults_and_rejects_legacy_contrast() -> None:
    parameters = {"graph_result_ref": "graph:0", "treatment": "x", "outcome": "y", "effect_request": REQUEST}
    query = build_effect_query(capability_id=FLEX_CAPABILITY, parameters=parameters)
    assert isinstance(query, EffectQuery)
    assert query.effect_request == REQUEST
    assert query.estimator_config == {"outcome_model": "linear", "bootstrap": 200}
    assert query.contrast is None
    assert build_effect_query(capability_id=FLEX_CAPABILITY, parameters={**parameters, "contrast": [0, 1]}) is None
    assert build_effect_query(capability_id=FLEX_CAPABILITY, parameters={k: v for k, v in parameters.items() if k != "effect_request"}) is None
    changed = build_effect_query(capability_id=FLEX_CAPABILITY, parameters={**parameters, "bootstrap": 12, "outcome_model": "hgb"})
    assert not query.matches_estimation(changed)
