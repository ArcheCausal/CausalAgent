"""通过同源 Grafana 代理查询 Loki 日志，核对测试窗口内的服务端行为。

账号从部署配置读取，口令从统一密钥文件读取，不写入报告、日志或版本库，也不回显。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx

from runner import config
from runner.evidence import _job_ids, latest_runs
from ops.server_evidence import diagnostic_fields, request_samples, safe_token

DEPLOY_ENV_CANDIDATES = ("../.env.staging", ".env.staging")


def read_grafana_credentials() -> tuple[str, str]:
    """读取 Grafana 账号与口令，不打印任何取值。

    支持显式进程凭据；否则账号从部署配置读取，口令只从统一密钥文件读取。
    """
    env_user = os.environ.get("GRAFANA_ADMIN_USER", "").strip()
    env_password = os.environ.get("GRAFANA_ADMIN_PASSWORD", "").strip()
    if env_user and env_password:
        return env_user, env_password
    user = env_user
    for relative in DEPLOY_ENV_CANDIDATES:
        path = (config.LOADTEST_DIR / relative).resolve()
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        user = user or _match(text, "GRAFANA_ADMIN_USER")
    secret_path = Path(os.environ.get(
        "CAUSALAGENT_SECRET_FILE", str(config.LOADTEST_DIR.parent / "causalagent.env")
    ))
    try:
        password = _match(
            secret_path.read_text(encoding="utf-8"), "GRAFANA_ADMIN_PASSWORD", preserve_value=True
        )
    except OSError:
        raise RuntimeError("无法读取 Grafana 统一密钥文件，请设置 CAUSALAGENT_SECRET_FILE") from None
    if user and password and not password.startswith("REPLACE"):
        return user, password
    raise RuntimeError("未找到可用的 Grafana 凭据")


def _match(text: str, key: str, *, preserve_value: bool = False) -> str:
    match = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
    if not match:
        return ""
    value = match.group(1).removesuffix("\r")
    # 官方 Grafana 入口使用 Shell 读取器；引号和空格都是口令内容。
    if preserve_value:
        return value
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


class GrafanaLoki:
    """登录 Grafana 并通过其数据源代理查询 Loki。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/") + "/grafana"
        user, password = read_grafana_credentials()
        # staging 的 Grafana 关闭了登录表单，改用基础认证。
        self.client = httpx.Client(
            timeout=config.HTTP_TIMEOUT_SECONDS, auth=(user, password)
        )
        self.loki_uid: str | None = None

    def login(self) -> None:
        """确认凭据可用；基础认证下只校验 /api/user。"""
        response = self.client.get(f"{self.base_url}/api/user")
        if response.status_code != 200:
            raise RuntimeError(f"Grafana 凭据不可用: {response.status_code}")

    def datasources(self) -> list[dict]:
        response = self.client.get(f"{self.base_url}/api/datasources")
        response.raise_for_status()
        payload = response.json()
        return [
            {"name": item.get("name"), "type": item.get("type"), "uid": item.get("uid")}
            for item in payload
        ]

    def pick_loki(self) -> str:
        for item in self.datasources():
            if item["type"] == "loki":
                self.loki_uid = item["uid"]
                return item["uid"]
        raise RuntimeError("未找到 Loki 数据源")

    def loki_labels(self) -> list[str]:
        response = self.client.get(
            f"{self.base_url}/api/datasources/proxy/uid/{self.loki_uid}"
            "/loki/api/v1/labels"
        )
        response.raise_for_status()
        return response.json().get("data", [])

    def label_values(self, label: str) -> list[str]:
        response = self.client.get(
            f"{self.base_url}/api/datasources/proxy/uid/{self.loki_uid}"
            f"/loki/api/v1/label/{label}/values"
        )
        response.raise_for_status()
        return response.json().get("data", [])

    def query_range(
        self, query: str, start_ns: int, end_ns: int, limit: int = 200
    ) -> list[dict]:
        """按时间窗口查询日志，返回 (时间戳, 原始行) 列表。"""
        # 逐 Job 查询会形成连续请求，Loki 偶发返回 5xx 或连接错误；
        # 有限重试只消除瞬时失败，持续失败仍保留 unknown，不当作无日志。
        for attempt in range(3):
            try:
                response = self.client.get(
                    f"{self.base_url}/api/datasources/proxy/uid/{self.loki_uid}"
                    "/loki/api/v1/query_range",
                    params={
                        "query": query,
                        "start": str(start_ns),
                        "end": str(end_ns),
                        "limit": str(limit),
                        "direction": "forward",
                    },
                )
                response.raise_for_status()
                break
            except (httpx.HTTPError, httpx.TransportError):
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
        response.raise_for_status()
        streams = response.json().get("data", {}).get("result", [])
        rows: list[dict] = []
        for stream in streams:
            labels = stream.get("stream", {})
            for timestamp, line in stream.get("values", []):
                rows.append({"timestamp": timestamp, "labels": labels, "line": line})
        return rows

    def close(self) -> None:
        self.client.close()


def resolve_run_window(stage: str, summary: dict | None = None) -> tuple[int, int] | None:
    """按档位找到最近一次运行的时间窗口，返回纳秒时间戳。"""
    if summary is None:
        path = latest_runs(config.RUNS_DIR).get(stage)
        if path is None:
            return None
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    started = summary.get("started_at")
    finished = summary.get("finished_at")
    if not started or not finished:
        return None
    from ops.server_evidence import epoch

    db = summary.get("job_db_check") or {}
    scoped = db.get("scope") == "run" and db.get("run_id") == summary.get("run_id")
    checked = epoch(db.get("db_checked_at")) if scoped else None
    finished = max(finished, checked) if checked is not None else finished
    return int(started * 1_000_000_000), int(finished * 1_000_000_000)


def scan_window(stage: str) -> dict:
    """查询一档运行窗口内的 Loki 标签结构，供进一步核对使用。"""
    client = GrafanaLoki(config.base_url())
    try:
        client.login()
        uid = client.pick_loki()
        window = resolve_run_window(stage)
        result: dict = {
            "loki_uid": uid,
            "labels": client.loki_labels(),
            "window": window,
            "label_values": {},
        }
        for label in result["labels"]:
            if label in ("job", "service_name", "container", "compose_service"):
                result["label_values"][label] = client.label_values(label)
        return result
    finally:
        client.close()


def sample_line(job_id: str, start_ns: int, end_ns: int) -> list[str]:
    """只返回指定 Job 的白名单事件字段，不输出原始日志。"""
    client = GrafanaLoki(config.base_url())
    try:
        client.login()
        client.pick_loki()
        out = []
        for service in ("web", "worker", "mcp"):
            rows = client.query_range(
                f'{{service_name="{service}"}} |= {json.dumps(job_id)}', start_ns, end_ns, 20
            )
            for row in rows:
                parsed = _parse_job_row(row, {job_id})
                if parsed:
                    out.append(json.dumps({"service": service, **parsed}, ensure_ascii=False))
        return out
    finally:
        client.close()


def main(stage: str | None = None) -> int:
    """发现模式：打印数据源与标签结构，不输出任何日志正文。"""
    try:
        client = GrafanaLoki(config.base_url())
    except RuntimeError as exc:
        print(f"无法准备日志查询: {exc}")
        return 1
    try:
        client.login()
    except (httpx.HTTPError, RuntimeError) as exc:
        client.close()
        print(
            "Grafana 登录失败，日志侧核查暂时不可用。"
            "请检查 Grafana 连接与认证配置；本次未取得日志证据。"
            f"（{exc}）"
        )
        return 1
    try:
        print("数据源:", json.dumps(client.datasources(), ensure_ascii=False))
        uid = client.pick_loki()
        print("Loki uid:", uid)
        print("标签:", client.loki_labels())
        if stage:
            window = resolve_run_window(stage)
            print(f"{stage} 运行窗口（纳秒）:", window)
    finally:
        client.close()
    return 0


def _parse_job_row(row: dict, allowed: set[str]) -> dict | None:
    """文本命中后仍需校验结构化 job_id；正文里的 UUID 不作为归属证据。"""
    try:
        payload = json.loads(row["line"])
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
    if not isinstance(payload, dict) or str(payload.get("job_id") or "") not in allowed:
        return None
    result = diagnostic_fields(payload)
    result["loki_timestamp_ns"] = row.get("timestamp")
    stack = payload.get("stack")
    frame = stack[-1] if isinstance(stack, list) and stack else None
    # 兼容既有字段，但只允许共享运行时的清理后代码帧，不接纳异常正文。
    result["stack_top"] = frame if isinstance(frame, str) and re.fullmatch(r"[A-Za-z0-9_./<>-]+:[0-9]+:[A-Za-z0-9_<>]+", frame) else None
    return result


def _scoped_logs(client: GrafanaLoki, job_ids: list[str], start_ns: int, end_ns: int) -> tuple[list[dict], dict]:
    """每个 Job/服务独立查询；截断或失败保留未知，不据空结果判定通过。"""
    events, coverage, seen = [], {}, set()
    limit = 5000
    for job_id in job_ids:
        for service in ("web", "worker", "mcp"):
            key = f"{job_id}/{service}"
            try:
                rows = client.query_range(
                    f'{{service_name="{service}"}} |= {json.dumps(job_id)}',
                    start_ns, end_ns, limit
                )
            except (httpx.HTTPError, ValueError, TypeError):
                coverage[key] = {"status": "unknown", "reason": "query_failed"}
                continue
            matched = 0
            for row in rows:
                parsed = _parse_job_row(row, {job_id})
                if not parsed:
                    continue
                matched += 1
                identity = (service, row.get("timestamp"), row.get("line"))
                if identity in seen:
                    continue
                seen.add(identity)
                events.append({"service": service, **parsed})
            coverage[key] = {"status": "unknown" if len(rows) >= limit or not matched else "collected",
                             "reason": "limit_reached" if len(rows) >= limit else ("missing_job_logs" if not matched else None),
                             "matched_rows": matched, "limit": limit}
    events.sort(key=lambda row: int(row.get("loki_timestamp_ns") or 0))
    return events, coverage


def _request_logs(client: GrafanaLoki, ids: list[str], start_ns: int, end_ns: int) -> tuple[list[dict], dict]:
    events, coverage = [], {}
    limit = 5000
    for request_id in ids:
        try:
            rows = client.query_range(f'{{service_name="web"}} |= {json.dumps(request_id)}', start_ns, end_ns, limit)
        except (httpx.HTTPError, ValueError, TypeError):
            coverage[f"{request_id}/web"] = {"status": "unknown", "reason": "query_failed"}
            continue
        found = []
        for row in rows:
            try:
                payload = json.loads(row["line"])
            except (ValueError, TypeError, KeyError):
                continue
            if isinstance(payload, dict) and payload.get("request_id") == request_id:
                found.append({"service": "web", **diagnostic_fields(payload), "loki_timestamp_ns": row.get("timestamp")})
        events.extend(found)
        coverage[f"{request_id}/web"] = {"status": "collected" if found and len(rows) < limit else "unknown",
            "matched_rows": len(found), "reason": "limit_reached" if len(rows) >= limit else (None if found else "missing_request_logs")}
    return events, coverage


def correlate_requests(summary: dict, events: list[dict], coverage: dict) -> dict:
    samples = request_samples(summary)
    db = summary.get("job_db_check") or {}
    scoped = db.get("scope") == "run" and db.get("run_id") == summary.get("run_id")
    server = (db.get("request_evidence") or {}) if scoped else {}
    records = []
    for sample in samples:
        ids = {sample[key] for key in ("request_id", "client_request_id", "response_request_id") if sample.get(key)}
        gateway = [row for row in server.get("gateway", []) if row.get("request_id") in ids]
        web = [row for row in events if row.get("service") == "web" and row.get("request_id") in ids]
        if not web:
            web = [row for row in server.get("web", []) if row.get("request_id") in ids]
        gateway_ok = bool(gateway) and all(server.get("coverage", {}).get(f"{row['request_id']}/gateway", {}).get("status") == "collected" for row in gateway)
        web_ok = bool(web) and all(coverage.get(f"{row['request_id']}/web",
            server.get("coverage", {}).get(f"{row['request_id']}/web", {})).get("status") == "collected" for row in web)
        upstream_ok = bool(gateway) and all(all(row.get(key) is not None for key in (
            "upstream_status", "upstream_response_time", "upstream_connect_time", "upstream_header_time", "request_time")) for row in gateway)
        records.append({"client": sample, "gateway": gateway, "web": web,
            "status": "collected" if ids and gateway_ok and web_ok and upstream_ok else "unknown",
            "gateway_status": "collected" if gateway_ok else "unknown",
            "web_status": "collected" if web_ok else "unknown",
            "upstream_fields_status": "collected" if upstream_ok else "unknown"})
    return {"status": ("collected" if all(row["status"] == "collected" for row in records) else "unknown") if records else "not_applicable",
            "records": records, "scope": "run", "run_id": summary.get("run_id"), "coverage": coverage}


def cdfm_log_evidence(events: list[dict], coverage: dict, job_ids: list[str]) -> dict:
    """按 Job/invocation/attempt/retry 去重；缺失身份时保留未知。"""
    statuses = {"mcp.request.received": "received", "mcp.tool.finished": "succeeded",
                "mcp.tool.failed": "failed", "mcp.tool.canceled": "canceled"}
    attempts, unidentified = {}, []
    allowed = set(job_ids)
    for event in events:
        details = event.get("details") or {}
        if (event.get("service") != "mcp" or event.get("job_id") not in allowed
                or "cdfm" not in str(details.get("capability") or "").lower()
                or event.get("event_code") not in statuses):
            continue
        retry = details.get("retry_ordinal") if details.get("retry_ordinal") is not None else details.get("retry")
        record = {"source": "mcp_logs", "job_id": event["job_id"],
                  "invocation_id": event.get("invocation_id"), "attempt": details.get("attempt"),
                  "retry": retry, "retry_ordinal": retry, "status": statuses[event["event_code"]],
                  "duration_ms": details.get("duration_ms"), "event_id": None,
                  "log_timestamp_ns": event.get("loki_timestamp_ns"),
                  "request_id": event.get("request_id"), "event_codes": [event["event_code"]]}
        attempt = record["attempt"]
        if (not record["invocation_id"] or not isinstance(retry, int) or isinstance(retry, bool) or retry < 0
                or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0):
            unidentified.append(record)
            continue
        key = (record["job_id"], record["invocation_id"], attempt, retry)
        prior = attempts.get(key)
        if prior is None:
            attempts[key] = record
            continue
        codes = sorted(set(prior["event_codes"] + record["event_codes"]))
        # received 和 terminal 是同一次尝试的两种观察。优先终态，再按实际日志时间排序。
        rank = lambda item: (item["status"] != "received", int(item.get("log_timestamp_ns") or 0))
        selected = record if rank(record) > rank(prior) else prior
        selected["event_codes"] = codes
        attempts[key] = selected
    records = list(attempts.values()) + unidentified
    mcp_coverage = {job_id: coverage.get(f"{job_id}/mcp", {"status": "unknown"}) for job_id in job_ids}
    complete = bool(job_ids) and all(row.get("status") == "collected" for row in mcp_coverage.values())
    logical = {(row["job_id"], row["invocation_id"]) for row in records if row["invocation_id"]}
    identified = bool(records) and not unidentified
    return {"source": "mcp_logs", "status": "collected" if complete and identified else "missing",
            "records": records, "coverage": mcp_coverage, "job_ids": job_ids,
            "logical_calls": len(logical) if complete and records and all(row["invocation_id"] for row in records) else None,
            "actual_attempts": len(attempts) if complete and identified else None,
            "known_logical_calls": len(logical), "known_actual_attempts": len(attempts),
            "unidentified_records": len(unidentified)}


def logcheck(stage: str, base_url: str, *, summary: dict | None = None) -> dict:
    """仅按本轮已知和主库核对恢复出的 Job 统计，不查询七天全局历史。"""
    if summary is None:
        path = latest_runs(config.RUNS_DIR).get(stage)
        if path is None:
            raise RuntimeError(f"找不到 {stage} 的运行窗口")
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    if summary.get("stage") != stage or not summary.get("run_id"):
        raise ValueError("日志采集摘要身份无效")
    window = resolve_run_window(stage, summary)
    if window is None:
        raise RuntimeError(f"找不到 {stage} 的运行窗口")
    ids = _job_ids(summary)
    db = summary.get("job_db_check") or {}
    if db.get("scope") == "run" and db.get("run_id") == summary.get("run_id"):
        ids = list(dict.fromkeys(ids + [str(row["job_id"]) for row in db.get("jobs", []) if row.get("job_id")]))
    result = {"schema_version": "loadtest-logcheck-v2", "run_id": summary.get("run_id"),
              "stage": stage, "scope": "run", "job_ids": ids, "window_ns": list(window),
              "status": "unknown", "counts": {}, "errors": {}, "job_lifecycle": {},
              "request_id_propagation": {}, "capability": {}, "event_codes": {},
              "web_failures": [], "worker_degraded": [],
              "mcp_failure_history": {}, "coverage": {}, "cdfm": {"status": "missing"}}
    requests = request_samples(summary)
    request_ids = sorted({row[key] for row in requests for key in ("request_id", "client_request_id", "response_request_id") if row.get(key)})
    result["request_evidence"] = correlate_requests(summary, [], {})
    if not ids and not request_ids:
        result["reason"] = "missing_run_job_and_request_ids"
        return result
    client = None
    try:
        client = GrafanaLoki(base_url)
        client.login()
        client.pick_loki()
        events, coverage = _scoped_logs(client, ids, *window)
        request_events, request_coverage = _request_logs(client, request_ids, *window)
        result["request_evidence"] = correlate_requests(summary, request_events, request_coverage)
        seen = {(row.get("service"), row.get("loki_timestamp_ns"), row.get("event_code"), row.get("request_id")) for row in events}
        events.extend(row for row in request_events if (row.get("service"), row.get("loki_timestamp_ns"), row.get("event_code"), row.get("request_id")) not in seen)
    except (httpx.HTTPError, RuntimeError, ValueError):
        result["reason"] = "log_source_unavailable"
        return result
    finally:
        if client is not None:
            client.close()
    result["coverage"] = coverage
    complete = ((bool(coverage) and all(row["status"] == "collected" for row in coverage.values())) if ids else True)
    complete = complete and result["request_evidence"]["status"] in {"collected", "not_applicable"}
    result["status"] = "collected" if complete else "unknown"
    for service in ("web", "worker", "mcp"):
        own = [row for row in events if row["service"] == service]
        result["event_codes"][service] = {}
        for level in ("info", "warning", "error"):
            result["counts"][f"{service}/{level}"] = sum(row.get("level") == level for row in own) if complete else None
        result["errors"][service] = [row for row in own if row.get("level") == "error"]
        for row in own:
            code = row.get("event_code") or "<无事件码>"
            counter = result["event_codes"][service]
            counter[code] = counter.get(code, 0) + 1
    lifecycles, propagation = [], []
    for job_id in ids:
        own = [row for row in events if row.get("job_id") == job_id]
        lifecycles.append({"job_id": job_id, "event_count": len(own), "events": own,
                           "status": "collected" if own else "missing"})
        web_ids = {row["request_id"] for row in own if row["service"] == "web" and row.get("request_id")}
        worker_ids = {row["request_id"] for row in own if row["service"] == "worker" and row.get("request_id")}
        propagation.append({"job_id": job_id, "gateway_entry_request_ids": sorted(web_ids),
                            "worker_request_ids": sorted(worker_ids), "shared_request_ids": sorted(web_ids & worker_ids),
                            "propagated": bool(web_ids & worker_ids) if web_ids and worker_ids else None})
    result["job_lifecycle"] = {**(lifecycles[0] if lifecycles else {}), "jobs": lifecycles}
    result["request_id_propagation"] = {**(propagation[0] if propagation else {}), "jobs": propagation}
    codes = {"mcp.request.received": "received", "mcp.tool.finished": "finished",
             "mcp.tool.failed": "failed", "mcp.tool.slow": "slow"}
    for row in events:
        capability = row["details"].get("capability")
        if row["service"] == "mcp" and capability and row.get("event_code") in codes:
            bucket = result["capability"].setdefault(capability, {key: 0 for key in codes.values()})
            bucket[codes[row["event_code"]]] += 1
    if not complete:
        for bucket in result["capability"].values():
            for key, count in bucket.items():
                if count == 0:
                    bucket[key] = None
    result["web_failures"] = [row for row in events if row["service"] == "web" and row.get("level") == "error"]
    result["worker_degraded"] = [row for row in events if row["service"] == "worker" and row.get("level") == "warning"]
    result["cdfm"] = {**cdfm_log_evidence(events, coverage, ids),
                       "run_id": summary.get("run_id"), "scope": "run"}
    if db.get("scope") == "run" and db.get("run_id") == summary.get("run_id"):
        known = [row for row in db.get("jobs", []) if row.get("job_id") in ids]
        records = [call for row in known for call in row.get("cdfm_calls", [])]
        result["cdfm"]["persisted_records"] = records
    return result


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
