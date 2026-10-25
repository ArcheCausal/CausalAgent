"""OpenSandbox 客户端与受限的数据处理工作区。"""

from __future__ import annotations

import asyncio
import codecs
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
import errno
import inspect
import ipaddress
import json
import os
from pathlib import PurePosixPath
import re
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.models.execd import ExecutionHandlers, RunCommandOpts
from opensandbox.models.sandboxes import (
    NetworkPolicy,
    SandboxImageSpec,
)


WORKSPACE_ROOT = PurePosixPath("/workspace")
RAW_INPUT_PATH = WORKSPACE_ROOT / "raw" / "original.csv"
NETWORK_PROBE_PATH = WORKSPACE_ROOT / ".causalagent-network-probe.py"
SANDBOX_LEASE_HEARTBEAT_SECONDS = 20
SANDBOX_LEASE_MIN_SECONDS = 60
_SAFE_RELATIVE_PATH = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")
_IMMUTABLE_IMAGE_REFERENCE = re.compile(r".+@sha256:[0-9a-f]{64}\Z")
_NETWORK_PROBE = """
import errno
import json
import socket

targets = (
    ("ipv4", socket.AF_INET, "1.1.1.1"),
    ("ipv4-secondary", socket.AF_INET, "8.8.8.8"),
    ("ipv6", socket.AF_INET6, "2606:4700:4700::1111"),
    ("ipv6-secondary", socket.AF_INET6, "2001:4860:4860::8888"),
)
blocked_errors = {
    errno.EACCES, errno.EAFNOSUPPORT, errno.EHOSTUNREACH,
    errno.ENETUNREACH, errno.EPERM, errno.ETIMEDOUT,
}
results = []
for name, family, address in targets:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(2.0)
            connection.connect((address, 443))
        results.append({"family": name, "blocked": False})
    except OSError as error:
        results.append({
            "family": name,
            "blocked": error.errno in blocked_errors or isinstance(error, TimeoutError),
            "error": error.errno,
        })
print(json.dumps(results, separators=(",", ":")))
if not all(item["blocked"] for item in results):
    raise SystemExit(31)
"""
_INPUT_WRITE_PROBE = """
from pathlib import Path
path = Path("/workspace/raw/original.csv")
try:
    with path.open("ab") as output:
        output.write(b"x")
except PermissionError:
    raise SystemExit(0)
raise SystemExit(32)
"""


class OpenSandboxConfigurationError(ValueError):
    """OpenSandbox 配置缺失或不符合安全边界。"""


class SandboxIsolationError(RuntimeError):
    """沙盒未能证明输入只读且没有外部网络连接。"""


class SandboxOutputLimitError(RuntimeError):
    """沙盒命令输出超过配置上限。"""


class SandboxFileLimitError(RuntimeError):
    """沙盒文件超过配置上限。"""


class SandboxCapacityTimeout(RuntimeError):
    """全局沙盒并发额度在等待期限内不可用。"""


class SandboxLeaseLostError(RuntimeError):
    """执行期间失去 MySQL 全局沙盒租约或 Job 租约。"""


@dataclass(frozen=True, slots=True)
class OpenSandboxSettings:
    """一次 worker 进程使用的 OpenSandbox 连接和资源限制。"""

    api_url: str
    api_key: str = field(repr=False)
    image: str
    cpu_limit: str = "1"
    memory_limit: str = "2Gi"
    sandbox_ttl_seconds: int = 900
    command_timeout_seconds: int = 300
    api_timeout_seconds: int = 30
    max_concurrent_per_worker: int = 2
    global_capacity_wait_seconds: int = 300
    max_input_bytes: int = 64 * 1024 * 1024
    max_preview_bytes: int = 32 * 1024
    max_output_file_bytes: int = 64 * 1024 * 1024
    max_command_bytes: int = 16 * 1024
    max_command_output_bytes: int = 64 * 1024
    agent_uid: int = 10001
    agent_gid: int = 10001
    allow_http: bool = False

    def __post_init__(self) -> None:
        parsed = urlsplit(self.api_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise OpenSandboxConfigurationError(
                "CAUSALAGENT_SANDBOX_URL must be an http(s) origin without credentials or path"
            )
        if parsed.scheme == "http" and not self.allow_http:
            try:
                is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                is_loopback = parsed.hostname.casefold() == "localhost"
            if not is_loopback:
                raise OpenSandboxConfigurationError(
                    "CAUSALAGENT_SANDBOX_URL must use HTTPS outside loopback unless HTTP is explicitly enabled"
                )
        if not self.api_key or self.api_key != self.api_key.strip():
            raise OpenSandboxConfigurationError("OpenSandbox API key is missing or malformed")
        if (
            not self.image
            or self.image != self.image.strip()
            or _IMMUTABLE_IMAGE_REFERENCE.fullmatch(self.image) is None
        ):
            raise OpenSandboxConfigurationError(
                "CAUSALAGENT_SANDBOX_IMAGE must be an immutable image reference pinned by sha256 digest"
            )
        if not self.cpu_limit.strip() or not self.memory_limit.strip():
            raise OpenSandboxConfigurationError("Sandbox CPU and memory limits are required")
        if self.command_timeout_seconds <= 0 or self.api_timeout_seconds <= 0:
            raise OpenSandboxConfigurationError("Sandbox timeouts must be positive")
        if self.sandbox_ttl_seconds < self.command_timeout_seconds:
            raise OpenSandboxConfigurationError(
                "Sandbox lifetime must cover at least one maximum command"
            )
        if self.max_concurrent_per_worker <= 0:
            raise OpenSandboxConfigurationError("Sandbox concurrency limit must be positive")
        if self.global_capacity_wait_seconds <= 0:
            raise OpenSandboxConfigurationError("Global sandbox capacity wait must be positive")
        if min(
            self.max_input_bytes,
            self.max_preview_bytes,
            self.max_output_file_bytes,
            self.max_command_bytes,
            self.max_command_output_bytes,
        ) <= 0:
            raise OpenSandboxConfigurationError("Sandbox size limits must be positive")
        if self.agent_uid <= 0 or self.agent_gid <= 0:
            raise OpenSandboxConfigurationError("Sandbox agent must use a non-root uid and gid")

    @classmethod
    def from_environment(
        cls,
        *,
        api_key: str,
        environ: Mapping[str, str] | None = None,
    ) -> "OpenSandboxSettings":
        values = os.environ if environ is None else environ
        api_url = values.get("CAUSALAGENT_SANDBOX_URL", "").strip()
        image = values.get("CAUSALAGENT_SANDBOX_IMAGE", "").strip()
        if not api_url or not image:
            raise OpenSandboxConfigurationError(
                "CAUSALAGENT_SANDBOX_URL and CAUSALAGENT_SANDBOX_IMAGE are required"
            )
        return cls(
            api_url=api_url,
            api_key=api_key,
            image=image,
            cpu_limit=values.get("CAUSALAGENT_SANDBOX_CPU_LIMIT", "1").strip(),
            memory_limit=values.get("CAUSALAGENT_SANDBOX_MEMORY_LIMIT", "2Gi").strip(),
            sandbox_ttl_seconds=_positive_int(values, "CAUSALAGENT_SANDBOX_TTL_SECONDS", 900),
            command_timeout_seconds=_positive_int(
                values, "CAUSALAGENT_SANDBOX_COMMAND_TIMEOUT_SECONDS", 300
            ),
            api_timeout_seconds=_positive_int(
                values, "CAUSALAGENT_SANDBOX_API_TIMEOUT_SECONDS", 30
            ),
            max_concurrent_per_worker=_positive_int(
                values, "CAUSALAGENT_SANDBOX_MAX_CONCURRENT_PER_WORKER", 2
            ),
            global_capacity_wait_seconds=_positive_int(
                values, "CAUSALAGENT_SANDBOX_GLOBAL_CAPACITY_WAIT_SECONDS", 300
            ),
            max_input_bytes=_positive_int(
                values, "CAUSALAGENT_SANDBOX_MAX_INPUT_BYTES", 64 * 1024 * 1024
            ),
            max_preview_bytes=_positive_int(
                values, "CAUSALAGENT_SANDBOX_MAX_PREVIEW_BYTES", 32 * 1024
            ),
            max_output_file_bytes=_positive_int(
                values, "CAUSALAGENT_SANDBOX_MAX_OUTPUT_FILE_BYTES", 64 * 1024 * 1024
            ),
            max_command_bytes=_positive_int(
                values, "CAUSALAGENT_SANDBOX_MAX_COMMAND_BYTES", 16 * 1024
            ),
            max_command_output_bytes=_positive_int(
                values, "CAUSALAGENT_SANDBOX_MAX_COMMAND_OUTPUT_BYTES", 64 * 1024
            ),
            agent_uid=_positive_int(values, "CAUSALAGENT_SANDBOX_AGENT_UID", 10001),
            agent_gid=_positive_int(values, "CAUSALAGENT_SANDBOX_AGENT_GID", 10001),
            allow_http=values.get("CAUSALAGENT_SANDBOX_ALLOW_HTTP", "false").casefold()
            in {"1", "true", "yes"},
        )

    def connection_config(self) -> ConnectionConfig:
        parsed = urlsplit(self.api_url)
        return ConnectionConfig(
            api_key=self.api_key,
            domain=parsed.netloc,
            protocol=parsed.scheme,
            use_server_proxy=True,
            request_timeout=timedelta(seconds=self.api_timeout_seconds),
            disable_metrics=True,
        )


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise OpenSandboxConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise OpenSandboxConfigurationError(f"{name} must be positive")
    return value


def _sandbox_capacity_lease_seconds(settings: OpenSandboxSettings) -> int:
    """Keep a capacity slot reserved until an orphaned sandbox must have expired."""
    return max(
        SANDBOX_LEASE_MIN_SECONDS,
        settings.sandbox_ttl_seconds + 2 * settings.api_timeout_seconds,
    )


@dataclass(frozen=True, slots=True)
class SandboxCommandResult:
    """一条沙盒命令的有限输出。"""

    exit_code: int | None
    stdout: str
    stderr: str


class OpenSandboxSession:
    """一个 Job 的短期工作区；原始 CSV 由 root 拥有且不可写。"""

    def __init__(
        self,
        sandbox: Sandbox,
        settings: OpenSandboxSettings,
        *,
        lease_lost: asyncio.Event,
        execution_guard: Any,
    ) -> None:
        self._sandbox = sandbox
        self._settings = settings
        self._lease_lost = lease_lost
        self._execution_guard = execution_guard
        self._command_lock = asyncio.Lock()
        self._active_capture: _BoundedOutput | None = None

    @property
    def sandbox_id(self) -> str:
        return self._sandbox.id

    async def _ensure_active(self) -> None:
        if self._lease_lost.is_set():
            raise SandboxLeaseLostError("sandbox global lease is no longer active")
        if self._execution_guard is not None:
            ensure_active = getattr(self._execution_guard, "ensure_active", None)
            if callable(ensure_active):
                result = ensure_active()
                if inspect.isawaitable(result):
                    await result

    async def revoke(self) -> None:
        """Interrupt the current command as soon as lease ownership is lost."""
        self._lease_lost.set()
        if self._active_capture is not None:
            await self._active_capture.interrupt_active()

    async def write_script(self, relative_path: str, content: str) -> str:
        await self._ensure_active()
        path = _workspace_path(relative_path, allowed_roots={"scripts"})
        payload = content.encode("utf-8")
        if len(payload) > self._settings.max_command_bytes:
            raise SandboxFileLimitError("Processing script exceeds the configured size limit")
        await self._sandbox.files.write_file(
            str(path),
            payload,
            mode=600,
            owner=str(self._settings.agent_uid),
            group=str(self._settings.agent_gid),
        )
        await self._ensure_active()
        return str(path)

    async def read_preview(self, relative_path: str) -> str:
        await self._ensure_active()
        path = _workspace_path(
            relative_path, allowed_roots={"raw", "scripts", "output"}
        )
        data = await self._read_bounded(
            path, self._settings.max_preview_bytes, allow_truncated=True
        )
        text = _decode_preview(data, self._settings.max_preview_bytes)
        await self._ensure_active()
        return text

    async def read_output(self, relative_path: str) -> bytes:
        await self._ensure_active()
        path = _workspace_path(relative_path, allowed_roots={"output"})
        data = await self._read_bounded(path, self._settings.max_output_file_bytes)
        await self._ensure_active()
        return data

    async def run(self, command: str) -> SandboxCommandResult:
        await self._ensure_active()
        command_bytes = command.encode("utf-8")
        if not command.strip() or len(command_bytes) > self._settings.max_command_bytes:
            raise ValueError("Sandbox command is empty or exceeds the configured size limit")
        capture = _BoundedOutput(
            self._settings.max_command_output_bytes,
            interrupt=self._sandbox.commands.interrupt,
        )
        handlers = ExecutionHandlers(
            on_init=capture.on_init,
            on_stdout=capture.on_stdout,
            on_stderr=capture.on_stderr,
            skip_accumulation=True,
        )
        options = RunCommandOpts(
            working_directory=str(WORKSPACE_ROOT),
            timeout=timedelta(seconds=self._settings.command_timeout_seconds),
            uid=self._settings.agent_uid,
            gid=self._settings.agent_gid,
            envs={
                "HOME": "/workspace",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },
        )
        async with self._command_lock:
            self._active_capture = capture
            try:
                async with asyncio.timeout(self._settings.command_timeout_seconds + 5):
                    execution = await self._sandbox.commands.run(
                        command, opts=options, handlers=handlers
                    )
            except TimeoutError:
                await capture.interrupt_active()
                raise
            finally:
                self._active_capture = None
        await self._ensure_active()
        if capture.exceeded:
            raise SandboxOutputLimitError("Sandbox command output exceeded its configured limit")
        return SandboxCommandResult(
            exit_code=execution.exit_code,
            stdout=capture.stdout,
            stderr=capture.stderr,
        )

    async def _initialize(self, original_csv: bytes) -> None:
        await self._ensure_active()
        settings = self._settings
        sandbox = self._sandbox
        # This setup runs as root before any model-authored command. Later
        # commands always use the configured unprivileged uid/gid.
        setup = await sandbox.commands.run(
            "mkdir -p /workspace/raw /workspace/scripts /workspace/output && "
            "chown root:root /workspace/raw && chmod 0555 /workspace/raw && "
            f"chown {settings.agent_uid}:{settings.agent_gid} /workspace/scripts /workspace/output && "
            "chmod 0700 /workspace/scripts /workspace/output",
            opts=RunCommandOpts(
                working_directory=str(WORKSPACE_ROOT),
                timeout=timedelta(seconds=settings.api_timeout_seconds),
                uid=0,
                gid=0,
            ),
        )
        if setup.exit_code != 0:
            raise SandboxIsolationError("Sandbox workspace could not be prepared safely")

        await sandbox.files.write_file(
            str(RAW_INPUT_PATH),
            original_csv,
            mode=444,
            owner="root",
            group="root",
        )
        await sandbox.files.write_file(
            str(NETWORK_PROBE_PATH),
            _NETWORK_PROBE.encode("utf-8"),
            mode=444,
            owner="root",
            group="root",
        )
        write_probe_path = WORKSPACE_ROOT / ".causalagent-input-write-probe.py"
        await sandbox.files.write_file(
            str(write_probe_path),
            _INPUT_WRITE_PROBE.encode("utf-8"),
            mode=444,
            owner="root",
            group="root",
        )

        input_result = await sandbox.commands.run(
            "python3 /workspace/.causalagent-input-write-probe.py",
            opts=_agent_command_options(settings, timeout=5),
        )
        if input_result.exit_code != 0:
            raise SandboxIsolationError("Sandbox source copy is writable by the processing user")

        policy = await sandbox.get_egress_policy()
        if policy.default_action != "deny" or policy.egress not in (None, []):
            raise SandboxIsolationError("OpenSandbox did not apply the explicit deny-all policy")
        network_capture = _BoundedOutput(4096, interrupt=sandbox.commands.interrupt)
        network_result = await sandbox.commands.run(
            "python3 /workspace/.causalagent-network-probe.py",
            opts=_agent_command_options(settings, timeout=12),
            handlers=ExecutionHandlers(
                on_init=network_capture.on_init,
                on_stdout=network_capture.on_stdout,
                on_stderr=network_capture.on_stderr,
                skip_accumulation=True,
            ),
        )
        if network_result.exit_code != 0 or network_capture.exceeded:
            raise SandboxIsolationError("OpenSandbox sandbox can reach an external network")
        try:
            network_checks = json.loads(network_capture.stdout)
        except (TypeError, ValueError) as exc:
            raise SandboxIsolationError(
                "OpenSandbox network isolation probe returned invalid data"
            ) from exc
        if (
            not isinstance(network_checks, list)
            or {
                item.get("family")
                for item in network_checks
                if isinstance(item, dict)
            }
                != {"ipv4", "ipv4-secondary", "ipv6", "ipv6-secondary"}
            or any(
                not item.get("blocked")
                for item in network_checks
                if isinstance(item, dict)
            )
        ):
            raise SandboxIsolationError(
                "OpenSandbox sandbox did not block IPv4 and IPv6 egress"
            )

    async def _read_bounded(
        self, path: PurePosixPath, max_bytes: int, *, allow_truncated: bool = False
    ) -> bytes:
        stream = await self._sandbox.files.read_bytes_stream(
            str(path),
            chunk_size=min(64 * 1024, max_bytes + 1),
            range_header=f"bytes=0-{max_bytes}",
        )
        chunks: list[bytes] = []
        total = 0
        async with aclosing(stream):
            async for chunk in stream:
                await self._ensure_active()
                remaining = max_bytes + 1 - total
                bounded_chunk = chunk[:remaining]
                chunks.append(bounded_chunk)
                total += len(bounded_chunk)
                if total > max_bytes:
                    if allow_truncated:
                        break
                    raise SandboxFileLimitError(
                        "Sandbox file exceeds its configured size limit"
                    )
        return b"".join(chunks)


def _decode_preview(data: bytes, max_bytes: int) -> str:
    """Decode a bounded prefix, allowing only an unfinished trailing UTF-8 character."""
    truncated = len(data) > max_bytes
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        text = decoder.decode(data[:max_bytes], final=not truncated)
    except UnicodeDecodeError as exc:
        raise ValueError("Requested preview is not UTF-8 text") from exc
    if truncated:
        text += "\n[Preview truncated at the configured byte limit]"
    return text


class _BoundedOutput:
    def __init__(self, max_bytes: int, *, interrupt: Any) -> None:
        self._max_bytes = max_bytes
        self._interrupt = interrupt
        self._execution_id: str | None = None
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._size = 0
        self._interrupted = False
        self.exceeded = False

    @property
    def stdout(self) -> str:
        return "".join(self._stdout)

    @property
    def stderr(self) -> str:
        return "".join(self._stderr)

    async def on_init(self, event: Any) -> None:
        execution_id = getattr(event, "id", None)
        if isinstance(execution_id, str) and execution_id:
            self._execution_id = execution_id

    async def on_stdout(self, message: Any) -> None:
        await self._append(self._stdout, message)

    async def on_stderr(self, message: Any) -> None:
        await self._append(self._stderr, message)

    async def on_result(self, result: Any) -> None:
        await self._append(self._stdout, result)

    async def _append(self, target: list[str], item: Any) -> None:
        text = getattr(item, "text", "")
        if not isinstance(text, str) or not text:
            return
        payload = text.encode("utf-8")
        remaining = self._max_bytes - self._size
        if remaining <= 0:
            self.exceeded = True
            await self.interrupt_active()
            return
        if len(payload) > remaining:
            self.exceeded = True
            target.append(payload[:remaining].decode("utf-8", errors="ignore"))
            self._size += remaining
            await self.interrupt_active()
            return
        target.append(text)
        self._size += len(payload)

    async def interrupt_active(self) -> None:
        if self._interrupted or not self._execution_id:
            return
        self._interrupted = True
        await self._interrupt(self._execution_id)


def _workspace_path(relative_path: str, *, allowed_roots: set[str]) -> PurePosixPath:
    if (
        not isinstance(relative_path, str)
        or not _SAFE_RELATIVE_PATH.fullmatch(relative_path)
        or relative_path.startswith("/")
    ):
        raise ValueError("Sandbox file path must be a safe workspace-relative path")
    path = PurePosixPath(relative_path)
    if not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Sandbox file path escapes its workspace")
    if path.parts[0] not in allowed_roots:
        raise ValueError("Sandbox file path is outside the permitted workspace area")
    return WORKSPACE_ROOT / path


class OpenSandboxProvider:
    """创建禁网、资源受限且每次使用后销毁的 OpenSandbox 沙盒。"""

    def __init__(self, settings: OpenSandboxSettings, *, lease_manager: Any) -> None:
        self._settings = settings
        self._lease_manager = lease_manager
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_per_worker)

    @property
    def settings(self) -> OpenSandboxSettings:
        """Expose the validated resource limits used by independent validation."""
        return self._settings

    async def _renew_global_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        lease_token: str,
        lease_seconds: int,
        lease_lost: asyncio.Event,
        execution_guard: Any,
        session_ref: list[OpenSandboxSession],
    ) -> None:
        while True:
            await asyncio.sleep(SANDBOX_LEASE_HEARTBEAT_SECONDS)
            try:
                if execution_guard is not None:
                    ensure_active = getattr(execution_guard, "ensure_active", None)
                    if callable(ensure_active):
                        result = ensure_active()
                        if inspect.isawaitable(result):
                            await result
                renewed = await asyncio.to_thread(
                    self._lease_manager.renew,
                    job_id,
                    worker_id,
                    lease_epoch,
                    lease_token,
                    lease_seconds=lease_seconds,
                )
            except Exception:
                renewed = False
            if not renewed:
                lease_lost.set()
                if session_ref:
                    await session_ref[0].revoke()
                return

    @asynccontextmanager
    async def open_session(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        execution_guard: Any,
        original_csv: bytes,
    ) -> AsyncIterator[OpenSandboxSession]:
        if not original_csv:
            raise ValueError("Frozen source CSV is empty")
        if len(original_csv) > self._settings.max_input_bytes:
            raise SandboxFileLimitError("Frozen source CSV exceeds its configured size limit")
        if not job_id or len(job_id) > 80 or not re.fullmatch(r"[A-Za-z0-9-]+", job_id):
            raise ValueError("Sandbox job identity is invalid")
        if not worker_id or worker_id != worker_id.strip() or lease_epoch < 0:
            raise ValueError("Sandbox worker lease identity is invalid")

        await self._semaphore.acquire()
        lease_token = str(uuid4())
        lease_seconds = _sandbox_capacity_lease_seconds(self._settings)
        lease_acquired = False
        sandbox: Sandbox | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        lease_lost = asyncio.Event()
        session_ref: list[OpenSandboxSession] = []
        try:
            deadline = asyncio.get_running_loop().time() + self._settings.global_capacity_wait_seconds
            while not lease_acquired:
                if execution_guard is not None:
                    ensure_active = getattr(execution_guard, "ensure_active", None)
                    if callable(ensure_active):
                        result = ensure_active()
                        if inspect.isawaitable(result):
                            await result
                lease_acquired = await asyncio.to_thread(
                    self._lease_manager.try_acquire,
                    job_id,
                    worker_id,
                    lease_epoch,
                    lease_token,
                    lease_seconds=lease_seconds,
                )
                if lease_acquired:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise SandboxCapacityTimeout(
                        "Global OpenSandbox capacity was not available before the wait limit"
                    )
                await asyncio.sleep(1)

            heartbeat_task = asyncio.create_task(
                self._renew_global_lease(
                    job_id=job_id,
                    worker_id=worker_id,
                    lease_epoch=lease_epoch,
                    lease_token=lease_token,
                    lease_seconds=lease_seconds,
                    lease_lost=lease_lost,
                    execution_guard=execution_guard,
                    session_ref=session_ref,
                )
            )
            sandbox = await Sandbox.create(
                SandboxImageSpec(self._settings.image),
                timeout=timedelta(seconds=self._settings.sandbox_ttl_seconds),
                ready_timeout=timedelta(seconds=self._settings.api_timeout_seconds),
                env={"PYTHON_VERSION": "3.11"},
                resource={
                    "cpu": self._settings.cpu_limit,
                    "memory": self._settings.memory_limit,
                },
                network_policy=NetworkPolicy(default_action="deny", egress=[]),
                entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
                metadata={
                    "application": "causalagent",
                    "purpose": "data-preparation",
                    "job": job_id,
                },
                connection_config=self._settings.connection_config(),
            )
            session = OpenSandboxSession(
                sandbox,
                self._settings,
                lease_lost=lease_lost,
                execution_guard=execution_guard,
            )
            session_ref.append(session)
            await session._initialize(original_csv)
            yield session
        finally:
            try:
                if sandbox is not None:
                    destroy_task = asyncio.create_task(sandbox.destroy())
                    try:
                        await asyncio.shield(destroy_task)
                    except asyncio.CancelledError:
                        await asyncio.shield(destroy_task)
                        raise
            finally:
                try:
                    if heartbeat_task is not None:
                        heartbeat_task.cancel()
                        await asyncio.gather(heartbeat_task, return_exceptions=True)
                    if lease_acquired:
                        await asyncio.to_thread(
                            self._lease_manager.release,
                            job_id,
                            worker_id,
                            lease_epoch,
                            lease_token,
                        )
                finally:
                    self._semaphore.release()


def _agent_command_options(
    settings: OpenSandboxSettings, *, timeout: int | None = None
) -> RunCommandOpts:
    return RunCommandOpts(
        working_directory=str(WORKSPACE_ROOT),
        timeout=timedelta(seconds=timeout or settings.command_timeout_seconds),
        uid=settings.agent_uid,
        gid=settings.agent_gid,
        envs={
            "HOME": "/workspace",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        },
    )
