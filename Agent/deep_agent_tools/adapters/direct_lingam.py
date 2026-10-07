"""DirectLiNGAM Adapter：连续数值、无缺失和 target_to_source 语义。"""

from __future__ import annotations

import csv
import io
import math

from Agent.deep_agent_tools.algorithm_specs import DIRECT_LINGAM_SPEC
from Agent.deep_agent_tools.error_codes import SafeErrorCode

from .base import AdapterInput, BaseAlgorithmAdapter, PreprocessingRecipe


class DirectLiNGAMAdapter(BaseAlgorithmAdapter):
    def __init__(self, *, executor, raw_backend=None) -> None:
        super().__init__(spec=DIRECT_LINGAM_SPEC, executor=executor, raw_backend=raw_backend)

    def preprocessing_recipe(self, adapter_input: AdapterInput) -> PreprocessingRecipe:
        return PreprocessingRecipe(
            version="direct-lingam-preprocess-v1",
            operations=("validate_numeric_columns", "preserve_rows", "preserve_scale"),
            parameters={"matrix_convention": "target_to_source", "missing_values": "reject"},
        )

    def validate_input(self, adapter_input: AdapterInput):
        profile = adapter_input.data_profile
        if profile.row_count < 2 or profile.column_count < 2:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if profile.categorical_columns or len(profile.numeric_columns) != profile.column_count:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if adapter_input.missing_values_present:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if not adapter_input.dataset_csv and not adapter_input.dataset_authority_available:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if not adapter_input.dataset_csv:
            # 新路径由 causal-mcp 按可信上下文从冻结文件读取；本地 Adapter
            # 仍保留 profile/缺失值校验，但不复制文件正文到 State。
            return None
        try:
            rows = list(csv.reader(io.StringIO(adapter_input.dataset_csv), strict=True))
        except (csv.Error, UnicodeError):
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if len(rows) < 3:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        header = [name.strip() for name in rows[0]]
        if len(header) < 2 or any(not name for name in header) or len(set(header)) != len(header):
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        columns: list[list[float]] = [[] for _ in header]
        try:
            for row in rows[1:]:
                if len(row) != len(header) or any(not value.strip() for value in row):
                    return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
                for index, value in enumerate(row):
                    number = float(value)
                    if not math.isfinite(number):
                        return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
                    columns[index].append(number)
        except (TypeError, ValueError):
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if any(len(set(column)) <= 1 for column in columns):
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        return None
