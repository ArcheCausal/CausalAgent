"""按档位执行负载并在同一窗口内采集服务器资源。

用法：python ops/stage_sampling.py <档位>

本脚本把三件事串成一档：先在服务器上启动主机资源采样，再在本地按档位跑负载，
最后等采样结束后把结果写入该档运行摘要。采样时长大于负载时长，因为停止提交后
Job 还会继续执行。

连接与凭据：SSH 私钥取自 ~/.ssh/causalagent_staging，目标为 ubuntu@203.195.194.205；
远端采样脚本需要预先上传到 /tmp/_sr.sh（内容即 loadtest/stage_resource_sample.sh）。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

KEY = str(pathlib.Path.home() / ".ssh" / "causalagent_staging")
LOADTEST = pathlib.Path(__file__).resolve().parent.parent
RUNS = LOADTEST / "local" / "runs"
LOG = RUNS / "_orch.log"
HOST = "ubuntu@203.195.194.205"

# 每档：(负载命令参数, 采样秒数)。采样秒数要大于负载持续时间。
SPECS: dict[str, tuple[list[str], int]] = {
    "baseline": (["stage", "--stage", "baseline", "--users", "1", "--duration", "240"], 330),
    "low": (["stage", "--stage", "low", "--duration", "300"], 390),
    "mid_low": (["stage", "--stage", "mid_low", "--duration", "600"], 690),
    "mid": (["stage", "--stage", "mid", "--duration", "600"], 690),
    "stability": (["stage", "--stage", "stability", "--duration", "1800"], 1900),
    "recovery": (["recovery"], 900),
    "burst": (["stage", "--stage", "burst", "--duration", "600"], 1200),
}


def log(message: str) -> None:
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def ssh(command: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "ssh",
            "-i",
            KEY,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            HOST,
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_load(arguments: list[str], budget: int) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "runner", *arguments],
        cwd=str(LOADTEST),
        capture_output=True,
        text=True,
        timeout=budget + 300,
        env=environment,
    )


def main(stage: str) -> int:
    if stage not in SPECS:
        print(f"未知档位：{stage}")
        return 2
    load_arguments, budget = SPECS[stage]
    remote = f"/tmp/_sr_{stage}.json"
    log(f"=== {stage} start budget={budget} args={' '.join(load_arguments)}")
    running = ssh("pgrep -f '_sr.sh' | grep -v pgrep || true")
    if running.stdout.strip():
        log(f"[{stage}] 已有采样进程在运行，先结束它们再启动本次采样")
        return 1
    ssh(
        f"rm -f {remote}; nohup bash /tmp/_sr.sh {budget} 10 {stage} "
        f"> {remote} 2>/tmp/_sr_{stage}.err & echo started"
    )
    time.sleep(3)
    try:
        process = run_load(load_arguments, budget)
        log(f"[{stage}] load exit={process.returncode}")
        for line in (process.stdout or "").strip().splitlines()[-8:]:
            log("   " + line[:200])
    except subprocess.TimeoutExpired:
        log(f"[{stage}] load TIMEOUT after {budget + 300}s")
    deadline = time.time() + 300
    while time.time() < deadline:
        check = ssh(f"pgrep -f '_sr.sh {budget} 10 {stage}' >/dev/null && echo RUNNING || echo DONE")
        if "DONE" in check.stdout:
            break
        time.sleep(5)
    pull = ssh(f"cat {remote}")
    body = pull.stdout[pull.stdout.index("{"):]
    (RUNS / f"stage-sample-{stage}.json").write_text(body, encoding="utf-8")
    data = json.loads(body)
    log(f"[{stage}] sample samples={len(data['samples'])}")
    if data.get("finished_at"):
        log(f"=== {stage} done")
        print(stage + " complete")
        return 0
    log(f"=== {stage} INCOMPLETE (采样未收尾)")
    print(stage + " incomplete")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))

