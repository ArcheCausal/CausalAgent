"""Deep Agent 的本地数据处理确认工具。

工具只生成候选方案并触发 LangGraph interrupt，不调用 MCP 预览，也不执行
清洗。用户答复通过原生 resume 返回；获批方案在运行时 State 中形成凭证，
由后续算法调用核对后注入。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .models import (
    ContractModel,
    DataProfile,
    PreprocessingApproval,
    PreprocessingPlan,
    PreprocessingStep,
    canonical_json_bytes,
)
from .registry import AlgorithmRegistry
from .runtime_updates import (
    emit_public_decision_event,
    emit_tool_lifecycle_event,
    resolve_runtime_invocation,
    with_tool_runtime_schema,
)

if TYPE_CHECKING:
    from Agent.deep_agent.context import AgentRunContext


DATA_PROCESSING_INTERACTION_TYPE = "data_processing_request"
DATA_PROCESSING_TOOL_NAME = "request_data_processing"
DATA_PROCESSING_TIMEOUT_SECONDS = 300


def build_option_id(*, capability_id: str, plan_digest: str) -> str:
    """按算法与方案内容生成稳定选项标识。"""

    raw = f"{capability_id}:{plan_digest}".encode("utf-8")
    return "opt_" + hashlib.sha256(raw).hexdigest()[:24]


class ProcessingOption(ContractModel):
    """一个可提交给用户的处理或诊断选项。"""

    option_id: str
    label: str
    capability_id: str
    plan: PreprocessingPlan = Field(default_factory=PreprocessingPlan)
    impact: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    @field_validator("option_id", "label", "capability_id")
    @classmethod
    def validate_strings(cls, value: str, info: Any) -> str:
        if not value or not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-blank trimmed string")
        return value

    @model_validator(mode="after")
    def validate_option_identity(self) -> "ProcessingOption":
        expected = build_option_id(
            capability_id=self.capability_id,
            plan_digest=self.plan.digest,
        )
        if self.option_id != expected:
            raise ValueError("option_id does not match capability and plan content")
        return self


class DataProcessingRequest(ContractModel):
    """进入 interrupt 的公开确认载荷；不包含文件正文或内部标识。"""

    type: Literal["data_processing_request"] = DATA_PROCESSING_INTERACTION_TYPE
    prompt: str
    algorithm_name: str = ""
    question_id: str = ""
    expires_at: str = ""
    options: tuple[ProcessingOption, ...]
    allow_other: bool = True
    expires_in_seconds: int = Field(default=DATA_PROCESSING_TIMEOUT_SECONDS, gt=0)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip() or len(value) > 4000:
            raise ValueError("prompt must be a non-blank bounded string")
        return value

    @field_validator("algorithm_name")
    @classmethod
    def validate_algorithm_name(cls, value: str) -> str:
        if not value:
            return value
        if value != value.strip() or len(value) > 200:
            raise ValueError("algorithm_name must be a bounded trimmed string")
        return value

    @field_validator("question_id", "expires_at")
    @classmethod
    def validate_public_metadata(cls, value: str) -> str:
        if not value:
            return value
        if value != value.strip() or len(value) > 255:
            raise ValueError("public interaction metadata must be a bounded trimmed string")
        return value

    @model_validator(mode="after")
    def validate_options(self) -> "DataProcessingRequest":
        if not 1 <= len(self.options) <= 3:
            raise ValueError("data processing request requires 1 to 3 options")
        ids = [option.option_id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("data processing options must be unique")
        return self

    def public_payload(self, *, question_id: str, expires_at: datetime) -> dict[str, Any]:
        """生成允许进入 SSE 与历史回放的公开结构。"""

        return {
            "type": "interaction",
            "interaction_type": DATA_PROCESSING_INTERACTION_TYPE,
            "question_id": question_id or self.question_id,
            "message": self.prompt,
            "algorithm_name": self.algorithm_name,
            "expires_at": self.expires_at
            or expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "allow_other": self.allow_other,
            "options": [
                {
                    "option_id": option.option_id,
                    "label": option.label,
                    "capability_id": option.capability_id,
                    "steps": [
                        {
                            "operation": step.operation,
                            "columns": list(step.columns),
                            "method": step.method,
                        }
                        for step in option.plan.steps
                    ],
                    "impact": list(option.impact),
                    "risks": list(option.risks),
                }
                for option in self.options
            ],
        }


class DataProcessingInputOption(ContractModel):
    """模型提交的一个候选方案；选项标识和确认凭证由后端生成。"""

    label: str = Field(
        min_length=1,
        max_length=200,
        description="面向用户的简短方案名称，说明采用什么处理。",
    )
    steps: tuple[PreprocessingStep, ...] = Field(
        min_length=1,
        description="按 select_columns、drop_rows、numeric_impute、categorical_encode 的顺序排列。",
    )
    impact: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "处理影响。只有已经计算的内容才能写精确数字；确认前未执行预览时写“未计算”。"
        ),
    )
    risks: tuple[str, ...] = Field(
        default_factory=tuple,
        description="这次处理可能引入的研究风险。",
    )

    @model_validator(mode="after")
    def validate_steps(self) -> "DataProcessingInputOption":
        PreprocessingPlan(steps=self.steps)
        return self


class DataProcessingInput(ContractModel):
    """request_data_processing 的模型可见输入；不包含后端生成的确认字段。"""

    capability_id: str = Field(
        min_length=1,
        description="准备执行的算法 capability_id。",
    )
    reason: str = Field(
        min_length=1,
        max_length=4000,
        description="为什么当前数据不能直接执行该算法。",
    )
    options: tuple[DataProcessingInputOption, ...] = Field(
        min_length=1,
        max_length=3,
        description="1 到 3 个候选处理方案；每个方案只使用该算法允许的操作和真实列名。",
    )

    @model_validator(mode="after")
    def validate_option_identity(self) -> "DataProcessingInput":
        for option in self.options:
            if option.label != option.label.strip():
                raise ValueError("option label must not contain surrounding whitespace")
        return self


def parse_selection(
    answer: Any,
    *,
    options: tuple[ProcessingOption, ...],
) -> tuple[ProcessingOption | None, str | None]:
    """解析用户答复；返回 (选项, 其他文本)。"""

    if isinstance(answer, str):
        text = answer.strip()
        if not text:
            return None, ""
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, Mapping):
            answer = decoded
        else:
            option = next(
                (item for item in options if item.option_id == text),
                None,
            )
            return (option, None) if option is not None else (None, text)
    if not isinstance(answer, Mapping):
        return None, str(answer)
    selected = answer.get("option_id")
    if isinstance(selected, str):
        option = next(
            (item for item in options if item.option_id == selected),
            None,
        )
        if option is None:
            raise ValueError("选择的处理方案不属于当前问题")
        return option, None
    other = answer.get("other_text")
    if isinstance(other, str):
        return None, other.strip()
    raise ValueError("用户答复既没有选项标识，也没有其他说明")


def validate_option_against_registry(
    *,
    option: ProcessingOption,
    registry: AlgorithmRegistry,
    profile: DataProfile | None,
    protected_columns: tuple[str, ...],
) -> None:
    """校验方案是否使用了该算法允许的列和操作。"""

    try:
        entry = registry.get(option.capability_id)
    except Exception as exc:
        raise ValueError("处理方案引用了未注册的算法") from exc
    entry.spec.validate_preprocessing_plan(
        plan=option.plan,
        profile=profile,
        protected_columns=protected_columns,
    )


class DataProcessingTool:
    """生成并持久化一次处理确认；独占一个工具调用轮次。"""

    name = DATA_PROCESSING_TOOL_NAME

    def __init__(self, *, registry: AlgorithmRegistry) -> None:
        self.registry = registry

    def schema(self) -> dict[str, Any]:
        parameters = DataProcessingInput.model_json_schema()
        parameters.pop("title", None)
        parameters.pop("description", None)
        return {
            "name": self.name,
            "description": (
                "当算法要求的数据条件与当前画像不一致时，向用户提交 1 到 3 个"
                "受控处理或诊断选项。选项只使用算法 Spec 允许的操作和真实列名；"
                "无法准确计算的影响必须标注“未计算”；dropna 只删除缺失值，"
                "不会删除正负无穷值。调用后本轮会暂停等待用户。"
            ),
            "parameters": parameters,
        }

    def to_langchain_tool(self) -> Any:
        from langchain.tools import ToolRuntime
        from langchain_core.tools import StructuredTool
        from langgraph.types import Command, interrupt

        async def call(runtime: Any, **kwargs: Any) -> Any:
            identity = resolve_runtime_invocation(runtime)
            state = runtime.state
            if not isinstance(state, Mapping):
                raise RuntimeError("ToolRuntime.state must be a mapping")
            from langchain_core.messages import ToolMessage
            profile_value = state.get("data_profile")
            profile = (
                profile_value
                if isinstance(profile_value, DataProfile)
                else DataProfile.model_validate(profile_value)
                if isinstance(profile_value, Mapping)
                else None
            )
            analysis_parameters = state.get("analysis_parameters")
            protected: list[str] = []
            if isinstance(analysis_parameters, Mapping):
                for key in ("target", "treatment"):
                    value = analysis_parameters.get(key)
                    if isinstance(value, str) and value in (profile.column_names if profile else ()):
                        protected.append(value)
            try:
                request_input = DataProcessingInput.model_validate(kwargs)
            except ValidationError as exc:
                await emit_tool_lifecycle_event(
                    runtime,
                    event_type="tool_call_result",
                    identity=identity,
                    tool_name=self.name,
                    status="failed",
                    safe_error_code="DATA_PROCESSING_INPUT_INVALID",
                )
                return ToolMessage(
                    content=json.dumps(
                        {
                            "status": "invalid_input",
                            "reason": "处理方案参数不符合要求，请按字段说明修正后重试。",
                            "errors": _validation_diagnostics(exc),
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=identity.provider_call_id,
                    name=self.name,
                    status="error",
                )
            try:
                spec_entry = self.registry.get(request_input.capability_id)
            except Exception:
                await emit_tool_lifecycle_event(
                    runtime,
                    event_type="tool_call_result",
                    identity=identity,
                    tool_name=self.name,
                    status="failed",
                    safe_error_code="UNKNOWN_CAPABILITY",
                )
                return ToolMessage(
                    content=json.dumps(
                        {
                            "status": "invalid_input",
                            "reason": "处理方案引用了未注册的算法 capability_id。",
                            "errors": ["capability_id"],
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=identity.provider_call_id,
                    name=self.name,
                    status="error",
                )
            spec = spec_entry.spec
            if profile is None:
                await emit_tool_lifecycle_event(
                    runtime,
                    event_type="tool_call_result",
                    identity=identity,
                    tool_name=self.name,
                    status="failed",
                    safe_error_code="DATA_PROFILE_UNAVAILABLE",
                )
                return ToolMessage(
                    content=json.dumps(
                        {
                            "status": "invalid_input",
                            "reason": "当前缺少数据画像，无法生成受控处理方案。",
                            "errors": ["data_profile"],
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=identity.provider_call_id,
                    name=self.name,
                    status="error",
                )
            options: list[ProcessingOption] = []
            plan_digests: set[str] = set()
            for index, raw in enumerate(request_input.options):
                plan = PreprocessingPlan(
                    steps=raw.steps,
                    protected_columns=tuple(protected),
                )
                if plan.digest in plan_digests:
                    return ToolMessage(
                        content=json.dumps(
                            {
                                "status": "invalid_input",
                                "reason": "候选处理方案不能重复；请为每个选项提供不同的步骤。",
                                "errors": ["options"],
                            },
                            ensure_ascii=False,
                        ),
                        tool_call_id=identity.provider_call_id,
                        name=self.name,
                        status="error",
                    )
                plan_digests.add(plan.digest)
                option = ProcessingOption(
                    option_id=build_option_id(
                        capability_id=spec.capability_id,
                        plan_digest=plan.digest,
                    ),
                    label=raw.label,
                    capability_id=spec.capability_id,
                    plan=plan,
                    impact=tuple(str(item) for item in raw.impact),
                    risks=tuple(str(item) for item in raw.risks),
                )
                try:
                    validate_option_against_registry(
                        option=option,
                        registry=self.registry,
                        profile=profile,
                        protected_columns=tuple(protected),
                    )
                except ValueError as exc:
                    await emit_tool_lifecycle_event(
                        runtime,
                        event_type="tool_call_result",
                        identity=identity,
                        tool_name=self.name,
                        status="failed",
                        safe_error_code="PREPROCESSING_PLAN_INVALID",
                    )
                    return ToolMessage(
                        content=json.dumps(
                            {
                                "status": "invalid_input",
                                "reason": str(exc),
                                "errors": [f"options[{index}].steps"],
                            },
                            ensure_ascii=False,
                        ),
                        tool_call_id=identity.provider_call_id,
                        name=self.name,
                        status="error",
                    )
                options.append(option)
            request = DataProcessingRequest(
                prompt=request_input.reason.strip()
                or "当前数据需要确认处理方式后才能继续。",
                algorithm_name=spec.public_name,
                options=tuple(options),
            )
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_start",
                identity=identity,
                tool_name=self.name,
            )
            answer = interrupt(
                request.public_payload(
                    question_id=identity.provider_call_id,
                    expires_at=confirmation_expiry(),
                )
            )
            selected, other_text = parse_selection(answer, options=request.options)
            if other_text is not None:
                await emit_tool_lifecycle_event(
                    runtime,
                    event_type="tool_call_result",
                    identity=identity,
                    tool_name=self.name,
                    status="succeeded",
                )
                from langchain_core.messages import ToolMessage

                return ToolMessage(
                    content=json.dumps(
                        {
                            "status": "needs_replanning",
                            "message": (
                                "用户没有选择已有方案，请根据这条说明重新生成受控选项："
                                + other_text[:1000]
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=identity.provider_call_id,
                    name=self.name,
                )
            assert selected is not None
            approval = PreprocessingApproval(
                option_id=selected.option_id,
                # 使用框架注入的 ToolRuntime.tool_call_id，与父图发布的 question_id 一致。
                question_id=identity.provider_call_id,
                plan_digest=selected.plan.digest,
            )
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_result",
                identity=identity,
                tool_name=self.name,
                status="succeeded",
            )
            from langchain_core.messages import ToolMessage

            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "status": "approved",
                                    "option_id": selected.option_id,
                                    "plan": selected.plan.model_dump(mode="json"),
                                    "message": "用户已确认该处理方案，请携带同一方案调用算法工具。",
                                },
                                ensure_ascii=False,
                            ),
                            tool_call_id=identity.provider_call_id,
                            name=self.name,
                        )
                    ],
                    "data_processing_approvals": {
                        selected.option_id: {
                            "approval": approval.model_dump(mode="json"),
                            "plan": selected.plan.model_dump(mode="json"),
                            "capability_id": selected.capability_id,
                        }
                    },
                }
            )

        call.__annotations__["runtime"] = ToolRuntime
        runtime_args_schema = with_tool_runtime_schema(
            DataProcessingInput,
            tool_name=self.name,
            tool_runtime_type=ToolRuntime,
        )
        return StructuredTool.from_function(
            coroutine=call,
            name=self.name,
            description=self.schema()["description"],
            args_schema=runtime_args_schema,
        )


def _validation_diagnostics(exc: ValidationError) -> list[str]:
    """只公开字段路径和稳定错误类型，不暴露模型原文或 Python 对象。"""

    return [
        ".".join(str(item) for item in error["loc"]) + ":" + str(error["type"])
        for error in exc.errors(include_url=False, include_input=False)
    ]


def find_approved_plan(
    *,
    state: Mapping[str, Any],
    capability_id: str,
    plan: PreprocessingPlan | None,
) -> tuple[PreprocessingApproval, PreprocessingPlan] | None:
    """从 State 中找出与算法和方案内容一致的确认凭证。"""

    approvals = state.get("data_processing_approvals")
    if not isinstance(approvals, Mapping):
        return None
    for option_id, raw in approvals.items():
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("capability_id") or "") != capability_id:
            continue
        plan_value = raw.get("plan")
        if not isinstance(plan_value, Mapping):
            continue
        approved_plan = PreprocessingPlan.model_validate(plan_value)
        if plan is not None and approved_plan.digest != plan.digest:
            continue
        approval = PreprocessingApproval.model_validate(raw.get("approval"))
        if option_id != approval.option_id:
            continue
        if approval.option_id != build_option_id(
            capability_id=capability_id,
            plan_digest=approved_plan.digest,
        ):
            continue
        return approval, approved_plan
    return None


@dataclass(frozen=True)
class ApprovedPlanResolution:
    """一次算法调用解析到的唯一批准方案，或需要模型补充的信息。"""

    status: Literal["resolved", "missing", "ambiguous"]
    entry: tuple[PreprocessingApproval, PreprocessingPlan] | None = None


def resolve_approved_plan(
    *,
    state: Mapping[str, Any],
    capability_id: str,
    plan: PreprocessingPlan | None,
) -> ApprovedPlanResolution:
    """按算法和方案摘要解析唯一批准记录；省略方案时只在无歧义时自动取用。"""

    if plan is not None:
        entry = find_approved_plan(
            state=state,
            capability_id=capability_id,
            plan=plan,
        )
        return ApprovedPlanResolution(
            status="resolved" if entry is not None else "missing",
            entry=entry,
        )
    matches = _approved_plan_entries(
        state=state,
        capability_id=capability_id,
    )
    if len(matches) == 1:
        return ApprovedPlanResolution(status="resolved", entry=matches[0])
    if len(matches) > 1:
        return ApprovedPlanResolution(status="ambiguous")
    return ApprovedPlanResolution(status="missing")


def _approved_plan_entries(
    *,
    state: Mapping[str, Any],
    capability_id: str,
) -> tuple[tuple[PreprocessingApproval, PreprocessingPlan], ...]:
    """读取同一算法下内容互不相同的批准记录。"""

    approvals = state.get("data_processing_approvals")
    if not isinstance(approvals, Mapping):
        return ()
    by_digest: dict[str, tuple[PreprocessingApproval, PreprocessingPlan]] = {}
    for option_id, raw in approvals.items():
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("capability_id") or "") != capability_id:
            continue
        plan_value = raw.get("plan")
        if not isinstance(plan_value, Mapping):
            continue
        approved_plan = PreprocessingPlan.model_validate(plan_value)
        approval = PreprocessingApproval.model_validate(raw.get("approval"))
        if option_id != approval.option_id or approval.plan_digest != approved_plan.digest:
            continue
        if approval.option_id != build_option_id(
            capability_id=capability_id,
            plan_digest=approved_plan.digest,
        ):
            continue
        by_digest.setdefault(approved_plan.digest, (approval, approved_plan))
    return tuple(by_digest[key] for key in sorted(by_digest))


def confirmation_expiry(now: datetime | None = None) -> datetime:
    """返回以 UTC 计算的确认截止时间。"""

    current = now or datetime.now(timezone.utc)
    return current + timedelta(seconds=DATA_PROCESSING_TIMEOUT_SECONDS)

