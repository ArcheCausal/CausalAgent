"""OpenSandbox 资源适配；只有控制服务可以调用上游管理 API。"""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
from typing import Any

from opensandbox import Sandbox
from opensandbox.exceptions import SandboxApiException
from opensandbox.manager import SandboxManager
from opensandbox.models.sandboxes import NetworkPolicy, SandboxFilter, SandboxImageSpec

from Agent.data_processing.opensandbox_runtime import OpenSandboxSession, OpenSandboxSettings


def _request_metadata(request_id: str) -> dict[str, str]:
    # OpenSandbox metadata labels allow at most 63 characters. Preserve all
    # 256 request-id bits in a 52-character, label-safe representation.
    value = base64.b32encode(bytes.fromhex(request_id)).decode("ascii").rstrip("=").lower()
    return {"application": "causalagent", "request": value}


class OpenSandboxRemote:
    def __init__(self, settings: OpenSandboxSettings) -> None:
        self.settings = settings

    async def create(self, request_id: str, job_id: str) -> Sandbox:
        return await Sandbox.create(
            SandboxImageSpec(self.settings.image),
            timeout=timedelta(seconds=self.settings.sandbox_ttl_seconds),
            ready_timeout=timedelta(seconds=self.settings.api_timeout_seconds),
            env={"PYTHON_VERSION": "3.11"},
            resource={"cpu": self.settings.cpu_limit, "memory": self.settings.memory_limit},
            network_policy=NetworkPolicy(default_action="deny", egress=[]),
            entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
            metadata={**_request_metadata(request_id), "purpose": "data-preparation", "job": job_id},
            connection_config=self.settings.connection_config(),
        )

    async def connect(self, remote_id: str, *, guard: Any = None) -> OpenSandboxSession:
        sandbox = await Sandbox.connect(remote_id, connection_config=self.settings.connection_config())
        return self.session(sandbox, guard=guard)

    def session(self, sandbox: Sandbox, *, guard: Any = None) -> OpenSandboxSession:
        return OpenSandboxSession(sandbox, self.settings, lease_lost=asyncio.Event(), execution_guard=guard)

    async def find(self, request_id: str) -> list[str]:
        manager = await SandboxManager.create(self.settings.connection_config())
        try:
            result = []
            page = 1
            while True:
                response = await manager.list_sandbox_infos(SandboxFilter(metadata=_request_metadata(request_id), page=page, page_size=100))
                result.extend(item.id for item in response.sandbox_infos if item.status.state.upper() != "TERMINATED")
                if not response.pagination.has_next_page:
                    return result
                page += 1
        finally:
            await manager.close()

    async def exists(self, remote_id: str) -> bool:
        manager = await SandboxManager.create(self.settings.connection_config())
        try:
            info = await manager.get_sandbox_info(remote_id)
            return info.status.state.upper() != "TERMINATED"
        except SandboxApiException as exc:
            if exc.status_code == 404:
                return False
            raise
        finally:
            await manager.close()

    async def destroy(self, remote_id: str) -> bool:
        manager = await SandboxManager.create(self.settings.connection_config())
        try:
            try:
                await manager.kill_sandbox(remote_id)
            except SandboxApiException as exc:
                if exc.status_code != 404:
                    raise
        finally:
            await manager.close()
        return not await self.exists(remote_id)
