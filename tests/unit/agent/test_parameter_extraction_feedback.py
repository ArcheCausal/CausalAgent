"""真实 Pydantic、LangChain parser 与提示词检查；不使用模型或数据库替身。"""

from copy import deepcopy
import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from pydantic import ValidationError

from Agent.data_processing.agent import _parameter_extraction_prompt
from Agent.data_processing.parameters import (
    ParameterContractError, ParameterExtraction, apply_parameter_patch, extraction_patch,
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


def test_complete_example_passes_parser_source_validation_and_revision_rules():
    extracted = _parse(_payload())
    patch = extraction_patch(extracted, answer_text=ANSWER, current_parameters={})
    parameters, history = apply_parameter_patch(
        {}, [], patch=patch, job_id=JOB, source_input_id=11,
    )
    assert parameters["measurement_semantics"] == [
        {"column": "次数", "kind": "count", "evidence": ANSWER},
    ]
    assert len(history) == 1
    assert history[0]["source_input_id"] == 11


@pytest.mark.parametrize("layer", ["outer", "inner"])
def test_correct_structure_still_rejects_ungrounded_evidence(layer):
    payload = _payload()
    edit = payload["updates"][0]
    (edit if layer == "outer" else edit["value"][0])["evidence"] = "用户未说过的内容"
    with pytest.raises(ParameterContractError):
        extraction_patch(_parse(payload), answer_text=ANSWER, current_parameters={})


@pytest.mark.parametrize("evidence", [None, "", "   "])
def test_inner_evidence_remains_required_and_nonempty(evidence):
    payload = _payload()
    declaration = payload["updates"][0]["value"][0]
    if evidence is None:
        del declaration["evidence"]
    else:
        declaration["evidence"] = evidence
    with pytest.raises(ParameterContractError):
        patch = extraction_patch(_parse(payload), answer_text=ANSWER, current_parameters={})
        apply_parameter_patch({}, [], patch=patch, job_id=JOB, source_input_id=11)


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
