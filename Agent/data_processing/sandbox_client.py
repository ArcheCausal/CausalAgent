"""Worker 调用沙盒控制服务的受限 HTTP 接口，不依赖 OpenSandbox SDK。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
import re
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit

import httpx

from Agent.execution_control import JobExecutionRevoked
from sandbox_controller.protocol import read_token, request_headers, validate_identity


HEARTBEAT_SECONDS = 20
RUN_COMMAND = "python3 /workspace/scripts/process.py"
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_LIMIT = 64 * 1024
_PREVIEW_TRUNCATION_SUFFIX = "\n[Preview truncated at the configured byte limit]"


class SandboxControllerError(RuntimeError):
    """服务拒绝请求或 HTTP 通信失败；不携带服务响应正文。"""

    def __init__(self, code: str, *, status_code: int = 0, retry_after: float = 1) -> None:
        super().__init__(f"sandbox controller: {code}")
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


class SandboxFileLimitError(SandboxControllerError):
    """输入、脚本或下载文件超过限制。"""


class SandboxCapacityTimeout(SandboxControllerError):
    """本地或全局容量在等待期限内不可用。"""


class SandboxLeaseLostError(JobExecutionRevoked):
    """沙盒心跳失败或本次 Job 执行资格已撤销。"""


class SandboxOperationUncertain(SandboxControllerError):
    """执行结果尚未确定，当前会话不得提交新的执行操作。"""


@dataclass(frozen=True, slots=True)
class SandboxControllerSettings:
    """仅保存控制服务连接参数和 Worker 的本地限制。"""

    controller_url: str
    token: str = field(repr=False)
    timeout_seconds: float = 30
    command_timeout_seconds: float = 300
    max_concurrent_per_worker: int = 2
    global_capacity_wait_seconds: float = 300
    max_input_bytes: int = 64 * 1024 * 1024
    max_output_file_bytes: int = 64 * 1024 * 1024
    max_preview_bytes: int = 32 * 1024
    max_command_bytes: int = 16 * 1024
    max_command_output_bytes: int = 64 * 1024

    @property
    def execution_wait_seconds(self) -> float:
        """为服务端命令中断、回执写入和状态查询保留 HTTP 宽限。"""
        return self.command_timeout_seconds + 2 * self.timeout_seconds + 10

    def __post_init__(self) -> None:
        parsed = urlsplit(self.controller_url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("invalid sandbox controller URL port") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
            or port == 0
        ):
            raise ValueError("sandbox controller URL must be an http(s) origin")
        if not 32 <= len(self.token) <= 4096 or any(c.isspace() for c in self.token):
            raise ValueError("sandbox controller token must be a single token")
        for name in ("timeout_seconds", "command_timeout_seconds", "global_capacity_wait_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
            "max_concurrent_per_worker", "max_input_bytes", "max_output_file_bytes",
            "max_preview_bytes", "max_command_bytes", "max_command_output_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> SandboxControllerSettings:
        values = os.environ if environ is None else environ
        token_file = values.get("CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN_FILE", "").strip()
        if not token_file:
            raise ValueError("CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN_FILE must be configured")
        options: dict[str, Any] = {
            "controller_url": values.get("CAUSALAGENT_SANDBOX_CONTROLLER_URL", "http://sandbox-controller:8081").strip(),
            "token": read_token(token_file),
        }
        for name in (
            "timeout_seconds", "command_timeout_seconds", "max_concurrent_per_worker",
            "global_capacity_wait_seconds", "max_input_bytes", "max_output_file_bytes",
            "max_preview_bytes", "max_command_bytes", "max_command_output_bytes",
        ):
            env_name = "CAUSALAGENT_SANDBOX_" + ("CONTROLLER_" if name == "timeout_seconds" else "") + name.upper()
            raw = values.get(env_name)
            if raw is not None:
                try:
                    options[name] = float(raw) if name.endswith("seconds") else int(raw)
                except ValueError:
                    raise ValueError(f"{env_name} must be numeric") from None
        return cls(**options)


@dataclass(frozen=True, slots=True)
class SandboxCommandResult:
    exit_code: int | None
    stdout: str
    stderr: str


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise SandboxControllerError("invalid_response") from None
    if not isinstance(result, dict):
        raise SandboxControllerError("invalid_response")
    return result


def _retry_after(value: str | None) -> float:
    try:
        delay = float(value) if value is not None else 1.0
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 1.0
    return max(0.1, delay) if math.isfinite(delay) else 1.0


class SandboxControllerSession:
    """只开放固定文件和固定脚本执行入口的短期会话。"""

    def __init__(self, client: httpx.AsyncClient, settings: SandboxControllerSettings,
                 identity: dict[str, Any], execution_guard: Any,
                 plan_version: str | None = None) -> None:
        self._client = client
        self._settings = settings
        self._identity = identity
        self._guard = execution_guard
        self._plan_version = plan_version
        self._revoked = asyncio.Event()
        self._lock = asyncio.Lock()
        self.session_id: str | None = None
        self._script_hash: str | None = None
        self._operation_id: str | None = None
        self._result: SandboxCommandResult | None = None

    @property
    def sandbox_id(self) -> str | None:
        return self.session_id

    @property
    def _path(self) -> str:
        if self.session_id is None:
            raise SandboxControllerError("session_unavailable")
        return f"/v1/sessions/{self.session_id}"

    async def _ensure_active(self, *, after: bool = False) -> None:
        if self._revoked.is_set():
            raise SandboxLeaseLostError("sandbox session execution revoked")
        if self._guard is not None:
            if after:
                await self._guard.check_after_call()
            else:
                await self._guard.ensure_active()

    def _revoke(self) -> None:
        self._revoked.set()
        if self._guard is not None:
            self._guard.mark_revoked()

    async def _exchange(self, method: str, path: str, body: bytes, limit: int,
                        *, json_body: bool = False) -> tuple[bytes, httpx.Headers]:
        headers = request_headers(self._settings.token, method, path, self._identity, body)
        headers["Accept-Encoding"] = "identity"
        if method in {"POST", "PUT"}:
            headers["Content-Type"] = "application/json" if json_body else "application/octet-stream"
        try:
            async with asyncio.timeout(self._settings.timeout_seconds):
                async with self._client.stream(method, path, content=body, headers=headers) as response:
                    success = 200 <= response.status_code < 300
                    bound = limit if success else _ERROR_LIMIT
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise SandboxControllerError("encoded_response_rejected", status_code=response.status_code)
                    length = response.headers.get("content-length")
                    if length is not None:
                        if not length.isdecimal():
                            raise SandboxControllerError("invalid_content_length", status_code=response.status_code)
                        if int(length) > bound:
                            raise SandboxFileLimitError("response_too_large", status_code=response.status_code)
                    chunks = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                        if len(chunks) + len(chunk) > bound:
                            raise SandboxFileLimitError("response_too_large", status_code=response.status_code)
                        chunks.extend(chunk)
                    payload = bytes(chunks)
                    if not success:
                        try:
                            error = _json_object(payload)
                        except SandboxControllerError:
                            error = {}
                        code = error.get("code", "request_failed")
                        if not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,80}", code) is None:
                            code = "request_failed"
                        raise SandboxControllerError(code, status_code=response.status_code,
                                                     retry_after=_retry_after(response.headers.get("retry-after")))
                    return payload, response.headers
        except (httpx.HTTPError, TimeoutError):
            raise SandboxControllerError("transport_unavailable") from None

    async def _request(self, method: str, path: str, body: bytes = b"", *,
                       limit: int = _ERROR_LIMIT, json_body: bool = False) -> tuple[bytes, httpx.Headers]:
        await self._ensure_active()
        request = asyncio.create_task(self._exchange(method, path, body, limit, json_body=json_body))
        revoked = asyncio.create_task(self._revoked.wait())
        guard_wait = asyncio.create_task(self._guard.wait_revoked()) if self._guard is not None else None
        tasks = [request, revoked] + ([guard_wait] if guard_wait is not None else [])
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if revoked in done or (guard_wait is not None and guard_wait in done):
                raise SandboxLeaseLostError("sandbox session execution revoked")
            try:
                return await request
            except SandboxControllerError as exc:
                if exc.status_code in {401, 403}:
                    self._revoke()
                    raise SandboxLeaseLostError("sandbox controller execution authorization revoked") from None
                raise
            finally:
                await self._ensure_active(after=True)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                async with asyncio.timeout(HEARTBEAT_SECONDS):
                    data, _ = await self._request("POST", self._path + "/heartbeat")
                    if _json_object(data).get("state") != "active":
                        raise SandboxControllerError("invalid_heartbeat_state")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._revoke()

    async def write_script(self, relative_path: str, content: str) -> str:
        if relative_path != "scripts/process.py":
            raise ValueError("only scripts/process.py is allowed")
        payload = content.encode("utf-8")
        if not payload or len(payload) > self._settings.max_command_bytes:
            raise SandboxFileLimitError("script_size_invalid")
        async with self._lock:
            if self._operation_id is not None:
                raise SandboxOperationUncertain("operation_already_submitted")
            self._script_hash = None
            data, _ = await self._request("PUT", self._path + "/script", payload)
            path = _json_object(data).get("path")
            if path != "/workspace/scripts/process.py":
                raise SandboxControllerError("invalid_script_path")
            self._script_hash = hashlib.sha256(payload).hexdigest()
            return path

    async def read_preview(self, relative_path: str) -> str:
        if relative_path != "raw/original.csv":
            raise ValueError("only raw/original.csv preview is allowed")
        data, _ = await self._request("GET", self._path + "/preview",
                                      limit=6 * self._settings.max_preview_bytes + 1024)
        preview = _json_object(data).get("preview")
        if not isinstance(preview, str):
            raise SandboxControllerError("invalid_preview")
        # 上游在受限文件前缀之后追加固定截断提示，该提示不属于文件正文额度。
        if len(preview.removesuffix(_PREVIEW_TRUNCATION_SUFFIX).encode("utf-8")) > self._settings.max_preview_bytes:
            raise SandboxFileLimitError("preview_too_large")
        return preview

    async def read_output(self, relative_path: str) -> bytes:
        if relative_path != "output/prepared.csv":
            raise ValueError("only output/prepared.csv is allowed")
        data, headers = await self._request("GET", self._path + "/output",
                                             limit=self._settings.max_output_file_bytes)
        expected = headers.get("x-content-sha256", "")
        if not _HEX_DIGEST.fullmatch(expected) or hashlib.sha256(data).hexdigest() != expected:
            raise SandboxControllerError("output_hash_mismatch")
        return data

    def _parse_result(self, value: Any) -> SandboxCommandResult:
        if not isinstance(value, dict):
            raise SandboxControllerError("invalid_execution_result")
        exit_code, stdout, stderr = (value.get(name) for name in ("exit_code", "stdout", "stderr"))
        if (exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int))) or not isinstance(stdout, str) or not isinstance(stderr, str):
            raise SandboxControllerError("invalid_execution_result")
        if len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")) > self._settings.max_command_output_bytes:
            raise SandboxFileLimitError("command_output_too_large")
        return SandboxCommandResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    async def _poll_result(self, deadline: float) -> SandboxCommandResult:
        while asyncio.get_running_loop().time() < deadline:
            await self._ensure_active()
            try:
                data, _ = await self._request("GET", self._path,
                                              limit=6 * self._settings.max_command_output_bytes + 4096)
                status = _json_object(data)
                if status.get("session_id") != self.session_id:
                    raise SandboxControllerError("invalid_session_status")
                if status.get("operation_status") == "succeeded" and status.get("result") is not None:
                    return self._parse_result(status["result"])
                if status.get("state") in {"closed", "deleting"}:
                    raise SandboxOperationUncertain("session_closed_without_result")
            except SandboxControllerError as exc:
                if exc.code != "transport_unavailable" and exc.status_code < 500:
                    raise
            await asyncio.sleep(min(1, max(0, deadline - asyncio.get_running_loop().time())))
        raise SandboxOperationUncertain("execution_result_unknown")

    async def run(self, command: str) -> SandboxCommandResult:
        if command != RUN_COMMAND:
            raise ValueError("only the fixed processing command is allowed")
        if self._plan_version is None or _HEX_DIGEST.fullmatch(self._plan_version) is None:
            raise SandboxControllerError("confirmed_plan_version_required")
        async with self._lock:
            await self._ensure_active()
            if self._result is not None:
                return self._result
            if self._script_hash is None:
                raise SandboxControllerError("script_required")
            deadline = asyncio.get_running_loop().time() + self._settings.execution_wait_seconds
            if self._operation_id is None:
                self._operation_id = hashlib.sha256(
                    f"{self.session_id}:{self._script_hash}:{self._plan_version}".encode("ascii")
                ).hexdigest()
                try:
                    data, _ = await self._request("POST", self._path + "/execute",
                        _json_bytes({"script_hash": self._script_hash, "operation_id": self._operation_id,
                                     "plan_version": self._plan_version}),
                        limit=6 * self._settings.max_command_output_bytes + 4096, json_body=True)
                    self._result = self._parse_result(_json_object(data))
                    return self._result
                except SandboxControllerError as exc:
                    if exc.code not in {"transport_unavailable", "operation_pending"} and exc.status_code < 500:
                        raise
            try:
                async with asyncio.timeout_at(deadline):
                    self._result = await self._poll_result(deadline)
            except TimeoutError:
                raise SandboxOperationUncertain("execution_result_unknown") from None
            return self._result


class SandboxControllerProvider:
    """Worker 共享并发闸门；控制服务负责全局容量和远端资源清理。"""

    def __init__(self, settings: SandboxControllerSettings) -> None:
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_per_worker)

    @asynccontextmanager
    async def open_session(self, *, job_id: str, worker_id: str, attempt_count: int,
                           lease_epoch: int, source_hash: str, request_id: str,
                           execution_guard: Any, original_csv: bytes,
                           plan_version: str | None = None) -> AsyncIterator[SandboxControllerSession]:
        identity = validate_identity({"job_id": job_id, "worker_id": worker_id,
                                      "attempt_count": attempt_count, "lease_epoch": lease_epoch})
        if not isinstance(original_csv, bytes) or not original_csv:
            raise ValueError("Frozen source CSV must be nonempty bytes")
        if len(original_csv) > self.settings.max_input_bytes:
            raise SandboxFileLimitError("input_too_large")
        if not _HEX_DIGEST.fullmatch(request_id) or not _HEX_DIGEST.fullmatch(source_hash):
            raise ValueError("request_id and source_hash must be SHA-256 digests")
        if hashlib.sha256(original_csv).hexdigest() != source_hash:
            raise ValueError("frozen input hash mismatch")
        if plan_version is not None and (
            not isinstance(plan_version, str) or _HEX_DIGEST.fullmatch(plan_version) is None
        ):
            raise ValueError("plan_version must be a SHA-256 digest")
        if execution_guard is not None:
            await execution_guard.ensure_active()
        deadline = asyncio.get_running_loop().time() + self.settings.global_capacity_wait_seconds
        acquired = False
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    await self._semaphore.acquire()
                    acquired = True
            except TimeoutError:
                raise SandboxCapacityTimeout("local_capacity_timeout") from None
            async with httpx.AsyncClient(base_url=self.settings.controller_url.rstrip("/"),
                                         timeout=self.settings.timeout_seconds, trust_env=False,
                                         follow_redirects=False) as client:
                session = SandboxControllerSession(client, self.settings, identity, execution_guard, plan_version)
                heartbeat: asyncio.Task[None] | None = None
                try:
                    # 会话 ID 与申请 ID 相同；即使创建响应丢失，finally 仍能尝试清理。
                    session.session_id = request_id
                    body = _json_bytes({"request_id": request_id, "source_hash": source_hash})
                    while True:
                        try:
                            async with asyncio.timeout_at(deadline):
                                data, _ = await session._request("POST", "/v1/sessions", body, json_body=True)
                            created = _json_object(data)
                            if created.get("session_id") != request_id or created.get("state") not in {"creating", "active"}:
                                raise SandboxControllerError("invalid_created_session")
                            break
                        except SandboxControllerError as exc:
                            if not ((exc.status_code == 409 and exc.code in {"capacity", "sandbox_capacity_unavailable"}) or
                                    exc.code == "transport_unavailable" or exc.status_code >= 500):
                                raise
                            remaining = deadline - asyncio.get_running_loop().time()
                            if remaining <= 0:
                                raise SandboxCapacityTimeout("global_capacity_timeout") from None
                            await session._ensure_active()
                            await asyncio.sleep(min(exc.retry_after, remaining))
                        except TimeoutError:
                            raise SandboxCapacityTimeout("global_capacity_timeout") from None
                    heartbeat = asyncio.create_task(session._heartbeat())
                    data, _ = await session._request("PUT", session._path + "/input", original_csv)
                    if _json_object(data).get("state") != "active":
                        raise SandboxControllerError("invalid_initialized_session")
                    await session._ensure_active()
                    yield session
                finally:
                    session._revoked.set()
                    # 清理不依赖仍有效的 Job guard；失败保留给控制服务后台核查。
                    async def destroy() -> None:
                        if heartbeat is not None:
                            heartbeat.cancel()
                            await asyncio.gather(heartbeat, return_exceptions=True)
                        async with asyncio.timeout(min(30, self.settings.timeout_seconds)):
                            await session._exchange("DELETE", session._path, b"", _ERROR_LIMIT)

                    cleanup = asyncio.create_task(destroy())
                    cancelled = False
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            cancelled = True
                        except Exception:
                            break
                    await asyncio.gather(cleanup, return_exceptions=True)
                    if cancelled:
                        raise asyncio.CancelledError
        finally:
            if acquired:
                self._semaphore.release()
