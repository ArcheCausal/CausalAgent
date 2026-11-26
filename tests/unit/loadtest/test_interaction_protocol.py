"""压测交互答复的协议兼容检查；只使用固定载荷，不连接任何服务。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "loadtest"))

from runner import interaction  # noqa: E402

VERSION = "b" * 64
PLAN = {
    "summary": "keep all rows",
    "observation_unit": "one device, one measurement",
    "transformations": ["keep original columns"],
    "variable_semantics": {name: "continuous measurement" for name in ("x1", "x2", "x3", "x4", "x5")},
}


def profile(**overrides):
    spec = {"mode": "sandbox_flow", "research_answer": "keep every row",
            "observation_unit": "one device, one measurement",
            "expected_columns": ["x1", "x2", "x3", "x4", "x5"],
            "max_interactions": 6, "interaction_deadline_seconds": 300}
    spec.update(overrides)
    return interaction.profile_from_spec(spec)


def decide(payload, ledger=None, **overrides):
    return interaction.decide(payload, "job-1", profile(**overrides),
                              ledger or interaction.InteractionLedger(6, 300))


def interrupt(kind, question="q-1", **fields):
    return {"question_id": question, "interaction": {"interaction_type": kind, **fields}}


def test_research_question_uses_scenario_answer():
    decision = decide(interrupt(interaction.QUESTION_TYPE))
    assert decision.sendable and decision.answer == "keep every row"
    assert decision.question_id == "q-1"


def test_plan_confirmation_uses_event_version():
    decision = decide(interrupt(interaction.PLAN_CONFIRMATION_TYPE, plan_version=VERSION, plan=PLAN))
    assert decision.sendable
    assert json.loads(decision.answer) == {"action": "confirm", "plan_version": VERSION}
    assert decision.plan_version == VERSION


def test_plan_outside_scope_is_recorded_and_not_confirmed():
    plan = dict(PLAN, transformations=["aggregate per device and drop x3"])
    decision = decide(interrupt(interaction.PLAN_CONFIRMATION_TYPE, plan_version=VERSION, plan=plan))
    assert decision.status == "deviation" and not decision.sendable
    assert decision.answer == ""
    assert any(item.startswith("forbidden_transformation:") for item in decision.deviation)


def test_plan_with_changed_variable_scope_is_a_deviation():
    plan = dict(PLAN, variable_semantics={"x1": "continuous", "x2": "continuous"})
    decision = decide(interrupt(interaction.PLAN_CONFIRMATION_TYPE, plan_version=VERSION, plan=plan))
    assert decision.status == "deviation"
    assert any(item.startswith("variable_scope_changed:") for item in decision.deviation)


@pytest.mark.parametrize("kind", ["unexpected_interaction", "", "data_processing_options"])
def test_unrecognized_interaction_never_sends_default_answer(kind):
    decision = decide(interrupt(kind))
    assert decision.status == "unrecognized" and not decision.sendable
    assert decision.answer == ""


def test_invalid_plan_version_is_unrecognized():
    decision = decide(interrupt(interaction.PLAN_CONFIRMATION_TYPE, plan_version="short", plan=PLAN))
    assert decision.status == "unrecognized" and decision.detail == "invalid_plan_version"


def test_same_question_reuses_key_and_new_question_uses_new_key():
    ledger = interaction.InteractionLedger(6, 300)
    first = decide(interrupt(interaction.QUESTION_TYPE, question="q-1"), ledger)
    retry = decide(interrupt(interaction.QUESTION_TYPE, question="q-1"), ledger)
    second = decide(interrupt(interaction.QUESTION_TYPE, question="q-2"), ledger)
    assert first.key == retry.key
    assert second.key != first.key


def test_round_and_time_limits_stop_further_answers():
    ledger = interaction.InteractionLedger(1, 300)
    assert decide(interrupt(interaction.QUESTION_TYPE, question="q-1"), ledger).sendable
    exhausted = decide(interrupt(interaction.QUESTION_TYPE, question="q-2"), ledger)
    assert exhausted.status == "budget_exhausted" and not exhausted.sendable
    timed = interaction.InteractionLedger(6, 0)
    assert decide(interrupt(interaction.QUESTION_TYPE), timed).detail == "interaction_time_limit"


def test_missing_question_id_is_unrecognized():
    decision = interaction.decide({"interaction": {"interaction_type": interaction.QUESTION_TYPE}},
                                  "job-1", profile(), interaction.InteractionLedger(6, 300))
    assert decision.status == "unrecognized" and not decision.sendable
