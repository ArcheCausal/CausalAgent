"""研究声明已统一交由数据处理参数契约处理。"""
import pytest
from Agent.data_processing.parameters import ParameterContractError, resolve_research_spec
from tests.support.research_specs import spec_from_parameters


def test_empty_spec_is_explicitly_valid_without_inventing_roles():
    result = resolve_research_spec(spec_from_parameters({}), input_columns=["x"])
    assert result["target"] is None and result["treatment"] is None
    assert result["measurement_semantics"] == []


def test_empty_frozen_structure_cannot_publish_a_specification():
    with pytest.raises(ParameterContractError):
        resolve_research_spec(spec_from_parameters({}), input_columns=[])
