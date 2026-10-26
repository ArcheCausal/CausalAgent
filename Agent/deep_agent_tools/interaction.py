"""共享 Deep Agent 工具 schema 辅助函数和数据处理问题契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

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


class DataProcessingPlanSummary(ContractModel):
    """展示给用户确认的处理语义，不包含数据行或脚本正文。"""

    summary: str = Field(min_length=1, max_length=1200)
    observation_unit: str = Field(min_length=1, max_length=500)
    transformations: tuple[str, ...] = Field(min_length=1, max_length=12)
    variable_semantics: dict[str, str] = Field(min_length=1, max_length=128)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=12)


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
