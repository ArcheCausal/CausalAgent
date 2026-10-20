"""预检测画像、确定性处理和确认选项的纯实现测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from Agent.deep_agent_tools.algorithm_specs import (
    CDFM_SPEC,
    DEFAULT_ALGORITHM_SPECS,
    PC_SPEC,
)
from Agent.deep_agent_tools.interaction import (
    DataProcessingRequest,
    ProcessingOption,
    build_option_id,
    parse_selection,
    resolve_approved_plan,
    validate_option_against_registry,
)
from Agent.deep_agent_tools.models import (
    PreprocessingApproval,
    PreprocessingPlan,
    PreprocessingStep,
)
from Agent.deep_agent_tools.preprocessing import (
    PreprocessingError,
    apply_preprocessing,
    build_data_profile_from_csv,
)
from Agent.deep_agent_tools.registry import build_default_registry


CSV_WITH_ISSUES = "age,city,label\n20,beijing,a\n,shanghai,b\n30,beijing,a\n40,,b\n"


def _empty_adapters() -> dict[str, object]:
    return {
        spec.capability_id: object()
        for spec in DEFAULT_ALGORITHM_SPECS
    }


def test_profile_reports_missing_categorical_and_id_facts() -> None:
    profile = build_data_profile_from_csv(CSV_WITH_ISSUES)

    assert profile.row_count == 4
    assert profile.column_names == ("age", "city", "label")
    assert profile.missing_columns == ("age", "city")
    assert profile.categorical_columns == ("city", "label")
    assert profile.has_missing_values is True
    codes = {issue.code for issue in profile.issues}
    assert "missing_values" in codes
    assert all(issue.blocking for issue in profile.issues if issue.code == "missing_values")


def test_profile_detects_non_finite_and_constant_columns() -> None:
    profile = build_data_profile_from_csv("x,y\n1,5\ninf,5\n2,5\n")

    assert profile.non_finite_columns == ("x",)
    assert profile.constant_columns == ("y",)
    assert {issue.code for issue in profile.issues} >= {
        "non_finite_values",
        "constant_column",
    }


def test_operations_follow_confirmed_order_and_keep_original_csv() -> None:
    original = CSV_WITH_ISSUES
    plan = PreprocessingPlan(
        steps=(
            PreprocessingStep(operation="select_columns", columns=("age", "city")),
            PreprocessingStep(operation="numeric_impute", columns=("age",), method="mean"),
            PreprocessingStep(operation="categorical_encode", columns=("city",), method="ordinal"),
        )
    )

    processed, summary = apply_preprocessing(original, plan)

    assert original == CSV_WITH_ISSUES
    assert summary["operations"] == [
        "select_columns",
        "numeric_impute",
        "categorical_encode",
    ]
    assert summary["imputed_columns"] == ["age"]
    assert summary["encoded_mappings"]["city"] == {"beijing": 0, "shanghai": 1}
    assert "label" not in summary["result_columns"]


def test_drop_rows_must_name_columns_and_never_defaults_to_whole_table() -> None:
    with pytest.raises(ValidationError):
        PreprocessingStep(operation="drop_rows", columns=())


def test_plan_rejects_steps_out_of_order() -> None:
    with pytest.raises(ValidationError):
        PreprocessingPlan(
            steps=(
                PreprocessingStep(operation="categorical_encode", columns=("city",), method="ordinal"),
                PreprocessingStep(operation="drop_rows", columns=("age",)),
            )
        )


def test_numeric_impute_rejects_non_numeric_or_empty_columns() -> None:
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="numeric_impute", columns=("city",), method="mean"),)
    )
    with pytest.raises(PreprocessingError):
        apply_preprocessing(CSV_WITH_ISSUES, plan)

    empty_plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="numeric_impute", columns=("age",), method="mean"),)
    )
    with pytest.raises(PreprocessingError):
        apply_preprocessing("age\n\n", empty_plan)


def test_non_finite_values_are_not_silently_imputed() -> None:
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="numeric_impute", columns=("x",), method="mean"),)
    )
    profile = build_data_profile_from_csv("x,y\n1,1\ninf,2\n3,3\n")
    assert profile.non_finite_columns == ("x",)
    with pytest.raises(PreprocessingError, match="无法得到有限插补值"):
        apply_preprocessing("x,y\n1,1\ninf,2\n3,3\n", plan)


def test_option_identity_is_stable_and_selection_parses_json() -> None:
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),)
    )
    option = ProcessingOption(
        option_id=build_option_id(capability_id="causal.pc", plan_digest=plan.digest),
        label="删除缺失行",
        capability_id="causal.pc",
        plan=plan,
    )
    request = DataProcessingRequest(prompt="请选择处理方式", options=(option,))
    payload = request.public_payload(
        question_id="q-1",
        expires_at=datetime.now(timezone.utc),
    )
    assert payload["type"] == "interaction"
    assert payload["options"][0]["option_id"] == option.option_id
    assert payload["question_id"] == "q-1"
    assert payload["expires_at"].endswith("Z")

    selected, other = parse_selection(
        '{"option_id": "' + option.option_id + '"}',
        options=(option,),
    )
    assert selected is option
    assert other is None

    selected, other = parse_selection("换一种处理方式", options=(option,))
    assert selected is None
    assert other == "换一种处理方式"


def test_option_rejects_operations_the_algorithm_does_not_allow() -> None:
    registry = build_default_registry(_empty_adapters())
    profile = build_data_profile_from_csv("x,y\n1,2\n3,4\n")
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="categorical_encode", columns=("x",), method="ordinal"),)
    )
    option = ProcessingOption(
        option_id=build_option_id(capability_id="causal.cdfm", plan_digest=plan.digest),
        label="编码",
        capability_id="causal.cdfm",
        plan=plan,
    )
    with pytest.raises(ValueError, match="不允许的处理操作"):
        validate_option_against_registry(
            option=option,
            registry=registry,
            profile=profile,
            protected_columns=(),
        )


def test_pc_accepts_discrete_encoding_while_cdfm_rejects_categoricals() -> None:
    profile = build_data_profile_from_csv("x,label\n1,a\n2,b\n3,a\n")
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="categorical_encode", columns=("label",), method="ordinal"),)
    )

    assert PC_SPEC.categorical_allowed() is True
    assert CDFM_SPEC.categorical_allowed() is False
    assert CDFM_SPEC.assess_applicability(profile=profile, preprocessing=plan)[0] == "not_applicable"


def test_target_columns_cannot_be_dropped_by_missing_value_rows() -> None:
    profile = build_data_profile_from_csv(CSV_WITH_ISSUES)
    registry = build_default_registry(_empty_adapters())
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),),
        protected_columns=("age",),
    )
    option = ProcessingOption(
        option_id=build_option_id(capability_id="causal.pc", plan_digest=plan.digest),
        label="删除缺失行",
        capability_id="causal.pc",
        plan=plan,
    )
    with pytest.raises(ValueError, match="目标变量或处理变量"):
        validate_option_against_registry(
            option=option,
            registry=registry,
            profile=profile,
            protected_columns=("age",),
        )


def _approved_plan_state(
    *plans: PreprocessingPlan,
    capability_id: str = "causal.pc",
) -> dict[str, object]:
    approvals: dict[str, object] = {}
    for plan in plans:
        option_id = build_option_id(capability_id=capability_id, plan_digest=plan.digest)
        approvals[option_id] = {
            "approval": PreprocessingApproval(
                option_id=option_id,
                question_id="q-1",
                plan_digest=plan.digest,
            ).model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "capability_id": capability_id,
        }
    return {"data_processing_approvals": approvals}


def test_approved_plan_resolution_matches_exact_digest() -> None:
    older = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),)
    )
    newer = PreprocessingPlan(
        steps=(
            PreprocessingStep(operation="numeric_impute", columns=("age",), method="median"),
        )
    )
    state = _approved_plan_state(older, newer)

    resolved = resolve_approved_plan(
        state=state,
        capability_id="causal.pc",
        plan=newer,
    )

    assert resolved.status == "resolved"
    assert resolved.entry is not None
    assert resolved.entry[1].digest == newer.digest


def test_approved_plan_resolution_requires_explicit_choice_when_ambiguous() -> None:
    older = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),)
    )
    newer = PreprocessingPlan(
        steps=(
            PreprocessingStep(operation="numeric_impute", columns=("age",), method="median"),
        )
    )
    state = _approved_plan_state(older, newer)

    assert (
        resolve_approved_plan(state=state, capability_id="causal.pc", plan=None).status
        == "ambiguous"
    )
    single = _approved_plan_state(newer)
    resolved = resolve_approved_plan(
        state=single,
        capability_id="causal.pc",
        plan=None,
    )
    assert resolved.status == "resolved"
    assert resolved.entry is not None
    assert resolved.entry[1].digest == newer.digest


def test_approved_plan_resolution_ignores_unrelated_capability() -> None:
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),)
    )
    state = _approved_plan_state(plan, capability_id="causal.cdfm")

    assert (
        resolve_approved_plan(state=state, capability_id="causal.pc", plan=plan).status
        == "missing"
    )


def test_approved_plan_rejects_inconsistent_option_identity() -> None:
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),)
    )
    state = _approved_plan_state(plan)
    approvals = state["data_processing_approvals"]
    assert isinstance(approvals, dict)
    entry = next(iter(approvals.values()))
    assert isinstance(entry, dict)
    entry["approval"]["option_id"] = "opt_" + "0" * 24

    assert (
        resolve_approved_plan(state=state, capability_id="causal.pc", plan=plan).status
        == "missing"
    )
