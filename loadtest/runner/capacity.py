"""本轮身份、观察策略与主库证据的纯统计；不访问服务，不改写原始记录。"""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone

from runner.metrics import percentile

TERMINAL = {"succeeded", "failed", "canceled"}


def epoch(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
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
    return (b - a) * 1000 if a is not None and b is not None and b >= a else None


def execution_released(row: dict) -> bool | None:
    """从原始列重新判定，兼容旧采集器曾把空 execution_state 误判为释放。"""
    state = row.get("execution_state")
    if state in ("leased", "draining"):
        return False
    if "execution_state" not in row or state is not None:
        return None
    if (row.get("attempt_count") == 0 and "started_at" in row and row["started_at"] is None
            and row.get("status") in TERMINAL):
        return True
    if (epoch(row.get("execution_released_at")) is not None
            and row.get("execution_release_reason") == "worker_confirmed"):
        return True
    return None


def _rate(count, total) -> float | None:
    return count / total if count is not None and total else None


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _records(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _policy(rows: list[dict], field: str) -> bool | None:
    # 同一个 Job 的独立观察只要有一条预声明持续观察，就仍属于持续观察分母。
    values = [row.get(field) for row in rows]
    if any(value is True for value in values):
        return True
    return False if values and all(value is False for value in values) else None


def _deadline(samples: list[dict], denominator: int | None, seconds: int, *, client: bool = False) -> dict:
    terminal = succeeded = terminal_known = success_known = 0
    for sample in samples:
        status = sample.get("final_status" if client else "db_status")
        duration = sample.get("user_wait_ms" if client else "server_total_ms")
        age = sample.get("client_observed_ms" if client else "db_observed_ms")
        is_terminal = sample.get("terminal_received_at") is not None if client else status in TERMINAL
        if is_terminal and duration is not None:
            within = duration <= seconds * 1000
            terminal += int(within)
            terminal_known += 1
            if status in TERMINAL or not within:
                succeeded += int(within and status == "succeeded")
                success_known += 1
        elif not is_terminal and age is not None and age >= seconds * 1000:
            # 只有该 Job 自己的观察时刻可以证明它已超过期限。
            terminal_known += 1
            success_known += 1
    return {"denominator": denominator, "completed": terminal if terminal_known else None,
            "terminal": terminal if terminal_known else None,
            "succeeded": succeeded if success_known else None,
            "unknown": denominator - terminal_known if denominator is not None else None,
            "success_unknown": denominator - success_known if denominator is not None else None,
            "rate": _rate(terminal if terminal_known else None, denominator),
            "terminal_rate": _rate(terminal if terminal_known else None, denominator),
            "success_rate": _rate(succeeded if success_known else None, denominator),
            "basis": "created_request_at_to_client_terminal" if client else "db_created_at_to_finished_at"}


def _integrity(samples: list[dict], denominator: int | None, *, truncated: bool = False) -> dict:
    comparable = [sample for sample in samples if sample.get("event_set_compared")]
    checked = [sample for sample in comparable if truncated or sample.get("stream_complete")]
    missing = sum(len(sample["unobserved_event_ids"]) for sample in checked)
    return {"denominator": denominator, "checked": len(checked),
            "coverage": _rate(len(checked), denominator),
            "missing_events": missing if checked and not truncated else None,
            "unobserved_events": missing if checked and truncated else None,
            "unexpected_events": sum(len(sample["unexpected_event_ids"]) for sample in checked) if checked else None}


def _business_groups(requests: list[dict], row: dict) -> list[str]:
    groups = {str(request["group"]) for request in requests if isinstance(request.get("group"), str) and request["group"] != "ordinary"}
    if row.get("resume_count", 0) or any(request.get("resumed") for request in requests):
        groups.add("resumed")
    if row.get("status") == "waiting_input" or row.get("waiting_input") is True or any(request.get("waiting_input") for request in requests):
        groups.add("waiting_input")
    if (_number(row.get("attempt_count")) or 0) > 1 or row.get("recovery_count", 0):
        groups.add("retried")
    if any(request.get("cancel_attempts") or "cancel" in str(request.get("group") or "")
           or "cancel" in str(request.get("observation_reason") or "") for request in requests):
        groups.add("cancellation")
    if not groups:
        groups.add("ordinary" if any(request.get("group") == "ordinary" for request in requests) else "unknown")
    return sorted(groups)


def _latency_groups(samples: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for sample in samples:
        for group in sample["business_groups"]:
            groups.setdefault(group, []).append(sample)
    result = {}
    for name, members in groups.items():
        bucket = {"accepted": len(members), "sample_count": sum(member["db_status"] in TERMINAL for member in members) if any(member["db_checked"] for member in members) else None,
                  "client_terminal": sum(member["terminal_received_at"] is not None for member in members) if any(member["client_observation_known"] for member in members) else None,
                  "db_status_counts": dict(Counter(member["db_status"] or "unknown" for member in members)),
                  "observation_groups": dict(Counter(member["observation_group"] for member in members))}
        for field in ("initial_queue_ms", "execution_span_ms", "server_total_ms", "user_wait_ms"):
            values = [member[field] for member in members if member[field] is not None
                      and (member["terminal_received_at"] is not None if field == "user_wait_ms" else member["db_status"] in TERMINAL)]
            bucket[f"completed_sample_p95_{field}"] = percentile(values, .95)
            bucket[f"{field}_samples"] = len(values)
        result[name] = bucket
    return result


def _business_results(samples: list[dict], denominator: int | None) -> dict:
    """互斥业务分类独立于恢复/等待/重试等可重叠的延迟分组。"""
    result = {}
    for name in ("total", "ordinary", "cancellation", "unknown"):
        members = samples if name == "total" else [row for row in samples if row["business_class"] == name]
        total = denominator if name == "total" else len(members)
        if name == "unknown" and denominator is not None:
            total += denominator - len(samples)
        checked = sum(row["db_checked"] for row in members)
        counts = Counter(row["db_status"] for row in members if row["db_checked"])
        counts = {status: counts[status] if checked or total == 0 else None
                  for status in ("succeeded", "failed", "canceled", "queued", "running", "waiting_input")}
        terminal = sum(counts[status] or 0 for status in TERMINAL) if checked or total == 0 else None
        result[name] = {"denominator": total, "checked": checked, "status_counts": counts,
                        "unknown": total - checked if total is not None else None, "terminal": terminal,
                        "terminal_rate": _rate(terminal, total), "success_rate": _rate(counts["succeeded"], total),
                        "coverage": _rate(checked, total)}
    return result


def _submission(requests: list[dict], has_ledger: bool) -> dict:
    """按逻辑请求报告提交意图及首次发送前等待，不将未发送下界混入耗时分位。"""
    def bucket(rows):
        samples = []
        for row in rows:
            intended = epoch(row.get("scheduled_at"))
            if intended is None:
                intended = epoch(row.get("intent_created_at"))
            sent = epoch(row.get("created_request_at"))
            not_sent = row.get("admission") == "not_sent"
            wait = _number(row.get("pre_submit_wait_ms"))
            if wait is None and not not_sent:
                wait = _span(intended, sent)
            samples.append({"intent": intended, "sent": sent, "not_sent": not_sent,
                            "pre_submit_wait_ms": wait if not not_sent and sent is not None else None,
                            "scheduler_lag_ms": _number(row.get("scheduler_lag_ms")),
                            "wait_lower_bound_ms": _span(intended, row.get("observation_finished_at")) if sent is None else None})
        sent = sum(row["sent"] is not None and not row["not_sent"] for row in samples)
        not_sent = sum(row["not_sent"] for row in samples)
        result = {"intents": len(rows), "intent_timestamp_known": sum(row["intent"] is not None for row in samples),
                  "actual_sent": sent, "not_sent": not_sent, "send_unknown": len(rows) - sent - not_sent,
                  "not_sent_with_send_timestamp": sum(row["not_sent"] and row["sent"] is not None for row in samples),
                  "not_sent_reasons": dict(Counter(str(row.get("not_sent_reason") or row.get("reason") or "unknown") for row in rows if row.get("admission") == "not_sent"))}
        for field in ("scheduler_lag_ms", "pre_submit_wait_ms", "wait_lower_bound_ms"):
            values = [row[field] for row in samples if row[field] is not None]
            result[f"{field}_samples"] = len(values)
            result[f"{field}_p50"] = percentile(values, .5)
            result[f"{field}_p95"] = percentile(values, .95)
        return result
    groups = {}
    for row in requests:
        groups.setdefault(str(row.get("group") or "unknown"), []).append(row)
    return {"summary": bucket(requests) if has_ledger else None,
            "groups": {name: bucket(rows) for name, rows in groups.items()}}


def enrich_summary(source: dict, logcheck: dict | None = None) -> dict:
    """仅在副本内计算派生统计；requests/job/http/sse 原始内容保持不变。"""
    summary = deepcopy(source)
    has_ledger = isinstance(summary.get("requests"), list)
    requests = deepcopy(_records(summary.get("requests")))
    job = summary.get("job") or {}
    ids = {str(value) for value in job.get("created_ids", []) if value}
    for records in (requests, _records(job.get("samples")), _records((summary.get("sse") or {}).get("samples"))):
        ids.update(str(row["job_id"]) for row in records if row.get("job_id"))
    db = summary.get("job_db_check") or {}
    legacy_scoped = (db.get("schema_version") == "loadtest-job-db-check-v1" and db.get("scope") is None
                     and isinstance(db.get("jobs"), list) and all(str(row.get("job_id")) in ids for row in _records(db["jobs"])))
    scoped = (bool(db.get("run_id")) and db.get("run_id") == summary.get("run_id")
              and (db.get("scope") == "run" or legacy_scoped))
    available = scoped and db.get("status") not in {"missing", "error", "unknown"}
    db_rows = {str(row["job_id"]): row for row in _records(db.get("jobs")) if row.get("job_id")} if available else {}
    resolutions = {str(row["logical_request_id"]): row for row in _records(db.get("requests")) if row.get("logical_request_id")} if available else {}
    for row in db_rows.values():
        if row.get("logical_request_id"):
            resolutions.setdefault(str(row["logical_request_id"]), row)
    for request in requests:
        resolution = resolutions.get(str(request.get("logical_request_id")))
        if request.get("admission") not in {"unknown", "pending", "acceptance_unknown"} or not resolution:
            continue
        if resolution.get("identity_match") is False:
            request["db_identity_match"] = False
        elif resolution.get("job_id") and not resolution.get("missing") and resolution.get("identity_match") is True:
            request.update(job_id=resolution["job_id"], admission="accepted", admission_source="primary_db")
            ids.add(str(resolution["job_id"]))
        elif resolution.get("admission") == "not_found":
            request.update(admission="not_found", admission_source="primary_db")
    raw_count = _number(job.get("created"))
    n = max(len(ids), int(raw_count or 0)) if ids or raw_count is not None or has_ledger else None
    by_job: dict[str, list[dict]] = {}
    for request in requests:
        if request.get("job_id"):
            by_job.setdefault(str(request["job_id"]), []).append(request)
    existing = {str(row["job_id"]): row for row in _records(job.get("samples")) if row.get("job_id")}
    subscriptions: dict[str, list[dict]] = {}
    for row in _records((summary.get("sse") or {}).get("samples")):
        if row.get("job_id"):
            subscriptions.setdefault(str(row["job_id"]), []).append(row)
    samples = []
    valid_rows = {}
    end = epoch(summary.get("observation_ended_at"))
    for job_id in sorted(ids):
        observations = by_job.get(job_id, [])
        request = observations[0] if observations else {}
        row = db_rows.get(job_id, {})
        valid_db = (bool(row) and not row.get("missing") and row.get("session_owner_match", True) not in (False, 0)
                    and row.get("identity_match") is not False and all(r.get("db_identity_match") is not False for r in observations)
                    and row.get("status") in TERMINAL | {"queued", "running", "waiting_input"})
        if valid_db:
            valid_rows[job_id] = row
        else:
            row = {}
        terminal_times = [epoch(r.get("terminal_received_at")) for r in observations]
        terminal_times = [at for at in terminal_times if at is not None and (end is None or at <= end)]
        terminal_at = min(terminal_times) if terminal_times else None
        terminal_request = next((r for r in observations if epoch(r.get("terminal_received_at")) == terminal_at), request) if terminal_at is not None else request
        starts = [epoch(r.get("created_request_at")) for r in observations]
        starts = [at for at in starts if at is not None]
        created = min(starts) if starts else None
        requires_terminal = _policy(observations, "requires_terminal")
        requires_full_stream = _policy(observations, "requires_full_stream")
        group = "continuous" if requires_terminal is True else "truncated" if requires_terminal is False else "unknown"
        user_wait = _span(created, terminal_at)
        if user_wait is None and terminal_at is not None:
            user_wait = _number(terminal_request.get("user_wait_ms"))
        observed_ends = [epoch(r.get("observation_finished_at")) for r in observations]
        # 未保存逐 Job 结束时刻的旧记录不能假设一直观察到整轮结束。
        observed_ends = [at for at in observed_ends if at is not None]
        observed_end = max(observed_ends) if observed_ends else None
        event_sets = [r["received_event_ids"] for r in observations if isinstance(r.get("received_event_ids"), list)]
        if not event_sets:
            event_sets = [r["received_ids"] for r in subscriptions.get(job_id, []) if isinstance(r.get("received_ids"), list)]
        observed_ids = set().union(*(set(values) for values in event_sets)) if event_sets else None
        persisted = row.get("persisted_event_ids")
        event_compared = isinstance(persisted, list) and observed_ids is not None
        business = _business_groups(observations, row)
        declared_classes = {r.get("business_class") for r in observations if r.get("business_class") in {"ordinary", "cancellation"}}
        business_class = (next(iter(declared_classes)) if len(declared_classes) == 1 else
                          "cancellation" if "cancellation" in business else
                          "ordinary" if observations and all(r.get("group") and r.get("group") not in {"unknown", "recovery"} for r in observations) else "unknown")
        sse_terminal = any(r.get("observation_end_reason") == "terminal" for r in observations) or any(
            r.get("terminal_event") in {"final_result", "error", "canceled", "succeeded", "failed"} for r in subscriptions.get(job_id, []))
        sample = {"job_id": job_id, "group": business[0], "business_groups": business,
                  "business_class": business_class, "client_observation_known": bool(observations),
                  "db_checked": valid_db, "db_status": row.get("status"),
                  "execution_released": execution_released(row),
                  "final_status": terminal_request.get("final_status") or existing.get(job_id, {}).get("final_status"),
                  "created_request_at": created, "terminal_received_at": terminal_at,
                  "requires_terminal": requires_terminal, "requires_full_stream": requires_full_stream,
                  "observation_group": group, "observation_reasons": sorted({str(r["observation_reason"]) for r in observations if r.get("observation_reason")}),
                  "observation_end_reasons": sorted({str(r["observation_end_reason"]) for r in observations if r.get("observation_end_reason")}),
                  "initial_queue_ms": _number(row.get("initial_queue_ms")),
                  "execution_span_ms": _number(row.get("execution_span_ms")),
                  "server_total_ms": _number(row.get("server_total_ms")), "user_wait_ms": user_wait,
                  "client_observed_ms": _span(created, observed_end),
                  "db_observed_ms": _span(row.get("created_at"), row.get("db_read_started_at")),
                  "wait_lower_bound_ms": _number(request.get("wait_lower_bound_ms")),
                  "history_check_ms": _number(request.get("history_check_ms")),
                  "event_set_compared": event_compared,
                  "stream_complete": requires_full_stream is True and sse_terminal and terminal_at is not None and row.get("status") in TERMINAL,
                  "unobserved_event_ids": sorted(set(persisted) - observed_ids) if event_compared else None,
                  "unexpected_event_ids": sorted(observed_ids - set(persisted)) if event_compared else None}
        for field, a, b in (("initial_queue_ms", "created_at", "first_started_at"),
                            ("execution_span_ms", "first_started_at", "finished_at"),
                            ("server_total_ms", "created_at", "finished_at")):
            if sample[field] is None:
                sample[field] = _span(row.get(a), row.get(b))
        sample["missing_event_ids"] = sample["unobserved_event_ids"] if sample["stream_complete"] else None
        samples.append(sample)
    checked = len(valid_rows)
    succeeded = sum(sample["db_status"] == "succeeded" for sample in samples)
    realtime = sum(sample["terminal_received_at"] is not None for sample in samples) if has_ledger else None
    valid_requests = [row for row in requests if row.get("admission") != "not_sent"]
    admissions = Counter(str(row.get("admission") or "unknown") for row in valid_requests)
    continuous = [sample for sample in samples if sample["requires_terminal"] is True]
    truncated = [sample for sample in samples if sample["requires_terminal"] is False]
    full = [sample for sample in samples if sample["requires_full_stream"] is True]
    partial = [sample for sample in samples if sample["requires_full_stream"] is False]
    policy_known = sum(sample["requires_terminal"] is not None for sample in samples)
    full_policy_known = sum(sample["requires_full_stream"] is not None for sample in samples)
    capacity = {"schema_version": "loadtest.capacity.v2", "samples": samples,
        "admission": {"denominator": len(valid_requests) if has_ledger else None, "accepted": n,
                      "offered": len(requests) if has_ledger else None,
                      "not_sent": len(requests) - len(valid_requests) if has_ledger else None,
                      "replays": job.get("idempotent_replays"),
                      "conflicts": admissions["conflict"] if has_ledger else None,
                      "unknown": sum(admissions[key] for key in ("pending", "unknown", "acceptance_unknown")) if has_ledger else None,
                      "rate": _rate(n, len(valid_requests)) if has_ledger else None,
                      "request_receipt_rate": _rate(sum(bool(row.get("job_id")) for row in valid_requests), len(valid_requests)) if has_ledger else None,
                      "outcomes": dict(admissions)},
        "db_success": {"denominator": n, "checked": checked, "succeeded": succeeded if checked else None,
                       "unidentified_accepted": n - len(ids) if n is not None else None,
                       "rate": _rate(succeeded, n) if checked else None, "coverage": _rate(checked, n)},
        "client_terminal": {"denominator": n, "realtime": realtime,
                            "after_db": sum(sample["db_status"] in TERMINAL and sample["terminal_received_at"] is None for sample in samples) if checked and has_ledger else None,
                            "coverage": _rate(realtime, n)},
        "observation": {"all_accepted": n, "continuous_denominator": len(continuous) if policy_known else None,
                        "truncated_denominator": len(truncated) if policy_known else None,
                        "unknown_policy": n - policy_known if n is not None else None,
                        "full_stream_denominator": len(full) if full_policy_known else None,
                        "partial_stream_denominator": len(partial) if full_policy_known else None,
                        "unknown_stream_policy": n - full_policy_known if n is not None else None,
                        "end_reasons": dict(Counter(reason for sample in samples for reason in sample["observation_end_reasons"]))},
        "continuous_client_terminal": {"denominator": len(continuous) if policy_known else None,
                                       "realtime": sum(sample["terminal_received_at"] is not None for sample in continuous) if policy_known else None,
                                       "coverage": _rate(sum(sample["terminal_received_at"] is not None for sample in continuous), len(continuous))},
        "sse_integrity": _integrity(full, len(full) if full_policy_known else None),
        "truncated_sse": _integrity(partial, len(partial) if full_policy_known else None, truncated=True)}
    for seconds in (120, 300):
        capacity[f"completion_{seconds}s"] = _deadline(samples, n, seconds)
        capacity[f"continuous_completion_{seconds}s"] = _deadline(continuous, len(continuous) if policy_known else None, seconds, client=True)
    capacity["latency_groups"] = _latency_groups(samples)
    capacity["business_results"] = _business_results(samples, n)
    capacity["submission"] = _submission(requests, has_ledger)
    capacity["multi_session"] = _multi_session(summary, ids, db_rows, by_job)
    cancel_requests = [request for request in requests if request.get("cancel_attempts")]
    cancel_jobs = [sample for sample in samples if "cancellation" in sample["business_groups"]]
    attempts = [attempt for request in cancel_requests for attempt in _records(request.get("cancel_attempts"))]
    released = [sample for sample in cancel_jobs if execution_released(valid_rows.get(sample["job_id"], {})) is not None]
    capacity["cancellation"] = {"denominator": len(cancel_jobs) if has_ledger else None,
        "api_attempts": len(attempts) if cancel_requests else None,
        "api_outcomes": dict(Counter(str(attempt.get("http_status") if attempt.get("http_status") is not None else attempt.get("status", attempt.get("status_code", "unknown"))) for attempt in attempts)),
        "api_business_statuses": dict(Counter(str(attempt.get("api_status") or "unknown") for attempt in attempts)),
        "business_canceled": sum(sample["db_status"] == "canceled" for sample in cancel_jobs) if any(sample["db_checked"] for sample in cancel_jobs) else None,
        "db_checked": sum(sample["db_checked"] for sample in cancel_jobs),
        "execution_release_checked": len(released),
        "execution_released": sum(execution_released(valid_rows[sample["job_id"]]) is True for sample in released) if released else None,
        "process_termination": "unknown", "browser_evidence": "unknown"}
    capacity["cdfm"] = _cdfm(summary, ids, valid_rows, logcheck)
    capacity["cdfm"]["job_denominator"] = n
    capacity["cdfm"]["collection_job_coverage"] = _rate(capacity["cdfm"]["collection_covered_jobs"], n)
    capacity["cdfm"]["job_coverage"] = _rate(capacity["cdfm"]["covered_jobs"], n)
    if n != len(ids):
        capacity["cdfm"].update(status="partial" if capacity["cdfm"]["covered_jobs"] else "missing",
                                  final_success_rate=None, logical_calls=None, actual_attempts=None, retries=None)
    capacity["db_checked_at"] = epoch(db.get("collected_at")) if available else None
    _drain_evidence(summary, db if available else {}, samples, n or 0, requests)
    phase_stats = _phases(summary, samples, valid_rows)
    capacity["phases"] = phase_stats
    capacity["evidence_boundary"] = {
        "schema_version": "loadtest.evidence-boundary.v1",
        "report_structure": "requires_verify_report",
        "admission": {"denominator": n, "identified_jobs": len(ids),
                      "unknown_requests": capacity["admission"]["unknown"]},
        "client_terminal": {"source": "requests.terminal_received_at", "db_backfill_allowed": False,
                            "coverage": capacity["client_terminal"]["coverage"]},
        "server_terminal": {"source": "same_run_primary_db", "coverage": capacity["db_success"]["coverage"]},
        "cleanup": {"business_active_cleared": summary.get("drain", {}).get("business_active_cleared"),
                    "execution_released": summary.get("drain", {}).get("execution_released"),
                    "release_known_jobs": sum(sample["execution_released"] is not None for sample in samples),
                    "release_unknown_jobs": sum(sample["execution_released"] is None for sample in samples),
                    "source": "strict_primary_db_release_evidence"},
        "sse_full_stream": {"denominator": capacity["sse_integrity"].get("denominator"),
                            "checked": capacity["sse_integrity"].get("checked")},
        "cdfm": {"identity_coverage": capacity["cdfm"]["job_coverage"],
                 "call_success_rate": capacity["cdfm"]["final_success_rate"], "algorithm_accuracy": None},
        "multi_session": {"status": capacity["multi_session"]["status"],
                          "core_status": capacity["multi_session"].get("core_status")},
        "performance_acceptance": "unknown_without_declared_thresholds",
        "process_termination": "unknown", "browser_evidence": "unknown"}
    summary["phases"] = phase_stats
    summary["capacity"] = capacity
    return summary


def _drain_evidence(summary: dict, db: dict, samples: list[dict], denominator: int, requests: list[dict]) -> None:
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
                     for request in requests)
    complete = (not unresolved and denominator > 0 and len(samples) == denominator
                and all(sample.get("db_checked") for sample in samples))
    releases = [execution_released(rows[s["job_id"]]) for s in samples] if complete else []
    release_known = complete and (False in releases or all(value is not None for value in releases))
    released = release_known and all(value is True for value in releases)
    terminal = complete and all(s.get("db_status") in TERMINAL for s in samples)
    drain["business_active_cleared"] = bool(terminal) if complete else None
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


def _multi_session(summary: dict, accepted: set[str], db_rows: dict, by_job: dict) -> dict:
    """多会话接纳、活动重叠与单次执行区间重叠分别核查。"""
    evidence = _records(summary.get("multi_session_evidence"))
    results = []
    for item in evidence:
        jobs = [str(value) for value in item.get("job_ids", [])]
        sessions = [str(value) for value in item.get("session_ids", [])]
        identity_valid = len(jobs) == len(sessions) == 2 and len(set(jobs)) == len(set(sessions)) == 2
        reasons = []
        ownership = None
        mapping = dict(zip(jobs, sessions)) if identity_valid else {}
        if not identity_valid:
            reasons.append("two_distinct_job_and_session_identities_required")
        elif not set(jobs) <= accepted:
            ownership = False
            reasons.append("job_outside_this_run")
        else:
            declarations = [row for job_id in jobs for row in by_job.get(job_id, [])]
            matches = bool(item.get("username")) and all(by_job.get(job_id) for job_id in jobs)
            matches = matches and all(row.get("username") == item["username"] and str(row.get("session_id")) == mapping[str(row["job_id"])] for row in declarations)
            if not matches or item.get("ownership_verified") is False:
                ownership = False if declarations or item.get("ownership_verified") is False else None
                reasons.append("request_account_or_session_mapping_not_verified")
            elif item.get("ownership_verified") is True:
                ownership = True
            rows = [db_rows[job_id] for job_id in jobs if job_id in db_rows]
            if any(row.get("session_owner_match") in (False, 0) or row.get("session_id") is not None and str(row.get("session_id")) != mapping[str(row["job_id"])] for row in rows):
                ownership = False
                reasons.append("primary_db_ownership_mismatch")
            users = {str(row["user_id"]) for row in rows if row.get("user_id") is not None}
            if len(users) > 1:
                ownership = False
                reasons.append("primary_db_account_mismatch")
        activity = running = None
        overlap_at = None
        for snapshot in _records(item.get("active_snapshots")):
            at = epoch(snapshot.get("at"))
            rows = _records(snapshot.get("jobs"))
            pairs = {str(row.get("job_id")): row for row in rows if str(row.get("job_id")) in mapping}
            if any(str(row.get("session_id")) != mapping[str(row.get("job_id"))] for row in pairs.values()):
                ownership = False
                reasons.append("active_snapshot_session_mismatch")
            if not identity_valid or at is None or set(pairs) != set(jobs) or len(rows) != 2:
                continue
            times = [[epoch(row.get("created_request_at")) for row in by_job.get(job_id, [])] for job_id in jobs]
            times = [[value for value in values if value is not None] for values in times]
            if not all(times) or at < max(min(values) for values in times):
                continue
            if ownership is True and all(row.get("status") in {"queued", "running", "waiting_input"} and str(row.get("session_id")) == mapping[str(row["job_id"])] for row in pairs.values()):
                activity = True
                overlap_at = at
                running = all(row.get("status") == "running" for row in pairs.values())
        if item.get("activity_overlap") is True and activity is not True:
            reasons.append("activity_claim_without_valid_joint_snapshot")
        execution = overlap_ms = None
        intervals = []
        if identity_valid and ownership is True:
            for job_id in jobs:
                row = db_rows.get(job_id, {})
                a, b = epoch(row.get("first_started_at")), epoch(row.get("finished_at"))
                reliable = (not row.get("missing") and row.get("identity_match") is not False
                            and row.get("attempt_count") == 1 and row.get("resume_count") == 0 and row.get("recovery_count") == 0
                            and row.get("first_started_at_source") == "analysis_jobs.started_at_coalesce_first_claim"
                            and row.get("session_owner_match") in (True, 1) and str(row.get("session_id")) == mapping[job_id]
                            and row.get("status") in TERMINAL and a is not None and b is not None and a <= b)
                if not reliable:
                    reasons.append("single_attempt_execution_interval_unavailable")
                    break
                intervals.append({"job_id": job_id, "first_started_at": a, "finished_at": b})
            if len(intervals) == 2:
                overlap_ms = max(0, min(row["finished_at"] for row in intervals) - max(row["first_started_at"] for row in intervals)) * 1000
                execution = overlap_ms > 0
        core = "verified" if ownership is True and activity is True else "failed" if ownership is False else "unverified"
        if execution is False:
            reasons.append("execution_overlap_not_triggered")
        status = "verified" if core == "verified" and execution is True else "failed" if core == "failed" else "unverified"
        results.append({"job_ids": jobs, "session_ids": sessions, "ownership_verified": ownership,
                        "activity_overlap": activity, "activity_overlap_at": overlap_at, "running_status_overlap": running,
                        "execution_overlap": execution, "execution_overlap_ms": overlap_ms,
                        "execution_overlap_source": "primary_db_single_attempt_intervals" if execution is not None else None,
                        "execution_intervals": intervals, "core_status": core, "status": status, "reasons": sorted(set(reasons))})
    return {"status": "verified" if results and all(row["status"] == "verified" for row in results) else
                      "failed" if any(row["status"] == "failed" for row in results) else "unverified",
            "core_status": "verified" if results and all(row["core_status"] == "verified" for row in results) else
                           "failed" if any(row["core_status"] == "failed" for row in results) else "unverified",
            "reason": ("multi_session_evidence_not_collected" if not evidence else
                       "execution_overlap_not_triggered" if any(row["execution_overlap"] is False for row in results) else None), "pairs": results}


def _server_observation_end(summary: dict, db_rows: dict) -> tuple[float | None, str | None]:
    candidates = []
    for field, time_field in (("job_db_check", "collected_at"), ("server_after", "collected_at"), ("resource_curve", "finished_at")):
        payload = summary.get(field) or {}
        if (payload.get("run_id") != summary.get("run_id") or not summary.get("run_id")
                or payload.get("status") in {"missing", "error", "unknown"}):
            continue
        if field == "job_db_check" and not db_rows:
            continue
        at = epoch(payload.get(time_field))
        if at is not None:
            candidates.append((at, f"{field}.{time_field}"))
    for row in db_rows.values():
        at = epoch(row.get("db_read_finished_at"))
        if at is not None:
            candidates.append((at, "job_db_check.jobs.db_read_finished_at"))
    return max(candidates) if candidates else (None, None)


def _phases(summary: dict, samples: list[dict], db_rows: dict) -> dict:
    start, stop, client_end = (epoch(summary.get(key)) for key in ("started_at", "stop_submitting_at", "observation_ended_at"))
    if start is None or stop is None or start > stop:
        return {"load": None, "drain": None}
    observed_until, observation_source = _server_observation_end(summary, db_rows)
    drain = summary.get("drain") or {}
    release_ms = _number(drain.get("drain_ms"))
    released_at = stop + release_ms / 1000 if drain.get("server_drained_at_check") is True and release_ms is not None else None
    server_end = released_at if released_at is not None else observed_until
    server_end_source = "primary_db_execution_release_timestamps" if released_at is not None else observation_source
    if server_end is not None and server_end < stop:
        server_end = None
    http_samples = (summary.get("http") or {}).get("samples")
    http_known = (isinstance(http_samples, list)
                  and len(http_samples) >= (_number((summary.get("http") or {}).get("requests")) or 0)
                  and all(isinstance(row, dict) and epoch(row.get("started_at")) is not None for row in http_samples))
    requests = summary.get("requests")
    client_known = isinstance(requests, list) and all(any(str(row.get("job_id")) == sample["job_id"] for row in _records(requests)) for sample in samples)
    denominator = max(len(samples), int(_number((summary.get("job") or {}).get("created")) or 0))
    client_unknown = sum(sample.get("terminal_received_at") is None and sample.get("final_status") in TERMINAL for sample in samples)
    client_known = client_known and denominator == len(samples) and not client_unknown
    server_unknown = sum(sample["db_status"] in TERMINAL and epoch(db_rows.get(sample["job_id"], {}).get("finished_at")) is None for sample in samples)
    server_known = denominator == len(db_rows) and not server_unknown
    result = {}
    for name in ("load", "drain"):
        a = start if name == "load" else stop
        client_b = stop if name == "load" else client_end
        server_b = stop if name == "load" else server_end
        client_seconds = client_b - a if client_b is not None and client_b >= a else None
        server_seconds = server_b - a if server_b is not None and server_b >= a else None
        def in_window(at, finish):
            value = epoch(at)
            return finish is not None and value is not None and a <= value <= finish and (name == "load" or value > a)
        http = sum(in_window(row.get("started_at"), client_b) for row in _records(http_samples)) if client_seconds is not None else None
        clients = [sample for sample in samples if in_window(sample.get("terminal_received_at"), client_b)]
        server = [sample for sample in samples if sample["db_status"] in TERMINAL and in_window(db_rows.get(sample["job_id"], {}).get("finished_at"), server_b)]
        queues = [sample["initial_queue_ms"] for sample in samples if sample["initial_queue_ms"] is not None
                  and sample["db_status"] in TERMINAL and in_window(db_rows.get(sample["job_id"], {}).get("first_started_at"), server_b)]
        client_count = len(clients) if client_known and client_seconds is not None else None
        client_success = sum(sample["final_status"] == "succeeded" for sample in clients) if client_count is not None and all(sample["final_status"] in TERMINAL for sample in clients) else None
        server_count = len(server) if server_known and db_rows and server_seconds is not None else None
        server_success = sum(sample["db_status"] == "succeeded" for sample in server) if server_count is not None else None
        result[name] = {"started_at": a, "finished_at": client_b, "duration_ms": client_seconds * 1000 if client_seconds is not None else None,
                        "client_finished_at": client_b, "client_duration_ms": client_seconds * 1000 if client_seconds is not None else None,
                        "server_finished_at": server_b, "server_duration_ms": server_seconds * 1000 if server_seconds is not None else None,
                        "server_end_source": "stop_submitting_at" if name == "load" else server_end_source,
                        "server_execution_released_at": released_at, "server_observed_until": observed_until,
                        "http_requests": http if http_known else None, "http_rps": _rate(http if http_known else None, client_seconds),
                        "client_completions": client_count, "client_completion_throughput": _rate(client_count, client_seconds),
                        "client_successes": client_success, "client_success_throughput": _rate(client_success, client_seconds),
                        "server_completions": server_count, "server_completion_throughput": _rate(server_count, server_seconds),
                        "server_successes": server_success, "server_success_throughput": _rate(server_success, server_seconds),
                        "known_server_completions": len(server) if db_rows and server_seconds is not None else None,
                        "client_terminal_timestamp_unknown": client_unknown,
                        "server_denominator": denominator, "server_checked": len(db_rows), "server_coverage": _rate(len(db_rows), denominator),
                        "server_finished_timestamp_unknown": sum(sample["db_status"] in TERMINAL and epoch(db_rows.get(sample["job_id"], {}).get("finished_at")) is None for sample in samples),
                        "completed_sample_queue_p95_ms": percentile(queues, .95),
                        "queue_samples": len(queues), "queue_phase_basis": "first_started_at"}
    return result


def _ordinal(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _cdfm(summary: dict, accepted: set[str], db_rows: dict, logcheck: dict | None = None) -> dict:
    """事件只提供身份的观察；分别归并 invocation、worker attempt 与 MCP retry。"""
    calls = []
    db_covered = set()
    for job_id, row in db_rows.items():
        if job_id not in accepted:
            continue
        calls.extend({**call, "job_id": job_id, "evidence_source": "events"} for call in _records(row.get("cdfm_calls")))
        if (isinstance(row.get("cdfm_calls"), list) and
                ((row.get("cdfm") or {}).get("status") == "collected" or isinstance(row.get("persisted_event_ids"), list))):
            db_covered.add(job_id)
    calls.extend({**call, "evidence_source": "logs" if call.get("source") == "mcp_logs" else "summary"}
                 for call in _records(summary.get("cdfm_calls")))
    logs = summary.get("cdfm_log_evidence") or {}
    attached_log = logcheck if logcheck is not None else summary.get("logcheck")
    if (isinstance(attached_log, dict) and attached_log.get("run_id") == summary.get("run_id")
            and attached_log.get("scope") == "run" and isinstance(attached_log.get("cdfm"), dict)):
        logs = {**attached_log["cdfm"], "run_id": attached_log["run_id"], "scope": "run"}
    valid_logs = bool(summary.get("run_id")) and logs.get("run_id") == summary.get("run_id") and logs.get("scope") == "run"
    log_covered = set()
    if valid_logs:
        calls.extend({**call, "evidence_source": "logs"} for call in _records(logs.get("records")))
        log_covered = {str(job_id) for job_id, status in (logs.get("coverage") or {}).items()
                       if isinstance(status, dict) and status.get("status") == "collected"} & accepted
    # 日志只有本轮身份记录不足以证明所有 Job 都检索完整。
    collected_jobs = db_covered | log_covered
    by_invocation: dict[tuple, list[dict]] = {}
    attempts: dict[tuple, list[dict]] = {}
    mcp_attempts = set()
    workers = set()
    jobs_with_calls = set()
    identity_gaps = set()
    logical_gaps = set()
    source_ids = {source: {"logical": set(), "attempt": set()} for source in ("events", "logs")}
    attempt_records = {}
    unidentified = missing_worker = missing_retry = foreign = 0
    for call in calls:
        job_id = str(call.get("job_id") or "")
        if job_id not in accepted:
            foreign += 1
            continue
        jobs_with_calls.add(job_id)
        invocation = call.get("invocation_id")
        if not invocation:
            unidentified += 1
            identity_gaps.add(job_id)
            logical_gaps.add(job_id)
            continue
        identity = (job_id, str(invocation))
        worker = _ordinal(call.get("worker_attempt", call.get("attempt")))
        retry = _ordinal(call.get("mcp_retry", call.get("retry_ordinal") if call.get("retry_ordinal") is not None else call.get("retry")))
        status = {"valid": "succeeded", "success": "succeeded", "error": "failed", "timeout": "timed_out", "cancelled": "canceled"}.get(call.get("status"), call.get("status"))
        call = {**call, "status": status}
        by_invocation.setdefault(identity, []).append({**call, "worker_ordinal": worker, "mcp_ordinal": retry})
        attempt_records.setdefault((*identity, worker, retry), []).append(call)
        source = call["evidence_source"]
        if source in source_ids:
            source_ids[source]["logical"].add(identity)
            if worker is not None and retry is not None:
                source_ids[source]["attempt"].add((*identity, worker, retry))
        missing_worker += int(worker is None)
        missing_retry += int(retry is None)
        if worker is None or retry is None:
            identity_gaps.add(job_id)
        if worker is not None:
            workers.add((*identity, worker))
        if worker is not None and retry is not None:
            mcp_attempts.add((*identity, worker, retry))
        if worker is not None and retry is not None:
            attempts.setdefault((*identity, worker, retry), []).append(call)
    terminals = {"succeeded", "valid", "failed", "timed_out", "canceled"}
    successes = {"succeeded", "valid"}
    succeeded = terminal = conflicts = 0
    durations = []
    outcomes = Counter({key: 0 for key in ("succeeded", "failed", "timed_out", "canceled", "unfinished", "unknown", "conflicting")})
    records = []
    for identity, entries in by_invocation.items():
        full = all(entry["worker_ordinal"] is not None and entry["mcp_ordinal"] is not None for entry in entries)
        latest = max((entry["worker_ordinal"], entry["mcp_ordinal"]) for entry in entries) if full else None
        selected = [entry for entry in entries if (entry["worker_ordinal"], entry["mcp_ordinal"]) == latest] if full else entries
        finals = {entry.get("status") for entry in selected if entry.get("status") in terminals}
        normalized = {"succeeded" if status in successes else status for status in finals}
        outcome = next(iter(normalized)) if len(normalized) == 1 else "conflicting" if len(normalized) > 1 else "unknown"
        if not normalized and full and any(entry.get("status") in {"received", "started", "running", "pending"} for entry in selected):
            outcome = "unfinished"
        outcomes[outcome] += 1
        records.append({"job_id": identity[0], "invocation_id": identity[1], "outcome": outcome,
                        "final_outcome": outcome if full and outcome in terminals else None,
                        "final_identity_verified": full and len(normalized) == 1,
                        "observed_terminal_statuses": sorted({entry["status"] for entry in entries if entry.get("status") in terminals}),
                        "identity_status": "complete" if full else "unknown",
                        "source_statuses": {source: sorted({str(entry.get("status") or "unknown") for entry in entries if entry["evidence_source"] == source})
                                            for source in sorted({entry["evidence_source"] for entry in entries})},
                        "sources": sorted({entry["evidence_source"] for entry in entries})})
        if len(normalized) == 1:
            terminal += 1
            succeeded += int("succeeded" in normalized)
        elif len(normalized) > 1:
            conflicts += 1
            identity_gaps.add(identity[0])
    for entries in attempts.values():
        values = {_number(entry.get("duration_ms")) for entry in entries if entry.get("status") in terminals}
        values.discard(None)
        # 同一次身份的重复日志不扩大时延样本；冲突时不选择一个值冒充事实。
        if len(values) == 1:
            durations.append(values.pop())
    covered_jobs = collected_jobs - identity_gaps
    complete = bool(accepted) and covered_jobs == accepted and not (unidentified or missing_worker or missing_retry or conflicts)
    # retry=0 是首次 MCP 调用；worker 重新执行不算 MCP retry。
    retries = sum(identity[-1] > 0 for identity in attempts)
    def identity_rows(values):
        return [dict(zip(("job_id", "invocation_id", "worker_attempt", "mcp_retry"), value)) for value in sorted(values)]
    reconciliation = {"both_collected_jobs": sorted(db_covered & log_covered),
                      "events_collected_jobs": sorted(db_covered), "logs_collected_jobs": sorted(log_covered)}
    for level in ("logical", "attempt"):
        left, right = source_ids["events"][level], source_ids["logs"][level]
        comparable = (db_covered & log_covered) - (logical_gaps if level == "logical" else identity_gaps)
        a = {value for value in left if value[0] in comparable}
        b = {value for value in right if value[0] in comparable}
        reconciliation[level] = {"status": "compared" if comparable else "unknown",
            "compared_jobs": sorted(comparable), "identity_unknown_jobs": sorted((db_covered & log_covered) - comparable),
            "events_identities": identity_rows(left), "log_identities": identity_rows(right),
            "intersection": identity_rows(a & b) if comparable else None,
            "events_only": identity_rows(a - b) if comparable else None,
            "logs_only": identity_rows(b - a) if comparable else None}
    attempt_details = []
    for identity, entries in attempt_records.items():
        attempt_details.append({"job_id": identity[0], "invocation_id": identity[1],
            "worker_attempt": identity[2], "mcp_retry": identity[3],
            "identity_status": "complete" if identity[2] is not None and identity[3] is not None else "unknown",
            "statuses": sorted({str(entry.get("status") or "unknown") for entry in entries}),
            "source_statuses": {source: sorted({str(entry.get("status") or "unknown") for entry in entries if entry["evidence_source"] == source})
                                for source in sorted({entry["evidence_source"] for entry in entries})},
            "sources": sorted({entry["evidence_source"] for entry in entries})})
    known = bool(by_invocation or collected_jobs)
    return {"status": "collected" if complete else "partial" if calls or collected_jobs else "missing",
            "source": "primary_db_and_mcp_logs" if valid_logs else "primary_db_events",
            "job_coverage": _rate(len(covered_jobs), len(accepted)), "covered_jobs": len(covered_jobs),
            "collection_covered_jobs": len(collected_jobs), "collection_job_coverage": _rate(len(collected_jobs), len(accepted)),
            "identity_gap_jobs": sorted(identity_gaps),
            "jobs_with_calls": len(jobs_with_calls) if calls or collected_jobs else None,
            "jobs_without_calls": len(collected_jobs - jobs_with_calls) if collected_jobs else None,
            "unidentified_records": unidentified if calls or collected_jobs else None,
            "missing_worker_identity_records": missing_worker if calls or collected_jobs else None,
            "missing_retry_identity_records": missing_retry if calls or collected_jobs else None,
            "foreign_records_excluded": foreign, "conflicting_invocations": conflicts,
            "logical_calls": len(by_invocation) if complete else None,
            "known_logical_calls": len(by_invocation) if known else None,
            "terminal_calls": terminal if known else None,
            "succeeded": succeeded if known else None,
            "outcomes": dict(outcomes) if known else None,
            "records": records if calls or collected_jobs else None,
            "attempt_records": attempt_details if calls or collected_jobs else None,
            "source_reconciliation": reconciliation,
            "final_success_rate": _rate(succeeded, len(by_invocation)) if complete and terminal == len(by_invocation) else None,
            "worker_attempts": len(workers) if complete else None,
            "known_worker_attempts": len(workers) if calls or covered_jobs else None,
            "known_mcp_attempts": len(mcp_attempts) if calls or covered_jobs else None,
            "known_actual_attempts": len(attempts) if calls or covered_jobs else None,
            "actual_attempts": len(attempts) if complete else None, "retries": retries if complete else None,
            "attempt_duration_p95_ms": percentile(durations, .95), "duration_samples": len(durations),
            "accuracy": None, "accuracy_status": "unknown_without_ground_truth"}
