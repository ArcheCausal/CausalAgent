"""虚拟用户的业务场景：多会话准备、提交与观察分离、账号级额度与结果校验。

模型结构（每个 account 一行，Session 与 Job 逐层展开）：

    UserAccount
    ├── Session A ── Job A ── SSE observer
    └── Session B ── Job B ── SSE observer

同一账号的不同会话允许同时运行任务，由服务端接口决定；同一会话仍然遵守应用现有的活动
Job 约束，因此每个会话最多一个正在运行或等待的 Job。账号级 create_budget 由线程安全计数器
控制；接纳未知的请求保留额度与会话锁，避免重复提交。
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

from runner import config, interaction
from runner.api import ApiError, UserClient, new_idempotency_key
from runner.metrics import JobSample, Recorder

MESSAGE = "请分析这些变量之间的因果关系，并给出结论摘要。"


def build_csv(account_index: int, rows: int = 40) -> bytes:
    """生成一个体积很小、内容确定的测试 CSV。"""
    rng = random.Random(1000 + account_index)
    lines = ["age,income,education_years,exercise_minutes,blood_pressure"]
    for _ in range(rows):
        age = rng.randint(22, 65)
        education = rng.randint(9, 22)
        exercise = rng.randint(0, 90)
        income = int(2000 + education * 480 + rng.gauss(0, 300))
        pressure = int(140 - exercise * 0.2 + age * 0.4 + rng.gauss(0, 5))
        lines.append(f"{age},{max(income, 900)},{education},{exercise},{pressure}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_cdfm_csv(rows: int, seed: int) -> bytes:
    """生成固定种子、固定行数的连续数值工作负载，所有账号使用相同内容。"""
    if rows <= 0:
        raise ValueError("CDFM rows 必须大于零")
    rng = random.Random(seed)
    lines = ["x1,x2,x3,x4,x5"]
    for _ in range(rows):
        x1, x2 = rng.gauss(0, 1), rng.gauss(0, 1)
        x3 = 0.8 * x1 + rng.gauss(0, 0.5)
        x4 = -0.6 * x2 + 0.5 * x3 + rng.gauss(0, 0.5)
        x5 = 0.7 * x4 + rng.gauss(0, 0.5)
        lines.append(",".join(f"{value:.8f}" for value in (x1, x2, x3, x4, x5)))
    return ("\n".join(lines) + "\n").encode("utf-8")


@dataclass
class StopController:
    """停止条件：一旦触发，就不再创建新 Job 或新增虚拟用户。"""

    triggered: bool = False
    reasons: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def trip(self, reason: str) -> None:
        with self._lock:
            if not self.triggered:
                self.triggered = True
            if reason not in self.reasons:
                self.reasons.append(reason)

    def check_http(self, path: str, status: int, duration_ms: float) -> None:
        if status >= 500:
            self.trip(f"普通业务请求出现 {status}: {path}")
        if path.endswith("/cancel") and duration_ms > 10_000:
            self.trip(f"取消请求超过 10 秒: {duration_ms:.0f} ms")
        if path.endswith("/events") and duration_ms > 10_000:
            self.trip(f"事件订阅请求超过 10 秒: {duration_ms:.0f} ms")

    def check_sse(self, missing: int, duplicates: int, out_of_order: int) -> None:
        """只在出现重复或乱序事件时触发停止条件。

        analysis_job_events.id 是全局自增主键，同一 Job 的事件 ID 本就不连续，
        因此不能用整数缺口判断漏事件。
        """
        if missing > 0:
            self.trip(f"SSE 漏事件 {missing} 个")
        if duplicates > 0:
            self.trip(f"SSE 重复事件 {duplicates} 个")
        if out_of_order > 0:
            self.trip(f"SSE 乱序事件 {out_of_order} 个")


@dataclass
class SessionSlot:
    """一个账号下的一条真实会话；同一会话最多承载一个未完成 Job。"""

    index: int
    session_id: str
    file_id: int | None = None
    current_job_id: str | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    logical_request_id: str | None = None
    idempotency_key: str | None = None
    admission: str = "pending"


@dataclass
class UserContext:
    """一个虚拟账号在运行期间持有的状态与预算。"""

    index: int
    username: str
    password: str
    client: UserClient
    sessions: list[SessionSlot] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    budget: int | None = None
    budget_lock: threading.Lock = field(default_factory=threading.Lock)
    budget_used: int = 0
    observers: list[threading.Thread] = field(default_factory=list)
    peak_active_jobs: int = 0
    active_jobs_now: int = 0
    active_lock: threading.Lock = field(default_factory=threading.Lock)

    def occupy_budget(self) -> bool:
        """占用一个账号级创建额度；额度用尽时返回 False。"""
        with self.budget_lock:
            if self.budget is not None and self.budget_used >= self.budget:
                return False
            self.budget_used += 1
            return True

    def release_budget(self) -> None:
        """创建最终没有拿到唯一新 Job 时退还额度。"""
        with self.budget_lock:
            self.budget_used = max(0, self.budget_used - 1)

    def enter_job(self) -> None:
        with self.active_lock:
            self.active_jobs_now += 1
            self.peak_active_jobs = max(self.peak_active_jobs, self.active_jobs_now)

    def leave_job(self) -> None:
        with self.active_lock:
            self.active_jobs_now = max(0, self.active_jobs_now - 1)


class VirtualUser:
    """一个真实账号的任务生命周期；到达调度与 SSE 观察分离。"""

    def __init__(self, context: UserContext, recorder: Recorder, job_gate,
                 stop: StopController, rng: random.Random, sessions_per_user: int = 1) -> None:
        self.ctx, self.recorder = context, recorder
        self.job_gate, self.stop, self.rng = job_gate, stop, rng
        self.sessions_per_user = max(1, int(sessions_per_user))
        self.spec = dict(recorder.scenario or config.STAGES.get(recorder.stage, {}))
        self.scenario = str(self.spec.get("mode") or recorder.stage or "mixed")
        self.csv = (build_cdfm_csv(int(self.spec.get("rows", config.CDFM_ROWS)),
                                   int(self.spec.get("seed", config.CDFM_SEED)))
                    if self.scenario.startswith("cdfm") else build_csv(context.index))
        self.message = (str(self.spec.get("message") or "")
                        or (config.CDFM_MESSAGE if self.scenario.startswith("cdfm") else MESSAGE))
        self.profile = interaction.profile_from_spec(self.spec)
        self._ledgers: dict[str, interaction.InteractionLedger] = {}
        self.sse_gate = context.client.sse_gate
        self._session_lock = threading.Lock()
        self._prepare_lock = threading.Lock()
        self._prepared = False
        self._load_deadline = float("inf")
        # 限制等待 SSE 的后台线程数；达到上限的到达明确记录为 not_sent。
        self._arrival_slots = threading.BoundedSemaphore(
            max(1, int(self.spec.get("arrival_pending", config.ARRIVAL_PENDING))))

    def _can_submit(self) -> bool:
        return (not self.stop.triggered and not self.recorder.observation_stop.is_set()
                and time.monotonic() < min(self._load_deadline, self.recorder.observation_deadline))

    def set_submission_deadline(self, deadline: float) -> None:
        """设置绝对 monotonic 提交截止；已有截止只能缩短。"""
        with self._session_lock:
            self._load_deadline = min(self._load_deadline, deadline)

    def _new_session(self) -> SessionSlot:
        session_id = self.ctx.client.new_chat()
        with self._session_lock:
            slot = SessionSlot(index=len(self.ctx.sessions), session_id=session_id)
            self.ctx.sessions.append(slot)
        slot.file_id = self.ctx.client.upload_csv(
            f"loadtest_{self.ctx.index:02d}_{slot.index:04d}.csv", self.csv)
        slot.ready.set()
        return slot

    def prepare(self) -> None:
        """一次登录并准备会话；可由调度器重复调用。"""
        with self._prepare_lock:
            if self._prepared:
                return
            self.ctx.client.login(self.ctx.password)
            # 速率场景为每次到达创建独立会话，不提前制造闲置会话。
            if self.scenario != "cdfm_rate":
                for _ in range(self.sessions_per_user):
                    self._new_session()
            self._prepared = True

    def run_full_flow(self, rounds: int) -> None:
        self.prepare()
        for index in range(rounds):
            if not self._can_submit():
                return
            slot = self._new_session() if index else self.ctx.sessions[0]
            self._run_blocking_job_cycle(slot)
            if self._can_submit():
                self._read_queries()
                self._think()

    def run_cdfm_fixed(self, rounds: int) -> None:
        """顺序完成分配给本账号的固定数量 CDFM 任务，不执行混合读写。"""
        if rounds < 0:
            raise ValueError("rounds 不得小于零")
        if rounds == 0:
            return
        try:
            self.prepare()
        except Exception:
            for _ in range(rounds):
                self._not_sent(new_idempotency_key(), "fixed_preparation_failed")
            raise
        for index in range(rounds):
            if not self._can_submit():
                for _ in range(index, rounds):
                    self._not_sent(new_idempotency_key(), "submission_stopped")
                return
            logical = new_idempotency_key()
            try:
                slot = self._new_session() if index else self.ctx.sessions[0]
                self._run_blocking_job_cycle(slot, logical_request_id=logical)
            except Exception:
                if logical not in self.recorder.requests:
                    self._not_sent(logical, "fixed_preparation_failed")
                for _ in range(index + 1, rounds):
                    self._not_sent(new_idempotency_key(), "fixed_preparation_failed")
                raise

    def submit_arrival(self, *, logical_request_id: str | None = None,
                       scheduled_at: float | None = None,
                       created_request_at: float | None = None,
                       scheduler_lag_ms: float | None = None,
                       deadline: float | None = None) -> str | None:
        """调度线程内创建真实会话、上传固定数据并 POST，后台继续观察。

        scheduled_at/created_request_at 均为 epoch；scheduler_lag_ms 在真正 POST
        时重新计算，传入的调度延迟另存 dispatch_lag_ms；deadline 为绝对 monotonic。
        """
        if deadline is not None:
            self.set_submission_deadline(deadline)
        logical = logical_request_id or new_idempotency_key()
        if not self._can_submit():
            self._not_sent(logical, "submission_stopped", scheduled_at=scheduled_at)
            return None
        if not self._arrival_slots.acquire(blocking=False):
            self._not_sent(logical, "client_observer_capacity", scheduled_at=scheduled_at)
            return None
        observing = False
        try:
            self.prepare()
            if not self._can_submit():
                self._not_sent(logical, "submission_stopped", scheduled_at=scheduled_at)
                return None
            slot = self._new_session()
            job_id = self._submit_job(slot=slot, logical_request_id=logical,
                                      scheduled_at=scheduled_at,
                                      created_request_at=created_request_at,
                                      dispatch_lag_ms=scheduler_lag_ms, arrival=True)
            observing = job_id is not None
            return job_id
        except Exception as exc:
            row = self.recorder.requests.get(logical, {})
            if row.get("admission") in ("accepted", "unknown", "pending"):
                self.recorder.lifecycle(logical, event="arrival_error",
                                        arrival_error=type(exc).__name__)
            else:
                self._not_sent(logical, "arrival_preparation_error", scheduled_at=scheduled_at,
                               preparation_error=type(exc).__name__)
            raise
        finally:
            if not observing:
                self._arrival_slots.release()

    def run_mixed(self, duration_seconds: float) -> None:
        self._load_deadline = time.monotonic() + duration_seconds
        self.prepare()
        while self._can_submit():
            if not self._has_budget():
                self._think()
                continue
            operation = self._pick_operation()
            try:
                if operation == "read":
                    self._read_queries()
                elif operation == "page":
                    self._page_requests()
                elif operation == "upload":
                    if self._can_submit():
                        self.ctx.client.upload_csv(f"loadtest_{self.ctx.index:02d}.csv", self.csv)
                else:
                    self._submit_job()
            except ApiError as exc:
                self.recorder.note(f"虚拟用户 {self.ctx.username} 操作失败: {type(exc).__name__}")
            self._think()

    def wait_observers(self, deadline_seconds: float) -> None:
        deadline = time.monotonic() + deadline_seconds
        with self._session_lock:
            threads = list(self.ctx.observers)
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))

    def _pick_operation(self) -> str:
        roll, cumulative = self.rng.random(), 0.0
        for name in ("read", "page", "upload", "create_job"):
            cumulative += config.MIX_WEIGHTS[name]
            if roll <= cumulative:
                return name
        return "read"

    def _has_budget(self) -> bool:
        with self.ctx.budget_lock:
            return self.ctx.budget is None or self.ctx.budget_used < self.ctx.budget

    def _pick_session(self) -> SessionSlot | None:
        deadline = min(self._load_deadline, time.monotonic() + config.SESSION_SLOT_WAIT_SECONDS)
        while self._can_submit() and time.monotonic() < deadline:
            with self._session_lock:
                for slot in self.ctx.sessions:
                    if slot.ready.is_set():
                        slot.ready.clear()
                        return slot
            self.recorder.observation_stop.wait(min(0.05, max(0, deadline - time.monotonic())))
        return None

    def _read_queries(self) -> None:
        client = self.ctx.client
        with self._session_lock:
            slots = list(self.ctx.sessions)
        for call in [client.sessions, *[lambda s=s: client.load_session(s.session_id)
                                       for s in slots], client.list_files, client.active_jobs]:
            if not self._can_submit():
                return
            call()

    def _page_requests(self) -> None:
        for call in (self.ctx.client.site_page, self.ctx.client.dashboard_page,
                     self.ctx.client.check_auth):
            if not self._can_submit():
                return
            call()

    def _register(self, logical: str, **fields) -> None:
        # stages 只登记未发出的 tick；也允许调用方预先登记传入的同一逻辑身份。
        if logical in self.recorder.requests:
            self.recorder.lifecycle(logical, event="submission_prepared", **fields)
        else:
            self.recorder.register_request(logical, username=self.ctx.username,
                                           user_index=self.ctx.index, scenario=self.scenario,
                                           group="ordinary",
                                           subscription_started_at=None, quota_acquired_at=None,
                                           quota_wait_ms=None, sse_headers_at=None,
                                           sse_headers_status=None, first_event_at=None,
                                           terminal_received_at=None, user_wait_ms=None,
                                           history_check_ms=None, received_event_ids=[],
                                           cdfm_observed=None, cdfm_status=None, **fields)

    def _not_sent(self, logical: str, reason: str, **fields) -> None:
        scheduled = fields.get("scheduled_at")
        self._register(logical, session_id=None, idempotency_key=None,
                       created_request_at=None)
        self.recorder.lifecycle(logical, event="not_sent", admission="not_sent",
                                not_sent_reason=reason,
                                scheduler_lag_ms=(max(0, time.time() - scheduled) * 1000
                                                  if scheduled is not None else None), **fields)

    def _submit_job(self, *, slot: SessionSlot | None = None,
                    logical_request_id: str | None = None, arrival: bool = False,
                    **fields) -> str | None:
        logical = logical_request_id or new_idempotency_key()
        if not self.ctx.occupy_budget():
            self.recorder.bump("job_submissions_over_budget")
            self._not_sent(logical, "create_budget", **fields)
            return None
        gate_acquired = accepted = False
        try:
            if slot is None:
                slot = self._pick_session()
            else:
                with self._session_lock:
                    if not slot.ready.is_set():
                        self._not_sent(logical, "session_locked", **fields)
                        return None
                    slot.ready.clear()
            if slot is None:
                self.recorder.bump("job_submissions_session_busy")
                self._not_sent(logical, "session_busy_or_deadline", **fields)
                return None
            if not self._can_submit() or not self._acquire_job_slot():
                slot.ready.set()
                self._not_sent(logical, "job_quota_or_deadline", **fields)
                return None
            gate_acquired = True
            job_id = self._create_job(slot, logical, arrival=arrival, **fields)
            accepted = job_id is not None
            return job_id
        finally:
            if not accepted:
                if gate_acquired:
                    self._release_job_slot()
                if slot is None or slot.admission != "unknown":
                    self.ctx.release_budget()
                    if slot is not None and slot.current_job_id is None:
                        slot.ready.set()

    def _acquire_job_slot(self) -> bool:
        if self.job_gate is None:
            return self._can_submit()
        deadline = min(self._load_deadline, time.monotonic() + config.JOB_SLOT_WAIT_SECONDS)
        while self._can_submit() and time.monotonic() < deadline:
            if self.job_gate.acquire(timeout=min(0.05, max(0, deadline - time.monotonic()))):
                return True
        return False

    def _release_job_slot(self) -> None:
        if self.job_gate is not None:
            self.job_gate.release()

    def _create_job(self, slot: SessionSlot, logical: str, *, arrival: bool = False,
                    scheduled_at: float | None = None, created_request_at: float | None = None,
                    dispatch_lag_ms: float | None = None) -> str | None:
        client = self.ctx.client
        key = new_idempotency_key()
        started, started_at = time.monotonic(), time.time()
        slot.logical_request_id, slot.idempotency_key = logical, key
        self._register(logical, session_id=slot.session_id, idempotency_key=key,
                       created_request_at=started_at, scheduled_at=scheduled_at,
                       supplied_created_request_at=created_request_at,
                       dispatch_lag_ms=dispatch_lag_ms,
                       scheduler_lag_ms=(max(0, started_at - scheduled_at) * 1000
                                         if scheduled_at is not None else None))
        errors, uncertain = [], False
        status, payload = 0, {}
        self.recorder.bump("job_create_attempts")
        for attempt in range(1, config.CREATE_MAX_ATTEMPTS + 1):
            if not self._can_submit():
                break
            # POST 一旦发出，异常返回前不能证明事务未提交。
            slot.admission = "unknown"
            self.recorder.lifecycle(logical, event="create_post_started",
                                    post_started_at=time.time(), create_attempt=attempt,
                                    create_transport_retries=attempt - 1,
                                    admission="unknown")
            try:
                remaining = min(self._load_deadline, self.recorder.observation_deadline) - time.monotonic()
                if remaining <= 0 or not self._can_submit():
                    break
                status, payload = client.create_job(slot.session_id, self.message, slot.file_id, key,
                    timeout=min(config.HTTP_TIMEOUT_SECONDS, remaining))
                self.recorder.lifecycle(logical, event="create_response",
                                        create_response_at=time.time(),
                                        created_status=status, create_attempt=attempt)
                if status in (200, 202) and payload.get("job_id"):
                    break
                errors.append({"attempt": attempt, "status": status,
                               "code": payload.get("code")})
                uncertain = uncertain or status >= 500 or status in (200, 202)
                self.recorder.lifecycle(logical, event="create_error", create_errors=list(errors))
                if status < 500 and status not in (200, 202):
                    break
            except ApiError as exc:
                uncertain = True
                errors.append({"attempt": attempt, "error": str(exc)})
                self.recorder.lifecycle(logical, event="create_error", create_errors=list(errors))
            if attempt < config.CREATE_MAX_ATTEMPTS:
                self.recorder.lifecycle(logical, event="create_retry")
                self._pause(0.5)
        if status not in (200, 202) or not payload.get("job_id"):
            admission = ("unknown" if uncertain else
                         "conflict" if status == 409 else
                         "rejected" if status else "not_sent")
            slot.admission = admission
            self.recorder.lifecycle(logical, event="admission", admission=admission,
                                    created_status=status, create_errors=errors,
                                    not_sent_reason="submission_deadline" if admission == "not_sent" else None)
            if admission != "unknown":
                slot.ready.set()
            return None
        job_id = str(payload["job_id"])
        slot.current_job_id, slot.admission = job_id, "accepted"
        client.bind_job_request(job_id, logical, started)
        self.recorder.lifecycle(logical, job_id=job_id, event="accepted", admission="accepted",
                                accepted_at=time.time(), created_status=status, create_errors=errors,
                                existing=bool(payload.get("existing")))
        if payload.get("existing"):
            self.recorder.bump("job_idempotent_replays")
        if self.recorder.record_created_job(job_id):
            self.recorder.bump("job_created")
            self.recorder.bump_user("job_created_by_user", self.ctx.index)
            with self._session_lock:
                self.ctx.job_ids.append(job_id)
        self.ctx.enter_job()
        thread = threading.Thread(target=self._observe,
            name=f"obs-{self.ctx.index}-{slot.index}-{job_id[:8]}",
            args=(slot, job_id, started, status, arrival), daemon=True)
        with self._session_lock:
            self.ctx.observers.append(thread)
        thread.start()
        return job_id

    def _observe(self, slot: SessionSlot, job_id: str, created_at: float,
                 created_status: int = 202, arrival: bool = False) -> None:
        deadline = created_at + config.JOB_OBSERVE_DEADLINE_SECONDS
        terminal = terminal_at = None
        history_ok = None
        ids, errors = [], []
        cursor, group = 0, "ordinary"
        final_status, observation = "unfinished", "observation_timeout"
        elapsed = None
        resources_released = False
        try:
            while not self.recorder.observation_stop.is_set() and time.monotonic() < min(
                    deadline, self.recorder.observation_deadline):
                outcome = self.ctx.client.subscribe_job_events(
                    job_id, after_event_id=cursor, deadline=deadline,
                    stop_event=self.recorder.observation_stop)
                ids.extend(outcome.sample.received_ids)
                cursor = max(cursor, outcome.sample.last_event_id)
                errors.extend(outcome.errors)
                self.recorder.lifecycle(job_id=job_id, event="events_received",
                                        received_event_ids=list(ids), sse_errors=list(errors))
                terminal, terminal_at = outcome.terminal_event, outcome.terminal_received_at
                if terminal is not None:
                    final_status, observation = _final_status(terminal, ""), "terminal"
                    break
                if outcome.interrupt_payload is None:
                    break
                group = "waiting_input"
                self.recorder.lifecycle(job_id=job_id, event="waiting_input", group=group,
                                        interrupted_at=time.time(), waiting_input=True)
                if not self._answer_interrupt(job_id, outcome.interrupt_payload, deadline):
                    final_status = "waiting_input"
                    # 恢复失败或接纳未知时继续从 interrupt 后订阅，等待服务端终态，
                    # 不把等待输入当成完成，也不重放同一个问题。
                    continue
                group = "resumed"
                self.recorder.lifecycle(job_id=job_id, event="resumed", group=group,
                                        resumed=True, waiting_input=False)
            elapsed = (time.monotonic() - created_at) * 1000
            self.stop.check_sse(0, _duplicates(ids), _out_of_order(ids))
            if terminal is not None:
                with self._session_lock:
                    slot.current_job_id = None
                    slot.ready.set()
                self.ctx.leave_job()
                self._release_job_slot()
                resources_released = True
                # 用户等待已经在终态抵达时记录；历史核对不进入用户耗时。
                history_started = time.monotonic()
                try:
                    history_ok = self._verify_history(slot.session_id, job_id)
                    self.recorder.lifecycle(job_id=job_id, event="history_checked",
                                            history_consistent=history_ok, history_checked_at=time.time(),
                                            history_check_ms=(time.monotonic() - history_started) * 1000)
                except ApiError as exc:
                    self.recorder.lifecycle(job_id=job_id, event="history_error",
                                            history_consistent=None, history_error=str(exc),
                                            history_check_ms=(time.monotonic() - history_started) * 1000)
        except Exception as exc:
            errors.append(type(exc).__name__)
            self.recorder.lifecycle(job_id=job_id, event="observer_error", observer_error=type(exc).__name__)
        finally:
            elapsed = elapsed if elapsed is not None else (time.monotonic() - created_at) * 1000
            if terminal is None:
                self.recorder.bump("job_observation_timeouts")
            self.recorder.lifecycle(job_id=job_id, event="observation_finished",
                                    final_status=final_status, observation=observation,
                                    observed_wait_ms=elapsed, received_event_ids=list(ids),
                                    sse_errors=errors, observation_finished_at=time.time())
            self.recorder.record_job(JobSample(
                job_id=job_id, created_status=created_status, final_status=final_status,
                queue_seconds=None, execution_seconds=None, total_seconds=elapsed / 1000,
                history_consistent=history_ok, user_index=self.ctx.index, session_index=slot.index,
                observation=observation, note=";".join(errors)))
            if not resources_released:
                if terminal is not None:
                    with self._session_lock:
                        slot.current_job_id = None
                        slot.ready.set()
                    self.ctx.leave_job()
                self._release_job_slot()
            if arrival:
                self._arrival_slots.release()

    def _run_blocking_job_cycle(self, slot: SessionSlot | None = None,
                                logical_request_id: str | None = None) -> str | None:
        job_id = self._submit_job(slot=slot or self.ctx.sessions[0],
                                  logical_request_id=logical_request_id)
        if job_id is not None:
            with self._session_lock:
                observer = self.ctx.observers[-1]
            observer.join()
        return job_id

    def _answer_interrupt(self, job_id: str, payload: dict, deadline: float) -> bool:
        """按交互类型答复；未识别或超出场景约束时记录缺口，不发送默认答复。"""
        ledger = self._ledgers.setdefault(job_id, interaction.InteractionLedger(
            max_rounds=self.profile.max_rounds,
            max_interaction_seconds=self.profile.max_interaction_seconds))
        decision = interaction.decide(payload, job_id, self.profile, ledger)
        self.recorder.lifecycle(job_id=job_id, event="interaction_decided",
                                interaction_round=len(ledger.rounds),
                                interactions=list(ledger.rounds), **decision.record())
        answered_started = time.monotonic()
        if not decision.sendable:
            # 缺口写入运行说明，供报告区分“未识别”和“已经答复”。
            self.recorder.note(
                f"交互未答复：job={job_id} type={decision.interaction_type or 'unknown'} "
                f"status={decision.status} detail={decision.detail or '-'}")
            return False
        key, errors = decision.key, []
        for attempt in range(1, config.CREATE_MAX_ATTEMPTS + 1):
            if self.recorder.observation_stop.is_set() or time.monotonic() >= min(
                    deadline, self.recorder.observation_deadline):
                return False
            try:
                remaining = min(deadline, self.recorder.observation_deadline) - time.monotonic()
                status, body = self.ctx.client.resume_job(
                    job_id, decision.question_id, decision.answer, key,
                    timeout=max(0.001, min(config.HTTP_TIMEOUT_SECONDS, remaining)))
                if status in (200, 202):
                    self.recorder.lifecycle(job_id=job_id, event="resume_accepted",
                                            resume_errors=errors, resume_idempotency_key=key,
                                            interaction_type=decision.interaction_type or None,
                                            plan_version=decision.plan_version,
                                            interactions=list(ledger.rounds),
                                            interaction_pause_ms=(time.monotonic() - answered_started) * 1000)
                    return True
                errors.append({"attempt": attempt, "status": status, "code": body.get("code")})
                if status < 500:
                    self.recorder.lifecycle(job_id=job_id, event="resume_error", resume_errors=errors)
                    return False
            except ApiError as exc:
                errors.append({"attempt": attempt, "error": str(exc)})
            self.recorder.lifecycle(job_id=job_id, event="resume_error", resume_errors=list(errors))
            self.recorder.observation_stop.wait(0.5)
        return False

    def _verify_history(self, session_id: str, job_id: str) -> bool:
        messages = self.ctx.client.load_session(session_id).get("messages") or []
        for message in messages:
            if str(message.get("analysis_job_id")) == job_id and message.get("sender") == "ai":
                text = message.get("text")
                if isinstance(text, dict) and text or isinstance(text, str) and text.strip():
                    return True
        return False

    def _pause(self, seconds: float) -> None:
        deadline = min(self._load_deadline, time.monotonic() + seconds)
        while self._can_submit() and time.monotonic() < deadline:
            self.recorder.observation_stop.wait(min(0.05, max(0, deadline - time.monotonic())))

    def _think(self) -> None:
        self._pause(self.rng.uniform(config.THINK_MIN_SECONDS, config.THINK_MAX_SECONDS))


def _out_of_order(ids: list[int]) -> int:
    """返回非严格递增的事件次数；这是唯一与漏事件相关的客户端缺陷信号。"""
    return sum(1 for previous, current in zip(ids, ids[1:]) if current <= previous)


def _duplicates(ids: list[int]) -> int:
    return len(ids) - len(set(ids))


def _final_status(terminal_event: str | None, error: str) -> str:
    if terminal_event == "final_result":
        return "succeeded"
    if terminal_event == "canceled":
        return "canceled"
    if terminal_event == "error":
        return "failed"
    if terminal_event == "interrupt":
        return "waiting_input"
    if error:
        return f"unfinished:{error}"
    return "unfinished"
