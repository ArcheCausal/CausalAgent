"""测量语义声明和已校验快照的共享纯函数。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType


MEASUREMENT_KINDS = frozenset({"count", "ordinal", "category", "continuous", "binary", "unknown"})


def normalize_semantic_kinds(
    value: object, *, columns: Sequence[str] | None = None,
) -> dict[str, str]:
    """校验映射并复制快照；不把无效值转换为字符串。"""
    if not isinstance(value, Mapping):
        raise ValueError("semantic_kinds must be a mapping")
    result: dict[str, str] = {}
    for column, kind in value.items():
        if not isinstance(column, str) or not column.strip() or column != column.strip():
            raise ValueError("semantic column must be a non-blank trimmed string")
        if not isinstance(kind, str) or kind not in MEASUREMENT_KINDS:
            raise ValueError("invalid measurement kind")
        if columns is not None and column not in columns:
            raise ValueError("semantic column is absent from the validated dataset")
        result[column] = kind
    return dict(sorted(result.items()))


def normalize_measurement_semantics(
    declarations: object, *, columns: Sequence[str] | None = None,
) -> dict[str, str]:
    """解析声明列表；相同声明可重复，冲突声明必须拒绝。"""
    if not isinstance(declarations, (list, tuple)):
        raise ValueError("measurement_semantics must be a list")
    result: dict[str, str] = {}
    for declaration in declarations:
        if not isinstance(declaration, Mapping):
            raise ValueError("measurement declaration must be a mapping")
        column = declaration.get("column")
        if not isinstance(column, str):
            raise ValueError("semantic column must be a string")
        item = normalize_semantic_kinds(
            {column: declaration.get("kind")}, columns=columns,
        )
        for column, kind in item.items():
            if column in result and result[column] != kind:
                raise ValueError("conflicting measurement declarations")
            result[column] = kind
    return dict(sorted(result.items()))


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate validation report key")
        result[key] = value
    return result


def semantic_kinds_from_validation_report(report: object) -> dict[str, str]:
    """只读取通过校验且显式保存语义快照的报告；缺失时拒绝。"""
    if isinstance(report, (str, bytes, bytearray)):
        report = json.loads(report, object_pairs_hook=_unique_json_object)
    if not isinstance(report, Mapping) or report.get("accepted") is not True:
        raise ValueError("accepted validation report is required")
    if report.get("errors") != []:
        raise ValueError("validation report contains errors")
    columns = report.get("columns")
    if (
        not isinstance(columns, (list, tuple)) or not columns
        or any(not isinstance(column, str) or not column.strip() for column in columns)
        or len(set(columns)) != len(columns)
    ):
        raise ValueError("validation report columns are invalid")
    return normalize_semantic_kinds(report.get("semantic_kinds"), columns=columns)


@dataclass(frozen=True)
class ValidatedAlgorithmInput:
    """服务端可信 reader 返回的 CSV 和不可变测量语义快照。"""

    csv_data: str
    semantic_kinds: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.csv_data, str) or not self.csv_data:
            raise ValueError("validated CSV must be a non-empty string")
        object.__setattr__(
            self, "semantic_kinds",
            MappingProxyType(normalize_semantic_kinds(self.semantic_kinds)),
        )
