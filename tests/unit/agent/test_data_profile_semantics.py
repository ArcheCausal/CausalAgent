"""存储、统计特征与测量语义的确定性边界。"""

import numpy as np
import pandas as pd

from Agent.Processing.fold_processing import apply_measurement_semantics, get_data_summary
from Agent.Processing.nonlinearity import measure_nonlinearity
from Agent.deep_agent_tools.preprocessing import build_data_profile, profile_from_analysis_parameters


def test_integer_low_cardinality_and_low_frequency_do_not_determine_meaning():
    frame = pd.DataFrame({"X": [0] * 99 + [2], "Y": np.arange(100, dtype=float)})
    original = frame.copy(deep=True)
    summary = get_data_summary(frame)
    profile = summary["column_profiles"]["X"]
    assert profile["storage_type"] == "int64"
    features = profile["statistical_features"]
    assert features["integer_valued"] and features["nonnegative"]
    assert features["low_cardinality"] and features["low_frequency"]
    assert profile["measurement_semantics"]["kind"] == "unknown"
    assert profile["measurement_semantics"]["source"] == "csv_inference"
    assert profile["value_counts"] == {"0": 99, "2": 1}
    pd.testing.assert_frame_equal(frame, original)


def test_measurement_semantics_require_locatable_user_evidence():
    summary = get_data_summary(pd.DataFrame({"X": [0, 1, 2]}))
    apply_measurement_semantics(summary, [{"column": "X", "kind": "count", "evidence": "X 是告警次数"}], user_text="分析数据中的关系")
    assert summary["column_profiles"]["X"]["measurement_semantics"]["kind"] == "unknown"
    apply_measurement_semantics(summary, [{"column": "X", "kind": "count", "evidence": "X 是告警次数"}], user_text="X 是告警次数，请分析变量关系")
    semantics = summary["column_profiles"]["X"]["measurement_semantics"]
    assert semantics["kind"] == "count"
    assert semantics["status"] == "user_stated"
    assert semantics["source"] == "user_description"


def test_nonlinearity_uses_numeric_conditions_instead_of_old_type_labels():
    rng = np.random.default_rng(7)
    frame = pd.DataFrame({"X": rng.integers(0, 20, 200), "Y": rng.normal(size=200)})
    summary = get_data_summary(frame)
    summary["column_profiles"]["X"]["inferred_type"] = "categorical_numeric"
    result = measure_nonlinearity(frame, summary, n_perm=20)
    assert result["ratio"] is not None and result["n_vars"] == 2
    short = measure_nonlinearity(pd.DataFrame({"X": [0, 1] * 100, "Y": [1, 2] * 100}), None)
    assert short["verdict"] == "insufficient"
    assert short["reason"] == "fewer_than_two_numeric_columns_with_at_least_10_distinct_values"


def test_empty_missing_and_nonfinite_profiles_are_explicit():
    assert get_data_summary(pd.DataFrame())["quality_assessment"]["total_missing_ratio"] == 0
    summary = get_data_summary(pd.DataFrame({"X": [np.nan, np.nan], "Y": [np.inf, -np.inf]}))
    assert summary["column_profiles"]["X"]["missing_count"] == 2
    assert summary["column_profiles"]["Y"]["statistical_features"]["non_finite_count"] == 2


def test_algorithm_profile_uses_numeric_facts_without_guessing_identifier_semantics():
    frame = pd.DataFrame({"X": range(100), "Y": [0, 1] * 50})
    direct = build_data_profile(frame)
    restored = profile_from_analysis_parameters(get_data_summary(frame))
    assert direct.numeric_columns == restored.numeric_columns == ("X", "Y")
    assert direct.suspected_id_columns == restored.suspected_id_columns == ()
