"""控制服务启动配置；策略不写入容量配置表。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping

from Agent.data_processing.opensandbox_runtime import OpenSandboxSettings, _positive_int
from sandbox_controller.protocol import read_token


@dataclass(frozen=True)
class ControllerSettings:
    remote: OpenSandboxSettings
    token: str = field(repr=False)
    capacity: int = 2
    heartbeat_timeout_seconds: int = 90
    reconcile_interval_seconds: int = 15
    port: int = 8081

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ControllerSettings":
        values = os.environ if environ is None else environ
        return cls(
            remote=OpenSandboxSettings.from_environment(api_key=read_token(values["CAUSALAGENT_SANDBOX_API_KEY_FILE"]), environ=values),
            token=read_token(values["CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN_FILE"]),
            capacity=_positive_int(values, "CAUSALAGENT_SANDBOX_GLOBAL_CAPACITY", 2),
            heartbeat_timeout_seconds=_positive_int(values, "CAUSALAGENT_SANDBOX_HEARTBEAT_TIMEOUT_SECONDS", 90),
            reconcile_interval_seconds=_positive_int(values, "CAUSALAGENT_SANDBOX_RECONCILE_INTERVAL_SECONDS", 15),
            port=_positive_int(values, "CAUSALAGENT_SANDBOX_CONTROLLER_PORT", 8081),
        )
