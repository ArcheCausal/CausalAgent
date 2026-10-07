"""基于 Flask Cookie Session 的同步 CSRF 令牌。"""

from __future__ import annotations

from functools import wraps
import secrets

from flask import jsonify, request, session
import logging

from app.auth.authorization import admin_required
from app.request_context import current_request_log_details, get_request_id
from observability.logging_runtime import log_event


CSRF_SESSION_KEY = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
LOGGER = logging.getLogger(__name__)


def ensure_csrf_token() -> str:
    """返回当前会话令牌，不存在时生成不可预测的新值。"""
    token = session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_token_is_valid() -> bool:
    """使用常量时间比较校验请求头和当前 Session 令牌。"""
    expected = session.get(CSRF_SESSION_KEY)
    supplied = request.headers.get(CSRF_HEADER)
    return (
        isinstance(expected, str)
        and isinstance(supplied, str)
        and secrets.compare_digest(expected, supplied)
    )


def csrf_rejection_response():
    """构造统一的 CSRF 拒绝响应，并记录缺失或无效这两种原因。"""
    expected = session.get(CSRF_SESSION_KEY)
    supplied = request.headers.get(CSRF_HEADER)
    log_event(
        LOGGER,
        "security.csrf.rejected",
        details={
            **current_request_log_details(),
            "reason_code": (
                "csrf_missing"
                if not isinstance(expected, str) or not isinstance(supplied, str)
                else "csrf_invalid"
            ),
        },
    )
    return jsonify({
        "success": False,
        "error": "CSRF 令牌无效或已过期",
        "code": "csrf_invalid",
        "request_id": get_request_id(),
    }), 403


def admin_write_required(view_func):
    """组合管理员强一致授权与 Session CSRF 校验。"""

    @wraps(view_func)
    def csrf_checked(*args, **kwargs):
        """拒绝缺失或无效 CSRF 请求头的管理员写请求。"""
        if not csrf_token_is_valid():
            return csrf_rejection_response()
        return view_func(*args, **kwargs)

    return admin_required(csrf_checked)
