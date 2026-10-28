"""只验证方案和产物的确定性规则；不替代数据库、模型或沙盒验收。"""

from copy import deepcopy
import hashlib

import pytest

from Agent.data_processing.lifecycle import (
    confirmed_version, execution_report, invalidate_plan, require_candidate_binding, update_plan, update_script,
)
from Agent.data_processing.parameters import PARAMETER_BINDING_KEY, ParameterContractError, parameter_binding
from Agent.data_processing.plans import build_processing_plan_version


JOB = "00000000-0000-0000-0000-000000000978"
SOURCE = "a" * 64
SEMANTIC = "b" * 64
PLAN = {"summary": "保留观测值", "observation_unit": "一次观测",
        "transformations": ["保留字段"], "variable_semantics": {"x": "测量值"}, "role_columns": {}, "limitations": []}
SCRIPT = "print('prepare')"
DATA = b"x,y\n1,2\n3,4\n"
VERSION = build_processing_plan_version(job_id=JOB, source_hash=SOURCE, semantic_identity=SEMANTIC, metadata=PLAN)


def current_state():
    return {"data_processing_metadata": deepcopy(PLAN), "data_processing_confirmed_plan": deepcopy(PLAN),
            "data_processing_confirmed_plan_version": VERSION, "data_processing_semantic_identity": SEMANTIC,
            "data_processing_draft_script": SCRIPT, "data_processing_candidate_id": 12,
            "data_processing_candidate_revision": 2, "data_processing_execution_report": execution_report(
                candidate_id=12, revision=2, plan_version=VERSION, source_hash=SOURCE,
                data_bytes=DATA, script_bytes=SCRIPT.encode()),
            "data_processing_completion_retry_count": 1, "data_processing_retry_count": 2}


def candidate():
    return {"dataset_id": 12, "revision": 2, "source_hash": SOURCE, "data_bytes": DATA,
            "script_bytes": SCRIPT.encode(), "script_hash": hashlib.sha256(SCRIPT.encode()).hexdigest(),
            "metadata": {**deepcopy(PLAN), "processing_plan_identity": {
                "plan_version": VERSION, "semantic_identity": SEMANTIC},
                PARAMETER_BINDING_KEY: parameter_binding({}, [], job_id=JOB)}}


def test_identical_plan_and_script_preserve_confirmation_candidate_and_budgets():
    state = current_state()
    original = deepcopy(state)
    state.update(update_plan(state, deepcopy(PLAN)))
    state.update(update_script(state, SCRIPT))
    assert state == original


@pytest.mark.parametrize("field,value", [("summary", "按天聚合"), ("observation_unit", "一天"),
                                            ("role_columns", {"target": "y"}),
                                            ("limitations", ["仅最近一周"])])
def test_plan_change_clears_all_current_bindings(field, value):
    state = current_state()
    revised = {**deepcopy(PLAN), field: value}
    update = update_plan(state, revised)
    state.update(update)
    assert state["data_processing_metadata"] == revised
    assert state["data_processing_confirmed_plan"] is None
    assert state["data_processing_confirmed_plan_version"] is None
    assert state["data_processing_candidate_id"] == state["data_processing_candidate_revision"] == 0
    assert state["data_processing_execution_report"] is None
    assert state["data_processing_completion_retry_count"] == 1
    assert state["data_processing_retry_count"] == 2
    revised["variable_semantics"]["z"] = "new"
    assert "z" not in update["data_processing_metadata"]["variable_semantics"]


def test_script_fix_preserves_plan_and_confirmation_but_requires_new_execution():
    state = current_state()
    snapshot = deepcopy(state["data_processing_confirmed_plan"])
    state.update(update_script(state, SCRIPT + "\nprint('fixed')"))
    assert state["data_processing_candidate_id"] == 0
    assert state["data_processing_execution_report"] is None
    assert state["data_processing_confirmed_plan"] == snapshot
    assert confirmed_version(state, job_id=JOB, source_hash=SOURCE) == VERSION


def test_report_explanation_keeps_confirmation_and_cannot_change_artifact_facts():
    state = current_state()
    state["data_processing_execution_report"]["explanation"] = "保留两行实际观测。"
    require_candidate_binding(state, candidate(), job_id=JOB, source_hash=SOURCE)
    report = state["data_processing_execution_report"]
    assert report["row_count"] == 2 and report["columns"] == ["x", "y"]
    report["row_count"] = 999
    with pytest.raises(ParameterContractError, match="execution report"):
        require_candidate_binding(state, candidate(), job_id=JOB, source_hash=SOURCE)


@pytest.mark.parametrize("field,value", [("data_processing_draft_script", SCRIPT + "\n# changed"),
    ("data_processing_candidate_revision", 1), ("data_processing_candidate_id", 11),
    ("data_processing_semantic_identity", "c" * 64),
    ("data_processing_confirmed_plan_version", "d" * 64),
    ("data_processing_confirmed_plan", None), ("data_processing_execution_report", None)])
def test_stale_script_candidate_plan_or_report_is_rejected(field, value):
    state = current_state()
    state[field] = value
    with pytest.raises(ParameterContractError):
        require_candidate_binding(state, candidate(), job_id=JOB, source_hash=SOURCE)


def test_reverting_a_changed_plan_does_not_resurrect_old_confirmation():
    state = current_state()
    state.update(update_plan(state, {**PLAN, "summary": "改为聚合"}))
    state.update(update_plan(state, PLAN))
    assert confirmed_version(state, job_id=JOB, source_hash=SOURCE) is None
    assert state["data_processing_candidate_id"] == 0


def test_semantic_invalidation_clears_snapshot_candidate_and_report():
    state = current_state()
    state.update(invalidate_plan())
    assert confirmed_version(state, job_id=JOB, source_hash=SOURCE) is None
    assert state["data_processing_execution_report"] is None


def test_invalid_csv_facts_do_not_bypass_independent_validation():
    report = execution_report(candidate_id=12, revision=2, plan_version=VERSION, source_hash=SOURCE,
                              data_bytes=b"x,x\n1,2\n", script_bytes=SCRIPT.encode())
    assert report["row_count"] is None and report["artifact_error"] == "DatasetValidationError"
    assert "accepted" not in report
