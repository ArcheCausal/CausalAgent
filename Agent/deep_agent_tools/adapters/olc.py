"""OLC Adapter 的连续数据/样本量和潜在混杂假设边界。"""

from __future__ import annotations

from Agent.deep_agent_tools.algorithm_specs import OLC_SPEC
from Agent.deep_agent_tools.error_codes import SafeErrorCode

from .base import AdapterInput, BaseAlgorithmAdapter, PreprocessingRecipe


class OlcAdapter(BaseAlgorithmAdapter):
    def __init__(self, *, executor, raw_backend=None) -> None:
        super().__init__(spec=OLC_SPEC, executor=executor, raw_backend=raw_backend)

    def preprocessing_recipe(self, adapter_input: AdapterInput) -> PreprocessingRecipe:
        return PreprocessingRecipe(
            version="olc-preprocess-v1",
            operations=("validate_continuous_columns", "preserve_rows", "preserve_scale"),
            parameters={"missing_values": "reject"},
        )

    def validate_input(self, adapter_input: AdapterInput):
        profile = adapter_input.data_profile
        if profile.row_count < 200 or profile.column_count < 2:
            return "not_applicable", SafeErrorCode.ALGORITHM_NOT_APPLICABLE
        if profile.categorical_columns or len(profile.numeric_columns) != profile.column_count:
            return "not_applicable", SafeErrorCode.ALGORITHM_NOT_APPLICABLE
        if adapter_input.missing_values_present:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        return None

