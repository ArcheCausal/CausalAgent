"""CDFM runner：只负责 CSV、CPU 推理和私有 raw payload 转换。"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import numpy as np

from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC


CDFM_RUNNER_VERSION = "cdfm-runner-v3"
CDFM_ALGORITHM = "cdfm"


class _CdfmInputError(ValueError):
    """CDFM 输入无法满足 runner 契约。"""


class _CdfmResultError(ValueError):
    """CDFM 返回结构无法满足 runner 契约。"""


def _failure(
    error_type: str,
    message: str,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "causal_discovery_v1",
        "success": False,
        "algorithm": CDFM_ALGORITHM,
        "runner_version": CDFM_RUNNER_VERSION,
        "error_type": error_type,
        "message": message,
    }
    if diagnostics:
        payload["diagnostics"] = diagnostics
    return payload


_MAX_DIAGNOSTIC_MESSAGE_CHARS = 500


def _failure_diagnostics(
    exc: BaseException,
    model_path: str,
) -> dict[str, Any]:
    """收集失败现场的受控诊断字段，不写入 CSV、堆栈或来源正文。"""
    message = str(exc).strip().replace("\n", " ")[:_MAX_DIAGNOSTIC_MESSAGE_CHARS]
    return {
        "error_type": type(exc).__name__,
        "error_message": message or type(exc).__name__,
        "model_path": model_path,
        "model_source": "local_dir" if os.path.isdir(model_path) else "hub",
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
                raise _CdfmInputError("CDFM 输入列必须能够转换为数值，不直接接受非数值分类列。") from exc
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
    *,
    probabilities: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """按行→列的成对二值编码解码；对称两个 1 表示一条双箭头边。"""

    matrix = np.asarray(adjacency, dtype=np.float64)
    if matrix.shape != (len(column_names), len(column_names)):
        raise _CdfmResultError("adjacency 维度与列数不一致。")
    if not np.isfinite(matrix).all():
        raise _CdfmResultError("adjacency 包含非有限值。")
    if not np.isin(matrix, (0, 1)).all():
        raise _CdfmResultError("adjacency 必须是 0/1 矩阵。")

    scores = None if probabilities is None else np.asarray(probabilities, dtype=np.float64)
    if scores is not None and (
        scores.shape != matrix.shape or not np.isfinite(scores).all()
        or np.any(scores < 0) or np.any(scores > 1)
    ):
        raise _CdfmResultError("probabilities 必须是与邻接矩阵同形的有限概率矩阵。")

    edges: list[dict[str, Any]] = []
    for first in range(len(column_names)):
        # 非零对角线也保留，交由共享结构校验报告 self_loop。
        if matrix[first, first] == 1:
            edges.append({"from": str(column_names[first]), "to": str(column_names[first]), "arrows": "to"})
        for second in range(first + 1, len(column_names)):
            forward, backward = matrix[first, second], matrix[second, first]
            if not forward and not backward:
                continue
            if forward and backward:
                source, target, arrows = first, second, "to,from"
            elif forward:
                source, target, arrows = first, second, "to"
            else:
                source, target, arrows = second, first, "to"
            edges.append({"from": str(column_names[source]), "to": str(column_names[target]), "arrows": arrows})
    if scores is not None:
        positions = {str(name): index for index, name in enumerate(column_names)}
        for edge in edges:
            source, target = positions[edge["from"]], positions[edge["to"]]
            edge["prediction"] = {
                "forward_probability": float(scores[source, target]),
                "reverse_probability": float(scores[target, source]),
            }
    return edges


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


def _weights_identity(model_path: str) -> dict[str, str | None]:
    """记录本次本地权重的实际摘要；未验证的 revision 保持未知。"""
    directory = Path(model_path)
    if not directory.is_dir():
        return {"source": model_path, "revision": None, "sha256": None}
    weights = directory / "model.safetensors"
    if not weights.is_file():
        return {"source": "local_checkpoint", "revision": None, "sha256": None}
    with weights.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    identity = {"source": "local_checkpoint", "revision": None, "sha256": digest}
    version_file = directory / "version.json"
    if version_file.is_file():
        try:
            version = json.loads(version_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return identity
        if not isinstance(version, dict) or not isinstance(version.get("files"), list):
            return identity
        recorded = next((item for item in version["files"] if isinstance(item, dict) and item.get("name") == weights.name), {})
        if recorded.get("sha256") == digest:
            identity["source"] = version.get("repo_id") or "local_checkpoint"
            identity["revision"] = version.get("revision")
    return identity


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

    if importlib.util.find_spec("cdfm") is None:
        return _failure("DependencyUnavailableError", "CDFM 依赖不可用。")
    from cdfm import CDFM

    model_path = os.getenv("CDFM_MODEL_PATH", "DMIRLAB/CDFM").strip() or "DMIRLAB/CDFM"
    try:
        model = CDFM.from_pretrained(model_path, device="cpu")
    except Exception as exc:
        return _failure(
            "AlgorithmExecutionError",
            "CDFM 模型加载失败。",
            diagnostics=_failure_diagnostics(exc, model_path),
        )

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
        edges = adjacency_to_edges(adjacency, column_names, probabilities=probabilities)
    except _CdfmResultError as exc:
        return _failure("ResultValidationError", str(exc))
    except Exception as exc:
        return _failure(
            "AlgorithmExecutionError",
            "CDFM 推理失败。",
            diagnostics=_failure_diagnostics(exc, model_path),
        )

    return {
        "schema_version": "causal_discovery_v1",
        "success": True,
        "algorithm": CDFM_ALGORITHM,
        "runner_version": CDFM_RUNNER_VERSION,
        "implementation": {
            "package": "cdfm-base",
            "version": _package_version(),
            "module": "cdfm",
            "weights": _weights_identity(model_path),
        },
        "parameters": {"threshold": threshold},
        "method": CDFM_SPEC.method_definition().model_dump(mode="json"),
        "matrix_convention": CDFM_SPEC.matrix_convention,
        "data": {
            "nodes": [{"id": name, "label": name} for name in column_names],
            "edges": edges,
        },
        "raw_results": {
            "adjacency_matrix": adjacency.tolist(),
            "variable_order": list(column_names),
            "logits": logits.tolist(),
            "probabilities": probabilities.tolist(),
            "threshold": actual_threshold,
        },
        "diagnostics": {
            "n_samples": int(data.shape[0]),
            "n_features": int(data.shape[1]),
            "missing_values": int(missing_mask.sum()),
            "runtime_sec": runtime_seconds,
            "threshold": actual_threshold,
            "standardize": True,
            "missing_handling": "native_mask" if bool(missing_mask.any()) else "no_missing_values",
        },
        "message": "CDFM 因果发现完成。",
    }


__all__ = ["adjacency_to_edges", "run_cdfm_analysis"]
