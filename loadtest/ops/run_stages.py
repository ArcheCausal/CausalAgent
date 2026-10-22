"""按顺序跑多个档位，每个档位调用 ops/stage_sampling.py。

用法：python ops/run_stages.py [档位 ...]

不传档位时按既定顺序跑完全部档位。任何一档失败就停止后续档位，避免上一档未排空或
核对失败时污染下一档；逐档日志写入 local/runs/_driver.log。
"""
from __future__ import annotations

import pathlib
import json
import os
import subprocess
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from runner import config
from runner.rounds import read_manifest, record_run
from ops.stage_sampling import make_run_id

DRIVER_LOG = config.RUNS_DIR / "_driver.log"
DEFAULT_STAGES = [
    "low",
    "mid_low",
    "mid",
    "stability",
    "recovery",
    "burst",
    "burst_ungated",
    "queue_pressure",
    "multi_session_smoke",
    "multi_session_pressure",
]


def log(message: str) -> None:
    DRIVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DRIVER_LOG.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def main(stages: list[str]) -> int:
    selected = stages or DEFAULT_STAGES
    manifest_path = os.environ.get("LOADTEST_ROUND_MANIFEST")
    explicit = os.environ.get("LOADTEST_RUN_ID")
    if manifest_path:
        selected_runs = [(row["stage"], row["run_id"]) for row in read_manifest(manifest_path)["runs"]
                         if row["disposition"] == "planned" and (not stages or row["stage"] in stages)
                         and (not explicit or row["run_id"] == explicit)]
        if not selected_runs:
            raise ValueError("missing_planned_runs")
    else:
        if explicit and len(selected) != 1:
            raise ValueError("explicit_run_id_requires_one_stage")
        selected_runs = [(stage, explicit or make_run_id(stage)) for stage in selected]
    log("=== driver start: " + " ".join(selected))
    exit_code = 0
    for stage, run_id in selected_runs:
        start = time.time()
        log(f"[driver] begin {stage}")
        environment = dict(os.environ, LOADTEST_RUN_ID=run_id)
        try:
            process = subprocess.run(
                [sys.executable, "ops/stage_sampling.py", stage],
                cwd=str(BASE),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )
        except (KeyboardInterrupt, OSError) as exc:
            if manifest_path:
                record_run(manifest_path, stage, run_id, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                           reason=type(exc).__name__)
            return 1
        log(f"[driver] end {stage} exit={process.returncode} elapsed={round(time.time() - start)}s")
        try:
            state = json.loads((config.RUNS_DIR / run_id / "orchestration.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        allowed = (state.get("run_id") == run_id and state.get("stage") == stage
                   and state.get("continue_allowed") is True and state.get("cleanup", {}).get("status") == "passed"
                   and state.get("evidence", {}).get("status") == "collected")
        log(f"[driver] run_id={run_id} scenario={state.get('scenario', {}).get('status', 'unknown')} "
            f"evidence={state.get('evidence', {}).get('status', 'unknown')} cleanup={state.get('cleanup', {}).get('status', 'unknown')}")
        if process.returncode or not allowed:
            if manifest_path:
                row = next(row for row in read_manifest(manifest_path)["runs"] if row["run_id"] == run_id)
                if row["disposition"] == "planned":
                    record_run(manifest_path, stage, run_id, "failed", reason="orchestration_not_confirmed")
            exit_code = 1
            log(f"[driver] {stage} 失败，停止后续档位")
            break
    log("=== driver done" if exit_code == 0 else "=== driver unknown (证据不完整或档位未排空)")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

