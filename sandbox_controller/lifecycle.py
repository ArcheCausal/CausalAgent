"""短事务资源账本与 OpenSandbox 操作之间的生命周期协调。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from pathlib import PurePosixPath
from typing import Any, Mapping

from Database import sandbox_sessions as store
from sandbox_controller.config import ControllerSettings
from sandbox_controller.protocol import canonical_bytes
from sandbox_controller.remote import OpenSandboxRemote
from sandbox_controller.singleton import ControllerSingleton


logger = logging.getLogger(__name__)


class ControllerError(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class _SessionGuard:
    def __init__(self, controller: "SandboxController", session_id: str, identity: Mapping[str, Any]) -> None:
        self.controller = controller
        self.session_id = session_id
        self.identity = dict(identity)

    async def ensure_active(self) -> None:
        await self.controller.ensure_owned()
        row = await asyncio.to_thread(store.require_owner, self.session_id, self.identity)
        if row["status"] not in {"creating", "active"} or row["expired"]:
            raise ControllerError("session_execution_revoked", 403)


class SandboxController:
    def __init__(self, settings: ControllerSettings) -> None:
        self.settings = settings
        self.remote = OpenSandboxRemote(settings.remote)
        self.singleton = ControllerSingleton()
        self.ready = False
        self._admission = asyncio.Lock()
        self._singleton_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._sessions: dict[str, Any] = {}

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def _update(self, request_id: str, *, remote_absent_confirmed: bool = False, **changes: Any) -> dict[str, Any]:
        for _ in range(8):
            await self.ensure_owned()
            row = await asyncio.to_thread(store.get_session, request_id)
            if row is None:
                raise ControllerError("session_missing", 404)
            if row["status"] == "closed":
                if changes.get("status") == "closed":
                    return row
                raise ControllerError("session_closed")
            try:
                return await asyncio.to_thread(store.update_session, request_id, expected_version=row["version"], changes=changes, remote_absent_confirmed=remote_absent_confirmed)
            except store.SandboxVersionError:
                continue
        raise ControllerError("session_update_contended", 503)

    async def _finish(self, request_id: str, operation_id: str, digest: str, result: dict[str, Any]) -> None:
        for _ in range(8):
            await self.ensure_owned()
            row = await asyncio.to_thread(store.get_session, request_id)
            if row is None or row["status"] != "active":
                raise ControllerError("execution_result_no_longer_writable")
            try:
                await asyncio.to_thread(store.finish_operation, request_id, operation_id=operation_id, operation_digest=digest, expected_version=row["version"], operation_status="succeeded", operation_result=result)
                return
            except store.SandboxVersionError:
                continue
        raise ControllerError("execution_result_contended", 503)

    async def ensure_owned(self) -> None:
        async with self._singleton_lock:
            try:
                await asyncio.to_thread(self.singleton.ensure_owned)
            except Exception:
                self.ready = False
                raise ControllerError("controller_authority_unavailable", 503) from None

    async def _timing(self, request_id: str, stage: str) -> None:
        """写入固定的阶段时间；观测失败不改变创建、执行或清理结果。"""
        try:
            await asyncio.to_thread(store.record_stage_timing, request_id, stage)
        except Exception as exc:
            logger.warning("sandbox stage timing not recorded: %s/%s", stage, type(exc).__name__)

    async def startup(self) -> None:
        await asyncio.to_thread(self.singleton.acquire)
        try:
            await self.reconcile(startup=True)
            self.ready = True
        except BaseException:
            await asyncio.to_thread(self.singleton.close)
            raise

    async def shutdown(self) -> None:
        self.ready = False
        for session in list(self._sessions.values()):
            await session.revoke()
            await session._sandbox.close()
        self._sessions.clear()
        await asyncio.to_thread(self.singleton.close)

    async def create(self, identity: Mapping[str, Any], request_id: str, source_hash: str) -> dict[str, Any]:
        await self.ensure_owned()
        digest = hashlib.sha256(canonical_bytes({"identity": identity, "source_hash": source_hash, "image": self.settings.remote.image})).hexdigest()
        async with self.lock(request_id):
            async with self._admission:
                await self.ensure_owned()
                row, created = await asyncio.to_thread(
                    store.reserve_session, identity, request_id=request_id, request_digest=digest,
                    source_hash=source_hash, capacity=self.settings.capacity, ttl_seconds=self.settings.remote.sandbox_ttl_seconds,
                )
                if created:
                    await self._timing(request_id, "reserve_committed")
            if not created:
                if row["status"] in {"active", "creating"} and row.get("remote_sandbox_id"):
                    return {"session_id": request_id, "state": row["status"]}
                raise ControllerError("session_" + row["status"])
            sandbox = None
            try:
                sandbox = await self.remote.create(request_id, str(identity["job_id"]))
                await self._timing(request_id, "remote_created")
                await self._update(request_id, status="creating", remote_sandbox_id=sandbox.id)
                session = self.remote.session(sandbox, guard=_SessionGuard(self, request_id, identity))
                self._sessions[request_id] = session
                return {"session_id": request_id, "state": "creating"}
            except BaseException:
                try:
                    await self._update(request_id, status="uncertain")
                finally:
                    if sandbox is not None:
                        await sandbox.close()
                raise

    async def owner(self, session_id: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        await self.ensure_owned()
        return await asyncio.to_thread(store.require_owner, session_id, identity)

    async def _session(self, row: Mapping[str, Any], identity: Mapping[str, Any]) -> Any:
        session_id = row["request_id"]
        session = self._sessions.get(session_id)
        if session is None:
            # A previous controller process may have died during an operation.
            # Its resources are reconciled before this process admits requests.
            raise ControllerError("session_not_recoverable")
        return session

    async def initialize(self, session_id: str, identity: Mapping[str, Any], data: bytes) -> None:
        async with self.lock(session_id):
            row = await self.owner(session_id, identity)
            if not data or len(data) > self.settings.remote.max_input_bytes or hashlib.sha256(data).hexdigest() != row["source_hash"]:
                raise ControllerError("frozen_input_mismatch", 422)
            if row["status"] == "active":
                return
            if row["status"] != "creating" or not row.get("remote_sandbox_id"):
                raise ControllerError("session_not_initializable")
            session = await self._session(row, identity)
            try:
                await session._initialize(data)
                await session._ensure_active()
                await self._timing(session_id, "initialized")
                await self._update(session_id, status="active")
            except BaseException:
                await self._update(session_id, status="deleting")
                raise

    async def write_script(self, session_id: str, identity: Mapping[str, Any], data: bytes) -> str:
        row = await self.owner(session_id, identity)
        if row["status"] != "active" or row.get("operation_id"):
            raise ControllerError("script_write_not_allowed")
        async with self.lock(session_id):
            row = await self.owner(session_id, identity)
            if row["status"] != "active" or row.get("operation_id"):
                raise ControllerError("script_write_not_allowed")
            if not data or len(data) > self.settings.remote.max_command_bytes:
                raise ControllerError("script_size_limit", 413)
            session = await self._session(row, identity)
            return await session.write_script("scripts/process.py", data.decode("utf-8"))

    async def preview(self, session_id: str, identity: Mapping[str, Any]) -> str:
        async with self.lock(session_id):
            row = await self.owner(session_id, identity)
            if row["status"] != "active":
                raise ControllerError("session_not_active")
            return await (await self._session(row, identity)).read_preview("raw/original.csv")

    async def execute(self, session_id: str, identity: Mapping[str, Any], operation_id: str, script_hash: str, plan_version: str) -> dict[str, Any]:
        execution_identity = {**identity, "plan_version": plan_version}
        digest = hashlib.sha256(canonical_bytes({"script_hash": script_hash, "plan_version": plan_version, "entrypoint": "python3 /workspace/scripts/process.py"})).hexdigest()
        row = await self.owner(session_id, identity)
        if row.get("operation_id"):
            row, _ = await asyncio.to_thread(store.begin_operation, session_id, execution_identity, operation_id=operation_id, operation_type="execute", operation_digest=digest)
            if row["operation_status"] == "succeeded":
                return row["operation_result"]
            raise ControllerError("operation_pending")
        async with self.lock(session_id):
            row = await self.owner(session_id, identity)
            row, started = await asyncio.to_thread(store.begin_operation, session_id, execution_identity, operation_id=operation_id, operation_type="execute", operation_digest=digest)
            if not started:
                if row["operation_status"] == "succeeded":
                    return row["operation_result"]
                raise ControllerError("operation_pending")
            await self._timing(session_id, "execute_started")
            try:
                session = await self._session(row, identity)
                script = await session._read_bounded(PurePosixPath("/workspace/scripts/process.py"), self.settings.remote.max_command_bytes)
                if hashlib.sha256(script).hexdigest() != script_hash:
                    raise ControllerError("script_hash_mismatch", 422)
                await asyncio.to_thread(store.require_plan_confirmation, session_id, execution_identity)
                result = asdict(await session.run("python3 /workspace/scripts/process.py"))
                await self._timing(session_id, "execute_finished")
                await asyncio.to_thread(store.require_plan_confirmation, session_id, execution_identity)
                await self._finish(session_id, operation_id, digest, result)
                return result
            except BaseException:
                await self._uncertain_operation(session_id, operation_id, digest)
                raise

    async def _uncertain_operation(self, request_id: str, operation_id: str, digest: str) -> None:
        for _ in range(8):
            await self.ensure_owned()
            row = await asyncio.to_thread(store.get_session, request_id)
            if row is None or row["status"] == "closed" or row["operation_status"] == "succeeded":
                return
            changes = {} if row["status"] == "deleting" else {"status": "uncertain"}
            try:
                await asyncio.to_thread(
                    store.finish_operation, request_id, operation_id=operation_id,
                    operation_digest=digest, expected_version=row["version"],
                    operation_status="uncertain", operation_result=None, changes=changes,
                )
                return
            except store.SandboxVersionError:
                continue
        raise ControllerError("execution_result_contended", 503)

    async def output(self, session_id: str, identity: Mapping[str, Any]) -> bytes:
        async with self.lock(session_id):
            row = await self.owner(session_id, identity)
            if row["status"] != "active" or row.get("operation_status") != "succeeded" or row.get("operation_result", {}).get("exit_code") != 0:
                raise ControllerError("output_not_available")
            return await (await self._session(row, identity)).read_output("output/prepared.csv")

    async def heartbeat(self, session_id: str, identity: Mapping[str, Any]) -> None:
        # No per-session execution lock: a long command still needs heartbeats.
        await self.owner(session_id, identity)
        await asyncio.to_thread(store.heartbeat_session, session_id, identity)

    async def close(self, session_id: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        row = await self.owner(session_id, identity)
        if row["status"] == "closed":
            return {"session_id": session_id, "state": "closed"}
        await self._update(session_id, status="deleting")
        session = self._sessions.get(session_id)
        if session is not None:
            await session.revoke()
        async with self.lock(session_id):
            row = await asyncio.to_thread(store.get_session, session_id)
            await self._cleanup(row)
        return {"session_id": session_id, "state": (await asyncio.to_thread(store.get_session, session_id))["status"]}

    async def _cleanup(self, row: Mapping[str, Any]) -> None:
        if row["status"] == "closed":
            return
        request_id = row["request_id"]
        retry_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=self.settings.reconcile_interval_seconds)
        await self._update(request_id, status="deleting", next_retry_at=retry_at)
        if row.get("operation_status") == "pending":
            await self._uncertain_operation(request_id, row["operation_id"], row["operation_digest"])
        try:
            remote_ids = set(await self.remote.find(request_id))
            if row.get("remote_sandbox_id"):
                remote_ids.add(row["remote_sandbox_id"])
            if not remote_ids:
                # One empty discovery response cannot prove creation was absent.
                await self._update(request_id, status="uncertain")
                return
            for remote_id in remote_ids:
                await self.ensure_owned()
                if not await self.remote.destroy(remote_id):
                    return
            # Query once more to catch resources discovered while terminating.
            if await self.remote.find(request_id):
                return
        except BaseException:
            await self._update(request_id, status="uncertain")
            raise
        await self._update(request_id, remote_absent_confirmed=True, status="closed", next_retry_at=None)
        session = self._sessions.pop(request_id, None)
        if session is not None:
            await session._sandbox.close()

    async def reconcile(self, *, startup: bool = False) -> None:
        await self.ensure_owned()
        rows = []
        after_id = 0
        while True:
            page = await asyncio.to_thread(store.list_open_sessions, after_id=after_id)
            if not page:
                break
            rows.extend(page)
            after_id = page[-1]["id"]
        now = datetime.now(timezone.utc)
        for row in rows:
            request_id = row["request_id"]
            retry_at = row.get("next_retry_at")
            if not startup and retry_at is not None:
                retry_at = retry_at.replace(tzinfo=timezone.utc) if retry_at.tzinfo is None else retry_at
                if retry_at > now:
                    continue
            identity = {key: row[key] for key in ("job_id", "worker_id", "attempt_count", "lease_epoch")}
            stale = startup or row["status"] in {"deleting", "uncertain"}
            for key, seconds in (("heartbeat_at", self.settings.heartbeat_timeout_seconds), ("expires_at", 0)):
                observed = row.get(key)
                if observed is not None:
                    observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed
                    stale = stale or (now - observed).total_seconds() > seconds
            try:
                await asyncio.to_thread(store.require_owner, request_id, identity)
            except store.SandboxFencedError:
                stale = True
            if not stale:
                continue
            session = self._sessions.get(request_id)
            if session is not None:
                await session.revoke()
            async with self.lock(request_id):
                try:
                    await self._cleanup(await asyncio.to_thread(store.get_session, request_id))
                except Exception as exc:
                    # One unreachable resource must not starve later records.
                    logger.error("sandbox cleanup requires retry: %s", type(exc).__name__)
                    if startup:
                        raise
