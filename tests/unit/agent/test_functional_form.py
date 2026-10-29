"""函数形式判定纯函数：六种判定、信号与缺失输入矩阵。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from Agent.deep_agent_tools.functional_form import (
    DIFF_RATIO,
    FLEX_CAPABILITY,
    OLS_CAPABILITY,
    RATIO_LINE,
    SIGNAL_FLOOR,
    assess_functional_form,
    estimation_pair,
    flex_status_from_results,
    select_result,
    signal_triggered,
)


def test_constants_match_the_design() -> None:
    assert (SIGNAL_FLOOR, RATIO_LINE, DIFF_RATIO) == (0.1, 1.0, 0.5)


def test_estimation_pair_requires_distinct_target_and_treatment() -> None:
    assert estimation_pair({"target": "y", "treatment": "x"}) == ("x", "y")
    assert estimation_pair({"target": "y"}) is None
    assert estimation_pair({"target": "x", "treatment": "x"}) is None
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


def test_both_routes_within_half_se_is_linear() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 1.0, "difference_se": 3.0},
        flex_status="valid",
    )
    assert result["verdict"] == "linear"
    assert result["comparison"] == {
        "linear_estimate": 2.0,
        "effect_difference": 1.0,
        "difference_se": 3.0,
    }


def test_both_routes_beyond_half_se_is_nonlinear() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 0.1, "effect_difference": -2.9, "difference_se": 0.2},
        flex_status="valid",
    )
    assert result["verdict"] == "nonlinear"


def test_zero_se_with_zero_difference_is_linear() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 0.0, "difference_se": 0.0},
        flex_status="valid",
    )
    assert result["verdict"] == "linear"


def test_missing_difference_se_is_undetermined() -> None:
    result = assess_functional_form(
        ols_metrics={},
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 0.1},
        flex_status="valid",
    )
    assert result["verdict"] == "undetermined"
    assert result["reason"] == "difference_se_unavailable"
    assert result["comparison"] is None


def test_flexible_only_is_flexible_primary() -> None:
    result = assess_functional_form(
        ols_metrics=None,
        flex_metrics={"linear_estimate": 2.0, "effect_difference": 0.1},
        flex_status="valid",
    )
    assert result["verdict"] == "flexible_primary"
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


def _result(capability, ref, status, *, treatment="x", outcome="y"):
    return SimpleNamespace(
        result_ref=ref,
        capability_id=capability,
        status=status,
        causal_effect=(
            SimpleNamespace(treatment=treatment, outcome=outcome) if status == "valid" else None
        ),
    )


def test_select_result_is_deterministic_and_pair_scoped() -> None:
    results = [
        _result(OLS_CAPABILITY, "ols:2", "valid"),
        _result(OLS_CAPABILITY, "ols:1", "valid"),
        _result(OLS_CAPABILITY, "ols:0", "valid", treatment="z"),
    ]
    assert select_result(results, capability=OLS_CAPABILITY, treatment="x", outcome="y").result_ref == "ols:1"
    assert select_result(results, capability=FLEX_CAPABILITY, treatment="x", outcome="y") is None


def test_flex_status_prefers_valid_then_first_terminal() -> None:
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex:0", "valid")], treatment="x", outcome="y",
    ) == "valid"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex:0", "not_applicable")], treatment="x", outcome="y",
    ) == "not_applicable"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex:0", "timed_out")], treatment="x", outcome="y",
    ) == "timed_out"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex:0", "execution_failed")], treatment="x", outcome="y",
    ) == "failed"
    assert flex_status_from_results(
        [_result(FLEX_CAPABILITY, "flex:0", "skipped")], treatment="x", outcome="y",
    ) is None
