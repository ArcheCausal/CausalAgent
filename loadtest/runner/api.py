"""普通用户 API 与 SSE 的低层客户端封装。"""

from __future__ import annotations

import asyncio
import json
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from runner import config
from runner.config import HTTP_TIMEOUT_SECONDS
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

_SSL_CONTEXT: ssl.SSLContext | None = None
_SSL_CONTEXT_LOCK = threading.Lock()


def shared_ssl_context() -> ssl.SSLContext:
    """进程内只加载一次系统信任证书，避免阻塞每条异步 SSE 的事件循环。"""
    global _SSL_CONTEXT
    with _SSL_CONTEXT_LOCK:
        if _SSL_CONTEXT is None:
            _SSL_CONTEXT = ssl.create_default_context()
        return _SSL_CONTEXT


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
    terminal_received_at: float | None = None
    errors: list[str] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)


class UserClient:
    """单个虚拟用户的 HTTP 会话与 SSE 订阅能力。"""

    def __init__(self, base_url: str, recorder: Recorder, username: str) -> None:
        self.base_url = base_url
        self.recorder = recorder
        self.username = username
        self.ssl_context = shared_ssl_context()
        self.client = httpx.Client(
            base_url=base_url,
            verify=self.ssl_context,
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
            follow_redirects=False,
            headers={"User-Agent": "causalagent-loadtest/1.0"},
        )
        self.csrf_token: str | None = None
        # 由执行器注入的并发信号量；SSE 订阅按档位上单独限流。
        self.sse_gate = None
        self._closed = threading.Event()
        self._job_requests: dict[str, tuple[str, float]] = {}
        self._job_sse_errors: dict[str, list[str]] = {}
        self._first_event_seen: set[str] = set()
        self._request_lock = threading.Lock()

    def close(self) -> None:
        self._closed.set()
        self.client.close()

    def bind_job_request(self, job_id: str, logical_request_id: str, created_at: float) -> None:
        """绑定终态回调需要的逻辑请求及首次 POST 时间（monotonic）。"""
        with self._request_lock:
            self._job_requests.setdefault(job_id, (logical_request_id, created_at))

    # --- 基础请求 ---

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """执行一次请求并记录耗时、状态码和传输错误。"""
        started = time.monotonic()
        started_at = time.time()
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        headers = dict(kwargs.pop("headers", None) or {})
        client_request_id = str(uuid.uuid4())
        headers["X-Request-ID"] = client_request_id
        kwargs["headers"] = headers
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
                started_at=started_at,
                finished_at=time.time(),
                request_id=(response.headers.get("X-Request-ID") if response is not None else None) or client_request_id,
                client_request_id=client_request_id,
                response_request_id=response.headers.get("X-Request-ID") if response is not None else None,
                run_id=self.recorder.run_id,
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
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
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
        *,
        timeout: float = HTTP_TIMEOUT_SECONDS,
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
            timeout=timeout,
        )

    def active_jobs(
        self, session_id: str | None = None, *, timeout: float = HTTP_TIMEOUT_SECONDS
    ) -> list:
        """读取当前用户的活动 Job。"""
        params = {"session_id": session_id} if session_id else None
        status, payload = self.json_request(
            "GET", "/api/agent/jobs/active", params=params, timeout=timeout
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
        self, job_id: str, question_id: str, answer: str | dict, idempotency_key: str,
        *, timeout: float = HTTP_TIMEOUT_SECONDS,
    ) -> tuple[int, dict]:
        """恢复 waiting_input Job。"""
        return self.json_request(
            "POST",
            f"/api/agent/jobs/{job_id}/resume",
            json={"question_id": question_id, "answer": answer},
            headers={"Idempotency-Key": idempotency_key},
            timeout=timeout,
        )

    def count_job_events(self, job_id: str, deadline: float | None = None) -> int:
        """独立重放历史事件；不写入实时终态和 SSE 计分样本。"""
        result = self.subscribe_job_events(job_id, deadline=deadline, audit=True)
        if result.terminal_event not in TERMINAL_EVENTS:
            return -1
        return len(set(result.event_ids))

    def subscribe_job_events(
        self,
        job_id: str,
        after_event_id: int = 0,
        allow_reconnect: bool = True,
        deadline: float | None = None,
        stop_event: threading.Event | None = None,
        *,
        audit: bool = False,
    ) -> SseOutcome:
        """立即订阅；deadline 为 monotonic 截止，可被 Recorder 的截止继续缩短。"""
        from runner.sse import subscribe

        deadline = (time.monotonic() + config.JOB_OBSERVE_DEADLINE_SECONDS
                    if deadline is None else deadline)
        result = asyncio.run(subscribe(
            self, job_id, after_event_id, allow_reconnect, deadline, stop_event, audit
        ))
        return SseOutcome(**result)


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
