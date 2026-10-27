"""直接测试研究修订和候选身份纯函数，不替代模型或数据库验收。"""

from copy import deepcopy

import pytest

from Agent.data_processing.parameters import (
    PARAMETER_BINDING_KEY, ParameterContractError, ParameterExtraction,
    apply_parameter_patch, extraction_patch, parameter_binding, parameter_identity,
    restore_parameter_binding, sandbox_request_id, validate_parameter_history,
)
from Agent.data_processing.persistence import DatasetPersistenceError, require_candidate_parameters
from Agent.causal_agent.analysis_context import rebuild_context_index
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent_tools.identity import build_data_processing_run_id
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC, DIRECT_LINGAM_SPEC
from Database.analysis_contexts import context_has_results, final_context_action


JOB = "00000000-0000-0000-0000-000000000951"
PARAMETERS = {"target": "outcome", "treatment": "exposure",
              "analysis_question": "研究暴露与结果之间的关系", "measurement_semantics": []}


def revise(parameters=PARAMETERS, history=None, *, patch=None, source=11):
    return apply_parameter_patch(
        parameters, history or [], patch=patch or {}, job_id=JOB, source_input_id=source,
    )


def test_successive_revisions_preserve_absent_fields_and_clear_explicit_null():
    first, history = revise(patch={"target": None, "observation_unit": "设备-日期"})
    final, history = revise(first, history, patch={"time_window": "一天"}, source=12)
    assert final["target"] is None
    assert final["treatment"] == PARAMETERS["treatment"]
    assert final["observation_unit"] == "设备-日期"
    assert [row["revision"] for row in history] == [1, 2]
    assert [row["source_input_id"] for row in history] == [11, 12]
    assert history[0]["changes"]["target"] == {"before": "outcome", "after": None}
    assert PARAMETERS["target"] == "outcome"


def test_no_change_does_not_advance_revision_and_derived_profile_does_not_change_identity():
    final, history = revise(patch={"target": "outcome"})
    assert history == []
    assert parameter_identity(final) == parameter_identity({**final, "data_profile": {"rows": 20}})


def test_same_input_replay_is_idempotent_even_after_later_changes():
    first, history = revise(patch={"target": "new_outcome"})
    second, history = revise(first, history, patch={"target": "latest_outcome"}, source=12)
    replay, records = revise(second, history, patch={"target": "new_outcome"})
    assert replay == second
    assert records == history
    with pytest.raises(ParameterContractError, match="conflicts"):
        revise(second, history, patch={"target": "conflicting"})


@pytest.mark.parametrize("source", [None, True, 0, -1, "11"])
def test_untrusted_source_identifier_types_are_rejected(source):
    with pytest.raises(ParameterContractError):
        revise(patch={"target": None}, source=source)


@pytest.mark.parametrize("mutation", ["revision", "before", "after", "update_id", "source", "duplicate"])
def test_history_rejects_tampering(mutation):
    final, history = revise(patch={"target": None})
    if mutation == "revision": history[0]["revision"] = 3
    elif mutation == "before": history[0]["changes"]["target"]["before"] = None
    elif mutation == "after": history[0]["changes"]["target"]["after"] = "unapplied"
    elif mutation == "update_id": history[0]["update_id"] = "wrong"
    elif mutation == "source": history[0]["source_input_id"] = 123
    else: history.append(deepcopy(history[0]))
    with pytest.raises(ParameterContractError):
        validate_parameter_history(final, history, job_id=JOB)


def test_extraction_accepts_explanatory_evidence_and_keeps_ambiguous_answer_uncommitted():
    extracted = ParameterExtraction.model_validate({"updates": [
        {"field": "target", "value": None, "evidence": "不指定结果变量"},
    ]})
    assert extraction_patch(extracted) == {"target": None}
    extracted = ParameterExtraction.model_validate({"updates": [
        {"field": "target", "value": "收入", "evidence": "用户接受当前问题中的目标变量提议"},
    ]})
    assert extraction_patch(extracted) == {"target": "收入"}
    ambiguous = ParameterExtraction(needs_clarification=True, clarification_question="每天还是每周？",
                                    updates=extracted.updates)
    assert extraction_patch(ambiguous) == {}


def test_unknown_patch_fields_and_model_supplied_provenance_are_rejected():
    with pytest.raises(ParameterContractError):
        revise(patch={"source_input_id": 1})
    with pytest.raises(ValueError):
        ParameterExtraction.model_validate({"source_input_id": 1})


def test_measurement_patch_preserves_other_columns_and_allows_explicit_clear():
    current = {**PARAMETERS, "measurement_semantics": [
        {"column": "x", "kind": "continuous", "evidence": "x是连续测量"},
        {"column": "y", "kind": "continuous", "evidence": "y是连续测量"},
    ]}
    extraction = ParameterExtraction.model_validate({"updates": [{
        "field": "measurement_semantics", "evidence": "y实际是计数",
        "value": [{"column": "y", "kind": "count", "evidence": "y实际是计数"}],
    }]})
    patch = extraction_patch(extraction, current_parameters=current)
    final, history = revise(current, patch=patch)
    assert final["measurement_semantics"][0] == current["measurement_semantics"][0]
    assert final["measurement_semantics"][1]["kind"] == "count"
    cleared, history = revise(final, history, patch={"measurement_semantics": None}, source=12)
    assert cleared["measurement_semantics"] is None and len(history) == 2


def test_candidate_round_trip_binds_parameters_and_revision_chain():
    final, history = revise(patch={"target": None})
    metadata = {PARAMETER_BINDING_KEY: parameter_binding(final, history, job_id=JOB)}
    restored = restore_parameter_binding(metadata, job_id=JOB)
    assert restored["analysis_parameters"]["target"] is None
    assert restored["analysis_parameter_changes"] == history
    require_candidate_parameters(metadata, job_id=JOB, parameters=final, changes=history)
    with pytest.raises(DatasetPersistenceError):
        require_candidate_parameters(metadata, job_id=JOB, parameters=PARAMETERS, changes=[])
    metadata[PARAMETER_BINDING_KEY]["parameters"]["time_window"] = "每周"
    with pytest.raises(ParameterContractError):
        restore_parameter_binding(metadata, job_id=JOB)


def test_old_candidate_without_parameter_snapshot_is_rejected():
    with pytest.raises(ParameterContractError):
        restore_parameter_binding({}, job_id=JOB)


def test_candidate_revision_boolean_cannot_impersonate_an_integer():
    final, history = revise(patch={"target": None})
    metadata = {PARAMETER_BINDING_KEY: parameter_binding(final, history, job_id=JOB)}
    metadata[PARAMETER_BINDING_KEY]["revision"] = True
    with pytest.raises(ParameterContractError):
        restore_parameter_binding(metadata, job_id=JOB)


def test_independent_validator_rejects_new_parameters_with_old_candidate():
    metadata = {PARAMETER_BINDING_KEY: parameter_binding(PARAMETERS, [], job_id=JOB)}
    changed = {**PARAMETERS, "target": None}
    report = validate_candidate(data_bytes=b"a,b\n1,2\n", script_bytes=b"pass",
                                metadata=metadata, analysis_parameters=changed, specifications=[],
                                max_bytes=1000, expected_parameter_identity=parameter_identity(changed))
    assert report["accepted"] is False
    assert report["errors"][0]["code"] == "parameter_identity_mismatch"


def test_null_roles_and_one_compatible_algorithm_still_allow_analysis():
    parameters = {"target": None, "treatment": None, "measurement_semantics": None}
    metadata = {PARAMETER_BINDING_KEY: parameter_binding(parameters, [], job_id=JOB),
                "summary": "保留原始观察行", "observation_unit": "每行一个观察",
                "transformations": ["保留原始字段"],
                "variable_semantics": {"category": "类别标签", "value": "测量值"}}
    report = validate_candidate(
        data_bytes=b"category,value\na,1\nb,2\na,3\n", script_bytes=b"pass",
        metadata=metadata, analysis_parameters=parameters, specifications=[PC_SPEC, DIRECT_LINGAM_SPEC],
        max_bytes=1000, expected_parameter_identity=parameter_identity(parameters),
    )
    assert report["accepted"] is True
    assert report["compatible_algorithm_count"] == 1
    assert {entry["status"] for entry in report["algorithm_compatibility"]} == {"compatible", "incompatible"}


def test_child_run_identity_survives_parameter_changes_but_not_input_changes():
    original = build_data_processing_run_id(job_id=JOB, source_input_identity="a" * 64, semantic_identity="b" * 64)
    assert original == build_data_processing_run_id(job_id=JOB, source_input_identity="a" * 64, semantic_identity="c" * 64)
    assert original != build_data_processing_run_id(job_id=JOB, source_input_identity="d" * 64)


def test_sandbox_request_id_binds_tool_operation_job_and_frozen_source():
    values = dict(tool_call_id="call-1", job_id=JOB, source_hash="a" * 64, purpose="inspect")
    digest = sandbox_request_id(**values)
    assert len(digest) == 64 and digest == sandbox_request_id(**values)
    for change in ({"tool_call_id": "call-2"}, {"purpose": "execute"},
                   {"source_hash": "b" * 64}, {"job_id": JOB + "2"}):
        assert digest != sandbox_request_id(**{**values, **change})


def test_sandbox_request_id_survives_worker_takeover():
    request_ids = []
    for attempt, epoch in ((1, 3), (2, 4)):
        identity = {"job_id": JOB, "attempt_count": attempt, "lease_epoch": epoch,
                    "source_hash": "a" * 64}
        request_ids.append(sandbox_request_id(
            job_id=identity["job_id"], source_hash=identity["source_hash"],
            tool_call_id="checkpoint-call-1", purpose="execute",
        ))
    assert request_ids[0] == request_ids[1]


@pytest.mark.parametrize("source_hash", [None, "", "wrong", "z" * 64])
def test_sandbox_request_id_rejects_missing_or_invalid_source(source_hash):
    with pytest.raises(ParameterContractError):
        sandbox_request_id(tool_call_id="call-1", job_id=JOB, source_hash=source_hash, purpose="execute")


def test_historical_context_with_null_roles_is_not_an_unfinished_context():
    context = {"file_object_id": 9, "status": "active", "target": None, "treatment": None,
               "analysis_question": "旧问题", "latest_report_id": 5}
    assert context_has_results(context)
    assert final_context_action(context, {"analysis_question": "新问题"}, has_history=True,
                                historical_identity=None, file_object_id=9) == "created"
    assert final_context_action(context, {"analysis_question": "新问题"}, has_history=False,
                                historical_identity=None, file_object_id=9) == "updated"


def test_history_only_reuses_an_exact_final_parameter_snapshot():
    context = {**PARAMETERS, "file_object_id": 9, "status": "active"}
    digest = parameter_identity(PARAMETERS)
    assert final_context_action(context, PARAMETERS, has_history=True, historical_identity=digest,
                                file_object_id=9) == "unchanged"
    assert final_context_action(context, {**PARAMETERS, "time_window": "每天"}, has_history=True,
                                historical_identity=digest, file_object_id=9) == "created"


def test_final_context_switch_retains_the_old_context_and_deduplicates_index():
    previous = {"analysis_context_id": "old", "analysis_question": "原研究"}
    index = [{"analysis_context_id": "old"}, {"analysis_context_id": "current"},
             {"analysis_context_id": "older"}]
    result = rebuild_context_index(index, previous, "current")
    assert [entry["analysis_context_id"] for entry in result] == ["old", "older"]
