"""用仓库 CSV 和实际画像函数校验算法输入要求。"""

from pathlib import Path

from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC, PC_SPEC
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv


DATASET_DIR = Path(__file__).resolve().parents[2] / "dataset"


def _profile(filename: str):
    return build_data_profile_from_csv(
        (DATASET_DIR / filename).read_text(encoding="utf-8-sig")
    )


def test_profile_reports_actual_missing_and_categorical_columns() -> None:
    profile = _profile("usa_data.CSV")

    assert profile.row_count == 40876
    assert profile.missing_columns
    assert profile.categorical_columns
    assert "missing_values" in {issue.code for issue in profile.issues}


def test_profile_detects_non_finite_and_constant_values() -> None:
    profile = build_data_profile_from_csv("x,y\n1,5\ninf,5\n2,5\n")

    assert profile.non_finite_columns == ("x",)
    assert profile.constant_columns == ("y",)
    assert {issue.code for issue in profile.issues} >= {
        "non_finite_values",
        "constant_column",
    }


def test_algorithm_applicability_uses_the_actual_dataset_profile() -> None:
    numeric = _profile("fMRI1.csv")
    mixed = _profile("usa_data.CSV")

    status, reason = PC_SPEC.assess_applicability(
        profile=numeric,
        execution_columns=numeric.column_names,
    )
    assert (status, reason) == ("ready", None)

    status, reason = CDFM_SPEC.assess_applicability(
        profile=mixed,
        execution_columns=mixed.column_names,
    )
    assert status == "not_applicable"
    assert reason
