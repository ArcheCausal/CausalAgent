"""AlgorithmExecutor implementation backed by the private causal-mcp client pool."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from Agent.deep_agent_tools.algorithm_executor import (
    AlgorithmExecutionResponse,
    AlgorithmExecutorError,
    validate_executor_command,
    validate_executor_result,
)
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand,
    AlgorithmResult,
    McpInvocationContext,
)
from Agent.execution_control import JobExecutionRevoked

from app.agent.worker.mcp_client_pool import (
    McpClientPool,
    McpPoolError,
    McpTransportError,
)
from app.agent.worker.execution_guard import current_execution_guard

from Agent.CausalAgentMCP.auth import sign_invocation
from observability.logging_runtime import log_context, log_event


LOGGER = logging.getLogger(__name__)
_CANCELLATION_STATUSES = frozenset(
    {"canceled", "cancel_pending", "already_canceled", "already_finished", "not_found"}
)


class McpAlgorithmExecutor:
    """Keep transport concerns out of the model-visible AlgorithmSpec tools."""

    def __init__(
        self,
        pool: McpClientPool,
        *,
        signing_key: str,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.05,
        cancel_timeout_seconds: float | None = None,
    ) -> None:
        self.pool = pool
        self.signing_key = signing_key
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.cancel_timeout_seconds = (
            float(os.getenv("CAUSAL_MCP_CANCEL_TIMEOUT_SECONDS", "6"))
            if cancel_timeout_seconds is None
            else cancel_timeout_seconds
        )
        if self.cancel_timeout_seconds <= 0:
            raise ValueError("MCP cancel timeout must be positive")

    @staticmethod
    def _remote_lease_revoked(invocation_id: str) -> JobExecutionRevoked:
        """把 MCP 已确认的 stale lease 转回 worker 控制流。"""

        error = JobExecutionRevoked("MCP reported a stale execution lease")
        error.invocation_id = invocation_id
        error.remote_execution_status = "revoked"
        return error

    async def execute(
        self,
        command: AlgorithmExecutionCommand,
        trusted_context: McpInvocationContext,
    ) -> AlgorithmResult | AlgorithmExecutionResponse:
        validate_executor_command(command, trusted_context=trusted_context)
        with log_context(
            invocation_id=command.invocation_id,
            tool=command.capability_id,
        ):
            return await self._execute_bound(command, trusted_context)

    async def _cancel_remote(
        self,
        *,
        arguments: dict[str, Any],
        command: AlgorithmExecutionCommand,
        retry_ordinal: int,
    ) -> str:
        log_event(
            LOGGER,
            "mcp.client.cancel.requested",
            details={
                "capability": command.capability_id,
                "retry_ordinal": retry_ordinal,
            },
        )
        cancel_task = asyncio.create_task(
            self.pool.cancel_tool(
                {**arguments, "retry_ordinal": retry_ordinal},
                invocation_id=command.invocation_id,
                retry_ordinal=retry_ordinal,
            )
        )
        try:
            response = await asyncio.wait_for(
                asyncio.shield(cancel_task), timeout=self.cancel_timeout_seconds
            )
            payload = getattr(response, "structured_content", None)
            if payload is None:
                payload = getattr(response, "structuredContent", None)
            cancellation = payload.get("cancellation") if isinstance(payload, dict) else None
            status = (
                cancellation.get("status")
                if isinstance(cancellation, dict)
                else None
            )
            if status not in _CANCELLATION_STATUSES:
                raise ValueError("MCP cancellation response is invalid")
            log_event(
                LOGGER,
                "mcp.client.cancel.finished",
                details={
                    "capability": command.capability_id,
                    "cancellation_status": status,
                },
            )
            return status
        except asyncio.CancelledError:
            if not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            raise
        except asyncio.TimeoutError:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            log_event(
                LOGGER,
                "mcp.client.cancel.failed",
                details={
                    "capability": command.capability_id,
                    "reason_code": "response_timeout",
                },
            )
            return "unknown"
        except McpPoolError as exc:
            if not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            reason_code = (
                "control_capacity_timeout"
                if exc.safe_error_code == SafeErrorCode.MCP_CAPACITY_EXHAUSTED
                else "transport_error"
            )
            log_event(
                LOGGER,
                "mcp.client.cancel.failed",
                details={
                    "capability": command.capability_id,
                    "reason_code": reason_code,
                },
            )
            return "unknown"
        except (McpTransportError, OSError, ConnectionError) as exc:
            if not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            log_event(
                LOGGER,
                "mcp.client.cancel.failed",
                details={
                    "capability": command.capability_id,
                    "reason_code": "transport_error",
                },
            )
            return "unknown"
        except (ValueError, TypeError, KeyError, AttributeError):
            if not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            log_event(
                LOGGER,
                "mcp.client.cancel.failed",
                details={
                    "capability": command.capability_id,
                    "reason_code": "invalid_response",
                },
            )
            return "unknown"
        except Exception:
            if not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            log_event(
                LOGGER,
                "mcp.client.cancel.failed",
                details={
                    "capability": command.capability_id,
                    "reason_code": "transport_error",
                },
            )
            return "unknown"

    async def _execute_bound(
        self,
        command: AlgorithmExecutionCommand,
        trusted_context: McpInvocationContext,
    ) -> AlgorithmResult | AlgorithmExecutionResponse:
        signature = sign_invocation(trusted_context, command, self.signing_key)
        arguments = {
            "command": command.model_dump(mode="json"),
            "trusted_context": trusted_context.model_dump(mode="json"),
            "signature": signature,
        }
        last_error: BaseException | None = None
        for retry_ordinal in range(self.max_retries + 1):
            guard = current_execution_guard()
            if guard is not None:
                await guard.ensure_active()
            call_task: asyncio.Task[Any] | None = None
            revoked_task: asyncio.Task[Any] | None = None
            try:
                log_event(
                    LOGGER,
                    "mcp.client.call.started",
                    details={
                        "capability": command.capability_id,
                        "retry_ordinal": retry_ordinal,
                    },
                )
                call_task = asyncio.create_task(
                    self.pool.call_tool(
                        {**arguments, "retry_ordinal": retry_ordinal},
                        invocation_id=command.invocation_id,
                        retry_ordinal=retry_ordinal,
                    )
                )
                if guard is not None:
                    revoked_task = asyncio.create_task(guard.wait_revoked())
                    done, _pending = await asyncio.wait(
                        {call_task, revoked_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if revoked_task in done and guard.revoked:
                        call_task.cancel()
                        await asyncio.gather(call_task, return_exceptions=True)
                        remote_status = await self._cancel_remote(
                            arguments=arguments,
                            command=command,
                            retry_ordinal=retry_ordinal,
                        )
                        error = JobExecutionRevoked("Job execution revoked during MCP call")
                        error.invocation_id = command.invocation_id
                        error.remote_execution_status = remote_status
                        raise error
                response = await call_task
                # 远端响应可能在本地 Job 被取消后才抵达；此时只能丢弃响应，
                # 不能把它标准化为 AlgorithmResult 或进入后续 Gate。
                guard = current_execution_guard()
                if guard is not None:
                    await guard.check_after_call()
                payload = getattr(response, "structured_content", None)
                if payload is None:
                    payload = getattr(response, "structuredContent", None)
                if not isinstance(payload, dict):
                    raise AlgorithmExecutorError(
                        "MCP response is not structured",
                        safe_error_code=SafeErrorCode.MCP_TRANSPORT_FAILED,
                    )
                if payload.get("ok") is not True:
                    error = payload.get("error") or {}
                    code = error.get("code", SafeErrorCode.MCP_TRANSPORT_FAILED.value)
                    try:
                        safe_code = SafeErrorCode(code)
                    except ValueError:
                        safe_code = SafeErrorCode.MCP_TRANSPORT_FAILED
                    if safe_code == SafeErrorCode.MCP_LEASE_STALE:
                        raise self._remote_lease_revoked(command.invocation_id)
                    if safe_code == SafeErrorCode.ALGORITHM_CANCELED:
                        error = JobExecutionRevoked("MCP invocation was canceled")
                        error.invocation_id = command.invocation_id
                        error.remote_execution_status = "canceled"
                        raise error
                    raise AlgorithmExecutorError(
                        "MCP call returned a safe error",
                        safe_error_code=safe_code,
                    )
                result = AlgorithmResult.model_validate(payload.get("result"))
                result_safe_code = result.diagnostics.safe_error_code
                if (
                    str(getattr(result_safe_code, "value", result_safe_code) or "")
                    == SafeErrorCode.MCP_LEASE_STALE.value
                ):
                    raise self._remote_lease_revoked(command.invocation_id)
                validated_result = validate_executor_result(
                    result,
                    command=command,
                    trusted_context=trusted_context,
                )
                raw_payload = payload.get("raw_payload")
                if raw_payload is None:
                    return validated_result
                if not isinstance(raw_payload, dict):
                    raise AlgorithmExecutorError(
                        "MCP raw payload is not structured",
                        safe_error_code=SafeErrorCode.ALGORITHM_RESULT_CONTRACT_INVALID,
                    )
                return AlgorithmExecutionResponse(
                    result=validated_result,
                    raw_payload=raw_payload,
                )
            except asyncio.CancelledError as exc:
                if call_task is not None and not call_task.done():
                    call_task.cancel()
                    await asyncio.gather(call_task, return_exceptions=True)
                remote_status = await self._cancel_remote(
                    arguments=arguments,
                    command=command,
                    retry_ordinal=retry_ordinal,
                )
                exc.invocation_id = command.invocation_id
                exc.remote_execution_status = remote_status
                raise
            except McpTransportError as exc:
                last_error = exc
            except McpPoolError as exc:
                last_error = exc
                if exc.safe_error_code != SafeErrorCode.MCP_CAPACITY_EXHAUSTED:
                    break
            except AlgorithmExecutorError as exc:
                last_error = exc
                if exc.safe_error_code not in {
                    SafeErrorCode.MCP_CAPACITY_EXHAUSTED,
                    SafeErrorCode.MCP_TRANSPORT_FAILED,
                }:
                    break
            finally:
                if revoked_task is not None:
                    revoked_task.cancel()
                    await asyncio.gather(revoked_task, return_exceptions=True)
            if retry_ordinal < self.max_retries:
                guard = current_execution_guard()
                if guard is not None:
                    await guard.check_after_call()
                await asyncio.sleep(self.retry_backoff_seconds * (2**retry_ordinal))
        raise AlgorithmExecutorError(
            "MCP execution failed after bounded retries",
            safe_error_code=(
                getattr(last_error, "safe_error_code", None)
                or SafeErrorCode.MCP_TRANSPORT_FAILED
            ),
        ) from last_error
