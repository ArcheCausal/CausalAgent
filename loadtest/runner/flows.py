"""虚拟用户的业务场景：多会话准备、提交与观察分离、账号级额度与结果校验。

模型结构（每个 account 一行，Session 与 Job 逐层展开）：

    UserAccount
    ├── Session A ── Job A ── SSE observer
    └── Session B ── Job B ── SSE observer

同一账号的不同会话允许同时运行任务，由服务端接口决定；同一会话仍然遵守应用现有的活动
Job 约束，因此每个会话最多一个正在运行或等待的 Job。账号级 create_budget 由线程安全计数器
控制，只有“成功返回一个唯一新 Job”的创建结果才占用额度。
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

from runner import config
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
    """执行单一账号的完整流程或混合操作，创建 Job 后交给后台观察器。"""

    def __init__(
        self,
        context: UserContext,
        recorder: Recorder,
        job_gate,
        stop: StopController,
        rng: random.Random,
        sessions_per_user: int = 1,
    ) -> None:
        self.ctx = context
        self.recorder = recorder
        self.job_gate = job_gate
        self.stop = stop
        self.rng = rng
        self.sessions_per_user = max(1, int(sessions_per_user))
        self.csv = build_csv(context.index)
        self.sse_gate = context.client.sse_gate
        self._session_lock = threading.Lock()

    # --- 组合流程 ---

    def prepare(self) -> None:
        """登录、建立全部真实会话并各自上传测试文件。"""
        self.ctx.client.login(self.ctx.password)
        for index in range(self.sessions_per_user):
            session_id = self.ctx.client.new_chat()
            slot = SessionSlot(index=index, session_id=session_id)
            slot.file_id = self.ctx.client.upload_csv(
                f"loadtest_{self.ctx.index:02d}_{index:02d}.csv", self.csv
            )
            # 新会话没有活动 Job，初始状态就是可提交。
            slot.ready.set()
            self.ctx.sessions.append(slot)
        self.recorder.note(
            f"账号 {self.ctx.username} 建立 {len(self.ctx.sessions)} 个真实会话"
        )

    def run_full_flow(self, rounds: int) -> None:
        """单用户基线：重复完整流程，每轮创建 Job 后同步等待终态。"""
        self.prepare()
        for round_index in range(rounds):
            if self.stop.triggered:
                return
            if round_index > 0:
                session_id = self.ctx.client.new_chat()
                slot = SessionSlot(index=0, session_id=session_id)
                slot.file_id = self.ctx.client.upload_csv(
                    f"loadtest_{self.ctx.index:02d}.csv", self.csv
                )
                slot.ready.set()
                self.ctx.sessions[0] = slot
            self._run_blocking_job_cycle()
            self._read_queries()
            self._think()

    def run_mixed(self, duration_seconds: float) -> None:
        """混合负载：按权重挑选操作，创建 Job 后由后台观察器继续等待。"""
        self.prepare()
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline and not self.stop.triggered:
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
                    slot = self._pick_session()
                    if slot is not None:
                        self.ctx.client.upload_csv(
                            f"loadtest_{self.ctx.index:02d}_{slot.index:02d}.csv", self.csv
                        )
                else:
                    self._submit_job()
            except ApiError as exc:
                self.recorder.note(f"虚拟用户 {self.ctx.username} 操作失败: {exc}")
            self._think()

    def wait_observers(self, deadline_seconds: float) -> None:
        """等待本账号全部后台观察器结束；用于停止提交后的排空阶段。"""
        deadline = time.monotonic() + deadline_seconds
        for thread in list(self.ctx.observers):
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

    # --- 单项操作 ---

    def _pick_operation(self) -> str:
        roll = self.rng.random()
        cumulative = 0.0
        for name in ("read", "page", "upload", "create_job"):
            cumulative += config.MIX_WEIGHTS[name]
            if roll <= cumulative:
                return name
        return "read"

    def _has_budget(self) -> bool:
        """检查账号级额度是否还有余额，不占用额度。"""
        with self.ctx.budget_lock:
            return self.ctx.budget is None or self.ctx.budget_used < self.ctx.budget

    def _pick_session(self) -> SessionSlot | None:
        """挑一条可用会话；全部忙时等待，等待超时返回 None。"""
        deadline = time.monotonic() + config.SESSION_SLOT_WAIT_SECONDS
        while time.monotonic() < deadline:
            for slot in self.ctx.sessions:
                if slot.ready.is_set():
                    return slot
            time.sleep(0.1)
        return None

    def _read_queries(self) -> None:
        """会话、历史、文件与活动 Job 查询。"""
        client = self.ctx.client
        client.sessions()
        for slot in self.ctx.sessions:
            client.load_session(slot.session_id)
        client.list_files()
        client.active_jobs()

    def _page_requests(self) -> None:
        """登录状态与普通页面接口。"""
        client = self.ctx.client
        client.site_page()
        client.dashboard_page()
        client.check_auth()

    def _submit_job(self) -> str | None:
        """占用账号额度、某个会话和 Job 并发额度后提交，再交给后台观察器。"""
        if not self.ctx.occupy_budget():
            self.recorder.bump("job_submissions_over_budget")
            return None
        submitted = False
        try:
            slot = self._pick_session()
            if slot is None:
                self.recorder.bump("job_submissions_session_busy")
                self.recorder.note(
                    f"账号 {self.ctx.username} 会话在 "
                    f"{config.SESSION_SLOT_WAIT_SECONDS} 秒内全部忙碌，本次不提交"
                )
                return None
            if self.stop.triggered:
                return None
            if not self._acquire_job_slot():
                self.recorder.note("等待 Job 并发额度超时，仍未提交本次创建")
                return None
            try:
                job_id = self._create_job(slot)
                submitted = job_id is not None
                return job_id
            finally:
                if not submitted:
                    self._release_job_slot()
        finally:
            if not submitted:
                self.ctx.release_budget()

    def _acquire_job_slot(self) -> bool:
        """占用 Job 并发额度；额度为 None 表示客户端不设上限。"""
        if self.job_gate is None:
            return True
        return bool(self.job_gate.acquire(timeout=config.JOB_SLOT_WAIT_SECONDS))

    def _release_job_slot(self) -> None:
        if self.job_gate is not None:
            self.job_gate.release()

    def _create_job(self, slot: SessionSlot) -> str | None:
        """创建 Job；只有返回唯一新 Job 才计入成功创建。"""
        client = self.ctx.client
        self.recorder.bump("job_create_attempts")
        key = new_idempotency_key()
        created_at = time.monotonic()
        try:
            status, payload = client.create_job(
                session_id=slot.session_id,
                message=MESSAGE,
                file_id=slot.file_id,
                idempotency_key=key,
            )
        except ApiError as exc:
            self.recorder.note(f"创建 Job 传输失败 {self.ctx.username}: {exc}")
            return None
        if status not in (200, 202):
            self.recorder.note(f"创建 Job 返回 {status}: {payload.get('error')}")
            return None
        job_id = str(payload.get("job_id"))
        if payload.get("existing"):
            # 幂等重放不占用账号创建额度，也不作为成功提交计数。
            self.recorder.bump("job_idempotent_replays")
            return None
        self.ctx.job_ids.append(job_id)
        self.recorder.bump("job_created")
        self.recorder.bump_user("job_created_by_user", self.ctx.index)
        slot.current_job_id = job_id
        slot.ready.clear()
        self.ctx.enter_job()
        self.recorder.peak_active_jobs_per_user = max(
            self.recorder.peak_active_jobs_per_user or 0, self.ctx.active_jobs_now
        )
        thread = threading.Thread(
            target=self._observe,
            name=f"obs-{self.ctx.index}-{slot.index}-{job_id[:8]}",
            args=(slot, job_id, created_at),
            daemon=True,
        )
        self.ctx.observers.append(thread)
        thread.start()
        return job_id

    def _observe(self, slot: SessionSlot, job_id: str, created_at: float) -> None:
        """后台观察器：订阅 SSE、必要时重连，收到终态或到达观察期限后释放会话。"""
        try:
            queue_seconds, queue_timeout = self._observe_queue(job_id, created_at)
            outcome = self.ctx.client.subscribe_job_events(job_id)
            sample = outcome.sample
            duplicates = _duplicates(sample.received_ids)
            out_of_order = _out_of_order(sample.received_ids)
            missing = 0
            unique_received = len(set(sample.received_ids))
            if sample.terminal_event is not None and (
                sample.error
                or sample.reconnects
                or missing
                or duplicates
                or out_of_order
            ):
                # 只在已到达终态且出现异常信号时读取全量事件，保证比对不会阻塞。
                total_events = self.ctx.client.count_job_events(job_id)
                if total_events >= 0:
                    missing = max(0, total_events - unique_received)
                    self.recorder.sse_missing_events += missing
            self.stop.check_sse(missing, duplicates, out_of_order)
            terminal = sample.terminal_event
            if terminal == "interrupt":
                final_status = self._answer_interrupt(
                    job_id, outcome.interrupt_payload, slot.session_id
                )
            else:
                final_status = _final_status(terminal, sample.error)
            observation = (
                "terminal"
                if terminal in ("final_result", "error", "canceled") or final_status == "succeeded"
                else "observation_timeout"
            )
            if observation == "observation_timeout":
                self.recorder.bump("job_observation_timeouts")
            history_ok = (
                self._verify_history(slot.session_id, job_id)
                if observation == "terminal"
                else None
            )
            elapsed = time.monotonic() - created_at
            execution_seconds = (
                None
                if queue_seconds is None
                else round(max(0.0, elapsed - queue_seconds), 2)
            )
            self.recorder.record_job(
                JobSample(
                    job_id=job_id,
                    created_status=202,
                    final_status=final_status,
                    queue_seconds=queue_seconds,
                    execution_seconds=execution_seconds,
                    total_seconds=elapsed,
                    history_consistent=history_ok,
                    user_index=self.ctx.index,
                    session_index=slot.index,
                    queue_timeout=queue_timeout,
                    observation=observation,
                    queue_seconds_lower_bound=(
                        None if queue_seconds is not None else round(elapsed, 2)
                    ),
                )
            )
        except ApiError as exc:
            self.recorder.note(f"后台观察失败 {self.ctx.username}/{job_id}: {exc}")
        finally:
            slot.current_job_id = None
            slot.ready.set()
            self.ctx.leave_job()
            self._release_job_slot()

    def _run_blocking_job_cycle(self) -> str | None:
        """基线档的同步周期：提交后立即在调用线程内等待终态。"""
        slot = self.ctx.sessions[0]
        if not self.ctx.occupy_budget():
            return None
        if not self._acquire_job_slot():
            self.ctx.release_budget()
            return None
        job_id = self._create_job(slot)
        if job_id is None:
            self._release_job_slot()
            self.ctx.release_budget()
            return None
        self.wait_observers(config.JOB_OBSERVE_DEADLINE_SECONDS)
        return job_id

    def _answer_interrupt(
        self, job_id: str, payload: dict | None, session_id: str
    ) -> str:
        """对 waiting_input 发送真实的恢复输入。"""
        question_id = str((payload or {}).get("question_id") or "")
        if not question_id:
            self.recorder.note(f"interrupt 缺少 question_id: {payload}")
            return "waiting_input"
        status, body = self.ctx.client.resume_job(
            job_id,
            question_id,
            "请按默认参数继续分析。",
            new_idempotency_key(),
        )
        if status not in (200, 202):
            self.recorder.note(f"resume 返回 {status}: {body.get('error')}")
            return "waiting_input"
        outcome = self.ctx.client.subscribe_job_events(job_id)
        self._verify_history(session_id, job_id)
        return _final_status(outcome.terminal_event, outcome.sample.error)

    def _observe_queue(self, job_id: str, created_at: float) -> tuple[float | None, bool]:
        """轮询会话活动 Job，记录从创建到首次观察到 running 的排队耗时。

        返回 (排队秒数或 None, 是否超过排队观测窗口)。超过窗口时不再阻塞观察，
        由 SSE 继续等待终态；排队时间按“未取得”处理，另记等待时间下界。
        """
        deadline = created_at + config.QUEUE_OBSERVE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                for job in self.ctx.client.active_jobs():
                    if str(job.get("job_id")) != job_id:
                        continue
                    status = str(job.get("status"))
                    if status == "running":
                        return round(time.monotonic() - created_at, 2), False
                    if status in ("succeeded", "failed", "canceled"):
                        return None, False
            except ApiError:
                return None, False
            time.sleep(0.5)
        return None, True

    def _verify_history(self, session_id: str, job_id: str) -> bool:
        """核对会话历史是否包含该 Job 的终态结果。"""
        payload = self.ctx.client.load_session(session_id)
        messages = payload.get("messages") or []
        for message in messages:
            if str(message.get("analysis_job_id")) != job_id:
                continue
            if message.get("sender") == "ai":
                text = message.get("text")
                if isinstance(text, dict) and text:
                    return True
                if isinstance(text, str) and text.strip():
                    return True
        return False

    def _think(self) -> None:
        time.sleep(self.rng.uniform(config.THINK_MIN_SECONDS, config.THINK_MAX_SECONDS))


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
