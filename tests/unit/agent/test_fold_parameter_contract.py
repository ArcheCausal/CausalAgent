"""初始研究参数直接使用真实 Pydantic schema 校验，不调用替代模型。"""

import pytest
from pydantic import ValidationError

from Agent.causal_agent.nodes import foldQuery


def declaration(kind, *, column="x", evidence="x是连续测量变量"):
    return {"column": column, "kind": kind, "evidence": evidence}


def test_fold_rejects_conflicting_measurement_kinds_before_parameter_handoff():
    with pytest.raises(ValidationError, match="conflicting measurement declarations"):
        foldQuery.model_validate({"measurement_semantics": [declaration("continuous"), declaration("count")]})


def test_fold_accepts_consistent_repeated_declarations():
    result = foldQuery.model_validate({"measurement_semantics": [declaration("continuous"), declaration("continuous")]})
    assert result.measurement_semantics[0].kind == "continuous"


@pytest.mark.parametrize("column,evidence", [(" x", "x是连续测量变量"), ("x", " ")])
def test_fold_rejects_unusable_column_or_evidence(column, evidence):
    with pytest.raises(ValidationError):
        foldQuery.model_validate({"measurement_semantics": [declaration("continuous", column=column, evidence=evidence)]})
