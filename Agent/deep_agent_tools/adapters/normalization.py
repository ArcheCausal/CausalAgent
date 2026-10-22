"""应用适配器的图标准化入口；实现与 MCP 共用轻量模块。"""

from ..normalization import (
    RunnerContractError,
    result_from_runner_payload,
    runner_method_metadata,
    standardize_runner_graph,
)

__all__ = ["RunnerContractError", "result_from_runner_payload",
           "runner_method_metadata", "standardize_runner_graph"]
