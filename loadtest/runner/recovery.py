"""真实 API 专项：有限观察 SSE、幂等、取消和隔离；服务器与前端证据独立列出。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx

from runner import config
from runner.accounts import Account
from runner.api import ApiError, UserClient, new_idempotency_key
from runner.flows import MESSAGE, build_csv
from runner.manifest import collect_client_manifest
from runner.metrics import HttpSample, Recorder, SseSample
from runner.sse import iter_events

TERMINAL = {"final_result", "error", "canceled"}
SPECIAL_GAPS = [
    "取消执行释放：需主库 execution_state、execution_released_at、释放原因和 worker 槽位前后证据；取消 API 成功仅证明业务取消。",
    "算法进程终止：需真实 invocation 与目标 PID、取消响应、PID 消失及重复取消证据；本专项不执行进程终止操作。",
    "前端阶段：需真实浏览器刷新恢复、waiting_input 展示、取消后迟到事件不反转及页面交互证据；API 检查不证明前端通过。",
]


class RecoverySuite:
    """顺序执行真实 API 检查，每项只判定自己实际取得的证据。"""

    def __init__(self, base_url: str, account: Account, peer_account: Account, recorder: Recorder) -> None:
        self.base_url = base_url
        self.recorder = recorder
        self.client = UserClient(base_url, recorder, account.username)
        self.peer = UserClient(base_url, recorder, peer_account.username)
        self.account = account
        self.peer_account = peer_account
        self.results: list[dict] = []
        self.session_id: str | None = None
        self.file_id: int | None = None
        self.job_requests: dict[str, str] = {}
        self.created_at: dict[str, float] = {}
        self.created_mono: dict[str, float] = {}
        self.deadlines: dict[str, float] = {}
        self.received: dict[str, list[int]] = {}
        self.terminals: dict[str, str] = {}
        self.first_event_jobs: set[str] = set()

    def close(self) -> None:
        self.client.close()
        self.peer.close()

    def record(self, name: str, passed: bool | None, detail: str) -> None:
        label = "未验证" if passed is None else ("通过" if passed else "未通过")
        self.results.append({"check": name, "passed": passed, "outcome": label, "detail": detail})
        self.recorder.note(f"[{label}] {name}: {detail}")

    def setup(self) -> None:
        self.client.login(self.account.password)
        self.session_id = self.client.new_chat()
        self.file_id = self.client.upload_csv("loadtest_recovery.csv", build_csv(90))

    def _create_job(self, session_id: str, message: str, file_id: int | None, key: str,
                    payload_variant: str = "original") -> tuple[int, dict]:
        logical_id = new_idempotency_key()
        started_at, started = time.time(), time.monotonic()
        deadline = started + config.JOB_OBSERVE_DEADLINE_SECONDS
        fingerprint = hashlib.sha256(json.dumps({"session_id": session_id, "message": message,
                                                 "input_user_file_id": file_id, "web_search_enabled": False},
                                                sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        self.recorder.register_request(
            logical_id, created_request_at=started_at, group="recovery", session_id=session_id,
            username=self.account.username, user_index=1, idempotency_key=key,
            payload_variant=payload_variant, payload_fingerprint=fingerprint,
            intentional_conflict=payload_variant == "different_message",
            job_id=None, terminal_received_at=None, user_wait_ms=None, wait_lower_bound_ms=None,
            history_check_ms=None, received_event_ids=[],
        )
        self.recorder.bump("job_create_attempts")
        errors, uncertain = [], False
        status, payload = 0, {}
        for attempt in range(1, config.CREATE_MAX_ATTEMPTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.recorder.lifecycle(logical_id, event="create_post_started",
                                    post_started_at=time.time(), create_attempt=attempt,
                                    create_transport_retries=attempt - 1, admission="unknown")
            try:
                status, payload = self.client.create_job(session_id, message, file_id, key,
                    timeout=min(config.HTTP_TIMEOUT_SECONDS, remaining))
                self.recorder.lifecycle(logical_id, event="create_response",
                                        create_response_at=time.time(),
                                        created_status=status, create_attempt=attempt)
                if status in (200, 202) and payload.get("job_id"):
                    break
                errors.append({"attempt": attempt, "status": status})
                uncertain = uncertain or status >= 500 or status in (200, 202)
                if status < 500 and status not in (200, 202):
                    break
            except ApiError as exc:
                uncertain = True
                errors.append({"attempt": attempt, "error": type(exc).__name__})
            self.recorder.lifecycle(logical_id, event="create_error", create_errors=list(errors),
                                    admission="acceptance_unknown" if uncertain else "rejected")
            if attempt < config.CREATE_MAX_ATTEMPTS:
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        job_id = str(payload.get("job_id") or "")
        accepted = status == 202 and payload.get("existing") is False and bool(job_id)
        replay = status == 200 and payload.get("existing") is True and bool(job_id)
        admission = "accepted" if accepted else ("replay" if replay else ("conflict" if status == 409 else "rejected"))
        if status == 0 or status >= 500 or (status in (200, 202) and not (accepted or replay)) or (uncertain and not (accepted or replay)):
            admission = "acceptance_unknown"
        self.recorder.lifecycle(logical_id, job_id=job_id or None, event="admission",
                                admission=admission, created_status=status)
        if accepted or replay:
            if job_id not in self.job_requests:
                self.recorder.record_created_job(job_id)
                self.recorder.bump("job_created")
                self.job_requests[job_id] = logical_id
                self.created_at[job_id] = started_at
                self.created_mono[job_id] = started
                self.deadlines[job_id] = deadline
                self.client.bind_job_request(job_id, logical_id, started)
        if replay:
            self.recorder.bump("job_idempotent_replays")
        return status, payload

    def check_sse_resume(self) -> None:
        status, payload = self._create_job(self.session_id, MESSAGE, self.file_id, new_idempotency_key())
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record("SSE 断线恢复", False, f"首次创建未成功，HTTP {status}")
            return
        first = self._read_segment(job_id, 0, stop_after=1)
        if first["terminal"] or not first["ids"]:
            self.record("SSE 断线恢复", None, "未取得可用于中途断开的非终态游标，不能验证重连分支")
            self._cancel(job_id, "结束未触发的重连检查")
            return
        second = self._read_segment(job_id, first["last_event_id"])
        observed = first["ids"] + second["ids"]
        # 全局自增 ID 可以跨 Job 跳号；只能用同 Job 的完整重放集合核对遗漏。
        full = self._read_segment(job_id, 0, capture=False) if second["terminal"] in TERMINAL else None
        complete = bool(full and full["terminal"] in TERMINAL and not full["error"])
        missing = sorted(set(full["ids"]) - set(observed)) if complete else None
        unexpected = sorted(set(observed) - set(full["ids"])) if complete else None
        duplicate_count = len(observed) - len(set(observed))
        ordered = all(a < b for a, b in zip(observed, observed[1:]))
        passed = (complete and bool(full["ids"]) and full["terminal"] == second["terminal"]
                  and not second["error"] and not missing and not unexpected and duplicate_count == 0 and ordered)
        self.record("SSE 断线恢复", bool(passed),
                    f"首段 {len(first['ids'])}、重连 {len(second['ids'])}、完整重放可核对={complete}；"
                    f"漏事件={len(missing) if missing is not None else '未采集'}，重复={duplicate_count}，严格递增={ordered}，"
                    f"重连终态={second['terminal']}。完整重放只证明本次流恢复，主库事件集核对另行采集。")
        if not complete:
            self._cancel(job_id, "结束未完成的重连检查")

    def _read_segment(self, job_id: str, after: int = 0, stop_after: int = 0,
                      capture: bool = True, client: UserClient | None = None, headers_only: bool = False) -> dict:
        """总期限覆盖连接和心跳，空闲读取默认 45 秒；所有连接均关闭。"""
        return asyncio.run(self._read_segment_async(job_id, after, stop_after, capture, client or self.client, headers_only))

    async def _read_segment_async(self, job_id: str, after: int, stop_after: int,
                                  capture: bool, client: UserClient, headers_only: bool) -> dict:
        started = time.monotonic()
        remaining = max(0, self.deadlines.get(job_id, started + config.JOB_OBSERVE_DEADLINE_SECONDS) - started)
        result = {"ids": [], "last_event_id": after, "terminal": None, "payload": {}, "status": None, "error": ""}
        headers = {"Accept": "text/event-stream"}
        if after:
            headers["Last-Event-ID"] = str(after)
        established = False
        waiting_headers = True
        headers_recorded = False
        http_started_at = time.time()
        first_event_ms = None
        terminal_ms = None
        logical_id = self.job_requests.get(job_id) if capture and not headers_only else None
        if logical_id:
            self.recorder.lifecycle(logical_id, job_id=job_id, event="subscription_started",
                                    subscription_started_at=http_started_at)
        self.recorder.sse_transition("waiting_headers")
        try:
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(base_url=client.base_url, cookies=client.client.cookies,
                                             headers=client.client.headers, follow_redirects=False,
                                             verify=client.ssl_context,
                                             timeout=httpx.Timeout(config.HTTP_TIMEOUT_SECONDS, read=config.SSE_IDLE_READ_TIMEOUT_SECONDS)) as stream_client:
                    async with stream_client.stream("GET", f"/api/agent/jobs/{job_id}/events", headers=headers) as response:
                        headers_at = time.time()
                        result["status"] = response.status_code
                        if logical_id:
                            self.recorder.lifecycle(logical_id, job_id=job_id, event="sse_headers",
                                                    sse_headers_at=headers_at,
                                                    sse_headers_status=response.status_code)
                        self.recorder.record_http(HttpSample(
                            "GET", "/api/agent/jobs/<id>/events", response.status_code,
                            (time.monotonic() - started) * 1000, started_at=http_started_at, finished_at=headers_at))
                        headers_recorded = True
                        self.recorder.sse_transition("waiting_headers", -1)
                        waiting_headers = False
                        if response.status_code != 200:
                            result["error"] = f"http_{response.status_code}"
                            return result
                        if not response.headers.get("content-type", "").startswith("text/event-stream"):
                            result["error"] = "invalid_event_stream_content_type"
                            return result
                        established = True
                        self.recorder.sse_transition("established")
                        if headers_only:
                            return result
                        async for event_type, event_id, payload in iter_events(response):
                            event_at, event_mono = time.time(), time.monotonic()
                            if first_event_ms is None:
                                first_event_ms = (event_mono - started) * 1000
                                if logical_id and job_id not in self.first_event_jobs:
                                    self.first_event_jobs.add(job_id)
                                    self.recorder.lifecycle(logical_id, job_id=job_id, event="first_event",
                                                            first_event_at=event_at)
                            if event_id is not None:
                                result["ids"].append(event_id)
                                result["last_event_id"] = event_id
                            if event_type in TERMINAL | {"interrupt"}:
                                result["terminal"] = event_type
                                result["terminal_received_at"] = event_at
                                result["terminal_received_mono"] = event_mono
                                terminal_ms = (event_mono - started) * 1000
                                if event_type == "interrupt":
                                    result["payload"] = payload
                                    if logical_id:
                                        self.recorder.lifecycle(logical_id, job_id=job_id, event="waiting_input",
                                                                interrupted_at=event_at, waiting_input=True, group="waiting_input")
                                elif logical_id and job_id not in self.terminals:
                                    self.terminals[job_id] = event_type
                                    self.recorder.lifecycle(logical_id, job_id=job_id, event="terminal_received",
                                        terminal_received_at=event_at,
                                        user_wait_ms=(event_mono - self.created_mono[job_id]) * 1000,
                                        received_event_ids=list(self.received.get(job_id, [])) + list(result["ids"]),
                                        final_status={"final_result": "succeeded", "error": "failed", "canceled": "canceled"}[event_type])
                                break
                            if stop_after and len(result["ids"]) >= stop_after:
                                break
                        if result["terminal"] is None and not (stop_after and len(result["ids"]) >= stop_after):
                            result["error"] = "stream_closed_without_terminal"
        except (TimeoutError, httpx.HTTPError) as exc:
            result["error"] = type(exc).__name__
        finally:
            if not headers_recorded:
                self.recorder.record_http(HttpSample(
                    "GET", "/api/agent/jobs/<id>/events", 0, (time.monotonic() - started) * 1000,
                    result["error"] or "headers_not_received", http_started_at, time.time()))
            if established:
                self.recorder.sse_transition("established", -1)
            if waiting_headers:
                self.recorder.sse_transition("waiting_headers", -1)
            self.recorder.sse_transition("closed")
            self.recorder.sse_transition("closed", -1)
            if capture and not headers_only:
                self.recorder.record_sse(SseSample(
                    job_id=job_id, first_event_ms=first_event_ms, terminal_event_ms=terminal_ms,
                    terminal_event=result["terminal"], last_event_id=result["last_event_id"],
                    disconnects=int(bool(result["error"])), reconnects=int(after > 0),
                    received_ids=list(result["ids"]), error=result["error"],
                ))
                self.received.setdefault(job_id, []).extend(result["ids"])
                logical_id = self.job_requests.get(job_id)
                if logical_id:
                    fields = {"received_event_ids": list(self.received[job_id])}
                    self.recorder.lifecycle(logical_id, job_id=job_id, event="sse", **fields)
        return result

    def check_idempotency(self) -> None:
        session = self.client.new_chat()
        key = new_idempotency_key()
        first_status, first = self._create_job(session, MESSAGE, self.file_id, key)
        job_id = str(first.get("job_id") or "")
        if first_status not in (200, 202) or job_id not in self.job_requests:
            self.record("幂等创建", False, f"首次未创建唯一新 Job，HTTP {first_status}")
            return
        second_status, second = self._create_job(session, MESSAGE, self.file_id, key,
                                                   payload_variant="same_payload_replay")
        third_status, _ = self._create_job(session, MESSAGE + " 换个问题", self.file_id, key,
                                           payload_variant="different_message")
        self.record("幂等创建", second_status == 200 and second.get("existing") is True
                    and str(second.get("job_id")) == job_id and third_status == 409,
                    f"首次 {first_status}；同键重放 {second_status}、existing={second.get('existing')}；不同参数 {third_status}")
        self._cancel(job_id, "结束幂等检查")

    def check_cancel_queued(self) -> None:
        self._check_cancel("queued")

    def check_cancel_running(self) -> None:
        self._check_cancel("running")

    def _check_cancel(self, target: str) -> None:
        name = f"取消 {target} Job（业务状态）"
        session = self.client.new_chat()
        status, payload = self._create_job(session, MESSAGE, self.file_id, new_idempotency_key())
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record(name, False, f"创建失败，HTTP {status}")
            return
        observed = self._active_status(session, job_id) == target if target == "queued" else self._wait_for_status(session, job_id, target, 30)
        key = new_idempotency_key()
        cancel_status, _ = self.client.cancel_job(job_id, key)
        replay_status, replay = self.client.cancel_job(job_id, key)
        history = self._job_status_in_history(session, job_id)
        if not observed:
            self.record(name, None, f"取消前未实际观察到 {target}；取消 HTTP {cancel_status}，历史 {history}。该分支未验证")
            return
        self.record(name, cancel_status == 202 and replay_status == 200 and replay.get("existing") is True and history == "canceled",
                    f"取消前观察到 {target}；取消 {cancel_status}、同键重放 {replay_status}，历史 {history}；执行释放待主库与 worker 证据")
        self._read_segment(job_id)

    def check_no_reversal(self) -> None:
        session = self.client.new_chat()
        status, payload = self._create_job(session, MESSAGE, self.file_id, new_idempotency_key())
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record("取消后状态不反转（短观察）", False, f"创建失败，HTTP {status}")
            return
        cancel_status, _ = self.client.cancel_job(job_id, new_idempotency_key())
        first = self._job_status_in_history(session, job_id)
        time.sleep(5.0)
        second = self._job_status_in_history(session, job_id)
        stream = self._read_segment(job_id)
        self.record("取消后状态不反转（短观察）", cancel_status == 202 and first == second == "canceled"
                    and stream["terminal"] == "canceled" and not stream["error"],
                    f"历史首次 {first}，5 秒后 {second}，重放终态 {stream['terminal']}；仅证明此短窗口，worker fencing 与前端迟到事件另验")

    def check_waiting_input(self) -> None:
        session = self.client.new_chat()
        status, payload = self._create_job(session, "请用 patients.csv 这个文件再做一次分析。", None, new_idempotency_key())
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record("waiting_input 恢复", False, f"创建失败，HTTP {status}")
            return
        first = self._read_segment(job_id)
        if first["terminal"] != "interrupt":
            self.record("waiting_input 恢复", None, f"未进入 interrupt，观测结果 {first['terminal']}，传输状态 {first['error'] or '正常'}；恢复分支未验证")
            self._cancel(job_id, "结束未触发的等待输入检查")
            return
        interaction = first["payload"].get("interaction") or {}
        question_id = str(first["payload"].get("question_id") or interaction.get("question_id") or "")
        if not question_id:
            self.record("waiting_input 恢复", False, "interrupt 缺少 question_id")
            self._cancel(job_id, "结束缺少问题标识的检查")
            return
        resume_status, _ = self.client.resume_job(job_id, question_id, "请改用文件库中已上传的 CSV。", new_idempotency_key())
        self.recorder.lifecycle(self.job_requests.get(job_id), job_id=job_id, event="resume", resumed=True, group="resumed")
        resumed = self._read_segment(job_id, first["last_event_id"])
        self.record("waiting_input 恢复", resume_status in (200, 202) and resumed["terminal"] == "final_result" and not resumed["error"],
                    f"恢复 HTTP {resume_status}，游标续传后 {resumed['terminal']}；error、canceled 或再次 interrupt 均不能判恢复成功")
        if resumed["terminal"] not in TERMINAL:
            self._cancel(job_id, "结束未完成的恢复检查")

    def check_cross_account_isolation(self) -> None:
        self.peer.login(self.peer_account.password)
        session = self.client.new_chat()
        status, payload = self._create_job(session, MESSAGE, self.file_id, new_idempotency_key())
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record("跨账号隔离", False, f"属主创建失败，HTTP {status}；不能用空 Job 的 404 证明隔离")
            return
        cross_session = self.peer.request("GET", "/api/load_session", params={"session": session})
        cross_events = self._read_segment(job_id, capture=False, client=self.peer, headers_only=True)
        owner_files, peer_files = self.client.list_files(), self.peer.list_files()
        owner_ids = {item["user_file_id"] for item in owner_files if item.get("user_file_id") is not None}
        peer_ids = {item["user_file_id"] for item in peer_files if item.get("user_file_id") is not None}
        passed = cross_session.status_code in (403, 404) and cross_events["status"] in (403, 404) and self.file_id in owner_ids and not owner_ids & peer_ids
        self.record("跨账号隔离", passed, f"跨账号会话 HTTP {cross_session.status_code}，事件响应头 HTTP {cross_events['status']}，交叉可见文件 {len(owner_ids & peer_ids)}")
        self._cancel(job_id, "结束隔离检查")

    def _active_status(self, session_id: str, job_id: str) -> str | None:
        for job in self.client.active_jobs(session_id):
            if str(job.get("job_id")) == job_id:
                return job.get("status")
        return None

    def _job_status_in_history(self, session_id: str, job_id: str) -> str | None:
        started = time.monotonic()
        payload = self.client.load_session(session_id)
        statuses = [(message.get("thinking_after") or {}).get("status")
                    for message in payload.get("messages") or [] if str(message.get("analysis_job_id")) == job_id]
        status = next((value for value in reversed(statuses) if value), None)
        self.recorder.lifecycle(self.job_requests.get(job_id), job_id=job_id, event="history",
                                history_check_ms=(time.monotonic() - started) * 1000)
        # completed 是展示阶段状态，也可能来自 interrupt，不能直接当主库 succeeded。
        return status

    def _wait_for_status(self, session_id: str, job_id: str, target: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._active_status(session_id, job_id) == target:
                return True
            time.sleep(min(1.0, max(0, deadline - time.monotonic())))
        return False

    def _cancel(self, job_id: str | None, reason: str) -> None:
        if not job_id:
            return
        try:
            status, _ = self.client.cancel_job(job_id, new_idempotency_key())
            if status not in (200, 202, 409):
                self.recorder.note(f"{reason}：HTTP {status}，执行释放未核对")
        except ApiError as exc:
            self.recorder.note(f"{reason}：{type(exc).__name__}，取消结果未知")


def run_recovery_suite(base_url: str, accounts: list[Account], server_baseline: dict | None = None,
                       run_id: str | None = None) -> dict:
    if len(accounts) < 2:
        raise RuntimeError("取消与恢复专项至少需要 2 个测试账号")
    chosen_run_id = run_id or (time.strftime("%Y%m%dT%H%M%S") + "-recovery")
    if Path(chosen_run_id).name != chosen_run_id or chosen_run_id in {".", ".."}:
        raise ValueError("run_id 必须是单一目录名")
    output_dir = config.RUNS_DIR / chosen_run_id
    if (output_dir / "jobs.jsonl").exists() or (output_dir / "summary.json").exists():
        raise ValueError("run_id 已有运行产物，请使用新的 run_id")
    recorder = Recorder(run_id=chosen_run_id, output_dir=output_dir)
    recorder.stage, recorder.users = "recovery", 2
    recorder.scenario = {"mode": "recovery",
                         "job_observe_deadline_seconds": config.JOB_OBSERVE_DEADLINE_SECONDS,
                         "http_timeout_seconds": config.HTTP_TIMEOUT_SECONDS,
                         "sse_idle_read_timeout_seconds": config.SSE_IDLE_READ_TIMEOUT_SECONDS,
                         "create_max_attempts": config.CREATE_MAX_ATTEMPTS}
    recorder.server_baseline = server_baseline
    recorder.started_at = time.time()
    recorder.note(f"客户端清单: {json.dumps(collect_client_manifest(), ensure_ascii=False)}")
    suite = RecoverySuite(base_url, accounts[0], accounts[1], recorder)
    checks = (suite.check_sse_resume, suite.check_idempotency, suite.check_cancel_queued,
              suite.check_cancel_running, suite.check_no_reversal, suite.check_waiting_input,
              suite.check_cross_account_isolation)
    try:
        suite.setup()
        for check in checks:
            try:
                check()
            except (ApiError, httpx.HTTPError) as exc:
                suite.record(check.__name__, False, f"真实请求异常 {type(exc).__name__}；本项未完成")
    except (ApiError, httpx.HTTPError) as exc:
        suite.record("专项前置条件", False, f"登录或建会话/上传失败：{type(exc).__name__}")
    finally:
        suite.close()
    recorder.finished_at = time.time()
    recorder.stop_submitting_at = recorder.finished_at
    recorder.observation_ended_at = recorder.finished_at
    summary = recorder.summary()
    summary["checks"] = suite.results
    conflicts = [row for row in summary.get("requests", []) if row.get("created_status") == 409]
    summary["special_counts"] = {"intentional_conflicts": sum(bool(row.get("intentional_conflict")) for row in conflicts),
                                 "unexpected_conflicts": sum(not row.get("intentional_conflict") for row in conflicts)}
    summary["special_evidence_gaps"] = list(SPECIAL_GAPS)
    summary["elapsed_seconds"] = round(recorder.finished_at - recorder.started_at, 2)
    summary["special_status"] = "incomplete"
    return summary
