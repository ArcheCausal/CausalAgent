"""分级负载的执行器：虚拟用户线程、健康探测与运行产物落盘。"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

from runner import config
from runner.accounts import Account
from runner.api import UserClient
from runner.flows import StopController, UserContext, VirtualUser
from runner.metrics import HttpSample, Recorder
from runner.manifest import collect_client_manifest


def make_run_id(stage: str) -> str:
    """生成一次运行的标识。"""
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{stage}"


class CountingGate:
    """带并发峰值统计的信号量，用于记录实际活动 Job / SSE 峰值。"""

    def __init__(self, capacity: int) -> None:
        self._semaphore = threading.Semaphore(capacity)
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def __enter__(self) -> "CountingGate":
        self._semaphore.acquire()
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)
        return self

    def __exit__(self, *exc_info) -> None:
        with self._lock:
            self.current -= 1
        self._semaphore.release()

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        acquired = self._semaphore.acquire(blocking, timeout)
        if acquired:
            with self._lock:
                self.current += 1
                self.peak = max(self.peak, self.current)
        return acquired

    def release(self) -> None:
        with self._lock:
            self.current -= 1
        self._semaphore.release()


def run_stage(
    stage: str,
    accounts: list[Account],
    base_url: str,
    server_baseline: dict | None = None,
) -> dict:
    """执行一个档位的负载并返回聚合结果。"""
    spec = config.STAGES[stage]
    users = min(int(spec["users"]), len(accounts))
    recorder = Recorder(run_id=make_run_id(stage))
    recorder.stage = stage
    recorder.users = users
    recorder.server_baseline = server_baseline
    stop = StopController()
    job_gate = CountingGate(int(spec["active_jobs"]))
    sse_gate = CountingGate(int(spec["sse"]))
    recorder.note(f"档位规格: {json.dumps(spec, ensure_ascii=False)}")
    recorder.note(f"客户端清单: {json.dumps(collect_client_manifest(), ensure_ascii=False)}")

    contexts = [
        UserContext(
            index=index + 1,
            username=item.username,
            password=item.password,
            client=UserClient(base_url, recorder, item.username),
        )
        for index, item in enumerate(accounts[:users])
    ]
    for context in contexts:
        context.client.job_gate = job_gate
        context.client.sse_gate = sse_gate

    recorder.started_at = time.time()
    threads = [
        threading.Thread(
            target=_run_user,
            name=f"user-{context.index}",
            args=(context, recorder, job_gate, sse_gate, stop, spec),
            daemon=True,
        )
        for context in contexts
    ]
    monitor = threading.Thread(
        target=_monitor,
        name="health",
        args=(contexts[0], recorder, stop),
        daemon=True,
    )
    for thread in threads:
        thread.start()
    monitor.start()
    for thread in threads:
        thread.join()
    recorder.finished_at = time.time()
    stop.trip("负载结束")
    recorder.peak_active_jobs = job_gate.peak
    recorder.peak_sse = sse_gate.peak
    recorder.note(
        f"配置上限：活动 Job {spec['active_jobs']}，SSE {spec['sse']}；"
        f"实际峰值：活动 Job {job_gate.peak}，SSE {sse_gate.peak}"
    )

    _drain_and_release(contexts, recorder, stop)

    for context in contexts:
        context.client.close()
    if stop.reasons:
        recorder.note("停止条件记录: " + "; ".join(stop.reasons))
    return recorder.summary()


def _run_user(
    context: UserContext,
    recorder: Recorder,
    job_gate: threading.Semaphore,
    sse_gate: threading.Semaphore,
    stop: StopController,
    spec: dict,
) -> None:
    """单个虚拟用户线程的主体。"""
    rng = random.Random(context.index * 7919)
    user = VirtualUser(context, recorder, job_gate, stop, rng)
    try:
        if spec["mode"] == "full_flow":
            user.run_full_flow(int(spec["flows"]))
        else:
            user.run_mixed(float(spec["duration"]))
    except Exception as exc:  # 单个用户失败不应中断整档负载
        recorder.note(f"虚拟用户 {context.username} 异常退出: {type(exc).__name__}: {exc}")


def _monitor(probe: UserContext, recorder: Recorder, stop: StopController) -> None:
    """每 5 秒用一个轻量请求探测网关与主库，记录延迟并判断停止条件。"""
    client = probe.client
    while not stop.triggered:
        started = time.monotonic()
        status = 0
        error = ""
        try:
            response = client.raw_request("GET", "/api/check_auth")
            status = response.status_code
        except Exception as exc:
            error = type(exc).__name__
        duration_ms = (time.monotonic() - started) * 1000
        recorder.record_http(
            HttpSample(
                method="GET",
                path="/health/probe",
                status=status,
                duration_ms=duration_ms,
                error=error,
            )
        )
        if status >= 500:
            stop.trip(f"健康探测出现 {status}")
        time.sleep(5.0)


def _drain_and_release(
    contexts: list[UserContext], recorder: Recorder, stop: StopController
) -> None:
    """停止提交后等待活动 Job 清空，并记录队列恢复时间。"""
    started = time.monotonic()
    deadline = started + 300
    while time.monotonic() < deadline:
        pending = 0
        for context in contexts:
            try:
                pending += len(context.client.active_jobs())
            except Exception:
                continue
        if pending == 0:
            recorder.note(
                f"停止提交后 {time.monotonic() - started:.1f} 秒活动 Job 清空"
            )
            recorder.server_after = {"drain_seconds": round(time.monotonic() - started, 1)}
            return
        time.sleep(5.0)
    recorder.note("停止提交后 5 分钟内活动 Job 未清空")
    stop.trip("停止提交后队列 5 分钟没有减少")
    recorder.server_after = {"drain_seconds": None}


def write_run_outputs(summary: dict, run_output_dir: Path) -> Path:
    """把运行摘要与说明写入 runs/<run_id>/。"""
    run_output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    notes_path = run_output_dir / "notes.md"
    notes_path.write_text(render_notes(summary), encoding="utf-8")
    return summary_path


def render_notes(summary: dict) -> str:
    """把聚合结果整理成可读的运行说明。"""
    http = summary["http"]
    job = summary["job"]
    sse = summary["sse"]
    lines = [
        f"# 运行记录 {summary['run_id']}",
        "",
        f"- 档位: {summary['stage']}",
        f"- 虚拟用户: {summary['users']}",
        f"- 时长: {summary['elapsed_seconds']} 秒",
        "",
        "## HTTP",
        "",
        f"- 请求数: {http['requests']}，RPS: {http['rps']}",
        f"- p50/p95/p99: {_ms(http['p50_ms'])} / {_ms(http['p95_ms'])} / {_ms(http['p99_ms'])}",
        f"- 状态码分布: {json.dumps(http['status_counts'], ensure_ascii=False)}",
        f"- 4xx/5xx/传输错误: {http['client_errors_4xx']} / "
        f"{http['server_errors_5xx']} / {http['timeouts_or_transport_errors']}",
        "",
        "## Job",
        "",
        f"- 创建: {job['created']}，幂等重放: {job['idempotent_replays']}",
        f"- 终态分布: {json.dumps(job['terminal_status_counts'], ensure_ascii=False)}",
        f"- 排队 p50/p95: {_seconds(job.get('queue_p50_seconds'))} / "
        f"{_seconds(job.get('queue_p95_seconds'))}",
        f"- 执行 p50/p95: {_seconds(job.get('execution_p50_seconds'))} / "
        f"{_seconds(job.get('execution_p95_seconds'))}",
        f"- 总耗时 p50/p95: {_seconds(job.get('total_p50_seconds'))} / "
        f"{_seconds(job.get('total_p95_seconds'))}",
        f"- 历史一致: {job['history_consistent']}/{job['history_checked']}",
        "",
        "## SSE",
        "",
        f"- 订阅数: {sse['subscriptions']}",
        f"- 首事件 p50/p95: {_ms(sse['first_event_p50_ms'])} / {_ms(sse['first_event_p95_ms'])}",
        f"- 终态 p50/p95: {_ms(sse['terminal_latency_p50_ms'])} / "
        f"{_ms(sse['terminal_latency_p95_ms'])}",
        f"- 断线/重连: {sse['disconnects']} / {sse['reconnects']}",
        f"- 重复/乱序事件: {sse['duplicate_events']} / {sse['out_of_order_events']}",
        f"- 比对得到的漏事件: {sse['verified_missing_events']}",
        f"- 事件 ID 缺口(信息项): {sse['id_gap_events_informational']}",
        "",
        "## 说明",
        "",
    ]
    lines.extend(f"- {item}" for item in summary["notes"])
    return "\n".join(lines) + "\n"


def _ms(value) -> str:
    """把毫秒值格式化为一位小数；空值返回“未采集”。"""
    if value is None:
        return "未采集"
    return f"{value:.1f} ms"


def _seconds(value) -> str:
    """把秒值格式化为一位小数；空值返回“未采集”。"""
    if value is None:
        return "未采集"
    return f"{value:.1f} s"
