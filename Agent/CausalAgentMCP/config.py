"""Configuration for the private causal-mcp service."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class McpServerConfig:
    """Validated service settings; secrets are never rendered in responses."""

    service_token: str
    signing_key_current: str
    signing_key_previous: str | None
    signing_key_id: str
    signing_key_previous_id: str
    host: str = "0.0.0.0"
    port: int = 8080
    process_workers: int = 2
    queue_capacity: int = 4
    enqueue_timeout_seconds: float = 5.0
    algorithm_deadline_seconds: int = 600
    slow_log_seconds: float = 60.0
    input_max_bytes: int = 20 * 1024 * 1024
    input_max_rows: int = 100_000
    input_max_columns: int = 1_000
    result_max_bytes: int = 2 * 1024 * 1024
    timeout_recycle_threshold: int = 3
    clock_skew_seconds: float = 30.0

    @classmethod
    def from_env(cls, *, strict: bool = True) -> "McpServerConfig":
        """Load deployment configuration without importing the application settings."""

        service_token = os.getenv("CAUSAL_MCP_SERVICE_TOKEN", "")
        signing_key_current = os.getenv("CAUSAL_MCP_SIGNING_KEY_CURRENT", "")
        if strict and (not service_token or not signing_key_current):
            raise ValueError(
                "CAUSAL_MCP_SERVICE_TOKEN and CAUSAL_MCP_SIGNING_KEY_CURRENT are required"
            )
        config = cls(
            service_token=service_token,
            signing_key_current=signing_key_current,
            signing_key_previous=os.getenv("CAUSAL_MCP_SIGNING_KEY_PREVIOUS") or None,
            signing_key_id=os.getenv("CAUSAL_MCP_SIGNING_KEY_ID", "current"),
            signing_key_previous_id=os.getenv(
                "CAUSAL_MCP_SIGNING_KEY_PREVIOUS_ID", "previous"
            ),
            host=os.getenv("CAUSAL_MCP_HOST", "0.0.0.0"),
            port=_int_env("CAUSAL_MCP_PORT", 8080),
            process_workers=_int_env("CAUSAL_MCP_PROCESS_WORKERS", 2),
            queue_capacity=_int_env("CAUSAL_MCP_QUEUE_CAPACITY", 4),
            enqueue_timeout_seconds=_float_env(
                "CAUSAL_MCP_ENQUEUE_TIMEOUT_SECONDS", 5.0
            ),
            algorithm_deadline_seconds=_int_env(
                "CAUSAL_MCP_ALGORITHM_DEADLINE_SECONDS", 600
            ),
            slow_log_seconds=_float_env("CAUSAL_MCP_SLOW_LOG_SECONDS", 60.0),
            input_max_bytes=_int_env("CAUSAL_MCP_INPUT_MAX_BYTES", 20 * 1024 * 1024),
            input_max_rows=_int_env("CAUSAL_MCP_INPUT_MAX_ROWS", 100_000),
            input_max_columns=_int_env("CAUSAL_MCP_INPUT_MAX_COLUMNS", 1_000),
            result_max_bytes=_int_env(
                "CAUSAL_MCP_RESULT_MAX_BYTES", 2 * 1024 * 1024
            ),
            timeout_recycle_threshold=_int_env(
                "CAUSAL_MCP_TIMEOUT_RECYCLE_THRESHOLD", 3
            ),
            clock_skew_seconds=_float_env("CAUSAL_MCP_CLOCK_SKEW_SECONDS", 30.0),
        )
        config.validate(strict=strict)
        return config

    def validate(self, *, strict: bool = True) -> None:
        if strict and (not self.service_token or not self.signing_key_current):
            raise ValueError("MCP service authentication is not configured")
        positive = {
            "port": self.port,
            "process_workers": self.process_workers,
            "queue_capacity": self.queue_capacity,
            "enqueue_timeout_seconds": self.enqueue_timeout_seconds,
            "algorithm_deadline_seconds": self.algorithm_deadline_seconds,
            "slow_log_seconds": self.slow_log_seconds,
            "input_max_bytes": self.input_max_bytes,
            "input_max_rows": self.input_max_rows,
            "input_max_columns": self.input_max_columns,
            "result_max_bytes": self.result_max_bytes,
            "timeout_recycle_threshold": self.timeout_recycle_threshold,
            "clock_skew_seconds": self.clock_skew_seconds,
        }
        if any(value <= 0 for value in positive.values()):
            raise ValueError("MCP numeric settings must be positive")
        if self.algorithm_deadline_seconds > 600:
            raise ValueError("CAUSAL_MCP_ALGORITHM_DEADLINE_SECONDS cannot exceed 600")
        if not self.signing_key_id or not self.signing_key_previous_id:
            raise ValueError("MCP signing key ids must be non-blank")
