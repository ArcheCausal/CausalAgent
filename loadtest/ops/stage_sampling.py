"""按档位执行一档负载，并在同一窗口内采集服务器资源与逐 Job 主库核对。

用法：python ops/stage_sampling.py <档位>

本脚本把四件事串成一档：在服务器上启动主机资源采样、在本地跑负载、等采样收尾并把结果
写入运行摘要、再导出本轮 job_id 清单并执行逐 Job 主库核对。采样时长大于负载时长，因为
停止提交后 Job 还会继续执行。

连接与凭据：SSH 私钥取自 ~/.ssh/causalagent_staging，目标为 ubuntu@203.195.194.205；
远端脚本需要预先上传到 /tmp/_lt/（内容即 loadtest/ 下的三个只读脚本）。
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
JOB_IDS = RUNS / "job-ids"
LOG = RUNS / "_orch.log"
HOST = "ubuntu@203.195.194.205"
REMOTE_DIR = "/tmp/_lt"

# 每档：(负载命令参数, 采样秒数)。采样秒数要大于负载持续时间。
SPECS: dict[str, tuple[list[str], int]] = {
    "baseline": (["stage", "--stage", "baseline", "--users", "1", "--duration", "240"], 330),
    "low": (["stage", "--stage", "low", "--duration", "300"], 390),
    "mid_low": (["stage", "--stage", "mid_low", "--duration", "600"], 690),
    "mid": (["stage", "--stage", "mid", "--duration", "600"], 690),
    "stability": (["stage", "--stage", "stability", "--duration", "1800"], 1900),
    "recovery": (["recovery"], 900),
    "burst": (["stage", "--stage", "burst", "--duration", "600"], 1200),
    "burst_ungated": (["stage", "--stage", "burst_ungated", "--duration", "600"], 1500),
    "queue_pressure": (["stage", "--stage", "queue_pressure", "--duration", "300"], 1500),
    "multi_session_smoke": (
        ["stage", "--stage", "multi_session_smoke", "--duration", "180"],
        420,
    ),
    "multi_session_pressure": (
        ["stage", "--stage", "multi_session_pressure", "--duration", "600"],
        1500,
    ),
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


def _run_id_of(stage: str) -> str:
    """从该档最近一次运行摘要读取 run_id；读取失败时返回空串。"""
    summaries = sorted(RUNS.glob(f"*-{stage}/summary.json"))
    if not summaries:
        return ""
    try:
        return str(json.loads(summaries[-1].read_text(encoding="utf-8")).get("run_id") or "")
    except json.JSONDecodeError:
        return ""


def collect_server_snapshot(stage: str, phase: str) -> bool:
    """在服务器上执行一次单次只读采集并写回本地。"""
    run_id = _run_id_of(stage)
    if not run_id:
        log(f"[{stage}] 找不到运行摘要，跳过 {phase} 服务器采集")
        return False
    remote = f"{REMOTE_DIR}/server-{phase}-{run_id}.json"
    result = ssh(
        f"sudo -n bash {REMOTE_DIR}/server_side_collect.sh {run_id} {phase} > {remote} "
        f"2>{REMOTE_DIR}/server-{phase}-{run_id}.err; echo exit=$?"
    )
    log(f"[{stage}] {phase} 采集: {result.stdout.strip()}")
    pull = ssh(f"cat {remote}")
    if not pull.stdout.strip().startswith("{"):
        log(f"[{stage}] {phase} 采集没有输出 JSON，跳过写入")
        return False
    target = RUNS / f"server-side-{run_id}-{phase}.json"
    target.write_text(pull.stdout, encoding="utf-8")
    log(f"[{stage}] {phase} 采集写入 {target.name}")
    return True


def check_jobs_in_db(stage: str) -> bool:
    """导出本轮 job_id 清单，上传后在服务器上逐 Job 核对主库。"""
    run_id = _run_id_of(stage)
    if not run_id:
        log(f"[{stage}] 找不到运行摘要，跳过逐 Job 主库核对")
        return False
    summary = RUNS / f"{run_id}" / "summary.json"
    ids = _job_ids(json.loads(summary.read_text(encoding="utf-8")))
    if not ids:
        log(f"[{stage}] 本轮没有 job_id，逐 Job 主库核对记为未执行")
        return False
    JOB_IDS.mkdir(parents=True, exist_ok=True)
    local_ids = JOB_IDS / f"job-ids-{stage}.txt"
    local_ids.write_text("\n".join(ids) + "\n", encoding="utf-8")
    upload = subprocess.run(
        [
            "scp",
            "-i",
            KEY,
            "-o",
            "BatchMode=yes",
            str(local_ids),
            f"{HOST}:{REMOTE_DIR}/job-ids-{stage}.txt",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if upload.returncode:
        log(f"[{stage}] 清单上传失败: {upload.stderr.strip()[:200]}")
        return False
    remote = f"{REMOTE_DIR}/job-db-{run_id}.json"
    ssh(
        f"sudo -n bash {REMOTE_DIR}/job_db_check.sh {run_id} "
        f"{REMOTE_DIR}/job-ids-{stage}.txt > {remote} 2>{REMOTE_DIR}/job-db-{run_id}.err"
    )
    pull = ssh(f"cat {remote}")
    if not pull.stdout.strip().startswith("{"):
        log(f"[{stage}] 逐 Job 主库核对没有输出 JSON")
        return False
    target = LOADTEST / "local" / f"job-db-{run_id}.json"
    target.write_text(pull.stdout, encoding="utf-8")
    attach = subprocess.run(
        [sys.executable, "-m", "runner", "attach-job-db", "--from", str(target)],
        cwd=str(LOADTEST),
        capture_output=True,
        text=True,
        timeout=120,
    )
    log(f"[{stage}] 逐 Job 主库核对: {attach.stdout.strip()[:300]}")
    return attach.returncode == 0


def _job_ids(summary: dict) -> list[str]:
    """从一份运行摘要取出本轮创建过的唯一 job_id。"""
    ids: list[str] = []
    for sample in summary.get("sse", {}).get("samples", []):
        if sample.get("job_id"):
            ids.append(str(sample["job_id"]))
    for sample in summary.get("job", {}).get("samples", []):
        if sample.get("job_id"):
            ids.append(str(sample["job_id"]))
    return list(dict.fromkeys(ids))


def main(stage: str) -> int:
    if stage not in SPECS:
        print(f"未知档位：{stage}")
        return 2
    load_arguments, budget = SPECS[stage]
    remote = f"{REMOTE_DIR}/sample-{stage}.json"
    log(f"=== {stage} start budget={budget} args={' '.join(load_arguments)}")
    running = ssh(f"pgrep -f 'stage_resource_sample.sh' | grep -v pgrep || true")
    if running.stdout.strip():
        log(f"[{stage}] 已有采样进程在运行，先结束它们再启动本次采样")
        return 1
    collect_server_snapshot(stage, "before")
    ssh(
        f"rm -f {remote}; nohup bash {REMOTE_DIR}/stage_resource_sample.sh "
        f"{stage}-$(date -u +%Y%m%dT%H%M%S) {budget} 10 {stage} "
        f"> {remote} 2>{REMOTE_DIR}/sample-{stage}.err & echo started"
    )
    time.sleep(3)
    load_ok = True
    try:
        process = run_load(load_arguments, budget)
        load_ok = process.returncode == 0
        log(f"[{stage}] load exit={process.returncode}")
        for line in (process.stdout or "").strip().splitlines()[-8:]:
            log("   " + line[:200])
    except subprocess.TimeoutExpired:
        load_ok = False
        log(f"[{stage}] load TIMEOUT after {budget + 300}s")
    deadline = time.time() + 300
    while time.time() < deadline:
        check = ssh(f"pgrep -f 'stage_resource_sample.sh' >/dev/null && echo RUNNING || echo DONE")
        if "DONE" in check.stdout:
            break
        time.sleep(5)
    pull = ssh(f"cat {remote}")
    if not pull.stdout.strip().startswith("{"):
        log(f"=== {stage} FAILED (采样没有输出 JSON)")
        return 1
    body = pull.stdout[pull.stdout.index("{") :]
    sample_path = RUNS / f"stage-sample-{stage}.json"
    sample_path.write_text(body, encoding="utf-8")
    data = json.loads(body)
    log(f"[{stage}] sample samples={len(data.get('samples') or [])}")
    if not data.get("finished_at"):
        log(f"=== {stage} INCOMPLETE (采样未收尾)")
        return 1
    attach = subprocess.run(
        [sys.executable, "-m", "runner", "attach-sample", "--stage", stage, "--from", str(sample_path)],
        cwd=str(LOADTEST),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if attach.returncode:
        log(f"[{stage}] 资源采样写入摘要失败: {attach.stderr.strip()[:200]}")
        return 1
    collect_server_snapshot(stage, "after")
    db_ok = check_jobs_in_db(stage)
    if not load_ok or not db_ok:
        log(f"=== {stage} FAILED (load_ok={load_ok} db_ok={db_ok})")
        return 1
    log(f"=== {stage} done")
    print(stage + " complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
