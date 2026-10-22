"""分级负载的执行器：虚拟账号线程、后台 SSE 观察器、健康探测与运行产物落盘。"""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
    if users < 1:
        raise ValueError("档位至少需要一个真实测试账号")
    chosen_run_id = run_id or make_run_id(stage)
    if Path(chosen_run_id).name != chosen_run_id or chosen_run_id in {".", ".."}:
        raise ValueError("run_id 必须是单一目录名")
    # 原始产物按 run_id 日期前缀归档，编排脚本与客户端因此得到同一目录。
    run_parent, _evidence_parent = config.run_dir_for(chosen_run_id)
    output_dir = run_parent / chosen_run_id
    if (output_dir / "jobs.jsonl").exists() or (output_dir / "summary.json").exists():
        raise ValueError("run_id 已有运行产物，请使用新的 run_id")
    recorder = Recorder(
        run_id=chosen_run_id, output_dir=output_dir, date_dir=run_parent.name
    )
    recorder.stage = stage
    recorder.users = users
    recorder.server_baseline = server_baseline
    recorder.scenario = dict(spec)
    if spec["mode"] == "cdfm_rate":
        def start_arrivals():
            recorder.arrival_started_monotonic = time.monotonic()
            recorder.arrival_started_at = time.time()
        recorder.arrival_barrier = threading.Barrier(users, action=start_arrivals)
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
    recorder.stop_submitting_at = time.time()
    recorder.stop_submitting_monotonic = time.monotonic()
    (output_dir / "phases.json").write_text(json.dumps({
        "run_id": recorder.run_id, "started_at": recorder.started_at,
        "stop_submitting_at": recorder.stop_submitting_at,
    }), encoding="utf-8")
    stop.trip("负载结束")
    monitor.join()
    recorder.peak_active_jobs = job_gate.peak
    recorder.peak_sessions_per_user = sessions_per_user
    recorder.peak_active_jobs_per_account = max(
        (context.peak_active_jobs for context in contexts), default=0
    )
    recorder.note(
        f"配置上限：活动 Job {capacity if capacity is not None else '不限'}，"
        f"SSE 客户端额度 {spec['sse']}；客户端活动 Job 峰值 {job_gate.peak}"
    )

    _drain_and_release(contexts, recorder, stop)
    recorder.observation_ended_at = time.time()
    recorder.finished_at = recorder.observation_ended_at
    (output_dir / "phases.json").write_text(json.dumps({
        "run_id": recorder.run_id, "started_at": recorder.started_at,
        "stop_submitting_at": recorder.stop_submitting_at,
        "observation_ended_at": recorder.observation_ended_at,
    }), encoding="utf-8")

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
        elif spec["mode"] == "cdfm_fixed":
            total = int(spec.get("tasks", 4))
            rounds = total // recorder.users + int(context.index <= total % recorder.users)
            user.run_cdfm_fixed(rounds)
        elif spec["mode"] == "cdfm_rate":
            _run_arrivals(user, recorder, stop, spec)
        else:
            user.run_mixed(float(spec["duration"]))
    except Exception as exc:  # 单个账号失败不应中断整档负载
        if spec["mode"] == "cdfm_rate":
            recorder.arrival_barrier.abort()
        recorder.note(f"虚拟账号 {context.username} 异常退出: {type(exc).__name__}: {exc}")


def _monitor(probe: UserContext, recorder: Recorder, stop: StopController) -> None:
    """每 5 秒用一个轻量请求探测网关与主库，记录延迟并判断停止条件。"""
    client = probe.client
    while not stop.triggered:
        started = time.monotonic()
        started_at = time.time()
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
                started_at=started_at,
                finished_at=time.time(),
            )
        )
        if status >= 500:
            stop.trip(f"健康探测出现 {status}")
        for _ in range(50):
            if stop.triggered:
                break
            time.sleep(0.1)


def _run_arrivals(user: VirtualUser, recorder: Recorder, stop: StopController, spec: dict) -> None:
    """按固定节拍安排提交，繁忙客户端拒发的节拍仍进入记录。"""
    rate = float(spec["arrival_rate"])
    if rate <= 0:
        raise ValueError("arrival_rate 必须大于零")
    duration = float(spec["duration"])
    workers = int(spec.get("arrival_workers", 4))
    total = int(spec["tasks"])
    plan = [(str(uuid.uuid4()), ordinal)
            for ordinal in range(user.ctx.index - 1, total, recorder.users)]
    # 准备失败也必须保留完整分母，逻辑身份先于登录及 barrier 登记。
    for logical, ordinal in plan:
        recorder.register_request(logical, username=user.ctx.username,
            user_index=user.ctx.index, scenario="cdfm_rate", arrival_ordinal=ordinal,
            scheduled_at=None, admission="pending")
    tick = 0
    started_at = None
    prepared = False
    reason = "submission_stopped"
    try:
        user.prepare()
        prepared = True
        recorder.arrival_barrier.wait(timeout=config.HTTP_TIMEOUT_SECONDS * 3)
        started = recorder.arrival_started_monotonic
        started_at = recorder.arrival_started_at
        deadline = started + duration
        user.set_submission_deadline(deadline)
        for logical, ordinal in plan:
            recorder.lifecycle(logical, event="arrival_scheduled",
                scheduled_at=started_at + ordinal / rate,
                submission_deadline_at=started_at + duration)
        pending = set()
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"arrival-{user.ctx.index}") as pool:
            while tick < len(plan) and not stop.triggered:
                logical, ordinal = plan[tick]
                now = time.monotonic()
                if now >= deadline or ordinal / rate >= duration:
                    reason = "outside_load_duration"
                    break
                scheduled = started + ordinal / rate
                scheduled_at = started_at + ordinal / rate
                remaining = scheduled - now
                if remaining > 0:
                    time.sleep(min(remaining, .1, deadline - now))
                    continue
                pending = {f for f in pending if not f.done()}
                if len(pending) >= workers:
                    _arrival_not_sent(recorder, logical, "client_submit_capacity", scheduled_at)
                else:
                    pending.add(pool.submit(_arrival, user, recorder, logical,
                                            scheduled, scheduled_at, deadline))
                tick += 1
            for future in pending:
                future.result()
    except Exception as exc:
        recorder.arrival_barrier.abort()
        reason = ("arrival_barrier_failed" if isinstance(exc, threading.BrokenBarrierError)
                  else "arrival_preparation_failed" if not prepared
                  else "arrival_scheduler_failed")
        raise
    finally:
        for logical, ordinal in plan[tick:]:
            scheduled_at = None if started_at is None else started_at + ordinal / rate
            _arrival_not_sent(recorder, logical, reason, scheduled_at)


def _arrival_not_sent(recorder: Recorder, logical: str, reason: str,
                      scheduled_at: float | None) -> None:
    recorder.lifecycle(logical, event="not_sent", admission="not_sent",
        reason=reason, not_sent_reason=reason, scheduled_at=scheduled_at,
        scheduler_lag_ms=(None if scheduled_at is None else
                          max(0, time.time() - scheduled_at) * 1000))


def _arrival(user: VirtualUser, recorder: Recorder, logical: str,
             scheduled: float, scheduled_at: float, deadline: float) -> None:
    try:
        lag = max(0, time.monotonic() - scheduled)
        user.submit_arrival(logical_request_id=logical, scheduled_at=scheduled_at,
                            scheduler_lag_ms=lag * 1000, deadline=deadline)
    except Exception as exc:
        row = recorder.requests[logical]
        if row.get("admission") == "pending" and row.get("post_started_at") is None:
            _arrival_not_sent(recorder, logical, "arrival_dispatch_failed", scheduled_at)
        recorder.lifecycle(logical, event="arrival_dispatch_error",
                            dispatch_error=type(exc).__name__)
        recorder.note(f"CDFM 到达提交失败：{type(exc).__name__}")


def _drain_and_release(
    contexts: list[UserContext], recorder: Recorder, stop: StopController
) -> None:
    """停止提交后等待后台观察器与活动 Job 清空，并记录队列恢复时间。"""
    started = getattr(recorder, "stop_submitting_monotonic", time.monotonic())
    budget = config.DRAIN_DEADLINE_SECONDS
    checkpoint = config.DRAIN_CHECKPOINT_SECONDS
    deadline = started + budget
    recorder.observation_deadline = min(recorder.observation_deadline, deadline)
    checkpoint_result = None
    state = "unknown"
    drained_at = None
    while time.monotonic() < deadline:
        pending = 0
        failures = 0
        unresolved = any(r.get("admission") in {"unknown", "pending", "acceptance_unknown"}
                         for r in recorder.requests.values())
        for context in contexts:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failures += 1
                break
            try:
                # active API 包含账号历史会话；只将本轮已登记 Job 计入。
                pending += sum(str(job.get("job_id")) in recorder.job_created_ids
                               for job in context.client.active_jobs(
                                   timeout=min(config.HTTP_TIMEOUT_SECONDS, remaining)))
            except Exception as exc:
                failures += 1
                recorder.note(f"排空状态查询未知：{context.username}/{type(exc).__name__}")
        alive = [t for c in contexts for t in c.observers if t.is_alive()]
        state = "unknown" if failures or unresolved else ("active" if pending else "empty")
        elapsed = time.monotonic() - started
        if checkpoint_result is None and elapsed >= checkpoint:
            checkpoint_result = {"at": time.time(), "state": state,
                                 "observed_ms": elapsed * 1000,
                                 "observers_alive": len(alive), "drained": None,
                                 "client_cleared_within_checkpoint": False}
        if state == "empty" and not alive and elapsed <= budget:
            drained_at = elapsed * 1000
            break
        # 每次 join 都重新计算预算，轮询不得重复消费整段剩余期限。
        for thread in alive:
            thread.join(timeout=max(0, min(.05, deadline - time.monotonic())))
        recorder.observation_stop.wait(min(1, max(0, deadline - time.monotonic())))
    recorder.observation_stop.set()
    # 停止观察不是取消服务端 Job。等待所有写入者退出后才冻结证据。
    for context in contexts:
        for thread in list(context.observers):
            thread.join()
    elapsed_ms = (time.monotonic() - started) * 1000
    if checkpoint_result is None and drained_at is not None and drained_at <= checkpoint * 1000:
        checkpoint_result = {"state": "empty", "observers_alive": 0, "drained": None,
                             "client_cleared_within_checkpoint": True, "observed_drain_ms": drained_at}
    recorder.drain = {"checkpoint_ms": checkpoint * 1000, "checkpoint": checkpoint_result,
                      "observation_limit_ms": budget * 1000, "final_state": state,
                      "client_observers_cleared": True,
                      "business_active_cleared": state == "empty" if state != "unknown" else None,
                      "client_cleared_within_deadline": drained_at is not None,
                      "client_drain_ms": drained_at, "execution_released": None,
                      "finally_drained": None, "drain_ms": None,
                      "observed_ms": elapsed_ms}
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
    """运行说明与正式报告使用相同的 v2 口径和旧产物兼容规则。"""
    from runner.report import render_stage_report

    return render_stage_report(summary)
