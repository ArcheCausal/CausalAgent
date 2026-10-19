"""按当前契约重新生成 Deep Agent schema 快照。

只在模型或 Tool schema 按计划变更后运行；输出必须随对应代码一起评审。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import Agent.deep_agent_tools.models as models
from Agent.deep_agent_tools.algorithm_specs import build_tool_schema_snapshot


SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
MODEL_NAMES = (
    "ActionAttempt",
    "AlgorithmExecutionCommand",
    "AlgorithmResult",
    "AlgorithmResultProvenance",
    "DataIssue",
    "DataProfile",
    "DataViewSummary",
    "Diagnostics",
    "EvidenceResult",
    "FinalAnalysisDecision",
    "GraphEdge",
    "InvocationRecord",
    "McpInvocationContext",
    "PreprocessingApproval",
    "PreprocessingPlan",
    "PreprocessingStep",
    "PublicDecision",
    "RawResultMetadata",
    "ResultAssessment",
    "RevisionProposal",
    "SafeWarning",
    "ScientificConflict",
    "StandardizedGraph",
    "WebEvidenceResult",
)
NEWLINE = chr(10)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def main() -> None:
    model_manifest: dict[str, object] = {}
    for name in MODEL_NAMES:
        schema = getattr(models, name).model_json_schema()
        model_manifest[name] = {
            "properties": sorted(schema.get("properties", {})),
            "required": schema.get("required", []),
            "sha256": hashlib.sha256(_canonical_json(schema)).hexdigest(),
            "title": schema.get("title"),
        }

    tools = build_tool_schema_snapshot()
    manifest = {
        "pydantic_models": model_manifest,
        "schema_version": "p1-v1",
        "tools": [
            {
                "capability_id": item["capability_id"],
                "schema_sha256": hashlib.sha256(
                    _canonical_json(item["tool"])
                ).hexdigest(),
                "spec_digest": item["spec_digest"],
                "tool_name": item["tool"]["name"],
            }
            for item in tools
        ],
    }
    full = {
        "models": {
            name: getattr(models, name).model_json_schema() for name in MODEL_NAMES
        },
        "schema_version": "p1-v1",
        "tools": tools,
    }
    (SNAPSHOT_DIR / "deep_agent_schema_snapshot.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + NEWLINE,
        encoding="utf-8",
    )
    (SNAPSHOT_DIR / "deep_agent_full_schema_snapshot.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2) + NEWLINE,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
