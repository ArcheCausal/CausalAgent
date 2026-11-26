"""真实画像函数与父子 State 转换的纯函数回归，不执行模型或算法。"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from Agent.deep_agent.state import from_deep_agent_output, to_deep_agent_input
from Agent.deep_agent_tools.algorithm_tools import AlgorithmTool
from Agent.deep_agent_tools.dependency_dispatch import _initial_artifacts
from Agent.deep_agent_tools.models import DataProfile
from Agent.deep_agent_tools.preprocessing import build_data_profile, build_data_profile_from_csv


@pytest.fixture
def quality_profile() -> DataProfile:
    """真实列值同时覆盖字符串分类、缺失、无穷、空列、常数和标识符。"""

    return build_data_profile(
        pd.DataFrame(
            {
                "count": [0, 1, 0, 1, 1, 0],
                "city": ["北京", "上海", "北京", "上海", "北京", "上海"],
                "missing": [1.5, None, 2.5, 3.5, 1.5, 2.5],
                "nonfinite": [0.5, float("inf"), 1.5, 0.5, 1.5, 2.5],
                "empty": [None] * 6,
                "constant": [7] * 6,
                "record_id": [101, 102, 103, 104, 105, 106],
            }
        )
    )


def _prompt_payload(state: dict) -> dict:
    return json.loads(state["messages"][0]["content"])


@pytest.mark.parametrize("as_model", [False, True], ids=["mapping", "model"])
def test_complete_profile_preserves_all_facts_and_consumer_input(
    quality_profile: DataProfile, as_model: bool
) -> None:
    original = quality_profile.model_dump(mode="json")
    assert quality_profile.categorical_columns == ("city",)
    assert quality_profile.missing_columns == ("missing", "empty")
    assert quality_profile.non_finite_columns == ("nonfinite",)
    assert quality_profile.empty_columns == ("empty",)
    assert quality_profile.constant_columns == ("constant",)
    assert quality_profile.suspected_id_columns == ()
    assert "record_id" in quality_profile.numeric_columns
    parent = {
        "analysis_parameters": {
            "data_profile": quality_profile if as_model else original,
            "columns": list(quality_profile.column_names),
            "column_profiles": {
                name: {"inferred_type": "continuous"}
                for name in quality_profile.column_names
            },
        },
        # 旧派生字段不能覆盖本次画像的质量事实。
        "missing_values_present": False,
    }
    parent_snapshot = deepcopy(parent)

    state = to_deep_agent_input(parent)
    profile = state["data_profile"]

    assert profile.model_dump(mode="json") == original
    assert profile is not quality_profile
    assert profile.issues[0] is not quality_profile.issues[0]
    assert state["missing_values_present"] is True
    assert _prompt_payload(state)["data_profile"] == original
    assert _prompt_payload(state)["analysis_parameters"]["data_profile"] == original
    assert state["analysis_parameters"]["data_profile"] == original
    assert parent == parent_snapshot
    tool_profile = AlgorithmTool._data_profile_from_state(state)
    assert tool_profile == profile
    assert tool_profile is not profile
    assert _initial_artifacts(state) == frozenset({"tabular_dataset"})
    assert "data_profile" not in from_deep_agent_output(state)

    profile.issues[0].message = "副本中的说明"
    assert quality_profile.model_dump(mode="json") == original
    assert parent == parent_snapshot


@pytest.mark.parametrize("stale_top", [None, {}, "invalid", DataProfile(row_count=1, column_count=0)])
def test_nested_profile_takes_priority_even_over_invalid_top(stale_top: object) -> None:
    profile = build_data_profile_from_csv("count,flag\n0,1\n1,0\n0,1\n")
    state = to_deep_agent_input(
        {
            "data_profile": stale_top,
            "analysis_parameters": {
                "data_profile": profile.model_dump(mode="json"),
                "columns": ["old"],
                "n_rows": 900,
                "column_profiles": {"old": {"inferred_type": "continuous"}},
            },
            "file_summary": {"rows": 900, "columns": ["old"]},
        }
    )

    assert state["data_profile"] == profile
    assert _initial_artifacts(state) == frozenset(
        {"tabular_dataset", "continuous_tabular_dataset"}
    )


@pytest.mark.parametrize("as_model", [False, True], ids=["mapping", "model"])
def test_top_profile_is_compatible_and_clears_stale_missing_flag(as_model: bool) -> None:
    profile = build_data_profile_from_csv("a,b\n0,1\n1,0\n0,1\n")
    state = to_deep_agent_input(
        {
            "data_profile": profile if as_model else profile.model_dump(mode="json"),
            "analysis_parameters": {"target": "b"},
            "missing_values_present": True,
        }
    )

    assert state["data_profile"] == profile
    assert state["missing_values_present"] is False
    assert state["analysis_parameters"]["target"] == "b"
    assert _prompt_payload(state)["analysis_parameters"]["data_profile"] == profile.model_dump(mode="json")


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        [],
        "invalid",
        {"row_count": -1, "column_count": 2},
        {"row_count": 3, "column_count": 2, "numeric_columns": "a,b"},
        {"row_count": 3, "column_count": 2, "unknown_field": True},
        {"row_count": 3, "column_count": 2, "issues": [{"code": "missing_values"}]},
        DataProfile.model_construct(row_count=-1, column_count=2),
    ],
)
@pytest.mark.parametrize("source", ["nested", "top"])
def test_invalid_present_profile_fails_without_fallback(invalid: object, source: str) -> None:
    parent = {
        "analysis_parameters": {
            "columns": ["a", "b"],
            "n_rows": 3,
            "column_profiles": {"a": {"inferred_type": "continuous"}},
        },
        "file_summary": {"rows": 3, "columns": ["a", "b"]},
    }
    if source == "nested":
        parent["analysis_parameters"]["data_profile"] = invalid
        parent["data_profile"] = build_data_profile_from_csv("a,b\n0,1\n1,0\n")
    else:
        parent["data_profile"] = invalid

    with pytest.raises(ValidationError):
        to_deep_agent_input(parent)


@pytest.mark.parametrize("label", ["continuous", "binary", "categorical_numeric", "categorical", "id"])
def test_legacy_statistics_never_confirm_storage_types(label: str) -> None:
    state = to_deep_agent_input(
        {
            "analysis_parameters": {
                "n_rows": 4,
                "columns": ["z", "a"],
                "column_profiles": {
                    "z": {"inferred_type": label, "missing_count": 1},
                    "a": {"inferred_type": label},
                },
            }
        }
    )

    profile = state["data_profile"]
    assert (profile.row_count, profile.column_count, profile.column_names) == (4, 2, ("z", "a"))
    assert profile.numeric_columns == ()
    assert profile.categorical_columns == ()
    assert state["missing_values_present"] is True
    assert _initial_artifacts(state) == frozenset({"tabular_dataset"})
    assert _prompt_payload(state)["data_profile"] == profile.model_dump(mode="json")
    assert AlgorithmTool._data_profile_from_state(state) == profile


@pytest.mark.parametrize(
    ("parameters", "summary", "rows", "columns"),
    [
        ({}, {"rows": 4, "columns": ["z", "a"]}, 4, ("z", "a")),
        ({"n_rows": 4}, {"columns": ["z", "a"]}, 4, ("z", "a")),
        ({"columns": ["z", "a"]}, {"rows": 4}, 4, ("z", "a")),
        ({"n_rows": 0, "columns": []}, {"rows": 4, "columns": ["old"]}, 0, ()),
        ({"n_rows": 4, "column_profiles": {"z": {}, "a": {}}}, {}, 4, ("z", "a")),
        ({}, {}, 0, ()),
    ],
)
def test_legacy_sources_recover_only_basic_facts(
    parameters: dict, summary: dict, rows: int, columns: tuple[str, ...]
) -> None:
    parent = {"analysis_parameters": parameters, "file_summary": summary}
    state = to_deep_agent_input(parent)
    profile = state["data_profile"]

    assert profile == DataProfile(row_count=rows, column_count=len(columns), column_names=columns)
    assert state["file_summary"] == summary
    assert "continuous_tabular_dataset" not in _initial_artifacts(state)


def test_real_alarm_csv_keeps_all_eleven_numeric_columns() -> None:
    """外部文件只读挂载；用例不写入或输出用户 CSV 正文。"""

    configured = os.environ.get("CAUSALAGENT_PROFILE_TEST_CSV")
    path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[3] / "tests/dataset/Alarm-window-counts-300.csv"
    )
    if not configured and not path.is_file():
        pytest.skip("需只读挂载 Alarm CSV，并设置 CAUSALAGENT_PROFILE_TEST_CSV")
    profile = build_data_profile_from_csv(path.read_text(encoding="utf-8-sig"))
    assert profile.row_count == len(pd.read_csv(path))
    assert profile.row_count >= 2
    assert profile.column_count == 11
    assert profile.numeric_columns == profile.column_names
    assert profile.categorical_columns == ()
    state = to_deep_agent_input(
        {
            "analysis_parameters": {
                "data_profile": profile.model_dump(mode="json"),
                "columns": list(profile.column_names),
                "n_rows": profile.row_count,
                "column_profiles": {
                    column: {"inferred_type": "categorical_numeric"}
                    for column in profile.column_names
                },
            },
            "data_profile": DataProfile(row_count=1, column_count=0),
        }
    )

    assert state["data_profile"] == profile
    assert _prompt_payload(state)["data_profile"] == profile.model_dump(mode="json")
    assert AlgorithmTool._data_profile_from_state(state) == profile
    assert _initial_artifacts(state) == frozenset(
        {"tabular_dataset", "continuous_tabular_dataset"}
    )


@pytest.mark.parametrize("explicit_null", [False, True])
def test_consumers_do_not_rebuild_missing_profile_from_summary(explicit_null: bool) -> None:
    state = {"file_summary": {"rows": 4, "columns": ["a", "b"]}}
    if explicit_null:
        state["data_profile"] = None

    with pytest.raises(ValueError):
        AlgorithmTool._data_profile_from_state(state)
    assert _initial_artifacts(state) == frozenset()


@pytest.mark.parametrize(
    "invalid",
    [{}, [], "invalid", {"row_count": -1, "column_count": 2}],
)
def test_consumers_reject_invalid_profile_instead_of_summary_fallback(invalid: object) -> None:
    state = {
        "data_profile": invalid,
        "file_summary": {"rows": 4, "columns": ["a", "b"]},
    }

    with pytest.raises(ValueError):
        AlgorithmTool._data_profile_from_state(state)
    with pytest.raises(ValueError):
        _initial_artifacts(state)


@pytest.mark.parametrize(
    ("columns", "numeric"),
    [(("a", "b"), ("a", "a")), (("a", "b"), ("a", "other")), (("a", "a"), ("a", "a")), ((), ())],
)
def test_unconfirmed_column_sets_never_publish_numeric_artifact(
    columns: tuple[str, ...], numeric: tuple[str, ...]
) -> None:
    # 历史最小画像允许只有 counts；不改变全局 schema 的兼容规则。
    profile = DataProfile(
        row_count=4, column_count=2, column_names=columns, numeric_columns=numeric
    )
    state = to_deep_agent_input({"data_profile": profile})

    assert state["data_profile"] == profile
    assert AlgorithmTool._data_profile_from_state(state) == profile
    assert _initial_artifacts(state) == frozenset({"tabular_dataset"})
