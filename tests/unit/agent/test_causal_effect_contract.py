"""因果效应结果的契约校验。"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultProvenance,
    CausalEffect,
    Diagnostics,
    StandardizedGraph,
)


def _effect(**overrides) -> CausalEffect:
    data = dict(
        estimand="ATE",
        graph_result_ref="inv-1:0",
        treatment="x",
        outcome="y",
        adjustment_set=("z",),
        estimate=2.0,
        standard_error=0.1,
        ci95=(1.8, 2.2),
        estimator="backdoor_ols",
        n_rows=500,
    )
    data.update(overrides)
    return CausalEffect(**data)


def _result(**overrides) -> AlgorithmResult:
    data = dict(
        result_ref="inv-1:0",
        invocation_id="inv-1",
        provider_call_id="call-1",
        capability_id="causal.backdoor_ols",
        capability_version="1.0",
        status="valid",
        standardized_graph=StandardizedGraph(graph_semantics="pdag", nodes=["x", "y"]),
        diagnostics=Diagnostics(),
        provenance=AlgorithmResultProvenance(
            job_id="job-1",
            attempt_count=0,
            lease_epoch=1,
            input_identity="sha",
            spec_digest="a" * 64,
        ),
    )
    data.update(overrides)
    return AlgorithmResult(**data)


def test_causal_effect_round_trips() -> None:
    effect = _effect()
    assert effect.adjustment_set == ("z",)
    assert effect.ci95 == (1.8, 2.2)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_causal_effect_rejects_non_finite_estimate(value: float) -> None:
    with pytest.raises(ValidationError):
        _effect(estimate=value)


def test_causal_effect_rejects_inverted_ci() -> None:
    with pytest.raises(ValidationError):
        _effect(ci95=(2.2, 1.8))


def test_causal_effect_carries_estimation_controls() -> None:
    effect = _effect(contrast=(0.0, 1.0), outcome_model="hgb", bootstrap_n=200)
    assert effect.contrast == (0.0, 1.0)
    assert effect.outcome_model == "hgb"
    assert effect.bootstrap_n == 200


def test_causal_effect_controls_default_to_none() -> None:
    effect = _effect()
    assert effect.contrast is None
    assert effect.outcome_model is None
    assert effect.bootstrap_n is None


@pytest.mark.parametrize("value", [(math.nan, 1.0), (0.0, math.inf)])
def test_causal_effect_rejects_non_finite_contrast(value) -> None:
    with pytest.raises(ValidationError):
        _effect(contrast=value)


def test_causal_effect_rejects_negative_bootstrap() -> None:
    with pytest.raises(ValidationError):
        _effect(bootstrap_n=-1)


def test_result_rejects_effect_without_graph() -> None:
    # 用非 valid 状态，确保拒绝来自新增的交叉校验而不是 valid 必须带图的旧规则。
    with pytest.raises(ValidationError, match="causal_effect"):
        _result(
            status="execution_failed",
            standardized_graph=None,
            diagnostics=Diagnostics(
                safe_error_code=SafeErrorCode.ALGORITHM_EXECUTION_FAILED
            ),
            causal_effect=_effect(),
        )


def test_result_accepts_effect_with_graph() -> None:
    assert _result(causal_effect=_effect()).causal_effect is not None


def test_command_artifacts_default_to_empty_and_are_dumped() -> None:
    command = AlgorithmExecutionCommand(
        invocation_id="00000000-0000-0000-0000-000000000001",
        capability_id="causal.backdoor_ols",
        capability_version="1.0",
        spec_digest="a" * 64,
        provider_call_id="call-1",
        input_identity="sha",
    )
    assert command.artifacts == {}
    forwarded = command.model_copy(
        update={"artifacts": {"standardized_graph": {"nodes": []}}}
    )
    assert forwarded.model_dump(mode="json")["artifacts"] == {
        "standardized_graph": {"nodes": []}
    }
