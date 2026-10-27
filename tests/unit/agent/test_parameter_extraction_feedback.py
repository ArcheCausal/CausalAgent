"""真实 Pydantic、LangChain parser 与提示词检查；不使用模型或数据库替身。"""

from copy import deepcopy
import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from pydantic import ValidationError

from Agent.data_processing.agent import _parameter_extraction_prompt
from Agent.data_processing.parameters import (
    ParameterContractError, ParameterExtraction, ParameterExtractionError,
    apply_parameter_patch, extraction_patch,
)
from Agent.llm_structured_output import StructuredOutputError, _with_validation_feedback


ANSWER = "次数是计数变量"
JOB = "00000000-0000-0000-0000-000000000952"


def _payload():
    return json.loads(_parameter_extraction_prompt().partial_variables["example"])


def _parse(payload):
    return PydanticToolsParser(tools=[ParameterExtraction], first_tool_only=True).invoke(
        AIMessage(content="", tool_calls=[{
            "name": "ParameterExtraction", "args": payload, "id": "extract-parameters",
        }]),
    )


@pytest.mark.parametrize("index", [0, 1, 2])
def test_missing_outer_evidence_reports_the_actual_update_index(index):
    payload = _payload()
    payload["updates"] = [
        {"field": "time_window", "value": "一天", "evidence": "一天"},
    ] * index + payload["updates"]
    del payload["updates"][index]["evidence"]
    with pytest.raises(ValidationError) as captured:
        _parse(payload)
    error = StructuredOutputError(
        node_name="data_processing_parameter_extraction",
        schema_name="ParameterExtraction", cause=captured.value,
    )
    prompt = _parameter_extraction_prompt()
    inputs = {"parameters": "{}", "question": "变量含义？", "answer": ANSWER}
    original = prompt.format_messages(**inputs)
    corrected = _with_validation_feedback(prompt, error).format_messages(**inputs)
    assert corrected[:-1] == original
    assert f"updates.{index}.evidence" in corrected[-1].content
    assert "missing" in corrected[-1].content
    assert ANSWER not in corrected[-1].content
    assert prompt.format_messages(**inputs) == original


def test_complete_example_passes_parser_business_validation_and_revision_rules():
    extracted = _parse(_payload())
    patch = extraction_patch(extracted, current_parameters={})
    parameters, history = apply_parameter_patch(
        {}, [], patch=patch, job_id=JOB, source_input_id=11,
    )
    assert parameters["measurement_semantics"] == [
        {"column": "次数", "kind": "count", "evidence": ANSWER},
    ]
    assert len(history) == 1
    assert history[0]["source_input_id"] == 11


@pytest.mark.parametrize("layer", ["outer", "inner"])
def test_explanatory_evidence_does_not_require_literal_answer(layer):
    payload = _payload()
    edit = payload["updates"][0]
    (edit if layer == "outer" else edit["value"][0])["evidence"] = "根据用户对次数测量方式的确认，采用计数类型。"
    patch = extraction_patch(_parse(payload), current_parameters={})
    assert patch["measurement_semantics"][0]["evidence"] == (
        "根据用户对次数测量方式的确认，采用计数类型。"
        if layer == "inner" else ANSWER
    )


@pytest.mark.parametrize("evidence", [None, "", "   "])
def test_inner_evidence_remains_required_and_nonempty(evidence):
    payload = _payload()
    declaration = payload["updates"][0]["value"][0]
    if evidence is None:
        del declaration["evidence"]
    else:
        declaration["evidence"] = evidence
    with pytest.raises(ParameterContractError):
        patch = extraction_patch(_parse(payload), current_parameters={})
        apply_parameter_patch({}, [], patch=patch, job_id=JOB, source_input_id=11)


def test_prompt_explains_full_partial_and_modified_proposal_confirmation():
    rendered = _parameter_extraction_prompt().format_messages(
        parameters="{}", question="请选择 A 和 B", answer="A 默认，但 B 改为每周",
    )
    prompt = "\n".join(message.content for message in rendered)
    assert "A 默认，B 默认" in prompt
    assert "用户回复‘A 默认’" in prompt
    assert "B 改为每周" in prompt


def test_parameter_business_validation_rejects_duplicate_fields_with_safe_location():
    extraction = ParameterExtraction.model_validate({"updates": [
        {"field": "target", "value": "收入", "evidence": "接受目标提议"},
        {"field": "target", "value": "利润", "evidence": "修改目标提议"},
    ]})
    with pytest.raises(ParameterExtractionError) as captured:
        extraction_patch(extraction)
    assert captured.value.field_path == "updates.1.field"
    assert captured.value.rule == "同一参数字段只能修改一次"


@pytest.mark.parametrize("kind", ["Continuous", "invalid"])
def test_parameter_business_validation_rejects_invalid_measurement_kind(kind):
    extraction = ParameterExtraction.model_validate({"updates": [{
        "field": "measurement_semantics",
        "value": [{"column": "次数", "kind": kind, "evidence": "合理的测量类型说明"}],
        "evidence": "用户确认次数的测量方式",
    }]})
    with pytest.raises(ParameterExtractionError) as captured:
        extraction_patch(extraction)
    assert captured.value.field_path == "updates.0.value.0.kind"
    assert captured.value.rule == "测量类型必须属于允许的枚举"


def test_feedback_excludes_extra_field_names_and_model_output():
    payload = deepcopy(_payload())
    payload["private-model-field"] = "private-model-value"
    with pytest.raises(ValidationError) as captured:
        _parse(payload)
    error = StructuredOutputError(node_name="extraction", schema_name="ParameterExtraction", cause=captured.value)
    corrected = _with_validation_feedback(_parameter_extraction_prompt(), error)
    feedback = corrected.format_messages(parameters="{}", question="含义？", answer=ANSWER)[-1].content
    assert "extra" in feedback
    assert "private-model-field" not in feedback
    assert "private-model-value" not in feedback


def test_nonvalidation_failure_does_not_invent_a_field_path():
    error = StructuredOutputError(node_name="extraction", schema_name="ParameterExtraction", cause=TimeoutError())
    prompt = _parameter_extraction_prompt()
    assert _with_validation_feedback(prompt, error) is prompt
