"""研究需求完整提交、可信来源及候选快照的确定性契约。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Literal, Mapping
from pydantic import BaseModel, ConfigDict, Field
from Agent.effect_request import EffectRequest, EffectExecutionContext, normalize_effect_request
from Agent.measurement_semantics import normalize_measurement_semantics

ParameterField = Literal["target", "treatment", "analysis_question", "measurement_semantics",
                         "observation_unit", "time_window", "aggregation_scope", "effect_request"]
RESEARCH_FIELDS = frozenset(ParameterField.__args__)
PARAMETER_BINDING_KEY = "analysis_parameter_identity"
PROCESSING_STATE_VERSION = 4
PROCESSING_GRAPH_NAME = "causalagent"
RevisionReason = Literal["initial", "user_change", "correction"]

class ParameterContractError(ValueError):
    """参数、修订链或候选身份不满足恢复契约。"""

class UnsupportedProcessingCheckpointError(ParameterContractError):
    """旧处理流程不支持恢复，历史数据保留，需新建任务。"""

class MeasurementScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["columns", "all_input_columns"]
    columns: list[str] = Field(description="columns 范围的原始列名；全部输入列时必须为空")
    kind: Literal["count", "ordinal", "category", "continuous", "binary", "unknown"]
    evidence: str = Field(min_length=1, max_length=4000)

class ResearchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str | None = Field(description="用户指定的结果变量或目标角色名称，不要求等于 CSV 列名；未指定或明确取消为 null，分析范围不是角色。")
    treatment: str | None = Field(description="用户指定的处理或干预变量名称，不要求等于 CSV 列名；未指定或明确取消为 null。")
    analysis_question: str | None = Field(description="本次需要回答的研究问题，结合真实输入与已接受的上下文概括。")
    observation_unit: str | None = Field(description="每一行代表的观测对象或记录；没有明确说明时为 null，不写成测量列。")
    time_window: str | None = Field(description="用户指定或接受的研究时间范围；后续未修改时保留当前值。")
    aggregation_scope: str | None = Field(description="用户指定或接受的研究参与范围及聚合范围；后续未修改时保留当前值。")
    measurement_semantics: list[MeasurementScope] = Field(description="完整的原始列测量声明，不从数值或列名猜测；全体原始列使用 all_input_columns，无明确列级声明时为空列表。")
    effect_request: EffectRequest | None = Field(description="唯一效应请求。没有估计需求明确提交 null；存在需求时提交平均总效应及用户明确或确认的干预和单位。不得默认补 (0,1)。目标和处理角色引用现有字段。")

class ResearchSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec: ResearchSpec
    expected_revision: int = Field(ge=0)
    source_input_id: int = Field(gt=0, description="从程序提供的真实输入来源中选择，工具核验归属")
    reason: RevisionReason

def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

def research_parameters(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ParameterContractError("analysis parameters must be an object")
    # 缺字段的历史记录保留缺席，不能被投影为明确的未请求。
    result = {key: deepcopy(value.get(key)) for key in RESEARCH_FIELDS
              if key != "effect_request" or key in value}
    for key, item in result.items():
        if key == "effect_request":
            try:
                result[key] = normalize_effect_request(item)
            except ValueError as exc:
                raise ParameterContractError("invalid effect_request") from exc
        elif key == "measurement_semantics":
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

def execution_measurement_semantics(parameters: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[dict[str, str]]:
    """只经显式一对一映射传递测量声明；None 表示删列或不再继承。"""
    declarations = research_parameters(parameters).get("measurement_semantics") or []
    mapping = metadata.get("measurement_columns", {})
    if not isinstance(mapping, Mapping):
        raise ParameterContractError("measurement_columns must be an object")
    destinations = [value for value in mapping.values() if value is not None]
    if any(not isinstance(key, str) or not key.strip() for key in mapping) or any(
        not isinstance(value, str) or not value.strip() for value in destinations
    ) or len(destinations) != len(set(destinations)):
        raise ParameterContractError("measurement column mapping must be one-to-one")
    output = []
    seen = set()
    for item in declarations:
        column = mapping.get(item["column"], item["column"])
        if column is None:
            continue
        if column in seen:
            raise ParameterContractError("measurement columns have conflicting output references")
        seen.add(column)
        output.append({**item, "column": column})
    return output

def execution_parameters(parameters: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    if "effect_request" not in parameters:
        raise UnsupportedProcessingCheckpointError("effect_request was not recorded; start a new analysis")
    role_columns = metadata.get("role_columns", {})
    if not isinstance(role_columns, Mapping) or set(role_columns) - {"target", "treatment"}:
        raise ParameterContractError("role_columns must map target and treatment to output columns")
    result = dict(parameters)
    result["effect_request"] = research_parameters(parameters)["effect_request"]
    result["measurement_semantics"] = execution_measurement_semantics(parameters, metadata)
    for role, column in role_columns.items():
        if column is None:
            continue
        if not isinstance(column, str) or not column.strip() or len(column) > 255:
            raise ParameterContractError("role column must be a non-blank bounded string")
        declared = parameters.get(role)
        if not isinstance(declared, str) or not declared.strip():
            raise ParameterContractError("role column requires a declared research role")
        result[role] = column.strip()
    execution_context = metadata.get("effect_execution_context")
    if execution_context is not None:
        try:
            parsed = EffectExecutionContext.model_validate(execution_context)
        except ValueError as exc:
            raise ParameterContractError("invalid effect_execution_context") from exc
        if (not parameters.get("treatment") or not parameters.get("target")
                or parsed.treatment_column != result.get("treatment")
                or parsed.outcome_column != result.get("target")):
            raise ParameterContractError("effect execution context must refer to the resolved research columns")
        request = result["effect_request"]
        if request is not None and parsed.treatment_unit != request["intervention"]["unit"]:
            raise ParameterContractError("effect execution context unit conflicts with the confirmed request")
        execution_context = parsed.model_dump(mode="json", exclude_none=True)
        # null 不能因序列化而变成算法层的 identity 默认值。
        execution_context.setdefault("treatment_transform", None)
        execution_context.setdefault("outcome_transform", None)
    # 不从同名列、自由文本或上次执行参数推断 identity。
    result["effect_execution_context"] = execution_context
    return result

def resolve_research_spec(spec: ResearchSpec, *, input_columns: list[str]) -> dict[str, Any]:
    if not input_columns or len(set(input_columns)) != len(input_columns):
        raise ParameterContractError("complete unique frozen input columns are required")
    result = spec.model_dump(mode="json", by_alias=True)
    if spec.effect_request is not None and (not spec.target or not spec.treatment):
        raise ParameterContractError("effect_request requires declared target and treatment roles")
    declarations = {}
    for item in spec.measurement_semantics:
        if not item.evidence.strip():
            raise ParameterContractError("measurement evidence must be non-blank")
        if item.scope == "all_input_columns":
            if item.columns:
                raise ParameterContractError("all_input_columns cannot contain named columns")
            columns = input_columns
        else:
            columns = item.columns
            if not columns:
                raise ParameterContractError("columns scope requires actual input column names")
        for column in columns:
            if column not in input_columns:
                raise ParameterContractError(f"measurement reference is not an input column: {column}")
            if column in declarations and declarations[column]["kind"] != item.kind:
                raise ParameterContractError("conflicting measurement declarations")
            declarations[column] = {"column": column, "kind": item.kind, "evidence": item.evidence}
    result["measurement_semantics"] = [declarations[key] for key in sorted(declarations)]
    return research_parameters(result)

def parameter_update_id(*, job_id: str, source_input_id: int, changes: Mapping[str, Any], reason: str) -> str:
    if type(source_input_id) is not int or source_input_id <= 0:
        raise ParameterContractError("source_input_id must identify a trusted input record")
    return _digest({"job_id": job_id, "source_input_id": source_input_id, "changes": changes, "reason": reason})

def validate_parameter_history(parameters: Mapping[str, Any], changes: Any, *, job_id: str) -> list[dict[str, Any]]:
    if not isinstance(parameters, Mapping) or "effect_request" not in parameters:
        raise UnsupportedProcessingCheckpointError("effect_request was not recorded; start a new analysis")
    current = research_parameters(parameters)
    if not isinstance(changes, list):
        raise ParameterContractError("parameter changes must be a list")
    for revision, record in enumerate(changes, 1):
        if not isinstance(record, dict) or set(record) != {"revision", "update_id", "source_input_id", "reason", "changes"}:
            raise ParameterContractError("invalid parameter revision record")
        if type(record["revision"]) is not int or record["revision"] != revision or record["reason"] not in RevisionReason.__args__:
            raise ParameterContractError("invalid parameter revision sequence or reason")
        if record["update_id"] != parameter_update_id(job_id=job_id, source_input_id=record["source_input_id"],
                                                     changes=record["changes"], reason=record["reason"]):
            raise ParameterContractError("invalid parameter update identity")
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

def commit_research_spec(parameters: Mapping[str, Any], changes: list[dict[str, Any]], *, spec: ResearchSpec,
                         input_columns: list[str], job_id: str, source_input_id: int,
                         expected_revision: int, reason: RevisionReason) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    history = validate_parameter_history(parameters, changes, job_id=job_id)
    if reason not in RevisionReason.__args__ or type(source_input_id) is not int or source_input_id <= 0:
        raise ParameterContractError("invalid research specification source or reason")
    updated = resolve_research_spec(spec, input_columns=input_columns)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ParameterContractError("invalid expected revision")
    if expected_revision != len(history):
        # Only the immediately committed identical snapshot is a safe replay.
        if expected_revision == len(history) - 1 and updated == research_parameters(parameters):
            last = history[-1]
            if last["source_input_id"] == source_input_id and last["reason"] == reason:
                return deepcopy(dict(parameters)), history
        raise ParameterContractError("research specification revision is stale")
    delta = {key: {"before": deepcopy(parameters.get(key)), "after": deepcopy(value)}
             for key, value in updated.items() if parameters.get(key) != value}
    if delta:
        history.append({"revision": len(history) + 1, "source_input_id": source_input_id,
            "reason": reason, "changes": delta,
            "update_id": parameter_update_id(job_id=job_id, source_input_id=source_input_id, changes=delta, reason=reason)})
    return updated, history

def parameter_binding(parameters: Mapping[str, Any], changes: Any, *, job_id: str) -> dict[str, Any]:
    history = validate_parameter_history(parameters, changes, job_id=job_id)
    return {"state_version": PROCESSING_STATE_VERSION, "parameters": research_parameters(parameters), "changes": history,
            "identity": parameter_identity(parameters), "revision": len(history)}


def restore_parameter_binding(metadata: Mapping[str, Any], *, job_id: str) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ParameterContractError("candidate metadata must be an object")
    binding = metadata.get(PARAMETER_BINDING_KEY)
    if not isinstance(binding, dict) or set(binding) != {"state_version", "parameters", "changes", "identity", "revision"}:
        raise ParameterContractError("candidate has no current parameter binding")
    if type(binding["state_version"]) is not int or binding["state_version"] != PROCESSING_STATE_VERSION:
        raise UnsupportedProcessingCheckpointError("legacy processing candidate requires a new analysis")
    expected = parameter_binding(binding["parameters"], binding["changes"], job_id=job_id)
    if type(binding["revision"]) is not int or binding != expected:
        raise ParameterContractError("candidate parameter identity conflicts with its snapshot")
    return {"analysis_parameters": expected["parameters"],
            "analysis_parameter_changes": expected["changes"]}


def historical_parameter_identity(metadata: Mapping[str, Any], *, job_id: str) -> str | None:
    """比较历史研究身份；旧版本不能证明相同，也不尝试恢复执行。"""
    binding = metadata.get(PARAMETER_BINDING_KEY)
    if not isinstance(binding, Mapping) or binding.get("state_version") != PROCESSING_STATE_VERSION:
        return None
    restored = restore_parameter_binding(metadata, job_id=job_id)
    return parameter_identity(restored["analysis_parameters"])


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
