"""按顺序跑多个档位，每个档位调用 ops/stage_sampling.py。

用法：python ops/run_stages.py [档位 ...]

不传档位时按既定顺序跑完全部档位。任何一档失败就停止后续档位，避免上一档未排空或
核对失败时污染下一档；逐档日志写入 local/runs/<date>/_driver.log。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from runner import config

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
    """驱动日志写入当天日期目录，与同一天的其他产物一起归档。"""
    runs_dir, _evidence_dir = config.run_artifacts_dir(time.time())
    runs_dir.mkdir(parents=True, exist_ok=True)
    with (runs_dir / "_driver.log").open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def main(stages: list[str]) -> int:
    selected = stages or DEFAULT_STAGES
    log("=== driver start: " + " ".join(selected))
    exit_code = 0
    for stage in selected:
        start = time.time()
        log(f"[driver] begin {stage}")
        process = subprocess.run(
            [sys.executable, "ops/stage_sampling.py", stage],
            cwd=str(BASE),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log(f"[driver] end {stage} exit={process.returncode} elapsed={round(time.time() - start)}s")
        for line in (process.stdout or "").strip().splitlines()[-6:]:
            log("    out " + line[:200])
        for line in (process.stderr or "").strip().splitlines()[-6:]:
            log("    err " + line[:200])
        if process.returncode:
            exit_code = 1
            log(f"[driver] {stage} 失败，停止后续档位")
            break
    log("=== driver done" if exit_code == 0 else "=== driver unknown (证据不完整或档位未排空)")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

