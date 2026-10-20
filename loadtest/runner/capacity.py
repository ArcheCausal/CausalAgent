"""本轮逻辑请求、唯一 Job 和主库证据的纯数据汇总，不访问业务服务。"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone

from runner.metrics import percentile

TERMINAL = {"succeeded", "failed", "canceled"}


def epoch(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value or str(value).lower() in {"null", "missing", "none"}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _span(start, end) -> float | None:
    a, b = epoch(start), epoch(end)
    return max(0, (b - a) * 1000) if a is not None and b is not None and b >= a else None


def _rate(count: int, total: int) -> float | None:
    return count / total if total else None


def enrich_summary(source: dict) -> dict:
    """保留原始证据，补充带明确分母的 v2 指标；缺失证据不转换为零。"""
    summary = deepcopy(source)
    requests = summary.get("requests")
    has_ledger = isinstance(requests, list)
    requests = requests if has_ledger else []
    db = summary.get("job_db_check") or {}
    known_ids = {str(r["job_id"]) for r in requests if r.get("job_id")}
    known_ids.update(str(i) for i in summary.get("job", {}).get("created_ids", []) if i)
    for records in (summary.get("job", {}).get("samples", []), summary.get("sse", {}).get("samples", [])):
        known_ids.update(str(r["job_id"]) for r in records if r.get("job_id"))
    legacy_scoped = (db.get("schema_version") == "loadtest-job-db-check-v1"
                     and db.get("scope") is None and isinstance(db.get("jobs"), list)
                     and all(str(r.get("job_id")) in known_ids for r in db["jobs"]))
    scoped = (bool(db.get("run_id")) and db.get("run_id") == summary.get("run_id")
              and (db.get("scope") == "run" or legacy_scoped))
    db_available = scoped and db.get("status") not in {"missing", "error"}
    db_rows = {str(r["job_id"]): r for r in db.get("jobs", [])
               if r.get("job_id")} if db_available else {}
    resolutions = {str(r.get("logical_request_id")): r for r in db.get("requests", [])} if scoped and db_available else {}
    for row in db_rows.values():
        if row.get("logical_request_id"):
            resolutions.setdefault(str(row["logical_request_id"]), row)
    for request in requests:
        resolution = resolutions.get(str(request.get("logical_request_id")))
        if request.get("admission") not in {"unknown", "pending", "acceptance_unknown"}:
            continue
        if resolution and resolution.get("identity_match") is False:
            request["db_identity_match"] = False
            continue
        if (resolution and resolution.get("job_id") and not resolution.get("missing")
                and resolution.get("identity_match") is True):
            request["job_id"] = resolution["job_id"]
            request["admission"] = "accepted"
            request["admission_source"] = "primary_db"
        elif resolution and resolution.get("admission") == "not_found":
            request["admission"] = "not_found"
            request["admission_source"] = "primary_db"

    ids = list(dict.fromkeys(str(i) for i in summary.get("job", {}).get("created_ids", []) if i))
    ids = list(dict.fromkeys(ids + [str(r["job_id"]) for r in requests if r.get("job_id")]))
    for records in (summary.get("job", {}).get("samples", []), summary.get("sse", {}).get("samples", [])):
        ids.extend(str(r["job_id"]) for r in records if r.get("job_id"))
    ids = list(dict.fromkeys(ids))
    accepted = set(ids)
    request_by_job = {}
    for request in requests:
        if not request.get("job_id"):
            continue
        job_id = str(request["job_id"])
        if job_id not in request_by_job:
            request_by_job[job_id] = dict(request)
            continue
        primary = request_by_job[job_id]
        # 幂等重放可能登记为另一个逻辑请求；不能覆盖同 Job 已取得的终态。
        for key, value in request.items():
            if value is not None and primary.get(key) is None:
                primary[key] = value
        primary["received_event_ids"] = sorted(set(primary.get("received_event_ids") or [])
                                                | set(request.get("received_event_ids") or []))
    existing = {str(r["job_id"]): r for r in summary.get("job", {}).get("samples", []) if r.get("job_id")}
    sse_samples = summary.get("sse", {}).get("samples", [])
    observed_ids: dict[str, set[int]] = {}
    for sample in sse_samples:
        if isinstance(sample.get("received_ids"), list):
            observed_ids.setdefault(str(sample.get("job_id")), set()).update(sample["received_ids"])
    checked = succeeded = realtime = integrity_checked = 0
    missing_events = 0
    completion = {120: {"completed": 0, "unknown": 0}, 300: {"completed": 0, "unknown": 0}}
    groups: dict[str, list[dict]] = {}
    samples = []
    for job_id in ids:
        request = request_by_job.get(job_id, {})
        row = db_rows.get(job_id, {})
        valid_db = (bool(row) and not row.get("missing") and row.get("session_owner_match", True) not in (False, 0)
                    and request.get("db_identity_match") is not False
                    and row.get("status") not in {"error", "not_found", "unknown"})
        checked += int(valid_db)
        succeeded += int(valid_db and row.get("status") == "succeeded")
        terminal_received = request.get("terminal_received_at") is not None
        realtime += int(terminal_received)
        group = request.get("group") or "ordinary"
        if valid_db:
            group = "ordinary"
            if int(row.get("resume_count") or 0) or request.get("resumed"):
                group = "resumed"
            elif row.get("status") == "waiting_input" or row.get("waiting_input") or request.get("waiting_input"):
                group = "waiting_input"
            elif int(row.get("attempt_count") or 0) > 1 or int(row.get("recovery_count") or 0):
                group = "retried"
        queue_ms = row.get("initial_queue_ms") if valid_db else None
        span_ms = row.get("execution_span_ms") if valid_db else None
        total_ms = row.get("server_total_ms") if valid_db else None
        # 旧 started_at 未声明首次语义时不推算首次排队；新采集器明确 first_started_at。
        if valid_db and queue_ms is None:
            queue_ms = _span(row.get("created_at"), row.get("first_started_at"))
        if valid_db and span_ms is None:
            span_ms = _span(row.get("first_started_at"), row.get("finished_at"))
        if valid_db and total_ms is None:
            total_ms = _span(row.get("created_at"), row.get("finished_at"))
        user_wait = request.get("user_wait_ms")
        if user_wait is None:
            user_wait = _span(request.get("created_request_at"), request.get("terminal_received_at"))
        sample = {**existing.get(job_id, {}), "job_id": job_id, "group": group,
                  "initial_queue_ms": queue_ms, "execution_span_ms": span_ms,
                  "server_total_ms": total_ms, "user_wait_ms": user_wait,
                  "wait_lower_bound_ms": request.get("wait_lower_bound_ms"),
                  "history_check_ms": request.get("history_check_ms"),
                  "terminal_received_at": request.get("terminal_received_at"),
                  "db_status": row.get("status") if valid_db else None,
                  "db_checked": valid_db,
                  "final_status": request.get("final_status") or existing.get(job_id, {}).get("final_status"),
                  "observation": "terminal" if terminal_received else "unobserved"}
        samples.append(sample)
        groups.setdefault(str(group), []).append(sample)
        for seconds, counts in completion.items():
            if valid_db and row.get("status") in TERMINAL and total_ms is not None:
                counts["completed"] += int(total_ms <= seconds * 1000)
            else:
                age_ms = _span(row.get("created_at"), db.get("collected_at")) if valid_db else None
                if age_ms is None or age_ms < seconds * 1000:
                    counts["unknown"] += 1
        persisted = row.get("persisted_event_ids") if valid_db else None
        observed = request.get("received_event_ids")
        if observed is None and job_id in observed_ids:
            observed = list(observed_ids[job_id])
        if isinstance(persisted, list) and isinstance(observed, list):
            integrity_checked += 1
            difference = set(persisted) - set(observed)
            missing_events += len(difference)
            sample["missing_event_ids"] = sorted(difference)
            sample["unexpected_event_ids"] = sorted(set(observed) - set(persisted))
        else:
            sample["missing_event_ids"] = None

    n = max(len(accepted), int(summary.get("job", {}).get("created") or 0))
    valid_requests = [r for r in requests if r.get("admission") != "not_sent"]
    admissions = Counter(str(r.get("admission") or "unknown") for r in valid_requests)
    capacity = {
        "schema_version": "loadtest.capacity.v2",
        "admission": {"denominator": len(valid_requests) if has_ledger else None, "accepted": n,
                      "offered": len(requests) if has_ledger else None,
                      "not_sent": len(requests) - len(valid_requests) if has_ledger else None,
                      "replays": summary.get("job", {}).get("idempotent_replays"),
                      "conflicts": admissions["conflict"] if has_ledger else None,
                      "unknown": sum(v for k, v in admissions.items() if k in {"pending", "unknown", "acceptance_unknown"}) if has_ledger else None,
                      "rate": _rate(n, len(valid_requests)) if has_ledger else None,
                      "request_receipt_rate": _rate(sum(bool(r.get("job_id")) for r in valid_requests), len(valid_requests)) if has_ledger else None,
                      "outcomes": dict(admissions)},
        "db_success": {"denominator": n, "checked": checked, "succeeded": succeeded,
                       "unidentified_accepted": n - len(accepted),
                       "rate": _rate(succeeded, n) if checked else None, "coverage": _rate(checked, n)},
        "client_terminal": {"denominator": n, "realtime": realtime if has_ledger else None,
                            "after_db": sum(s["db_status"] in TERMINAL and s["terminal_received_at"] is None for s in samples) if has_ledger else None,
                            "coverage": _rate(realtime, n) if has_ledger else None},
        "sse_integrity": {"denominator": n, "checked": integrity_checked,
                          "coverage": _rate(integrity_checked, n),
                          "missing_events": missing_events if integrity_checked else None},
    }
    for seconds, counts in completion.items():
        counts["unknown"] += n - len(accepted)
        capacity[f"completion_{seconds}s"] = {"denominator": n, **counts,
                                               "rate": _rate(counts["completed"], n) if checked else None}
    capacity["latency_groups"] = {}
    for name, members in groups.items():
        bucket = {"accepted": len(members), "sample_count": sum(m["db_status"] in TERMINAL for m in members)}
        for field in ("initial_queue_ms", "execution_span_ms", "server_total_ms", "user_wait_ms"):
            values = [m[field] for m in members if m[field] is not None
                      and (m["terminal_received_at"] is not None if field == "user_wait_ms" else m["db_status"] in TERMINAL)]
            bucket[f"completed_sample_p95_{field}"] = percentile(values, .95)
            bucket[f"{field}_samples"] = len(values)
        capacity["latency_groups"][name] = bucket
    capacity["cdfm"] = _cdfm(summary, accepted, db_rows)
    capacity["cdfm"]["job_denominator"] = n
    if n != len(accepted):
        capacity["cdfm"]["job_coverage"] = _rate(
            capacity["cdfm"]["covered_jobs"], n)
        capacity["cdfm"]["status"] = "partial" if capacity["cdfm"]["covered_jobs"] else "missing"
        capacity["cdfm"]["final_success_rate"] = None
    phase_stats = _phases(summary, samples, db_rows)
    capacity["phases"] = phase_stats
    _drain_evidence(summary, db if db_available else {}, samples, n)
    summary["phases"] = phase_stats
    summary["capacity"] = capacity
    if has_ledger:
        summary["requests"] = requests
        job = summary.setdefault("job", {})
        job.update({"created": n, "created_ids": ids, "samples": samples, "recorded": len(samples),
                    "completed": realtime, "observation_timeouts": n - realtime,
                    "terminal_status_counts": dict(Counter(s.get("final_status") or "unfinished" for s in samples))})
        for field in ("queue_p50_seconds", "queue_p95_seconds", "execution_p50_seconds",
                      "execution_p95_seconds", "total_p50_seconds", "total_p95_seconds"):
            job[field] = None
        summary.setdefault("sse", {})["verified_missing_events"] = capacity["sse_integrity"]["missing_events"]
    if db_available and db and scoped:
        summary["db_checked_at"] = epoch(db.get("collected_at"))
    return summary


def _drain_evidence(summary: dict, db: dict, samples: list[dict], denominator: int) -> None:
    """客户端空列表不证明资源释放；只读主库时间戳负责服务器排空结论。"""
    stop = epoch(summary.get("stop_submitting_at"))
    if stop is None:
        return
    drain = summary.setdefault("drain", {})
    checkpoint_record = drain.get("checkpoint") or {}
    checkpoint_record["drained"] = None
    drain["checkpoint"] = checkpoint_record
    rows = {str(row.get("job_id")): row for row in db.get("jobs", [])}
    unresolved = any(request.get("admission") in {"pending", "unknown", "acceptance_unknown"}
                     for request in summary.get("requests", []))
    complete = (not unresolved and denominator > 0 and len(samples) == denominator
                and all(sample.get("db_checked") for sample in samples))
    release_known = complete and all("execution_state" in rows[s["job_id"]]
                                     and rows[s["job_id"]]["execution_state"] in (None, "leased", "draining")
                                     for s in samples)
    released = release_known and all(rows[s["job_id"]]["execution_state"] is None for s in samples)
    terminal = complete and all(s.get("db_status") in TERMINAL for s in samples)
    drain["execution_released"] = bool(released) if release_known else None
    drain["server_drained_at_check"] = bool(released and terminal) if release_known else None
    drain["finally_drained"] = None
    drain["drain_ms"] = None
    if released and terminal:
        ends = []
        for sample in samples:
            row = rows[sample["job_id"]]
            finished = epoch(row.get("finished_at"))
            released_at = epoch(row.get("execution_released_at"))
            # 从未领取的终态任务不需要执行释放时间；已执行任务缺失该时间时不猜测。
            if finished is None or (row.get("started_at") and released_at is None):
                break
            ends.append(max(finished, released_at or finished))
        if len(ends) == denominator:
            elapsed = max(0, max(ends) - stop) * 1000
            drain["drain_ms"] = elapsed
            limit = drain.get("observation_limit_ms")
            drain["finally_drained"] = elapsed <= limit if isinstance(limit, (int, float)) else None
            checkpoint = drain.setdefault("checkpoint", {})
            checkpoint["drained"] = elapsed <= drain.get("checkpoint_ms", 300000)
            checkpoint["source"] = "primary_db_release_timestamps"
    elif complete:
        # 每个 Job 的事务快照不同。整份采集结束时间不能证明早先读取的 Job 超期。
        for sample in samples:
            row = rows[sample["job_id"]]
            read_started = epoch(row.get("db_read_started_at"))
            still_active = (sample.get("db_status") not in TERMINAL
                            or row.get("execution_state") in {"leased", "draining"})
            if not still_active or read_started is None:
                continue
            elapsed = (read_started - stop) * 1000
            limit = drain.get("observation_limit_ms")
            if isinstance(limit, (int, float)) and elapsed >= limit:
                drain["finally_drained"] = False
            if elapsed >= drain.get("checkpoint_ms", 300000):
                drain.setdefault("checkpoint", {})["drained"] = False
    summary["drain"] = drain


def _phases(summary: dict, samples: list[dict], db_rows: dict) -> dict:
    start, stop, end = (epoch(summary.get(key)) for key in
                        ("started_at", "stop_submitting_at", "observation_ended_at"))
    if start is None or stop is None or end is None:
        return {"load": None, "drain": None}
    result = {}
    has_server_evidence = any(sample.get("db_checked") for sample in samples)
    for name, a, b in (("load", start, stop), ("drain", stop, end)):
        seconds = max(0, b - a)
        def in_window(at):
            value = epoch(at)
            return value is not None and a <= value <= b and (name == "load" or value > a)
        http = sum(in_window(r.get("started_at")) for r in summary.get("http", {}).get("samples", []))
        client = sum(in_window(s.get("terminal_received_at")) for s in samples)
        server = sum(in_window(db_rows.get(s["job_id"], {}).get("finished_at")) and s["db_status"] in TERMINAL for s in samples)
        queues = [s["initial_queue_ms"] for s in samples if s["initial_queue_ms"] is not None
                  and s["db_status"] in TERMINAL
                  and in_window(db_rows.get(s["job_id"], {}).get("first_started_at"))]
        result[name] = {"started_at": a, "finished_at": b, "duration_ms": seconds * 1000,
                        "http_requests": http, "http_rps": http / seconds if seconds else None,
                        "client_completions": client, "client_completion_throughput": client / seconds if seconds else None,
                        "server_completions": server if has_server_evidence else None,
                        "server_completion_throughput": server / seconds if seconds and has_server_evidence else None,
                        "completed_sample_queue_p95_ms": percentile(queues, .95),
                        "queue_samples": len(queues), "queue_phase_basis": "first_started_at"}
    return result


def _cdfm(summary: dict, accepted: set[str], db_rows: dict) -> dict:
    """只消费真实、带 Job 归属的逻辑调用记录；不能由场景名称推出调用发生。"""
    calls = []
    for row in db_rows.values():
        if str(row.get("job_id")) in accepted:
            calls.extend({**call, "job_id": row["job_id"]} for call in row.get("cdfm_calls", []))
    calls.extend(summary.get("cdfm_calls") or [])
    log_evidence = summary.get("cdfm_log_evidence") or {}
    valid_logs = (log_evidence.get("run_id") == summary.get("run_id")
                  and log_evidence.get("scope") == "run")
    log_complete = valid_logs and log_evidence.get("status") == "collected"
    if valid_logs:
        log_calls = log_evidence.get("records") or []
        logged_jobs = {str(call.get("job_id")) for call in log_calls}
        # 对已取得日志身份的 Job 使用 MCP 调用证据，不混入缺少内部身份的公开事件。
        calls = [c for c in calls if str(c.get("job_id")) not in logged_jobs]
        calls.extend(log_calls)
    else:
        logged_jobs = set()
    by_identity: dict[tuple, list[dict]] = {}
    unidentified = 0
    for call in calls:
        job_id, invocation = str(call.get("job_id") or ""), call.get("invocation_id")
        if job_id in accepted and invocation:
            by_identity.setdefault((job_id, str(invocation)), []).append(call)
        elif job_id in accepted:
            unidentified += 1
    attempts = retries = success = terminal = 0
    durations = []
    jobs_with_calls = set()
    missing_attempt_identity = False
    for (job_id, _), entries in by_identity.items():
        jobs_with_calls.add(job_id)
        unique_attempts = {(e.get("attempt"), e.get("retry", e.get("retry_ordinal"))) for e in entries
                           if e.get("attempt") is not None or e.get("retry", e.get("retry_ordinal")) is not None}
        attempts += len(unique_attempts)
        retries += max(0, len(unique_attempts) - 1)
        missing_attempt_identity |= not unique_attempts or any(
            e.get("attempt") is None and e.get("retry", e.get("retry_ordinal")) is None for e in entries)
        def attempt_key(entry):
            return (int(entry.get("attempt") or 0),
                    int(entry.get("retry", entry.get("retry_ordinal")) or 0))
        latest_attempt = max(attempt_key(entry) for entry in entries)
        finals = [e for e in entries if attempt_key(e) == latest_attempt
                  and e.get("status") in {"succeeded", "valid", "failed", "timed_out", "canceled"}]
        finals.sort(key=lambda e: int(e.get("log_timestamp_ns") or e.get("event_id") or 0))
        if finals:
            terminal += 1
            success += int(finals[-1].get("status") in {"succeeded", "valid"})
        duration_by_attempt = {}
        for e in sorted(entries, key=lambda e: int(e.get("log_timestamp_ns") or e.get("event_id") or 0)):
            retry = e.get("retry", e.get("retry_ordinal"))
            if isinstance(e.get("duration_ms"), (float, int)) and (e.get("attempt") is not None or retry is not None):
                duration_by_attempt[(e.get("attempt"), retry)] = float(e["duration_ms"])
        durations.extend(duration_by_attempt.values())
    db_covered = {str(row.get("job_id")) for row in db_rows.values() if str(row.get("job_id")) in accepted
                  and isinstance(row.get("cdfm_calls"), list) and (row.get("cdfm") or {}).get("status") != "missing"}
    log_covered = {str(job_id) for job_id, status in (log_evidence.get("coverage") or {}).items()
                   if valid_logs and status.get("status") == "collected"} & accepted
    covered_jobs = ((accepted if log_complete else log_covered)
                    | (db_covered - logged_jobs))
    covered = len(covered_jobs)
    complete = covered == len(accepted) and covered > 0 and not unidentified and not missing_attempt_identity
    return {"status": "collected" if complete else ("partial" if covered else "missing"),
            "source": "mcp_logs" if valid_logs else "primary_db_events",
            "job_coverage": _rate(covered, len(accepted)), "covered_jobs": covered, "jobs_with_calls": len(jobs_with_calls) if covered else None,
            "jobs_without_calls": len(covered_jobs - jobs_with_calls) if covered else None,
            "unidentified_records": unidentified if covered else None,
            "logical_calls": len(by_identity) if complete else None, "terminal_calls": terminal if by_identity else None,
            "known_logical_calls": len(by_identity), "known_actual_attempts": attempts,
            "succeeded": success if by_identity else None,
            "final_success_rate": _rate(success, len(by_identity)) if complete and terminal == len(by_identity) else None,
            "actual_attempts": attempts if complete else None,
            "retries": retries if complete else None,
            "attempt_duration_p95_ms": percentile(durations, .95)}
