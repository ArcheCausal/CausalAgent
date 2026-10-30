"""已知生成过程的数据直接运行正式内核/runner，不代替数据库和模型验收。"""

from copy import deepcopy
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from Agent.CausalAgentMCP.inference.ate import estimate_ate
from Agent.CausalAgentMCP.inference.g_formula import estimate_g_formula
from Agent.CausalAgentMCP.inference.interventions import (
    InterventionNotApplicable, prepare_intervention, sample_identity,
)
from Agent.CausalAgentMCP.runner_registry import build_default_registry
from Agent.CausalAgentMCP.executor_pool import BoundedProcessPool
from Agent.CausalAgentMCP.service import _estimation_result, effect_parameters_from_snapshot
from Agent.data_processing.parameters import parameter_binding, parameter_identity
from Agent.data_processing.plans import build_processing_plan_version
from Agent.deep_agent_tools.algorithm_executor import (
    AlgorithmExecutorError, validate_executor_result,
)
from Agent.deep_agent_tools.algorithm_specs import (
    CAUSAL_BACKDOOR_OLS_SPEC, CAUSAL_G_FORMULA_SPEC,
    CausalBackdoorOlsInput, CausalGFormulaInput,
)
from Agent.deep_agent_tools.ate_applicability import check_ate_graph
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand, CausalEffect, DataViewSummary, McpInvocationContext,
    StandardizedGraph,
)


@pytest.fixture
def data():
    rng = np.random.default_rng(1847)
    z = rng.normal(size=280)
    x = 8 + 0.4 * z + rng.uniform(-3, 3, len(z))
    return pd.DataFrame({"x": x, "y": 2.5 * x + 1.7 * z + rng.normal(0, 0.5, len(z)), "z": z})


@pytest.fixture
def graph():
    return StandardizedGraph(graph_semantics="pdag", nodes=["x", "y", "z"], edges=[
        {"source": "z", "target": "x"}, {"source": "z", "target": "y"},
        {"source": "x", "target": "y"},
    ])


def request(start=6, stop=9):
    return {"effect": "average_total_effect", "intervention": {
        "type": "fixed_contrast", "from": start, "to": stop, "unit": "L/min",
    }}


def context():
    return {
        "treatment_column": "x", "outcome_column": "y", "treatment_unit": "L/min",
        "treatment_transform": {"type": "identity", "source_column": "x",
            "output_column": "x", "source_unit": "L/min", "output_unit": "L/min"},
        "outcome_transform": {"type": "identity"},
    }


def compute(function, data, graph, effect_request=None, execution_context=None, **kwargs):
    return function(graph=graph, data=data, treatment="x", outcome="y",
        effect_request=request() if effect_request is None else effect_request,
        execution_context=context() if execution_context is None else execution_context, **kwargs)


def test_ols_scales_effect_se_and_reverse_interval(data, graph):
    unit = compute(estimate_ate, data, graph, request(6, 7))
    triple = compute(estimate_ate, data, graph)
    reverse = compute(estimate_ate, data, graph, request(9, 6))
    assert triple["estimate"] == pytest.approx(3 * unit["estimate"])
    assert triple["standard_error"] == pytest.approx(3 * unit["standard_error"])
    assert triple["ci95"] == pytest.approx(np.array(unit["ci95"]) * 3)
    assert reverse["estimate"] == pytest.approx(-triple["estimate"])
    assert reverse["standard_error"] == pytest.approx(triple["standard_error"])
    assert reverse["ci95"] == pytest.approx([-triple["ci95"][1], -triple["ci95"][0]])


def test_linear_methods_share_request_sample_and_adjustment(data, graph):
    ols = compute(estimate_ate, data, graph)
    gf = compute(estimate_g_formula, data, graph, bootstrap=16)
    assert gf["estimate"] == pytest.approx(ols["estimate"], abs=1e-10)
    for name in ("effect_request", "sample_identity", "adjustment_set", "adjustment_rule"):
        assert gf[name] == ols[name]
    metrics = gf["diagnostics_metrics"]
    assert metrics["difference_ci95_low"] <= metrics["difference_ci95_high"]
    assert abs(metrics["effect_difference"]) < 1e-10
    assert gf["support"]["status"] == "supported"
    assert gf["support"]["conditional_overlap"] == "not_assessed"
    assert gf["sample_identity"] == sample_identity(data[["x", "y", "z"]])
    CausalEffect.model_validate({"graph_result_ref": "graph:source",
        **{k: v for k, v in gf.items() if k != "diagnostics_metrics"}})


def test_hgb_predicts_requested_nonlinear_contrast_and_paired_difference(data, graph):
    nonlinear = data.copy()
    nonlinear["y"] = 0.9 * nonlinear.x ** 2 + 1.7 * nonlinear.z
    forward = compute(estimate_g_formula, nonlinear, graph, request(6, 8), outcome_model="hgb", bootstrap=10)
    reverse = compute(estimate_g_formula, nonlinear, graph, request(8, 6), outcome_model="hgb", bootstrap=10)
    assert forward["estimate"] > 0
    assert reverse["estimate"] == pytest.approx(-forward["estimate"])
    assert reverse["ci95"] == pytest.approx([-forward["ci95"][1], -forward["ci95"][0]])
    metrics = forward["diagnostics_metrics"]
    assert metrics["paired_bootstrap_n"] == 10
    assert metrics["difference_ci95_low"] <= metrics["difference_ci95_high"]


@pytest.mark.parametrize("function", [estimate_ate, estimate_g_formula])
def test_fixed_zero_one_outside_original_support(function, data, graph):
    original = data.copy(deep=True)
    with pytest.raises(InterventionNotApplicable) as caught:
        compute(function, data, graph, request(0, 1))
    support = caught.value.support
    assert caught.value.reason_code == "EFFECT_OUTSIDE_SUPPORT"
    assert support["out_of_range_count"] == len(data)
    assert support["out_of_range_fraction"] == 1
    assert support["status"] == "not_applicable"
    pd.testing.assert_frame_equal(data, original)


@pytest.mark.parametrize("delta", [-1, 1])
@pytest.mark.parametrize("function", [estimate_ate, estimate_g_formula])
def test_shift_refuses_boundary_extrapolation(function, delta, data, graph):
    shifted = {"effect": "average_total_effect", "intervention": {
        "type": "additive_shift", "delta": delta, "unit": "L/min"}}
    with pytest.raises(InterventionNotApplicable) as caught:
        compute(function, data, graph, shifted)
    expected = ((data.x + delta < data.x.min()) | (data.x + delta > data.x.max())).sum()
    support = caught.value.support
    assert support["out_of_range_count"] == expected
    assert support["out_of_range_fraction"] == expected / len(data)
    assert support["dropped_row_count"] == support["clipped_value_count"] == 0


@pytest.mark.parametrize("function", [estimate_ate, estimate_g_formula])
def test_affine_conversion_preserves_original_unit_effect(function, data, graph):
    reference = compute(function, data, graph, **({"bootstrap": 0} if function is estimate_g_formula else {}))
    transformed = data.copy()
    transformed.x = 0.5 * transformed.x - 4
    ctx = context()
    ctx["treatment_transform"].update(type="affine", scale=0.5, offset=-4, output_unit="scaled")
    result = compute(function, transformed, graph, execution_context=ctx,
        **({"bootstrap": 0} if function is estimate_g_formula else {}))
    assert result["estimate"] == pytest.approx(reference["estimate"])
    assert result["support"]["observed_min"] == pytest.approx(data.x.min())
    assert result["effect_request"] == request()


@pytest.mark.parametrize("change", ["unit", "column", "log", "missing_outcome", "unknown_outcome"])
def test_rejects_unknown_or_inconsistent_transformation(change, data, graph):
    ctx = context()
    if change == "unit":
        ctx["treatment_unit"] = "m3/s"
    elif change == "column":
        ctx["treatment_column"] = "wrong"
    elif change == "log":
        ctx["treatment_transform"]["type"] = "nonlinear"
    elif change == "missing_outcome":
        ctx.pop("outcome_transform")
    else:
        ctx["outcome_transform"] = None
    with pytest.raises(InterventionNotApplicable):
        compute(estimate_ate, data, graph, execution_context=ctx)


def test_no_request_cannot_execute(data, graph):
    with pytest.raises(InterventionNotApplicable, match="没有已确认"):
        estimate_ate(graph=graph, data=data, treatment="x", outcome="y", effect_request=None, execution_context=context())


@pytest.mark.parametrize("function", [estimate_ate, estimate_g_formula])
def test_unverified_target_population_cannot_be_replaced_by_all_rows(function, data, graph):
    definition = {**request(), "target_population": "age above 65"}
    with pytest.raises(InterventionNotApplicable) as caught:
        compute(function, data, graph, definition)
    assert caught.value.reason_code == "EFFECT_POPULATION_UNSUPPORTED"
    assert caught.value.support["status"] == "not_applicable"
    assert caught.value.support["population_verified"] is False
    assert caught.value.support["target_population"] == definition["target_population"]


@pytest.mark.parametrize("schema", [CausalBackdoorOlsInput, CausalGFormulaInput])
def test_tool_requires_confirmed_object_and_rejects_legacy_contrast(schema):
    params = {"graph_result_ref": "graph:source", "treatment": "x", "outcome": "y"}
    with pytest.raises(ValidationError):
        schema.model_validate(params)
    with pytest.raises(ValidationError):
        schema.model_validate({**params, "effect_request": request(), "contrast": [0, 1]})
    assert schema.model_validate({**params, "effect_request": request()}).effect_request == request()


def test_sample_identity_detects_row_order_content_and_sample_changes(data):
    original = sample_identity(data)
    assert original != sample_identity(data.iloc[::-1])
    assert original != sample_identity(data.iloc[:-1])
    changed = data.copy()
    changed.loc[0, "y"] += 0.01
    assert original != sample_identity(changed)


@pytest.mark.parametrize("spec", [CAUSAL_BACKDOOR_OLS_SPEC, CAUSAL_G_FORMULA_SPEC])
def test_actual_runner_and_service_normalization_preserve_query_provenance(spec, data, graph):
    params = {"graph_result_ref": "graph:source", "treatment": "x", "outcome": "y", "effect_request": request()}
    if spec == CAUSAL_G_FORMULA_SPEC:
        params.update(outcome_model="linear", bootstrap=6)
    artifacts = {"standardized_graph": graph.model_dump(mode="json"), "effect_execution_context": context()}
    entry = build_default_registry().resolve(spec.capability_id, spec.version, spec.spec_digest)
    raw = entry.runner(data.to_csv(index=False), params, artifacts)
    assert raw["success"] is True
    now = datetime.now(timezone.utc)
    ctx = McpInvocationContext(invocation_id="00000000-0000-0000-0000-000000000801",
        job_id="00000000-0000-0000-0000-000000000802", session_id="00000000-0000-0000-0000-000000000803",
        user_id=7, attempt_count=1, lease_epoch=1, worker_id="real-computation-test",
        input_snapshot_digest="computed-input", research_identity="confirmed-research",
        issued_at=now, expires_at=now + timedelta(minutes=5), key_id="current")
    command = AlgorithmExecutionCommand(invocation_id=ctx.invocation_id, capability_id=spec.capability_id,
        capability_version=spec.version, spec_digest=spec.spec_digest, provider_call_id="calculation",
        input_identity=ctx.input_snapshot_digest, research_identity=ctx.research_identity, parameters=params, artifacts=artifacts)
    check = check_ate_graph(graph=graph, graph_result_ref=params["graph_result_ref"], treatment="x", outcome="y")
    result = _estimation_result(raw, command=command, context=ctx, capability_version=spec.version, graph=graph,
        data_view=DataViewSummary(row_count=len(data), column_count=3, columns=tuple(data.columns)), ate_check=check)
    assert validate_executor_result(result, command=command, trusted_context=ctx) == result
    assert result.provenance.research_identity == ctx.research_identity
    tampered = result.model_copy(deep=True)
    tampered.causal_effect.effect_request = request(7, 8)
    with pytest.raises(AlgorithmExecutorError):
        validate_executor_result(tampered, command=command, trusted_context=ctx)


def test_confirmation_snapshot_reconstructs_definition_and_detects_edits():
    job_id = "00000000-0000-0000-0000-000000000802"
    parameters = {"target": "y", "treatment": "x", "analysis_question": "flow effect",
        "observation_unit": "record", "time_window": None, "aggregation_scope": None,
        "measurement_semantics": [], "effect_request": request()}
    plan = {"summary": "original units retained", "role_columns": {"treatment": "x", "target": "y"},
        "effect_execution_context": context()}
    source_hash, semantic = "a" * 64, "b" * 64
    version = build_processing_plan_version(job_id=job_id, source_hash=source_hash, semantic_identity=semantic, metadata=plan)
    metadata = {**plan, "analysis_parameter_identity": parameter_binding(parameters, [], job_id=job_id),
        "processing_plan_identity": {"plan_version": version, "semantic_identity": semantic}}
    resolved = effect_parameters_from_snapshot(metadata, job_id=job_id, source_hash=source_hash)
    assert resolved["effect_request"] == request()
    assert resolved["effect_execution_context"] == context()
    assert resolved["research_identity"] == parameter_identity(parameters)
    changed = deepcopy(metadata)
    changed["effect_execution_context"]["treatment_unit"] = "m3/s"
    with pytest.raises(ValueError):
        effect_parameters_from_snapshot(changed, job_id=job_id, source_hash=source_hash)


def test_real_process_pool_runs_both_estimators_and_preserves_rejections(data, graph):
    async def run():
        pool = BoundedProcessPool(process_workers=1)
        await pool.start()
        results = []
        try:
            for spec in (CAUSAL_BACKDOOR_OLS_SPEC, CAUSAL_G_FORMULA_SPEC):
                entry = build_default_registry().resolve(spec.capability_id, spec.version, spec.spec_digest)
                for definition in (request(), request(0, 1)):
                    params = {"graph_result_ref": "graph:source", "treatment": "x",
                        "outcome": "y", "effect_request": definition}
                    if spec == CAUSAL_G_FORMULA_SPEC:
                        params.update(outcome_model="linear", bootstrap=4)
                    value = await pool.execute(capability_id=entry.capability_id, runner=entry.runner,
                        csv_data=data.to_csv(index=False), parameters=params,
                        artifacts={"standardized_graph": graph.model_dump(mode="json"),
                            "effect_execution_context": context()}, timeout_seconds=30, concurrency=1)
                    results.append(value.value)
        finally:
            await pool.close()
        return results
    values = asyncio.run(run())
    assert [value["success"] for value in values] == [True, False, True, False]
    assert values[0]["causal_effect"]["estimate"] == pytest.approx(values[2]["causal_effect"]["estimate"])
    for index in (1, 3):
        assert values[index]["support"]["status"] == "not_applicable"
        assert values[index]["support"]["out_of_range_count"] == len(data)
