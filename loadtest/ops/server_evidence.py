"""服务器只读证据采集。由 job_db_check.sh 和 stage_resource_sample.sh 调用。

部署时将本文件放在这两个脚本同级目录的 ops/ 下。仅输出字段白名单，
不读取或返回事件正文、输入正文、异常正文或凭据。
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
import base64
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def epoch(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        number = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    return number if math.isfinite(number) else None


def elapsed_ms(start, finish):
    left, right = epoch(start), epoch(finish)
    return round((right - left) * 1000, 3) if left is not None and right is not None and right >= left else None


def execute(args, timeout=45):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


class Primary:
    def __init__(self):
        self.project = os.environ.get("COMPOSE_PROJECT_NAME", "causalagent-staging")
        self.docker = ["docker"]
        if execute(["docker", "info"], 15) is None:
            self.docker = ["sudo", "-n", "docker"]
        env_path = Path(os.environ.get("CAUSALAGENT_ENV_FILE", "/opt/causalagent-staging/.env.staging"))
        try:
            content = env_path.read_text(encoding="utf-8")
        except OSError:
            content = execute(["sudo", "-n", "cat", str(env_path)]) or ""
        values = {}
        for line in content.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"MYSQL_USER", "MYSQL_DATABASE"}:
                values[key] = value.strip().strip("\"'")
        self.user = values.get("MYSQL_USER")
        self.database = values.get("MYSQL_DATABASE", "rag_eval_staging")
        self.ready = bool(self.user and re.fullmatch(r"[A-Za-z0-9_]+", self.database))

    def query(self, sql):
        if not self.ready:
            return None
        # 每次采集使用主库只读事务，mysql 失败与成功查空严格区分。
        # 密码仅在容器内从统一密钥文件读取，不经过宿主命令参数。
        command = ('. /opt/causalagent/secret-env.sh; '
                   'MYSQL_PWD="$(secret_value MYSQL_PASSWORD)" || exit 1; '
                   'export MYSQL_PWD; exec mysql "$@"')
        raw = execute([*self.docker, "exec", f"{self.project}-mysql-primary-1",
                       "sh", "-c", command, "_", f"-u{self.user}",
                       "--default-character-set=utf8mb4", "-N", "-B", "--raw",
                       self.database, "-e",
                       "SET time_zone='+00:00'; SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ; "
                       "START TRANSACTION READ ONLY; " + sql + "; COMMIT;"])
        if raw is None:
            return None
        try:
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        except json.JSONDecodeError:
            return None


def literal(value) -> str:
    # UTF-8 十六进制字面量不受引号、反斜杠或 sql_mode 影响。
    # 与业务表字符序一致，避免 MySQL 8 默认字符序导致等值比较报 1267。
    return ("CONVERT(X'" + str(value).encode("utf-8").hex()
            + "' USING utf8mb4) COLLATE utf8mb4_unicode_ci")


def manifest(path: str, run_id: str) -> dict:
    text = Path(path).read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("{"):
        payload = json.loads(text)
        if payload.get("run_id") != run_id:
            raise ValueError("manifest_run_id_mismatch")
        if not isinstance(payload.get("requests", []), list) or not isinstance(payload.get("job_ids", []), list):
            raise ValueError("invalid_manifest")
        if any(not isinstance(row, dict) for row in payload.get("requests", [])):
            raise ValueError("invalid_request_row")
        return payload
    return {"run_id": run_id, "requests": [], "job_ids": list(dict.fromkeys(
        line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )), "legacy": True}


def cdfm_evidence(events: list[dict] | None) -> dict:
    if events is None:
        return {"status": "missing", "logical_calls": None, "actual_attempts": None, "records": []}
    records = [event for event in events if str(event.get("event_type", "")).startswith("tool_call")
               and "cdfm" in str(event.get("tool_name") or event.get("capability") or "").lower()]
    calls, attempts = set(), set()
    unidentified = 0
    for item in records:
        invocation = item.get("invocation_id")
        if invocation:
            calls.add(invocation)
        retry = item.get("retry")
        if retry is None:
            retry = item.get("retry_ordinal")
        attempt = item.get("attempt")
        if invocation and attempt is not None and retry is not None:
            attempts.add((invocation, attempt, retry))
        else:
            unidentified += 1
    return {"status": "collected" if not unidentified else "missing",
            "source": "analysis_job_events.tool_call",
            "logical_calls": len(calls) if all(r.get("invocation_id") for r in records) else None,
            "actual_attempts": len(attempts) if not unidentified else None,
            "known_logical_calls": len(calls), "known_actual_attempts": len(attempts),
            "unidentified_records": unidentified, "records": records}


def lifecycle(row: dict, events: list[dict] | None, inputs: list[dict] | None) -> dict:
    row = dict(row)
    row["business_terminal"] = row.get("status") in {"succeeded", "failed", "canceled"}
    state = row.get("execution_state")
    row["execution_released"] = (state is None if "execution_state" in row
                                 and state in (None, "leased", "draining") else None)
    row["execution_release_status"] = ("released" if row["execution_released"] is True else
                                       "occupied" if row["execution_released"] is False else "unknown")
    row["db_started_at"] = row.get("started_at")
    # 当前实现保留 COALESCE(started_at, ...)。多 attempt 没有逐 attempt 时间表，
    # 不能把该列假定为当前 attempt，更不能把首条 progress/node_start 当作首次领取。
    single = row.get("attempt_count") == 1 and row.get("resume_count") == 0 and row.get("recovery_count") == 0
    row["first_started_at"] = row.get("db_started_at")
    row["first_started_at_source"] = "analysis_jobs.started_at_coalesce_first_claim" if row.get("db_started_at") else "missing"
    row["started_at_source"] = row["first_started_at_source"]
    row["current_attempt_started_at"] = row.get("db_started_at") if single else None
    row["initial_queue_ms"] = elapsed_ms(row.get("created_at"), row["first_started_at"])
    row["execution_span_ms"] = elapsed_ms(row["first_started_at"], row.get("finished_at"))
    row["server_total_ms"] = elapsed_ms(row.get("created_at"), row.get("finished_at"))
    row["persisted_event_ids"] = sorted({event["event_id"] for event in events}) if events is not None else None
    row["events_status"] = "collected" if events is not None else "missing"
    row["waiting_input"] = row.get("status") == "waiting_input" or (any(e.get("event_type") == "interrupt" for e in events) if events is not None else None)
    row["resume"] = bool(row["resume_count"]) if row.get("resume_count") is not None else None
    row["group"] = {"attempt_count": row.get("attempt_count"), "waiting_input": row["waiting_input"],
                    "resume": row["resume"], "recovery_count": row.get("recovery_count")}
    row["input_groups"] = inputs
    row["lifecycle_events"] = [{key: event.get(key) for key in ("event_id", "event_type", "created_at")}
        for event in events if event.get("event_type") in {"interrupt", "final_result", "error", "canceled"}] if events is not None else None
    row["inputs_status"] = "collected" if inputs is not None else "missing"
    row["cdfm"] = cdfm_evidence(events)
    row["missing_fields"] = [key for key in ("first_started_at", "started_at", "initial_queue_ms",
        "execution_span_ms", "server_total_ms", "persisted_event_ids", "input_groups") if row.get(key) is None]
    return row


def collect_jobs(run_id: str, path: str) -> dict:
    result = {"schema_version": "loadtest-job-db-check-v2", "run_id": run_id,
              "status": "unknown", "scope": "run", "jobs": [], "requests": [],
              "by_status_all_time": {}, "by_status_last_3h": {}, "failed_jobs": [],
              "source": "mysql-primary/read-only", "db_check_started_at": utc_now(),
              "snapshot_kind": "per_job_readonly_transactions"}
    try:
        listing = manifest(path, run_id)
    except (OSError, ValueError, TypeError):
        result.update(reason="missing_or_invalid_manifest", db_checked_at=utc_now(), collected_at=utc_now())
        return result
    db = Primary()
    job_ids = list(dict.fromkeys(str(j) for j in listing.get("job_ids", []) if j))
    associations = {}
    resolution_ok = True
    for request in listing.get("requests", []):
        resolution = {"logical_request_id": request.get("logical_request_id"), "job_id": None, "admission": "unknown"}
        admission = request.get("admission")
        if admission in {"not_sent", "conflict", "rejected"}:
            # 原幂等键命中已有 Job 不能证明被拒绝的另一请求获准。
            resolution["admission"] = admission
            result["requests"].append(resolution)
            continue
        identity = [request.get(key) for key in ("username", "session_id", "idempotency_key")]
        if all(identity):
            rows = db.query("SELECT JSON_OBJECT('job_id',j.job_id) FROM analysis_jobs j "
                "JOIN users u ON u.id=j.user_id JOIN sessions s ON s.id=j.session_id AND s.user_id=j.user_id "
                f"WHERE u.username={literal(identity[0])} AND j.session_id={literal(identity[1])} "
                f"AND j.idempotency_key={literal(identity[2])}")
            if rows is not None and len(rows) <= 1:
                resolution["admission"] = "accepted" if rows else "not_found"
                if request.get("job_id") and not rows:
                    resolution["identity_match"] = False
                if rows:
                    resolution["job_id"] = rows[0]["job_id"]
                    resolution["identity_match"] = not request.get("job_id") or request["job_id"] == resolution["job_id"]
        if resolution["admission"] == "unknown" or resolution.get("identity_match") is False:
            resolution_ok = False
        if admission == "replay":
            resolution["db_admission"] = resolution["admission"]
            resolution["admission"] = "replay"
        known = request.get("job_id")
        for job_id in (known, resolution["job_id"]):
            if job_id:
                job_id = str(job_id)
                if job_id not in job_ids:
                    job_ids.append(job_id)
                if resolution.get("job_id") == job_id and resolution.get("identity_match") is not False:
                    associations.setdefault(job_id, []).append(request.get("logical_request_id"))
        result["requests"].append(resolution)
    for job_id in job_ids:
        read_started_at = utc_now()
        condition = f"job_id={literal(job_id)}"
        fields = ("job_id", "user_id", "session_id", "status", "attempt_count", "resume_count",
                  "recovery_count", "created_at", "started_at", "finished_at", "execution_state",
                  "execution_release_reason", "execution_released_at")
        pairs = ",".join(f"'{field}',j.{field}" for field in fields)
        # row/events/inputs 在同一主库只读事务的快照内读取。
        event_keys = ("tool_name", "capability", "invocation_id", "attempt", "retry", "retry_ordinal", "status", "duration_ms")
        event_pairs = ",".join(f"'{key}',JSON_EXTRACT(payload_json,'$.{key}')" for key in event_keys)
        rows = db.query(f"SELECT JSON_OBJECT('kind','job',{pairs},'session_owner_match',"
            f"EXISTS(SELECT 1 FROM sessions s WHERE s.id=j.session_id AND s.user_id=j.user_id)) "
            f"FROM analysis_jobs j WHERE j.{condition}; "
            f"SELECT JSON_OBJECT('kind','event','event_id',id,'event_type',event_type,'created_at',created_at,{event_pairs}) "
            f"FROM analysis_job_events WHERE {condition} ORDER BY id; "
            "SELECT JSON_OBJECT('kind','input','sequence',sequence,'input_type',input_type,'created_at',created_at) "
            f"FROM analysis_job_inputs WHERE {condition} ORDER BY input_id")
        logical_ids = list(dict.fromkeys(associations.get(job_id, [])))
        base = {"job_id": job_id, "logical_request_id": logical_ids[0] if logical_ids else None,
                "logical_request_ids": logical_ids, "identity_match": True if logical_ids else None,
                "db_read_started_at": read_started_at,
                "db_read_finished_at": utc_now()}
        jobs = [r for r in rows or [] if r.get("kind") == "job"]
        if rows is None or len(jobs) != 1:
            base.update(status="unknown" if rows is None else "not_found", missing=True,
                        evidence_status="missing" if rows is None else "not_found",
                        persisted_event_ids=None, first_started_at=None, started_at=None,
                        created_at=None, finished_at=None, initial_queue_ms=None, execution_span_ms=None, server_total_ms=None)
        else:
            base.update(lifecycle(jobs[0], [r for r in rows if r.get("kind") == "event"],
                                  [r for r in rows if r.get("kind") == "input"]))
            base.pop("kind", None)
            base["evidence_status"] = "collected"
            base["cdfm_calls"] = [{"job_id": job_id, "invocation_id": event.get("invocation_id"),
                "attempt": event.get("attempt"),
                "retry": event.get("retry") if event.get("retry") is not None else event.get("retry_ordinal"),
                "status": event.get("status"), "duration_ms": event.get("duration_ms"),
                "event_id": event["event_id"]} for event in base["cdfm"]["records"]]
        result["jobs"].append(base)
    has_evidence = bool(job_ids) or any(row.get("admission") in {"accepted", "not_found"}
        or (row.get("admission") == "replay" and row.get("db_admission") in {"accepted", "not_found"})
        for row in result["requests"])
    complete = resolution_ok and has_evidence and all(
        j.get("evidence_status") == "collected" and j.get("session_owner_match") in (True, 1)
        for j in result["jobs"])
    result.update(status="collected" if complete else "unknown", db_checked_at=utc_now(), collected_at=utc_now())
    result["by_status_run"] = dict(Counter(j["status"] for j in result["jobs"]))
    result["failed_jobs"] = [j for j in result["jobs"] if j.get("status") == "failed"]
    result["business_drained"] = all(j.get("business_terminal") is True for j in result["jobs"]) if complete else None
    released = [j.get("execution_released") for j in result["jobs"]]
    result["execution_drained"] = (False if False in released else
        True if all(value is True for value in released) else None) if complete else None
    result["execution_release_counts"] = dict(Counter(
        j.get("execution_release_status", "unknown") for j in result["jobs"]))
    result["execution_release_reasons"] = dict(Counter(
        j.get("execution_release_reason") or "missing" for j in result["jobs"]))
    result["drained"] = (False if result["business_drained"] is False or result["execution_drained"] is False else
        True if result["business_drained"] is True and result["execution_drained"] is True else None)
    # 沙盒资源释放与业务终态、执行释放并列判定；缺少本轮会话证据时保持未知。
    sandboxes = sandbox_sessions(db, job_ids)
    sessions = sandboxes.get("sessions") or []
    sandbox_status_known = sandboxes.get("status") == "collected"
    open_sessions = [item for item in sessions if item.get("status") != "closed"]
    uncertain = [item for item in sessions if item.get("status") == "uncertain"]
    sandbox_closed = (not open_sessions) if sandbox_status_known else None
    result["sandbox"] = {
        "schema_version": "loadtest.sandbox-evidence.v1",
        "status": sandboxes.get("status"), "reason": sandboxes.get("reason"),
        "jobs_with_sessions": len({item.get("job_id") for item in sessions if item.get("job_id")}),
        "session_count": len(sessions),
        "closed": sum(1 for item in sessions if item.get("status") == "closed"),
        "open": len(open_sessions), "uncertain": len(uncertain),
        "succeeded_operations": sum(1 for item in sessions if item.get("operation_status") == "succeeded"),
        "pending_operations": sum(1 for item in sessions if item.get("operation_status") == "pending"),
        "remote_identity_known": sum(1 for item in sessions if item.get("remote_sandbox_id")),
        "timings": sandbox_timing(sandboxes), "sessions": sessions,
    }
    result["sandbox_drained"] = sandbox_closed
    # 完整排空要求业务终态、执行释放、沙盒关闭三类证据都成立且身份核对完整。
    result["complete_drained"] = (
        None if complete is False or sandbox_closed is None else
        bool(complete and result["business_drained"] is True and result["execution_drained"] is True
             and sandbox_closed is True and all(item.get("remote_sandbox_id") or item.get("status") == "closed"
                                               for item in sessions)))
    return result


SANDBOX_SESSION_FIELDS = (
    "request_id", "remote_sandbox_id", "status", "operation_status",
    "attempt_count", "lease_epoch", "created_at", "closed_at", "heartbeat_at", "expires_at",
)


def request_label(request_id: str) -> str | None:
    """与控制服务一致的请求标签：32 字节 base32 小写、去掉填充。"""
    try:
        return base64.b32encode(bytes.fromhex(request_id)).decode("ascii").rstrip("=").lower()
    except (ValueError, AttributeError):
        return None


def sandbox_sessions(db: "Primary", job_ids: list[str]) -> dict:
    """按本轮 Job 读取沙盒生命周期，并给出阶段时间与远端身份。"""
    result = {"status": "missing", "sessions": [], "request_ids": [], "reason": None}
    if not job_ids:
        result["reason"] = "no_job_ids"
        return result
    # job_id 是 VARCHAR 主键；只接受受限 ASCII 标识符后直接引用，避免字符序转换失败。
    safe_ids = [job_id for job_id in job_ids if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", str(job_id))]
    if len(safe_ids) != len(job_ids):
        result["reason"] = "invalid_job_id"
        return result
    quoted = ",".join("'" + job_id + "'" for job_id in safe_ids)
    rows = db.query(
        "SELECT JSON_OBJECT(" + ",".join(f"'{name}',s.{name}" for name in SANDBOX_SESSION_FIELDS)
        + ",'stage_timings',JSON_EXTRACT(s.metadata,'$.stage_timings')"
        + ",'execute_plan_version',JSON_EXTRACT(s.metadata,'$.execute_plan_version'))"
        + " FROM analysis_sandbox_sessions s WHERE s.job_id IN (" + quoted + ") ORDER BY s.id"
    )
    if rows is None:
        result["reason"] = "query_failed"
        return result
    sessions = []
    for row in rows:
        created, closed = epoch(row.get("created_at")), epoch(row.get("closed_at"))
        sessions.append({
            **{name: row.get(name) for name in SANDBOX_SESSION_FIELDS},
            "request_label": request_label(str(row.get("request_id") or "")),
            "stage_timings": row.get("stage_timings") if isinstance(row.get("stage_timings"), dict) else None,
            "plan_version_recorded": bool(row.get("execute_plan_version")),
            "lifetime_ms": round((closed - created) * 1000, 3) if created is not None and closed is not None else None,
        })
    result.update(status="collected", sessions=sessions,
                  request_ids=[item["request_id"] for item in sessions])
    return result


def sandbox_timing(payload: dict) -> dict:
    """汇总阶段时间；缺失阶段显示为未采集，不做推算。"""
    stages = {"reserve_committed": [], "remote_created": [], "initialized": [],
              "execute_started": [], "execute_finished": []}
    for session in payload.get("sessions") or []:
        timings = session.get("stage_timings") or {}
        for name in stages:
            value = epoch(timings.get(name)) if isinstance(timings, dict) else None
            if value is not None:
                stages[name].append(value)
    result = {}
    for name, values in stages.items():
        result[name] = {"samples": len(values),
                        "min_ms": round((max(values) - min(values)) * 1000, 3) if len(values) > 1 else None}
    return result


def host_sample() -> dict:
    result = {"timestamp": utc_now()}
    result = {"timestamp": utc_now()}
    try:
        loads = Path("/proc/loadavg").read_text().split()
        result.update(zip(("load1", "load5", "load15"), map(float, loads[:3])))
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, value = line.split(":", 1)
            memory[name] = int(value.strip().split()[0])
        for target, source in (("mem_total_kb", "MemTotal"), ("mem_available_kb", "MemAvailable"),
                               ("swap_total_kb", "SwapTotal"), ("swap_free_kb", "SwapFree")):
            result[target] = memory.get(source)
        counters = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        # guest/guest_nice 已包含在 user/nice 中，不能重复加到分母。
        result.update(cpu_total_jiffies=sum(counters[:8]), cpu_idle_jiffies=sum(counters[3:5]), status="collected")
    except (OSError, ValueError, IndexError):
        result["status"] = "missing"
    return result


def attributed_containers(db: "Primary", owners: dict[str, str] | None = None) -> dict:
    """按应用标签与请求标签归属动态沙盒容器；名称只作为补充线索。

    owners 是远端容器标识到本轮请求标签的映射，来自本轮沙盒会话证据。未取得标签或
    映射的容器单列为未归属，不按名称猜测成本轮资源。
    """
    owners = owners or {}
    result = {"status": "unknown", "attribute": "docker_labels", "mine": [], "background": [],
              "unattributed": [], "exited": [], "oom_killed": []}
    listing = execute([*db.docker, "ps", "-a", "--filter", "label=application=causalagent",
                       "--format", "{{json .}}"], 30)
    if listing is None:
        return result
    try:
        items = [json.loads(line) for line in listing.splitlines() if line.strip()]
    except (ValueError, TypeError):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Names") or "")
        labels = item.get("Labels") or ""
        request_label = None
        for part in str(labels).split(","):
            key, separator, value = part.partition("=")
            if separator and key.strip() == "request":
                request_label = value.strip()
        entry = {"name": name, "request_label": request_label,
                 "status": str(item.get("Status") or ""),
                 "state": str(item.get("State") or "")}
        if "exited" in entry["state"].lower() or "exited" in entry["status"].lower():
            result["exited"].append(name)
        owner = owners.get(request_label) if request_label else None
        if owner:
            entry["owner_request_id"] = owner
            result["mine"].append(entry)
        elif request_label:
            result["background"].append(entry)
        else:
            result["unattributed"].append(entry)
    result["status"] = "collected"
    return result


def _container_owners(db: "Primary") -> dict[str, str]:
    """本轮会话请求标签到请求 ID 的映射；未取得会话记录时为空。"""
    rows = db.query("SELECT JSON_OBJECT('request_id',request_id) FROM analysis_sandbox_sessions")
    owners: dict[str, str] = {}
    for row in rows or []:
        request_id = str(row.get("request_id") or "")
        label = request_label(request_id)
        if label:
            owners[label] = request_id
    return owners


def resource_sample(db: Primary) -> dict:
    stats = execute([*db.docker, "stats", "--no-stream", "--format", "{{json .}}"])
    states = execute([*db.docker, "ps", "-a", "--format", "{{.Names}}={{.State}}/{{.Status}}"])
    containers = {"status": "unknown", "stats": [], "container_states": None, "exited_containers": None}
    if stats is not None and states is not None:
        try:
            for line in stats.splitlines():
                item = json.loads(line)
                if item.get("Name", "").startswith(db.project + "-"):
                    containers["stats"].append({"name": item["Name"], "cpu": item.get("CPUPerc"), "mem": item.get("MemUsage")})
            entries = [line for line in states.splitlines() if line.startswith(db.project + "-")]
            containers.update(status="collected" if entries else "unknown", container_states=",".join(entries),
                              exited_containers=",".join(line.split("=")[0] for line in entries if "=exited/" in line))
        except (ValueError, TypeError):
            containers["status"] = "unknown"
    # 队列是容量背景，不作为本轮 Job 排空证明。draining 是 execution_state，非 status。
    queue = db.query("SELECT JSON_OBJECT('total',COUNT(*),'queued',COALESCE(SUM(status='queued'),0),"
        "'running',COALESCE(SUM(status='running'),0),'waiting_input',COALESCE(SUM(status='waiting_input'),0),"
        "'draining',COALESCE(SUM(execution_state='draining'),0)) FROM analysis_jobs")
    connections = db.query("SELECT JSON_OBJECT('name',VARIABLE_NAME,'value',VARIABLE_VALUE) "
        "FROM performance_schema.global_status WHERE VARIABLE_NAME IN "
        "('Threads_connected','Threads_running','Aborted_connects','Uptime')")
    connection_data = {"status": "unknown"}
    if connections is not None:
        values = {str(row["name"]).lower(): row["value"] for row in connections}
        keys = {"threads_connected": "threads_connected", "threads_running": "threads_running",
                "aborted_connects": "aborted_connects", "uptime_seconds": "uptime"}
        connection_data.update({key: int(values[source]) if str(values.get(source, "")).isdigit() else None
                                for key, source in keys.items()})
        if all(connection_data.get(key) is not None for key in keys):
            connection_data["status"] = "collected"
    sandbox_containers = attributed_containers(db, _container_owners(db))
    return {"host": host_sample(), "containers": containers,
            "sandbox_containers": sandbox_containers,
            "queue": {"status": "collected", "scope": "database_background", **queue[0]} if queue else {"status": "unknown"},
            "connections": connection_data}


def sample_window(run_id, duration, interval=10, label=None, marker=None, final_limit=None):
    duration, interval = float(duration), float(interval)
    final_limit = float(final_limit if final_limit is not None else os.environ.get("LOADTEST_DRAIN_DEADLINE_SECONDS", "1800"))
    if not all(map(math.isfinite, (duration, interval, final_limit))) or duration < 0 or interval <= 0 or final_limit <= 0:
        raise ValueError("invalid_sample_window")
    deadline = time.monotonic() + duration
    payload = {"schema_version": "loadtest-server-sample-v2", "run_id": run_id, "label": label or run_id,
               "started_at": utc_now(), "interval_seconds": interval, "cpu_count": os.cpu_count(), "samples": [],
               "submission_stopped_at": None, "load_started_at": None, "observation_limit_seconds": final_limit,
               "checkpoints": {}, "status": "unknown"}
    if final_limit < 300:
        payload["checkpoints"]["300"] = {"status": "missing", "reason": "observation_limit_before_checkpoint"}
    db = Primary()
    while True:
        if marker and payload["submission_stopped_at"] is None:
            try:
                boundary = json.loads(Path(marker).read_text(encoding="utf-8"))
                stopped = epoch(boundary.get("stop_submitting_at") or boundary.get("submission_stopped_at"))
                if boundary.get("run_id") == run_id and stopped is not None and stopped <= time.time():
                    payload["submission_stopped_at"] = stopped
                    payload["load_started_at"] = epoch(boundary.get("started_at"))
                    deadline = time.monotonic() + max(0, stopped + final_limit - time.time())
            except (OSError, ValueError, TypeError):
                pass
        sample = resource_sample(db)
        observed = epoch(sample["host"]["timestamp"])
        stopped = payload["submission_stopped_at"]
        sample["phase"] = "observation" if stopped is not None and observed >= stopped else "load"
        sample["seconds_after_submission_stop"] = observed - stopped if stopped is not None else None
        payload["samples"].append(sample)
        if stopped is not None:
            for seconds in sorted({300, final_limit}):
                key = format(seconds, "g")
                if observed >= stopped + seconds and key not in payload["checkpoints"]:
                    payload["checkpoints"][key] = {"status": "collected", "target_at": stopped + seconds,
                        "observed_at": observed, "delay_seconds": observed - stopped - seconds,
                        "sample_index": len(payload["samples"]) - 1}
        if time.monotonic() >= deadline:
            break
        wait = min(interval, max(0, deadline - time.monotonic()))
        if stopped is not None and "300" not in payload["checkpoints"]:
            wait = min(wait, max(0.1, stopped + 300 - time.time()))
        time.sleep(wait)
    payload["finished_at"] = utc_now()
    covered = all(payload["checkpoints"].get(format(seconds, "g"), {}).get("status") == "collected"
                  for seconds in (300, final_limit))
    healthy = all(section.get("status") == "collected" for sample in payload["samples"]
                  for section in (sample["host"], sample["containers"], sample["queue"], sample["connections"]))
    payload["status"] = "collected" if healthy and (covered or not marker) else "unknown"
    payload["observation_status"] = "collected" if covered else "missing"
    return payload


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "db":
        payload = collect_jobs(sys.argv[2], sys.argv[3] if len(sys.argv) >= 4 else "")
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload["status"] == "collected" else 1
    if len(sys.argv) >= 3 and sys.argv[1] == "sample":
        payload = sample_window(*sys.argv[2:]) if len(sys.argv) >= 4 else sample_window(sys.argv[2], 120)
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload["status"] == "collected" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
