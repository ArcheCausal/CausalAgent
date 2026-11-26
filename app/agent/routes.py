"""
agent.routes - analysis job API 和 SSE 事件流。
"""

from __future__ import annotations

import json
import logging
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.agent import job_service
from app.agent.public_events import public_event_payload
from app.auth.session_guard import get_current_session_user
from app.request_context import (
    bind_request_log_context,
    get_request_id,
    log_authorization_denied,
    log_request_failure,
)
from config.settings import settings
from observability.logging_runtime import log_event


agent_bp = Blueprint("agent", __name__, url_prefix="/api")
LOGGER = logging.getLogger(__name__)


def _sse(event_type: str, payload: dict, event_id: int | None = None) -> str:
    """把数据库事件转换成浏览器 EventSource 可识别的标准 SSE 文本。"""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def _public_event_payload(payload: dict) -> dict:
    """兼容既有测试与调用，实际清洗规则集中在公共事件模块。"""
    return public_event_payload(str(payload.get("type") or ""), payload)


@agent_bp.route("/send_stream", methods=["POST"])
def handle_message_stream():
    """旧流式接口已下线，前端应使用 analysis job API。"""
    return jsonify({
        "success": False,
        "error": "旧 /api/send_stream 已废弃，请使用 analysis job API: POST /api/agent/jobs + GET /api/agent/jobs/<job_id>/events",
    }), 410


@agent_bp.route('/agent/jobs', methods=['POST'])
def create_analysis_job():
    """
    创建后台分析任务。

    Web 层只做认证、参数校验和 job 入队，不执行 Agent/MCP。
    请求级幂等由 Idempotency-Key 和 job_service 的数据库唯一约束共同保证。
    """
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401

    data = request.get_json(silent=True) or {}
    message = data.get("message")
    session_id = data.get("session_id")
    input_user_file_id = data.get("input_user_file_id")
    raw_web_search_enabled = data.get("web_search_enabled", False)
    if not isinstance(raw_web_search_enabled, bool):
        return jsonify({"success": False, "error": "web_search_enabled 必须是布尔值"}), 400
    web_search_enabled = raw_web_search_enabled
    if not isinstance(message, str) or not message.strip():
        return jsonify({"success": False, "error": "消息不能为空"}), 400
    if not session_id:
        return jsonify({"success": False, "error": "请求无效，缺少会话ID"}), 400
    try:
        idempotency_key = job_service.normalize_idempotency_key(
            request.headers.get("Idempotency-Key")
        )
    except job_service.InvalidIdempotencyKeyError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    try:
        #  创建任务，获取任务是否创建成功表标识
        job, existing = job_service.create_job(
            current_user["id"],
            session_id,
            message,
            idempotency_key,
            input_user_file_id,
            web_search_enabled=web_search_enabled,
            request_id=get_request_id(),
        )
        bind_request_log_context(
            session_id=session_id,
            job_id=job["job_id"],
        )
        log_event(
            LOGGER,
            "job.create.replayed" if existing else "job.create.accepted",
            details={"status": str(job["status"])},
        )
        return jsonify({
            "success": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "existing": existing,
            "message_id": job.get("message_id"),
            "conversation_version": job.get("conversation_version"),
        }), 200 if existing else 202
    except job_service.OwnershipDeniedError as exc:
        log_authorization_denied(
            LOGGER,
            resource_type=exc.resource_type,
            action="create_job",
        )
        return jsonify({"success": False, "error": str(exc)}), 403
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except job_service.ActiveJobConflictError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "code": "active_job_conflict",
            "active_job": {
                "job_id": exc.job.get("job_id"),
                "status": exc.job.get("status"),
            },
        }), 409
    except job_service.IdempotencyConflictError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        log_event(
            LOGGER,
            "job.create.failed",
            details={"reason_code": "unexpected_error"},
            exc_info=True,
        )
        return jsonify({"success": False, "error": "创建任务失败"}), 500


@agent_bp.route('/agent/jobs/active')
def get_active_analysis_jobs():
    """读取当前用户的活动 Job 摘要，供刷新页面恢复等待或执行状态。"""
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401
    session_id = (request.args.get("session_id") or "").strip() or None
    if session_id and len(session_id) > 36:
        return jsonify({"success": False, "error": "会话 ID 无效"}), 400
    jobs = []
    for job in job_service.get_active_jobs(current_user["id"], session_id):
        job["last_event_id"] = job_service.get_latest_event_id(job["job_id"])
        jobs.append(job)
    return jsonify({"success": True, "jobs": jobs})


@agent_bp.route('/agent/jobs/<job_id>/events')
def stream_analysis_job_events(job_id: str):
    """
    向前端推送指定 job 的 SSE 事件流。
    """
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401

    # 校验该会话属于当前用户，防止前端篡改
    try:
        job = job_service.get_job_for_user(
            job_id,
            current_user["id"],
            detect_ownership_denial=True,
        )
    except job_service.OwnershipDeniedError as exc:
        log_authorization_denied(
            LOGGER,
            resource_type=exc.resource_type,
            action="stream_events",
        )
        return jsonify({"success": False, "error": "任务不存在"}), 404
    if not job:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    visible_boundary = job_service.get_public_event_boundary(job_id, current_user["id"])
    if visible_boundary == 0:
        return jsonify({"success": False, "error": "任务所属消息已被替换", "code": "job_superseded"}), 404
    bind_request_log_context(
        session_id=job.get("session_id"),
        job_id=job["job_id"],
    )

    last_event_id = request.headers.get("Last-Event-ID") or request.args.get("last_event_id") or "0"
    try:
        after_id = int(last_event_id)
    except ValueError:
        after_id = 0

    def generate():
        """轮询事件表并生成 SSE；终态事件出现后结束连接。"""
        # nonlocal是外部嵌套函数内的变量
        nonlocal after_id
        last_heartbeat = time.monotonic()
        try:
            ## 循环监听
            while True:
                events = job_service.read_events_after(job_id, after_id, user_id=current_user["id"])
                for row in events:
                    # 这里获取数据库中的id，并传给前端
                    after_id = int(row["id"])
                    ## payload事件具体包
                    payload = row["payload_json"] or {}
                    if isinstance(payload, dict) and "type" not in payload:
                        payload = {**payload, "type": row["event_type"]}
                    payload = public_event_payload(row["event_type"], payload)
                    yield _sse(row["event_type"], payload, after_id)
                    if row["event_type"] in job_service.TERMINAL_EVENTS or row["event_type"] == "interrupt":
                        return

                current_job = job_service.get_job_for_user(job_id, current_user["id"])
                if not current_job or current_job["status"] in job_service.TERMINAL_STATUSES:
                    return
                ## 返回单调递增的时间戳
                now = time.monotonic()
                # 如果超过了心跳设置间隔，返回空包，保持连接
                if now - last_heartbeat >= settings.SSE_HEARTBEAT_INTERVAL_SECONDS:
                    yield _sse("heartbeat", {"type": "heartbeat", "job_id": job_id})
                    last_heartbeat = now
                time.sleep(settings.SSE_POLL_INTERVAL_SECONDS)
        except Exception:
            # 流式响应开始后 Flask 的默认 500 日志边界不再可靠；保持异常向上
            # 传播，只补齐一次受管请求结果事件。
            log_request_failure(LOGGER, exc_info=True)
            raise

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@agent_bp.route("/agent/jobs/<job_id>/resume", methods=["POST"])
def resume_analysis_job(job_id: str):
    """提交 waiting_input Job 的恢复输入。"""
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401
    data = request.get_json(silent=True) or {}
    try:
        idempotency_key = job_service.normalize_idempotency_key(
            request.headers.get("Idempotency-Key")
        )
        job, existing = job_service.resume_job(
            current_user["id"],
            job_id,
            data.get("question_id"),
            data.get("answer", data.get("message")),
            idempotency_key,
        )
        bind_request_log_context(
            session_id=job.get("session_id"),
            job_id=job["job_id"],
        )
        return jsonify({
            "success": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "existing": existing,
            "message_id": job.get("message_id"),
            "conversation_version": job.get("conversation_version"),
        }), 200 if existing else 202
    except job_service.InvalidIdempotencyKeyError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except job_service.IdempotencyConflictError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except job_service.JobStateConflictError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "code": "job_state_conflict",
            "status": (exc.job or {}).get("status"),
        }), 409
    except job_service.OwnershipDeniedError as exc:
        log_authorization_denied(
            LOGGER,
            resource_type=exc.resource_type,
            action="resume_job",
        )
        return jsonify({"success": False, "error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        log_request_failure(LOGGER, exc_info=True)
        return jsonify({"success": False, "error": "恢复任务失败"}), 500


@agent_bp.route("/agent/jobs/<job_id>/cancel", methods=["POST"])
def cancel_analysis_job(job_id: str):
    """即时逻辑取消 queued/running/waiting_input Job。"""
    current_user = get_current_session_user()
    if not current_user:
        return jsonify({"success": False, "error": "用户未登录或会话已过期"}), 401
    try:
        idempotency_key = job_service.normalize_idempotency_key(
            request.headers.get("Idempotency-Key")
        )
        job, existing = job_service.cancel_job(
            current_user["id"],
            job_id,
            idempotency_key,
        )
        bind_request_log_context(
            session_id=job.get("session_id"),
            job_id=job["job_id"],
        )
        return jsonify({
            "success": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "existing": existing,
        }), 200 if existing else 202
    except job_service.InvalidIdempotencyKeyError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except job_service.IdempotencyConflictError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except job_service.JobStateConflictError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "code": "job_state_conflict",
            "status": (exc.job or {}).get("status"),
        }), 409
    except job_service.OwnershipDeniedError as exc:
        log_authorization_denied(
            LOGGER,
            resource_type=exc.resource_type,
            action="cancel_job",
        )
        return jsonify({"success": False, "error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    except Exception:
        log_request_failure(LOGGER, exc_info=True)
        return jsonify({"success": False, "error": "取消任务失败"}), 500
