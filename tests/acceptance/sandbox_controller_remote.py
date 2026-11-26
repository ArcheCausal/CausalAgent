"""显式验收真实控制 HTTP、MySQL、OpenSandbox 与本机处理镜像。

只允许本次隔离环境；保留记录和报告，不删除库、卷或他人资源。
执行需同时设置 SANDBOX_CONTROLLER_HTTP_ACCEPTANCE=1、
SANDBOX_CONTROLLER_REMOTE_ACCEPTANCE=1，以及 SANDBOX_CONTROLLER_ACCEPTANCE_IMAGE
为同机 daemon 上已有的处理镜像 manifest digest，再显式运行本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from Agent.data_processing.opensandbox_runtime import OpenSandboxSettings, _BoundedOutput, _agent_command_options
from Agent.data_processing.sandbox_client import SandboxControllerProvider, SandboxControllerSettings, SandboxLeaseLostError
from Database import sandbox_sessions as store
from app.db import get_write_connection
from sandbox_controller.api import create_app
from sandbox_controller.config import ControllerSettings
from sandbox_controller.protocol import canonical_bytes, request_headers
from sandbox_controller.remote import OpenSandboxRemote
from sandbox_controller_http import INPUT, SOURCE_HASH, PLAN_VERSION, _boundary, _identity, _port, _ready, _sql
from opensandbox.models.execd import ExecutionHandlers
from opensandbox.exceptions import SandboxApiException


IMAGE = os.environ.get("SANDBOX_CONTROLLER_ACCEPTANCE_IMAGE", "")


def _settings(token_file: Path) -> ControllerSettings:
    return ControllerSettings(
        remote=OpenSandboxSettings(
            api_url="http://opensandbox:8080",
            api_key=Path("/tmp/opensandbox-api-key").read_text().strip(),
            image=IMAGE, sandbox_ttl_seconds=300,
            allow_http=True,
            command_timeout_seconds=50, api_timeout_seconds=45,
        ),
        token=token_file.read_text().strip(), capacity=4,
        reconcile_interval_seconds=1,
    )


def _launch(directory: Path, token_file: Path):
    port = _port()
    log = (directory / ("controller-" + uuid4().hex + ".log")).open("wb")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", "--port", str(port),
         "--token-file", str(token_file)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
    )
    log.close()
    return process, port


async def _until(predicate, timeout=65):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("real dependency condition did not complete before deadline")


async def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 15)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, 5)


async def _run(directory, report):
    token_file = directory / "controller.token"
    token_file.write_text(secrets.token_urlsafe(48))
    token_file.chmod(0o600)
    settings = _settings(token_file)
    token = settings.token
    remote = OpenSandboxRemote(settings.remote)
    processes = []
    http_tasks = []
    owned_requests = []

    def passed(name):
        report["checks"].append({"name": name, "status": "passed"})

    async def row_when(request_id, predicate):
        row = await asyncio.to_thread(store.get_session, request_id)
        return row if row is not None and predicate(row) else None

    async def request(client, identity, method, path, body=b"", expected=200):
        response = await client.request(method, path, content=body,
            headers=request_headers(token, method, path, identity, body))
        assert response.status_code == expected, f"unexpected HTTP {response.status_code} for {method} {path}"
        return response

    def new_identity():
        identity = _identity()
        request_id = hashlib.sha256(uuid4().bytes).hexdigest()
        owned_requests.append(request_id)
        return identity, request_id, "/v1/sessions/" + request_id

    async def create(client):
        identity, request_id, path = new_identity()
        await request(client, identity, "POST", "/v1/sessions",
                      canonical_bytes({"request_id": request_id, "source_hash": SOURCE_HASH}))
        return identity, request_id, path

    async def remote_file(observer, path):
        try:
            return await observer._sandbox.files.read_file(path)
        except SandboxApiException as exc:
            if exc.status_code == 404:
                return None
            raise

    async def closed(request_id):
        return await _until(lambda: row_when(request_id, lambda r: r["status"] == "closed"), 20)

    process, port = _launch(directory, token_file)
    processes.append(process)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=70, trust_env=False) as client:
            await _ready(client, process)
            passed("real_upstream_controller_ready")

            # Hold an actual Job row lock until the complete signed request has
            # disconnected. Uvicorn must finish the original request itself.
            identity, request_id, path = new_identity()
            blocking = get_write_connection()
            try:
                blocking.start_transaction()
                with blocking.cursor() as cursor:
                    cursor.execute("SELECT job_id FROM analysis_jobs WHERE job_id=%s FOR UPDATE", (identity["job_id"],))
                    cursor.fetchone()
                body = canonical_bytes({"request_id": request_id, "source_hash": SOURCE_HASH})
                headers = request_headers(token, "POST", "/v1/sessions", identity, body)
                headers.update({"Host": "127.0.0.1", "Content-Length": str(len(body)), "Connection": "close"})
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                payload = "POST /v1/sessions HTTP/1.1\r\n" + "\r\n".join(f"{k}: {v}" for k, v in headers.items()) + "\r\n\r\n"
                writer.write(payload.encode() + body)
                await writer.drain()

                async def blocked():
                    rows = await asyncio.to_thread(_sql, "SELECT COUNT(*) AS n FROM performance_schema.data_lock_waits", fetch=True)
                    return rows[0]["n"] > 0

                await _until(blocked, 10)
                writer.close()
                await writer.wait_closed()
                blocking.commit()
            finally:
                blocking.close()
            row = await _until(lambda: row_when(request_id, lambda r: bool(r["remote_sandbox_id"])))
            assert row["status"] == "creating" and row["operation_id"] is None
            assert await remote.find(request_id) == [row["remote_sandbox_id"]]
            assert store.count_non_closed_sessions() >= 1
            passed("client_disconnect_continues_original_create_and_retains_capacity")
            await _stop(process)

        process, port = _launch(directory, token_file)
        processes.append(process)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=70, trust_env=False) as client:
            await _ready(client, process)
            row = store.get_session(request_id)
            assert row["status"] == "closed" and row["operation_id"] is None
            assert not await remote.exists(row["remote_sandbox_id"])
            passed("startup_destroys_disconnected_create_without_execution")

            identity, request_id, path = await create(client)
            await request(client, identity, "PUT", path + "/input", INPUT)
            assert (await request(client, identity, "GET", path + "/preview")).json()["preview"] == INPUT.decode()
            initial_version = store.get_session(request_id)["version"]
            await request(client, identity, "PUT", path + "/input", INPUT)
            assert store.get_session(request_id)["version"] == initial_version
            passed("real_processing_image_initialize_preview_and_input_replay")
            script = b"from pathlib import Path\nimport time\np=Path('/workspace/output')\n(p/'started').write_text('yes')\ntime.sleep(4)\n(p/'prepared.csv').write_bytes(Path('/workspace/raw/original.csv').read_bytes())\nprint('done')\n"
            await request(client, identity, "PUT", path + "/script", script)
            operation = canonical_bytes({"operation_id": hashlib.sha256(uuid4().bytes).hexdigest(),
                                        "script_hash": hashlib.sha256(script).hexdigest(), "plan_version": PLAN_VERSION})
            execution = asyncio.create_task(request(client, identity, "POST", path + "/execute", operation))
            http_tasks.append(execution)
            row = await _until(lambda: row_when(request_id, lambda r: r["operation_status"] == "pending"))
            begin_version = row["version"]
            started = time.monotonic()
            await request(client, identity, "POST", path + "/execute", operation, expected=409)
            await request(client, identity, "PUT", path + "/script", script, expected=409)
            assert time.monotonic() - started < 2
            passed("pending_execute_and_script_overwrite_rejected_without_waiting_for_command")
            await request(client, identity, "POST", path + "/heartbeat")
            assert store.get_session(request_id)["version"] > begin_version
            result = (await execution).json()
            assert result["exit_code"] == 0 and result["stdout"].strip() == "done"
            assert (await request(client, identity, "POST", path + "/execute", operation)).json() == result
            assert (await request(client, identity, "GET", path + "/output")).content == INPUT
            passed("heartbeat_version_change_finish_cas_output_and_execute_replay")
            observer = await remote.connect(store.get_session(request_id)["remote_sandbox_id"])
            try:
                capture = _BoundedOutput(4096, interrupt=observer._sandbox.commands.interrupt)

                async def revoked_before_id(event):
                    # An actual command receives the actual revoke before the
                    # actual SDK init event is delivered to the capture.
                    await capture.interrupt_active()
                    await capture.on_init(event)

                started = time.monotonic()
                interrupted = await observer._sandbox.commands.run(
                    "python3 -c 'import time; time.sleep(30)'",
                    opts=_agent_command_options(settings.remote, timeout=35),
                    handlers=ExecutionHandlers(on_init=revoked_before_id, skip_accumulation=True),
                )
                assert capture._interrupted and interrupted.exit_code != 0
                assert time.monotonic() - started < 10
            finally:
                await observer._sandbox.close()
            passed("real_command_revoked_before_execution_id_arrives")
            await request(client, identity, "DELETE", path)
            await closed(request_id)
            passed("normal_delete_confirms_remote_absence")

            identity, request_id, path = await create(client)
            await request(client, identity, "PUT", path + "/input", INPUT)
            script = b"from pathlib import Path\nimport time\nPath('/workspace/output/started').write_text('yes')\ntime.sleep(40)\nPath('/workspace/output/late').write_text('unsafe')\n"
            await request(client, identity, "PUT", path + "/script", script)
            operation = canonical_bytes({"operation_id": hashlib.sha256(uuid4().bytes).hexdigest(),
                                        "script_hash": hashlib.sha256(script).hexdigest(), "plan_version": PLAN_VERSION})
            execution = asyncio.create_task(request(client, identity, "POST", path + "/execute", operation, expected=403))
            http_tasks.append(execution)
            row = store.get_session(request_id)
            observer = await remote.connect(row["remote_sandbox_id"])
            try:
                await _until(lambda: remote_file(observer, "/workspace/output/started"), 15)
                started = time.monotonic()
                _sql("UPDATE analysis_jobs SET status='canceled', execution_state='draining' WHERE job_id=%s", (identity["job_id"],))
                await execution
                row = await closed(request_id)
                assert time.monotonic() - started < 15
                assert row["operation_status"] == "uncertain"
                assert not await remote.exists(row["remote_sandbox_id"])
            finally:
                await observer._sandbox.close()
            passed("reconcile_revokes_real_running_command_while_session_lock_is_held")

            identity, request_id, path = await create(client)
            row = store.get_session(request_id)
            observer = await remote.connect(row["remote_sandbox_id"])
            try:
                initializing = asyncio.create_task(request(client, identity, "PUT", path + "/input", INPUT, expected=403))
                http_tasks.append(initializing)
                await _until(lambda: remote_file(observer, "/workspace/raw/original.csv"), 15)
                _sql("UPDATE analysis_jobs SET status='canceled', execution_state='draining' WHERE job_id=%s", (identity["job_id"],))
                await initializing
                row = await closed(request_id)
                assert row["operation_id"] is None and not await remote.exists(row["remote_sandbox_id"])
            finally:
                await observer._sandbox.close()
            passed("reconcile_revokes_real_initialization_without_promoting_active")

            identity, request_id, path = new_identity()
            provider = SandboxControllerProvider(SandboxControllerSettings(
                controller_url=f"http://127.0.0.1:{port}", token=token,
                timeout_seconds=60, command_timeout_seconds=50,
            ))
            async with provider.open_session(
                **identity, source_hash=SOURCE_HASH, request_id=request_id,
                execution_guard=None, original_csv=INPUT, plan_version=PLAN_VERSION,
            ) as session:
                assert await session.read_preview("raw/original.csv") == INPUT.decode()
                script = "from pathlib import Path\nPath('/workspace/output/prepared.csv').write_bytes(Path('/workspace/raw/original.csv').read_bytes())\nprint('worker-client')\n"
                await session.write_script("scripts/process.py", script)
                result = await session.run("python3 /workspace/scripts/process.py")
                assert result.exit_code == 0 and result.stdout.strip() == "worker-client"
                assert await session.read_output("output/prepared.csv") == INPUT
            row = await closed(request_id)
            assert row["operation_status"] == "succeeded" and not await remote.exists(row["remote_sandbox_id"])
            passed("actual_worker_client_create_initialize_execute_output_and_finally_cleanup")

            identity, request_id, path = new_identity()
            async with provider.open_session(
                **identity, source_hash=SOURCE_HASH, request_id=request_id,
                execution_guard=None, original_csv=INPUT, plan_version=PLAN_VERSION,
            ) as session:
                _sql("UPDATE analysis_jobs SET lease_epoch=lease_epoch+1 WHERE job_id=%s", (identity["job_id"],))
                try:
                    await session.read_preview("raw/original.csv")
                except SandboxLeaseLostError:
                    passed("actual_worker_client_rejects_replaced_lease")
                else:
                    raise AssertionError("worker client retained a fenced lease")
            await closed(request_id)
            passed("background_cleanup_after_worker_client_loses_delete_authority")
    finally:
        for task in http_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*http_tasks, return_exceptions=True)
        for child in processes:
            await _stop(child)
        report["owned_requests"] = owned_requests
        report["retained_states"] = {rid: (store.get_session(rid) or {}).get("status") for rid in owned_requests}


async def _resilience(directory, report, *, upstream_fault):
    token_file = directory / "controller.token"
    token_file.write_text(secrets.token_urlsafe(48))
    token_file.chmod(0o600)
    settings = _settings(token_file)
    remote = OpenSandboxRemote(settings.remote)
    processes, owned, tasks = [], [], []
    baseline = store.count_non_closed_sessions()
    assert baseline < settings.capacity, "no free capacity for isolated resilience acceptance"

    def passed(name):
        report["checks"].append({"name": name, "status": "passed"})

    async def exchange(client, identity, method, path, body=b""):
        return await client.request(method, path, content=body,
            headers=request_headers(settings.token, method, path, identity, body))

    def candidate():
        identity = _identity()
        rid = hashlib.sha256(uuid4().bytes).hexdigest()
        owned.append(rid)
        return identity, rid, "/v1/sessions/" + rid

    async def create(client):
        identity, rid, path = candidate()
        response = await exchange(client, identity, "POST", "/v1/sessions",
            canonical_bytes({"request_id": rid, "source_hash": SOURCE_HASH}))
        assert response.status_code == 200
        return identity, rid, path

    async def state(rid, expected):
        row = await asyncio.to_thread(store.get_session, rid)
        return row if row is not None and row["status"] == expected else None

    async def file_started(observer):
        try:
            return await observer._sandbox.files.read_file("/workspace/output/started")
        except SandboxApiException as exc:
            if exc.status_code == 404:
                return None
            raise

    async def gate(phase):
        (directory / "phase.json").write_text(json.dumps({"phase": phase}))
        print(json.dumps({"phase": phase, "directory": str(directory)}), flush=True)
        async def acknowledged():
            return (directory / (phase + ".done")).exists()
        await _until(acknowledged, 120)

    process, port = _launch(directory, token_file)
    processes.append(process)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=70, trust_env=False) as client:
            await _ready(client, process)
            identity, rid, path = await create(client)
            assert (await exchange(client, identity, "PUT", path + "/input", INPUT)).status_code == 200
            script = b"from pathlib import Path\nimport time\nPath('/workspace/output/started').write_text('one invocation')\ntime.sleep(40)\nprint('finished')\n"
            assert (await exchange(client, identity, "PUT", path + "/script", script)).status_code == 200
            operation = canonical_bytes({"operation_id": hashlib.sha256(uuid4().bytes).hexdigest(),
                "script_hash": hashlib.sha256(script).hexdigest(), "plan_version": PLAN_VERSION})
            execution = asyncio.create_task(exchange(client, identity, "POST", path + "/execute", operation))
            tasks.append(execution)
            observer = await remote.connect(store.get_session(rid)["remote_sandbox_id"])
            try:
                await _until(lambda: file_started(observer), 15)
                before = store.get_session(rid)
                assert before["operation_status"] == "pending"
                process.kill()
                await asyncio.to_thread(process.wait, 5)
                assert process.returncode == -9, "controller must actually exit via SIGKILL"
                interrupted_http = (await asyncio.gather(execution, return_exceptions=True))[0]
                assert isinstance(interrupted_http, httpx.HTTPError)
                after = store.get_session(rid)
                assert after["status"] == "active" and after["operation_status"] == "pending"
                assert await remote.exists(after["remote_sandbox_id"])
                assert store.count_non_closed_sessions() == baseline + 1
            finally:
                await observer._sandbox.close()
            report["crash_exit_code"] = process.returncode
            passed("actual_sigkill_after_remote_script_started_retains_pending_and_capacity")

        process, port = _launch(directory, token_file)
        processes.append(process)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=70, trust_env=False) as client:
            await _ready(client, process)
            after = store.get_session(rid)
            assert after["status"] == "closed" and after["operation_status"] == "uncertain"
            assert after["operation_id"] == before["operation_id"] and after["operation_result"] is None
            assert not await remote.exists(after["remote_sandbox_id"]) and not await remote.find(rid)
            assert store.count_non_closed_sessions() == baseline
            passed("startup_cleans_sigkill_pending_without_reexecution_and_releases_capacity")

            keepers = []
            for _ in range(settings.capacity - baseline - 1):
                keepers.append(await create(client))
            contenders = [candidate(), candidate()]
            responses = await asyncio.gather(*[
                exchange(client, owner, "POST", "/v1/sessions",
                    canonical_bytes({"request_id": request_id, "source_hash": SOURCE_HASH}))
                for owner, request_id, _ in contenders
            ])
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert store.count_non_closed_sessions() == settings.capacity
            for contender, response in zip(contenders, responses):
                owner, request_id, session_path = contender
                if response.status_code == 409:
                    assert response.json()["code"] == "sandbox_capacity_unavailable"
                    assert "Retry-After" in response.headers
                    assert store.get_session(request_id) is None and not await remote.find(request_id)
                else:
                    keepers.append(contender)
            passed("real_http_concurrent_last_capacity_admits_exactly_one_remote_create")
            for owner, request_id, session_path in keepers:
                assert (await exchange(client, owner, "DELETE", session_path)).status_code == 200
                await _until(lambda: state(request_id, "closed"), 20)
            assert store.count_non_closed_sessions() == baseline
            passed("http_capacity_test_resources_confirmed_closed")

            if upstream_fault:
                owner, request_id, session_path = await create(client)
                before = store.get_session(request_id)
                await gate("stop_upstream")
                response = await exchange(client, owner, "DELETE", session_path)
                assert response.status_code == 503
                after = store.get_session(request_id)
                assert after["status"] == "uncertain" and after["remote_sandbox_id"] == before["remote_sandbox_id"]
                assert store.count_non_closed_sessions() == baseline + 1
                replacement = hashlib.sha256(uuid4().bytes).hexdigest()
                response = await exchange(client, owner, "POST", "/v1/sessions",
                    canonical_bytes({"request_id": replacement, "source_hash": SOURCE_HASH}))
                assert response.status_code == 409 and store.get_session(replacement) is None
                passed("stopped_real_upstream_delete_retains_uncertain_capacity_and_blocks_replacement")
                await gate("restore_upstream")
                after = await _until(lambda: state(request_id, "closed"), 65)
                assert not await remote.exists(after["remote_sandbox_id"])
                assert store.count_non_closed_sessions() == baseline
                passed("restored_real_upstream_background_delete_confirms_absence_and_releases_capacity")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for child in processes:
            await _stop(child)
        report["owned_requests"] = owned
        report["retained_states"] = {rid: (store.get_session(rid) or {}).get("status") for rid in owned}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--scenario", choices=("chain", "resilience"), default="chain")
    parser.add_argument("--upstream-fault", action="store_true",
                        help="resilience only: require stop/restore acknowledgements after actual Docker operations")
    args = parser.parse_args()
    _boundary()
    if os.environ.get("SANDBOX_CONTROLLER_REMOTE_ACCEPTANCE") != "1":
        raise RuntimeError("explicit real upstream acceptance opt in required")
    if args.serve:
        uvicorn.run(create_app(_settings(args.token_file)), host="127.0.0.1", port=args.port,
                    workers=1, access_log=False, log_level="warning")
        return
    directory = Path("/tmp/sandbox-controller-remote") / uuid4().hex
    directory.mkdir(parents=True)
    report = {"status": "running", "evidence": "real TCP HTTP, MySQL, OpenSandbox and processing image", "checks": []}
    try:
        if args.scenario == "resilience":
            asyncio.run(_resilience(directory, report, upstream_fault=args.upstream_fault))
        else:
            asyncio.run(_run(directory, report))
        report["status"] = "passed"
    except BaseException as exc:
        report.update(status="failed", failure_type=type(exc).__name__)
        raise
    finally:
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({"status": report["status"], "passed_checks": len(report["checks"]),
                          "report": str(directory / "report.json")}))


if __name__ == "__main__":
    main()
