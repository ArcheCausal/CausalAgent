"""PC Adapter 的输入约束和确定性预处理配方。"""

from __future__ import annotations

from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import DataProfile

from .base import AdapterInput, BaseAlgorithmAdapter, PreprocessingRecipe


class PcAdapter(BaseAlgorithmAdapter):
    def __init__(self, *, executor, raw_backend=None) -> None:
        super().__init__(spec=PC_SPEC, executor=executor, raw_backend=raw_backend)

    def preprocessing_recipe(self, adapter_input: AdapterInput) -> PreprocessingRecipe:
        return PreprocessingRecipe(
            version="pc-preprocess-v1",
            operations=("validate_header", "preserve_rows", "preserve_columns"),
            parameters={"categorical_encoding": "none_in_adapter"},
        )

    def validate_input(self, adapter_input: AdapterInput):
        profile: DataProfile = adapter_input.data_profile
        if profile.row_count <= 0 or profile.column_count < 2:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if adapter_input.missing_values_present:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        return None

