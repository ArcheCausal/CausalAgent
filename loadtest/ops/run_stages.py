"""按顺序跑多个档位，每个档位调用 ops/stage_sampling.py。

用法：python ops/run_stages.py [档位 ...]

不传档位时按既定顺序跑完剩下全部档位。逐档日志写入 local/runs/_driver.log。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
DRIVER_LOG = BASE / "local" / "runs" / "_driver.log"
DEFAULT_STAGES = ["low", "mid_low", "mid", "stability", "recovery", "burst"]


def log(message: str) -> None:
    with DRIVER_LOG.open("a", encoding="utf-8") as handle:
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
        )
        log(f"[driver] end {stage} exit={process.returncode} elapsed={round(time.time() - start)}s")
        for line in (process.stdout or "").strip().splitlines()[-6:]:
            log("    out " + line[:200])
        for line in (process.stderr or "").strip().splitlines()[-6:]:
            log("    err " + line[:200])
        if process.returncode:
            exit_code = 1
    log("=== driver done")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

