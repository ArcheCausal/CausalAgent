"""ATE 共享检查、结果契约与真实 OLS；不替换算法或外部服务。"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from Agent.CausalAgentMCP.inference.ate import AteNotApplicable, estimate_ate
from Agent.deep_agent_tools.algorithm_executor import (
    AlgorithmExecutorError,
    validate_executor_result,
)
from Agent.deep_agent_tools.algorithm_specs import CAUSAL_BACKDOOR_OLS_SPEC
from Agent.deep_agent_tools.ate_applicability import (
    ATE_REASON_CODES,
    ATE_REASON_SUMMARIES,
    check_ate_graph,
    check_ate_profile,
)
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    AlgorithmResultProvenance,
    AteApplicabilityCheck,
    CausalEffect,
    Diagnostics,
    GraphEdge,
    McpInvocationContext,
    StandardizedGraph,
)
from Agent.deep_agent_tools.preprocessing import build_data_profile


GRAPH_REF = "source-graph:0"


def _graph(*, adjusted: bool = True) -> StandardizedGraph:
    return StandardizedGraph(
        graph_semantics="dag", nodes=["T", "Y", "Z"] if adjusted else ["T", "Y"],
        edges=[GraphEdge(source="T", target="Y")]
        + ([GraphEdge(source="Z", target="T"), GraphEdge(source="Z", target="Y")]
           if adjusted else []),
    )


def _check(graph=None, *, treatment="T", outcome="Y") -> AteApplicabilityCheck:
    return check_ate_graph(
        graph=graph if graph is not None else _graph(), graph_result_ref=GRAPH_REF,
        treatment=treatment, outcome=outcome,
    )


def _frame() -> pd.DataFrame:
    rng = np.random.default_rng(832)
    z = rng.normal(size=300)
    t = 0.8 * z + rng.normal(size=300)
    return pd.DataFrame({"Z": z, "T": t, "Y": 2 * t + 1.5 * z + rng.normal(size=300)})


def _replace_check(check, **fields):
    return AteApplicabilityCheck.model_validate(check.model_dump() | fields)


def _execution():
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    context = McpInvocationContext(
        invocation_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        session_id="00000000-0000-0000-0000-000000000003", user_id=1,
        attempt_count=1, lease_epoch=3, worker_id="worker-test",
        input_snapshot_digest="dataset-hash", issued_at=now,
        source_input_identity="source-hash", analysis_dataset_id=12,
        expires_at=now + timedelta(minutes=5), key_id="test-key",
    )
    command = AlgorithmExecutionCommand(
        invocation_id=context.invocation_id, capability_id=CAUSAL_BACKDOOR_OLS_SPEC.capability_id,
        capability_version=CAUSAL_BACKDOOR_OLS_SPEC.version, spec_digest=CAUSAL_BACKDOOR_OLS_SPEC.spec_digest,
        provider_call_id="call-ate", input_identity=context.input_snapshot_digest,
        parameters={"graph_result_ref": GRAPH_REF, "treatment": "T", "outcome": "Y"},
        artifacts={"standardized_graph": _graph().model_dump(mode="json")},
    )
    return command, context


def _result(*, status="valid", check=None, effect=None, code=None, include_check=True):
    command, context = _execution()
    if status == "valid" and effect is None:
        effect = CausalEffect(
            graph_result_ref=GRAPH_REF,
            **estimate_ate(graph=_graph(), data=_frame(), treatment="T", outcome="Y"),
        )
    return AlgorithmResult(
        result_ref=f"{context.invocation_id}:0", invocation_id=context.invocation_id,
        provider_call_id=command.provider_call_id, capability_id=command.capability_id,
        capability_version=command.capability_version, status=status,
        standardized_graph=_graph() if status == "valid" else None,
        causal_effect=effect, ate_check=(check if check is not None else _check()) if include_check else None,
        diagnostics=Diagnostics(safe_error_code=code),
        provenance=AlgorithmResultProvenance(
            job_id=context.job_id, attempt_count=context.attempt_count,
            lease_epoch=context.lease_epoch, input_identity=command.input_identity,
            source_input_identity=context.source_input_identity,
            analysis_dataset_id=context.analysis_dataset_id,
            spec_digest=command.spec_digest,
        ),
    )


def test_resolves_all_parents_sorted_and_preserves_graph():
    graph = _graph()
    graph.nodes.append("A")
    graph.edges.append(GraphEdge(source="A", target="T"))
    before = graph.model_dump()
    check = _check(graph)
    assert check.adjustment_set == ("A", "Z")
    assert check.execution_columns == ("T", "Y", "A", "Z")
    assert check.applicability == "ready" and check.reason_code is None
    assert graph.model_dump() == before


def test_no_parents_is_resolved_empty_tuple_not_null():
    check = _check(_graph(adjusted=False))
    assert check.adjustment_set == ()
    assert check.execution_columns == ("T", "Y")


@pytest.mark.parametrize("edge_type,reason", [
    ("undirected", "ATE_GRAPH_HAS_UNDIRECTED_EDGE"),
    ("bidirected", "ATE_GRAPH_HAS_BIDIRECTED_EDGE"),
    ("circle_arrow", "ATE_GRAPH_HAS_UNSUPPORTED_EDGE"),
])
def test_edge_rejection_does_not_claim_resolved_adjustment(edge_type, reason):
    graph = _graph()
    graph.edges[0].edge_type = edge_type
    check = _check(graph)
    assert check.applicability == "not_applicable" and check.reason_code == reason
    assert check.adjustment_set is None and check.execution_columns is None
    assert check_ate_profile(check, profile=build_data_profile(_frame()), semantic_kinds={}) == check


@pytest.mark.parametrize("case,reason", [
    ("cycle", "ATE_GRAPH_HAS_DIRECTED_CYCLE"),
    ("missing", "ATE_GRAPH_VARIABLE_MISSING"),
    ("same", "ATE_GRAPH_VARIABLES_IDENTICAL"),
    ("reverse", "ATE_GRAPH_TREATMENT_NOT_ANCESTOR"),
])
def test_graph_rejections(case, reason):
    graph = _graph()
    treatment, outcome = "T", "Y"
    if case == "cycle":
        graph.edges.append(GraphEdge(source="Y", target="Z"))
    elif case == "missing":
        outcome = "absent"
    elif case == "same":
        outcome = "T"
    else:
        treatment, outcome = "Y", "T"
    check = _check(graph, treatment=treatment, outcome=outcome)
    assert check.reason_code == reason
    assert check.adjustment_set is None and check.execution_columns is None


@pytest.mark.parametrize("column", ["T", "Y", "Z"])
@pytest.mark.parametrize("kind", ["category", "ordinal", "binary"])
def test_explicit_semantics_rejects_every_execution_column(column, kind):
    frame = _frame()
    before = frame.copy(deep=True)
    check = check_ate_profile(_check(), profile=build_data_profile(frame), semantic_kinds={column: kind})
    reason = "ATE_ADJUSTMENT_SEMANTICS_UNSUPPORTED" if column == "Z" else "ATE_VARIABLE_SEMANTICS_UNSUPPORTED"
    assert check.reason_code == reason
    assert check.execution_columns == ("T", "Y", "Z") and check.adjustment_set == ("Z",)
    with pytest.raises(AteNotApplicable) as caught:
        estimate_ate(graph=_graph(), data=frame, treatment="T", outcome="Y", semantic_kinds={column: kind})
    assert caught.value.reason_code == reason
    assert str(caught.value) == ATE_REASON_SUMMARIES[reason]
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("column", ["T", "Y", "Z"])
@pytest.mark.parametrize("condition,reason", [
    ("absent", "ATE_EXECUTION_COLUMN_MISSING"),
    ("string_numbers", "ATE_EXECUTION_COLUMN_NOT_NUMERIC"),
    ("missing", "ATE_EXECUTION_COLUMN_MISSING_VALUES"),
    ("empty", "ATE_EXECUTION_COLUMN_MISSING_VALUES"),
    ("constant", "ATE_EXECUTION_COLUMN_CONSTANT"),
    ("infinite", "ATE_EXECUTION_COLUMN_NON_FINITE"),
    ("boolean", "ATE_EXECUTION_COLUMN_NOT_NUMERIC"),
])
def test_profile_and_kernel_share_complete_column_rules(column, condition, reason):
    frame = _frame()
    if condition == "absent":
        frame = frame.drop(columns=[column])
    elif condition == "string_numbers":
        frame[column] = frame[column].astype(str)
    elif condition == "missing":
        frame.loc[0, column] = np.nan
    elif condition == "empty":
        frame[column] = np.nan
    elif condition == "constant":
        frame[column] = 1.0
    elif condition == "infinite":
        frame.loc[0, column] = np.inf
    else:
        frame[column] = frame[column] > 0
    before = frame.copy(deep=True)
    check = check_ate_profile(_check(), profile=build_data_profile(frame), semantic_kinds=None)
    assert check.reason_code == reason
    with pytest.raises(AteNotApplicable) as caught:
        estimate_ate(graph=_graph(), data=frame, treatment="T", outcome="Y")
    assert caught.value.reason_code == reason
    pd.testing.assert_frame_equal(frame, before)


def test_unselected_category_and_missing_column_do_not_block_ols():
    frame = _frame()
    frame["unused"] = [None, "category"] * 150
    semantics = {"T": "continuous", "Y": "continuous", "Z": "continuous", "unused": "category"}
    check = check_ate_profile(_check(), profile=build_data_profile(frame), semantic_kinds=semantics)
    assert check.applicability == "ready"
    effect = estimate_ate(graph=_graph(), data=frame, treatment="T", outcome="Y", semantic_kinds=semantics)
    assert effect["n_rows"] == len(frame)
    assert effect["estimate"] == pytest.approx(2.0, abs=0.15)
    assert effect["adjustment_set"] == ("Z",)


def test_integer_storage_without_semantics_is_not_inferred_as_category():
    frame = _frame()
    frame["Z"] = np.arange(len(frame)) % 3
    check = check_ate_profile(_check(), profile=build_data_profile(frame), semantic_kinds={})
    assert check.applicability == "ready"
    assert estimate_ate(graph=_graph(), data=frame, treatment="T", outcome="Y")["n_rows"] == len(frame)


def test_nonconstant_collinearity_has_stable_reason():
    frame = _frame()
    frame["Z"] = 2 * frame["T"]
    assert check_ate_profile(_check(), profile=build_data_profile(frame), semantic_kinds={}).applicability == "ready"
    with pytest.raises(AteNotApplicable) as caught:
        estimate_ate(graph=_graph(), data=frame, treatment="T", outcome="Y")
    assert caught.value.reason_code == "ATE_DESIGN_MATRIX_SINGULAR"


def test_insufficient_dof_shared_check_and_kernel():
    frame = _frame().iloc[:3]
    check = check_ate_profile(_check(), profile=build_data_profile(frame), semantic_kinds={})
    assert check.reason_code == "ATE_INSUFFICIENT_DOF"
    with pytest.raises(AteNotApplicable) as caught:
        estimate_ate(graph=_graph(), data=frame, treatment="T", outcome="Y")
    assert caught.value.reason_code == check.reason_code


def test_duplicate_columns_rejected_without_mutation():
    frame = pd.concat([_frame(), _frame()[["Z"]]], axis=1)
    with pytest.raises(AteNotApplicable) as caught:
        estimate_ate(graph=_graph(), data=frame, treatment="T", outcome="Y")
    assert caught.value.reason_code == "ATE_EXECUTION_COLUMNS_INVALID"


@pytest.mark.parametrize("fields", [
    {"adjustment_set": None, "execution_columns": None},
    {"execution_columns": ("T", "Y")},
    {"execution_columns": ("Y", "T", "Z")},
    {"adjustment_set": ("Z", "Z"), "execution_columns": ("T", "Y", "Z", "Z")},
    {"adjustment_set": ("T",), "execution_columns": ("T", "Y", "T")},
    {"reason_code": "ATE_INSUFFICIENT_DOF"},
    {"applicability": "not_applicable", "reason_code": None},
    {"applicability": "not_applicable", "reason_code": "arbitrary exception message"},
    {"applicability": "not_applicable", "reason_code": "ATE_GRAPH_HAS_UNDIRECTED_EDGE"},
])
def test_check_rejects_inconsistent_fields(fields):
    with pytest.raises(ValidationError):
        _replace_check(_check(), **fields)


def test_reason_whitelist_and_unknown_exception_rejected():
    assert ATE_REASON_CODES == frozenset(ATE_REASON_SUMMARIES)
    with pytest.raises(ValueError, match="unknown ATE reason_code"):
        AteNotApplicable("arbitrary exception message")


@pytest.mark.parametrize("status,code", [
    ("execution_failed", SafeErrorCode.ALGORITHM_EXECUTION_FAILED),
    ("timed_out", SafeErrorCode.ALGORITHM_TIMED_OUT),
])
def test_ready_record_survives_postcheck_failure_and_timeout(status, code):
    result = _result(status=status, code=code)
    command, context = _execution()
    parsed = validate_executor_result(result, command=command, trusted_context=context)
    assert parsed.ate_check.applicability == "ready"
    assert parsed.standardized_graph is None and parsed.raw_result_ref is None
    assert AlgorithmResult.model_validate_json(result.model_dump_json()) == result


def test_not_applicable_record_and_safe_code_roundtrip():
    check = _replace_check(_check(), applicability="not_applicable", reason_code="ATE_ADJUSTMENT_SEMANTICS_UNSUPPORTED")
    result = _result(status="not_applicable", check=check, code=SafeErrorCode.ALGORITHM_NOT_APPLICABLE)
    command, context = _execution()
    assert validate_executor_result(result, command=command, trusted_context=context) == result


@pytest.mark.parametrize("status,code", [
    ("skipped", SafeErrorCode.ALGORITHM_NOT_APPLICABLE),
    ("not_ready", SafeErrorCode.ALGORITHM_NOT_READY),
    ("invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID),
    ("not_applicable", SafeErrorCode.ALGORITHM_NOT_APPLICABLE),
])
def test_ready_record_cannot_claim_unchecked_terminal(status, code):
    with pytest.raises(ValidationError):
        _result(status=status, code=code)


def test_not_applicable_record_cannot_use_execution_failure_status_or_code():
    check = _replace_check(_check(), applicability="not_applicable", reason_code="ATE_DESIGN_MATRIX_SINGULAR")
    for status, code in [
        ("execution_failed", SafeErrorCode.ALGORITHM_EXECUTION_FAILED),
        ("not_applicable", SafeErrorCode.ALGORITHM_EXECUTION_FAILED),
    ]:
        with pytest.raises(ValidationError):
            _result(status=status, code=code, check=check)


@pytest.mark.parametrize("status", ["execution_failed", "timed_out"])
@pytest.mark.parametrize("code", [
    SafeErrorCode.ALGORITHM_CANCELED,
    SafeErrorCode.ALGORITHM_NOT_READY,
    SafeErrorCode.ALGORITHM_NOT_APPLICABLE,
])
def test_ready_record_rejects_cancellation_or_precheck_rejection(status, code):
    with pytest.raises(ValidationError, match="ready ate_check cannot claim"):
        _result(status=status, code=code)


@pytest.mark.parametrize("field,value", [
    ("graph_result_ref", "other-graph:0"), ("treatment", "other-T"),
    ("outcome", "other-Y"), ("adjustment_set", ()),
])
def test_effect_must_match_check(field, value):
    payload = _result().model_dump()
    payload["causal_effect"][field] = value
    with pytest.raises(ValidationError, match="causal_effect does not match"):
        AlgorithmResult.model_validate(payload)


def test_success_requires_effect_when_record_present():
    payload = _result().model_dump()
    payload["causal_effect"] = None
    with pytest.raises(ValidationError, match="requires causal_effect"):
        AlgorithmResult.model_validate(payload)


def test_old_results_remain_readable_but_new_success_requires_record():
    result = _result(include_check=False)
    assert AlgorithmResult.model_validate_json(result.model_dump_json()).ate_check is None
    command, context = _execution()
    with pytest.raises(AlgorithmExecutorError) as caught:
        validate_executor_result(result, command=command, trusted_context=context)
    assert caught.value.safe_error_code == SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID


def test_failure_without_check_stays_unchecked():
    result = _result(status="execution_failed", code=SafeErrorCode.ALGORITHM_EXECUTION_FAILED, include_check=False)
    command, context = _execution()
    assert validate_executor_result(result, command=command, trusted_context=context).ate_check is None


@pytest.mark.parametrize("field,value", [
    ("graph_result_ref", "stale-graph:0"), ("treatment", "Z"), ("outcome", "Z"),
])
def test_executor_rejects_check_for_different_command(field, value):
    command, context = _execution()
    command.parameters[field] = value
    with pytest.raises(AlgorithmExecutorError):
        validate_executor_result(_result(), command=command, trusted_context=context)


def test_executor_rejects_omitted_adjustment_even_when_effect_agrees():
    check = _replace_check(_check(), adjustment_set=(), execution_columns=("T", "Y"))
    payload = _result().model_dump()
    payload["ate_check"] = check.model_dump()
    payload["causal_effect"]["adjustment_set"] = ()
    result = AlgorithmResult.model_validate(payload)
    command, context = _execution()
    with pytest.raises(AlgorithmExecutorError):
        validate_executor_result(result, command=command, trusted_context=context)


def test_executor_validates_graph_rejection_against_actual_graph():
    graph = _graph()
    graph.edges[0].edge_type = "undirected"
    result = _result(status="not_applicable", check=_check(graph), code=SafeErrorCode.ALGORITHM_NOT_APPLICABLE)
    command, context = _execution()
    with pytest.raises(AlgorithmExecutorError):
        validate_executor_result(result, command=command, trusted_context=context)
    command.artifacts["standardized_graph"] = graph.model_dump()
    assert validate_executor_result(result, command=command, trusted_context=context) == result


@pytest.mark.parametrize("field,value", [
    ("source_input_identity", "other-source"),
    ("source_input_identity", None),
    ("analysis_dataset_id", 13),
    ("analysis_dataset_id", None),
])
def test_executor_checks_source_and_dataset_identity(field, value):
    payload = _result().model_dump()
    payload["provenance"][field] = value
    result = AlgorithmResult.model_validate(payload)
    command, context = _execution()
    with pytest.raises(AlgorithmExecutorError) as caught:
        validate_executor_result(result, command=command, trusted_context=context)
    assert caught.value.safe_error_code == SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID


def test_executor_success_requires_exact_command_graph():
    payload = _result().model_dump()
    # 调整集不变，但成功结果少了一条源图中的边，仍须拒绝。
    payload["standardized_graph"]["edges"] = payload["standardized_graph"]["edges"][:2]
    result = AlgorithmResult.model_validate(payload)
    command, context = _execution()
    with pytest.raises(AlgorithmExecutorError):
        validate_executor_result(result, command=command, trusted_context=context)


def test_executor_revalidates_unchecked_model_copy():
    result = _result().model_copy(update={"ate_check": _check().model_copy(update={"execution_columns": ()})})
    command, context = _execution()
    with pytest.raises(AlgorithmExecutorError):
        validate_executor_result(result, command=command, trusted_context=context)


def test_shared_module_import_has_no_runtime_dependencies():
    completed = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import Agent.deep_agent_tools.ate_applicability; "
            "assert not any(name.split('.')[0] in "
            "{'langchain', 'langchain_core', 'langgraph', 'Database', 'sqlalchemy', 'app'} "
            "for name in sys.modules)"
        )], capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_contract_version_and_public_schema():
    assert CAUSAL_BACKDOOR_OLS_SPEC.version == "2.0"
    schema = AlgorithmResult.model_json_schema()
    assert "ate_check" in schema["properties"]
    assert "AteApplicabilityCheck" in schema["$defs"]
    assert "ate_check" not in CAUSAL_BACKDOOR_OLS_SPEC.model_input_schema.model_fields
