"""第五阶段专项：SSE 断线恢复、幂等、取消、等待输入与跨账号隔离。"""

from __future__ import annotations

import json
import time

from runner import config
from runner.accounts import Account
from runner.api import ApiError, UserClient, new_idempotency_key
from runner.flows import MESSAGE, VirtualUser, build_csv
from runner.manifest import collect_client_manifest
from runner.metrics import Recorder


class RecoverySuite:
    """在一个独立账号上顺序执行专项检查，逐项记录结论。"""

    def __init__(
        self,
        base_url: str,
        account: Account,
        peer_account: Account,
        recorder: Recorder,
    ) -> None:
        self.base_url = base_url
        self.recorder = recorder
        self.client = UserClient(base_url, recorder, account.username)
        self.peer = UserClient(base_url, recorder, peer_account.username)
        self.account = account
        self.peer_account = peer_account
        self.results: list[dict] = []
        self.session_id: str | None = None
        self.file_id: int | None = None

    def close(self) -> None:
        self.client.close()
        self.peer.close()

    def record(self, name: str, passed: bool | None, detail: str) -> None:
        """记录一项结论。

        passed 为 True 表示通过，False 表示未通过，None 表示本次没有触发对应
        分支、因此无法判定；最后一种不能当成通过。
        """
        label = "未触发" if passed is None else ("通过" if passed else "未通过")
        self.results.append(
            {"check": name, "passed": passed, "outcome": label, "detail": detail}
        )
        self.recorder.note(f"[{label}] {name}: {detail}")

    def _note_job(self) -> None:
        """专项检查也创建真实 Job，计入 Job 计数。"""
        self.recorder.bump("job_created")

    def setup(self) -> None:
        """登录、建会话、上传文件。"""
        self.client.login(self.account.password)
        self.session_id = self.client.new_chat()
        self.file_id = self.client.upload_csv("loadtest_recovery.csv", build_csv(90))

    # --- 1～3：SSE 断线恢复 ---

    def check_sse_resume(self) -> None:
        key = new_idempotency_key()
        status, payload = self.client.create_job(
            self.session_id, MESSAGE, self.file_id, key
        )
        if status not in (200, 202):
            self.record("SSE 断线恢复", False, f"创建 Job 失败 {status} {payload}")
            return
        self._note_job()
        job_id = str(payload["job_id"])
        first_segment = self._read_segment(job_id, 0, stop_after=1)
        cursor = first_segment["last_event_id"]
        time.sleep(1.0)
        second_segment = self._read_segment(job_id, cursor, stop_after=0)
        all_ids = first_segment["ids"] + second_segment["ids"]
        gap = _gap(first_segment["last_event_id"], second_segment["ids"])
        duplicates = len(all_ids) - len(set(all_ids))
        detail = (
            f"首段 {len(first_segment['ids'])} 事件(cursor={cursor})，"
            f"重连后 {len(second_segment['ids'])} 事件，"
            f"缺口 {gap}，重复 {duplicates}，终态 {second_segment['terminal']}"
        )
        self.record("SSE 断线恢复", gap == 0 and duplicates == 0, detail)

    def _read_segment(self, job_id: str, after: int, stop_after: int) -> dict:
        """读取一段事件流；stop_after>0 时读到该数量事件后主动断开。"""
        ids: list[int] = []
        terminal: str | None = None
        headers = {"Accept": "text/event-stream"}
        if after:
            headers["Last-Event-ID"] = str(after)
        with self.client.client.stream(
            "GET", f"/api/agent/jobs/{job_id}/events", headers=headers
        ) as response:
            from runner.api import _iter_sse

            for event_type, event_id, _payload in _iter_sse(response):
                if event_id is not None:
                    ids.append(event_id)
                if event_type in ("final_result", "error", "canceled", "interrupt"):
                    terminal = event_type
                    break
                if stop_after and len(ids) >= stop_after:
                    break
        last = ids[-1] if ids else after
        return {"ids": ids, "last_event_id": last, "terminal": terminal}

    # --- 4：幂等 ---

    def check_idempotency(self) -> None:
        session_id = self.client.new_chat()
        file_id = self.file_id
        key = new_idempotency_key()
        first_status, first = self.client.create_job(session_id, MESSAGE, file_id, key)
        self._note_job()
        second_status, second = self.client.create_job(session_id, MESSAGE, file_id, key)
        same_job = first.get("job_id") == second.get("job_id")
        third_status, third = self.client.create_job(
            session_id, MESSAGE + " 换个问题", file_id, key
        )
        detail = (
            f"首次 {first_status}，重放 {second_status}(existing={second.get('existing')})，"
            f"同键不同参数 {third_status} {third.get('code')}"
        )
        self.record(
            "幂等创建",
            second_status == 200 and same_job and third_status == 409,
            detail,
        )
        self._cancel(first.get("job_id"), "清理幂等检查的 Job")

    # --- 5：取消排队 Job ---

    def check_cancel_queued(self) -> None:
        session_id = self.client.new_chat()
        key = new_idempotency_key()
        status, payload = self.client.create_job(session_id, MESSAGE, self.file_id, key)
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or not job_id:
            self.record("取消排队 Job", False, f"创建失败 {status} {payload}")
            return
        self._note_job()
        cancel_key = new_idempotency_key()
        cancel_status, cancel_body = self.client.cancel_job(job_id, cancel_key)
        replay_status, replay_body = self.client.cancel_job(job_id, cancel_key)
        history = self._job_status_in_history(session_id, job_id)
        detail = (
            f"取消 {cancel_status}(status={cancel_body.get('status')})，"
            f"重放 {replay_status}(existing={replay_body.get('existing')})，"
            f"历史状态 {history}"
        )
        self.record(
            "取消排队 Job",
            cancel_status == 202 and replay_status == 200 and history == "canceled",
            detail,
        )

    # --- 6：取消运行中 Job ---

    def check_cancel_running(self) -> None:
        session_id = self.client.new_chat()
        status, payload = self.client.create_job(
            session_id, MESSAGE, self.file_id, new_idempotency_key()
        )
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or not job_id:
            self.record("取消运行中 Job", False, f"创建失败 {status} {payload}")
            return
        self._note_job()
        running = self._wait_for_status(session_id, "running", 30)
        cancel_status, cancel_body = self.client.cancel_job(
            job_id, new_idempotency_key()
        )
        final = self._job_status_in_history(session_id, job_id)
        detail = (
            f"观察到 running={running}，取消 {cancel_status}"
            f"(status={cancel_body.get('status')})，历史最终状态 {final}"
        )
        self.record(
            "取消运行中 Job",
            running and cancel_status == 202 and final == "canceled",
            detail,
        )

    # --- 7：取消后状态不反转 ---

    def check_no_reversal(self) -> None:
        session_id = self.client.new_chat()
        status, payload = self.client.create_job(
            session_id, MESSAGE, self.file_id, new_idempotency_key()
        )
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or not job_id:
            self.record("取消后状态不反转", False, f"创建失败 {status} {payload}")
            return
        self._note_job()
        self.client.cancel_job(job_id, new_idempotency_key())
        first = self._job_status_in_history(session_id, job_id)
        time.sleep(5.0)
        second = self._job_status_in_history(session_id, job_id)
        sequence = self._job_status_sequence(job_id)
        detail = f"取消后 {first}，5 秒后 {second}，事件序列 {sequence}"
        self.record(
            "取消后状态不反转",
            first == "canceled" and second == "canceled" and sequence[-1] == "canceled",
            detail,
        )

    # --- 8：waiting_input 恢复 ---

    def check_waiting_input(self) -> None:
        session_id = self.client.new_chat()
        status, payload = self.client.create_job(
            session_id,
            "请用 patients.csv 这个文件再做一次分析。",
            None,
            new_idempotency_key(),
        )
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or not job_id:
            self.record("waiting_input 恢复", False, f"创建失败 {status} {payload}")
            return
        self._note_job()
        outcome = self.client.subscribe_job_events(job_id)
        if outcome.terminal_event != "interrupt":
            self.record(
                "waiting_input 恢复",
                None,
                "本次请求未进入 waiting_input（终态 "
                f"{outcome.terminal_event}），恢复分支未触发，无法判定",
            )
            return
        question_id = str((outcome.interrupt_payload or {}).get("question_id") or "")
        if not question_id:
            self.record("waiting_input 恢复", False, f"interrupt 缺少 question_id")
            return
        resume_status, resume_body = self.client.resume_job(
            job_id, question_id, "请改用文件库中已上传的 CSV。", new_idempotency_key()
        )
        resumed = self.client.subscribe_job_events(job_id)
        detail = (
            f"问题 {question_id}，恢复 {resume_status}"
            f"(status={resume_body.get('status')})，恢复后终态 {resumed.terminal_event}"
        )
        self.record(
            "waiting_input 恢复",
            resume_status in (200, 202) and resumed.terminal_event is not None,
            detail,
        )

    # --- 9：跨账号隔离 ---

    def check_cross_account_isolation(self) -> None:
        self.peer.login(self.peer_account.password)
        owner_session = self.session_id
        owner_status, owner_payload = self.client.create_job(
            owner_session, MESSAGE, self.file_id, new_idempotency_key()
        )
        owner_job = str(owner_payload.get("job_id") or "")
        if owner_status in (200, 202):
            self._note_job()
        cross_session = self.peer.client.get(
            "/api/load_session", params={"session": owner_session}
        )
        cross_events = self.peer.client.get(f"/api/agent/jobs/{owner_job}/events")
        owner_files = self.client.list_files()
        peer_files = self.peer.list_files()
        owner_ids = {item.get("user_file_id") for item in owner_files}
        peer_ids = {item.get("user_file_id") for item in peer_files}
        leaked_ids = owner_ids & peer_ids
        session_ok = cross_session.status_code in (403, 404)
        events_ok = cross_events.status_code in (403, 404)
        files_ok = not leaked_ids
        detail = (
            f"跨账号读会话 {cross_session.status_code}，读事件 {cross_events.status_code}，"
            f"属主文件 {len(owner_files)} 条 / 对端自持 {len(peer_files)} 条 / "
            f"交叉可见 {len(leaked_ids)} 条"
        )
        self.record(
            "跨账号隔离", session_ok and events_ok and files_ok, detail
        )
        if owner_status in (200, 202):
            self._cancel(owner_job, "清理隔离检查的 Job")

    # --- 辅助 ---

    def _job_status_in_history(self, session_id: str, job_id: str) -> str | None:
        payload = self.client.load_session(session_id)
        for message in payload.get("messages") or []:
            if str(message.get("analysis_job_id")) != job_id:
                continue
            phase = message.get("thinking_after")
            if isinstance(phase, dict) and phase.get("status"):
                return str(phase["status"])
        jobs = self.client.active_jobs(session_id)
        for job in jobs:
            if str(job.get("job_id")) == job_id:
                return str(job.get("status"))
        return None

    def _job_status_sequence(self, job_id: str) -> list[str]:
        sequence: list[str] = []
        with self.client.client.stream(
            "GET", f"/api/agent/jobs/{job_id}/events"
        ) as response:
            from runner.api import _iter_sse

            for event_type, _event_id, _payload in _iter_sse(response):
                if event_type in ("final_result", "error", "canceled", "interrupt"):
                    sequence.append(event_type)
        return sequence

    def _wait_for_status(self, session_id: str, target: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for job in self.client.active_jobs(session_id):
                if str(job.get("status")) == target:
                    return True
            time.sleep(1.0)
        return False

    def _cancel(self, job_id: str | None, reason: str) -> None:
        if not job_id:
            return
        try:
            status, body = self.client.cancel_job(job_id, new_idempotency_key())
            if status not in (200, 202, 409):
                self.recorder.note(f"{reason} 返回 {status}: {body.get('error')}")
        except ApiError as exc:
            self.recorder.note(f"{reason} 失败: {exc}")


def run_recovery_suite(
    base_url: str,
    accounts: list[Account],
    server_baseline: dict | None = None,
) -> dict:
    """执行第五阶段全部专项检查。"""
    if len(accounts) < 2:
        raise RuntimeError("取消与恢复专项至少需要 2 个测试账号")
    recorder = Recorder(run_id=time.strftime("%Y%m%dT%H%M%S") + "-recovery")
    recorder.stage = "recovery"
    recorder.users = 2
    recorder.server_baseline = server_baseline
    started = time.time()
    recorder.started_at = started
    recorder.note(
        f"客户端清单: {json.dumps(collect_client_manifest(), ensure_ascii=False)}"
    )
    suite = RecoverySuite(base_url, accounts[0], accounts[1], recorder)
    try:
        suite.setup()
        suite.check_sse_resume()
        suite.check_idempotency()
        suite.check_cancel_queued()
        suite.check_cancel_running()
        suite.check_no_reversal()
        suite.check_waiting_input()
        suite.check_cross_account_isolation()
    finally:
        suite.close()
    recorder.finished_at = time.time()
    summary = recorder.summary()
    summary["checks"] = suite.results
    summary["elapsed_seconds"] = round(time.time() - started, 2)
    return summary


def _gap(cursor: int, ids: list[int]) -> int:
    """重连后应严格从 cursor+1 开始；返回缺失事件数量。"""
    if not ids:
        return 0
    expected = cursor + 1
    missing = 0
    for event_id in ids:
        if event_id > expected:
            missing += event_id - expected
        expected = event_id + 1
    return missing
