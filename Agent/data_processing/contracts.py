"""预处理方案工具和确认载荷的扩展契约。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from Agent.effect_request import EffectRequest, EffectExecutionContext, normalize_effect_request
from Agent.deep_agent_tools.interaction import (
    DataProcessingPlanSummary, DataProcessingPlanConfirmationRequest,
)
from Agent.data_processing.time_axis import TimeSeriesWindow


class ProcessingPlanSummary(DataProcessingPlanSummary):
    """预处理方案保存已明确的列、业务单位和转换语义。"""

    effect_execution_context: EffectExecutionContext | None = None


class ResearchPlanSummary(ProcessingPlanSummary):
    """确认卡片的效应定义由研究参数投影，模型不能另行提交。"""

    effect_request: EffectRequest | None
    discovery_goal: Literal["time_series"] | None = None
    time_series: TimeSeriesWindow | None = None

    @model_validator(mode="after")
    def validate_time_series(self) -> ResearchPlanSummary:
        if (self.discovery_goal == "time_series") != (self.time_series is not None):
            raise ValueError("时序目标必须展示实际选段")
        return self


class ResearchPlanConfirmationRequest(DataProcessingPlanConfirmationRequest):
    plan: ResearchPlanSummary

    def public_payload(self) -> dict[str, Any]:
        payload = super().public_payload()
        payload["plan"] = self.plan.model_dump(mode="json", by_alias=True)
        if self.plan.time_series is None:
            payload["plan"].pop("discovery_goal")
            payload["plan"].pop("time_series")
        payload["plan"]["effect_request"] = normalize_effect_request(
            payload["plan"]["effect_request"],
        )
        if self.plan.effect_execution_context is not None:
            context = self.plan.effect_execution_context.model_dump(mode="json", exclude_none=True)
            for key in ("treatment_transform", "outcome_transform"):
                context.setdefault(key, None)
            payload["plan"]["effect_execution_context"] = context
        return payload
