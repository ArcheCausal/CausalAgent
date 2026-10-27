"""研究参数的确定性修订、来源身份及候选快照契约。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from Agent.measurement_semantics import normalize_measurement_semantics


ParameterField = Literal[
    "target", "treatment", "analysis_question", "measurement_semantics",
    "observation_unit", "time_window", "aggregation_scope",
]
RESEARCH_FIELDS = frozenset(ParameterField.__args__)
PARAMETER_BINDING_KEY = "analysis_parameter_identity"
PROCESSING_STATE_VERSION = 2


class ParameterContractError(ValueError):
    """参数、修订链或候选身份不满足恢复契约。"""


class ParameterExtractionError(ParameterContractError):
    """模型提取结果不满足可纠正的确定性参数规则。"""

    structured_output_retryable = True

    def __init__(self, *, field_path: str, rule: str) -> None:
        self.field_path = field_path
        self.rule = rule
        super().__init__("parameter extraction result violates a deterministic rule")


class ParameterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: ParameterField
    value: str | list[dict[str, str]] | None = Field(
        description="用户本次指定、修正或接受的参数值；可以结合当前问题和已有参数理解。",
    )
    evidence: str = Field(
        min_length=1, max_length=4000,
        description="解释本次答复为何支持这项修改，可以引用原文或概括确认关系；必须为非空文本。",
    )


class ParameterExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    updates: list[ParameterEdit] = Field(default_factory=list, max_length=7)
    needs_clarification: bool = False
    clarification_question: str = Field(default="", max_length=3000)
    processing_scope_changed: bool = False


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def research_parameters(value: Mapping[str, Any]) -> dict[str, Any]:
    """仅投影研究字段，派生画像不参与参数版本或身份。"""
    if not isinstance(value, Mapping):
        raise ParameterContractError("analysis parameters must be an object")
    result = {key: deepcopy(value.get(key)) for key in RESEARCH_FIELDS}
    for key, item in result.items():
        if key == "measurement_semantics":
            if item is None:
                continue
            normalize_measurement_semantics(item)
            for declaration in item:
                if set(declaration) != {"column", "kind", "evidence"} or not (
                    isinstance(declaration["evidence"], str) and declaration["evidence"].strip()
                ):
                    raise ParameterContractError("measurement evidence is required")
        elif item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 6000):
            raise ParameterContractError(f"invalid research parameter: {key}")
    return result


def parameter_identity(parameters: Mapping[str, Any]) -> str:
    return _digest(research_parameters(parameters))


def parameter_update_id(*, job_id: str, source_input_id: int) -> str:
    if type(source_input_id) is not int or source_input_id <= 0:
        raise ParameterContractError("source_input_id must identify a trusted input record")
    return _digest({"job_id": job_id, "source_input_id": source_input_id})


def validate_parameter_history(
    parameters: Mapping[str, Any], changes: Any, *, job_id: str,
) -> list[dict[str, Any]]:
    """从最终快照倒推修订链，拒绝跳号、冲突及不可追溯的来源。"""
    current = research_parameters(parameters)
    if not isinstance(changes, list):
        raise ParameterContractError("parameter changes must be a list")
    seen: set[str] = set()
    for revision, record in enumerate(changes, 1):
        if not isinstance(record, dict) or set(record) != {
            "revision", "update_id", "source_input_id", "changes",
        }:
            raise ParameterContractError("invalid parameter revision record")
        if type(record["revision"]) is not int or record["revision"] != revision:
            raise ParameterContractError("invalid parameter revision sequence")
        expected = parameter_update_id(job_id=job_id, source_input_id=record["source_input_id"])
        if record["update_id"] != expected or expected in seen:
            raise ParameterContractError("duplicate or invalid parameter update identity")
        seen.add(expected)
    for record in reversed(changes):
        edits = record["changes"]
        if not isinstance(edits, dict) or not edits or set(edits) - RESEARCH_FIELDS:
            raise ParameterContractError("invalid parameter changes")
        for key, edit in edits.items():
            if not isinstance(edit, dict) or set(edit) != {"before", "after"}:
                raise ParameterContractError("invalid parameter edit")
            if edit["before"] == edit["after"] or current[key] != edit["after"]:
                raise ParameterContractError("parameter history conflicts with its snapshot")
            current[key] = deepcopy(edit["before"])
        research_parameters(current)
    return deepcopy(changes)


def apply_parameter_patch(
    parameters: Mapping[str, Any], changes: list[dict[str, Any]], *,
    patch: Mapping[str, Any], job_id: str, source_input_id: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """缺席字段不改；显式 null 清空；相同答复重放去重，冲突明确失败。"""
    history = validate_parameter_history(parameters, changes, job_id=job_id)
    if not isinstance(patch, Mapping) or set(patch) - RESEARCH_FIELDS:
        raise ParameterContractError("patch contains unsupported parameter fields")
    updated = deepcopy(dict(parameters))
    updated.update(deepcopy(dict(patch)))
    research_parameters(updated)
    update_id = parameter_update_id(job_id=job_id, source_input_id=source_input_id)
    for record in history:
        if record["update_id"] == update_id:
            expected_patch = {key: edit["after"] for key, edit in record["changes"].items()}
            # Replayed extraction may include fields that were unchanged originally.
            if any(key not in patch or patch[key] != val for key, val in expected_patch.items()) or any(
                key not in expected_patch and parameters.get(key) != val for key, val in patch.items()
            ):
                raise ParameterContractError("replayed parameter update conflicts with its content")
            return deepcopy(dict(parameters)), history
    delta = {key: {"before": deepcopy(parameters.get(key)), "after": deepcopy(val)}
             for key, val in patch.items() if parameters.get(key) != val}
    if delta:
        history.append({"revision": len(history) + 1, "update_id": update_id,
                        "source_input_id": source_input_id, "changes": delta})
    return updated, history


def validate_parameter_extraction(extraction: ParameterExtraction) -> None:
    """检查模型提取结果的确定性业务规则，不修改模型输出。"""
    patch_fields: set[str] = set()
    for index, edit in enumerate(extraction.updates):
        field_path = f"updates.{index}"
        if edit.field in patch_fields:
            raise ParameterExtractionError(
                field_path=f"{field_path}.field",
                rule="同一参数字段只能修改一次",
            )
        patch_fields.add(edit.field)
        if not isinstance(edit.evidence, str) or not edit.evidence.strip():
            raise ParameterExtractionError(
                field_path=f"{field_path}.evidence",
                rule="必须为非空文本",
            )
        if edit.field == "measurement_semantics":
            if edit.value is not None and not isinstance(edit.value, list):
                raise ParameterExtractionError(
                    field_path=f"{field_path}.value",
                    rule="测量声明必须为列表或 null",
                )
            if edit.value is None:
                continue
            for declaration_index, declaration in enumerate(edit.value):
                declaration_path = f"{field_path}.value.{declaration_index}"
                if set(declaration) != {"column", "kind", "evidence"}:
                    raise ParameterExtractionError(
                        field_path=declaration_path,
                        rule="测量声明必须包含 column、kind 和 evidence，且不能包含其他字段",
                    )
                if not isinstance(declaration["column"], str) or not declaration["column"].strip():
                    raise ParameterExtractionError(
                        field_path=f"{declaration_path}.column",
                        rule="列名必须为非空文本",
                    )
                if not isinstance(declaration["kind"], str) or declaration["kind"] not in {
                    "count", "ordinal", "category", "continuous", "binary", "unknown",
                }:
                    raise ParameterExtractionError(
                        field_path=f"{declaration_path}.kind",
                        rule="测量类型必须属于允许的枚举",
                    )
                if not isinstance(declaration["evidence"], str) or not declaration["evidence"].strip():
                    raise ParameterExtractionError(
                        field_path=f"{declaration_path}.evidence",
                        rule="必须为非空文本",
                    )
            try:
                normalize_measurement_semantics(edit.value)
            except ValueError as exc:
                raise ParameterExtractionError(
                    field_path=f"{field_path}.value",
                    rule="同一列的测量声明不能冲突",
                ) from exc
        elif edit.value is not None and (
            not isinstance(edit.value, str) or not edit.value.strip() or len(edit.value) > 6000
        ):
            raise ParameterExtractionError(
                field_path=f"{field_path}.value",
                rule="字段值必须为非空文本且不超过 6000 个字符，或为 null",
            )
    if extraction.needs_clarification and not extraction.clarification_question.strip():
        raise ParameterExtractionError(
            field_path="clarification_question",
            rule="澄清状态必须包含具体问题",
        )


def extraction_patch(
    extraction: ParameterExtraction,
    *,
    current_parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_parameter_extraction(extraction)
    if extraction.needs_clarification:
        return {}
    patch: dict[str, Any] = {}
    for edit in extraction.updates:
        patch[edit.field] = edit.value
    declarations = patch.get("measurement_semantics")
    if isinstance(declarations, list) and current_parameters is not None:
        normalize_measurement_semantics(declarations)
        previous = current_parameters.get("measurement_semantics") or []
        merged = {item["column"]: deepcopy(item) for item in previous}
        for item in declarations:
            merged[item["column"]] = deepcopy(item)
        patch["measurement_semantics"] = list(merged.values())
    return patch


def parameter_binding(parameters: Mapping[str, Any], changes: Any, *, job_id: str) -> dict[str, Any]:
    history = validate_parameter_history(parameters, changes, job_id=job_id)
    return {"parameters": research_parameters(parameters), "changes": history,
            "identity": parameter_identity(parameters), "revision": len(history)}


def restore_parameter_binding(metadata: Mapping[str, Any], *, job_id: str) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ParameterContractError("candidate metadata must be an object")
    binding = metadata.get(PARAMETER_BINDING_KEY)
    if not isinstance(binding, dict) or set(binding) != {"parameters", "changes", "identity", "revision"}:
        raise ParameterContractError("candidate has no current parameter binding")
    expected = parameter_binding(binding["parameters"], binding["changes"], job_id=job_id)
    if type(binding["revision"]) is not int or binding != expected:
        raise ParameterContractError("candidate parameter identity conflicts with its snapshot")
    return {"analysis_parameters": expected["parameters"],
            "analysis_parameter_changes": expected["changes"]}


def sandbox_request_id(*, tool_call_id: str, job_id: str, source_hash: str, purpose: str) -> str:
    """跨 Worker 接管保持同一申请身份；执行代次仅用于控制服务 fencing。"""
    if not tool_call_id or not purpose:
        raise ParameterContractError("sandbox request requires a tool call and operation")
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(
        char not in "0123456789abcdef" for char in source_hash
    ):
        raise ParameterContractError("sandbox request requires a frozen input SHA-256 digest")
    return _digest({"tool_call_id": tool_call_id, "job_id": job_id,
                    "source_hash": source_hash, "purpose": purpose})
