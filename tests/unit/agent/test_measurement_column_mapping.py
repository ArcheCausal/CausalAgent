"""显式测量列映射的确定性投影规则。"""
import pytest
from Agent.data_processing.parameters import execution_measurement_semantics, ParameterContractError

PARAMETERS = {"measurement_semantics": [
    {"column": "x", "kind": "continuous", "evidence": "x 是连续测量"},
    {"column": "y", "kind": "count", "evidence": "y 是事件次数"}]}


def test_rename_and_explicit_deletion_preserve_only_original_measurements():
    result = execution_measurement_semantics(PARAMETERS,
        {"measurement_columns": {"x": "renamed", "y": None}, "variable_semantics": {"sum": "计算值"}})
    assert result == [{**PARAMETERS["measurement_semantics"][0], "column": "renamed"}]
    assert PARAMETERS["measurement_semantics"][0]["column"] == "x"


@pytest.mark.parametrize("mapping", [{"x": "y"}, {"x": "z", "y": "z"}, {"x": " "}])
def test_mapping_collision_or_invalid_output_is_rejected(mapping):
    with pytest.raises(ParameterContractError):
        execution_measurement_semantics(PARAMETERS, {"measurement_columns": mapping})
