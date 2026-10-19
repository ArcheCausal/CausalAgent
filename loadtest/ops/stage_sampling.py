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
    # smoke 是单用户单流程检查，不覆盖持续时间，保持 full_flow 语义。
    "smoke": (["stage", "--stage", "smoke"], 900),
    # baseline 在档位表里是 full_flow 4 轮；传入 --duration 会把它改成 mixed，
    # 因此这里不覆盖持续时间，只按档位定义运行。
    "baseline": (["stage", "--stage", "baseline"], 900),
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


def ssh(
    command: str, timeout: int = 300, attempts: int = 3
) -> subprocess.CompletedProcess:
    """执行一次远端命令；超时或连接被重置时重试，重试仍失败才抛出。

    公网入口会持续收到扫描与口令尝试，sshd 在压力下会推迟握手；单次超时
    如果直接抛出，会把已经成功的负载与采样误判为整档失败。
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return subprocess.run(
                [
                    "ssh",
                    "-i",
                    KEY,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=20",
                    "-o",
                    "ServerAliveInterval=15",
                    "-o",
                    "ServerAliveCountMax=4",
                    HOST,
                    command,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_error = exc
            log(
                f"ssh 第 {attempt}/{attempts} 次失败（{type(exc).__name__}）："
                f"{command[:100]}"
            )
            time.sleep(5)
    raise last_error if last_error else RuntimeError("ssh 失败")


def run_load(arguments: list[str], budget: int) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "runner", *arguments],
        cwd=str(LOADTEST),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=budget + 300,
        env=environment,
    )


def make_run_id(stage: str) -> str:
    """按与 runner 相同的格式生成该档本次运行的 run_id。"""
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{stage}"


def collect_server_snapshot(stage: str, phase: str, run_id: str) -> pathlib.Path | None:
    """在服务器上执行一次单次只读采集并把结果写回本地。

    写入运行摘要的时机由调用方决定：负载前的快照在负载结束后才有摘要可写。
    """
    if not run_id:
        log(f"[{stage}] 缺少 run_id，跳过 {phase} 服务器采集")
        return None
    remote = f"{REMOTE_DIR}/server-{phase}-{run_id}.json"
    result = ssh(
        f"sudo -n bash {REMOTE_DIR}/server_side_collect.sh {run_id} {phase} > {remote} "
        f"2>{REMOTE_DIR}/server-{phase}-{run_id}.err; echo exit=$?"
    )
    log(f"[{stage}] {phase} 采集: {result.stdout.strip()}")
    pull = ssh(f"cat {remote}")
    if not pull.stdout.strip().startswith("{"):
        log(f"[{stage}] {phase} 采集没有输出 JSON，跳过写入")
        return None
    target = RUNS / f"server-side-{run_id}-{phase}.json"
    target.write_text(pull.stdout, encoding="utf-8")
    log(f"[{stage}] {phase} 采集写入 {target.name}")
    return target


def attach_server_snapshot(stage: str, phase: str, path: pathlib.Path) -> bool:
    """把一份服务器采集写入该档最近一次运行的摘要。"""
    attach_command = "attach-server" if phase == "before" else "attach-after"
    attached = subprocess.run(
        [
            sys.executable,
            "-m",
            "runner",
            attach_command,
            "--from",
            str(path),
            "--stage",
            stage,
        ],
        cwd=str(LOADTEST),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    log(f"[{stage}] {phase} 采集登记: {attached.stdout.strip()[:200]}")
    return attached.returncode == 0


def check_jobs_in_db(stage: str, run_id: str) -> bool:
    """导出本轮 job_id 清单，上传后在服务器上逐 Job 核对主库。"""
    if not run_id:
        log(f"[{stage}] 缺少 run_id，跳过逐 Job 主库核对")
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
        encoding="utf-8",
        errors="replace",
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
        [
            sys.executable,
            "-m",
            "runner",
            "attach-job-db",
            "--from",
            str(target),
            "--stage",
            stage,
        ],
        cwd=str(LOADTEST),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    log(f"[{stage}] 逐 Job 主库核对: {attach.stdout.strip()[:300]}")
    return attach.returncode == 0


def _job_ids(summary: dict) -> list[str]:
    """从一份运行摘要取出本轮创建过的唯一 job_id。"""
    ids: list[str] = []
    # 创建时登记的清单优先，保证未观测到终态的 Job 也在核对范围内。
    for job_id in summary.get("job", {}).get("created_ids") or []:
        if job_id:
            ids.append(str(job_id))
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
    run_id = make_run_id(stage)
    # 把 run_id 固定后传给 runner，使负载前的采集、负载与逐 Job 核对共用同一标识。
    load_arguments = [*load_arguments, "--run-id", run_id]
    log(
        f"=== {stage} start run_id={run_id} budget={budget} "
        f"args={' '.join(load_arguments)}"
    )
    # 用方括号写法避免匹配到本条命令自身的 bash 进程；pgrep 只输出 PID，
    # 管道过滤 pgrep 无法生效，所以守卫必须靠正则自身不匹配命令行文本来实现。
    running = ssh("pgrep -f '[s]tage_resource_sample.sh' || true")
    if running.stdout.strip():
        log(f"[{stage}] 已有采样进程在运行，先结束它们再启动本次采样")
        return 1
    before_snapshot = collect_server_snapshot(stage, "before", run_id)
    sampler_started = time.time()
    # 启动采样与取回结果都以本次 run_id 命名的文件为准，重试时不再重复启动；
    # 否则一次超时重试就会有两个采样进程写同一个文件，产出的 JSON 无法解析。
    sample_remote = f"{REMOTE_DIR}/sample-{run_id}.json"
    start = ssh(
        f"if [ -s {sample_remote} ]; then echo ALREADY; else rm -f {sample_remote}; "
        f"nohup bash {REMOTE_DIR}/stage_resource_sample.sh "
        f"{run_id} {budget} 10 {stage} "
        f"> {sample_remote} 2>{REMOTE_DIR}/sample-{stage}.err & echo OURS; fi",
        timeout=120,
    )
    log(f"[{stage}] 采样启动: {start.stdout.strip()}")
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
    # 等待采样进程真正收尾；以结果文件里的 finished_at 为收尾标志，
    # 因为采样进程在收尾前不会写出该字段，读到它即可确认 JSON 完整。
    deadline = sampler_started + budget + 120
    while time.time() < deadline:
        try:
            check = ssh(f"grep -c finished_at {sample_remote} 2>/dev/null || true")
        except (subprocess.TimeoutExpired, OSError) as exc:
            log(f"[{stage}] 等待采样收尾时 ssh 失败（{type(exc).__name__}），稍后重试")
            time.sleep(10)
            continue
        if check.stdout.strip().startswith("1"):
            break
        time.sleep(5)
    pull = ssh(f"cat {sample_remote}")
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
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if attach.returncode:
        log(f"[{stage}] 资源采样写入摘要失败: {attach.stderr.strip()[:200]}")
        return 1
    if before_snapshot is not None:
        if not attach_server_snapshot(stage, "before", before_snapshot):
            log(f"[{stage}] 负载前采集登记失败")
    after_snapshot = collect_server_snapshot(stage, "after", run_id)
    if after_snapshot is not None:
        if not attach_server_snapshot(stage, "after", after_snapshot):
            log(f"[{stage}] 停止后采集登记失败")
    db_ok = check_jobs_in_db(stage, run_id)
    if not load_ok or not db_ok:
        log(f"=== {stage} FAILED (load_ok={load_ok} db_ok={db_ok})")
        return 1
    log(f"=== {stage} done")
    print(stage + " complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
