"""对持久化的候选 CSV 执行独立、确定性校验。"""

from __future__ import annotations

import ast
import csv
from io import BytesIO, TextIOWrapper
from typing import Any, Iterable, Mapping

import pandas as pd

from Agent.deep_agent_tools.algorithm_specs import AlgorithmSpec
from Agent.deep_agent_tools.preprocessing import build_data_profile
from Agent.measurement_semantics import normalize_measurement_semantics
from Agent.data_processing.parameters import PARAMETER_BINDING_KEY, parameter_identity


class DatasetValidationError(ValueError):
    """候选数据集不能进入因果分析。"""


class DatasetSizeLimitError(DatasetValidationError):
    """候选文件超过独立校验器可安全处理的规模。"""


MAX_DATASET_CELLS = 1_000_000


def validate_candidate(
    *,
    data_bytes: bytes,
    script_bytes: bytes,
    metadata: Mapping[str, Any],
    analysis_parameters: Mapping[str, Any],
    specifications: Iterable[AlgorithmSpec],
    max_bytes: int,
    max_rows: int = 100_000,
    max_columns: int = 1_000,
    max_cells: int = MAX_DATASET_CELLS,
    expected_parameter_identity: str | None = None,
) -> dict[str, Any]:
    """校验 MySQL 中候选产物本身，并报告各算法对实际输入的兼容性。

    此函数不执行脚本，不读取模型提供的校验结论，也不检查算法的科学假设。
    返回值只包含可持久化的结构化事实，不包含任何 CSV 单元格内容。
    """

    errors: list[dict[str, str]] = []
    if expected_parameter_identity is not None:
        binding = metadata.get(PARAMETER_BINDING_KEY) if isinstance(metadata, Mapping) else None
        if (not isinstance(binding, dict) or binding.get("identity") != expected_parameter_identity
                or parameter_identity(analysis_parameters) != expected_parameter_identity
                or parameter_identity(binding.get("parameters")) != expected_parameter_identity):
            return _report(errors=[_issue("parameter_identity_mismatch", "候选研究参数与父节点最终参数不一致")])
    if not isinstance(data_bytes, bytes) or not data_bytes:
        return _report(errors=[_issue("empty_file", "候选 CSV 文件为空")])
    if len(data_bytes) > max_bytes:
        return _report(errors=[_issue("file_too_large", "候选 CSV 超过文件大小限制")])
    if max_cells <= 0:
        raise ValueError("max_cells must be positive")
    if not isinstance(script_bytes, bytes) or not script_bytes:
        errors.append(_issue("script_missing", "候选处理脚本为空"))
    else:
        try:
            script_text = script_bytes.decode("utf-8")
            ast.parse(script_text, filename="processing_script.py", mode="exec")
        except (UnicodeDecodeError, SyntaxError, ValueError):
            errors.append(_issue("script_invalid", "候选处理脚本不是有效 UTF-8 Python 源码"))

    try:
        header, parsed_rows = _inspect_csv_shape(
            data_bytes,
            max_rows=max_rows,
            max_columns=max_columns,
            max_cells=max_cells,
        )
        frame = pd.read_csv(BytesIO(data_bytes), low_memory=False)
    except DatasetSizeLimitError as exc:
        errors.append(_issue("cell_count_limit_exceeded", str(exc)))
        return _report(errors=errors)
    except DatasetValidationError as exc:
        errors.append(_issue("csv_invalid", str(exc)))
        return _report(errors=errors)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError, ValueError):
        errors.append(_issue("csv_invalid", "候选文件无法按 UTF-8 CSV 解析"))
        return _report(errors=errors)

    columns = tuple(str(column) for column in frame.columns)
    if columns != tuple(header):
        errors.append(_issue("header_mismatch", "CSV 解析后的字段名与文件标题不一致"))
    if frame.empty:
        errors.append(_issue("no_data_rows", "候选 CSV 没有数据行"))
    if frame.shape[1] == 0:
        errors.append(_issue("no_columns", "候选 CSV 没有字段"))
    if parsed_rows != len(frame):
        errors.append(_issue("row_count_mismatch", "CSV 记录数与解析结果不一致"))

    profile = build_data_profile(frame)
    if not isinstance(metadata, Mapping):
        metadata = {}
        errors.append(_issue("metadata_invalid", "缺少处理语义说明"))
    summary = metadata.get("summary")
    observation_unit = metadata.get("observation_unit")
    transformations = metadata.get("transformations")
    variable_semantics = metadata.get("variable_semantics")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(_issue("summary_missing", "缺少数据处理说明"))
    if not isinstance(observation_unit, str) or not observation_unit.strip():
        errors.append(_issue("observation_unit_missing", "缺少观察单位说明"))
    if not isinstance(transformations, (list, tuple)) or not transformations:
        errors.append(_issue("transformations_missing", "缺少已执行转换说明"))
    if not isinstance(variable_semantics, Mapping):
        variable_semantics = {}
        errors.append(_issue("variable_semantics_invalid", "字段语义必须是字段名到说明的映射"))

    unknown_semantics = sorted(set(variable_semantics) - set(columns))
    if unknown_semantics:
        errors.append(_issue("unknown_semantic_columns", "字段语义引用了输出中不存在的字段"))

    parameters = analysis_parameters if isinstance(analysis_parameters, Mapping) else {}
    try:
        semantic_kinds = normalize_measurement_semantics(
            parameters.get("measurement_semantics") or [], columns=columns,
        )
    except ValueError:
        semantic_kinds = {}
        errors.append(_issue("measurement_semantics_invalid", "测量语义包含非法类型、字段或冲突声明"))
    target = _optional_column_name(parameters.get("target"))
    treatment = _optional_column_name(parameters.get("treatment"))
    if target and treatment and target == treatment:
        errors.append(_issue("target_treatment_conflict", "目标变量和处理变量不能是同一字段"))
    for role, column in (("target", target), ("treatment", treatment)):
        if column and column not in columns:
            errors.append(_issue(f"{role}_missing", f"处理后数据中缺少已声明的{_role_name(role)}"))
        elif column and not _has_semantics(column, variable_semantics, parameters):
            errors.append(_issue(f"{role}_semantics_missing", f"缺少{_role_name(role)}的测量含义"))

    declared_semantics = parameters.get("measurement_semantics") or []
    if isinstance(declared_semantics, (list, tuple)):
        for item in declared_semantics:
            if not isinstance(item, Mapping):
                errors.append(_issue("measurement_semantics_invalid", "用户声明的字段语义格式无效"))
                continue
            column = item.get("column")
            evidence = item.get("evidence")
            if not isinstance(column, str) or column not in columns:
                errors.append(_issue("measurement_column_missing", "用户声明的测量字段不在处理后数据中"))
            if not isinstance(evidence, str) or not evidence.strip():
                errors.append(_issue("measurement_evidence_missing", "字段测量含义缺少用户原话依据"))

    algorithm_compatibility: list[dict[str, Any]] = []
    compatible_count = 0
    for spec in specifications:
        if not spec.enabled or spec.availability != "available":
            algorithm_compatibility.append(
                {
                    "capability_id": spec.capability_id,
                    "status": "unavailable",
                    "reason": "算法当前未启用或不可用",
                }
            )
            continue
        applicability, reason = spec.assess_applicability(
            profile=profile,
            execution_columns=profile.column_names,
            semantic_kinds=semantic_kinds,
        )
        status = "compatible" if applicability == "ready" else "incompatible"
        compatible_count += int(applicability == "ready")
        algorithm_compatibility.append(
            {"capability_id": spec.capability_id, "status": status, "reason": reason}
        )
    if not algorithm_compatibility or not any(
        item["status"] == "compatible" for item in algorithm_compatibility
    ):
        errors.append(_issue("no_compatible_algorithm", "当前候选数据不满足任何可用算法的输入要求"))

    report = {
        "accepted": not errors,
        "errors": errors,
        "data_profile": profile.model_dump(mode="json"),
        "row_count": int(frame.shape[0]),
        "column_count": int(frame.shape[1]),
        "cell_count": int(frame.shape[0] * frame.shape[1]),
        "columns": list(columns),
        "semantic_kinds": semantic_kinds,
        "observation_unit": observation_unit if isinstance(observation_unit, str) else None,
        "transformations": list(transformations) if isinstance(transformations, (list, tuple)) else [],
        "target": target,
        "treatment": treatment,
        "algorithm_compatibility": algorithm_compatibility,
        "compatible_algorithm_count": compatible_count,
        "scope_note": "输入兼容检查不证明算法科学假设成立，也不证明因果结论正确。",
    }
    return report


def _inspect_csv_shape(
    data: bytes,
    *,
    max_rows: int,
    max_columns: int,
    max_cells: int,
) -> tuple[list[str], int]:
    try:
        text = TextIOWrapper(BytesIO(data), encoding="utf-8-sig", newline="")
        reader = csv.reader(text, strict=True)
        header = next(reader)
        if not header or len(header) > max_columns:
            raise DatasetValidationError("CSV 字段数量无效或超过限制")
        if any(not value.strip() for value in header):
            raise DatasetValidationError("CSV 包含空字段名")
        if len(set(header)) != len(header):
            raise DatasetValidationError("CSV 包含重复字段名")
        count = 0
        for row in reader:
            count += 1
            if count > max_rows:
                raise DatasetValidationError("CSV 数据行数超过限制")
            if count * len(header) > max_cells:
                raise DatasetSizeLimitError("CSV 数据单元格总数超过独立校验限制")
            if len(row) != len(header):
                raise DatasetValidationError("CSV 中存在字段数不一致的数据行")
        return header, count
    except DatasetValidationError:
        raise
    except (csv.Error, StopIteration, UnicodeDecodeError) as exc:
        raise DatasetValidationError("候选文件无法按 UTF-8 CSV 解析") from exc
    finally:
        if "text" in locals():
            text.close()


def _optional_column_name(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _has_semantics(column: str, semantics: Mapping[str, Any], parameters: Mapping[str, Any]) -> bool:
    value = semantics.get(column)
    if isinstance(value, str) and value.strip():
        return True
    declarations = parameters.get("measurement_semantics") or []
    return any(
        isinstance(item, Mapping)
        and item.get("column") == column
        and isinstance(item.get("evidence"), str)
        and bool(item["evidence"].strip())
        for item in declarations
    ) if isinstance(declarations, (list, tuple)) else False


def _role_name(role: str) -> str:
    return "目标变量" if role == "target" else "处理变量"


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _report(*, errors: list[dict[str, str]]) -> dict[str, Any]:
    return {"accepted": False, "errors": errors, "algorithm_compatibility": []}
