"""绑定请求正文与可信 Job 执行身份的独立 Bearer/HMAC 协议。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import time
from typing import Mapping, Any
from uuid import UUID


class SandboxProtocolError(ValueError):
    """内部请求格式或认证不合法。"""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_token(path: str) -> str:
    token = Path(path).read_text(encoding="utf-8").strip()
    if len(token) < 32 or len(token) > 4096 or any(char.isspace() for char in token):
        raise SandboxProtocolError("sandbox token file must contain one token of at least 32 characters")
    return token


def validate_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    if set(identity) != {"job_id", "worker_id", "attempt_count", "lease_epoch"}:
        raise SandboxProtocolError("invalid execution identity fields")
    result = dict(identity)
    try:
        if str(UUID(result["job_id"])) != result["job_id"]:
            raise ValueError("noncanonical UUID")
    except (ValueError, TypeError, AttributeError) as exc:
        raise SandboxProtocolError("invalid Job identity") from exc
    if not isinstance(result["worker_id"], str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", result["worker_id"]) is None:
        raise SandboxProtocolError("invalid worker identity")
    for key in ("attempt_count", "lease_epoch"):
        if isinstance(result[key], bool) or not isinstance(result[key], int) or result[key] < 0:
            raise SandboxProtocolError("invalid execution generation")
    return result


def _signature(token: str, method: str, path: str, issued_at: str, encoded: str, body: bytes) -> str:
    payload = "\n".join((method.upper(), path, issued_at, encoded, hashlib.sha256(body).hexdigest())).encode("utf-8")
    return hmac.new(token.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def request_headers(token: str, method: str, path: str, identity: Mapping[str, Any], body: bytes = b"") -> dict[str, str]:
    encoded = base64.urlsafe_b64encode(canonical_bytes(validate_identity(identity))).decode("ascii")
    issued_at = str(int(time.time()))
    return {"Authorization": f"Bearer {token}", "X-Sandbox-Identity": encoded, "X-Sandbox-Issued-At": issued_at, "X-Sandbox-Signature": _signature(token, method, path, issued_at, encoded, body)}


def verify_request(token: str, method: str, path: str, headers: Mapping[str, str], body: bytes, *, now: float | None = None) -> dict[str, Any]:
    authorization = headers.get("authorization", "")
    encoded = headers.get("x-sandbox-identity", "")
    issued_at = headers.get("x-sandbox-issued-at", "")
    signature = headers.get("x-sandbox-signature", "")
    if len(encoded) > 2048 or not hmac.compare_digest(authorization.encode("utf-8"), f"Bearer {token}".encode("utf-8")):
        raise SandboxProtocolError("sandbox authentication failed")
    try:
        if abs((time.time() if now is None else now) - int(issued_at)) > 30:
            raise ValueError("expired signature")
        identity = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
    except (ValueError, TypeError) as exc:
        raise SandboxProtocolError("sandbox authentication failed") from exc
    expected = _signature(token, method, path, issued_at, encoded, body)
    if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("utf-8")) or not isinstance(identity, dict):
        raise SandboxProtocolError("sandbox authentication failed")
    return validate_identity(identity)
