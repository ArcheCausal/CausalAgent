"""Private MCP request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    McpInvocationContext,
)


class ExecuteAlgorithmRequest(BaseModel):
    """Internal service envelope; it is never registered as a model tool."""

    model_config = ConfigDict(extra="forbid")

    command: AlgorithmExecutionCommand
    trusted_context: McpInvocationContext
    signature: str = Field(min_length=1)
    retry_ordinal: int = Field(default=0, ge=0)


class McpError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    retry_after_seconds: int | None = Field(default=None, ge=0)


class McpExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    result: AlgorithmResult | None = None
    error: McpError | None = None
    trace: dict[str, str | int | None] = Field(default_factory=dict)


def error_payload(
    code: str,
    *,
    retry_after_seconds: int | None = None,
    trace: dict[str, str | int | None] | None = None,
) -> dict[str, Any]:
    """Return a stable, deliberately detail-free error envelope."""

    return McpExecutionResponse(
        ok=False,
        error=McpError(code=code, retry_after_seconds=retry_after_seconds),
        trace=trace or {},
    ).model_dump(mode="json")
