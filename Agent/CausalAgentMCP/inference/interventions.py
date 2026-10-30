"""已确认干预的 JSON 校验、原始单位支持检查和实际样本身份。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from Agent.effect_request import normalize_effect_request


EFFECT_REASONS = {
    "EFFECT_NOT_REQUESTED": "没有已确认的效应请求，不能执行效应估计。",
    "EFFECT_CONTEXT_MISSING": "缺少已确认的实际列、单位或数值转换信息。",
    "EFFECT_CONTEXT_MISMATCH": "请求与实际列映射、单位或已确认定义不一致。",
    "EFFECT_TRANSFORM_UNSUPPORTED": "当前数值转换不能安全应用原始单位的干预。",
    "EFFECT_OUTSIDE_SUPPORT": "干预超出观测处理范围；未截断、删行或外推。",
    "EFFECT_POPULATION_UNSUPPORTED": "当前估计器未验证目标子群筛选，不能以全部样本代替指定总体。",
}


class InterventionNotApplicable(ValueError):
    def __init__(self, reason_code: str, *, support: dict[str, Any] | None = None) -> None:
        super().__init__(EFFECT_REASONS[reason_code])
        self.reason_code = reason_code
        self.support = {
            **(support or {}),
            "reason_code": reason_code,
            "applicability": "not_applicable",
            "status": "not_applicable",
            "conditional_overlap": "not_assessed",
        }


def validate_effect_request(value: Any) -> dict[str, Any]:
    """只接受严格 JSON 对象；不补猜对比或把字符串数值转换成数值。"""
    normalized = normalize_effect_request(value)
    if normalized is None:
        raise ValueError("effect_request must be a confirmed JSON object")
    return normalized


def sample_identity(frame: pd.DataFrame) -> str:
    """对实际估计列、行序及数值内容作散列；不保存原始记录。"""
    header = json.dumps(list(frame.columns), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(header.encode("utf-8"))
    values = np.asarray(frame.to_numpy(dtype=float), dtype="<f8", order="C")
    digest.update(str(values.shape).encode("ascii"))
    digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


def prepare_intervention(
    *,
    treatment_values: np.ndarray,
    treatment: str,
    outcome: str,
    effect_request: Any,
    execution_context: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray, dict[str, Any]]:
    """返回执行尺度上的基线/干预值；范围检查始终使用原始业务单位。"""
    if effect_request is None:
        raise InterventionNotApplicable("EFFECT_NOT_REQUESTED")
    request = validate_effect_request(effect_request)
    if request.get("target_population"):
        raise InterventionNotApplicable(
            "EFFECT_POPULATION_UNSUPPORTED",
            support={"target_population": request["target_population"], "population_verified": False},
        )
    intervention = request["intervention"]
    if not isinstance(execution_context, Mapping) or not {
        "treatment_column", "outcome_column", "treatment_unit", "treatment_transform",
    }.issubset(execution_context):
        raise InterventionNotApplicable("EFFECT_CONTEXT_MISSING")
    if (
        execution_context["treatment_column"] != treatment
        or execution_context["outcome_column"] != outcome
        or execution_context["treatment_unit"] != intervention["unit"]
        or ("effect_request" in execution_context
            and execution_context["effect_request"] != request)
    ):
        raise InterventionNotApplicable("EFFECT_CONTEXT_MISMATCH")
    transform = execution_context["treatment_transform"]
    if not isinstance(transform, Mapping):
        raise InterventionNotApplicable("EFFECT_TRANSFORM_UNSUPPORTED")
    # outcome 为变换尺度时，均值差不能简单逆变换成原始单位。
    if execution_context.get("outcome_transform") != {"type": "identity"}:
        raise InterventionNotApplicable("EFFECT_TRANSFORM_UNSUPPORTED")
    kind = transform.get("type")
    transform_keys = set(transform) - {
        "source_column", "output_column", "source_unit", "output_unit", "description",
    }
    if (transform.get("output_column", treatment) != treatment
        or transform.get("source_unit", intervention["unit"]) != intervention["unit"]):
        raise InterventionNotApplicable("EFFECT_CONTEXT_MISMATCH")
    if kind == "identity" and transform_keys == {"type"}:
        scale, offset = 1.0, 0.0
    elif kind == "affine" and transform_keys == {"type", "scale", "offset"}:
        scale, offset = transform["scale"], transform["offset"]
        if (type(scale) not in (int, float) or type(offset) not in (int, float)
            or not np.isfinite([scale, offset]).all() or scale == 0):
            raise InterventionNotApplicable("EFFECT_TRANSFORM_UNSUPPORTED")
    else:
        # 对数、未知或复合转换不得直接套用原始业务值。
        raise InterventionNotApplicable("EFFECT_TRANSFORM_UNSUPPORTED")
    raw_values = (treatment_values - offset) / scale
    if len(raw_values) == 0 or not np.isfinite(raw_values).all():
        raise InterventionNotApplicable("EFFECT_TRANSFORM_UNSUPPORTED")
    low, high = float(raw_values.min()), float(raw_values.max())
    if intervention["type"] == "fixed_contrast":
        baseline = np.full(len(raw_values), intervention["from"], dtype=float)
        changed = np.full(len(raw_values), intervention["to"], dtype=float)
    else:
        baseline = raw_values.copy()
        changed = raw_values + intervention["delta"]
    outside = (baseline < low) | (baseline > high) | (changed < low) | (changed > high)
    count = int(outside.sum())
    support = {
        "observed_min": low, "observed_max": high,
        "intervention_min": float(changed.min()),
        "intervention_max": float(changed.max()),
        "out_of_range_count": count, "out_of_range_fraction": count / len(raw_values),
        "n_rows": len(raw_values), "unit": intervention["unit"],
        "treatment_column": treatment, "outcome_column": outcome,
        "treatment_transform": dict(transform),
        "scope": "global_observed_range_only",
        "conditional_overlap": "not_assessed",
        "extrapolation": False, "dropped_row_count": 0, "clipped_value_count": 0,
    }
    if count:
        raise InterventionNotApplicable("EFFECT_OUTSIDE_SUPPORT", support=support)
    support["applicability"] = "ready"
    support["status"] = "supported"
    return request, baseline * scale + offset, changed * scale + offset, support
