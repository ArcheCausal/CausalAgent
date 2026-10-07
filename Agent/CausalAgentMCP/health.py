"""Minimal health/readiness payloads for causal-mcp."""

from __future__ import annotations

from typing import Any


def health_payload() -> dict[str, str]:
    return {"status": "ok"}


def readiness_payload(
    *,
    config_ready: bool,
    mysql_strong_read: bool,
    executor_ready: bool,
) -> tuple[int, dict[str, Any]]:
    ready = config_ready and mysql_strong_read and executor_ready
    return (
        200 if ready else 503,
        {
            "status": "ready" if ready else "not_ready",
            "checks": {
                "config": config_ready,
                "mysql_strong_read": mysql_strong_read,
                "executor": executor_ready,
            },
        },
    )
