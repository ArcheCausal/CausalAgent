"""真实 API 专项：有限观察 SSE、幂等、取消和隔离；服务器与前端证据独立列出。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import httpx

from runner import config
from runner.accounts import Account
from runner.api import ApiError, UserClient, new_idempotency_key
from runner.flows import MESSAGE, build_csv
from runner.manifest import collect_client_manifest
from runner.metrics import HttpSample, Recorder, SseSample
from runner.sse import iter_events, PROGRESS_EVENTS
from runner.stages import make_run_id

TERMINAL = {"final_result", "error", "canceled"}
SPECIAL_GAPS = [
    "取消执行释放：需主库 execution_state、execution_released_at、释放原因和 worker 槽位前后证据；取消 API 成功仅证明业务取消。",
    "算法进程终止：需真实 invocation 与目标 PID、取消响应、PID 消失及重复取消证据；本专项不执行进程终止操作。",
    "前端阶段：需真实浏览器刷新恢复、waiting_input 展示、取消后迟到事件不反转及页面交互证据；API 检查不证明前端通过。",
]


class RecoverySuite:
    """顺序执行真实 API 检查，每项只判定自己实际取得的证据。"""

    def __init__(self, base_url: str, account: Account, peer_account: Account, recorder: Recorder) -> None:
        if account.username == peer_account.username or (account.user_id is not None
                and peer_account.user_id is not None and account.user_id == peer_account.user_id):
            raise ValueError("跨账号隔离必须使用两个不同的真实账号")
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
        self.segment_counts: dict[str, int] = {}
        self.cancel_attempts: dict[str, list[dict]] = {}

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
        data = build_csv(90)
        self.recorder.record_input(data, rows=40, columns=5, seed=1090, user_index=1, scenario="recovery")
        self.file_id = self.client.upload_csv("loadtest_recovery.csv", data)

    def submission_stop_reason(self) -> str | None:
        configured = os.environ.get("LOADTEST_STOP_FILE")
        if configured:
            path = Path(configured)
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else None
            except (OSError, ValueError):
                return "protection_signal_unreadable"
            if isinstance(payload, dict) and payload.get("run_id") == self.recorder.run_id and payload.get("stop_submitting") is True:
                self.recorder.scenario["protection_stop"] = {"reason": str(payload.get("reason") or "controlled")[:160],
                    "at": time.time(), "source": "stop_file"}
                if self.recorder.stop_submitting_at is None:
                    self.recorder.stop_submitting_at = time.time()
                return "resource_protection"
        return None

    def _create_job(self, session_id: str, message: str, file_id: int | None, key: str,
                    payload_variant: str = "original", *, group: str = "recovery",
                    observation_reason: str = "recovery_lifecycle",
                    requires_terminal: bool = True, requires_full_stream: bool = True) -> tuple[int, dict]:
        logical_id = new_idempotency_key()
        started_at, started = time.time(), time.monotonic()
        deadline = started + config.JOB_OBSERVE_DEADLINE_SECONDS
        fingerprint = hashlib.sha256(json.dumps({"session_id": session_id, "message": message,
                                                 "input_user_file_id": file_id, "web_search_enabled": False},
                                                sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        self.recorder.register_request(
            logical_id, created_request_at=started_at, group=group, session_id=session_id,
            business_class="cancellation" if group in {"recovery_cancel", "recovery_idempotency", "recovery_isolation"} else "ordinary",
            requires_terminal=requires_terminal and payload_variant == "original",
            requires_full_stream=requires_full_stream and payload_variant == "original",
            observation_reason=observation_reason,
            username=self.account.username, user_index=1, idempotency_key=key,
            input_user_file_id=file_id,
            payload_variant=payload_variant, payload_fingerprint=fingerprint,
            intentional_conflict=payload_variant == "different_message",
            job_id=None, terminal_received_at=None, user_wait_ms=None, wait_lower_bound_ms=None,
            history_check_ms=None, received_event_ids=[],
        )
        stopped = self.submission_stop_reason()
        if stopped is None and payload_variant == "original" and not self.recorder.reserve_submission():
            stopped = "task_budget"
        if stopped:
            self.recorder.lifecycle(logical_id, event="not_sent", admission="not_sent",
                created_request_at=None, not_sent_reason=stopped, observation_finished_at=time.time(),
                observation_end_reason="intentional_stop")
            return 0, {}
        self.recorder.bump("job_create_attempts")
        errors, uncertain = [], False
        status, payload = 0, {}
        for attempt in range(1, config.CREATE_MAX_ATTEMPTS + 1):
            stopped = self.submission_stop_reason()
            if stopped:
                if attempt == 1:
                    self.recorder.lifecycle(logical_id, event="not_sent", admission="not_sent",
                        created_request_at=None, not_sent_reason=stopped, observation_finished_at=time.time(),
                        observation_end_reason="intentional_stop")
                    return 0, {}
                break
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
        if payload_variant != "original" or admission not in {"accepted", "replay"}:
            self.recorder.lifecycle(logical_id, event="observation_finished",
                observation_finished_at=time.time(),
                observation_end_reason="transport_error" if admission == "acceptance_unknown" else "intentional_stop")
        return status, payload

    def check_sse_resume(self) -> None:
        status, payload = self._create_job(self.session_id, MESSAGE, self.file_id, new_idempotency_key(),
            group="recovery_sse", observation_reason="disconnect_and_resume")
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
        self.recorder.lifecycle(job_id=job_id, event="recovery_reconnect_evidence",
            actual_disconnects=int(first["intentional_disconnect"]),
            reconnect_attempts=int(second["reconnect_attempted"]),
            successful_reconnects=int(second["reconnected"]),
            reconnect_segment_event_count=len(second["ids"]))
        observed = first["ids"] + second["ids"]
        # 全局自增 ID 可以跨 Job 跳号；只能用同 Job 的完整重放集合核对遗漏。
        full = self._read_segment(job_id, 0, capture=False) if second["terminal"] in TERMINAL else None
        complete = bool(full and full["terminal"] in TERMINAL and not full["error"])
        missing = sorted(set(full["ids"]) - set(observed)) if complete else None
        unexpected = sorted(set(observed) - set(full["ids"])) if complete else None
        duplicate_count = len(observed) - len(set(observed))
        ordered = all(a < b for a, b in zip(observed, observed[1:]))
        passed = (first["intentional_disconnect"] and second["reconnected"]
                  and complete and bool(full["ids"]) and full["terminal"] == second["terminal"]
                  and not second["error"] and not missing and not unexpected and duplicate_count == 0 and ordered)
        self.record("SSE 断线恢复", bool(passed) if complete else None,
                    f"首段收到 {len(first['ids'])} 个事件；重连成功 {int(second['reconnected'])} 次，"
                    f"重连段收到 {len(second['ids'])} 个事件；完整重放可核对={complete}；"
                    f"漏事件={len(missing) if missing is not None else '未采集'}，重复={duplicate_count}，严格递增={ordered}，"
                    f"重连终态={second['terminal']}；观察结束原因={second['observation_end_reason']}；"
                    f"传输或观察错误={second['error'] or '无'}。完整重放只证明本次流恢复，主库事件集核对另行采集。")
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
        owned = capture and not headers_only and client is self.client
        reconnect_attempted = owned and self.segment_counts.get(job_id, 0) > 0
        if owned:
            self.segment_counts[job_id] = self.segment_counts.get(job_id, 0) + 1
        result = {"ids": [], "last_event_id": after, "terminal": None, "payload": {}, "status": None, "error": "",
                  "intentional_disconnect": False, "reconnect_attempted": reconnect_attempted,
                  "reconnected": False, "observation_end_reason": None}
        request_id = str(uuid.uuid4())
        headers = {"Accept": "text/event-stream", "X-Request-ID": request_id}
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
            if self.recorder.requests[logical_id].get("observation_started_at") is None:
                self.recorder.lifecycle(logical_id, event="observation_started", observation_started_at=http_started_at)
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
                            (time.monotonic() - started) * 1000, started_at=http_started_at, finished_at=headers_at,
                            request_id=request_id, client_request_id=request_id,
                            response_request_id=response.headers.get("X-Request-ID"), run_id=self.recorder.run_id))
                        headers_recorded = True
                        self.recorder.sse_transition("waiting_headers", -1)
                        waiting_headers = False
                        if response.status_code != 200:
                            result["error"] = f"http_{response.status_code}"
                            result["observation_end_reason"] = "transport_error"
                            return result
                        if not response.headers.get("content-type", "").startswith("text/event-stream"):
                            result["error"] = "invalid_event_stream_content_type"
                            result["observation_end_reason"] = "transport_error"
                            return result
                        established = True
                        self.recorder.sse_transition("established")
                        result["reconnected"] = reconnect_attempted
                        if headers_only:
                            result["observation_end_reason"] = "intentional_stop"
                            return result
                        async for event_type, event_id, payload in iter_events(response):
                            event_at, event_mono = time.time(), time.monotonic()
                            if first_event_ms is None and event_type in PROGRESS_EVENTS | TERMINAL:
                                first_event_ms = (event_mono - started) * 1000
                                if logical_id and job_id not in self.first_event_jobs:
                                    self.first_event_jobs.add(job_id)
                                    self.recorder.lifecycle(logical_id, job_id=job_id, event="first_event",
                                                            first_event_at=event_at)
                            if event_id is not None:
                                result["ids"].append(event_id)
                                result["last_event_id"] = event_id
                            if event_type in TERMINAL | {"interrupt"}:
                                result["observation_end_reason"] = "interrupt" if event_type == "interrupt" else "terminal"
                                result["terminal"] = event_type
                                result["terminal_received_at"] = event_at
                                result["terminal_received_mono"] = event_mono
                                terminal_ms = (event_mono - started) * 1000
                                if event_type == "interrupt":
                                    result["observation_end_reason"] = "interrupt"
                                    result["payload"] = payload
                                    if logical_id:
                                        self.recorder.lifecycle(logical_id, job_id=job_id, event="waiting_input",
                                                                interrupted_at=event_at, waiting_input=True)
                                elif logical_id and job_id not in self.terminals:
                                    result["observation_end_reason"] = "terminal"
                                    self.terminals[job_id] = event_type
                                    self.recorder.lifecycle(logical_id, job_id=job_id, event="terminal_received",
                                        terminal_received_at=event_at,
                                        user_wait_ms=(event_mono - self.created_mono[job_id]) * 1000,
                                        received_event_ids=list(self.received.get(job_id, [])) + list(result["ids"]),
                                        final_status={"final_result": "succeeded", "error": "failed", "canceled": "canceled"}[event_type])
                                break
                            if stop_after and len(result["ids"]) >= stop_after:
                                result["intentional_disconnect"] = True
                                result["observation_end_reason"] = "intentional_stop"
                                break
                        if result["terminal"] is None and not (stop_after and len(result["ids"]) >= stop_after):
                            result["error"] = "stream_closed_without_terminal"
                            result["observation_end_reason"] = "transport_error"
        except (TimeoutError, httpx.HTTPError) as exc:
            result["error"] = type(exc).__name__
            result["observation_end_reason"] = ("idle_timeout" if established and isinstance(exc, httpx.ReadTimeout)
                else "total_deadline" if isinstance(exc, TimeoutError) else "transport_error")
        except Exception as exc:
            result["error"] = type(exc).__name__
            result["observation_end_reason"] = "internal_error"
            raise
        finally:
            if not headers_recorded:
                self.recorder.record_http(HttpSample(
                    "GET", "/api/agent/jobs/<id>/events", 0, (time.monotonic() - started) * 1000,
                    result["error"] or "headers_not_received", http_started_at, time.time(),
                    request_id=request_id, client_request_id=request_id, run_id=self.recorder.run_id))
            if established:
                self.recorder.sse_transition("established", -1)
            if waiting_headers:
                self.recorder.sse_transition("waiting_headers", -1)
            self.recorder.sse_transition("closed")
            self.recorder.sse_transition("closed", -1)
            if capture and not headers_only:
                self.recorder.record_sse(SseSample(
                    job_id=job_id, first_event_ms=first_event_ms,
                    terminal_event_ms=terminal_ms if result["terminal"] in TERMINAL else None,
                    terminal_event=result["terminal"] if result["terminal"] in TERMINAL else None,
                    last_event_id=result["last_event_id"],
                    disconnects=int(established and (bool(result["error"]) or result["intentional_disconnect"])),
                    reconnects=int(result["reconnected"]),
                    received_ids=list(result["ids"]), error=result["error"],
                    intentional_disconnects=int(result["intentional_disconnect"]),
                    reconnect_attempts=int(reconnect_attempted),
                    connection_attempts=1,
                    reconnect_event_count=len(result["ids"]) if result["reconnected"] else 0,
                    observation_end_reason=result["observation_end_reason"],
                    started_at=http_started_at, finished_at=time.time(),
                ))
                self.received.setdefault(job_id, []).extend(result["ids"])
                logical_id = self.job_requests.get(job_id)
                if logical_id:
                    fields = {"received_event_ids": list(self.received[job_id])}
                    self.recorder.lifecycle(logical_id, job_id=job_id, event="sse", **fields)
                    if result["terminal"] in TERMINAL or result["error"]:
                        self.recorder.lifecycle(logical_id, event="observation_finished",
                            observation_finished_at=time.time(), observation_end_reason=result["observation_end_reason"])
        return result

    def check_idempotency(self) -> None:
        session = self.client.new_chat()
        key = new_idempotency_key()
        first_status, first = self._create_job(session, MESSAGE, self.file_id, key,
            group="recovery_idempotency", observation_reason="idempotency_check_then_cleanup",
            requires_terminal=False, requires_full_stream=False)
        job_id = str(first.get("job_id") or "")
        if first_status not in (200, 202) or job_id not in self.job_requests:
            self.record("幂等创建", False, f"首次未创建唯一新 Job，HTTP {first_status}")
            return
        try:
            second_status, second = self._create_job(session, MESSAGE, self.file_id, key,
                payload_variant="same_payload_replay", group="recovery_idempotency",
                observation_reason="same_key_replay")
            third_status, _ = self._create_job(session, MESSAGE + " 换个问题", self.file_id, key,
                payload_variant="different_message", group="recovery_idempotency",
                observation_reason="intentional_payload_conflict")
            self.record("幂等创建", second_status == 200 and second.get("existing") is True
                        and str(second.get("job_id")) == job_id and third_status == 409,
                        f"首次 {first_status}；同键重放 {second_status}、existing={second.get('existing')}；不同参数 {third_status}")
        finally:
            self._cancel(job_id, "结束幂等检查")

    def check_cancel_queued(self) -> None:
        self._check_cancel("queued")

    def check_cancel_running(self) -> None:
        self._check_cancel("running")

    def _check_cancel(self, target: str) -> None:
        name = f"取消 {target} Job（业务状态）"
        session = self.client.new_chat()
        status, payload = self._create_job(session, MESSAGE, self.file_id, new_idempotency_key(),
            group="recovery_cancel", observation_reason=f"cancel_{target}", requires_full_stream=False)
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record(name, False, f"创建失败，HTTP {status}")
            return
        observed = self._wait_for_status(session, job_id, target, 30)
        self.recorder.lifecycle(job_id=job_id, event="cancel_branch_observed",
            cancel_target_status=target, cancel_target_observed=observed)
        if not observed:
            self.record(name, None, f"取消前未实际观察到 {target}；该分支未触发，后续仅清理本次真实 Job")
            self._cancel(job_id, f"清理未触发的 {target} 取消检查")
            return
        key = new_idempotency_key()
        cancel_status, _ = self._cancel_request(job_id, key, "branch_check")
        replay_status, replay = self._cancel_request(job_id, key, "idempotency_replay")
        history = self._job_status_in_history(session, job_id)
        stream = self._read_segment(job_id)
        self.record(name, cancel_status == 202 and replay_status == 200 and replay.get("existing") is True
                    and history == "canceled" and stream["terminal"] == "canceled" and not stream["error"],
                    f"取消前观察到 {target}；取消 {cancel_status}、同键重放 {replay_status}，历史 {history}，"
                    f"流终态 {stream['terminal']}；执行释放待主库与 worker 证据")

    def check_no_reversal(self) -> None:
        session = self.client.new_chat()
        status, payload = self._create_job(session, MESSAGE, self.file_id, new_idempotency_key(),
            group="recovery_cancel", observation_reason="cancel_no_reversal", requires_full_stream=False)
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record("取消后状态不反转（短观察）", False, f"创建失败，HTTP {status}")
            return
        cancel_status, _ = self._cancel_request(job_id, new_idempotency_key(), "no_reversal")
        first = self._job_status_in_history(session, job_id)
        time.sleep(5.0)
        second = self._job_status_in_history(session, job_id)
        stream = self._read_segment(job_id)
        self.record("取消后状态不反转（短观察）", cancel_status == 202 and first == second == "canceled"
                    and stream["terminal"] == "canceled" and not stream["error"],
                    f"历史首次 {first}，5 秒后 {second}，重放终态 {stream['terminal']}；仅证明此短窗口，worker fencing 与前端迟到事件另验")

    def check_waiting_input(self) -> None:
        session = self.client.new_chat()
        status, payload = self._create_job(session, "请用 patients.csv 这个文件再做一次分析。", None, new_idempotency_key(),
            group="recovery_waiting_input", observation_reason="waiting_input_resume")
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
        self.recorder.lifecycle(self.job_requests.get(job_id), job_id=job_id, event="resume",
                                resumed=resume_status in (200, 202), resume_status=resume_status)
        resumed = self._read_segment(job_id, first["last_event_id"])
        self.record("waiting_input 恢复", resume_status in (200, 202) and resumed["terminal"] == "final_result" and not resumed["error"],
                    f"恢复 HTTP {resume_status}，游标续传后 {resumed['terminal']}；error、canceled 或再次 interrupt 均不能判恢复成功")
        if resumed["terminal"] not in TERMINAL:
            self._cancel(job_id, "结束未完成的恢复检查")

    def check_cross_account_isolation(self) -> None:
        self.peer.login(self.peer_account.password)
        session = self.client.new_chat()
        status, payload = self._create_job(session, MESSAGE, self.file_id, new_idempotency_key(),
            group="recovery_isolation", observation_reason="cross_account_isolation_then_cleanup",
            requires_terminal=False, requires_full_stream=False)
        job_id = str(payload.get("job_id") or "")
        if status not in (200, 202) or job_id not in self.job_requests:
            self.record("跨账号隔离", False, f"属主创建失败，HTTP {status}；不能用空 Job 的 404 证明隔离")
            return
        try:
            owner_session = self.client.load_session(session)
            owner_events = self._read_segment(job_id, capture=False, headers_only=True)
            cross_session = self.peer.request("GET", "/api/load_session", params={"session": session})
            cross_events = self._read_segment(job_id, capture=False, client=self.peer, headers_only=True)
            owner_files, peer_files = self.client.list_files(), self.peer.list_files()
            owner_ids = {item["user_file_id"] for item in owner_files if item.get("user_file_id") is not None}
            peer_ids = {item["user_file_id"] for item in peer_files if item.get("user_file_id") is not None}
            owner_job_visible = any(str(message.get("analysis_job_id")) == job_id
                for message in owner_session.get("messages", []))
            evidence = {"session_id": session, "job_id": job_id, "user_file_id": self.file_id,
                "owner_session_accessible": owner_session.get("success") is True,
                "owner_job_visible": owner_job_visible, "owner_events_status": owner_events["status"],
                "owner_file_visible": self.file_id in owner_ids,
                "peer_session_status": cross_session.status_code, "peer_events_status": cross_events["status"],
                "peer_file_visible": self.file_id in peer_ids}
            self.recorder.lifecycle(job_id=job_id, event="cross_account_isolation", isolation_evidence=evidence)
            passed = (evidence["owner_session_accessible"] and owner_job_visible and owner_events["status"] == 200
                and cross_session.status_code in (403, 404) and cross_events["status"] in (403, 404)
                and self.file_id in owner_ids and not owner_ids & peer_ids)
            self.record("跨账号隔离", passed, f"属主真实会话及 Job 可见={owner_job_visible}，属主事件 HTTP {owner_events['status']}；"
                f"跨账号会话 HTTP {cross_session.status_code}，事件响应头 HTTP {cross_events['status']}，"
                f"属主文件可见={self.file_id in owner_ids}，交叉可见文件 {len(owner_ids & peer_ids)}")
        finally:
            self._cancel(job_id, "结束隔离检查")

    def _active_status(self, session_id: str, job_id: str) -> str | None:
        for job in self.client.active_jobs(session_id):
            if str(job.get("job_id")) == job_id:
                self.recorder.lifecycle(job_id=job_id, event="active_status_observed",
                    active_status_observed=job.get("status"), active_status_observed_at=time.time())
                return job.get("status")
        return None

    def _job_status_in_history(self, session_id: str, job_id: str) -> str | None:
        started = time.monotonic()
        payload = self.client.load_session(session_id)
        statuses = [(message.get("thinking_after") or {}).get("status")
                    for message in payload.get("messages") or [] if str(message.get("analysis_job_id")) == job_id]
        status = next((value for value in reversed(statuses) if value), None)
        self.recorder.lifecycle(self.job_requests.get(job_id), job_id=job_id, event="history",
                                history_check_ms=(time.monotonic() - started) * 1000,
                                history_observed_status=status, history_observed_at=time.time(),
                                business_canceled=True if status == "canceled" else None)
        # completed 是展示阶段状态，也可能来自 interrupt，不能直接当主库 succeeded。
        return status

    def _wait_for_status(self, session_id: str, job_id: str, target: str, timeout: float) -> bool:
        deadline = min(time.monotonic() + timeout, self.deadlines.get(job_id, float("inf")))
        while time.monotonic() < deadline:
            status = self._active_status(session_id, job_id)
            if status == target:
                return True
            if status is None or (target == "queued" and status != "queued"):
                return False
            time.sleep(min(1.0, max(0, deadline - time.monotonic())))
        return False

    def _cancel(self, job_id: str | None, reason: str) -> None:
        if not job_id:
            return
        try:
            status, _ = self._cancel_request(job_id, new_idempotency_key(), reason)
            if status not in (200, 202, 409):
                self.recorder.note(f"{reason}：HTTP {status}，执行释放未核对")
        except ApiError as exc:
            self.recorder.note(f"{reason}：{type(exc).__name__}，取消结果未知")
        finally:
            logical = self.job_requests.get(job_id)
            if logical and self.recorder.requests[logical].get("observation_finished_at") is None:
                self.recorder.lifecycle(logical, event="observation_finished",
                    observation_finished_at=time.time(), observation_end_reason="intentional_stop")

    def _cancel_request(self, job_id: str, key: str, reason: str) -> tuple[int, dict]:
        """保存 API 事实，业务状态和执行释放由各自证据判定。"""
        attempt = {"idempotency_key": key, "reason": reason, "started_at": time.time(),
                   "finished_at": None, "http_status": None, "existing": None,
                   "api_status": None, "error": None}
        try:
            status, payload = self.client.cancel_job(job_id, key)
            attempt.update(http_status=status, existing=payload.get("existing"), api_status=payload.get("status"))
            return status, payload
        except ApiError as exc:
            attempt["error"] = type(exc).__name__
            raise
        finally:
            attempt["finished_at"] = time.time()
            self.cancel_attempts.setdefault(job_id, []).append(attempt)
            self.recorder.lifecycle(job_id=job_id, event="cancel_api_result",
                cancel_attempts=list(self.cancel_attempts[job_id]))


def run_recovery_suite(base_url: str, accounts: list[Account], server_baseline: dict | None = None,
                       run_id: str | None = None) -> dict:
    if len(accounts) < 2:
        raise RuntimeError("取消与恢复专项至少需要 2 个测试账号")
    chosen_run_id = run_id or make_run_id("recovery")
    if Path(chosen_run_id).name != chosen_run_id or chosen_run_id in {".", ".."}:
        raise ValueError("run_id 必须是单一目录名")
    output_dir = config.RUNS_DIR / chosen_run_id
    if (output_dir / "jobs.jsonl").exists() or (output_dir / "summary.json").exists():
        raise ValueError("run_id 已有运行产物，请使用新的 run_id")
    recorder = Recorder(run_id=chosen_run_id, output_dir=output_dir)
    recorder.stage, recorder.users = "recovery", 2
    from runner.rounds import declared_parameters
    parameters = declared_parameters("recovery", chosen_run_id)
    budget = parameters.get("task_budget", 7)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ValueError("invalid_task_budget")
    recorder.scenario = {"mode": "recovery", "task_budget": budget,
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
            if suite.submission_stop_reason():
                suite.record(check.__name__, None, "资源保护已停止新提交；未执行后续专项，原任务仍独立清理")
                break
            previous_jobs = set(suite.job_requests)
            try:
                check()
            except (ApiError, httpx.HTTPError) as exc:
                suite.record(check.__name__, False, f"真实请求异常 {type(exc).__name__}；本项未完成")
            except Exception as exc:
                suite.record(check.__name__, False, f"客户端内部异常 {type(exc).__name__}；本项未完成")
            finally:
                # 每项独立收尾；幂等或隔离请求失败不得跳过取消，也不影响下一项。
                for job_id in set(suite.job_requests) - previous_jobs:
                    logical = suite.job_requests[job_id]
                    if not suite.cancel_attempts.get(job_id) and job_id not in suite.terminals:
                        suite._cancel(job_id, f"{check.__name__} 独立清理")
                    if recorder.requests[logical].get("observation_finished_at") is None:
                        recorder.lifecycle(logical, event="observation_finished",
                            observation_finished_at=time.time(), observation_end_reason="intentional_stop")
    except (ApiError, httpx.HTTPError) as exc:
        suite.record("专项前置条件", False, f"登录或建会话/上传失败：{type(exc).__name__}")
    finally:
        suite.close()
    recorder.finished_at = time.time()
    if recorder.stop_submitting_at is None:
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
