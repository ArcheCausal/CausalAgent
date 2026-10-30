"""调用真实纯函数检查效应定义、确认与恢复，不运行外部或模拟链路。"""
from copy import deepcopy

import pytest

from Agent.effect_request import EffectRequest, EffectExecutionContext, normalize_effect_request
from Agent.data_processing.parameters import (
    PARAMETER_BINDING_KEY, PROCESSING_STATE_VERSION, ParameterContractError, ResearchSpec,
    UnsupportedProcessingCheckpointError, commit_research_spec, execution_parameters,
    parameter_binding, parameter_identity, research_parameters, resolve_research_spec,
    restore_parameter_binding, validate_parameter_history,
)
from Agent.data_processing.contracts import (
    ProcessingPlanSummary, ResearchPlanSummary, ResearchPlanConfirmationRequest,
)
from Agent.data_processing.plans import (
    build_processing_plan_version, extend_data_processing_semantic_identity, plan_confirmation_matches,
)
from Agent.data_processing.lifecycle import confirmed_version, update_plan
from app.agent.public_events import public_event_payload


JOB = "00000000-0000-4000-8000-000000000951"
FIXED = {"effect": "average_total_effect",
         "intervention": {"type": "fixed_contrast", "from": 5, "to": 7, "unit": "L/min"}}
SHIFT = {"effect": "average_total_effect",
         "intervention": {"type": "additive_shift", "delta": 1, "unit": "L/min"}}
PARAMETERS = {"target": "产量", "treatment": "流量", "analysis_question": "研究流量与产量的关系",
              "observation_unit": "一次实验", "time_window": None, "aggregation_scope": None,
              "measurement_semantics": [], "effect_request": None}
TRANSFORM = {"type": "identity", "source_column": "flow", "output_column": "flow",
             "source_unit": "L/min", "output_unit": "L/min"}
CONTEXT = {"treatment_column": "flow", "outcome_column": "yield", "treatment_unit": "L/min",
           "treatment_transform": TRANSFORM, "outcome_transform": {"type": "identity"}}
PLAN = {"summary": "保留原始观测", "observation_unit": "一次实验", "transformations": ["保留原始值"],
        "variable_semantics": {"flow": "流量", "yield": "产量"},
        "role_columns": {"target": "yield", "treatment": "flow"},
        "measurement_columns": {}, "limitations": [], "effect_execution_context": CONTEXT}


def commit_request(parameters, history, request, *, source=1):
    return commit_research_spec(parameters, history,
        spec=ResearchSpec.model_validate({**parameters, "effect_request": request}),
        input_columns=["flow", "yield"], job_id=JOB, source_input_id=source,
        expected_revision=len(history), reason="user_change")


@pytest.mark.parametrize("effect", [None, FIXED, SHIFT, {**FIXED, "target_population": "设备 A 在已确认时间内的观测"}])
def test_strict_request_round_trip_and_json_alias(effect):
    assert normalize_effect_request(effect) == effect
    if effect:
        model = EffectRequest.model_validate(effect)
        assert model.model_dump(mode="json", by_alias=True, exclude_none=True) == effect
        if model.intervention.type == "fixed_contrast":
            assert model.intervention.from_value == effect["intervention"]["from"]


@pytest.mark.parametrize("change", [
    {"type": "fixed_contrast"},
    {"type": "fixed_contrast", "from": "5", "to": 7, "unit": "L/min"},
    {"type": "fixed_contrast", "from": True, "to": 7, "unit": "L/min"},
    {"type": "fixed_contrast", "from": float("nan"), "to": 7, "unit": "L/min"},
    {"type": "fixed_contrast", "from": 5, "to": float("inf"), "unit": "L/min"},
    {"type": "fixed_contrast", "from_value": 5, "to": 7, "unit": "L/min"},
    {"type": "additive_shift", "delta": 1, "unit": " "},
    {"type": "additive_shift", "delta": 1, "unit": "L/min", "from": 5},
])
def test_incomplete_or_coerced_intervention_is_rejected(change):
    with pytest.raises(ValueError):
        normalize_effect_request({**FIXED, "intervention": change})


@pytest.mark.parametrize("change", [{"effect": "coefficient"}, {"target_population": " "}, {"internal": "private"}])
def test_unknown_request_semantics_and_extra_fields_are_rejected(change):
    with pytest.raises(ValueError):
        normalize_effect_request({**FIXED, **change})


def test_zero_and_reverse_contrasts_are_not_silently_rewritten():
    for start, end in ((7, 5), (5, 5)):
        request = {**FIXED, "intervention": {**FIXED["intervention"], "from": start, "to": end}}
        assert normalize_effect_request(request) == request


def test_explicit_null_allows_structure_with_existing_roles_and_missing_is_distinct():
    resolved = resolve_research_spec(ResearchSpec.model_validate(PARAMETERS), input_columns=["flow", "yield"])
    assert resolved["effect_request"] is None and resolved["treatment"] == "流量"
    legacy = {key: value for key, value in PARAMETERS.items() if key != "effect_request"}
    assert "effect_request" not in research_parameters(legacy)
    assert parameter_identity(legacy) != parameter_identity(PARAMETERS)
    with pytest.raises(ValueError):
        ResearchSpec.model_validate(legacy)
    with pytest.raises(UnsupportedProcessingCheckpointError):
        validate_parameter_history(legacy, [], job_id=JOB)
    with pytest.raises(UnsupportedProcessingCheckpointError):
        execution_parameters(legacy, PLAN)


@pytest.mark.parametrize("role", ["target", "treatment"])
def test_requested_effect_requires_both_declared_roles(role):
    spec = ResearchSpec.model_validate({**PARAMETERS, role: None, "effect_request": FIXED})
    with pytest.raises(ParameterContractError):
        resolve_research_spec(spec, input_columns=["flow", "yield"])


def test_effect_changes_are_revised_fingerprinted_and_restored():
    first, history = commit_request(PARAMETERS, [], FIXED)
    shifted, history = commit_request(first, history, SHIFT, source=2)
    limited, history = commit_request(shifted, history, {**SHIFT, "target_population": "设备 A"}, source=3)
    cleared, history = commit_request(limited, history, None, source=4)
    assert len(history) == 4
    assert history[0]["changes"]["effect_request"] == {"before": None, "after": FIXED}
    assert history[-1]["changes"]["effect_request"]["after"] is None
    assert len({parameter_identity(value) for value in (PARAMETERS, first, shifted, limited)}) == 4
    binding = parameter_binding(cleared, history, job_id=JOB)
    restored = restore_parameter_binding({PARAMETER_BINDING_KEY: binding}, job_id=JOB)
    assert restored["analysis_parameters"] == cleared
    assert restored["analysis_parameter_changes"] == history
    replay, records = commit_request(cleared, history, None, source=4)
    assert replay == cleared and records == history


@pytest.mark.parametrize("mutation", ["old_version", "missing_field", "tampered_effect"])
def test_legacy_or_conflicting_bindings_do_not_resume(mutation):
    parameters, history = commit_request(PARAMETERS, [], FIXED)
    binding = parameter_binding(parameters, history, job_id=JOB)
    if mutation == "old_version":
        binding["state_version"] = PROCESSING_STATE_VERSION - 1
    elif mutation == "missing_field":
        del binding["parameters"]["effect_request"]
    else:
        binding["parameters"]["effect_request"]["intervention"]["to"] = 9
    with pytest.raises(ParameterContractError):
        restore_parameter_binding({PARAMETER_BINDING_KEY: binding}, job_id=JOB)


def test_definition_change_rejects_old_confirmation_and_changes_plan_version():
    parameters, history = commit_request(PARAMETERS, [], FIXED)
    semantic = extend_data_processing_semantic_identity("b" * 64, {"parameters": parameter_identity(parameters), "revision": 1})
    version = build_processing_plan_version(job_id=JOB, source_hash="a" * 64, semantic_identity=semantic, metadata=PLAN)
    state = {"analysis_parameters": parameters, "analysis_parameter_changes": history,
             "data_processing_research_spec_ready": True, "data_processing_pending_input_id": None,
             "data_processing_plan_parameter_identity": parameter_identity(parameters),
             "data_processing_metadata": PLAN, "data_processing_confirmed_plan": PLAN,
             "data_processing_confirmed_plan_version": version, "data_processing_semantic_identity": semantic}
    assert confirmed_version(state, job_id=JOB, source_hash="a" * 64) == version
    changed, records = commit_request(parameters, history, SHIFT, source=2)
    with pytest.raises(ParameterContractError, match="stale research parameters"):
        confirmed_version({**state, "analysis_parameters": changed, "analysis_parameter_changes": records},
                          job_id=JOB, source_hash="a" * 64)
    new_semantic = extend_data_processing_semantic_identity(semantic, {"parameters": parameter_identity(changed), "revision": 2})
    new_version = build_processing_plan_version(job_id=JOB, source_hash="a" * 64, semantic_identity=new_semantic, metadata=PLAN)
    assert new_version != version
    assert plan_confirmation_matches({"interaction_type": "data_processing_plan_confirmation", "plan_version": new_version},
                                     {"action": "confirm", "plan_version": version}) is False


def test_execution_mapping_preserves_request_and_explicit_identity_context():
    parameters = {**PARAMETERS, "effect_request": FIXED}
    original = deepcopy(parameters)
    projected = execution_parameters(parameters, PLAN)
    assert projected["target"] == "yield" and projected["treatment"] == "flow"
    assert projected["effect_request"] == FIXED
    assert projected["effect_execution_context"] == CONTEXT
    projected["effect_request"]["intervention"]["to"] = 9
    projected["effect_execution_context"]["treatment_transform"]["type"] = "nonlinear"
    assert parameters == original and PLAN["effect_execution_context"] == CONTEXT


def test_affine_units_and_coefficients_survive_without_changing_business_request():
    context = {**CONTEXT, "treatment_column": "flow_z", "treatment_transform": {**TRANSFORM,
        "type": "affine", "output_column": "flow_z", "output_unit": "无量纲", "scale": 0.5, "offset": -3}}
    plan = {**PLAN, "role_columns": {"target": "yield", "treatment": "flow_z"}, "effect_execution_context": context}
    projected = execution_parameters({**PARAMETERS, "effect_request": FIXED}, plan)
    assert projected["effect_execution_context"] == context
    assert projected["effect_request"] == FIXED
    assert update_plan({"data_processing_metadata": PLAN}, plan)["data_processing_confirmed_plan_version"] is None


def test_unknown_transforms_do_not_default_to_identity():
    context = {**CONTEXT, "treatment_transform": None, "outcome_transform": None}
    projected = execution_parameters({**PARAMETERS, "effect_request": FIXED}, {**PLAN, "effect_execution_context": context})
    assert projected["effect_execution_context"] == context
    assert execution_parameters({**PARAMETERS, "effect_request": FIXED}, {})["effect_execution_context"] is None


@pytest.mark.parametrize("change", [{"treatment_column": "wrong"}, {"outcome_column": "wrong"},
    {"treatment_unit": "mL/min"}, {"treatment_transform": {**TRANSFORM, "output_unit": "standardized"}},
    {"treatment_transform": {**TRANSFORM, "type": "affine", "scale": 0, "offset": 0}}])
def test_conflicting_units_roles_and_transforms_are_rejected(change):
    with pytest.raises(ParameterContractError):
        execution_parameters({**PARAMETERS, "effect_request": FIXED}, {**PLAN, "effect_execution_context": {**CONTEXT, **change}})


@pytest.mark.parametrize("effect", [None, FIXED, SHIFT])
def test_confirmation_and_public_event_retain_the_same_definition(effect):
    summary = ResearchPlanSummary.model_validate({**PLAN, "effect_request": effect})
    confirmation = ResearchPlanConfirmationRequest(prompt="请确认研究问题和处理方案。", question_id="q-1",
        expires_at="2026-10-29T14:00:00Z", plan_version="a" * 64, plan=summary)
    payload = confirmation.public_payload()
    public = public_event_payload("interrupt", {"interaction": payload})["interaction"]
    assert public == payload
    assert public["plan"]["effect_request"] == effect
    assert public["plan"]["effect_execution_context"] == CONTEXT
    assert ResearchPlanConfirmationRequest(prompt=public["message"], question_id=public["question_id"],
        expires_at=public["expires_at"], plan_version=public["plan_version"], plan=public["plan"]).public_payload() == public
    with pytest.raises(ValueError):
        ProcessingPlanSummary.model_validate({**PLAN, "effect_request": effect})


def test_invalid_public_definition_suppresses_plan_instead_of_turning_it_into_no_request():
    plan = {**PLAN, "effect_request": {**FIXED, "script": "private"}}
    result = public_event_payload("interrupt", {"interaction": {"plan": plan}})
    assert "plan" not in result["interaction"]
    legacy = public_event_payload("interrupt", {"interaction": {"plan": PLAN}})
    assert "effect_request" not in legacy["interaction"]["plan"]


def test_context_schema_requires_explicit_transform_facts():
    with pytest.raises(ValueError):
        EffectExecutionContext.model_validate({key: value for key, value in CONTEXT.items() if key != "outcome_transform"})
