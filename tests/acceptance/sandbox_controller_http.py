"""真实 TCP HTTP + 隔离 MySQL 的显式控制 API 验收。

执行：SANDBOX_CONTROLLER_HTTP_ACCEPTANCE=1 python tests/acceptance/sandbox_controller_http.py
复用 sandbox-lifecycle-db-* / sandbox_lifecycle_acceptance 和现存临时密钥文件。
真实 OpenSandbox SDK 仅访问 http://127.0.0.1:9，连接失败属于故障注入。
不创建替代远端、不运行沙盒命令、不连接生产、不删除数据库或卷。
创建失败的 uncertain 记录和报告保留；重跑应使用另一隔离库或先人工核查记录。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from Agent.data_processing.opensandbox_runtime import OpenSandboxSettings
from Database import sandbox_sessions as store
from app.db import get_write_connection
from sandbox_controller.api import create_app
from sandbox_controller.config import ControllerSettings
from sandbox_controller.protocol import _signature, canonical_bytes, request_headers
from sandbox_controller.singleton import ControllerSingleton


INPUT = b"x,y\n1,2\n"
SOURCE_HASH = hashlib.sha256(INPUT).hexdigest()
PLAN_VERSION = "b" * 64


def _boundary() -> None:
    if (
        os.environ.get("SANDBOX_CONTROLLER_HTTP_ACCEPTANCE") != "1"
        or not os.environ.get("MYSQL_WRITE_HOST", "").startswith("sandbox-lifecycle-db-")
        or os.environ.get("MYSQL_DATABASE") != "sandbox_lifecycle_acceptance"
        or os.environ.get("CAUSALAGENT_SECRET_FILE") != "/tmp/sandbox-lifecycle-secrets.env"
    ):
        raise RuntimeError("refusing environment outside isolated HTTP acceptance")


def _settings(token_file: str) -> ControllerSettings:
    release = json.loads((ROOT / "deploy/opensandbox/release-lock.json").read_text(encoding="utf-8"))
    return ControllerSettings(
        remote=OpenSandboxSettings(
            api_url="http://127.0.0.1:9", api_key=secrets.token_urlsafe(32),
            image=release["images"]["interpreter_base"],
            sandbox_ttl_seconds=300, command_timeout_seconds=30, api_timeout_seconds=1,
            max_input_bytes=256, max_command_bytes=128,
        ),
        token=Path(token_file).read_text(encoding="utf-8").strip(),
        capacity=1, reconcile_interval_seconds=60,
    )


def _sql(statement: str, values: tuple = (), *, fetch: bool = False):
    connection = get_write_connection()
    try:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute(statement, values)
            result = cursor.fetchall() if fetch else cursor.lastrowid
        connection.commit()
        return result
    finally:
        connection.close()


def _identity() -> dict:
    user_id = _sql("INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                   ("http-acceptance-" + uuid4().hex, "unused-acceptance-login"))
    session_id, job_id = str(uuid4()), str(uuid4())
    _sql("INSERT INTO sessions (id, user_id) VALUES (%s, %s)", (session_id, user_id))
    _sql(
        """INSERT INTO analysis_jobs
           (job_id, user_id, session_id, message, status, worker_id, attempt_count,
            lease_epoch, execution_state, locked_at, heartbeat_at, input_file_hash)
           VALUES (%s, %s, %s, 'isolated HTTP acceptance', 'running', 'http-acceptance-worker',
                   1, 1, 'leased', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), %s)""",
        (job_id, user_id, session_id, SOURCE_HASH),
    )
    identity = {"job_id": job_id, "worker_id": "http-acceptance-worker", "attempt_count": 1, "lease_epoch": 1}
    _input(identity, {"action": "confirm", "plan_version": PLAN_VERSION}, 1)
    return identity


def _input(identity: dict, value, sequence: int) -> None:
    _sql(
        """INSERT INTO analysis_job_inputs
           (job_id, sequence, input_type, input_text, idempotency_key, request_fingerprint)
           VALUES (%s, %s, 'resume', %s, %s, %s)""",
        (identity["job_id"], sequence, json.dumps(value), str(uuid4()), "c" * 64),
    )


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _launch(directory: Path, token_file: Path, name: str):
    port = _port()
    log = (directory / f"{name}.log").open("wb")
    environment = dict(os.environ)
    # The controller's import/startup boundary must work without model settings.
    for key in ("MODEL", "BASE_URL", "DEEP_AGENT_MODEL", "DEEP_AGENT_BASE_URL"):
        environment.pop(key, None)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--port", str(port), "--token-file", str(token_file)],
        cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
    )
    log.close()
    return process, port


async def _wait_exit(process: subprocess.Popen, timeout: float = 10) -> int:
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    if process.poll() is None:
        raise AssertionError("acceptance controller process did not exit in time")
    return process.returncode


async def _ready(client: httpx.AsyncClient, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("acceptance controller startup failed; inspect retained process log")
        try:
            response = await client.get("/healthz")
            if response.status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError("acceptance controller did not become ready")


async def _run(directory: Path, report: dict) -> None:
    assert store.count_non_closed_sessions() == 0, "refusing to mix with existing unclosed resource records"
    # This is an actual socket probe of the fault endpoint, not a replacement SDK.
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", 9)) != 0, "fault endpoint unexpectedly accepts TCP"
    token = secrets.token_urlsafe(48)
    token_file = directory / "controller.token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    identity, other_identity = _identity(), _identity()
    request_id = hashlib.sha256(uuid4().bytes).hexdigest()
    missing_id = hashlib.sha256(uuid4().bytes).hexdigest()
    create_body = canonical_bytes({"request_id": request_id, "source_hash": SOURCE_HASH})
    root_path = "/v1/sessions"
    session_path = root_path + "/" + request_id
    checks = report["checks"]
    processes = []

    def passed(name: str) -> None:
        checks.append({"name": name, "status": "passed"})

    process, port = _launch(directory, token_file, "controller")
    processes.append(process)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=8, trust_env=False) as client:
            await _ready(client, process)
            passed("real_tcp_health_without_model_configuration")

            async def expect(name, method, path, body=b"", *, expected: int, code: str | None = None,
                             owner=None, headers=None, chunks: bool = False):
                signed = request_headers(token, method, path, owner or identity, body) if headers is None else headers
                if chunks:
                    async def stream():
                        for start in range(0, len(body), 43):
                            yield body[start:start + 43]
                    content = stream()
                else:
                    content = body
                response = await client.request(method, path, content=content, headers=signed)
                assert response.status_code == expected, f"{name}: expected {expected}, got {response.status_code}"
                if code is not None:
                    assert response.json().get("code") == code, f"{name}: unexpected safe error code"
                assert token not in response.text, f"{name}: credential appeared in response"
                passed(name)
                return response

            await expect("missing_bearer_rejected", "POST", root_path, create_body, headers={}, expected=401)
            await expect("wrong_bearer_rejected", "POST", root_path, create_body,
                         headers=request_headers(token + "wrong", "POST", root_path, identity, create_body), expected=401)
            await expect("bearer_without_signature_rejected", "POST", root_path, create_body,
                         headers={"Authorization": "Bearer " + token}, expected=401)
            wrong_signature = request_headers(token, "POST", root_path, identity, create_body)
            wrong_signature["X-Sandbox-Signature"] = "0" * 64
            await expect("invalid_signature_rejected", "POST", root_path, create_body, headers=wrong_signature, expected=401)
            await expect("modified_body_rejected", "POST", root_path, create_body + b" ",
                         headers=request_headers(token, "POST", root_path, identity, create_body), expected=401)
            await expect("modified_path_rejected", "GET", root_path + "/" + missing_id,
                         headers=request_headers(token, "GET", session_path, identity), expected=401)
            await expect("modified_method_rejected", "DELETE", session_path,
                         headers=request_headers(token, "GET", session_path, identity), expected=401)
            changed_identity = request_headers(token, "POST", root_path, identity, create_body)
            changed_identity["X-Sandbox-Identity"] = base64.urlsafe_b64encode(canonical_bytes(other_identity)).decode("ascii")
            await expect("modified_identity_rejected", "POST", root_path, create_body, headers=changed_identity, expected=401)
            expired = request_headers(token, "POST", root_path, identity, create_body)
            expired["X-Sandbox-Issued-At"] = str(int(time.time()) - 60)
            expired["X-Sandbox-Signature"] = _signature(token, "POST", root_path, expired["X-Sandbox-Issued-At"], expired["X-Sandbox-Identity"], create_body)
            await expect("valid_but_expired_signature_rejected", "POST", root_path, create_body, headers=expired, expected=401)
            assert store.count_non_closed_sessions() == 0
            passed("authentication_rejections_have_no_resource_side_effects")

            for name, method, path, body in (
                ("declared_json_size_limit", "POST", root_path, b"x" * 4097),
                ("declared_input_size_limit", "PUT", session_path + "/input", b"x" * 257),
                ("declared_script_size_limit", "PUT", session_path + "/script", b"x" * 129),
            ):
                await expect(name, method, path, body, expected=413, code="request_size_limit")
                await expect("chunked_" + name, method, path, body, chunks=True, expected=413, code="request_size_limit")
            await expect("invalid_json_fields", "POST", root_path, canonical_bytes({"request_id": request_id}), expected=422)
            await expect("body_on_status_rejected", "GET", session_path, b"hidden", expected=422)
            await expect("body_on_heartbeat_rejected", "POST", session_path + "/heartbeat", b"hidden", expected=422)
            encoded = request_headers(token, "PUT", session_path + "/input", identity, INPUT)
            encoded["Content-Encoding"] = "gzip"
            await expect("encoded_input_rejected", "PUT", session_path + "/input", INPUT, headers=encoded, expected=415)
            await expect("authorized_missing_session_returns_404", "GET", root_path + "/" + missing_id, expected=404)
            assert store.count_non_closed_sessions() == 0

            lock_id = _sql("SELECT IS_USED_LOCK(%s) AS owner", ("causalagent:sandbox-controller",), fetch=True)[0]["owner"]
            assert lock_id is not None
            duplicate, _ = _launch(directory, token_file, "duplicate-controller")
            processes.append(duplicate)
            assert await _wait_exit(duplicate) != 0
            assert _sql("SELECT IS_USED_LOCK(%s) AS owner", ("causalagent:sandbox-controller",), fetch=True)[0]["owner"] == lock_id
            await _ready(client, process)
            passed("second_real_controller_process_cannot_acquire_singleton")

            await expect("unreachable_upstream_create_returns_503", "POST", root_path, create_body, expected=503)
            row = store.get_session(request_id)
            assert row and row["status"] == "uncertain" and row["remote_sandbox_id"] is None
            assert store.count_non_closed_sessions() == 1
            version = row["version"]
            passed("failed_create_retains_uncertain_capacity_reservation")
            response = await expect("owner_can_query_uncertain_record", "GET", session_path, expected=200)
            assert response.json()["state"] == "uncertain"
            await expect("same_create_replay_does_not_retry_upstream", "POST", root_path, create_body, expected=409, code="session_uncertain")
            assert store.get_session(request_id)["version"] == version
            alternate_body = canonical_bytes({"request_id": hashlib.sha256(uuid4().bytes).hexdigest(), "source_hash": SOURCE_HASH})
            await expect("new_request_id_cannot_bypass_existing_job_record", "POST", root_path, alternate_body, expected=409, code="sandbox_request_conflict")
            response = await expect("other_job_cannot_bypass_occupied_capacity", "POST", root_path, alternate_body,
                                    owner=other_identity, expected=409, code="sandbox_capacity_unavailable")
            assert response.headers.get("Retry-After") == "60"
            assert store.count_non_closed_sessions() == 1

            execute_body = canonical_bytes({"operation_id": "e" * 64, "script_hash": "f" * 64, "plan_version": "a" * 64})
            await expect("latest_plan_confirmation_enforced_by_service", "POST", session_path + "/execute", execute_body,
                         expected=403, code="sandbox_execution_revoked")
            _input(identity, "revise processing scope", 2)
            execute_body = canonical_bytes({"operation_id": "e" * 64, "script_hash": "f" * 64, "plan_version": PLAN_VERSION})
            await expect("new_input_invalidates_previous_plan_confirmation", "POST", session_path + "/execute", execute_body,
                         expected=403, code="sandbox_execution_revoked")
            assert store.get_session(request_id)["operation_id"] is None

            _sql("UPDATE analysis_jobs SET lease_epoch = lease_epoch + 1 WHERE job_id = %s", (identity["job_id"],))
            for name, method, path, body in (
                ("status", "GET", session_path, b""),
                ("delete", "DELETE", session_path, b""),
                ("input", "PUT", session_path + "/input", INPUT),
                ("script", "PUT", session_path + "/script", b"print(1)"),
                ("preview", "GET", session_path + "/preview", b""),
                ("output", "GET", session_path + "/output", b""),
                ("execute", "POST", session_path + "/execute", execute_body),
                ("heartbeat", "POST", session_path + "/heartbeat", b""),
                ("create", "POST", root_path, alternate_body),
            ):
                await expect("revoked_epoch_rejected_" + name, method, path, body, expected=403, code="sandbox_execution_revoked")
            assert store.count_non_closed_sessions() == 1
            assert store.get_session(request_id)["version"] == version
            passed("revoked_requests_do_not_modify_lifecycle_record")

            # Kill the real named-lock connection from a separate MySQL session.
            _sql("KILL CONNECTION " + str(int(lock_id)))
            await expect("lost_singleton_makes_health_unready", "GET", "/healthz", headers={}, expected=503)
            await expect("lost_singleton_denies_new_request", "POST", root_path, alternate_body, owner=other_identity,
                         expected=503, code="controller_not_ready")
            assert store.count_non_closed_sessions() == 1
            replacement = ControllerSingleton()
            replacement.acquire()
            try:
                replacement.ensure_owned()
                try:
                    replacement.acquire()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("repeat singleton acquire unexpectedly succeeded")
                replacement.ensure_owned()
            finally:
                replacement.close()
                replacement.close()
            passed("terminated_lock_can_be_reacquired_and_repeat_acquire_is_rejected")

            recovery, _ = _launch(directory, token_file, "recovery-controller")
            processes.append(recovery)
            assert await _wait_exit(recovery) != 0
            assert _sql("SELECT IS_USED_LOCK(%s) AS owner", ("causalagent:sandbox-controller",), fetch=True)[0]["owner"] is None
            assert store.get_session(request_id)["status"] == "uncertain"
            assert store.count_non_closed_sessions() == 1
            passed("startup_remote_reconciliation_failure_blocks_admission_and_retains_uncertain")
            report["retained_request_id"] = request_id
            report["non_closed_sessions"] = 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    await _wait_exit(process)
                except AssertionError:
                    process.kill()
                    await _wait_exit(process)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--token-file")
    args = parser.parse_args()
    _boundary()
    if args.serve:
        uvicorn.run(create_app(_settings(args.token_file)), host="127.0.0.1", port=args.port,
                    workers=1, access_log=False, log_level="warning")
        return 0
    directory = Path("/tmp/sandbox-controller-http") / uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    report = {"status": "running", "checks": [], "remote_sandbox_execution": "not_executed",
              "evidence": "real TCP HTTP, real MySQL, unreachable loopback upstream"}
    try:
        asyncio.run(_run(directory, report))
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["failure_type"] = type(exc).__name__
    (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "passed_checks": len(report["checks"]),
                      "report": str(directory / "report.json"), "remote_sandbox_execution": "not_executed"}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
