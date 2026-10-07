"""CDFM runner：只负责 CSV、CPU 推理和私有 raw payload 转换。"""

from __future__ import annotations

import csv
import importlib.metadata
import io
import os
from collections.abc import Sequence
from typing import Any

import numpy as np


CDFM_RUNNER_VERSION = "cdfm-runner-v1"
CDFM_ALGORITHM = "cdfm"


class _CdfmInputError(ValueError):
    """CDFM 输入无法满足 runner 契约。"""


class _CdfmResultError(ValueError):
    """CDFM 返回结构无法满足 runner 契约。"""


def _failure(error_type: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": "causal_discovery_v1",
        "success": False,
        "algorithm": CDFM_ALGORITHM,
        "runner_version": CDFM_RUNNER_VERSION,
        "error_type": error_type,
        "message": message,
    }


def _parse_csv(csv_data_string: str) -> tuple[np.ndarray, list[str], np.ndarray]:
    if not isinstance(csv_data_string, str) or not csv_data_string.strip():
        raise _CdfmInputError("CSV 数据不能为空。")

    try:
        rows = list(csv.reader(io.StringIO(csv_data_string), strict=True))
    except (csv.Error, UnicodeError) as exc:
        raise _CdfmInputError("CSV 数据无法解析。") from exc

    if len(rows) < 3:
        raise _CdfmInputError("CDFM 至少需要 2 行数据。")
    header = list(rows[0])
    if header:
        header[0] = header[0].lstrip("\ufeff")
    if (
        not header
        or any(not name.strip() for name in header)
        or len(set(header)) != len(header)
        or len(header) < 2
    ):
        raise _CdfmInputError("CSV 表头必须包含至少 2 个不重复的非空列名。")

    values: list[list[float]] = []
    for row in rows[1:]:
        if len(row) != len(header):
            raise _CdfmInputError("CSV 每行的列数必须与表头一致。")
        parsed_row: list[float] = []
        for cell in row:
            value = cell.strip()
            if not value:
                parsed_row.append(np.nan)
                continue
            try:
                parsed_row.append(float(value))
            except (TypeError, ValueError) as exc:
                raise _CdfmInputError("CDFM 只接受连续数值列，不接受分类变量。") from exc
        values.append(parsed_row)

    data = np.asarray(values, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 2:
        raise _CdfmInputError("CDFM 输入必须至少包含 2 行和 2 列。")
    missing_mask = np.isnan(data) | np.isinf(data)
    return data, header, missing_mask


def _validate_threshold(threshold: Any) -> float | None:
    if threshold is None:
        return None
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise _CdfmInputError("threshold 必须是 0 到 1 之间的数值或 null。")
    value = float(threshold)
    if not np.isfinite(value) or not 0.0 <= value <= 1.0:
        raise _CdfmInputError("threshold 必须是 0 到 1 之间的数值或 null。")
    return value


def adjacency_to_edges(
    adjacency: np.ndarray,
    column_names: Sequence[str],
) -> list[dict[str, str]]:
    """按 CDFM 约定把 ``adjacency[i, j]`` 转换为 ``column_i → column_j``。"""

    matrix = np.asarray(adjacency, dtype=np.float64)
    if matrix.shape != (len(column_names), len(column_names)):
        raise _CdfmResultError("adjacency 维度与列数不一致。")
    if not np.isfinite(matrix).all():
        raise _CdfmResultError("adjacency 包含非有限值。")
    if not np.isin(matrix, (0, 1)).all():
        raise _CdfmResultError("adjacency 必须是 0/1 矩阵。")

    return [
        {
            "from": str(column_names[source]),
            "to": str(column_names[target]),
            "arrows": "to",
        }
        for source, target in np.argwhere(matrix == 1)
    ]


def _validate_result(
    result: Any,
    feature_count: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    try:
        logits = np.asarray(result.logits, dtype=np.float64)
        probabilities = np.asarray(result.probabilities, dtype=np.float64)
        adjacency = np.asarray(result.adjacency, dtype=np.float64)
        threshold = float(result.threshold)
        runtime_seconds = float(result.runtime_sec)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _CdfmResultError("CDFM 返回字段不完整或无法转换。") from exc

    expected_shape = (feature_count, feature_count)
    if logits.shape != expected_shape or probabilities.shape != expected_shape:
        raise _CdfmResultError("CDFM logits/probabilities 维度与列数不一致。")
    if adjacency.shape != expected_shape:
        raise _CdfmResultError("CDFM adjacency 维度与列数不一致。")
    if not np.isfinite(logits).all() or not np.isfinite(probabilities).all():
        raise _CdfmResultError("CDFM logits/probabilities 包含非有限值。")
    if not np.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise _CdfmResultError("CDFM threshold 不在合法范围内。")
    if not np.isfinite(runtime_seconds) or runtime_seconds < 0.0:
        raise _CdfmResultError("CDFM runtime_sec 无效。")
    if not np.isfinite(adjacency).all() or not np.isin(adjacency, (0, 1)).all():
        raise _CdfmResultError("CDFM adjacency 必须是有限的 0/1 矩阵。")
    if np.any(probabilities < 0.0) or np.any(probabilities > 1.0):
        raise _CdfmResultError("CDFM probabilities 超出 [0, 1]。")
    return logits, probabilities, adjacency.astype(np.int8, copy=False), threshold, runtime_seconds


def _package_version() -> str:
    try:
        return importlib.metadata.version("cdfm-base")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def run_cdfm_analysis(
    csv_data_string: str,
    *,
    threshold: float | None = None,
) -> dict[str, Any]:
    """运行一次 CPU CDFM 推理并返回标准 runner payload。"""

    try:
        threshold = _validate_threshold(threshold)
        data, column_names, missing_mask = _parse_csv(csv_data_string)
    except _CdfmInputError as exc:
        return _failure("InputValidationError", str(exc))

    try:
        from cdfm import CDFM
    except (ImportError, ModuleNotFoundError):
        return _failure("DependencyUnavailableError", "CDFM 依赖不可用。")

    model_path = os.getenv("CDFM_MODEL_PATH", "DMIRLAB/CDFM").strip() or "DMIRLAB/CDFM"
    try:
        model = CDFM.from_pretrained(model_path, device="cpu")
    except Exception:
        return _failure("AlgorithmExecutionError", "CDFM 模型加载失败。")

    try:
        result = model.predict(
            data,
            threshold=threshold,
            standardize=True,
            missing_mask=missing_mask if bool(missing_mask.any()) else None,
        )
        logits, probabilities, adjacency, actual_threshold, runtime_seconds = _validate_result(
            result, len(column_names)
        )
        edges = adjacency_to_edges(adjacency, column_names)
    except _CdfmResultError as exc:
        return _failure("ResultValidationError", str(exc))
    except Exception:
        return _failure("AlgorithmExecutionError", "CDFM 推理失败。")

    return {
        "schema_version": "causal_discovery_v1",
        "success": True,
        "algorithm": CDFM_ALGORITHM,
        "runner_version": CDFM_RUNNER_VERSION,
        "implementation": {
            "package": "cdfm-base",
            "version": _package_version(),
            "module": "cdfm",
        },
        "parameters": {"threshold": threshold},
        "matrix_convention": "row_to_column",
        "data": {
            "nodes": [{"id": name, "label": name} for name in column_names],
            "edges": edges,
        },
        "raw_results": {
            "logits": logits.tolist(),
            "probabilities": probabilities.tolist(),
            "threshold": actual_threshold,
        },
        "diagnostics": {
            "n_samples": int(data.shape[0]),
            "n_features": int(data.shape[1]),
            "missing_values": int(missing_mask.sum()),
            "runtime_sec": runtime_seconds,
        },
        "message": "CDFM 因果发现完成。",
    }


__all__ = ["adjacency_to_edges", "run_cdfm_analysis"]
