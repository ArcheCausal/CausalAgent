"""P1 Pydantic JSON Schema / Tool schema 固定快照。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.agent.schema_manifest import build_full_snapshot, derive_manifest
from Agent.deep_agent_tools.algorithm_specs import get_algorithm_spec
from Agent.deep_agent_tools.models import FinalAnalysisDecision, ResultAssessment


SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "deep_agent_schema_snapshot.json"
FULL_SNAPSHOT_PATH = (
    Path(__file__).parent / "snapshots" / "deep_agent_full_schema_snapshot.json"
)


def test_pydantic_and_tool_schema_snapshots_are_stable() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert derive_manifest(build_full_snapshot()) == expected


def test_full_pydantic_and_tool_schema_snapshot_matches() -> None:
    expected = json.loads(FULL_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert build_full_snapshot() == expected


def test_effect_tool_schemas_require_explicit_definition_under_v3() -> None:
    for capability in ("causal.backdoor_ols", "causal.g_formula"):
        spec = get_algorithm_spec(capability)
        schema = spec.model_input_schema.model_json_schema()
        assert spec.version == "3.0"
        assert "effect_request" in schema["required"]
        assert "contrast" not in schema["properties"]
        assert "default" not in schema["properties"]["effect_request"]


def test_decision_schema_has_only_independent_primary_roles() -> None:
    assert ResultAssessment.model_json_schema()["properties"]["disposition"]["enum"] == [
        "primary_graph", "primary_effect", "supporting", "discarded"]
    properties = FinalAnalysisDecision.model_json_schema()["properties"]
    assert {"primary_graph_result_ref", "primary_effect_result_ref", "primary_result_ref",
            "effect_status", "effect_reason"} <= properties.keys()
