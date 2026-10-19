"""Deep Agent 的本地数据处理确认工具。

工具只生成候选方案并触发 LangGraph interrupt，不调用 MCP 预览，也不执行
清洗。用户答复通过原生 resume 返回；获批方案在运行时 State 中形成凭证，
由后续算法调用核对后注入。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator, model_validator

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
    options: tuple[ProcessingOption, ...]
    allow_other: bool = True
    expires_in_seconds: int = Field(default=DATA_PROCESSING_TIMEOUT_SECONDS, gt=0)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip() or len(value) > 4000:
            raise ValueError("prompt must be a non-blank bounded string")
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
            "question_id": question_id,
            "message": self.prompt,
            "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
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
    spec = entry.spec
    operations = set(option.plan.operations())
    unsupported = operations - set(spec.allowed_preprocessing)
    if unsupported:
        raise ValueError(
            "该算法不允许的处理操作：" + "、".join(sorted(unsupported))
        )
    if profile is None:
        raise ValueError("缺少数据画像，无法校验处理方案")
    known_columns = set(profile.column_names)
    for step in option.plan.steps:
        unknown = set(step.columns) - known_columns
        if unknown:
            raise ValueError("处理方案引用了不存在的列：" + "、".join(sorted(unknown)))
        if step.operation == "numeric_impute":
            non_numeric = set(step.columns) - set(profile.numeric_columns)
            if non_numeric:
                raise ValueError(
                    "数值插补只能用于数值列：" + "、".join(sorted(non_numeric))
                )
        if step.operation == "drop_rows" and set(step.columns) == known_columns:
            raise ValueError("删行必须指定具体列，不能默认对全表删除")
    # target/treatment 可以显式插补或编码，但不能因为缺失值被删行。
    for step in option.plan.steps:
        if step.operation == "drop_rows" and set(protected_columns) & set(step.columns):
            raise ValueError("不能按目标变量或处理变量的缺失值删除整行")


class DataProcessingTool:
    """生成并持久化一次处理确认；独占一个工具调用轮次。"""

    name = DATA_PROCESSING_TOOL_NAME

    def __init__(self, *, registry: AlgorithmRegistry) -> None:
        self.registry = registry

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "当算法要求的数据条件与当前画像不一致时，向用户提交 1 到 3 个"
                "受控处理或诊断选项。选项只使用算法 Spec 允许的操作和真实列名；"
                "无法准确计算的影响必须标注“未计算”。调用后本轮会暂停等待用户。"
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "capability_id": {
                        "type": "string",
                        "description": "准备执行的算法 capability_id。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "为什么当前数据不能直接执行该算法。",
                    },
                    "options": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "label": {"type": "string"},
                                "steps": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "operation": {
                                                "type": "string",
                                                "enum": [
                                                    "select_columns",
                                                    "drop_rows",
                                                    "numeric_impute",
                                                    "categorical_encode",
                                                ],
                                            },
                                            "columns": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "method": {
                                                "type": ["string", "null"],
                                                "enum": ["mean", "median", "ordinal", None],
                                            },
                                        },
                                        "required": ["operation", "columns"],
                                    },
                                },
                                "impact": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "risks": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["label", "steps"],
                        },
                    },
                },
                "required": ["capability_id", "reason", "options"],
            },
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
            spec_entry = self.registry.get(str(kwargs.get("capability_id") or ""))
            spec = spec_entry.spec
            options: list[ProcessingOption] = []
            for raw in kwargs.get("options") or []:
                steps = tuple(
                    PreprocessingStep.model_validate(step)
                    for step in (raw.get("steps") or [])
                )
                plan = PreprocessingPlan(
                    steps=steps,
                    protected_columns=tuple(protected),
                )
                option = ProcessingOption(
                    option_id=build_option_id(
                        capability_id=spec.capability_id,
                        plan_digest=plan.digest,
                    ),
                    label=str(raw.get("label") or ""),
                    capability_id=spec.capability_id,
                    plan=plan,
                    impact=tuple(str(item) for item in raw.get("impact") or []),
                    risks=tuple(str(item) for item in raw.get("risks") or []),
                )
                validate_option_against_registry(
                    option=option,
                    registry=self.registry,
                    profile=profile,
                    protected_columns=tuple(protected),
                )
                options.append(option)
            request = DataProcessingRequest(
                prompt=str(kwargs.get("reason") or "").strip()
                or "当前数据需要确认处理方式后才能继续。",
                options=tuple(options),
            )
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_start",
                identity=identity,
                tool_name=self.name,
            )
            answer = interrupt(request.model_dump(mode="json"))
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
        return StructuredTool.from_function(
            coroutine=call,
            name=self.name,
            description=self.schema()["description"],
        )


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
    for raw in approvals.values():
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
        return PreprocessingApproval.model_validate(raw.get("approval")), approved_plan
    return None


def confirmation_expiry(now: datetime | None = None) -> datetime:
    """返回以 UTC 计算的确认截止时间。"""

    current = now or datetime.now(timezone.utc)
    return current + timedelta(seconds=DATA_PROCESSING_TIMEOUT_SECONDS)

