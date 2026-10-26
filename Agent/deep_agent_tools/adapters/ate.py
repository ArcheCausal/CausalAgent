"""因果效应估计 Adapter 的输入约束和确定性预处理配方。"""

from __future__ import annotations

from Agent.deep_agent_tools.algorithm_specs import CAUSAL_ATE_SPEC
from Agent.deep_agent_tools.error_codes import SafeErrorCode

from .base import AdapterInput, BaseAlgorithmAdapter, PreprocessingRecipe


class AteAdapter(BaseAlgorithmAdapter):
    def __init__(self, *, executor, raw_backend=None) -> None:
        super().__init__(spec=CAUSAL_ATE_SPEC, executor=executor, raw_backend=raw_backend)

    def preprocessing_recipe(self, adapter_input: AdapterInput) -> PreprocessingRecipe:
        return PreprocessingRecipe(
            version="causal-ate-preprocess-v1",
            operations=("validate_columns", "preserve_rows"),
            parameters={},
        )

    def validate_input(self, adapter_input: AdapterInput):
        if "standardized_graph" not in adapter_input.artifacts:
            return "not_ready", SafeErrorCode.ALGORITHM_NOT_READY
        return None
