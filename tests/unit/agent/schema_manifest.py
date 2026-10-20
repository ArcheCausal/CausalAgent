"""Deep Agent schema 快照的共享模型名单与派生规则。

文档职责：提供完整基线和摘要快照共用的模型名单，以及从完整 schema 派生摘要的
规范化哈希规则。

适用范围：schema 快照测试和重新生成脚本；新增模型时只在此处登记一次。
"""

from __future__ import annotations

import hashlib
import json

import Agent.deep_agent_tools.models as models
from Agent.deep_agent_tools.algorithm_specs import build_tool_schema_snapshot


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
SCHEMA_VERSION = "p1-v1"


def canonical_json(value: object) -> bytes:
    """按快照格式序列化，保证摘要与完整基线使用同一规则。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_full_snapshot() -> dict[str, object]:
    """从当前模型和 Tool 定义生成完整基线。"""
    return {
        "models": {
            name: getattr(models, name).model_json_schema() for name in MODEL_NAMES
        },
        "schema_version": SCHEMA_VERSION,
        "tools": build_tool_schema_snapshot(),
    }


def derive_manifest(full: dict[str, object]) -> dict[str, object]:
    """从完整基线派生属性、必填字段和摘要，不再单独维护名单。"""
    model_manifest: dict[str, object] = {}
    for name, schema in full["models"].items():
        model_manifest[name] = {
            "properties": sorted(schema.get("properties", {})),
            "required": schema.get("required", []),
            "sha256": hashlib.sha256(canonical_json(schema)).hexdigest(),
            "title": schema.get("title"),
        }
    return {
        "pydantic_models": model_manifest,
        "schema_version": full["schema_version"],
        "tools": [
            {
                "capability_id": item["capability_id"],
                "schema_sha256": hashlib.sha256(canonical_json(item["tool"])).hexdigest(),
                "spec_digest": item["spec_digest"],
                "tool_name": item["tool"]["name"],
            }
            for item in full["tools"]
        ],
    }
