"""分级负载的执行器：虚拟账号线程、后台 SSE 观察器、健康探测与运行产物落盘。"""

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
    """带并发峰值统计的信号量，用于记录实际活动 Job / SSE / 会话峰值。

    capacity 为 None 时表示客户端不设上限：acquire 直接成功，只统计当前数与峰值，
    让服务器自己形成排队。这时 release 必须与 acquire 严格配对，否则 current 会漂移。
    """

    def __init__(self, capacity: int | None) -> None:
        self.capacity = capacity
        self._semaphore = threading.Semaphore(capacity) if capacity is not None else None
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def __enter__(self) -> "CountingGate":
        if not self.acquire():
            raise RuntimeError("CountingGate 无上限时 acquire 不应失败")
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        if self._semaphore is not None:
            acquired = self._semaphore.acquire(blocking, timeout)
            if not acquired:
                return False
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)
        return True

    def release(self) -> None:
        with self._lock:
            self.current = max(0, self.current - 1)
        if self._semaphore is not None:
            self._semaphore.release()


def run_stage(
    stage: str,
    accounts: list[Account],
    base_url: str,
    server_baseline: dict | None = None,
    run_id: str | None = None,
) -> dict:
    """执行一个档位的负载并返回聚合结果。

    run_id 可由编排脚本预先固定，使负载前的服务器采集与本次运行使用同一个标识；
    不传时按开始时间自行生成。
    """
    spec = config.STAGES[stage]
    users = min(int(spec["users"]), len(accounts))
    sessions_per_user = max(1, int(spec.get("sessions_per_user", 1)))
    recorder = Recorder(run_id=run_id or make_run_id(stage))
    recorder.stage = stage
    recorder.users = users
    recorder.server_baseline = server_baseline
    stop = StopController()
    capacity = spec.get("active_jobs")
    job_gate = CountingGate(None if capacity is None else int(capacity))
    sse_gate = CountingGate(int(spec["sse"]))
    recorder.note(f"档位规格: {json.dumps(spec, ensure_ascii=False)}")
    recorder.note(f"客户端清单: {json.dumps(collect_client_manifest(), ensure_ascii=False)}")
    recorder.note(
        f"并发模型：{users} 个逻辑账号 × {sessions_per_user} 个真实会话；"
        f"每个会话最多一个活动 Job，账号级创建额度 "
        f"{spec.get('create_budget', '不限')}"
    )

    contexts = [
        UserContext(
            index=index + 1,
            username=item.username,
            password=item.password,
            client=UserClient(base_url, recorder, item.username),
            budget=(
                None
                if spec.get("create_budget") is None
                else int(spec["create_budget"])
            ),
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
            args=(context, recorder, job_gate, sse_gate, stop, spec, sessions_per_user),
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
    recorder.peak_sessions_per_user = sessions_per_user
    recorder.peak_active_jobs_per_account = max(
        (context.peak_active_jobs for context in contexts), default=0
    )
    recorder.note(
        f"配置上限：活动 Job {capacity if capacity is not None else '不限'}，"
        f"SSE {spec['sse']}；实际峰值：活动 Job {job_gate.peak}，SSE {sse_gate.peak}"
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
    job_gate: CountingGate,
    sse_gate: CountingGate,
    stop: StopController,
    spec: dict,
    sessions_per_user: int,
) -> None:
    """单个虚拟账号线程的主体；创建 Job 后由后台观察器继续等待 SSE。"""
    rng = random.Random(context.index * 7919)
    user = VirtualUser(
        context, recorder, job_gate, stop, rng, sessions_per_user=sessions_per_user
    )
    try:
        if spec["mode"] == "full_flow":
            user.run_full_flow(int(spec["flows"]))
        else:
            user.run_mixed(float(spec["duration"]))
    except Exception as exc:  # 单个账号失败不应中断整档负载
        recorder.note(f"虚拟账号 {context.username} 异常退出: {type(exc).__name__}: {exc}")


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
    """停止提交后等待后台观察器与活动 Job 清空，并记录队列恢复时间。"""
    started = time.monotonic()
    budget = config.DRAIN_DEADLINE_SECONDS
    for context in contexts:
        context_remaining = max(0.0, budget - (time.monotonic() - started))
        for thread in list(context.observers):
            thread.join(timeout=context_remaining)
    observers_alive = sum(
        1 for context in contexts for thread in context.observers if thread.is_alive()
    )
    if observers_alive:
        recorder.note(
            f"排空期限 {budget:.0f} 秒到达时仍有 {observers_alive} 个后台观察器在等待终态"
        )
        stop.trip(f"停止提交后 {budget:.0f} 秒仍有 Job 未到达终态")

    deadline = started + budget
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
    recorder.note(f"停止提交后 {budget:.0f} 秒内活动 Job 未清空")
    stop.trip(f"停止提交后队列 {budget:.0f} 秒没有减少")
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
        f"- 虚拟账号: {summary['users']}",
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
        f"- 创建尝试: {job.get('create_attempts', 0)}，成功创建唯一新 Job: {job['created']}",
        f"- 幂等重放: {job['idempotent_replays']}，超额跳过: {job.get('submissions_over_budget', 0)}，"
        f"会话忙跳过: {job.get('submissions_session_busy', 0)}",
        f"- 每账号成功创建上限使用最多: {job.get('budget_used_max', 0)}",
        f"- 终态分布: {json.dumps(job['terminal_status_counts'], ensure_ascii=False)}",
        f"- 观察超时 Job: {job.get('observation_timeouts', 0)}，"
        f"排队观测超时: {job.get('queue_observe_timeouts', 0)}",
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
