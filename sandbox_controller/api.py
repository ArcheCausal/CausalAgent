"""只开放固定工作区操作的内部 ASGI API。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import re
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from Agent.data_processing.opensandbox_runtime import SandboxLeaseLostError
from Database import sandbox_sessions as store
from sandbox_controller.config import ControllerSettings
from sandbox_controller.lifecycle import ControllerError, SandboxController
from sandbox_controller.protocol import SandboxProtocolError, verify_request


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
logger = logging.getLogger(__name__)


async def _body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdecimal() or int(declared) > limit):
        raise ControllerError("request_size_limit", 413)
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > limit:
            raise ControllerError("request_size_limit", 413)
        chunks.extend(chunk)
    return bytes(chunks)


def _json(body: bytes, fields: set[str]) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict) or set(value) != fields:
        raise ControllerError("invalid_request_fields", 422)
    for item in value.values():
        if not isinstance(item, str) or _DIGEST.fullmatch(item) is None:
            raise ControllerError("invalid_request_digest", 422)
    return value


def create_app(settings: ControllerSettings | None = None) -> Starlette:
    settings = settings or ControllerSettings.from_environment()
    controller = SandboxController(settings)

    async def reconcile_loop() -> None:
        while True:
            await asyncio.sleep(settings.reconcile_interval_seconds)
            try:
                await controller.reconcile()
            except Exception as exc:
                # Never log upstream exception text: it may contain credentials
                # or user-authored program output.
                logger.error("sandbox reconciliation failed: %s", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await controller.startup()
        task = asyncio.create_task(reconcile_loop())
        app.state.controller = controller
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await controller.shutdown()

    async def health(request: Request) -> Response:
        try:
            await controller.ensure_owned()
        except ControllerError:
            return JSONResponse({"ready": False}, status_code=503)
        return JSONResponse({"ready": controller.ready}, status_code=200 if controller.ready else 503)

    async def operation(request: Request) -> Response:
        try:
            if not hmac.compare_digest(request.headers.get("authorization", "").encode("utf-8"), ("Bearer " + settings.token).encode("utf-8")):
                raise SandboxProtocolError("sandbox authentication failed")
            if not controller.ready:
                raise ControllerError("controller_not_ready", 503)
            if request.headers.get("content-encoding", "identity").lower() != "identity":
                raise ControllerError("request_encoding_unsupported", 415)
            name = request.path_params.get("operation", "")
            session_id = request.path_params.get("session_id")
            if session_id is not None and _DIGEST.fullmatch(session_id) is None:
                raise ControllerError("invalid_session_id", 422)
            limit = settings.remote.max_input_bytes if name == "input" else settings.remote.max_command_bytes if name == "script" else 4096
            body = await _body(request, limit)
            identity = verify_request(settings.token, request.method, request.url.path, request.headers, body)
            if body and (request.method in {"GET", "DELETE"} or name == "heartbeat"):
                raise ControllerError("request_body_not_allowed", 422)
            if session_id is None and request.method == "POST":
                value = _json(body, {"request_id", "source_hash"})
                return JSONResponse(await controller.create(identity, value["request_id"], value["source_hash"]))
            if not name:
                if request.method == "GET":
                    row = await controller.owner(session_id, identity)
                    return JSONResponse({"session_id": session_id, "state": row["status"], "operation_status": row.get("operation_status"), "result": row.get("operation_result")})
                if request.method == "DELETE":
                    return JSONResponse(await controller.close(session_id, identity))
            if name == "input" and request.method == "PUT":
                await controller.initialize(session_id, identity, body)
                return JSONResponse({"state": "active"})
            if name == "script" and request.method == "PUT":
                return JSONResponse({"path": await controller.write_script(session_id, identity, body)})
            if name == "preview" and request.method == "GET":
                return JSONResponse({"preview": await controller.preview(session_id, identity)})
            if name == "execute" and request.method == "POST":
                value = _json(body, {"operation_id", "script_hash", "plan_version"})
                return JSONResponse(await controller.execute(session_id, identity, value["operation_id"], value["script_hash"], value["plan_version"]))
            if name == "output" and request.method == "GET":
                output = await controller.output(session_id, identity)
                return Response(output, media_type="application/octet-stream", headers={"X-Content-SHA256": hashlib.sha256(output).hexdigest()})
            if name == "heartbeat" and request.method == "POST":
                await controller.heartbeat(session_id, identity)
                return JSONResponse({"state": "active"})
            raise ControllerError("unsupported_operation", 405)
        except SandboxProtocolError:
            return JSONResponse({"code": "sandbox_auth_failed"}, status_code=401)
        except ControllerError as exc:
            return JSONResponse({"code": exc.code}, status_code=exc.status)
        except store.SandboxCapacityError:
            return JSONResponse({"code": "sandbox_capacity_unavailable"}, status_code=409, headers={"Retry-After": str(settings.reconcile_interval_seconds)})
        except (store.SandboxFencedError, SandboxLeaseLostError):
            return JSONResponse({"code": "sandbox_execution_revoked"}, status_code=403)
        except store.SandboxNotFoundError:
            return JSONResponse({"code": "sandbox_session_missing"}, status_code=404)
        except store.SandboxConflictError:
            return JSONResponse({"code": "sandbox_request_conflict"}, status_code=409)
        except (ValueError, UnicodeError):
            return JSONResponse({"code": "invalid_request"}, status_code=422)
        except Exception as exc:
            logger.error("sandbox operation failed: %s", type(exc).__name__)
            return JSONResponse({"code": "sandbox_operation_unavailable"}, status_code=503)

    return Starlette(routes=[
        Route("/healthz", health, methods=["GET"]),
        Route("/v1/sessions", operation, methods=["POST"]),
        Route("/v1/sessions/{session_id}", operation, methods=["GET", "DELETE"]),
        Route("/v1/sessions/{session_id}/{operation}", operation, methods=["GET", "POST", "PUT"]),
    ], lifespan=lifespan)
