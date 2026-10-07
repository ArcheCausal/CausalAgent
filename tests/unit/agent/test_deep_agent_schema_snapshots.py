"""P1 Pydantic JSON Schema / Tool schema 固定快照。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import Agent.deep_agent_tools.models as models
from Agent.deep_agent_tools.algorithm_specs import build_tool_schema_snapshot


SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "deep_agent_schema_snapshot.json"
FULL_SNAPSHOT_PATH = (
    Path(__file__).parent / "snapshots" / "deep_agent_full_schema_snapshot.json"
)
MODEL_NAMES = (
    "ActionAttempt",
    "AlgorithmExecutionCommand",
    "AlgorithmResult",
    "AlgorithmResultProvenance",
    "DataProfile",
    "Diagnostics",
    "EvidenceResult",
    "FinalAnalysisDecision",
    "GraphEdge",
    "InvocationRecord",
    "McpInvocationContext",
    "PublicDecision",
    "RawResultMetadata",
    "ResultAssessment",
    "RevisionProposal",
    "SafeWarning",
    "ScientificConflict",
    "StandardizedGraph",
    "WebEvidenceResult",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _schema_manifest() -> dict[str, object]:
    model_manifest: dict[str, object] = {}
    for name in MODEL_NAMES:
        schema = getattr(models, name).model_json_schema()
        model_manifest[name] = {
            "properties": sorted(schema.get("properties", {})),
            "required": schema.get("required", []),
            "sha256": hashlib.sha256(_canonical_json(schema)).hexdigest(),
            "title": schema.get("title"),
        }

    tool_manifest = []
    for item in build_tool_schema_snapshot():
        tool_manifest.append(
            {
                "capability_id": item["capability_id"],
                "schema_sha256": hashlib.sha256(
                    _canonical_json(item["tool"])
                ).hexdigest(),
                "spec_digest": item["spec_digest"],
                "tool_name": item["tool"]["name"],
            }
        )
    return {
        "pydantic_models": model_manifest,
        "schema_version": "p1-v1",
        "tools": tool_manifest,
    }


def test_pydantic_and_tool_schema_snapshots_are_stable() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert _schema_manifest() == expected


def test_full_pydantic_and_tool_schema_snapshot_matches() -> None:
    expected = json.loads(FULL_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    actual = {
        "models": {
            name: getattr(models, name).model_json_schema() for name in MODEL_NAMES
        },
        "schema_version": "p1-v1",
        "tools": build_tool_schema_snapshot(),
    }
    assert actual == expected
