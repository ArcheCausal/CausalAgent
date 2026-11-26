"""研究草稿的真实 schema 和确定性引用解析，不替代模型验收。"""
from copy import deepcopy
import pytest
from Agent.data_processing.parameters import (
    ResearchSpec, ResearchSpecInput, ParameterContractError, resolve_research_spec,
)
from tests.support.research_specs import spec_from_parameters


def declaration(scope="all_input_columns", columns=None, kind="continuous"):
    return {"scope": scope, "columns": columns or [], "kind": kind, "evidence": "全部11列是连续测量值"}


def draft(items):
    value = spec_from_parameters({"effect_request": None}).model_dump()
    value["measurement_semantics"] = items
    return ResearchSpec.model_validate(value)


def test_all_input_columns_resolves_against_complete_frozen_schema():
    columns = [str(i) for i in range(11)]
    result = resolve_research_spec(draft([declaration()]), input_columns=columns)
    assert {item["column"] for item in result["measurement_semantics"]} == set(columns)
    assert len(result["measurement_semantics"]) == 11


@pytest.mark.parametrize("items", [
    [declaration("columns", ["全部11列"])],
    [declaration("columns")],
    [declaration(columns=["x"])],
    [declaration(), declaration("columns", ["x"], kind="count")],
    [{**declaration(), "evidence": " "}],
])
def test_unresolvable_or_conflicting_draft_is_rejected_without_mutation(items):
    before = deepcopy(items)
    with pytest.raises(ParameterContractError):
        resolve_research_spec(draft(items), input_columns=["x", "y"])
    assert items == before


def test_complete_spec_rejects_missing_fields_and_arbitrary_state_writes():
    with pytest.raises(ValueError):
        ResearchSpec.model_validate({"target": "收入"})
    payload = {"spec": draft([]).model_dump(), "expected_revision": 0,
               "source_input_id": 1, "reason": "initial"}
    with pytest.raises(ValueError):
        ResearchSpecInput.model_validate({**payload, "data_processing_confirmed_plan_version": "forged"})
    assert ResearchSpecInput.model_validate(payload).source_input_id == 1
