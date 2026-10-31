"""真实参数规则、CSV 校验与内部重试反馈协作；不调用替代模型。"""

from copy import deepcopy
from io import BytesIO
import json

import pandas as pd
import pytest

from Agent.data_processing.graph import _validation_retry_message
from tests.support.research_specs import commit
from Agent.data_processing.parameters import research_parameters
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from app.agent.worker.event_adapter import LangGraphEventAdapter


@pytest.mark.parametrize("patch, expected", [
    ({}, {"target": "收入", "treatment": "培训"}),
    ({"target": None}, {"target": None, "treatment": "培训"}),
    ({"treatment": None}, {"target": "收入", "treatment": None}),
])
def test_absent_roles_and_explicit_null_use_existing_revision_contract(patch, expected):
    before = research_parameters({"target": "收入", "treatment": "培训", "measurement_semantics": []})
    actual, changes = commit(before, [], patch=patch,
        job_id="00000000-0000-0000-0000-000000000963", source_input_id=1)
    assert actual == research_parameters({**expected, "measurement_semantics": []})
    assert len(changes) == bool(patch)
    assert before["target"] == "收入" and before["treatment"] == "培训"


def test_validation_feedback_uses_report_roles_and_actual_csv_columns():
    role = "全部作为节点参与因果发现"
    data = ("0,1,2,3,4\n" + "\n".join(",".join(str((row * (column + 1)) % 37)
        for column in range(5)) for row in range(120)) + "\n").encode()
    report = validate_candidate(data_bytes=data, script_bytes=b"print('prepared')\n",
        metadata={"summary": "保留原始测量", "observation_unit": "一次独立测量",
            "transformations": ["保留全部列"], "variable_semantics": {}},
        analysis_parameters={"target": role, "treatment": None},
        specifications=[PC_SPEC], max_bytes=100_000)
    before = deepcopy(report)
    assert not report["accepted"]
    assert [error["code"] for error in report["errors"]] == ["target_missing"]
    message = _validation_retry_message(42, report)
    details = json.loads(message.content.split("\n", 1)[1])
    assert details["target"] == role
    assert details["treatment"] is None
    assert details["columns"] == ["0", "1", "2", "3", "4"]
    assert details["errors"] == report["errors"]
    assert details["algorithm_compatibility"] == report["algorithm_compatibility"]
    assert report == before
    assert message.id == _validation_retry_message(42, report).id
    assert message.id != _validation_retry_message(43, report).id

    without_roles = validate_candidate(data_bytes=data, script_bytes=b"print('prepared')\n",
        metadata={"summary": "保留原始测量", "observation_unit": "一次独立测量",
            "transformations": ["保留全部列"], "variable_semantics": {}},
        analysis_parameters={"target": None, "treatment": None},
        specifications=[PC_SPEC], max_bytes=100_000)
    assert without_roles["accepted"]
    assert without_roles["errors"] == []


def test_internal_retry_parameters_are_not_exposed_by_public_event_adapter():
    report = {"target": "internal-target-value", "treatment": "internal-treatment-value",
        "columns": ["internal-column-value"], "errors": [{"code": "target_missing", "message": "缺少目标"}]}
    message = _validation_retry_message(42, report)
    adapter = LangGraphEventAdapter("00000000-0000-0000-0000-000000000963", 1)
    state = {"messages": [message], "data_processing_retry_report": report}
    events = adapter.convert({"type": "tasks", "ns": (), "data": {
        "id": "retry-task", "name": "data_processing", "input": state}})
    events += adapter.convert({"type": "updates", "ns": (), "data": {"data_processing": state}})
    events += adapter.convert({"type": "messages", "ns": (),
        "data": (message, {"langgraph_node": "data_processing"})})
    events += adapter.convert({"type": "tasks", "ns": (), "data": {
        "id": "retry-task", "name": "data_processing", "result": state}})
    assert [event["type"] for event in events] == ["node_start", "node_end"]
    serialized = json.dumps(events)
    assert all(value not in serialized for value in ("internal-target-value", "internal-treatment-value", "internal-column-value"))


def test_early_validation_failure_does_not_invent_roles_or_columns():
    report = validate_candidate(data_bytes=b"", script_bytes=b"print('prepared')\n",
        metadata={}, analysis_parameters={"target": "收入"}, specifications=[PC_SPEC], max_bytes=1000)
    details = json.loads(_validation_retry_message(1, report).content.split("\n", 1)[1])
    assert details["errors"] == report["errors"]
    assert details["target"] is None
    assert details["treatment"] is None
    assert details["columns"] == []


@pytest.mark.parametrize("declare_dropped_column", [True, False])
def test_output_csv_mapping_keeps_renamed_measurements_without_inheriting_derived_types(declare_dropped_column):
    source = ("x,y,z\n" + "\n".join(f"{row / 7},{row % 13},{row / 9}" for row in range(120))).encode()
    frame = pd.read_csv(BytesIO(source))
    output = frame.rename(columns={"x": "feature"}).drop(columns=["y"])
    output["total"] = frame["x"] + frame["y"]
    parameters = {"target": None, "treatment": None, "measurement_semantics": [
        {"column": column, "kind": "continuous", "evidence": "全部原始输入列是连续测量"}
        for column in frame.columns]}
    mapping = {"x": "feature"}
    if declare_dropped_column:
        mapping["y"] = None
    report = validate_candidate(data_bytes=output.to_csv(index=False).encode(),
        script_bytes=b"# This check validates CSV bytes, not script execution.\n",
        metadata={"summary": "重命名 x、删除 y，并计算 total", "observation_unit": "一次测量",
            "transformations": ["重命名", "删列", "计算"], "measurement_columns": mapping,
            "variable_semantics": {"feature": "原 x 测量", "z": "原 z 测量", "total": "计算结果"}},
        analysis_parameters=parameters, specifications=[PC_SPEC], max_bytes=100_000)
    assert report["accepted"] is declare_dropped_column
    if declare_dropped_column:
        assert report["semantic_kinds"] == {"feature": "continuous", "z": "continuous"}
        assert "total" not in report["semantic_kinds"]
    else:
        assert "measurement_semantics_invalid" in {item["code"] for item in report["errors"]}
    assert [item["column"] for item in parameters["measurement_semantics"]] == ["x", "y", "z"]
