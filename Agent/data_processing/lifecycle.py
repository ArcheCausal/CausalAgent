"""处理方案、确认快照、候选引用和执行说明的确定性规则。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
from typing import Any

from Agent.data_processing.parameters import (
    PARAMETER_BINDING_KEY, ParameterContractError, parameter_identity, validate_parameter_history,
)
from Agent.data_processing.plans import build_processing_plan_version
from Agent.data_processing.validation import DatasetValidationError, _inspect_csv_shape


def clear_candidate() -> dict[str, Any]:
    """只清空当前引用，历史候选仍由 MySQL 保存。"""
    return {
        "data_processing_candidate_id": 0,
        "data_processing_candidate_revision": 0,
        "data_processing_execution_report": None,
    }


def invalidate_plan() -> dict[str, Any]:
    return {**clear_candidate(), "data_processing_confirmed_plan": None,
            "data_processing_confirmed_plan_version": None}


def update_plan(state: Mapping[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    update = {"data_processing_metadata": deepcopy(metadata)}
    if state.get("data_processing_metadata") != metadata:
        update.update(invalidate_plan())
    return update


def update_script(state: Mapping[str, Any], script: str) -> dict[str, Any]:
    update = {"data_processing_draft_script": script}
    if state.get("data_processing_draft_script") != script:
        update.update(clear_candidate())
    return update


def confirmed_version(state: Mapping[str, Any], *, job_id: str, source_hash: str) -> str | None:
    """缺少确认时返回空值；已经存在的确认发生冲突时直接失败。"""
    version = state.get("data_processing_confirmed_plan_version")
    snapshot = state.get("data_processing_confirmed_plan")
    if version is None:
        if snapshot is not None:
            raise ParameterContractError("unversioned processing confirmation snapshot")
        return None
    validate_parameter_history(state.get("analysis_parameters"), state.get("analysis_parameter_changes"), job_id=job_id)
    if (state.get("data_processing_pending_input_id") or not state.get("data_processing_research_spec_ready")
            or state.get("data_processing_plan_parameter_identity") != parameter_identity(state.get("analysis_parameters") or {})):
        raise ParameterContractError("processing confirmation has unresolved input or stale research parameters")
    if not isinstance(snapshot, dict) or snapshot != state.get("data_processing_metadata"):
        raise ParameterContractError("processing confirmation snapshot conflicts with draft")
    expected = build_processing_plan_version(
        job_id=job_id, source_hash=source_hash,
        semantic_identity=state["data_processing_semantic_identity"], metadata=snapshot,
    )
    if expected != version:
        raise ParameterContractError("processing confirmation version conflicts with snapshot")
    return expected


def execution_report(
    *, candidate_id: int, revision: int, plan_version: str, source_hash: str,
    data_bytes: bytes, script_bytes: bytes, explanation: str = "",
) -> dict[str, Any]:
    """文件事实只从实际产物生成；不把解释视为校验结论。"""
    report = {
        "candidate_id": candidate_id, "revision": revision, "plan_version": plan_version,
        "source_hash": source_hash, "data_hash": hashlib.sha256(data_bytes).hexdigest(),
        "script_hash": hashlib.sha256(script_bytes).hexdigest(), "output_bytes": len(data_bytes),
        "explanation": explanation,
    }
    try:
        columns, rows = _inspect_csv_shape(
            data_bytes, max_rows=100_000, max_columns=1_000, max_cells=1_000_000,
        )
    except (DatasetValidationError, UnicodeError) as exc:
        # 格式或规模错误仍交给独立校验及原有重试流程。
        report.update(row_count=None, columns=[], artifact_error=type(exc).__name__)
    else:
        report.update(row_count=rows, columns=columns)
    return report


def require_candidate_binding(
    state: Mapping[str, Any], candidate: Mapping[str, Any], *, job_id: str, source_hash: str,
) -> None:
    """在数据库的来源与确认校验之外，检查 checkpoint 的当前绑定。"""
    version = confirmed_version(state, job_id=job_id, source_hash=source_hash)
    metadata = dict(candidate["metadata"])
    binding = metadata.pop("processing_plan_identity")
    metadata.pop(PARAMETER_BINDING_KEY)
    if (version is None or binding["plan_version"] != version
            or binding["semantic_identity"] != state.get("data_processing_semantic_identity")
            or metadata != state.get("data_processing_confirmed_plan")
            or candidate["source_hash"] != source_hash
            or type(state.get("data_processing_candidate_id")) is not int
            or type(state.get("data_processing_candidate_revision")) is not int
            or candidate["dataset_id"] != state.get("data_processing_candidate_id")
            or candidate["revision"] != state.get("data_processing_candidate_revision")):
        raise ParameterContractError("candidate conflicts with current processing confirmation")
    script = state.get("data_processing_draft_script")
    if not isinstance(script, str) or hashlib.sha256(script.encode("utf-8")).hexdigest() != candidate["script_hash"]:
        raise ParameterContractError("candidate conflicts with current processing script")
    report = state.get("data_processing_execution_report")
    expected = execution_report(
        candidate_id=candidate["dataset_id"], revision=candidate["revision"],
        plan_version=version, source_hash=source_hash, data_bytes=candidate["data_bytes"],
        script_bytes=candidate["script_bytes"],
    )
    if not isinstance(report, dict) or {key: value for key, value in report.items() if key != "explanation"} != {
        key: value for key, value in expected.items() if key != "explanation"
    }:
        raise ParameterContractError("execution report conflicts with current candidate")
