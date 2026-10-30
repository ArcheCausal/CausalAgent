"""效应请求的共享纯定义；研究角色继续引用已有 target 和 treatment。"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator


Unit = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Population = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
InterventionValue = Annotated[FiniteFloat, Field(strict=True)]
Column = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class FixedContrast(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["fixed_contrast"]
    from_value: InterventionValue = Field(alias="from")
    to: InterventionValue
    unit: Unit = Field(description="已明确或经用户确认的处理变量单位；无量纲时明确写出无量纲。")


class AdditiveShift(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["additive_shift"]
    delta: InterventionValue
    unit: Unit = Field(description="已明确或经用户确认的处理变量单位；无量纲时明确写出无量纲。")


Intervention = Annotated[FixedContrast | AdditiveShift, Field(discriminator="type")]


class EffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    effect: Literal["average_total_effect"]
    intervention: Intervention
    target_population: Population | None = Field(
        default=None,
        description="仅在用户要求额外限定目标观测子群时填写筛选限制；使用全部已确认观测时省略或为 null。不是参与结构探索的列范围。",
    )


class TreatmentTransformation(BaseModel):
    """已确认的处理列数值语义；affine 为 output = scale * source + offset。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["identity", "affine", "nonlinear"]
    source_column: Column
    output_column: Column
    source_unit: Unit
    output_unit: Unit
    scale: InterventionValue | None = None
    offset: InterventionValue | None = None
    description: Population | None = None

    @model_validator(mode="after")
    def validate_transformation(self) -> TreatmentTransformation:
        if self.type == "identity":
            if self.source_unit != self.output_unit or self.scale is not None or self.offset is not None:
                raise ValueError("identity requires equal units and no affine coefficients")
        elif self.type == "affine":
            if self.scale is None or self.scale == 0 or self.offset is None:
                raise ValueError("affine requires finite nonzero scale and finite offset")
        elif self.description is None or self.scale is not None or self.offset is not None:
            raise ValueError("nonlinear requires a description and no affine coefficients")
        return self


class OutcomeTransformation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["identity", "affine", "nonlinear"]
    description: Population | None = None

    @model_validator(mode="after")
    def validate_description(self) -> OutcomeTransformation:
        if self.type == "identity" and self.description is not None:
            raise ValueError("identity outcome transform has no additional fields")
        if self.type != "identity" and self.description is None:
            raise ValueError("transformed outcome requires an explicit description")
        return self


class EffectExecutionContext(BaseModel):
    """已确认方案的列和转换事实；未知转换明确为 null。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    treatment_column: Column
    outcome_column: Column
    treatment_unit: Unit
    treatment_transform: TreatmentTransformation | None
    outcome_transform: OutcomeTransformation | None

    @model_validator(mode="after")
    def validate_mapping(self) -> EffectExecutionContext:
        transform = self.treatment_transform
        if transform is not None and (transform.output_column != self.treatment_column
                                      or transform.source_unit != self.treatment_unit):
            raise ValueError("treatment transform conflicts with actual column or business unit")
        if self.treatment_column == self.outcome_column:
            raise ValueError("treatment and outcome columns must differ")
        return self


def normalize_effect_request(value: Any) -> dict[str, Any] | None:
    """严格验证显式 null 或请求对象，返回使用 JSON 字段名的规范值。

    调用者必须单独检查字段是否存在；旧记录缺字段不能通过传入 None 升级。
    """
    if value is None:
        return None
    return EffectRequest.model_validate(value).model_dump(mode="json", by_alias=True, exclude_none=True)
