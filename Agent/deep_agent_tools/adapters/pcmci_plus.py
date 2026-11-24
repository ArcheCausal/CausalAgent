"""PCMCI+ 的应用侧可信输入入口。"""

from Agent.deep_agent_tools.algorithm_specs import PCMCI_PLUS_SPEC
from Agent.deep_agent_tools.error_codes import SafeErrorCode

from .base import AdapterInput, BaseAlgorithmAdapter


class PcmciPlusAdapter(BaseAlgorithmAdapter):
    def __init__(self, *, executor, raw_backend=None) -> None:
        super().__init__(spec=PCMCI_PLUS_SPEC, executor=executor, raw_backend=raw_backend)

    def validate_input(self, adapter_input: AdapterInput) -> tuple[str, SafeErrorCode] | None:
        if adapter_input.missing_values_present:
            return "invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID
        # Spec 已核对可信时间轴、指标语义和按实际滞后计算的样本数。
        return None
