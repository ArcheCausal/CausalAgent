"""
会话用户校验工具。
"""

from flask import g, has_request_context, session
import logging

from app.request_context import bind_request_log_context
from observability.logging_runtime import log_event


LOGGER = logging.getLogger(__name__)
_UNRESOLVED = object()


def find_user_by_id(user_id):
    """延迟导入认证服务并按 ID 获取当前数据库用户。"""
    from app.auth.service import find_user_by_id as _find_user_by_id

    return _find_user_by_id(user_id)


def get_current_session_user():
    """返回当前请求的会话用户，同一请求内只解析一次。

    身份有效性在每个请求首次需要时重新查询主库，请求内后续调用复用缓存，
    既不重复查库，也不把身份有效期延长到请求之外。
    """
    if has_request_context():
        cached = getattr(g, "_session_user_resolved", _UNRESOLVED)
        if cached is not _UNRESOLVED:
            return cached
    user = _resolve_session_user()
    if has_request_context():
        g._session_user_resolved = user
        if user is not None:
            g.current_user = user
    return user


def _resolve_session_user():
    """
    查询当前会话对应的真实用户。

    如果浏览器里还保留着旧 session，但当前数据库中该用户已不存在或
    已被禁用，则主动清空 session，避免继续以失效身份访问受保护接口。
    """
    user_id = session.get("user_id")
    if not user_id:
        return None

    user = find_user_by_id(user_id)
    if not user:
        log_event(
            LOGGER,
            "security.session.revoked",
            details={"reason_code": "account_missing"},
        )
        session.clear()
        return None

    if not user["is_active"]:
        log_event(
            LOGGER,
            "security.session.revoked",
            details={"reason_code": "account_disabled"},
        )
        session.clear()
        return None

    database_auth_version = int(user.get("auth_version") or 1)
    session_auth_version = session.get("auth_version")
    if session_auth_version is None:
        # 兼容升级前签发的 Cookie：只允许尚未发生安全变更的初始版本补写。
        if database_auth_version != 1:
            log_event(
                LOGGER,
                "security.session.revoked",
                details={"reason_code": "auth_version_changed"},
            )
            session.clear()
            return None
        session["auth_version"] = database_auth_version
    else:
        try:
            matches_version = int(session_auth_version) == database_auth_version
        except (TypeError, ValueError):
            matches_version = False
        if not matches_version:
            log_event(
                LOGGER,
                "security.session.revoked",
                details={"reason_code": "auth_version_changed"},
            )
            session.clear()
            return None

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    bind_request_log_context(user_id=user["id"])
    return user
