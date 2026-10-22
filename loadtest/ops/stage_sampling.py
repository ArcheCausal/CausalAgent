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
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

LOADTEST = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOADTEST))
from runner import config, resource
from runner.capacity import execution_released
from runner.evidence import export_request_manifest, merge_job_db_check, merge_logcheck
from runner.logs import logcheck
from ops.server_evidence import epoch, request_samples, collector_provenance
from runner.rounds import read_manifest, record_run
from runner.manifest import collect_source_manifest
from ops.deployment_fingerprint import capture_fingerprint
import verify_report

KEY = str(pathlib.Path.home() / ".ssh" / "causalagent_staging")
RUNS = config.RUNS_DIR
LOG = RUNS / "_orch.log"
HOST = os.environ.get("LOADTEST_SSH_HOST", "ubuntu@203.195.194.205")
REMOTE_DIR = "/tmp/_lt"
FINAL_LIMIT = config.DRAIN_DEADLINE_SECONDS
CHECKPOINTS = (300, 1800)

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


def read_client_summary(run: pathlib.Path, run_id: str, stage: str) -> dict | None:
    """首次读取时原字节排他保存；派生 summary 不替代客户端原始证据。"""
    original = run / "client-summary.json"
    source = original if original.exists() else run / "summary.json"
    try:
        raw = source.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("run_id") != run_id or payload.get("stage") != stage:
        raise ValueError("summary_identity_mismatch")
    if not original.exists():
        try:
            with original.open("xb") as handle:
                handle.write(raw)
        except FileExistsError:
            return read_client_summary(run, run_id, stage)
    return payload


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
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{stage}"


def deploy_collectors(run_id: str) -> bool:
    remote = remote_dir(run_id)
    # mkdir 原子守卫，同一 run_id 不覆盖已有远端产物。
    result = ssh(f"mkdir -p {shlex.quote(REMOTE_DIR)} && mkdir {shlex.quote(remote)} && mkdir {shlex.quote(remote + '/ops')}")
    if result.returncode:
        return False
    files = {}
    for relative in ("job_db_check.sh", "stage_resource_sample.sh", "server_side_collect.sh", "ops/server_evidence.py"):
        files[relative] = hashlib.sha256((LOADTEST / relative).read_bytes()).hexdigest()
        if not upload(LOADTEST / relative, f"{remote}/{relative}"):
            return False
        checked = ssh(f"sha256sum {shlex.quote(remote + '/' + relative)}", attempts=1)
        if checked.returncode or checked.stdout.split()[:1] != [files[relative]]:
            return False
    write_json(RUNS / run_id / "collectors.json", {"run_id": run_id, "sha256": files, "remote_verified": True})
    return True


def collect_preflight(run_id: str) -> dict:
    payload = {"schema_version": "loadtest-collector-preflight-v1", "run_id": run_id,
               "status": "unknown", "reason": "collector_preflight_failed"}
    try:
        result = ssh(f"sudo -n python3 {shlex.quote(remote_dir(run_id) + '/ops/server_evidence.py')} "
                     f"preflight {shlex.quote(run_id)}", timeout=180, attempts=1)
        actual = json.loads(result.stdout)
        if (isinstance(actual, dict) and actual.get("run_id") == run_id
                and actual.get("schema_version") == payload["schema_version"]):
            payload = actual
            sample = payload.get("sample") or {}
            probe = payload.get("probe") or []
            healthy = (result.returncode == 0 and payload.get("status") == "collected"
                and bool(probe) and probe[0].get("probe") == 1 and not payload.get("errors")
                and all((sample.get(key) or {}).get("status") == "collected"
                        for key in ("host", "containers", "queue", "connections")))
            if not healthy:
                payload.update(status="unknown", reason="collector_preflight_failed")
            payload["collector_exit_code"] = result.returncode
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError) as exc:
        payload.update(status="unknown", reason="collector_preflight_failed", error_type=type(exc).__name__)
    write_json(RUNS / run_id / "collector-preflight.json", payload)
    return payload


def verify_derived_summary(run: pathlib.Path, summary: dict) -> dict:
    validation = {"run_id": summary["run_id"], "stage": summary["stage"],
                  "checked_at": time.time(), "status": "unknown", "problems": []}
    try:
        body, problems = verify_report.verify(summary, summary.get("logcheck"))
        report_path = run / "report.md"
        report_path.write_text(body, encoding="utf-8")
        validation.update(status="failed" if problems else "passed", problems=problems,
                          report_path=str(report_path))
    except verify_report.ReportVerificationError as exc:
        validation.update(status="failed", problems=exc.problems, error_type=type(exc).__name__)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        validation["error_type"] = type(exc).__name__
    write_json(run / "report-validation.json", validation)
    return validation


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
    data["collector"] = collector_provenance()
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


def start_sampler(run_id: str, stage: str, duration: int, final_limit: float = FINAL_LIMIT) -> bool:
    remote = remote_dir(run_id)
    sampler = (f"bash {shlex.quote(remote + '/stage_resource_sample.sh')} {shlex.quote(run_id)} "
               f"{duration} 10 {shlex.quote(stage)} "
               f"{shlex.quote(remote + '/phases.json')} {final_limit} "
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
    summary = read_client_summary(path, run_id, stage)
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
    summary = current_summary(run_id, stage)
    listing = export_request_manifest(summary, run / f"requests-{phase}.json")
    inventory = read_json(listing) or {}
    inventory["http_samples"] = request_samples(summary)
    write_json(listing, inventory)
    query_budget = min(600, max(180, 120 + 5 * (len(inventory.get("requests", [])) + len(inventory.get("job_ids", [])))))
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


def load_runtime_budget(stage: str, sampling_budget: int, parameters: dict | None = None) -> float:
    """full_flow 按每个账号内串行轮数计算；账号之间并发，不乘账号数。"""
    spec = {**config.STAGES.get(stage, {}), **(parameters or {})}
    serial = int(spec.get("flows") or 1) if spec.get("mode") == "full_flow" else 1
    if stage == "cdfm_fixed":
        serial = int(spec.get("tasks") or 1)
    if stage == "recovery":
        # 专项检查顺序执行多个真实 Job，不使用 mixed 档的持续时间推算。
        serial = 12
    return float(spec.get("load_timeout_seconds") or (max(sampling_budget, float(spec.get("duration") or 0))
                 + serial * config.JOB_OBSERVE_DEADLINE_SECONDS + FINAL_LIMIT + 300))


def planned_run(stage: str) -> tuple[str, dict, str | None]:
    manifest_path = os.environ.get("LOADTEST_ROUND_MANIFEST")
    explicit = os.environ.get("LOADTEST_RUN_ID")
    if not manifest_path:
        run_id = explicit or make_run_id(stage)
        if pathlib.Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("invalid_run_id")
        return run_id, {}, None
    entries = [row for row in read_manifest(manifest_path)["runs"]
               if row["stage"] == stage and row["disposition"] == "planned"
               and (not explicit or row["run_id"] == explicit)]
    if len(entries) != 1:
        raise ValueError("unique_planned_run_required_use_LOADTEST_RUN_ID")
    ensure_previous_cleanup(read_manifest(manifest_path), entries[0]["run_id"])
    return entries[0]["run_id"], entries[0].get("parameters") or {}, manifest_path


def ensure_previous_cleanup(manifest: dict, run_id: str) -> None:
    """重启编排也必须重新核对所有已执行前档，禁止仅跳到下一 planned 项。"""
    for row in manifest["runs"]:
        if row["run_id"] == run_id:
            return
        source = pathlib.Path(row.get("summary_path") or RUNS / row["run_id"] / "summary.json")
        executed = (source.exists() or source.parent.exists()
                    or row["disposition"] in {"adopted", "failed", "interrupted"})
        if not executed:
            continue
        summary = read_json(source)
        final = read_json(source.parent / "job-db-final.json")
        if (not summary or not final or summary.get("run_id") != row["run_id"]
                or cleanup_result(summary, final, row["run_id"])["status"] != "passed"):
            raise ValueError("previous_run_cleanup_unconfirmed:" + row["run_id"])
    raise ValueError("run_id_not_in_manifest")


def stage_arguments(stage: str, parameters: dict) -> list[str]:
    args = list(SPECS[stage][0])
    if stage == "recovery":
        return args
    for name in ("users", "rows", "seed", "tasks", "arrival_rate", "duration"):
        if parameters.get(name) is None:
            continue
        option = "--" + name.replace("_", "-")
        if option in args:
            index = args.index(option)
            args[index + 1] = str(parameters[name])
        else:
            args.extend((option, str(parameters[name])))
    return args


def scenario_result(stage: str, summary: dict | None, exit_code) -> dict:
    if summary is None:
        return {"status": "unknown", "exit_code": exit_code, "reason": "missing_summary"}
    checks = summary.get("checks") or []
    if exit_code not in (0, 3) or any(row.get("passed") is False for row in checks):
        status = "failed"
    elif exit_code == 3:
        status = "incomplete"
    elif stage == "recovery" and (summary.get("special_status") == "incomplete"
            or summary.get("special_evidence_gaps") or any(row.get("passed") is None for row in checks)):
        status = "incomplete"
    else:
        status = "passed" if exit_code == 0 else "unknown"
    return {"status": status, "exit_code": exit_code,
            "failed_checks": sum(row.get("passed") is False for row in checks),
            "unverified_checks": sum(row.get("passed") is None for row in checks)}


def resource_limit_reason(payload: dict, limits: dict) -> str | None:
    """只依据实际样本；CPU 三次是三个 jiffy 区间，不能用轮询次数代替。"""
    samples = payload.get("samples") or []
    if not samples:
        return None
    latest = samples[-1]
    host, queue, connections = (latest.get(key) or {} for key in ("host", "queue", "connections"))
    memory = host.get("mem_available_kb")
    for value, threshold, below, reason in (
        (memory / 1024 if isinstance(memory, (int, float)) else None, limits.get("mem_available_mb"), True, "memory_limit"),
        (queue.get("queued"), limits.get("queued"), False, "queue_limit"),
        (connections.get("threads_connected"), limits.get("connections"), False, "connection_limit"),
    ):
        if isinstance(value, (int, float)) and threshold is not None and (value <= threshold if below else value >= threshold):
            return reason
    threshold = limits.get("cpu_percent")
    sustained = int(limits.get("cpu_sustained_samples", limits.get("sustained_samples", 3)))
    rates = []
    for previous, current in zip(samples, samples[1:]):
        left, right = previous.get("host") or {}, current.get("host") or {}
        values = [item.get(key) for item in (left, right) for key in ("cpu_total_jiffies", "cpu_idle_jiffies")]
        if not all(isinstance(value, (int, float)) for value in values):
            rates.append(None)
            continue
        total, idle = values[2] - values[0], values[3] - values[1]
        elapsed = (epoch(right.get("timestamp")) or 0) - (epoch(left.get("timestamp")) or 0)
        rates.append(100 * (1 - idle / total) if total > 0 and 0 <= idle <= total and elapsed > 0 else None)
    if threshold is not None and len(rates) >= sustained and all(value is not None and value >= threshold for value in rates[-sustained:]):
        return "cpu_limit"
    return None


def missing_protection_metrics(payload: dict, limits: dict) -> list[str]:
    samples = payload.get("samples") or []
    if not samples:
        return ["samples"]
    latest = samples[-1]
    missing = []
    for limit, section, key in (("mem_available_mb", "host", "mem_available_kb"),
                                ("queued", "queue", "queued"),
                                ("connections", "connections", "threads_connected")):
        value = (latest.get(section) or {}).get(key)
        if limit in limits and ((latest.get(section) or {}).get("status") != "collected"
                or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            missing.append(limit)
    if "cpu_percent" in limits:
        if len(samples) < 2:
            missing.append("cpu_interval")
        else:
            left, right = (item.get("host") or {} for item in samples[-2:])
            values = [item.get(key) for item in (left, right) for key in ("cpu_total_jiffies", "cpu_idle_jiffies")]
            if (any(item.get("status") != "collected" for item in (left, right))
                    or not all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in values)):
                missing.append("cpu_interval")
            else:
                total, idle = values[2] - values[0], values[3] - values[1]
                a, b = epoch(left.get("timestamp")), epoch(right.get("timestamp"))
                if total <= 0 or not 0 <= idle <= total or a is None or b is None or b <= a:
                    missing.append("cpu_interval")
    return missing


def check_resource_protection(run_id: str, limits: dict, stop_file: pathlib.Path, *, stop_on_unknown: bool = True) -> dict:
    try:
        pulled = ssh(f"cat {shlex.quote(remote_dir(run_id) + '/resource-latest.json')}", timeout=10, attempts=1)
        payload = json.loads(pulled.stdout)
        observed = epoch(payload.get("observed_at"))
        if (pulled.returncode or payload.get("run_id") != run_id or observed is None
                or not 0 <= time.time() - observed <= 60):
            raise ValueError("missing_or_stale_sample")
        missing = missing_protection_metrics(payload, limits)
        if missing:
            if stop_on_unknown:
                write_json(stop_file, {"run_id": run_id, "stop_submitting": True,
                                      "reason": "protection_metrics_missing", "missing_metrics": missing})
            return {"status": "unknown", "reason": "protection_metrics_missing", "missing_metrics": missing}
        reason = resource_limit_reason(payload, limits)
        result = {"status": "triggered" if reason else "observed", "sample_at": observed,
                  "checked_at": time.time(), "reason": reason}
        if reason:
            write_json(stop_file, {"run_id": run_id, "stop_submitting": True, "reason": reason,
                                  "observed_at": observed, "requested_at": time.time()})
        return result
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        if stop_on_unknown:
            write_json(stop_file, {"run_id": run_id, "stop_submitting": True, "reason": "protection_sample_unavailable"})
        return {"status": "unknown", "reason": "protection_sample_unavailable"}


def cleanup_result(summary: dict, final: dict, run_id: str) -> dict:
    """放行必须覆盖全部真实请求身份、已知 Job 和接纳恢复出的 Job。"""
    expected_requests = {row.get("logical_request_id") for row in summary.get("requests", [])}
    resolved = {row.get("logical_request_id"): row for row in final.get("requests", [])}
    expected_jobs = {str(job_id) for job_id in (summary.get("job") or {}).get("created_ids", [])}
    expected_jobs.update(str(row["job_id"]) for row in summary.get("requests", []) if row.get("job_id"))
    owned_jobs = {str(row["job_id"]): row for row in final.get("jobs", []) if row.get("job_id")}
    resolved_ok = None not in expected_requests and expected_requests <= resolved.keys()
    for request_id in expected_requests:
        row = resolved.get(request_id, {})
        admission = row.get("db_admission") if row.get("admission") == "replay" else row.get("admission")
        if admission not in {"accepted", "not_found", "not_sent", "conflict", "rejected"} or row.get("identity_match") is False:
            resolved_ok = False
        if admission == "accepted" and not row.get("job_id"):
            resolved_ok = False
        if row.get("job_id"):
            expected_jobs.add(str(row["job_id"]))
    db_sufficient = (final.get("run_id") == run_id and final.get("scope") == "run"
        and final.get("status") == "collected" and bool(expected_requests or expected_jobs)
        and expected_jobs <= owned_jobs.keys() and resolved_ok
        and all(row.get("evidence_status") == "collected" and row.get("session_owner_match") in (True, 1)
                for row in owned_jobs.values()))
    business = final.get("business_drained") is True and all(row.get("business_terminal") is True for row in owned_jobs.values())
    releases = [execution_released(row) for row in owned_jobs.values()]
    release = (False if False in releases else True if releases and all(value is True for value in releases) else None) if db_sufficient else None
    return {"status": "passed" if db_sufficient and business and release else "unknown",
            "db_sufficient": db_sufficient, "admission_resolved": resolved_ok,
            "business_drained": business, "execution_released": release,
            "expected_requests": len(expected_requests), "expected_jobs": len(expected_jobs),
            "db_checked_at": final.get("db_checked_at")}


def send_boundary(run_id: str, boundary: dict) -> bool:
    remote = remote_dir(run_id)
    try:
        body = json.dumps(boundary)
        return ssh(f"printf '%s' {shlex.quote(body)} >{shlex.quote(remote + '/phases.tmp')} && "
                   f"mv {shlex.quote(remote + '/phases.tmp')} {shlex.quote(remote + '/phases.json')}",
                   timeout=15, attempts=1).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def checkpoint_at(stage: str, run_id: str, stopped: float, seconds: float, cancel: threading.Event) -> dict:
    target = stopped + seconds
    while time.time() < target:
        if cancel.wait(min(1, max(0, target - time.time()))):
            return {"status": "not_reached", "target_at": target, "actual_at": None,
                    "delay_seconds": None, "reason": "early_release_confirmed"}
    actual = time.time()
    key = format(seconds, "g")
    try:
        payload = check_jobs_in_db(stage, run_id, f"stop-plus-{key}s")
        return {"status": payload.get("status", "unknown"), "target_at": target,
                "actual_at": actual, "check_started_at": actual,
                "delay_seconds": actual - target, "finished_at": time.time(),
                "db_checked_at": payload.get("db_checked_at"), "drained": payload.get("drained"),
                "business_drained": payload.get("business_drained"), "execution_drained": payload.get("execution_drained"),
                "path": f"job-db-stop-plus-{key}s.json"}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"status": "unknown", "target_at": target, "actual_at": actual,
                "delay_seconds": actual - target, "reason": type(exc).__name__}


def main(stage: str) -> int:
    if stage not in SPECS:
        print(f"未知档位：{stage}")
        return 2
    try:
        run_id, parameters, round_manifest = planned_run(stage)
        final_limit = float(parameters.get("observation_limit_seconds", FINAL_LIMIT))
        if not math.isfinite(final_limit) or not 300 <= final_limit <= 1800:
            raise ValueError("observation_limit_must_be_between_300_and_1800")
        runtime_budget = load_runtime_budget(stage, SPECS[stage][1], parameters)
        if not math.isfinite(runtime_budget) or runtime_budget <= 0:
            raise ValueError("invalid_load_timeout")
        limits = parameters.get("resource_limits") or {}
        for key in ("cpu_percent", "mem_available_mb", "queued", "connections"):
            if key in limits and (isinstance(limits[key], bool) or not isinstance(limits[key], (int, float))
                    or not math.isfinite(limits[key]) or limits[key] <= 0):
                raise ValueError("invalid_resource_limit")
        sustained = limits.get("cpu_sustained_samples", limits.get("sustained_samples", 3))
        if not isinstance(sustained, int) or isinstance(sustained, bool) or not 1 <= sustained <= 3:
            raise ValueError("invalid_cpu_sustained_samples")
        run = RUNS / run_id
        run.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, TypeError):
        print("无法确定唯一的新运行或有效观察上限")
        return 2
    state = {"schema_version": "loadtest-orchestration-v2", "run_id": run_id, "stage": stage,
             "status": "unknown", "checkpoints": {}, "stop_submitting_at": None,
             "observation_limit_seconds": final_limit, "observation_ended_at": None, "load_exit_code": None,
             "scenario": {"status": "unknown"}, "evidence": {"status": "unknown"},
             "cleanup": {"status": "unknown"}, "continue_allowed": False, "parameters": parameters}
    state["resource_protection"] = {"enabled": bool(limits), "status": "unknown" if limits else "disabled", "limits": limits}
    stop_file = run / "stop-submitting.json"
    state_file = run / "orchestration.json"
    write_json(state_file, state)
    process, data, before, after, final = None, None, None, None, {}
    deployed, sampler_started, stop_sent = False, False, False
    cancel = threading.Event()
    pool = ThreadPoolExecutor(max_workers=2)
    pending = {}
    interrupted = False
    try:
        if round_manifest:
            source = collect_source_manifest()
            write_json(run / "client-source-before.json", source)
            expected_source = read_manifest(round_manifest).get("client", {}).get("source", {}).get("source_sha256")
            if not expected_source or source["source_sha256"] != expected_source:
                raise RuntimeError("client_source_drift")
            # 此模块由主 agent 提供；只采集白名单版本，不变更部署。
            fingerprint = capture_fingerprint()
            write_json(run / "fingerprint.json", fingerprint)
            expected = read_manifest(round_manifest).get("fingerprint", {}).get("deployment")
            if not expected or fingerprint != expected:
                raise RuntimeError("deployment_fingerprint_drift")
        if not deploy_collectors(run_id):
            raise RuntimeError("collector_deployment_failed")
        deployed = True
        state["preflight"] = collect_preflight(run_id)
        write_json(state_file, state)
        if state["preflight"].get("status") != "collected":
            raise RuntimeError("collector_preflight_failed")
        before = collect_server_snapshot(stage, "before", run_id)
        sampler_started = start_sampler(run_id, stage, math.ceil(runtime_budget + final_limit + 300), final_limit)
        if not sampler_started:
            raise RuntimeError("sampler_start_unknown")
        if limits:
            ready_until = time.monotonic() + 60
            while True:
                protection = check_resource_protection(run_id, limits, stop_file, stop_on_unknown=False)
                state["resource_protection"].update(protection)
                write_json(state_file, state)
                if protection["status"] == "triggered":
                    raise RuntimeError("resource_protection_before_load")
                if protection["status"] == "observed":
                    break
                if time.monotonic() >= ready_until:
                    raise RuntimeError("resource_protection_unavailable_before_load")
                time.sleep(1)
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", LOADTEST_RUN_ID=run_id,
                           LOADTEST_DRAIN_DEADLINE_SECONDS=str(final_limit), LOADTEST_STOP_FILE=str(stop_file))
        with (run / "load.stdout.txt").open("w", encoding="utf-8") as stdout, (run / "load.stderr.txt").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen([sys.executable, "-m", "runner", *stage_arguments(stage, parameters), "--run-id", run_id],
                                       cwd=str(LOADTEST), stdout=stdout, stderr=stderr, env=environment)
            load_deadline = time.monotonic() + runtime_budget
            protection_due = 0
            while True:
                if (limits and process.poll() is None and time.monotonic() >= protection_due
                        and state["resource_protection"]["status"] != "triggered" and not stop_file.exists()):
                    state["resource_protection"].update(check_resource_protection(run_id, limits, stop_file))
                    protection_due = time.monotonic() + 10
                phases = read_json(run / "phases.json") or read_client_summary(run, run_id, stage) or {}
                stopped = epoch(phases.get("stop_submitting_at"))
                if phases.get("run_id") == run_id and stopped is not None and stopped <= time.time():
                    state["stop_submitting_at"] = stopped
                    state["observation_ended_at"] = epoch(phases.get("observation_ended_at"))
                    if not pending:
                        # 两个检查点独立等待及查询；300 秒查询变慢不延后 1800 秒派发。
                        for seconds in sorted({300, final_limit}):
                            pending[format(seconds, "g")] = pool.submit(checkpoint_at, stage, run_id, stopped, seconds, cancel)
                    if not stop_sent:
                        stop_sent = send_boundary(run_id, {"run_id": run_id, "started_at": phases.get("started_at"),
                                                          "stop_submitting_at": stopped})
                        state["sampler_boundary_status"] = "collected" if stop_sent else "unknown"
                for key, future in pending.items():
                    if future.done() and key not in state["checkpoints"]:
                        state["checkpoints"][key] = future.result()
                state["load_exit_code"] = process.poll()
                write_json(state_file, state)
                if process.poll() is None and time.monotonic() >= load_deadline:
                    state["load_status"] = "timeout"
                    raise RuntimeError("load_timeout")
                if process.poll() is not None:
                    if state["stop_submitting_at"] is None:
                        raise RuntimeError("missing_stop_submitting_boundary")
                    checkpoint = state["checkpoints"].get("300", {})
                    at_300 = read_json(run / checkpoint.get("path", "missing.json")) or {}
                    summary = current_summary(run_id, stage)
                    released = cleanup_result(summary, at_300, run_id)["status"] == "passed"
                    if released and stop_sent and time.time() < stopped + final_limit:
                        ended = time.time()
                        confirmed = send_boundary(run_id, {"run_id": run_id, "started_at": summary.get("started_at"),
                            "stop_submitting_at": stopped, "observation_ended_at": ended,
                            "final_release_confirmed": True, "release_checked_at": at_300.get("db_checked_at")})
                        if confirmed:
                            state["early_release_confirmed_at"] = ended
                            cancel.set()
                            for key in ("1800", format(final_limit, "g")):
                                if key not in state["checkpoints"]:
                                    state["checkpoints"][key] = {"status": "not_reached", "reason": "early_release_confirmed",
                                        "target_at": stopped + float(key), "actual_at": None, "delay_seconds": None}
                            break
                    if pending and all(future.done() for future in pending.values()):
                        if final_limit < 1800:
                            state["checkpoints"].setdefault("1800", {"status": "not_reached", "reason": "configured_observation_limit",
                                "target_at": stopped + 1800, "actual_at": None, "delay_seconds": None})
                        break
                time.sleep(1)
    except KeyboardInterrupt:
        interrupted = True
        state["reason"] = "interrupted"
    except (OSError, ValueError, RuntimeError, TypeError, subprocess.SubprocessError) as exc:
        # 原异常正文可能含路径、远端输出或凭据，只落受控类型和阶段。
        state["reason"] = str(exc) if isinstance(exc, RuntimeError) and str(exc) in {
            "deployment_fingerprint_drift", "client_source_drift", "collector_deployment_failed", "sampler_start_unknown",
            "load_timeout", "missing_stop_submitting_boundary", "collector_preflight_failed"} else type(exc).__name__
    finally:
        cancel.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
        state["load_exit_code"] = process.returncode if process is not None else None
        pool.shutdown(wait=True, cancel_futures=True)
        for key, future in pending.items():
            if key not in state["checkpoints"] and not future.cancelled():
                state["checkpoints"][key] = future.result()
        # 收尾三个独立步骤；采样传输故障不能跳过快照和主库核对。
        if deployed:
            for phase in ("sampler", "after", "db", "fingerprint", "client_source"):
                try:
                    if phase == "sampler":
                        data, state["sampler_exit_code"] = collect_sampler_result(run_id, timeout=150 if sampler_started else 15)
                        if data is not None:
                            data["observation_ended_at"] = state["observation_ended_at"]
                            write_json(run / "stage-sample.json", data)
                    elif phase == "after":
                        after = collect_server_snapshot(stage, "after", run_id)
                    elif phase == "db":
                        final = check_jobs_in_db(stage, run_id)
                    elif phase == "fingerprint" and round_manifest:
                        actual = capture_fingerprint()
                        write_json(run / "fingerprint-after.json", actual)
                        expected = read_manifest(round_manifest).get("fingerprint", {}).get("deployment")
                        state["deployment_stable"] = actual == expected
                        if not state["deployment_stable"]:
                            state["reason"] = "deployment_fingerprint_drift"
                    elif phase == "client_source" and round_manifest:
                        source = collect_source_manifest()
                        write_json(run / "client-source-after.json", source)
                        expected_source = read_manifest(round_manifest).get("client", {}).get("source", {}).get("source_sha256")
                        state["client_source_stable"] = source["source_sha256"] == expected_source
                        if not state["client_source_stable"]:
                            state["reason"] = "client_source_drift"
                except (OSError, ValueError, RuntimeError, TypeError, subprocess.SubprocessError) as exc:
                    state[f"{phase}_error_type"] = type(exc).__name__
        try:
            summary = read_client_summary(run, run_id, stage)
        except ValueError:
            state["reason"] = "summary_identity_mismatch"
            summary = None
        original = run / "client-summary.json"
        if original.exists():
            state["client_summary"] = {"path": str(original), "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}
        state["scenario"] = scenario_result(stage, summary, state["load_exit_code"])
        try:
            identity = summary or current_summary(run_id, stage)
        except (OSError, ValueError):
            identity = {"run_id": run_id, "stage": stage, "requests": []}
        state["cleanup"] = cleanup_result(identity, final, run_id)
        checkpoints_ok = bool(state["checkpoints"]) and all(item.get("status") == "collected" or
            (item.get("status") == "not_reached" and item.get("reason") in {"early_release_confirmed", "configured_observation_limit"}
             and state["cleanup"]["status"] == "passed") for item in state["checkpoints"].values())
        diagnostics = (final.get("request_evidence") or {}).get("status", "unknown")
        evidence_ok = (state.get("preflight", {}).get("status") == "collected"
            and before is not None and after is not None and final.get("status") == "collected"
            and data is not None and data.get("status") == "collected" and state.get("sampler_exit_code") == "0"
            and stop_sent and checkpoints_ok and diagnostics in {"collected", "not_applicable"})
        evidence_ok = evidence_ok and (not round_manifest or (state.get("deployment_stable") is True
                                                             and state.get("client_source_stable") is True))
        evidence_ok = evidence_ok and (not limits or state["resource_protection"]["status"] in {"observed", "triggered"})
        state["evidence"] = {"status": "collected" if evidence_ok else "unknown",
            "sampler_status": data.get("status") if data else "unknown", "request_diagnostics": diagnostics,
            "checkpoints_complete": checkpoints_ok, "db_status": final.get("status", "unknown")}
        state["continue_allowed"] = (not state.get("reason") and evidence_ok and state["cleanup"]["status"] == "passed"
            and state["resource_protection"]["status"] != "triggered"
            and (state["scenario"]["status"] == "passed" or (stage in {"recovery", "multi_session_smoke"}
                and parameters.get("allow_incomplete", True) is True and state["scenario"]["status"] == "incomplete")))
        state.update(status="collected" if evidence_ok else "unknown", db_checked_at=final.get("db_checked_at"),
            server_drained=final.get("drained"), business_drained=final.get("business_drained"), execution_drained=final.get("execution_drained"),
            server_observation_ended_at=data.get("finished_at") if data else None)
        write_json(state_file, state)
        if summary is not None:
            if data is not None:
                summary["resource_curve"] = resource.summarize(data)
            summary["server_checkpoints"] = {key: read_json(run / item["path"]) if item.get("path") else item
                                             for key, item in state["checkpoints"].items()}
            for key, target in (("server_baseline", before), ("server_after", after)):
                if target is not None:
                    summary[key] = read_json(target)
            summary["orchestration"] = state
            try:
                if final:
                    summary = merge_job_db_check(summary, final)
                logs = logcheck(stage, config.base_url(), summary=summary)
                write_json(run / "logcheck.json", logs)
                summary = merge_logcheck(summary, logs)
                state["evidence"]["job_logs_status"] = logs.get("status", "unknown")
                write_json(run / "summary.json", summary)
                validation = verify_derived_summary(run, summary)
                state["report_validation"] = validation
                state["evidence"]["report_status"] = validation["status"]
                if validation["status"] != "passed":
                    state.update(status="unknown", continue_allowed=False)
                    state.setdefault("reason", "report_problems" if validation["status"] == "failed" else "report_validation_failed")
                    state["evidence"]["status"] = "unknown"
                summary["orchestration"] = state
                write_json(run / "summary.json", summary)
                write_json(state_file, state)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                state.update(reason="derived_summary_failed", continue_allowed=False)
                state["evidence"].update(status="unknown", error_type=type(exc).__name__)
                write_json(state_file, state)
        if round_manifest:
            disposition = "interrupted" if interrupted else ("adopted" if state["continue_allowed"] else "failed")
            record_run(round_manifest, stage, run_id, disposition, summary_path=str(run / "summary.json"),
                       orchestration_path=str(state_file), scenario=state["scenario"], evidence=state["evidence"], cleanup=state["cleanup"])
    log(f"[{stage}] run_id={run_id} scenario={state['scenario']['status']} evidence={state['evidence']['status']} cleanup={state['cleanup']['status']}")
    print(f"{stage}: scenario={state['scenario']['status']} evidence={state['evidence']['status']} cleanup={state['cleanup']['status']} ({run_id})")
    return 0 if state["continue_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
