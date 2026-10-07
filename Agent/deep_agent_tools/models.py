"""Deep Agent P1 共享领域模型、传输上下文和状态 reducer。

本模块只保存跨 Deep Agent、Adapter 和 MCP executor 共享的纯契约。
它不导入 LangGraph/Deep Agents，不访问数据库、文件系统或网络；这样 P1
可以在运行时依赖完成前独立验证，并避免把传输实现反向写进领域模型。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .error_codes import SafeErrorCode, is_server_output_error, is_user_input_error


class ContractModel(BaseModel):
    """所有共享契约的共同 Pydantic 配置。"""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
    )


class PublicDecision(ContractModel):
    """模型在 Tool Call 中显式生成、可直接展示的简短决策说明。"""

    summary: str = Field(min_length=1, max_length=400)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("summary must not contain surrounding whitespace")
        if any(ord(character) < 32 for character in value):
            raise ValueError("summary must be a single line without control characters")
        return value


def _non_blank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")
    return value


def _canonical_uuid(value: str | UUID, *, field_name: str) -> str:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a canonical UUID value")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a canonical UUID value") from exc


def validate_result_ref_for_invocation(result_ref: str, invocation_id: str) -> str:
    """验证结果引用只属于指定 invocation 且使用规范的非负索引。"""

    prefix = f"{invocation_id}:"
    if not result_ref.startswith(prefix):
        raise ValueError("result_ref must be derived from invocation_id")
    result_index = result_ref[len(prefix) :]
    if (
        not result_index
        or not result_index.isascii()
        or not result_index.isdigit()
        or str(int(result_index)) != result_index
    ):
        raise ValueError("result_ref must end with a canonical non-negative result index")
    return result_ref


AlgorithmResultStatus: TypeAlias = Literal[
    "valid",
    "invalid_input",
    "not_applicable",
    "not_ready",
    "execution_failed",
    "timed_out",
]

ActionAttemptStatus: TypeAlias = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "not_ready",
    "canceled",
    "discarded",
]

InvocationStatus: TypeAlias = Literal[
    "pending",
    "succeeded",
    "failed",
    "timed_out",
    "not_ready",
    "canceled",
    "discarded",
]

ResponseIdentitySource: TypeAlias = Literal[
    "provider_response_id",
    "message_execution_id",
]

FinalizationStatus: TypeAlias = Literal["valid", "degraded"]


class GraphEdge(ContractModel):
    """标准化图中的一条有向或部分定向边。"""

    source: str
    target: str
    edge_type: str = "directed"
    weight: float | None = None

    _validate_source = field_validator("source", "target")(
        lambda value, info: _non_blank(value, field_name=info.field_name)
    )


class StandardizedGraph(ContractModel):
    """Adapter 输出的最小统一图外壳。

    具体算法可以在 diagnostics 中保留额外说明，但主图的节点、边和语义
    必须经过 Adapter 的硬契约校验后才能进入 ``AlgorithmResult``。
    """

    graph_semantics: str
    nodes: list[str] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    @field_validator("graph_semantics")
    @classmethod
    def validate_graph_semantics(cls, value: str) -> str:
        return _non_blank(value, field_name="graph_semantics")

    @field_validator("nodes")
    @classmethod
    def validate_nodes(cls, value: list[str]) -> list[str]:
        normalized = [_non_blank(node, field_name="node") for node in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("graph nodes must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_edge_nodes(self) -> "StandardizedGraph":
        node_set = set(self.nodes)
        missing = {
            endpoint
            for edge in self.edges
            for endpoint in (edge.source, edge.target)
            if endpoint not in node_set
        }
        if missing:
            raise ValueError(
                "graph edges reference unknown nodes: " + ", ".join(sorted(missing))
            )
        return self


class Diagnostics(ContractModel):
    """对模型安全的执行诊断；不允许把原始堆栈或文件正文放进来。"""

    summary: str | None = None
    sample_count: int | None = Field(default=None, ge=0)
    variable_count: int | None = Field(default=None, ge=0)
    assumptions_checked: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    safe_error_code: SafeErrorCode | str | None = None

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        return None if value is None else _non_blank(value, field_name="summary")


class SafeWarning(ContractModel):
    """可进入 State/报告的受控 warning。"""

    code: SafeErrorCode | str
    message: str

    _validate_message = field_validator("message")(
        lambda value, info: _non_blank(value, field_name=info.field_name)
    )


class AlgorithmResultProvenance(ContractModel):
    """结果归属、输入身份和 spec 版本的可信证明字段。"""

    job_id: str
    attempt_count: int = Field(ge=0)
    lease_epoch: int = Field(ge=0)
    input_identity: str
    spec_digest: str
    invocation_id: str | None = None
    capability_id: str | None = None
    capability_version: str | None = None
    preprocessing_recipe_digest: str | None = None
    algorithm_runner_version: str | None = None

    @model_validator(mode="after")
    def validate_non_blank_fields(self) -> "AlgorithmResultProvenance":
        for field_name in (
            "job_id",
            "input_identity",
            "spec_digest",
            "invocation_id",
            "capability_id",
            "capability_version",
            "preprocessing_recipe_digest",
            "algorithm_runner_version",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _non_blank(value, field_name=field_name)
        return self


class AlgorithmResult(ContractModel):
    """Adapter 对外发布的统一算法结果。"""

    result_ref: str
    invocation_id: str
    provider_call_id: str
    capability_id: str
    capability_version: str
    status: AlgorithmResultStatus
    standardized_graph: StandardizedGraph | None = None
    graph_semantics: str | None = None
    summary: str | None = None
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
    warnings: list[SafeWarning] = Field(default_factory=list)
    raw_result_ref: str | None = None
    raw_result_sha256: str | None = None
    raw_result_size_bytes: int | None = Field(default=None, ge=0)
    raw_result_serialization_version: str | None = None
    input_identity: str | None = None
    provenance: AlgorithmResultProvenance

    @field_validator(
        "result_ref",
        "invocation_id",
        "provider_call_id",
        "capability_id",
        "capability_version",
    )
    @classmethod
    def validate_identity_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        return None if value is None else _non_blank(value, field_name="summary")

    @model_validator(mode="after")
    def validate_result_contract(self) -> "AlgorithmResult":
        validate_result_ref_for_invocation(self.result_ref, self.invocation_id)
        if self.status == "valid" and self.standardized_graph is None:
            raise ValueError("valid AlgorithmResult must include standardized_graph")

        raw_fields = (
            self.raw_result_ref,
            self.raw_result_sha256,
            self.raw_result_size_bytes,
            self.raw_result_serialization_version,
        )
        if any(value is not None for value in raw_fields) and not all(
            value is not None for value in raw_fields
        ):
            raise ValueError("raw result metadata must be complete or absent")
        if self.raw_result_ref is not None and not self.raw_result_ref.startswith(
            "/raw_algorithm_results/"
        ):
            raise ValueError("raw_result_ref must use the protected virtual path")
        if self.status != "valid" and self.diagnostics.safe_error_code is None:
            raise ValueError("non-valid AlgorithmResult must include safe_error_code")
        safe_error_code = self.diagnostics.safe_error_code
        if safe_error_code is not None:
            if self.status == "invalid_input" and is_server_output_error(safe_error_code):
                raise ValueError(
                    "server output/transport errors must not be classified as invalid_input"
                )
            if self.status == "execution_failed" and is_user_input_error(safe_error_code):
                raise ValueError(
                    "user input errors must not be classified as execution_failed"
                )

        if self.graph_semantics is None and self.standardized_graph is not None:
            self.graph_semantics = self.standardized_graph.graph_semantics
        if self.input_identity is None:
            self.input_identity = self.provenance.input_identity
        elif self.input_identity != self.provenance.input_identity:
            raise ValueError("input_identity must equal provenance.input_identity")

        if self.provenance.invocation_id not in (None, self.invocation_id):
            raise ValueError("provenance.invocation_id does not match invocation_id")
        if self.provenance.capability_id not in (None, self.capability_id):
            raise ValueError("provenance.capability_id does not match capability_id")
        if self.provenance.capability_version not in (
            None,
            self.capability_version,
        ):
            raise ValueError("provenance.capability_version does not match result")
        return self

    @property
    def graph(self) -> StandardizedGraph | None:
        """产品语义中的 ``graph`` 访问器，canonical 字段仍为 standardized_graph。"""

        return self.standardized_graph


class ActionAttempt(ContractModel):
    """一次实际执行 attempt 的单调 revision 快照。"""

    retry_ordinal: int = Field(ge=0)
    revision: int = Field(ge=0)
    status: ActionAttemptStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    safe_error_code: SafeErrorCode | str | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> "ActionAttempt":
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class InvocationRecord(ContractModel):
    """一个逻辑 function call 及其全部 retry/revision 历史。"""

    invocation_id: str
    response_identity: str
    response_identity_source: ResponseIdentitySource
    provider_response_id: str | None = None
    provider_item_id: str | None = None
    provider_call_id: str
    tool_name: str
    final_status: InvocationStatus
    result_ref: str | None = None
    # 这些字段允许旧 checkpoint 被读取，但 FinalizationGate 对缺失值
    # fail-closed。新产生的 Ledger 记录必须完整填写它们。
    job_id: str | None = None
    attempt_count: int | None = Field(default=None, ge=0)
    lease_epoch: int | None = Field(default=None, ge=0)
    worker_id: str | None = None
    input_identity: str | None = None
    attempts: dict[int, ActionAttempt] = Field(default_factory=dict)

    @field_validator(
        "invocation_id",
        "response_identity",
        "provider_call_id",
        "tool_name",
    )
    @classmethod
    def validate_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator(
        "provider_response_id",
        "provider_item_id",
        "result_ref",
        "job_id",
        "worker_id",
        "input_identity",
    )
    @classmethod
    def validate_optional_strings(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _non_blank(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_identity_and_attempt_keys(self) -> "InvocationRecord":
        if self.response_identity_source == "provider_response_id":
            if self.provider_response_id is None:
                raise ValueError(
                    "provider_response_id is required for provider response identity"
                )
            if self.response_identity != self.provider_response_id:
                raise ValueError("response_identity must equal provider_response_id")
        else:
            if self.provider_response_id is not None:
                raise ValueError(
                    "provider_response_id must be absent when using local fallback"
                )

        for retry_ordinal, attempt in self.attempts.items():
            if retry_ordinal != attempt.retry_ordinal:
                raise ValueError("attempt map key must equal retry_ordinal")
        if self.job_id is not None:
            _canonical_uuid(self.job_id, field_name="job_id")
        return self


class EvidenceResult(ContractModel):
    """RAG evidence 的不可变 State 记录。"""

    evidence_ref: str
    evidence_id: str | None = None
    snippet: str
    source_title: str | None = None
    source_url: str | None = None
    locator: str | None = None
    modality: str | None = None
    score: float | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    rerank_score: float | None = None
    sufficiency: str | None = None
    release_id: str | None = None
    degradation_flags: tuple[str, ...] = ()

    @field_validator("evidence_ref", "snippet")
    @classmethod
    def validate_evidence_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator("evidence_id", "modality", "sufficiency")
    @classmethod
    def validate_optional_evidence_strings(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _non_blank(value, field_name=info.field_name)


class WebEvidenceResult(EvidenceResult):
    """Web evidence 在通用证据外增加 provider 状态和抓取时间。"""

    provider_status: str
    fetched_at: datetime

    @field_validator("provider_status")
    @classmethod
    def validate_provider_status(cls, value: str) -> str:
        return _non_blank(value, field_name="provider_status")


class DataProfile(ContractModel):
    """外层准入阶段生成、供 Agent 选择算法使用的数据画像。"""

    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    column_names: tuple[str, ...] = ()
    numeric_columns: tuple[str, ...] = ()
    categorical_columns: tuple[str, ...] = ()


class ScientificConflict(ContractModel):
    conflict_id: str
    result_refs: tuple[str, ...] = Field(
        default=(),
        description=(
            "冲突涉及的算法结果 result_ref；只能引用本次运行返回的算法结果，"
            "不要写入检索证据引用。"
        ),
    )
    summary: str
    resolution: str | None = None

    @field_validator("conflict_id", "summary")
    @classmethod
    def validate_conflict_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)


class RevisionProposal(ContractModel):
    """针对单个算法结果的修订建议；只作报告说明，不替换算法主图。"""

    result_ref: str = Field(
        description=(
            "被建议修订的算法结果 result_ref；只能引用本次运行返回的算法结果。"
        ),
    )
    action: Literal["retain", "orient", "reverse", "remove", "uncertain"]
    rationale: str
    evidence_refs: tuple[str, ...] = Field(
        default=(),
        description=(
            "支撑该建议的检索证据引用；只能填 rag_evidence_search 或 "
            "web_evidence_search 返回的 evidence_ref。算法结果之间互相印证请写进 "
            "rationale，不要填在这里。"
        ),
    )

    @field_validator("result_ref", "rationale")
    @classmethod
    def validate_revision_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)


class ResultAssessment(ContractModel):
    """对单个算法结果的取舍记录；只能引用算法结果。"""

    result_ref: str = Field(
        description=(
            "被取舍的算法结果 result_ref；只能引用本次运行返回的算法结果，"
            "检索证据不要写进这个字段。"
        ),
    )
    disposition: Literal["primary", "supporting", "discarded"]
    rationale: str

    @field_validator("result_ref", "rationale")
    @classmethod
    def validate_assessment_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)


class FinalAnalysisDecision(ContractModel):
    """ToolStrategy 的静态终态契约；动态归属由后续 FinalizationGate 校验。"""

    outcome: Literal["evidence_only", "algorithm_supported", "no_valid_algorithm"]
    primary_result_ref: str | None = Field(
        default=None,
        description="主结果 result_ref；必须是本次运行返回且状态有效的算法结果。",
    )
    result_assessments: list[ResultAssessment] = Field(
        default_factory=list,
        description=(
            "逐算法结果的唯一取舍；每个有效算法结果恰好一条。检索证据引用不属于"
            "这里，只能出现在 revision_proposals.evidence_refs。"
        ),
    )
    conflict_status: Literal["none", "resolved", "unresolved"]
    conflicts: list[ScientificConflict] = Field(default_factory=list)
    revision_proposals: list[RevisionProposal] = Field(default_factory=list)
    selection_rationale: str
    confidence: Literal["low", "medium", "high"]
    confidence_basis: list[str] = Field(default_factory=list)

    @field_validator("primary_result_ref")
    @classmethod
    def validate_primary_ref(cls, value: str | None) -> str | None:
        return None if value is None else _non_blank(value, field_name="primary_result_ref")

    @field_validator("selection_rationale")
    @classmethod
    def validate_selection_rationale(cls, value: str) -> str:
        return _non_blank(value, field_name="selection_rationale")

    @model_validator(mode="after")
    def validate_static_decision_shape(self) -> "FinalAnalysisDecision":
        refs = [assessment.result_ref for assessment in self.result_assessments]
        if len(refs) != len(set(refs)):
            raise ValueError("result_assessments must not repeat result_ref")

        primary_assessments = [
            assessment
            for assessment in self.result_assessments
            if assessment.disposition == "primary"
        ]
        if len(primary_assessments) > 1:
            raise ValueError("at most one result assessment may be primary")
        if self.primary_result_ref is not None:
            if not primary_assessments or (
                primary_assessments[0].result_ref != self.primary_result_ref
            ):
                raise ValueError(
                    "primary_result_ref must point to the sole primary assessment"
                )
        if self.outcome in {"evidence_only", "no_valid_algorithm"}:
            if self.primary_result_ref is not None or primary_assessments:
                raise ValueError(
                    f"{self.outcome} cannot declare a primary algorithm result"
                )
        if self.outcome == "algorithm_supported" and self.primary_result_ref is None:
            raise ValueError("algorithm_supported requires a primary_result_ref")
        if self.conflict_status == "none" and self.conflicts:
            raise ValueError("conflict_status=none cannot include conflicts")
        return self


class McpInvocationContext(ContractModel):
    """worker 注入、MCP 验签并再次查库的可信调用上下文。"""

    invocation_id: str
    job_id: str
    session_id: str
    user_id: int = Field(gt=0)
    attempt_count: int = Field(ge=0)
    lease_epoch: int = Field(ge=0)
    worker_id: str
    input_snapshot_digest: str
    issued_at: datetime
    expires_at: datetime
    key_id: str

    @field_validator("invocation_id", "job_id", "session_id")
    @classmethod
    def validate_uuid_fields(cls, value: str, info: Any) -> str:
        return _canonical_uuid(value, field_name=info.field_name)

    @field_validator("worker_id", "input_snapshot_digest", "key_id")
    @classmethod
    def validate_context_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def validate_aware_datetime(cls, value: datetime, info: Any) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_time_window(self) -> "McpInvocationContext":
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self


class AlgorithmExecutionCommand(ContractModel):
    """传输无关的算法执行请求；可信身份不由模型参数提供。"""

    invocation_id: str
    capability_id: str
    capability_version: str
    spec_digest: str
    provider_call_id: str
    input_identity: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    result_index: int = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)

    @field_validator(
        "invocation_id",
        "capability_id",
        "capability_version",
        "spec_digest",
        "provider_call_id",
        "input_identity",
    )
    @classmethod
    def validate_command_strings(cls, value: str, info: Any) -> str:
        return _non_blank(value, field_name=info.field_name)


class RawResultMetadata(ContractModel):
    """canonical raw JSON 的可审计元数据。"""

    raw_result_ref: str
    raw_result_sha256: str
    raw_result_size_bytes: int = Field(ge=0)
    raw_result_serialization_version: str


RAW_RESULT_SERIALIZATION_VERSION = "canonical-json-v1"


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (UUID, datetime)):
        return str(value)
    raise TypeError(f"unsupported value for canonical JSON: {type(value)!r}")


def canonical_json_bytes(value: Any) -> bytes:
    """生成稳定、无空白、UTF-8 的 canonical JSON bytes。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def build_raw_result_metadata(
    *, raw_result_ref: str, raw_result: Any
) -> RawResultMetadata:
    if not raw_result_ref.startswith("/raw_algorithm_results/"):
        raise ValueError("raw_result_ref must use the protected virtual path")
    payload = canonical_json_bytes(raw_result)
    return RawResultMetadata(
        raw_result_ref=raw_result_ref,
        raw_result_sha256=hashlib.sha256(payload).hexdigest(),
        raw_result_size_bytes=len(payload),
        raw_result_serialization_version=RAW_RESULT_SERIALIZATION_VERSION,
    )


def build_diagnostics_from_runner_payload(
    payload: Mapping[str, Any] | None,
) -> Diagnostics:
    """把 runner payload 的 diagnostics 映射到统一 ``Diagnostics``。

    runner 使用 ``n_samples``/``n_features`` 这类自有键名，worker 与 causal-mcp
    必须按同一规则映射，否则诊断字段会在一侧静默丢失。只接受标量，runner 的
    原始 payload 内容不会进入 ``Diagnostics``。
    """

    if not isinstance(payload, Mapping):
        return Diagnostics()
    raw = payload.get("diagnostics")
    if not isinstance(raw, Mapping):
        return Diagnostics()

    def _count(*keys: str) -> int | None:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    summary = raw.get("summary")
    assumptions = raw.get("assumptions_checked")
    if not isinstance(assumptions, (list, tuple)) or isinstance(assumptions, str):
        checked: list[str] = []
    else:
        checked = [
            item for item in assumptions if isinstance(item, str) and item.strip()
        ]

    return Diagnostics(
        summary=summary.strip() if isinstance(summary, str) and summary.strip() else None,
        sample_count=_count("n_samples", "sample_count"),
        variable_count=_count("n_features", "variable_count"),
        assumptions_checked=checked,
        metrics={
            key: value
            for key, value in raw.items()
            if isinstance(key, str)
            and (value is None or isinstance(value, (float, int, str, bool)))
        },
    )


class ReducerConflictError(ValueError):
    """同一不可变 key/revision 收到不同内容时抛出。"""


def _canonical_model_dump(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _coerce_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return {} if value is None else value


def merge_algorithm_results(
    left: Mapping[str, AlgorithmResult] | None,
    right: Mapping[str, AlgorithmResult] | None,
) -> dict[str, AlgorithmResult]:
    """按不可变 ``result_ref`` 合并 AlgorithmResult。"""

    merged: dict[str, AlgorithmResult] = {}
    for key, raw_value in _coerce_mapping(left).items():
        value = AlgorithmResult.model_validate(raw_value)
        if key != value.result_ref:
            raise ValueError("algorithm result map key must equal result_ref")
        merged[key] = value.model_copy(deep=True)

    for key, raw_value in _coerce_mapping(right).items():
        value = AlgorithmResult.model_validate(raw_value)
        if key != value.result_ref:
            raise ValueError("algorithm result map key must equal result_ref")
        if key in merged:
            if _canonical_model_dump(merged[key]) != _canonical_model_dump(value):
                raise ReducerConflictError(
                    f"algorithm result {key!r} is immutable and changed during replay"
                )
            continue
        merged[key] = value.model_copy(deep=True)
    return {key: merged[key] for key in sorted(merged)}


def _coerce_evidence(value: Any) -> EvidenceResult | WebEvidenceResult:
    if isinstance(value, (EvidenceResult, WebEvidenceResult)):
        return value.model_copy(deep=True)
    if isinstance(value, Mapping) and (
        "provider_status" in value or "fetched_at" in value
    ):
        return WebEvidenceResult.model_validate(value)
    return EvidenceResult.model_validate(value)


def merge_evidence_results(
    left: Mapping[str, EvidenceResult | WebEvidenceResult] | None,
    right: Mapping[str, EvidenceResult | WebEvidenceResult] | None,
) -> dict[str, EvidenceResult | WebEvidenceResult]:
    """按不可变 ``evidence_ref`` 合并 RAG/Web evidence。"""

    merged: dict[str, EvidenceResult | WebEvidenceResult] = {}
    for source in (_coerce_mapping(left), _coerce_mapping(right)):
        for key, raw_value in source.items():
            value = _coerce_evidence(raw_value)
            if key != value.evidence_ref:
                raise ValueError("evidence map key must equal evidence_ref")
            if key in merged:
                if _canonical_model_dump(merged[key]) != _canonical_model_dump(value):
                    raise ReducerConflictError(
                        f"evidence {key!r} is immutable and changed during replay"
                    )
                continue
            merged[key] = value
    return {key: merged[key] for key in sorted(merged)}


def _record_progress(record: InvocationRecord) -> tuple[int, int]:
    if not record.attempts:
        return (-1, -1)
    return max(
        (attempt.retry_ordinal, attempt.revision)
        for attempt in record.attempts.values()
    )


def _immutable_record_dump(record: InvocationRecord) -> dict[str, Any]:
    return record.model_dump(
        mode="json", exclude={"attempts", "final_status", "result_ref"}
    )


def _merge_invocation_records(
    current: InvocationRecord, incoming: InvocationRecord
) -> InvocationRecord:
    if _immutable_record_dump(current) != _immutable_record_dump(incoming):
        raise ReducerConflictError(
            f"invocation {current.invocation_id!r} identity changed during replay"
        )

    merged_attempts: dict[int, ActionAttempt] = {
        ordinal: attempt.model_copy(deep=True)
        for ordinal, attempt in current.attempts.items()
    }
    for ordinal, incoming_attempt in incoming.attempts.items():
        current_attempt = merged_attempts.get(ordinal)
        if current_attempt is None:
            merged_attempts[ordinal] = incoming_attempt.model_copy(deep=True)
            continue
        if incoming_attempt.revision > current_attempt.revision:
            merged_attempts[ordinal] = incoming_attempt.model_copy(deep=True)
        elif incoming_attempt.revision < current_attempt.revision:
            continue
        elif _canonical_model_dump(current_attempt) != _canonical_model_dump(
            incoming_attempt
        ):
            raise ReducerConflictError(
                f"invocation {current.invocation_id!r} retry {ordinal} has conflicting revision"
            )

    current_progress = _record_progress(current)
    incoming_progress = _record_progress(incoming)
    if incoming_progress > current_progress:
        final_status = incoming.final_status
        result_ref = incoming.result_ref
    elif incoming_progress < current_progress:
        final_status = current.final_status
        result_ref = current.result_ref
    else:
        if (
            current.final_status != incoming.final_status
            or current.result_ref != incoming.result_ref
        ):
            raise ReducerConflictError(
                f"invocation {current.invocation_id!r} terminal projection changed at the same revision"
            )
        final_status = current.final_status
        result_ref = current.result_ref

    return current.model_copy(
        deep=True,
        update={
            "attempts": {
                ordinal: merged_attempts[ordinal]
                for ordinal in sorted(merged_attempts)
            },
            "final_status": final_status,
            "result_ref": result_ref,
        },
    )


def merge_action_ledger(
    left: Mapping[str, InvocationRecord] | None,
    right: Mapping[str, InvocationRecord] | None,
) -> dict[str, InvocationRecord]:
    """按 invocation/attempt/revision 单调合并 Action Ledger。

    旧 revision 只能被忽略，不能覆盖新 revision；同一 revision 的不同内容
    视为 checkpoint 一致性错误。所有 retry ordinal 都保留，不使用 list append。
    """

    merged: dict[str, InvocationRecord] = {}
    for source in (_coerce_mapping(left), _coerce_mapping(right)):
        for key, raw_value in source.items():
            value = InvocationRecord.model_validate(raw_value)
            if key != value.invocation_id:
                raise ValueError("action ledger map key must equal invocation_id")
            if key not in merged:
                merged[key] = value.model_copy(deep=True)
            else:
                merged[key] = _merge_invocation_records(merged[key], value)
    return {key: merged[key] for key in sorted(merged)}
