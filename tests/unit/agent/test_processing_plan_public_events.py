"""直接调用公开事件纯函数，验证方案确认的字段、类型和大小边界。"""

from copy import deepcopy
import json

import pytest

from Agent.deep_agent_tools.interaction import (
    DataProcessingPlanConfirmationRequest,
    DataProcessingPlanSummary,
)
from app.agent.public_events import public_event_payload


PLAN_VERSION = "0123456789abcdef" * 4


def _plan() -> dict:
    return {
        "summary": "按设备和日期统计告警数。",
        "observation_unit": "设备-日期",
        "transformations": ["按设备和日期分组并计数"],
        "variable_semantics": {"alarm_count": "该设备当天的告警次数"},
        "role_columns": {},
        "measurement_columns": {},
        "limitations": ["不包含未触发告警的设备日期"],
    }


def _interaction(**overrides: object) -> dict:
    return {
        "type": "interaction",
        "interaction_type": "data_processing_plan_confirmation",
        "question_id": "q-plan-1",
        "message": "请检查方案。",
        "expires_at": "2026-10-23T12:05:00Z",
        "allow_other": True,
        "plan_version": PLAN_VERSION,
        "plan": _plan(),
        **overrides,
    }


def test_confirmation_request_survives_public_event_projection() -> None:
    request = DataProcessingPlanConfirmationRequest(
        prompt="请检查方案。",
        question_id="q-plan-1",
        expires_at="2026-10-23T12:05:00Z",
        plan_version=PLAN_VERSION,
        plan=DataProcessingPlanSummary.model_validate(_plan()),
    )
    payload = {
        "type": "interrupt",
        "question_id": request.question_id,
        "message": request.prompt,
        "interaction": request.public_payload(),
    }

    public = public_event_payload("interrupt", payload)

    assert public == payload
    assert DataProcessingPlanSummary.model_validate(
        public["interaction"]["plan"]
    ).model_dump(mode="json") == _plan()
    assert json.loads(json.dumps(public, ensure_ascii=False)) == public


def test_unknown_and_internal_fields_are_removed_at_each_plan_boundary() -> None:
    plan = {
        **_plan(),
        "script": "private script",
        "source_hash": "private source identity",
        "runtime": object(),
        "unknown": {"nested": "private value"},
    }
    payload = {
        "interaction": _interaction(
            plan=plan, script="private script", lease_epoch=7, state=object()
        ),
        "attempt": 5,
        "prompt": "private prompt",
        "plan": plan,
        "plan_version": PLAN_VERSION,
    }

    public = public_event_payload("interrupt", payload)

    assert public == {"type": "interrupt", "interaction": _interaction()}
    assert "private" not in json.dumps(public)


@pytest.mark.parametrize(
    "version",
    [None, 64, True, {}, [], object(), "", "a" * 63, "a" * 65,
     "A" * 64, "g" * 64, PLAN_VERSION + "\n", " " + PLAN_VERSION],
)
def test_invalid_version_is_dropped_without_string_conversion(version: object) -> None:
    public = public_event_payload(
        "interrupt", {"interaction": _interaction(plan_version=version)}
    )

    assert "plan_version" not in public["interaction"]
    assert public["interaction"]["plan"] == _plan()


@pytest.mark.parametrize(
    "plan",
    [None, "private script", 1, True, [], (), object(),
     DataProcessingPlanSummary.model_validate(_plan())],
)
def test_plan_requires_a_dictionary_instead_of_an_arbitrary_object(plan: object) -> None:
    public = public_event_payload(
        "interrupt", {"interaction": _interaction(plan=plan)}
    )

    assert "plan" not in public["interaction"]
    assert public["interaction"]["plan_version"] == PLAN_VERSION


@pytest.mark.parametrize("field", ["summary", "observation_unit"])
@pytest.mark.parametrize("value", [None, "", 1, False, [], {}, object()])
def test_invalid_required_text_suppresses_the_plan(field: str, value: object) -> None:
    plan = {**_plan(), field: value}

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    assert "plan" not in public["interaction"]


@pytest.mark.parametrize(
    "field", ["summary", "observation_unit", "transformations", "variable_semantics"]
)
def test_missing_required_field_suppresses_the_plan(field: str) -> None:
    plan = _plan()
    del plan[field]

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    assert "plan" not in public["interaction"]


@pytest.mark.parametrize(
    "value", [None, "private script", 1, True, {}, [], [None, {}, 1, object()]]
)
def test_invalid_transformations_suppress_the_plan(value: object) -> None:
    plan = {**_plan(), "transformations": value}

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    assert "plan" not in public["interaction"]


@pytest.mark.parametrize(
    "value", [None, "private script", 1, True, [], {}, {"x": object()}, {1: "说明"}]
)
def test_invalid_variable_semantics_suppress_the_plan(value: object) -> None:
    plan = {**_plan(), "variable_semantics": value}

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    assert "plan" not in public["interaction"]


def test_nested_non_strings_are_dropped_and_tuples_become_json_arrays() -> None:
    plan = {
        **_plan(),
        "transformations": ("分组计数", {"script": "private"}, None, 1, object()),
        "variable_semantics": {
            "alarm_count": "告警次数",
            "script": {"code": "private"},
            "raw": object(),
            1: "private",
            "flag": True,
        },
        "limitations": ["仅统计告警", {"state": "private"}, False, object()],
    }

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    expected = {
        **_plan(),
        "transformations": ["分组计数"],
        "variable_semantics": {"alarm_count": "告警次数"},
        "limitations": ["仅统计告警"],
    }
    assert public["interaction"]["plan"] == expected
    assert DataProcessingPlanSummary.model_validate(expected).model_dump(mode="json") == expected


@pytest.mark.parametrize("value", [None, "private", 1, True, {}, object()])
def test_invalid_optional_limitations_become_an_empty_array(value: object) -> None:
    plan = {**_plan(), "limitations": value}

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    assert public["interaction"]["plan"] == {**_plan(), "limitations": []}


def test_absent_optional_limitations_use_the_contract_default() -> None:
    plan = _plan()
    del plan["limitations"]

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    assert public["interaction"]["plan"] == {**plan, "limitations": []}


@pytest.mark.parametrize("extra_length", [0, 1, 1000])
def test_plan_text_and_collection_limits(extra_length: int) -> None:
    plan = {
        "summary": "概" * (1200 + extra_length),
        "observation_unit": "观" * (500 + extra_length),
        "transformations": ["转" * (600 + extra_length)] * (12 + extra_length),
        "variable_semantics": {
            f"column_{index}": "义" * (600 + extra_length)
            for index in range(128 + extra_length)
        },
        "limitations": ["限" * (600 + extra_length)] * (12 + extra_length),
        "role_columns": {},
        "measurement_columns": {},
    }

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    expected = {
        "summary": "概" * 1200,
        "observation_unit": "观" * 500,
        "transformations": ["转" * 600] * 12,
        "variable_semantics": {f"column_{index}": "义" * 600 for index in range(128)},
        "role_columns": {},
        "measurement_columns": {},
        "limitations": ["限" * 600] * 12,
    }
    assert public["interaction"]["plan"] == expected
    assert DataProcessingPlanSummary.model_validate(expected).model_dump(mode="json") == expected


def test_oversized_variable_names_are_dropped_without_name_collisions() -> None:
    name = "x" * 255
    plan = {**_plan(), "variable_semantics": {name: "保留", name + "y": "超长"}}

    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})

    assert public["interaction"]["plan"]["variable_semantics"] == {name: "保留"}


def test_role_columns_survive_confirmation_projection_without_internal_fields() -> None:
    plan = {**_plan(), "role_columns": {"target": "alarm_count", "treatment": None, "script": "private"}}
    public = public_event_payload("interrupt", {"interaction": _interaction(plan=plan)})
    assert public["interaction"]["plan"]["role_columns"] == {"target": "alarm_count", "treatment": None}
    assert "private" not in json.dumps(public)


def test_projection_is_idempotent_and_does_not_share_mutable_plan_containers() -> None:
    payload = {"interaction": _interaction()}
    original = deepcopy(payload)

    public = public_event_payload("interrupt", payload)

    assert payload == original
    assert public_event_payload("interrupt", public) == public
    plan = public["interaction"]["plan"]
    plan["transformations"].append("额外转换")
    plan["variable_semantics"]["alarm_count"] = "额外说明"
    plan["limitations"].append("额外限制")
    assert payload == original


def test_legacy_processing_options_keep_their_public_fields_and_limits() -> None:
    step = {"operation": "impute", "columns": ["x"], "method": "median"}
    option = {
        "option_id": "option-1",
        "label": "填补缺失值",
        "capability_id": "missing_values",
        "steps": [{**step, "script": "private"}] * 9,
        "impact": ["影" * 601] * 7,
        "risks": ["险" * 601] * 7,
        "internal": "private",
    }
    interaction = {
        "type": "interaction",
        "interaction_type": "data_processing_options",
        "question_id": "q-options-1",
        "message": "请选择处理方式",
        "algorithm_name": "causal_pc",
        "expires_at": "2026-10-23T12:05:00Z",
        "allow_other": True,
        "options": [option] * 4,
    }

    public = public_event_payload("interrupt", {"interaction": interaction})

    expected_option = {
        "option_id": "option-1",
        "label": "填补缺失值",
        "capability_id": "missing_values",
        "steps": [step] * 8,
        "impact": ["影" * 600] * 6,
        "risks": ["险" * 600] * 6,
    }
    assert public["interaction"] == {**interaction, "options": [expected_option] * 3}


def test_legacy_open_question_remains_unchanged() -> None:
    interaction = {
        "type": "interaction",
        "interaction_type": "data_processing_question",
        "question_id": "q-unit-1",
        "message": "观察单位是什么？",
        "expires_at": "2026-10-23T12:05:00Z",
        "allow_other": True,
    }

    public = public_event_payload("interrupt", {"interaction": interaction})

    assert public == {"type": "interrupt", "interaction": interaction}


@pytest.mark.parametrize(
    ("event_type", "fields"),
    [
        ("node_start", {"step_id": "step-1", "node_name": "deep_agent", "title": "分析"}),
        ("progress", {"summary": "处理中"}),
        ("decision", {"summary": "使用 PC", "decision_kind": "algorithm",
                      "tool_name": "causal_pc", "confidence": "high"}),
        ("tool_call_start", {"tool_name": "causal_pc", "argument_keys": ["alpha"]}),
        ("tool_call_result", {"tool_name": "causal_pc", "summary": "失败",
                              "status": "failed", "safe_error_code": "ALGORITHM_EXECUTION_FAILED"}),
        ("node_retry", {"message": "重试", "discard_stream_id": "stream-1"}),
        ("node_end", {"duration": 1.5, "status": "completed", "message": "完成"}),
        ("text_delta", {"stream_id": "stream-1", "sequence": 1, "delta": "回答"}),
        ("decision_delta", {"stream_id": "stream-2", "sequence": 1, "delta": "选择",
                            "decision_kind": "algorithm", "tool_name": "causal_pc"}),
        ("final_result", {"data": {"summary": "分析完成"}}),
        ("interrupt", {"message": "请补充信息", "question_id": "q-1"}),
        ("error", {"message": "处理失败"}),
        ("canceled", {"message": "已取消"}),
        ("heartbeat", {"job_id": "job-1"}),
    ],
)
def test_existing_events_keep_public_fields_and_remove_internal_fields(
    event_type: str, fields: dict
) -> None:
    payload = {**fields, "type": "untrusted", "attempt": 5,
               "raw_result": object(), "plan_version": PLAN_VERSION, "plan": _plan()}

    public = public_event_payload(event_type, payload)

    assert public == {**fields, "type": event_type}


def test_measurement_mapping_is_public_but_other_fields_remain_private():
    plan = {**_plan(), "measurement_columns": {"raw": "renamed", "dropped": None}, "script": "private"}
    public = public_event_payload("interrupt", {"type": "interrupt", "question_id": "q-plan-1",
        "message": "请确认", "interaction": _interaction(plan=plan)})
    assert public["interaction"]["plan"]["measurement_columns"] == plan["measurement_columns"]
    assert "script" not in public["interaction"]["plan"]
