"""报告图表按真实 dtype、有限值基数和原始类别生成的纯函数契约。"""

import json

import numpy as np
import pandas as pd
import pytest

from Agent.Report.assets import (
    CORRELATION_HEATMAP_KEY,
    _bar_asset,
    _histogram_asset,
    build_chart_assets,
)


@pytest.mark.parametrize(
    "inferred_type", [None, "continuous", "categorical", "datetime", "count"]
)
@pytest.mark.parametrize(
    "semantic_kind", ["unknown", "continuous", "ordinal", "count"]
)
def test_low_cardinality_numeric_values_keep_natural_order_and_rare_values(
    inferred_type: str | None, semantic_kind: str
) -> None:
    frame = pd.DataFrame({"value": [10] * 100 + [-10, -2, 0, 2, 100]})
    profile = {
        "storage_type": "int64",
        "inferred_type": inferred_type,
        "unique_count": 999,
        "statistical_features": {
            "is_numeric": True,
            "integer_valued": True,
            "nonnegative": False,
            "distinct_count": 999,
            "low_cardinality": False,
            "low_frequency": True,
        },
        "measurement_semantics": {
            "kind": semantic_kind,
            "status": "confirmed",
            "source": "user",
            "evidence": [],
        },
    }

    assets = build_chart_assets(frame, {"column_profiles": {"value": profile}})

    assert set(assets) == {"chart_bar_value"}
    asset = assets["chart_bar_value"]
    assert asset.data == {
        "categories": ["-10", "-2", "0", "2", "10", "100"],
        "counts": [1, 1, 1, 1, 100, 1],
    }
    assert asset.metadata["storage_type"] == str(frame["value"].dtype)
    assert asset.metadata["sample_count"] == len(frame)
    assert asset.metadata["unit"] is None


@pytest.mark.parametrize("distinct_count", [10, 11])
@pytest.mark.parametrize("dtype", ["int64", "float64", "Int64", "Float64"])
def test_numeric_distribution_switches_after_ten_actual_distinct_values(
    distinct_count: int, dtype: str
) -> None:
    frame = pd.DataFrame({"value": pd.Series(range(distinct_count), dtype=dtype)})
    assets = build_chart_assets(
        frame,
        {
            "column_profiles": {
                "value": {
                    "inferred_type": "categorical_numeric",
                    "statistical_features": {"low_cardinality": True, "distinct_count": 2},
                    "measurement_semantics": {"kind": "nominal"},
                }
            }
        },
    )

    expected_type = "bar" if distinct_count == 10 else "histogram"
    assert set(assets) == {f"chart_{expected_type}_value"}
    asset = assets[f"chart_{expected_type}_value"]
    assert asset.chart_type == expected_type
    assert sum(asset.data["counts"]) == distinct_count
    if expected_type == "bar":
        assert asset.data["categories"] == [str(value) for value in frame["value"]]
    else:
        assert len(asset.data["bins"]) == len(asset.data["counts"]) + 1


@pytest.mark.parametrize("dtype", ["float64", "Float64"])
def test_fractional_numeric_bars_filter_nonfinite_values(dtype: str) -> None:
    frame = pd.DataFrame(
        {"value": pd.Series([10.5, -2.5, 0.25, 10.5, np.nan, np.inf, -np.inf], dtype=dtype)}
    )

    asset = _bar_asset(frame, "value", {})

    assert asset is not None
    assert asset.data == {"categories": ["-2.5", "0.25", "10.5"], "counts": [1, 1, 2]}
    assert asset.metadata["sample_count"] == 4


def test_numeric_bars_preserve_distinct_large_integers() -> None:
    values = [2**63 + 10, 2**63 + 2, 2**63 + 1, 2**63 + 2]
    frame = pd.DataFrame({"value": pd.Series(values, dtype="uint64")})

    asset = build_chart_assets(frame, None)["chart_bar_value"]

    assert asset.data == {
        "categories": [str(2**63 + 1), str(2**63 + 2), str(2**63 + 10)],
        "counts": [1, 2, 1],
    }


@pytest.mark.parametrize("dtype", ["object", "string", "category"])
def test_numeric_looking_strings_remain_distinct_actual_categories(dtype: str) -> None:
    frame = pd.DataFrame(
        {"code": pd.Series(["01", "1", "1.0", "10", "2", "2", None], dtype=dtype)}
    )
    assets = build_chart_assets(
        frame,
        {
            "column_profiles": {
                "code": {
                    "storage_type": "float64",
                    "inferred_type": "continuous",
                    "unique_count": 999,
                    "statistical_features": {"is_numeric": True},
                    "measurement_semantics": {"kind": "continuous"},
                }
            }
        },
    )

    assert set(assets) == {"chart_bar_code"}
    asset = assets["chart_bar_code"]
    assert asset.data == {
        "categories": ["2", "01", "1", "1.0", "10"],
        "counts": [2, 1, 1, 1, 1],
    }
    assert asset.metadata["sample_count"] == 6
    assert asset.metadata["storage_type"] == str(frame["code"].dtype)
    assert _histogram_asset(frame, "code", {}) is None


@pytest.mark.parametrize("dtype", ["float64", "Float64"])
def test_histograms_exclude_nonfinite_values_before_binning(dtype: str) -> None:
    frame = pd.DataFrame(
        {"value": pd.Series(list(range(11)) + [np.nan, np.inf, -np.inf], dtype=dtype)}
    )
    snapshot = frame.copy(deep=True)

    assets = build_chart_assets(frame, None)
    direct = _histogram_asset(frame, "value", {})

    assert set(assets) == {"chart_histogram_value"}
    asset = assets["chart_histogram_value"]
    assert direct == asset
    assert asset.metadata["sample_count"] == 11
    assert sum(asset.data["counts"]) == 11
    assert asset.data["bins"][0] == 0.0
    assert asset.data["bins"][-1] == 10.0
    json.dumps(asset.model_dump(mode="json"), allow_nan=False)
    pd.testing.assert_frame_equal(frame, snapshot)


def test_nonfinite_values_do_not_inflate_numeric_bar_cardinality() -> None:
    frame = pd.DataFrame({"value": list(range(10)) + [np.nan, np.inf, -np.inf]})

    assets = build_chart_assets(frame, None)

    assert set(assets) == {"chart_bar_value"}
    assert len(assets["chart_bar_value"].data["categories"]) == 10
    assert sum(assets["chart_bar_value"].data["counts"]) == 10


def test_heatmap_uses_all_numeric_cardinalities_and_filters_constants_and_nonfinite() -> None:
    frame = pd.DataFrame(
        {
            "binary": [0, 1] * 6,
            "ordinal": pd.Series([0, 2, 10] * 4, dtype="Int64"),
            "continuous": [float(value) for value in range(12)],
            "descending": [11.0, 10.0, 9.0, 8.0, np.inf, -np.inf, 5.0, 4.0, 3.0, 2.0, 1.0, np.nan],
            "constant": [7] * 12,
            "finite_constant": [7.0] * 9 + [np.inf, -np.inf, np.nan],
            "empty": pd.Series([pd.NA] * 12, dtype="Float64"),
            "nonfinite": [np.inf, -np.inf, np.nan] * 4,
            "string_number": [str(value) for value in range(12)],
        }
    )
    snapshot = frame.copy(deep=True)
    profiles = {
        column: {"inferred_type": "categorical", "measurement_semantics": {"kind": "nominal"}}
        for column in frame.columns
    }
    params = {"n_rows": len(frame), "column_profiles": profiles}

    assets = build_chart_assets(frame, params)

    assert assets["chart_bar_binary"].chart_type == "bar"
    assert assets["chart_bar_ordinal"].chart_type == "bar"
    assert assets["chart_histogram_continuous"].chart_type == "histogram"
    heatmap = assets[CORRELATION_HEATMAP_KEY]
    assert heatmap.data["variables"] == ["binary", "ordinal", "continuous", "descending"]
    matrix = np.array(heatmap.data["matrix"])
    assert np.isfinite(matrix).all()
    np.testing.assert_array_equal(np.diag(matrix), np.ones(4))
    np.testing.assert_array_equal(matrix, matrix.T)
    assert matrix[2, 3] == matrix[3, 2] == -1.0
    assert heatmap.metadata["sample_count"] == len(frame)
    json.dumps({key: asset.model_dump(mode="json") for key, asset in assets.items()}, allow_nan=False)
    pd.testing.assert_frame_equal(frame, snapshot)


def test_heatmap_is_absent_without_two_nonconstant_numeric_columns() -> None:
    frame = pd.DataFrame(
        {"value": [0, 1, 0], "constant": [1, 1, 1], "string": ["0", "1", "0"]}
    )

    assert CORRELATION_HEATMAP_KEY not in build_chart_assets(frame, None)


@pytest.mark.parametrize(
    "series",
    [
        pd.Series([], dtype="float64"),
        pd.Series([np.nan, np.inf, -np.inf]),
        pd.Series([pd.NA, pd.NA], dtype="Int64"),
    ],
)
def test_numeric_columns_without_finite_samples_emit_no_assets(series: pd.Series) -> None:
    frame = pd.DataFrame({"value": series})

    assert build_chart_assets(frame, None) == {}
    assert _bar_asset(frame, "value", {}) is None
    assert _histogram_asset(frame, "value", {}) is None
