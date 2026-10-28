"""g-formula Adapter 的输入约束。"""

from __future__ import annotations

from Agent.deep_agent_tools.algorithm_specs import CAUSAL_G_FORMULA_SPEC
from Agent.deep_agent_tools.error_codes import SafeErrorCode

from .base import AdapterInput, BaseAlgorithmAdapter


class GFormulaAdapter(BaseAlgorithmAdapter):
    def __init__(self, *, executor, raw_backend=None) -> None:
        super().__init__(
            spec=CAUSAL_G_FORMULA_SPEC, executor=executor, raw_backend=raw_backend
        )

    def validate_input(self, adapter_input: AdapterInput):
        if "standardized_graph" not in adapter_input.artifacts:
            return "not_ready", SafeErrorCode.ALGORITHM_NOT_READY
        return None
