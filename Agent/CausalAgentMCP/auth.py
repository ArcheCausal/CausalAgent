"""Bearer/HMAC verification for internal causal-mcp calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from typing import Mapping

from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    McpInvocationContext,
    canonical_json_bytes,
)


class McpAuthError(ValueError):
    """Authentication failures expose only a stable safe error code."""

    def __init__(self, code: SafeErrorCode = SafeErrorCode.MCP_AUTH_FAILED) -> None:
        super().__init__(code.value)
        self.safe_error_code = code


def canonical_signature_payload(
    context: McpInvocationContext,
    command: AlgorithmExecutionCommand,
) -> bytes:
    """Bind both trusted identity and the algorithm command to the signature."""

    return canonical_json_bytes(
        {
            "command": command.model_dump(mode="json"),
            "trusted_context": context.model_dump(mode="json"),
        }
    )


def sign_invocation(
    context: McpInvocationContext,
    command: AlgorithmExecutionCommand,
    secret: str,
) -> str:
    if not secret:
        raise ValueError("signing secret is required")
    return hmac.new(
        secret.encode("utf-8"),
        canonical_signature_payload(context, command),
        hashlib.sha256,
    ).hexdigest()


def verify_invocation(
    context: McpInvocationContext,
    command: AlgorithmExecutionCommand,
    signature: str,
    *,
    keys: Mapping[str, str],
    now: datetime | None = None,
    clock_skew_seconds: float = 30.0,
) -> None:
    """Verify key id, time window and signature using constant-time comparison."""

    secret = keys.get(context.key_id)
    if not secret or not signature:
        raise McpAuthError()
    current = now or datetime.now(timezone.utc)
    skew = timedelta(seconds=clock_skew_seconds)
    if current < context.issued_at - skew or current > context.expires_at + skew:
        raise McpAuthError()
    expected = sign_invocation(context, command, secret)
    if not hmac.compare_digest(expected, signature):
        raise McpAuthError()
