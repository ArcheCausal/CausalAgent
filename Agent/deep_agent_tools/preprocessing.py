"""冻结表格的预检测画像和已确认处理方案的确定性执行。

本模块只处理内存中的 CSV 文本，不读取文件、不访问数据库，也不调用模型。
外层准入、MCP 执行和测试共用同一实现，避免模型描述与真实处理不一致。
"""

from __future__ import annotations

import io
import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
import numpy as np

from .models import (
    DataIssue,
    DataProfile,
    PreprocessingPlan,
    PreprocessingStep,
)


class PreprocessingError(ValueError):
    """已确认方案无法在当前数据上执行；消息只描述确定性事实。"""


def _normalized_column_map(frame: pd.DataFrame) -> dict[str, str]:
    return {str(column): str(column) for column in frame.columns}


def _numeric_missing(frame: pd.DataFrame, column: str) -> tuple[bool, bool]:
    """返回该列是否存在缺失值、是否存在非有限值。"""

    series = frame[column]
    if not pd.api.types.is_numeric_dtype(series):
        return bool(series.isna().any()), False
    values = series.to_numpy(dtype=float, na_value=np.nan)
    missing = bool(np.isnan(values).any())
    non_finite = bool(np.isinf(values).any())
    return missing, non_finite


def build_data_profile(frame: pd.DataFrame) -> DataProfile:
    """从真实 DataFrame 生成算法选择与处理方案所需的确定性画像。"""

    columns = tuple(str(column) for column in frame.columns)
    numeric_columns: list[str] = []
    categorical_columns: list[str] = []
    missing_columns: list[str] = []
    non_finite_columns: list[str] = []
    empty_columns: list[str] = []
    constant_columns: list[str] = []
    suspected_id_columns: list[str] = []
    issues: list[DataIssue] = []

    for column in columns:
        series = frame[column]
        column_missing, column_non_finite = _numeric_missing(frame, column)
        if column_missing:
            missing_columns.append(column)
        if column_non_finite:
            non_finite_columns.append(column)
        non_null = series.dropna()
        if len(non_null) == 0:
            empty_columns.append(column)
            issues.append(
                DataIssue(
                    code="empty_column",
                    message=f"列 {column} 完全缺失，无法参与分析",
                    columns=(column,),
                    blocking=True,
                )
            )
            continue
        if int(non_null.nunique(dropna=True)) <= 1:
            constant_columns.append(column)
            issues.append(
                DataIssue(
                    code="constant_column",
                    message=f"列 {column} 为常数列或唯一值过少",
                    columns=(column,),
                    blocking=True,
                )
            )
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            numeric_columns.append(column)
        else:
            categorical_columns.append(column)

    if missing_columns:
        issues.append(
            DataIssue(
                code="missing_values",
                message="存在缺失值，需要确认处理方式：" + "、".join(missing_columns),
                columns=tuple(missing_columns),
                blocking=True,
            )
        )
    if non_finite_columns:
        issues.append(
            DataIssue(
                code="non_finite_values",
                message="存在非有限值，需要确认处理方式：" + "、".join(non_finite_columns),
                columns=tuple(non_finite_columns),
                blocking=True,
            )
        )
    if not columns:
        issues.append(
            DataIssue(
                code="no_columns",
                message="表格没有可分析的列",
                columns=(),
                blocking=True,
            )
        )

    return DataProfile(
        row_count=int(len(frame)),
        column_count=len(columns),
        column_names=columns,
        numeric_columns=tuple(numeric_columns),
        categorical_columns=tuple(categorical_columns),
        missing_columns=tuple(missing_columns),
        non_finite_columns=tuple(non_finite_columns),
        empty_columns=tuple(empty_columns),
        constant_columns=tuple(constant_columns),
        suspected_id_columns=tuple(suspected_id_columns),
        issues=tuple(issues),
    )


def build_data_profile_from_csv(csv_data: str) -> DataProfile:
    """从冻结 CSV 文本生成画像；解析失败转换为稳定处理错误。"""

    try:
        frame = pd.read_csv(io.StringIO(csv_data))
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError) as exc:
        raise PreprocessingError("CSV 无法解析") from exc
    return build_data_profile(frame)


def profile_from_analysis_parameters(
    analysis_parameters: Mapping[str, Any] | None,
) -> DataProfile | None:
    """从 fold 阶段已持久化的 analysis_parameters 重建画像。

    旧 checkpoint 没有新字段时返回 None，由调用方决定是否要求在恢复时
    重新读取冻结文件，而不是用不完整信息猜测。
    """

    if not isinstance(analysis_parameters, Mapping):
        return None
    raw_profile = analysis_parameters.get("data_profile")
    if isinstance(raw_profile, Mapping):
        return DataProfile.model_validate(raw_profile)
    columns = tuple(str(column) for column in analysis_parameters.get("columns", ()))
    column_profiles = analysis_parameters.get("column_profiles")
    if not isinstance(column_profiles, Mapping) or not columns:
        return None

    numeric_columns: list[str] = []
    categorical_columns: list[str] = []
    missing_columns: list[str] = []
    empty_columns: list[str] = []
    constant_columns: list[str] = []
    suspected_id_columns: list[str] = []
    for column in columns:
        profile = column_profiles.get(column)
        if not isinstance(profile, Mapping):
            continue
        inferred_type = str(profile.get("inferred_type") or "")
        features = profile.get("statistical_features") or {}
        if features.get("is_numeric") is True or inferred_type in {
            "numeric",
            "binary",
            "continuous",
            "categorical_numeric",
        }:
            numeric_columns.append(column)
        else:
            categorical_columns.append(column)
        if int(profile.get("missing_count") or 0) > 0:
            missing_columns.append(column)
        if inferred_type == "empty":
            empty_columns.append(column)
        if bool(profile.get("is_constant")):
            constant_columns.append(column)
        if bool(profile.get("possible_id")):
            suspected_id_columns.append(column)
    issues: list[DataIssue] = []
    if missing_columns:
        issues.append(
            DataIssue(
                code="missing_values",
                message="存在缺失值，需要确认处理方式：" + "、".join(missing_columns),
                columns=tuple(missing_columns),
                blocking=True,
            )
        )
    if empty_columns:
        issues.append(
            DataIssue(
                code="empty_column",
                message="存在完全缺失的列：" + "、".join(empty_columns),
                columns=tuple(empty_columns),
                blocking=True,
            )
        )
    if constant_columns:
        issues.append(
            DataIssue(
                code="constant_column",
                message="存在常数列：" + "、".join(constant_columns),
                columns=tuple(constant_columns),
                blocking=True,
            )
        )
    return DataProfile(
        row_count=max(0, int(analysis_parameters.get("n_rows") or 0)),
        column_count=len(columns),
        column_names=columns,
        numeric_columns=tuple(numeric_columns),
        categorical_columns=tuple(categorical_columns),
        missing_columns=tuple(missing_columns),
        empty_columns=tuple(empty_columns),
        constant_columns=tuple(constant_columns),
        suspected_id_columns=tuple(suspected_id_columns),
        issues=tuple(issues),
    )


def _require_columns(frame: pd.DataFrame, step: PreprocessingStep) -> None:
    missing = [column for column in step.columns if column not in frame.columns]
    if missing:
        raise PreprocessingError(
            "处理方案引用了不存在的列：" + "、".join(missing)
        )


def apply_preprocessing(
    csv_data: str,
    plan: PreprocessingPlan,
    *,
    protected_columns: Sequence[str] = (),
) -> tuple[str, dict[str, Any]]:
    """按固定顺序执行已确认方案，返回新 CSV 和可审计摘要。

    原 CSV 字符串不会被修改。未在方案中出现的操作不会被执行。
    """

    try:
        frame = pd.read_csv(io.StringIO(csv_data))
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError) as exc:
        raise PreprocessingError("CSV 无法解析") from exc
    original_columns = tuple(str(column) for column in frame.columns)
    execution_columns = original_columns
    if "select_columns" in plan.operations():
        select_step = next(
            step for step in plan.steps if step.operation == "select_columns"
        )
        removed = set(original_columns) - set(select_step.columns)
        if protected_columns and set(protected_columns) & removed:
            raise PreprocessingError("选列不能删除目标变量或处理变量")
        execution_columns = select_step.columns
        frame = frame.loc[:, list(execution_columns)].copy()
    original_rows = int(len(frame))
    encoded_mappings: dict[str, dict[str, int]] = {}
    imputed_columns: list[str] = []
    dropped_rows = 0

    for step in plan.steps:
        if step.operation == "select_columns":
            continue
        _require_columns(frame, step)
        if step.operation == "drop_rows":

            before = int(len(frame))
            frame = frame.dropna(subset=list(step.columns)).copy()
            dropped_rows += before - int(len(frame))
        elif step.operation == "numeric_impute":
            for column in step.columns:
                series = frame[column]
                if not pd.api.types.is_numeric_dtype(series):
                    raise PreprocessingError(
                        f"列 {column} 不是数值列，不能使用数值插补"
                    )
                numeric = pd.to_numeric(series, errors="coerce")
                if numeric.notna().sum() == 0:
                    raise PreprocessingError(
                        f"列 {column} 全部缺失，无法估计插补值"
                    )
                fill_value = (
                    float(numeric.mean())
                    if step.method == "mean"
                    else float(numeric.median())
                )
                if not math.isfinite(fill_value):
                    raise PreprocessingError(
                        f"列 {column} 无法得到有限插补值"
                    )
                frame[column] = numeric.fillna(fill_value)
                imputed_columns.append(column)
        elif step.operation == "categorical_encode":
            for column in step.columns:
                series = frame[column]
                if pd.api.types.is_numeric_dtype(series) and not series.isna().any():
                    continue
                categories = sorted(
                    {str(value) for value in series.dropna().unique()},
                    key=lambda value: value,
                )
                mapping = {value: index for index, value in enumerate(categories)}
                frame[column] = series.map(
                    lambda value: mapping.get(str(value)) if pd.notna(value) else value
                )
                encoded_mappings[column] = mapping

    if len(frame.columns) < 1:
        raise PreprocessingError("处理方案执行后没有可用列")
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    summary = {
        "version": plan.version,
        "plan_digest": plan.digest,
        "operations": list(plan.operations()),
        "original_row_count": original_rows,
        "result_row_count": int(len(frame)),
        "dropped_row_count": dropped_rows,
        "original_columns": list(original_columns),
        "result_columns": [str(column) for column in frame.columns],
        "imputed_columns": imputed_columns,
        "encoded_mappings": encoded_mappings,
    }
    return buffer.getvalue(), summary
