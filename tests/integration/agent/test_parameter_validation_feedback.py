"""真实参数规则、CSV 校验与内部重试反馈协作；不调用替代模型。"""

from copy import deepcopy
import json

import pytest

from Agent.data_processing.graph import _validation_retry_message
from Agent.data_processing.parameters import ParameterExtraction, apply_parameter_patch, extraction_patch
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from app.agent.worker.event_adapter import LangGraphEventAdapter


@pytest.mark.parametrize("patch, expected", [
    ({}, {"target": "收入", "treatment": "培训"}),
    ({"target": None}, {"target": None, "treatment": "培训"}),
    ({"treatment": None}, {"target": "收入", "treatment": None}),
])
def test_absent_roles_and_explicit_null_use_existing_revision_contract(patch, expected):
    before = {"target": "收入", "treatment": "培训"}
    answer = "用户明确指定本次修订"
    extraction = ParameterExtraction.model_validate({"updates": [
        {"field": key, "value": value, "evidence": answer} for key, value in patch.items()
    ]})
    actual_patch = extraction_patch(extraction, current_parameters=before)
    actual, changes = apply_parameter_patch(before, [], patch=actual_patch,
        job_id="00000000-0000-0000-0000-000000000963", source_input_id=1)
    assert actual == expected
    assert len(changes) == bool(patch)
    assert before == {"target": "收入", "treatment": "培训"}


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
