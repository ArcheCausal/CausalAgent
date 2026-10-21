"""编排真实负载、300/1800 秒主库检查点和持续资源观察。

服务器脚本及 ops/server_evidence.py 每轮上传到独立目录，不复用其他轮的产物。
停止提交边界取 phases.json 或本轮 summary 的明确时间字段；线程观察结束、数据库检查完成单独记录。
没有可证明的停止边界或采集失败时，orchestration.json 保持 unknown。
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import shlex
import subprocess
import sys
import time

LOADTEST = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOADTEST))
from runner import config, resource
from runner.evidence import export_request_manifest, merge_job_db_check
from ops.server_evidence import epoch

KEY = str(pathlib.Path.home() / ".ssh" / "causalagent_staging")
RUNS = LOADTEST / "local" / "runs"
LOG = RUNS / "_orch.log"
HOST = os.environ.get("LOADTEST_SSH_HOST", "ubuntu@203.195.194.205")
REMOTE_DIR = "/tmp/_lt"
FINAL_LIMIT = config.DRAIN_DEADLINE_SECONDS
CHECKPOINTS = tuple(sorted({300, FINAL_LIMIT}))

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
    "cdfm_fixed": (["stage", "--stage", "cdfm_fixed"], 7500),
    "cdfm_rate": (["stage", "--stage", "cdfm_rate"], 500),
}


def log(message: str) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def write_json(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: pathlib.Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def ssh(command: str, timeout: int = 45, attempts: int = 2) -> subprocess.CompletedProcess:
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2", HOST, command],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
            if result.returncode == 0 or attempt == attempts - 1:
                return result
        except (subprocess.TimeoutExpired, OSError):
            if attempt == attempts - 1:
                raise
        time.sleep(1)
    raise RuntimeError("ssh_failed")


def upload(local: pathlib.Path, remote: str) -> bool:
    try:
        result = subprocess.run(
            ["scp", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             str(local), f"{HOST}:{remote}"], capture_output=True, timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def remote_dir(run_id: str) -> str:
    return f"{REMOTE_DIR}/{run_id}"


def make_run_id(stage: str) -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{stage}"


def deploy_collectors(run_id: str) -> bool:
    remote = remote_dir(run_id)
    # mkdir 原子守卫，同一 run_id 不覆盖已有远端产物。
    result = ssh(f"mkdir -p {shlex.quote(REMOTE_DIR)} && mkdir {shlex.quote(remote)} && mkdir {shlex.quote(remote + '/ops')}")
    if result.returncode:
        return False
    for relative in ("job_db_check.sh", "stage_resource_sample.sh", "server_side_collect.sh", "ops/server_evidence.py"):
        if not upload(LOADTEST / relative, f"{remote}/{relative}"):
            return False
    return True


def collect_server_snapshot(stage: str, phase: str, run_id: str) -> pathlib.Path | None:
    remote = remote_dir(run_id)
    try:
        result = ssh(f"sudo -n bash {shlex.quote(remote + '/server_side_collect.sh')} "
                     f"{shlex.quote(run_id)} {shlex.quote(phase)}", timeout=60, attempts=2)
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None
    if result.returncode or not isinstance(data, dict) or data.get("run_id") != run_id:
        return None
    target = RUNS / run_id / f"server-{phase}.json"
    write_json(target, data)
    log(f"[{stage}] {phase} snapshot saved")
    return target


def sampler_running(run_id: str) -> bool:
    """核验采样器保存的真实 PID 及精确 argv，避免匹配到探测 shell。"""
    remote = remote_dir(run_id)
    probe = ("import pathlib,sys; "
             "pid=pathlib.Path(sys.argv[1]).read_text().strip(); "
             "assert pid.isdigit() and int(pid)>0; "
             "args=pathlib.Path('/proc',pid,'cmdline').read_bytes().split(b'\\0'); "
             "assert args[1:4]==[sys.argv[2].encode(),b'sample',sys.argv[3].encode()]")
    command = " ".join(shlex.quote(value) for value in (
        "python3", "-c", probe, remote + "/sample.pid", remote + "/ops/server_evidence.py", run_id
    ))
    try:
        return ssh(command, timeout=15, attempts=1).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def start_sampler(run_id: str, stage: str, duration: int) -> bool:
    remote = remote_dir(run_id)
    sampler = (f"bash {shlex.quote(remote + '/stage_resource_sample.sh')} {shlex.quote(run_id)} "
               f"{duration} 10 {shlex.quote(stage)} "
               f"{shlex.quote(remote + '/phases.json')} {FINAL_LIMIT} "
               f">{shlex.quote(remote + '/sample.json')} 2>{shlex.quote(remote + '/sample.err')} & "
               f"sampler_pid=$!; printf '%s' \"$sampler_pid\" >{shlex.quote(remote + '/sample.pid')}; "
               f"wait \"$sampler_pid\"; printf '%s' $? >{shlex.quote(remote + '/sample.exit')}")
    # 只启动一次；无论 SSH 回执是否成功，都用真实进程身份确认启动。
    try:
        ssh(f"nohup bash -c {shlex.quote(sampler)} </dev/null >/dev/null 2>&1 &", attempts=1)
    except (OSError, subprocess.SubprocessError):
        pass
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if sampler_running(run_id):
            return True
        time.sleep(2)
    return False


def collect_sampler_result(run_id: str, timeout: float = 150) -> tuple[dict | None, str | None]:
    """在同一等待窗口内重试回收，传输失败不使其他收尾取证中断。"""
    remote = remote_dir(run_id)
    deadline = time.monotonic() + timeout
    exit_code = None
    while time.monotonic() < deadline:
        try:
            budget = max(1, min(30, int(deadline - time.monotonic())))
            done = ssh(f"cat {shlex.quote(remote + '/sample.exit')}", timeout=budget, attempts=1)
            if done.returncode == 0:
                exit_code = done.stdout.strip()
                budget = max(1, min(30, int(deadline - time.monotonic())))
                pulled = ssh(f"cat {shlex.quote(remote + '/sample.json')}", timeout=budget, attempts=1)
                if pulled.returncode == 0:
                    data = json.loads(pulled.stdout)
                    if (isinstance(data, dict) and data.get("run_id") == run_id
                            and data.get("finished_at")):
                        return data, exit_code
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            pass
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    return None, exit_code


def current_summary(run_id: str, stage: str) -> dict:
    """运行中从真实追加账本重建请求身份，兼容尚未生成 summary 的检查点。"""
    path = RUNS / run_id
    summary = read_json(path / "summary.json")
    if summary is not None:
        if summary.get("run_id") != run_id:
            raise ValueError("summary_run_id_mismatch")
        return summary
    requests, ids = {}, []
    ledger = path / "jobs.jsonl"
    if ledger.exists():
        with ledger.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    # 正在追加的最后一行可能尚未写完；未登记的身份不补造。
                    continue
                if row.get("run_id") != run_id:
                    continue
                if row.get("logical_request_id"):
                    requests[row["logical_request_id"]] = row
                if row.get("job_id"):
                    ids.append(str(row["job_id"]))
    return {"run_id": run_id, "stage": stage, "requests": list(requests.values()),
            "job": {"created_ids": list(dict.fromkeys(ids))}}


def check_jobs_in_db(stage: str, run_id: str, phase: str = "final") -> dict:
    run = RUNS / run_id
    listing = export_request_manifest(current_summary(run_id, stage), run / f"requests-{phase}.json")
    inventory = read_json(listing) or {}
    query_budget = min(600, max(60, 30 + 5 * (len(inventory.get("requests", [])) + len(inventory.get("job_ids", [])))))
    remote = remote_dir(run_id)
    missing = {"schema_version": "loadtest-job-db-check-v2", "run_id": run_id, "scope": "run",
               "status": "unknown", "reason": "collector_failed", "jobs": [], "requests": [],
               "db_checked_at": None, "collected_at": None,
               "query_started_at": time.time(), "query_budget_seconds": query_budget}
    if not upload(listing, f"{remote}/requests-{phase}.json"):
        payload = missing
    else:
        try:
            result = ssh(f"sudo -n timeout {query_budget}s bash {shlex.quote(remote + '/job_db_check.sh')} "
                         f"{shlex.quote(run_id)} {shlex.quote(remote + '/requests-' + phase + '.json')}",
                         timeout=query_budget + 15, attempts=1)
        except (OSError, subprocess.SubprocessError):
            payload = {**missing, "reason": "query_timeout_or_transport_failure", "collection_failed_at": time.time()}
            write_json(run / f"job-db-{phase}.json", payload)
            return payload
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            payload = missing
        if not isinstance(payload, dict) or payload.get("run_id") != run_id:
            payload = missing
        if result.returncode and payload.get("status") == "collected":
            payload["status"] = "unknown"
        payload["collector_exit_code"] = result.returncode
    payload["query_budget_seconds"] = query_budget
    if payload.get("status") == "unknown":
        payload["collection_failed_at"] = time.time()
    write_json(run / f"job-db-{phase}.json", payload)
    return payload


def load_runtime_budget(stage: str, sampling_budget: int) -> float:
    """full_flow 按每个账号内串行轮数计算；账号之间并发，不乘账号数。"""
    spec = config.STAGES.get(stage, {})
    serial = int(spec.get("flows") or 1) if spec.get("mode") == "full_flow" else 1
    if stage == "cdfm_fixed":
        serial = int(spec.get("tasks") or 1)
    if stage == "recovery":
        # 专项检查顺序执行多个真实 Job，不使用 mixed 档的持续时间推算。
        serial = 12
    return max(sampling_budget, float(spec.get("duration") or 0)) + serial * config.JOB_OBSERVE_DEADLINE_SECONDS + FINAL_LIMIT + 300


def main(stage: str) -> int:
    if stage not in SPECS:
        print(f"未知档位：{stage}")
        return 2
    arguments, budget = SPECS[stage]
    if not math.isfinite(FINAL_LIMIT) or FINAL_LIMIT <= 0:
        print("最终观察上限必须是有限正数")
        return 2
    run_id = make_run_id(stage)
    run = RUNS / run_id
    run.mkdir(parents=True, exist_ok=False)
    state = {"schema_version": "loadtest-orchestration-v2", "run_id": run_id, "stage": stage,
             "status": "unknown", "checkpoints": {}, "stop_submitting_at": None,
             "observation_limit_seconds": FINAL_LIMIT,
             "observation_ended_at": None, "load_exit_code": None}
    state_file = run / "orchestration.json"
    write_json(state_file, state)
    process = None
    if FINAL_LIMIT < 300:
        state["checkpoints"]["300"] = {"status": "missing", "reason": "observation_limit_before_checkpoint"}
    try:
        if not deploy_collectors(run_id):
            raise RuntimeError("collector_deployment_failed")
        before = collect_server_snapshot(stage, "before", run_id)
        remote = remote_dir(run_id)
        if not start_sampler(run_id, stage, math.ceil(load_runtime_budget(stage, budget) + FINAL_LIMIT + 300)):
            raise RuntimeError("sampler_start_unknown")
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        with (run / "load.stdout.txt").open("w", encoding="utf-8") as stdout, (run / "load.stderr.txt").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen([sys.executable, "-m", "runner", *arguments, "--run-id", run_id],
                                       cwd=str(LOADTEST), stdout=stdout, stderr=stderr, env=environment)
            load_deadline = time.monotonic() + load_runtime_budget(stage, budget)
            stop_sent = False
            while True:
                phases = read_json(run / "phases.json") or read_json(run / "summary.json") or {}
                stopped = epoch(phases.get("stop_submitting_at"))
                phase_run = phases.get("run_id", run_id)
                if stopped is not None and phase_run == run_id and stopped <= time.time():
                    state["stop_submitting_at"] = stopped
                    state["observation_ended_at"] = epoch(phases.get("observation_ended_at"))
                    if not stop_sent:
                        boundary = json.dumps({"run_id": run_id, "started_at": phases.get("started_at"), "stop_submitting_at": stopped})
                        for attempt in range(3):
                            try:
                                sent = ssh(f"printf '%s' {shlex.quote(boundary)} >{shlex.quote(remote + '/phases.tmp')} && "
                                           f"mv {shlex.quote(remote + '/phases.tmp')} {shlex.quote(remote + '/phases.json')}", attempts=1)
                                stop_sent = sent.returncode == 0
                            except (OSError, subprocess.SubprocessError):
                                stop_sent = False
                            if stop_sent:
                                break
                            time.sleep(2)
                        state["sampler_boundary_status"] = "collected" if stop_sent else "unknown"
                    for seconds in CHECKPOINTS:
                        key = format(seconds, "g")
                        if key not in state["checkpoints"] and time.time() >= stopped + seconds:
                            started = time.time()
                            payload = check_jobs_in_db(stage, run_id, f"stop-plus-{key}s")
                            state["checkpoints"][key] = {"target_at": stopped + seconds,
                                "check_started_at": started, "db_checked_at": payload.get("db_checked_at"),
                                "delay_seconds": started - stopped - seconds, "status": payload.get("status", "unknown"),
                                "drained": payload.get("drained"),
                                "business_drained": payload.get("business_drained"),
                                "execution_drained": payload.get("execution_drained"),
                                "path": f"job-db-stop-plus-{key}s.json"}
                state["load_exit_code"] = process.poll()
                write_json(state_file, state)
                if process.poll() is None and time.monotonic() >= load_deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=15)
                    state["load_exit_code"] = process.returncode
                    state["load_status"] = "unknown"
                if process.poll() is not None:
                    if state["stop_submitting_at"] is None:
                        raise RuntimeError("missing_stop_submitting_boundary")
                    if all(format(seconds, "g") in state["checkpoints"] for seconds in CHECKPOINTS):
                        break
                time.sleep(2)
        data, state["sampler_exit_code"] = collect_sampler_result(run_id)
        state["sampler_result_status"] = "collected" if data is not None else "unknown"
        if data is not None:
            data["observation_ended_at"] = state["observation_ended_at"]
            write_json(run / "stage-sample.json", data)
        after = collect_server_snapshot(stage, "after", run_id)
        final = check_jobs_in_db(stage, run_id)
        summary = read_json(run / "summary.json")
        if summary is None:
            raise RuntimeError("missing_final_summary")
        if data is not None:
            summary["resource_curve"] = resource.summarize(data)
        summary["server_checkpoints"] = {seconds: read_json(run / item["path"]) if item.get("path") else item
                                         for seconds, item in state["checkpoints"].items()}
        for key, target in (("server_baseline", before), ("server_after", after)):
            if target is not None:
                snapshot = read_json(target)
                if key == "server_after":
                    snapshot["drain_seconds"] = (summary.get("server_after") or {}).get("drain_seconds")
                summary[key] = snapshot
        healthy = (process.returncode == 0 and final.get("status") == "collected" and final.get("drained") is True
                   and data is not None and data.get("status") == "collected" and state.get("sampler_exit_code") == "0"
                   and stop_sent
                   and before is not None and after is not None
                   and all(item.get("status") == "collected" for item in state["checkpoints"].values()))
        state.update(status="collected" if healthy else "unknown", db_checked_at=final.get("db_checked_at"),
                     server_drained=final.get("drained"), business_drained=final.get("business_drained"),
                     execution_drained=final.get("execution_drained"),
                     server_observation_ended_at=data.get("finished_at") if data is not None else None)
        summary["orchestration"] = state
        summary = merge_job_db_check(summary, final)
        write_json(run / "summary.json", summary)
        log(f"[{stage}] run_id={run_id} status={state['status']}")
        print(f"{stage}: {state['status']} ({run_id})")
        return 0 if healthy else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        state.update(status="unknown", reason=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        log(f"[{stage}] run_id={run_id} status=unknown reason={type(exc).__name__}")
        return 1
    finally:
        # 异常不能让已启动的客户端继续提交；服务器采样保持有界观察，不停止业务服务。
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
        write_json(state_file, state)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
