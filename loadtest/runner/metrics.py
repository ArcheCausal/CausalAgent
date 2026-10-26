"""线程安全的结果记录器：HTTP、Job、SSE 三类指标与聚合统计。"""

from __future__ import annotations

import json
import math
import threading
import time
from copy import deepcopy
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path


def percentile(values: list[float], ratio: float) -> float | None:
    """返回排序后的线性插值百分位；空列表返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * ratio
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


@dataclass
class HttpSample:
    """一次 HTTP 请求的结果。"""

    method: str
    path: str
    status: int
    duration_ms: float
    error: str = ""
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class SseSample:
    """一次 SSE 订阅的结果。"""

    job_id: str
    first_event_ms: float | None
    terminal_event_ms: float | None
    terminal_event: str | None
    last_event_id: int
    disconnects: int
    reconnects: int
    received_ids: list[int] = field(default_factory=list)
    error: str = ""


@dataclass
class JobSample:
    """一个分析 Job 的生命周期结果。

    seconds 字段只为旧调用方保留。新时延由逐请求记录与主库核对计算，统一使用 ms。
    observation 表示客户端是否取得终态；没有终态的请求仍保留在逻辑请求登记中。
    """

    job_id: str
    created_status: int
    final_status: str | None
    queue_seconds: float | None
    execution_seconds: float | None
    total_seconds: float | None
    history_consistent: bool | None
    user_index: int = 0
    session_index: int = 0
    queue_timeout: bool = False
    observation: str = "terminal"
    queue_seconds_lower_bound: float | None = None
    note: str = ""


class Recorder:
    """汇总一次运行内所有观测结果，可导出 JSON 摘要。"""

    def __init__(
        self, run_id: str, output_dir: Path | None = None, date_dir: str | None = None
    ) -> None:
        self.run_id = run_id
        # 日期目录名与 run_id 一起定位产物；缺省时由摘要开始时刻推出。
        self.date_dir = date_dir
        self._lock = threading.Lock()
        self.requests: dict[str, dict] = {}
        self._job_requests: dict[str, str] = {}
        self.output_dir = output_dir
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
        self.observation_stop = threading.Event()
        self.observation_deadline = float("inf")
        self.stop_submitting_at: float | None = None
        self.observation_ended_at: float | None = None
        self.db_checked_at: float | None = None
        self.sse_states: dict[str, dict] = {}
        self.drain: dict = {}
        self.scenario: dict = {}
        self.http: list[HttpSample] = []
        self.sse: list[SseSample] = []
        self.jobs: list[JobSample] = []
        self.job_created = 0
        # 本轮成功创建过的唯一 job_id，按创建顺序记录；终态观测可能因排空期限
        # 到达而没有结果，这些 Job 仍要能进入主库核对清单。
        self.job_created_ids: list[str] = []
        self.job_idempotent_replays = 0
        # 提交侧计数：尝试创建、因额度用尽而跳过的创建、因会话忙而放弃的创建。
        self.job_create_attempts = 0
        self.job_submissions_over_budget = 0
        self.job_submissions_session_busy = 0
        # 观察侧计数：观察期限到达仍未终结的 Job。
        self.job_observation_timeouts = 0
        # 每账号成功创建的唯一新 Job 数量；键是虚拟用户序号。
        self.job_created_by_user: dict[int, int] = {}
        # 每个账号实际同时打开的会话数与同时运行的活动 Job 数峰值。
        self.peak_sessions_per_user: int | None = None
        self.peak_active_jobs_per_user: int | None = None
        self.peak_active_jobs_per_account: int | None = None
        self.notes: list[str] = []
        self.started_at = 0.0
        self.finished_at = 0.0
        self.stage = ""
        self.users = 0
        # 运行期间是否出现重复或乱序 SSE 事件；由执行器写入。
        self.sse_duplicate_events = 0
        self.sse_out_of_order_events = 0
        # 与全量事件集合逐 ID 比对得到的漏事件数；由执行器写入。
        self.sse_missing_events = 0
        # 并发峰值由执行器写入；没有采集来源时保持 None。
        self.peak_active_jobs: int | None = None
        self.peak_sse: int | None = None
        # 服务器侧只读采集结果；没有采集入口时保持 None。
        self.server_baseline: dict | None = None
        self.server_after: dict | None = None

    def _append(self, record: dict) -> None:
        if self.output_dir is not None:
            with (self.output_dir / "jobs.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def register_request(self, logical_request_id: str, **fields) -> None:
        """发送前持久化请求身份；文件不包含口令、提示词或文件正文。"""
        with self._lock:
            if logical_request_id in self.requests:
                raise ValueError("逻辑请求已经登记")
            row = {"logical_request_id": logical_request_id, "admission": "pending",
                   "username": None, "session_id": None, "idempotency_key": None,
                   "job_id": None, "created_request_at": None, "create_response_at": None,
                   "subscription_started_at": None, "sse_headers_at": None, "first_event_at": None,
                   "terminal_received_at": None, "user_wait_ms": None,
                   "wait_lower_bound_ms": None, "history_check_ms": None,
                   "received_event_ids": None, "group": "ordinary", **fields}
            self.requests[logical_request_id] = row
            self._append({"schema_version": "loadtest.lifecycle.v2", "run_id": self.run_id,
                          "event": "registered", "at": time.time(), **row})

    def lifecycle(self, logical_request_id: str | None = None, job_id: str | None = None,
                  event: str = "update", **fields) -> None:
        with self._lock:
            key = logical_request_id or self._job_requests.get(str(job_id))
            if key is None or key not in self.requests:
                # 专项和旧调用方没有登记逻辑请求时仍保存事件，但不虚构接纳分母。
                self._append({"run_id": self.run_id, "event": event, "at": time.time(),
                              "job_id": job_id, **fields})
                return
            row = self.requests[key]
            if job_id:
                row["job_id"] = job_id
                self._job_requests.setdefault(job_id, key)
            # 每个生命周期更新是完整快照，崩溃后也可逐条回放。
            row.update(fields)
            self._append({"schema_version": "loadtest.lifecycle.v2", "run_id": self.run_id,
                          "event": event, "at": time.time(), **row})

    def sse_transition(self, state: str, delta: int = 1) -> None:
        with self._lock:
            counts = self.sse_states.setdefault(state, {"current": 0, "peak": 0, "entries": 0})
            counts["current"] = max(0, counts["current"] + delta)
            counts["peak"] = max(counts["peak"], counts["current"])
            if delta > 0:
                counts["entries"] += delta

    def bump_user(self, field_name: str, user_index: int, amount: int = 1) -> None:
        """按账号累加一个计数；用于每账号成功创建 Job 这类分账号口径。"""
        with self._lock:
            mapping = getattr(self, field_name)
            mapping[user_index] = mapping.get(user_index, 0) + amount

    def record_http(self, sample: HttpSample) -> None:
        with self._lock:
            self.http.append(sample)

    def record_sse(self, sample: SseSample) -> None:
        with self._lock:
            self.sse.append(sample)

    def record_job(self, sample: JobSample) -> None:
        with self._lock:
            self.jobs.append(sample)

    def bump(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, field_name, getattr(self, field_name) + amount)

    def record_created_job(self, job_id: str) -> bool:
        """登记一个成功创建的唯一新 Job，用于导出主库核对清单。"""
        with self._lock:
            if job_id in self.job_created_ids:
                return False
            self.job_created_ids.append(job_id)
            return True

    def note(self, message: str) -> None:
        with self._lock:
            self.notes.append(message)

    def summary(self) -> dict:
        """生成与“最终报告格式”对应的聚合结果。"""
        with self._lock:
            http = list(self.http)
            sse = list(self.sse)
            jobs = list(self.jobs)
            notes = list(self.notes)
            requests = deepcopy(list(self.requests.values()))
            sse_states = deepcopy(self.sse_states)
        observed_until = self.observation_ended_at or self.finished_at or time.time()
        for row in requests:
            if row.get("created_request_at") is not None and row.get("terminal_received_at") is None:
                row["wait_lower_bound_ms"] = max(0, observed_until - row["created_request_at"]) * 1000
        # 交互轮次随每个逻辑请求保存；旧产物没有该字段，报告按未采集处理。
        for row in requests:
            row.setdefault("interactions", None)

        durations = [s.duration_ms for s in http]
        status_counts = Counter(s.status for s in http)
        error_counts = Counter(s.status for s in http if s.status >= 400)
        timeout_count = sum(1 for s in http if s.error)
        by_endpoint: dict[str, dict] = {}
        for sample in http:
            bucket = by_endpoint.setdefault(
                f"{sample.method} {sample.path}",
                {"count": 0, "durations": [], "statuses": Counter(), "errors": 0},
            )
            bucket["count"] += 1
            bucket["durations"].append(sample.duration_ms)
            bucket["statuses"][sample.status] += 1
            if sample.error:
                bucket["errors"] += 1
        endpoint_summary = {
            key: {
                "count": value["count"],
                "p50_ms": percentile(value["durations"], 0.5),
                "p95_ms": percentile(value["durations"], 0.95),
                "p99_ms": percentile(value["durations"], 0.99),
                "status_counts": dict(value["statuses"]),
                "errors": value["errors"],
            }
            for key, value in sorted(by_endpoint.items())
        }

        terminal_counts = Counter(j.final_status or "unfinished" for j in jobs)
        completed = [j for j in jobs if j.observation == "terminal"]
        timed_out = [j for j in jobs if j.observation == "observation_timeout"]
        # 这些兼容字段不能代表全体任务分布；v2 以 capacity 的完成样本分组为准。
        queue_times = [j.queue_seconds for j in completed if j.queue_seconds is not None]
        execution_times = [
            j.execution_seconds for j in completed if j.execution_seconds is not None
        ]
        total_times = [j.total_seconds for j in completed if j.total_seconds is not None]
        timeout_lower_bounds = [
            j.queue_seconds_lower_bound
            for j in timed_out
            if j.queue_seconds_lower_bound is not None
        ]
        history_checked = [j.history_consistent for j in jobs if j.history_consistent is not None]

        first_event = [s.first_event_ms for s in sse if s.first_event_ms is not None]
        terminal_latency = [s.terminal_event_ms for s in sse if s.terminal_event_ms is not None]
        duplicate_events = 0
        out_of_order_events = 0
        id_gap_events = 0
        for sample in sse:
            ids = sample.received_ids
            duplicate_events += len(ids) - len(set(ids))
            for previous, current in zip(ids, ids[1:]):
                if current <= previous:
                    out_of_order_events += 1
                elif current > previous + 1:
                    # analysis_job_events.id 是全局自增主键，跨 Job 事件本就不连续；
                    # 整数缺口只是信息，不构成漏事件证据。
                    id_gap_events += current - previous - 1

        elapsed = max(self.finished_at - self.started_at, 1e-9)
        result = {
            "schema_version": "loadtest.run.v2",
            "requests": requests,
            "scenario": self.scenario,
            "stop_submitting_at": self.stop_submitting_at,
            "observation_ended_at": self.observation_ended_at,
            "db_checked_at": self.db_checked_at,
            "drain": deepcopy(self.drain),
            "run_id": self.run_id,
            "date_dir": self.date_dir,
            "stage": self.stage,
            "users": self.users,
            "peak_active_jobs": self.peak_active_jobs,
            "peak_sse": (sse_states.get("established") or {}).get("peak", 0),
            "peak_sessions_per_user": self.peak_sessions_per_user,
            "peak_active_jobs_per_user": self.peak_active_jobs_per_user,
            "peak_active_jobs_per_account": self.peak_active_jobs_per_account,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(elapsed, 2),
            "http": {
                "requests": len(http),
                "rps": round(len(http) / elapsed, 3),
                "p50_ms": percentile(durations, 0.5),
                "p95_ms": percentile(durations, 0.95),
                "p99_ms": percentile(durations, 0.99),
                "status_counts": dict(status_counts),
                "client_errors_4xx": sum(v for k, v in error_counts.items() if 400 <= k < 500),
                "server_errors_5xx": sum(v for k, v in error_counts.items() if k >= 500),
                "timeouts_or_transport_errors": timeout_count,
                "by_endpoint": endpoint_summary,
                "samples": [asdict(sample) for sample in http],
            },
            "job": {
                "created": self.job_created,
                "created_ids": list(self.job_created_ids),
                "idempotent_replays": self.job_idempotent_replays,
                "recorded": len(jobs),
                "create_attempts": self.job_create_attempts,
                "submissions_over_budget": self.job_submissions_over_budget,
                "submissions_session_busy": self.job_submissions_session_busy,
                "observation_timeouts": len(timed_out),
                "queue_observe_timeouts": sum(1 for j in jobs if j.queue_timeout),
                "completed": len(completed),
                "created_by_user": {
                    str(key): value for key, value in sorted(self.job_created_by_user.items())
                },
                "budget_used_max": max(self.job_created_by_user.values(), default=0),
                "terminal_status_counts": dict(terminal_counts),
                "queue_p50_seconds": percentile(queue_times, 0.5),
                "queue_p95_seconds": percentile(queue_times, 0.95),
                "execution_p50_seconds": percentile(execution_times, 0.5),
                "execution_p95_seconds": percentile(execution_times, 0.95),
                "total_p50_seconds": percentile(total_times, 0.5),
                "total_p95_seconds": percentile(total_times, 0.95),
                "queue_timeout_wait_lower_bound_min_seconds": (
                    min(timeout_lower_bounds) if timeout_lower_bounds else None
                ),
                "queue_timeout_wait_lower_bound_max_seconds": (
                    max(timeout_lower_bounds) if timeout_lower_bounds else None
                ),
                "history_checked": len(history_checked),
                "history_consistent": sum(1 for value in history_checked if value),
                "samples": [asdict(sample) for sample in jobs],
            },
            "sse": {
                "states": sse_states,
                "connection_attempts": (sse_states.get("waiting_headers") or {}).get("entries", 0),
                "error_events": dict(Counter(error for sample in sse for error in sample.error.split(";") if error)),
                "error_events_count": sum(len([e for e in sample.error.split(";") if e]) for sample in sse),
                "subscriptions_with_errors": sum(bool(sample.error) for sample in sse),
                "subscription_error_rate": sum(bool(sample.error) for sample in sse) / len(sse) if sse else None,
                "subscriptions": len(sse),
                "first_event_p50_ms": percentile(first_event, 0.5),
                "first_event_p95_ms": percentile(first_event, 0.95),
                "terminal_latency_p50_ms": percentile(terminal_latency, 0.5),
                "terminal_latency_p95_ms": percentile(terminal_latency, 0.95),
                "disconnects": sum(s.disconnects for s in sse),
                "reconnects": sum(s.reconnects for s in sse),
                "duplicate_events": duplicate_events,
                "out_of_order_events": out_of_order_events,
                "verified_missing_events": None,
                "id_gap_events_informational": id_gap_events,
                "samples": [
                    {
                        "job_id": s.job_id,
                        "first_event_ms": s.first_event_ms,
                        "terminal_event_ms": s.terminal_event_ms,
                        "terminal_event": s.terminal_event,
                        "last_event_id": s.last_event_id,
                        "disconnects": s.disconnects,
                        "reconnects": s.reconnects,
                        "event_count": len(s.received_ids),
                        "error": s.error,
                        "received_ids": s.received_ids,
                    }
                    for s in sse
                ],
            },
            "server_baseline": self.server_baseline,
            "server_after": self.server_after,
            "interaction_schema_version": "loadtest.interaction.v1",
            "notes": notes,
        }
        from runner.capacity import enrich_summary
        return enrich_summary(result)

    def save(self, path: Path) -> Path:
        """把聚合摘要写入指定路径。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.summary(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
