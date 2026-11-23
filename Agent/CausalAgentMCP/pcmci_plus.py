"""Run the pinned Tigramite implementation on the MCP service's verified CSV."""

from __future__ import annotations

import io
import time
from collections.abc import Mapping
from importlib.metadata import version
from typing import Any

import numpy as np
import pandas as pd
from tigramite import data_processing
from tigramite.independence_tests.parcorr import ParCorr
from tigramite.pcmci import PCMCI

from Agent.deep_agent_tools.algorithm_specs import CausalPcmciPlusInput
from Agent.deep_agent_tools.time_series import VerifiedTimeAxis, pcmci_sample_issue


def run_pcmci_plus(
    csv_data: str, parameters: Mapping[str, Any], artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    axis = VerifiedTimeAxis.model_validate(artifacts["time_series"])
    params = CausalPcmciPlusInput.model_validate(parameters)
    frame = pd.read_csv(io.StringIO(csv_data), float_precision="round_trip")
    values = frame.to_numpy(dtype=float)
    if len(frame) != axis.row_count or not np.isfinite(values).all():
        raise ValueError("PCMCI+ input differs from verified input requirements")
    issue = pcmci_sample_issue(len(frame), len(frame.columns), params.tau_max)
    if issue:
        raise ValueError(issue)
    columns = list(frame.columns)
    settings = params.model_dump() | {
        "contemp_collider_rule": "majority", "conflict_resolution": True,
        "reset_lagged_links": False, "max_conds_dim": None, "max_combinations": 1,
        "max_conds_py": None, "max_conds_px": None, "max_conds_px_lagged": None,
        "fdr_method": "none",
    }
    parcorr = {"significance": "analytic", "recycle_residuals": False}
    started = time.perf_counter()
    result = PCMCI(
        dataframe=data_processing.DataFrame(values, var_names=columns),
        cond_ind_test=ParCorr(**parcorr), verbosity=0,
    ).run_pcmciplus(**settings)
    # JSON represents non-finite statistics as null. An accepted edge still needs finite statistics.
    matrices = {"graph": result["graph"].tolist()}
    for key in ("p_matrix", "val_matrix"):
        matrix = result[key]
        matrices[key] = np.where(np.isfinite(matrix), matrix, None).tolist()
    return {
        "schema_version": "pcmci_plus_v1", "success": True, "algorithm": "pcmci_plus",
        "runner_version": "pcmci-plus-runner-v1",
        "implementation": {"package": "tigramite", "version": version("tigramite")},
        "time_axis": axis.model_dump(mode="json"), "configuration": settings,
        "parcorr": parcorr, "diagnostics": {"runtime_seconds": time.perf_counter() - started},
        "raw_results": {"columns": columns, **matrices},
    }
