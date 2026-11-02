"""PC 的共享选择规则与真实候选 CSV 校验；不替代算法实跑。"""
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from Agent.data_processing.plans import build_processing_plan_version
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
from Agent.deep_agent_tools.interaction import DataProcessingPlanSummary
from Agent.measurement_semantics import semantic_kinds_from_validation_report
from Agent.pc_test import resolve_pc_test, MAX_PC_COUNT_LEVELS


@pytest.mark.parametrize("kinds,requested,cardinalities,selected", [
    ({"x": "binary", "y": "binary"}, "auto", None, "chisq"),
    ({"x": "category", "y": "ordinal"}, "chisq", None, "chisq"),
    ({"x": "binary", "y": "binary"}, "fisherz", None, None),
    ({"x": "continuous", "y": "continuous"}, "auto", None, "fisherz"),
    ({"x": "continuous", "y": "continuous"}, "fisherz", None, "fisherz"),
    ({"x": "continuous", "y": "continuous"}, "chisq", None, None),
    ({"x": "continuous", "y": "binary"}, "auto", None, None),
    ({"x": "continuous", "y": "binary"}, "chisq", None, None),
    ({}, "auto", None, None),
    ({"x": "binary", "y": "unknown"}, "chisq", None, None),
    ({"x": "binary"}, "auto", None, None),
    ({"x": "count", "y": "binary"}, "auto", {"x": 5}, "chisq"),
    ({"x": "count", "y": "binary"}, "auto", {"x": MAX_PC_COUNT_LEVELS + 1}, None),
    ({"x": "count", "y": "binary"}, "chisq", None, None),
])
def test_shared_resolution(kinds, requested, cardinalities, selected):
    result = resolve_pc_test(columns=["x", "y"], semantic_kinds=kinds,
                             test=requested, count_cardinalities=cardinalities)
    assert result.independence_test == selected
    assert result.requested_test == requested
    assert result.selection_reason


def candidate():
    # 原始告警类型转为是否为0、是否为1；全部行来自真实 Alarm 文件。
    raw = pd.read_csv(Path(__file__).resolve().parents[3] / "tests/dataset/Alarm.csv")
    frame = pd.DataFrame({"a": (raw.alarm_id == 0).astype(int),
                          "b": (raw.alarm_id == 1).astype(int)})
    plan = {"summary": "按告警类型构造二值变量", "observation_unit": "原始事件",
            "transformations": ["是否为指定告警类型"], "analysis_columns": ["a", "b"],
            "variable_semantics": {"a": "类型0", "b": "类型1"},
            "measurement_columns": {"alarm_id": None},
            "derived_measurement_semantics": [
                {"column": c, "kind": "binary", "evidence": "是否为指定类型"} for c in frame]}
    return frame, plan


def report(frame, plan):
    return validate_candidate(data_bytes=frame.to_csv(index=False).encode(), script_bytes=b"pass",
        metadata=plan, analysis_parameters={"effect_request": None, "measurement_semantics": [
            {"column": "alarm_id", "kind": "category", "evidence": "告警类型编号"}]},
        specifications=DEFAULT_ALGORITHM_SPECS, max_bytes=10_000_000)


def test_derived_binary_snapshot_is_frozen_without_original_category():
    frame, plan = candidate()
    actual = report(frame, plan)
    assert actual["accepted"], actual["errors"]
    assert semantic_kinds_from_validation_report(actual) == {"a": "binary", "b": "binary"}


@pytest.mark.parametrize("change", ["extra", "missing", "order"])
def test_wrong_output_columns_return_script_repair_feedback(change):
    frame, plan = candidate()
    if change == "extra":
        frame["event_id"] = range(len(frame))
    elif change == "missing":
        frame = frame[["a"]]
    else:
        frame = frame[["b", "a"]]
    actual = report(frame, plan)
    assert not actual["accepted"]
    assert "analysis_columns_mismatch" in {e["code"] for e in actual["errors"]}


def test_binary_declaration_rejects_values_outside_zero_one():
    frame, plan = candidate()
    frame.loc[0, "a"] = 2
    actual = report(frame, plan)
    assert not actual["accepted"]
    assert "binary_values_invalid" in {e["code"] for e in actual["errors"]}


def test_missing_analysis_columns_and_conflicting_inheritance_are_rejected():
    frame, plan = candidate()
    del plan["analysis_columns"]
    assert "analysis_columns_invalid" in {e["code"] for e in report(frame, plan)["errors"]}
    plan["analysis_columns"] = ["a", "b"]
    plan["measurement_columns"]["alarm_id"] = "a"
    assert "measurement_columns_invalid" in {e["code"] for e in report(frame, plan)["errors"]}


@pytest.mark.parametrize("declarations", [None, {}, [None], [{"column": "a", "kind": "wrong", "evidence": "依据"}]])
def test_malformed_derived_semantics_return_feedback_instead_of_crashing(declarations):
    frame, plan = candidate()
    plan["derived_measurement_semantics"] = declarations
    assert not report(frame, plan)["accepted"]


def test_plan_identity_includes_columns_and_derived_semantics():
    _, plan = candidate()
    def identity(metadata):
        return build_processing_plan_version(job_id="acceptance", source_hash="a" * 64,
                                             semantic_identity="b" * 64, metadata=metadata)
    for change in ("columns", "semantics"):
        revised = deepcopy(plan)
        if change == "columns":
            revised["analysis_columns"].reverse()
        else:
            revised["derived_measurement_semantics"][0]["kind"] = "count"
        assert identity(revised) != identity(plan)
    confirmed = DataProcessingPlanSummary.model_validate(plan).model_dump(mode="json")
    assert confirmed["analysis_columns"] == ["a", "b"]
    assert confirmed["derived_measurement_semantics"] == plan["derived_measurement_semantics"]


@pytest.mark.parametrize("columns", [[], ["a", "a"], [" a"], [""]])
def test_plan_requires_unique_nonempty_analysis_columns(columns):
    _, plan = candidate()
    plan["analysis_columns"] = columns
    with pytest.raises(ValidationError):
        DataProcessingPlanSummary.model_validate(plan)
