"""普通用户 API 与 SSE 的低层客户端封装。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

import httpx

from runner.config import HTTP_TIMEOUT_SECONDS, SSE_IDLE_READ_TIMEOUT_SECONDS
from runner.metrics import HttpSample, Recorder, SseSample

TERMINAL_EVENTS = {"final_result", "error", "canceled"}
PROGRESS_EVENTS = {
    "node_start",
    "progress",
    "decision",
    "tool_call_start",
    "tool_call_result",
    "node_retry",
    "node_end",
}


def new_idempotency_key() -> str:
    """生成标准 UUID v4 幂等键。"""
    return str(uuid.uuid4())


class ApiError(RuntimeError):
    """非预期 HTTP 状态或响应结构。"""


@dataclass
class SseOutcome:
    """一次 SSE 订阅的终态结果。"""

    sample: SseSample
    terminal_event: str | None
    interrupt_payload: dict | None


class UserClient:
    """单个虚拟用户的 HTTP 会话与 SSE 订阅能力。"""

    def __init__(self, base_url: str, recorder: Recorder, username: str) -> None:
        self.base_url = base_url
        self.recorder = recorder
        self.username = username
        self.client = httpx.Client(
            base_url=base_url,
            # 读取超时按 SSE 空闲等待设置：一次读取允许等待多久，长排队 Job 不会被误判为断线。
            timeout=httpx.Timeout(
                HTTP_TIMEOUT_SECONDS, read=SSE_IDLE_READ_TIMEOUT_SECONDS
            ),
            follow_redirects=False,
            headers={"User-Agent": "causalagent-loadtest/1.0"},
        )
        self.csrf_token: str | None = None
        # 由执行器注入的并发信号量；SSE 订阅按档位上单独限流。
        self.sse_gate = None

    def close(self) -> None:
        self.client.close()

    # --- 基础请求 ---

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """执行一次请求并记录耗时、状态码和传输错误。"""
        started = time.monotonic()
        error = ""
        try:
            response = self.client.request(method, path, **kwargs)
            status = response.status_code
        except httpx.HTTPError as exc:
            status = 0
            error = type(exc).__name__
            response = None
        duration_ms = (time.monotonic() - started) * 1000
        self.recorder.record_http(
            HttpSample(
                method=method,
                path=_template_path(path),
                status=status,
                duration_ms=duration_ms,
                error=error,
            )
        )
        if response is None:
            raise ApiError(f"{method} {path} 传输失败: {error}")
        return response

    def json_request(self, method: str, path: str, **kwargs) -> tuple[int, dict]:
        """执行请求并返回状态码与 JSON 体；非 JSON 体返回空字典。"""
        response = self.request(method, path, **kwargs)
        return response.status_code, _as_dict(response)

    def raw_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """不写入统计的执行路径，供健康探测等旁路调用使用。"""
        try:
            return self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(f"{method} {path} 传输失败: {type(exc).__name__}") from exc


    # --- 认证与页面 ---

    def login(self, password: str) -> dict:
        """登录并保存 CSRF token。"""
        status, payload = self.json_request(
            "POST",
            "/api/login",
            json={"username": self.username, "password": password},
        )
        if status != 200 or not payload.get("success"):
            raise ApiError(f"登录失败 {self.username}: {status} {payload.get('error')}")
        self.csrf_token = payload.get("csrf_token")
        return payload

    def check_auth(self) -> dict:
        """读取登录状态与 CSRF token。"""
        status, payload = self.json_request("GET", "/api/check_auth")
        if status == 200:
            self.csrf_token = payload.get("csrf_token") or self.csrf_token
        return payload

    def site_page(self) -> int:
        """访问官网入口页面。"""
        return self.request("GET", "/").status_code

    def dashboard_page(self) -> int:
        """访问普通用户应用页面。"""
        return self.request("GET", "/dashboard").status_code

    # --- 会话 ---

    def new_chat(self) -> str:
        """创建一个新的 Session。"""
        status, payload = self.json_request("POST", "/api/new_chat")
        if status != 200 or not payload.get("success"):
            raise ApiError(f"new_chat 失败: {status} {payload}")
        return payload["new_session_id"]

    def sessions(self) -> list:
        """读取未归档会话列表。"""
        status, payload = self.json_request("GET", "/api/sessions")
        if status != 200:
            raise ApiError(f"sessions 失败: {status} {payload}")
        return payload.get("data") or []

    def load_session(self, session_id: str) -> dict:
        """读取会话历史。"""
        status, payload = self.json_request(
            "GET", "/api/load_session", params={"session": session_id}
        )
        if status != 200:
            raise ApiError(f"load_session 失败: {status} {payload}")
        return payload

    # --- 文件 ---

    def list_files(self) -> list:
        """读取文件库逻辑记录。"""
        status, payload = self.json_request("GET", "/api/files")
        if status != 200:
            raise ApiError(f"files 失败: {status} {payload}")
        return payload.get("data") or []

    def upload_csv(self, filename: str, content: bytes) -> int:
        """上传一个 CSV 并返回 user_file_id。"""
        files = {"file": (filename, content, "text/csv")}
        status, payload = self.json_request(
            "POST", "/api/upload_file", files=files
        )
        if status != 200 or not payload.get("success"):
            raise ApiError(f"upload_file 失败: {status} {payload.get('error')}")
        return int(payload["user_file_id"])

    # --- Job ---

    def create_job(
        self,
        session_id: str,
        message: str,
        file_id: int | None,
        idempotency_key: str,
        web_search_enabled: bool = False,
    ) -> tuple[int, dict]:
        """创建分析 Job；返回状态码与响应体。"""
        body: dict = {
            "message": message,
            "session_id": session_id,
            "web_search_enabled": web_search_enabled,
        }
        if file_id is not None:
            body["input_user_file_id"] = file_id
        return self.json_request(
            "POST",
            "/api/agent/jobs",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
        )

    def active_jobs(self, session_id: str | None = None) -> list:
        """读取当前用户的活动 Job。"""
        params = {"session_id": session_id} if session_id else None
        status, payload = self.json_request(
            "GET", "/api/agent/jobs/active", params=params
        )
        if status != 200 or not payload.get("success"):
            raise ApiError(f"active jobs 失败: {status} {payload}")
        return payload.get("jobs") or []

    def cancel_job(self, job_id: str, idempotency_key: str) -> tuple[int, dict]:
        """取消活动 Job。"""
        return self.json_request(
            "POST",
            f"/api/agent/jobs/{job_id}/cancel",
            headers={"Idempotency-Key": idempotency_key},
        )

    def resume_job(
        self, job_id: str, question_id: str, answer: str, idempotency_key: str
    ) -> tuple[int, dict]:
        """恢复 waiting_input Job。"""
        return self.json_request(
            "POST",
            f"/api/agent/jobs/{job_id}/resume",
            json={"question_id": question_id, "answer": answer},
            headers={"Idempotency-Key": idempotency_key},
        )

    def count_job_events(self, job_id: str) -> int:
        """完整读取事件流并统计事件数量，用于与订阅收到的事件集合比对。"""
        count = 0
        with self.client.stream(
            "GET",
            f"/api/agent/jobs/{job_id}/events",
            headers={"Accept": "text/event-stream"},
        ) as response:
            if response.status_code != 200:
                return -1
            for event_type, _event_id, _payload in _iter_sse(response):
                if event_type in TERMINAL_EVENTS or event_type == "interrupt":
                    break
                count += 1
        return count

    # --- SSE ---

    def subscribe_job_events(
        self,
        job_id: str,
        after_event_id: int = 0,
        allow_reconnect: bool = True,
    ) -> SseOutcome:
        """订阅 Job 事件流，必要时用 Last-Event-ID 重连直到终态。"""
        if self.sse_gate is not None:
            with self.sse_gate:
                return self._subscribe(job_id, after_event_id, allow_reconnect)
        return self._subscribe(job_id, after_event_id, allow_reconnect)

    def _subscribe(
        self,
        job_id: str,
        after_event_id: int,
        allow_reconnect: bool,
    ) -> SseOutcome:
        """实际订阅循环；SSE 并发额度由调用方负责。"""
        received_ids: list[int] = []
        disconnects = 0
        reconnects = 0
        first_event_ms: float | None = None
        terminal_event_ms: float | None = None
        terminal_event: str | None = None
        interrupt_payload: dict | None = None
        error = ""
        cursor = after_event_id
        started = time.monotonic()
        stream_started_at = started

        while True:
            headers = {"Accept": "text/event-stream"}
            if cursor:
                headers["Last-Event-ID"] = str(cursor)
            saw_terminal = False
            stream_started = time.monotonic()
            try:
                with self.client.stream(
                    "GET", f"/api/agent/jobs/{job_id}/events", headers=headers
                ) as response:
                    if response.status_code != 200:
                        error = f"SSE status {response.status_code}"
                        break
                    for event_type, event_id, payload in _iter_sse(response):
                        if event_id is not None:
                            cursor = event_id
                            received_ids.append(event_id)
                        if first_event_ms is None and event_type in PROGRESS_EVENTS | TERMINAL_EVENTS:
                            first_event_ms = (time.monotonic() - stream_started_at) * 1000
                        if event_type in TERMINAL_EVENTS:
                            terminal_event = event_type
                            terminal_event_ms = (time.monotonic() - stream_started_at) * 1000
                            saw_terminal = True
                            break
                        if event_type == "interrupt":
                            interrupt_payload = payload
                            terminal_event_ms = (time.monotonic() - stream_started_at) * 1000
                            saw_terminal = True
                            break
            except httpx.HTTPError as exc:
                error = type(exc).__name__

            if saw_terminal:
                break
            if time.monotonic() - stream_started < 0.5:
                error = error or "stream_closed_without_terminal"
                break
            disconnects += 1
            if not allow_reconnect:
                break
            reconnects += 1
            time.sleep(0.5)

        sample = SseSample(
            job_id=job_id,
            first_event_ms=first_event_ms,
            terminal_event_ms=terminal_event_ms,
            terminal_event=terminal_event,
            last_event_id=cursor,
            disconnects=disconnects,
            reconnects=reconnects,
            received_ids=received_ids,
            error=error,
        )
        self.recorder.record_sse(sample)
        return SseOutcome(
            sample=sample,
            terminal_event=terminal_event,
            interrupt_payload=interrupt_payload,
        )


def _iter_sse(response: httpx.Response):
    """把 SSE 行流解析为 (event, id, data) 三元组。"""
    event_type = ""
    event_id: int | None = None
    data_lines: list[str] = []
    for raw_line in response.iter_lines():
        line = raw_line.rstrip()
        if not line:
            if event_type or data_lines:
                yield event_type or "message", event_id, _decode_payload(data_lines)
            event_type = ""
            event_id = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip()
        if field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            try:
                event_id = int(value)
            except ValueError:
                event_id = None
    if event_type or data_lines:
        yield event_type or "message", event_id, _decode_payload(data_lines)


def _decode_payload(data_lines: list[str]) -> dict:
    """把 data 行合并为 JSON；失败时返回摘要字典。"""
    if not data_lines:
        return {}
    joined = "\n".join(data_lines)
    try:
        payload = json.loads(joined)
    except json.JSONDecodeError:
        return {"raw": joined[:200]}
    return payload if isinstance(payload, dict) else {"data": payload}


def _template_path(path: str) -> str:
    """把路径中的 UUID 替换成占位符，便于按接口聚合。"""
    parts = [part for part in path.split("/") if part]
    rebuilt: list[str] = []
    for part in parts:
        if len(part) == 36 and part.count("-") == 4:
            rebuilt.append("<id>")
        else:
            rebuilt.append(part)
    return "/" + "/".join(rebuilt)


def _as_dict(response: httpx.Response) -> dict:
    """把响应体解析为字典；非对象 JSON 与非法 JSON 返回空字典。"""
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {"data": payload}
    return payload
