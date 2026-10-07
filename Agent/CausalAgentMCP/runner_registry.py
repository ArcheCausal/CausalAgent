"""Fixed causal runner registry; no runtime import paths or dynamic tool list."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from Agent.deep_agent_tools.algorithm_specs import (
    DEFAULT_ALGORITHM_SPECS,
    AlgorithmSpec,
)
from Agent.deep_agent_tools.error_codes import SafeErrorCode


Runner = Callable[[str, Mapping[str, Any]], dict[str, Any]]


class RunnerRegistryError(ValueError):
    def __init__(self, code: SafeErrorCode) -> None:
        super().__init__(code.value)
        self.safe_error_code = code


@dataclass(frozen=True)
class RunnerSpec:
    capability_id: str
    version: str
    spec_digest: str
    timeout_seconds: int
    concurrency: int
    runner: Runner


def _pc(csv_data: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    from Agent.causal.causalachieve import run_pc_analysis

    return run_pc_analysis(csv_data, alpha=float(parameters.get("alpha", 0.05)))


def _olc(csv_data: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    from Agent.causal.causalachieve import run_olc_analysis

    return run_olc_analysis(
        csv_data,
        alpha=float(parameters.get("alpha", 0.05)),
        beta=float(parameters.get("beta", 0.01)),
    )

def _direct_lingam(csv_data: str, _parameters: Mapping[str, Any]) -> dict[str, Any]:
    from Agent.causal.causalachieve import run_direct_lingam_analysis

    return run_direct_lingam_analysis(csv_data)


def _cdfm(csv_data: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    from Agent.causal.cdfm_runner import run_cdfm_analysis

    return run_cdfm_analysis(csv_data, threshold=parameters.get("threshold"))


class RunnerRegistry:
    """Index the enabled reviewed capabilities and reject incompatible callers."""

    def __init__(self, entries: list[RunnerSpec]) -> None:
        by_capability: dict[str, RunnerSpec] = {}
        for entry in entries:
            if entry.capability_id in by_capability:
                raise ValueError("duplicate MCP capability")
            by_capability[entry.capability_id] = entry
        self._entries = dict(by_capability)

    def resolve(self, capability_id: str, version: str, spec_digest: str) -> RunnerSpec:
        entry = self._entries.get(capability_id)
        if entry is None:
            raise RunnerRegistryError(SafeErrorCode.UNKNOWN_CAPABILITY)
        if entry.version != version or entry.spec_digest != spec_digest:
            raise RunnerRegistryError(
                SafeErrorCode.MCP_CAPABILITY_VERSION_UNSUPPORTED
            )
        return entry

    def capability_summary(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (entry.capability_id, entry.version, entry.spec_digest)
            for entry in sorted(self._entries.values(), key=lambda item: item.capability_id)
        )


def build_default_registry() -> RunnerRegistry:
    runners = {
        "causal.pc": _pc,
        "causal.olc": _olc,
        "causal.direct_lingam": _direct_lingam,
        "causal.cdfm": _cdfm,
    }
    entries = [
        (spec, runners[spec.capability_id]) for spec in DEFAULT_ALGORITHM_SPECS
    ]
    return RunnerRegistry(
        [
            RunnerSpec(
                capability_id=spec.capability_id,
                version=spec.version,
                spec_digest=spec.spec_digest,
                timeout_seconds=spec.default_timeout_seconds,
                concurrency=spec.default_concurrency,
                runner=runner,
            )
            for spec, runner in entries
        ]
    )
