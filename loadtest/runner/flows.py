"""虚拟用户的业务场景：完整流程、混合操作与结果校验。"""

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
class UserContext:
    """一个虚拟用户在运行期间持有的状态。"""

    index: int
    username: str
    password: str
    client: UserClient
    file_id: int | None = None
    session_id: str | None = None
    job_ids: list[str] = field(default_factory=list)


class VirtualUser:
    """执行单一账号的完整流程或混合操作。"""

    def __init__(
        self,
        context: UserContext,
        recorder: Recorder,
        job_gate: threading.Semaphore,
        stop: StopController,
        rng: random.Random,
    ) -> None:
        self.ctx = context
        self.recorder = recorder
        self.job_gate = job_gate
        self.stop = stop
        self.rng = rng
        self.csv = build_csv(context.index)
        # 创建 Job 必须先占用 Job 并发额度，再占用 SSE 额度。
        self.sse_gate = context.client.sse_gate
        self.job_slot = job_gate

    # --- 组合流程 ---

    def prepare(self) -> None:
        """登录、建立会话并上传测试文件。"""
        self.ctx.client.login(self.ctx.password)
        self.ctx.session_id = self.ctx.client.new_chat()
        self.ctx.file_id = self.ctx.client.upload_csv(
            f"loadtest_{self.ctx.index:02d}.csv", self.csv
        )

    def run_full_flow(self, rounds: int) -> None:
        """单用户基线：重复完整流程。"""
        self.prepare()
        for round_index in range(rounds):
            if self.stop.triggered:
                return
            if round_index > 0:
                self.ctx.session_id = self.ctx.client.new_chat()
            self._run_job_cycle()
            self._read_queries()
            self._think()

    def run_mixed(self, duration_seconds: float) -> None:
        """混合负载：按权重挑选操作，直到到达持续时间。"""
        self.prepare()
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline and not self.stop.triggered:
            operation = self._pick_operation()
            try:
                if operation == "read":
                    self._read_queries()
                elif operation == "page":
                    self._page_requests()
                elif operation == "upload":
                    self.ctx.client.upload_csv(
                        f"loadtest_{self.ctx.index:02d}.csv", self.csv
                    )
                else:
                    self._run_job_cycle()
            except ApiError as exc:
                self.recorder.note(f"虚拟用户 {self.ctx.username} 操作失败: {exc}")
            self._think()

    # --- 单项操作 ---

    def _pick_operation(self) -> str:
        roll = self.rng.random()
        cumulative = 0.0
        for name in ("read", "page", "upload", "create_job"):
            cumulative += config.MIX_WEIGHTS[name]
            if roll <= cumulative:
                return name
        return "read"

    def _read_queries(self) -> None:
        """会话、历史、文件与活动 Job 查询。"""
        client = self.ctx.client
        client.sessions()
        if self.ctx.session_id:
            client.load_session(self.ctx.session_id)
        client.list_files()
        client.active_jobs()

    def _page_requests(self) -> None:
        """登录状态与普通页面接口。"""
        client = self.ctx.client
        client.site_page()
        client.dashboard_page()
        client.check_auth()

    def _run_job_cycle(self) -> str | None:
        """创建 Job、订阅 SSE、校验历史，最后释放 Job 并发额度。"""
        client = self.ctx.client
        if not self.job_slot.acquire(timeout=config.JOB_SLOT_WAIT_SECONDS):
            self.recorder.note("等待 Job 并发额度超时，仍未提交本次创建")
            return None
        try:
            if self.stop.triggered:
                return None
            session_id = self.ctx.session_id or client.new_chat()
            self.ctx.session_id = session_id
            key = new_idempotency_key()
            created_at = time.monotonic()
            status, payload = client.create_job(
                session_id=session_id,
                message=MESSAGE,
                file_id=self.ctx.file_id,
                idempotency_key=key,
            )
            if status not in (200, 202):
                self.recorder.note(
                    f"创建 Job 返回 {status}: {payload.get('error')}"
                )
                return None
            job_id = str(payload.get("job_id"))
            self.ctx.job_ids.append(job_id)
            if payload.get("existing"):
                self.recorder.bump("job_idempotent_replays")
            else:
                self.recorder.bump("job_created")
            queue_seconds = self._observe_queue(session_id, job_id, created_at)
            outcome = client.subscribe_job_events(job_id)
            run_finished_at = time.monotonic()
            sample = outcome.sample
            duplicates = _duplicates(sample.received_ids)
            out_of_order = _out_of_order(sample.received_ids)
            missing = self.recorder.sse_missing_events
            unique_received = len(set(sample.received_ids))
            if sample.terminal_event is not None and (
                sample.error
                or sample.reconnects
                or sample.disconnects
                or missing
                or duplicates
                or out_of_order
            ):
                # 只在已到达终态且出现异常信号时读取全量事件，保证比对不会阻塞。
                total_events = client.count_job_events(job_id)
                if total_events >= 0:
                    missing = max(0, total_events - unique_received)
                    self.recorder.sse_missing_events += missing
            self.stop.check_sse(missing, duplicates, out_of_order)
            history_ok = self._verify_history(session_id, job_id)
            final_status = _final_status(outcome.terminal_event, sample.error)
            if outcome.terminal_event == "interrupt":
                final_status = self._answer_interrupt(
                    job_id, outcome.interrupt_payload, session_id
                )
            execution_seconds = (
                round(run_finished_at - created_at - queue_seconds, 2)
                if queue_seconds is not None
                else None
            )
            self.recorder.record_job(
                JobSample(
                    job_id=job_id,
                    created_status=status,
                    final_status=final_status,
                    queue_seconds=queue_seconds,
                    execution_seconds=execution_seconds,
                    total_seconds=time.monotonic() - created_at,
                    history_consistent=history_ok,
                )
            )
            return job_id
        except ApiError as exc:
            self.recorder.note(f"Job 周期失败 {self.ctx.username}: {exc}")
            return None
        finally:
            self.job_slot.release()

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

    def _observe_queue(
        self, session_id: str, job_id: str, created_at: float
    ) -> float | None:
        """轮询活动 Job，记录从创建到首次观察到 running 的排队耗时。"""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                for job in self.ctx.client.active_jobs(session_id):
                    if str(job.get("job_id")) != job_id:
                        continue
                    if str(job.get("status")) == "running":
                        return round(time.monotonic() - created_at, 2)
                    if str(job.get("status")) in ("succeeded", "failed", "canceled"):
                        return None
            except ApiError:
                return None
            time.sleep(0.5)
        return None

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
