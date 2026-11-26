"""PC、OLC、DirectLiNGAM、CDFM、后门 OLS 的应用侧 Adapter。"""

from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS

from .backdoor_ols import BackdoorOlsAdapter
from .base import AdapterInput, AlgorithmAdapter, BaseAlgorithmAdapter
from .cdfm import CDFMAdapter
from .direct_lingam import DirectLiNGAMAdapter
from .g_formula import GFormulaAdapter
from .olc import OlcAdapter
from .pc import PcAdapter


def build_default_adapters(*, executor, raw_backend=None) -> dict[str, BaseAlgorithmAdapter]:
    """只为默认 Spec allowlist 构造静态 Adapter 绑定。"""

    adapter_types = {
        "causal.pc": PcAdapter,
        "causal.olc": OlcAdapter,
        "causal.direct_lingam": DirectLiNGAMAdapter,
        "causal.cdfm": CDFMAdapter,
        "causal.backdoor_ols": BackdoorOlsAdapter,
        "causal.g_formula": GFormulaAdapter,
    }
    return {
        spec.capability_id: adapter_types[spec.capability_id](
            executor=executor,
            raw_backend=raw_backend,
        )
        for spec in DEFAULT_ALGORITHM_SPECS
    }

__all__ = [
    "AdapterInput",
    "AlgorithmAdapter",
    "BackdoorOlsAdapter",
    "BaseAlgorithmAdapter",
    "CDFMAdapter",
    "build_default_adapters",
    "DirectLiNGAMAdapter",
    "GFormulaAdapter",
    "OlcAdapter",
    "PcAdapter",
]
