"""共享 Deep Agent 工具 schema 辅助函数和数据处理问题契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .models import ContractModel
from .runtime_updates import with_tool_runtime_schema


DATA_PROCESSING_QUESTION_TYPE = "data_processing_question"
DATA_PROCESSING_PLAN_CONFIRMATION_TYPE = "data_processing_plan_confirmation"


class DataProcessingQuestionRequest(ContractModel):
    """数据处理 Agent 为语义澄清生成的开放式问题。"""

    type: Literal["data_processing_question"] = DATA_PROCESSING_QUESTION_TYPE
    prompt: str
    question_id: str
    expires_at: str
    allow_other: Literal[True] = True

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip() or len(value) > 3000:
            raise ValueError("prompt must be a non-blank bounded string")
        return value

    @field_validator("question_id", "expires_at")
    @classmethod
    def validate_question_metadata(cls, value: str, info: Any) -> str:
        if not value or value != value.strip() or len(value) > 255:
            raise ValueError(f"{info.field_name} must be a bounded trimmed string")
        return value

    def public_payload(self) -> dict[str, Any]:
        """生成现有 Job/SSE 可保存和回放的开放式问题载荷。"""
        return {
            "type": "interaction",
            "interaction_type": self.type,
            "question_id": self.question_id,
            "message": self.prompt,
            "expires_at": self.expires_at,
            "allow_other": True,
        }


class DerivedMeasurementSemantic(ContractModel):
    column: str = Field(min_length=1, max_length=255)
    kind: Literal["count", "ordinal", "category", "continuous", "binary", "unknown"]
    evidence: str = Field(min_length=1, max_length=600)

    @field_validator("column", "evidence")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("derived measurement text must be non-blank and trimmed")
        return value


class DataProcessingPlanSummary(ContractModel):
    """展示给用户确认的处理语义，不包含数据行或脚本正文。"""

    summary: str = Field(min_length=1, max_length=1200)
    observation_unit: str = Field(min_length=1, max_length=500)
    transformations: tuple[str, ...] = Field(min_length=1, max_length=12)
    variable_semantics: dict[str, str] = Field(min_length=1, max_length=128)
    analysis_columns: tuple[str, ...] = Field(
        min_length=1, max_length=128,
        description="最终分析 CSV 的完整列名及顺序；每列均参与建图，禁止写出辅助字段。",
    )
    derived_measurement_semantics: tuple[DerivedMeasurementSemantic, ...] = Field(
        default_factory=tuple, max_length=128,
        description="派生输出列的测量类型和转换依据；窗口内是否出现应声明 binary。",
    )
    role_columns: dict[Literal["target", "treatment"], str | None] = Field(
        default_factory=dict, max_length=2,
        description="用户已指定的 target、treatment 对应的处理后 CSV 列名；未指定的角色留空，不创造角色。",
    )
    measurement_columns: dict[str, str | None] = Field(
        default_factory=dict, max_length=128,
        description=(
            "原始输入列到保留或重命名后输出列的测量声明映射；null 表示明确删除该原始列，"
            "缺席表示默认同名保留。仅传递原始列已有的测量声明；聚合或计算派生列不得自动继承，"
            "应把被替换的原始列映射为 null。一个输出列不能继承相互冲突的输入测量声明。"
        ),
    )
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=12)

    @field_validator("analysis_columns")
    @classmethod
    def validate_analysis_columns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            not name.strip() or name != name.strip() or len(name) > 255 for name in value
        ):
            raise ValueError("analysis_columns must contain unique bounded trimmed names")
        return value

    @model_validator(mode="after")
    def validate_output_references(self):
        derived = [item.column for item in self.derived_measurement_semantics]
        if len(derived) != len(set(derived)) or set(derived) - set(self.analysis_columns):
            raise ValueError("derived measurements must uniquely reference analysis_columns")
        outputs = {value for value in self.measurement_columns.values() if value is not None}
        if outputs - set(self.analysis_columns) or outputs & set(derived):
            raise ValueError("inherited and derived measurements must refer to distinct analysis columns")
        return self

    @field_validator("measurement_columns")
    @classmethod
    def validate_measurement_columns(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        for source, output in value.items():
            for column in (source, output):
                if column is not None and (
                    not column.strip() or column != column.strip() or len(column) > 255
                ):
                    raise ValueError("measurement column must be a non-blank bounded trimmed string")
        return value


class DataProcessingPlanConfirmationRequest(ContractModel):
    """绑定一个精确方案版本的用户确认请求。"""

    type: Literal["data_processing_plan_confirmation"] = (
        DATA_PROCESSING_PLAN_CONFIRMATION_TYPE
    )
    prompt: str = Field(min_length=1, max_length=1200)
    question_id: str
    expires_at: str
    plan_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan: DataProcessingPlanSummary
    allow_other: Literal[True] = True

    @field_validator("question_id", "expires_at")
    @classmethod
    def validate_bounded_identity(cls, value: str, info: Any) -> str:
        if not value or value != value.strip() or len(value) > 255:
            raise ValueError(f"{info.field_name} must be a bounded trimmed string")
        return value

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("prompt must be a trimmed single-line string")
        return value

    def public_payload(self) -> dict[str, Any]:
        """生成可由 Job、SSE 与前端共同保存和校验的方案确认载荷。"""
        return {
            "type": "interaction",
            "interaction_type": self.type,
            "question_id": self.question_id,
            "message": self.prompt,
            "expires_at": self.expires_at,
            "allow_other": True,
            "plan_version": self.plan_version,
            "plan": self.plan.model_dump(mode="json"),
        }
