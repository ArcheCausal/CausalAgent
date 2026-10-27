"""CDFM 的应用侧输入边界。"""

from __future__ import annotations

from Agent.deep_agent_tools.algorithm_specs import CDFM_SPEC
from Agent.deep_agent_tools.error_codes import SafeErrorCode

from .base import AdapterInput, BaseAlgorithmAdapter


class CDFMAdapter(BaseAlgorithmAdapter):
    def __init__(self, *, executor, raw_backend=None) -> None:
        super().__init__(spec=CDFM_SPEC, executor=executor, raw_backend=raw_backend)

    def validate_input(self, adapter_input: AdapterInput):
        profile = adapter_input.data_profile
        if profile.row_count < 2 or profile.column_count < 2:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if profile.categorical_columns or len(profile.numeric_columns) != profile.column_count:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        if not adapter_input.dataset_authority_available:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        return None
