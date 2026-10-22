"""可取消的真实 HTTP SSE 传输；截止检查独立于心跳和事件读取。"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from runner import config
from runner.metrics import HttpSample, SseSample

TERMINAL_EVENTS = {"final_result", "error", "canceled"}
PROGRESS_EVENTS = {"node_start", "progress", "decision", "tool_call_start",
                   "tool_call_result", "node_retry", "node_end", "text_delta",
                   "decision_delta", "interrupt"}


async def iter_events(response):
    """解析完整 SSE 帧；heartbeat 无 ID，不修改持久化游标。"""
    from runner.api import _decode_payload

    event, event_id, data = "", None, []
    async for line in response.aiter_lines():
        if not line:
            if event or data:
                yield event or "message", event_id, _decode_payload(data)
            event, event_id, data = "", None, []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
            elif field == "id":
                try:
                    event_id = int(value)
                except ValueError:
                    event_id = None
    # 缺少帧结束空行的 EOF 不确认该事件，重连时让服务端重新发送。


async def subscribe(owner, job_id, after, reconnect, deadline, stop_event, audit=False):
    recorder = owner.recorder
    started = time.monotonic()
    started_at = time.time()
    cursor = after
    ids, errors = [], []
    first_ms = terminal_ms = terminal_at = None
    terminal = interrupt = None
    disconnects = reconnects = 0
    reconnect_attempts = reconnect_event_count = 0
    connection_attempts = intentional_disconnects = 0
    end_reason = None
    state = None
    acquired = False

    def transition(next_state):
        nonlocal state
        if state is not None:
            recorder.sse_transition(state, -1)
        state = next_state
        if state is not None:
            recorder.sse_transition(state)

    def stopped():
        return (owner._closed.is_set() or recorder.observation_stop.is_set()
                or (stop_event is not None and stop_event.is_set()))

    def expired():
        return time.monotonic() >= min(deadline, recorder.observation_deadline)

    def failure(error):
        errors.append(error)
        if not audit:
            with owner._request_lock:
                accumulated = owner._job_sse_errors.setdefault(job_id, [])
                accumulated.append(error)
                snapshot = list(accumulated)
            recorder.lifecycle(job_id=job_id, event="sse_error", sse_errors=snapshot)

    async def consume():
        nonlocal acquired, cursor, first_ms, terminal_ms, terminal_at
        nonlocal terminal, interrupt, disconnects, reconnects
        nonlocal reconnect_attempts, reconnect_event_count, end_reason
        nonlocal connection_attempts, intentional_disconnects
        transition("waiting_quota")
        if not audit:
            recorder.lifecycle(job_id=job_id, event="subscription_started",
                               subscription_started_at=started_at)
        if owner.sse_gate is not None:
            while not owner.sse_gate.acquire(blocking=False):
                await asyncio.sleep(config.OBSERVATION_CHECK_INTERVAL_SECONDS)
            acquired = True
        if not audit:
            recorder.lifecycle(job_id=job_id, event="sse_quota_acquired",
                               quota_acquired_at=time.time(),
                               quota_wait_ms=(time.monotonic() - started) * 1000)
        async with httpx.AsyncClient(
            base_url=owner.base_url, cookies=owner.client.cookies,
            verify=owner.ssl_context,
            headers=dict(owner.client.headers), follow_redirects=False,
            timeout=httpx.Timeout(config.HTTP_TIMEOUT_SECONDS,
                                  read=config.SSE_IDLE_READ_TIMEOUT_SECONDS),
        ) as client:
            while True:
                transition("waiting_headers")
                request_id = str(uuid.uuid4())
                headers = {"Accept": "text/event-stream", "X-Request-ID": request_id}
                is_reconnect = connection_attempts > 0
                connection_attempts += 1
                if is_reconnect:
                    reconnect_attempts += 1
                if cursor:
                    headers["Last-Event-ID"] = str(cursor)
                at, mono = time.time(), time.monotonic()
                headers_recorded = False
                established = False
                retryable = True
                try:
                    async with client.stream("GET", f"/api/agent/jobs/{job_id}/events",
                                             headers=headers) as response:
                        recorder.record_http(HttpSample(
                            "GET", "/api/agent/jobs/<id>/events", response.status_code,
                            (time.monotonic() - mono) * 1000,
                            started_at=at, finished_at=time.time(), request_id=request_id,
                            client_request_id=request_id, response_request_id=response.headers.get("X-Request-ID"),
                            run_id=recorder.run_id))
                        headers_recorded = True
                        if not audit:
                            recorder.lifecycle(job_id=job_id, event="sse_headers",
                                               sse_headers_at=time.time(),
                                               sse_headers_status=response.status_code)
                        if response.status_code != 200:
                            end_reason = "transport_error"
                            failure(f"http_{response.status_code}")
                            retryable = response.status_code in (502, 503, 504)
                        elif not response.headers.get("content-type", "").startswith("text/event-stream"):
                            end_reason = "transport_error"
                            failure("invalid_event_stream_content_type")
                            retryable = False
                        else:
                            established = True
                            transition("established")
                            if is_reconnect:
                                reconnects += 1
                            if not audit:
                                recorder.lifecycle(job_id=job_id, event="sse_established",
                                                   sse_established_at=time.time())
                            async for event, event_id, payload in iter_events(response):
                                if event_id is not None:
                                    ids.append(event_id)
                                    cursor = max(cursor, event_id)
                                    if is_reconnect:
                                        reconnect_event_count += 1
                                if first_ms is None and event in PROGRESS_EVENTS | TERMINAL_EVENTS:
                                    first_ms = (time.monotonic() - started) * 1000
                                    if not audit:
                                        with owner._request_lock:
                                            first = job_id not in owner._first_event_seen
                                            owner._first_event_seen.add(job_id)
                                        if first:
                                            recorder.lifecycle(job_id=job_id, event="first_event",
                                                               first_event_at=time.time())
                                if not audit and event == "tool_call_result":
                                    name = str(payload.get("tool_name", ""))
                                    if "cdfm" in name.lower():
                                        recorder.lifecycle(job_id=job_id, event="cdfm_result",
                                                           cdfm_observed=True,
                                                           cdfm_status=payload.get("status"))
                                if event in TERMINAL_EVENTS:
                                    end_reason = "terminal"
                                    terminal, terminal_at = event, time.time()
                                    terminal_ms = (time.monotonic() - started) * 1000
                                    if not audit:
                                        with owner._request_lock:
                                            identity = owner._job_requests.get(job_id)
                                        fields = {"terminal_event": event,
                                                  "terminal_received_at": terminal_at,
                                                  "final_status": {"final_result": "succeeded",
                                                                   "error": "failed",
                                                                   "canceled": "canceled"}[event]}
                                        if identity is not None:
                                            fields["user_wait_ms"] = (time.monotonic() - identity[1]) * 1000
                                        recorder.lifecycle(job_id=job_id, event="terminal_received", **fields)
                                    return
                                if event == "interrupt":
                                    interrupt = payload
                                    end_reason = "interrupt"
                                    return
                            failure("stream_closed_without_terminal")
                            end_reason = "transport_error"
                except httpx.HTTPError as exc:
                    end_reason = "idle_timeout" if established and isinstance(exc, httpx.ReadTimeout) else "transport_error"
                    failure(type(exc).__name__)
                    if not headers_recorded:
                        recorder.record_http(HttpSample(
                            "GET", "/api/agent/jobs/<id>/events", 0,
                            (time.monotonic() - mono) * 1000, type(exc).__name__, at, time.time(),
                            request_id=request_id, client_request_id=request_id, run_id=recorder.run_id))
                except asyncio.CancelledError:
                    if established:
                        disconnects += 1
                        intentional_disconnects += int(end_reason == "intentional_stop")
                    if not headers_recorded:
                        recorder.record_http(HttpSample(
                            "GET", "/api/agent/jobs/<id>/events", 0,
                            (time.monotonic() - mono) * 1000, "observation_canceled", at, time.time(),
                            request_id=request_id, client_request_id=request_id, run_id=recorder.run_id))
                    raise
                disconnects += int(established)
                end_reason = end_reason or "transport_error"
                if not retryable or not reconnect or reconnect_attempts >= config.SSE_MAX_RECONNECTS:
                    return
                transition("reconnect")
                await asyncio.sleep(config.SSE_RECONNECT_DELAY_SECONDS)

    task = asyncio.create_task(consume())
    try:
        while not task.done():
            if stopped() or expired():
                end_reason = "total_deadline" if expired() else "intentional_stop"
                if end_reason == "total_deadline":
                    failure("observation_deadline")
                task.cancel()
                break
            await asyncio.wait({task}, timeout=config.OBSERVATION_CHECK_INTERVAL_SECONDS)
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            end_reason = "internal_error"
            failure(type(exc).__name__)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if acquired:
            owner.sse_gate.release()
        transition("closed")
        transition(None)
    sample = SseSample(job_id, first_ms, terminal_ms, terminal, cursor, disconnects,
                       reconnects, ids, ";".join(errors),
                       observation_end_reason=end_reason, started_at=started_at, finished_at=time.time(),
                       intentional_disconnects=intentional_disconnects, connection_attempts=connection_attempts,
                       reconnect_attempts=reconnect_attempts, reconnect_event_count=reconnect_event_count)
    if not audit:
        recorder.record_sse(sample)
    return {"sample": sample, "terminal_event": terminal, "interrupt_payload": interrupt,
            "terminal_received_at": terminal_at, "errors": errors, "event_ids": ids}
