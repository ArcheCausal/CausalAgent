"""通过同源 Grafana 代理查询 Loki 日志，核对测试窗口内的服务端行为。

凭据只在进程内从部署环境文件读取，不写入报告、日志或版本库，也不回显。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx

from runner import config
from runner.evidence import latest_runs

DEPLOY_ENV_CANDIDATES = ("../.env.staging", ".env.staging")


def read_grafana_credentials() -> tuple[str, str]:
    """读取 Grafana 账号与口令，不打印任何取值。

    优先使用进程环境变量，随后才回退到部署环境文件；两者都不写入磁盘或报告。
    """
    env_user = os.environ.get("GRAFANA_ADMIN_USER", "").strip()
    env_password = os.environ.get("GRAFANA_ADMIN_PASSWORD", "").strip()
    if env_user and env_password:
        return env_user, env_password
    for relative in DEPLOY_ENV_CANDIDATES:
        path = (config.LOADTEST_DIR / relative).resolve()
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        user = _match(text, "GRAFANA_ADMIN_USER")
        password = _match(text, "GRAFANA_ADMIN_PASSWORD")
        if user and password and not password.startswith("REPLACE"):
            return user, password
    raise RuntimeError("未找到可用的 Grafana 凭据")


def _match(text: str, key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


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
        streams = response.json().get("data", {}).get("result", [])
        rows: list[dict] = []
        for stream in streams:
            labels = stream.get("stream", {})
            for timestamp, line in stream.get("values", []):
                rows.append({"timestamp": timestamp, "labels": labels, "line": line})
        return rows

    def close(self) -> None:
        self.client.close()


def resolve_run_window(stage: str) -> tuple[int, int] | None:
    """按档位找到最近一次运行的时间窗口，返回纳秒时间戳。"""
    path = latest_runs(config.RUNS_DIR).get(stage)
    if path is None:
        return None
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    started = summary.get("started_at")
    finished = summary.get("finished_at")
    if not started or not finished:
        return None
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
    """列出每个服务引用该 job_id 的样本行，用于确认日志字段结构。"""
    client = GrafanaLoki(config.base_url())
    try:
        client.login()
        client.pick_loki()
        out: list[str] = []
        for service in ("web", "worker", "mcp"):
            rows = client.query_range(
                f'{{service_name="{service}"}} |= "{job_id}"', start_ns, end_ns, 3
            )
            out.append(f"[{service}] 命中 {len(rows)} 行")
            for row in rows[:2]:
                out.append("  " + row["line"][:400])
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
            "需要可用的 Grafana 管理员口令；本机部署环境文件中的口令与服务器不一致。"
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


def logcheck(stage: str, base_url: str) -> dict:
    """按档位运行窗口核查服务端日志，返回结构化结论。"""
    window = resolve_run_window(stage)
    if window is None:
        raise RuntimeError(f"找不到 {stage} 的运行窗口")
    start_ns, end_ns = window
    client = GrafanaLoki(base_url)
    try:
        client.login()
        client.pick_loki()
        result: dict = {
            "stage": stage,
            "window_ns": [start_ns, end_ns],
            "counts": _level_counts(client, start_ns, end_ns),
            "errors": _error_samples(client, start_ns, end_ns),
            "job_lifecycle": _job_lifecycle(client, stage, start_ns, end_ns),
            "request_id_propagation": _request_id_propagation(
                client, stage, start_ns, end_ns
            ),
            "capability": _capability_breakdown(client, start_ns, end_ns),
            "event_codes": _event_code_counts(client, start_ns, end_ns),
            "web_failures": _web_failures(client, start_ns, end_ns),
            "worker_degraded": _worker_degraded(client, start_ns, end_ns),
            "mcp_failure_history": _mcp_failure_history(client),
        }
        return result
    finally:
        client.close()


def _level_counts(client: GrafanaLoki, start_ns: int, end_ns: int) -> dict:
    """统计各服务在各日志级别下的条数。"""
    counts: dict[str, int] = {}
    for service in ("web", "worker", "mcp"):
        for level in ("info", "warning", "error"):
            rows = client.query_range(
                f'{{service_name="{service}", level="{level}"}}',
                start_ns,
                end_ns,
                2000,
            )
            counts[f"{service}/{level}"] = len(rows)
    return counts


def _error_samples(client: GrafanaLoki, start_ns: int, end_ns: int) -> dict:
    """按服务取少量 error 级日志，只保留事件码与脱敏摘要。"""
    samples: dict[str, list[dict]] = {}
    for service in ("web", "worker", "mcp"):
        rows = client.query_range(
            f'{{service_name="{service}", level="error"}}', start_ns, end_ns, 20
        )
        samples[service] = [_summarize(row) for row in rows]
    return samples


def _job_lifecycle(
    client: GrafanaLoki, stage: str, start_ns: int, end_ns: int
) -> dict:
    """取一档运行里的一个 job_id，回放它在各服务的事件序列。"""
    job_id = _first_job_id(stage)
    if not job_id:
        return {"job_id": None, "note": "该档位没有记录到 job_id"}
    events: list[dict] = []
    for service in ("web", "worker", "mcp"):
        rows = client.query_range(
            f'{{service_name="{service}"}} |= "{job_id}"', start_ns, end_ns, 200
        )
        for row in rows:
            events.append({"service": service, **_summarize(row)})
    events.sort(key=lambda item: item["timestamp"])
    return {"job_id": job_id, "event_count": len(events), "events": events}


def _request_id_propagation(
    client: GrafanaLoki, stage: str, start_ns: int, end_ns: int
) -> dict:
    """核对同一 Job 的 request_id 是否从入口沿用到 worker。

    用一个已有的 job_id 在日志中关联：入口创建 Job 的行带有 request_id，
    worker 处理同一 Job 的行应沿用同一个 request_id，而不是各自重生。
    """
    job_id = _first_job_id(stage)
    if not job_id:
        return {"job_id": None, "note": "该档位没有记录到 job_id"}
    buckets: dict[str, set[str]] = {}
    for service in ("web", "worker", "mcp"):
        rows = client.query_range(
            f'{{service_name="{service}"}} |= "{job_id}"', start_ns, end_ns, 200
        )
        ids = set()
        for row in rows:
            try:
                payload = json.loads(row["line"])
            except (json.JSONDecodeError, TypeError):
                continue
            request_id = payload.get("request_id")
            if request_id:
                ids.add(request_id)
        buckets[service] = ids
    entry_ids = buckets.get("web", set())
    worker_ids = buckets.get("worker", set())
    shared = entry_ids & worker_ids
    return {
        "job_id": job_id,
        "gateway_entry_request_ids": sorted(entry_ids),
        "worker_request_ids": sorted(worker_ids),
        "shared_request_ids": sorted(shared),
        "propagated": bool(shared),
    }


def _capability_breakdown(client: GrafanaLoki, start_ns: int, end_ns: int) -> dict:
    """按算法能力统计收到、完成、失败与慢调用数量。

    MCP 侧每个能力有独立并发额度，整体条数会被可并行能力放大；按能力拆开后
    才能判断某个能力是否从未成功。
    """
    received: dict[str, int] = {}
    finished: dict[str, int] = {}
    failed: dict[str, int] = {}
    slow: dict[str, int] = {}
    for event_code, bucket in (
        ("mcp.request.received", received),
        ("mcp.tool.finished", finished),
        ("mcp.tool.failed", failed),
        ("mcp.tool.slow", slow),
    ):
        rows = client.query_range(
            f'{{service_name="mcp"}} |= "{event_code}"', start_ns, end_ns, 5000
        )
        for row in rows:
            try:
                payload = json.loads(row["line"])
            except (json.JSONDecodeError, TypeError):
                continue
            capability = (payload.get("details") or {}).get("capability") or "<未标注>"
            bucket[capability] = bucket.get(capability, 0) + 1
    return {
        capability: {
            "received": received.get(capability, 0),
            "finished": finished.get(capability, 0),
            "failed": failed.get(capability, 0),
            "slow": slow.get(capability, 0),
        }
        for capability in sorted(set(received) | set(finished) | set(failed) | set(slow))
    }


def _event_code_counts(client: GrafanaLoki, start_ns: int, end_ns: int) -> dict:
    """统计各服务在窗口内的事件码分布，用于识别非 error 级但异常的事件。"""
    breakdown: dict[str, dict[str, int]] = {}
    for service in ("web", "worker", "mcp"):
        rows = client.query_range(
            f'{{service_name="{service}"}}', start_ns, end_ns, 5000
        )
        counter: dict[str, int] = {}
        for row in rows:
            try:
                payload = json.loads(row["line"])
            except (json.JSONDecodeError, TypeError):
                counter["<无法解析>"] = counter.get("<无法解析>", 0) + 1
                continue
            code = payload.get("event_code") or "<无事件码>"
            counter[code] = counter.get(code, 0) + 1
        breakdown[service] = dict(
            sorted(counter.items(), key=lambda item: item[1], reverse=True)
        )
    return breakdown


def _web_failures(client: GrafanaLoki, start_ns: int, end_ns: int) -> list[dict]:
    """列出 web 侧 error 级请求失败的端点、状态码与异常类型。"""
    rows = client.query_range(
        '{service_name="web", level="error"}', start_ns, end_ns, 50
    )
    failures: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["line"])
        except (json.JSONDecodeError, TypeError):
            continue
        details = payload.get("details") or {}
        failures.append(
            {
                "timestamp": payload.get("timestamp"),
                "event_code": payload.get("event_code"),
                "method": details.get("method"),
                "endpoint": details.get("endpoint"),
                "status_code": details.get("status_code"),
                "reason_code": details.get("reason_code"),
                "exception_type": payload.get("exception_type"),
                "request_id": payload.get("request_id"),
                "stack_top": (payload.get("stack") or [None])[0],
            }
        )
    return failures


def _worker_degraded(client: GrafanaLoki, start_ns: int, end_ns: int) -> list[dict]:
    """列出 worker 侧的 warning 事件，含节点降级与原因码。"""
    rows = client.query_range(
        '{service_name="worker", level="warning"}', start_ns, end_ns, 50
    )
    events: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["line"])
        except (json.JSONDecodeError, TypeError):
            continue
        events.append(
            {
                "timestamp": payload.get("timestamp"),
                "event_code": payload.get("event_code"),
                "details": payload.get("details"),
            }
        )
    return events


def _mcp_failure_history(client: GrafanaLoki) -> dict:
    """统计最近七天 MCP 侧的成功与失败，判断故障是否为长期存在。

    CDFM 每次调用都会从 HuggingFace Hub 加载权重；把成功与失败按能力拆分，
    可以直接判断某个能力在这段时间里是否从未成功过。
    """
    now_ns = int(time.time() * 1_000_000_000)
    start_ns = now_ns - 7 * 24 * 3600 * 1_000_000_000
    finished: dict[str, int] = {}
    failed: dict[str, int] = {}
    for event_code, bucket in (
        ("mcp.tool.finished", finished),
        ("mcp.tool.failed", failed),
    ):
        rows = client.query_range(
            f'{{service_name="mcp"}} |= "{event_code}"', start_ns, now_ns, 5000
        )
        for row in rows:
            try:
                payload = json.loads(row["line"])
            except (json.JSONDecodeError, TypeError):
                continue
            capability = (payload.get("details") or {}).get("capability") or "<未标注>"
            bucket[capability] = bucket.get(capability, 0) + 1
    reloads = client.query_range(
        '{service_name="mcp"} |= "Loading CDFM from"', start_ns, now_ns, 5000
    )
    return {
        "window_ns": [start_ns, now_ns],
        "finished_by_capability": finished,
        "failed_by_capability": failed,
        "cdfm_model_load_attempts": len(reloads),
    }



def _summarize(row: dict) -> dict:
    """只保留时间、事件码、级别与消息，去掉正文与堆栈细节。"""
    try:
        payload = json.loads(row["line"])
    except (json.JSONDecodeError, TypeError):
        return {"timestamp": row.get("timestamp"), "raw_prefix": row["line"][:160]}
    return {
        "timestamp": payload.get("timestamp") or row.get("timestamp"),
        "level": payload.get("level"),
        "event_code": payload.get("event_code"),
        "message": payload.get("message"),
        "age_seconds": payload.get("age_seconds"),
    }


def _first_job_id(stage: str) -> str | None:
    path = latest_runs(config.RUNS_DIR).get(stage)
    if path is None:
        return None
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    samples = summary.get("sse", {}).get("samples", [])
    for sample in samples:
        if sample.get("job_id"):
            return str(sample["job_id"])
    return None


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
