"""
app.auth.authorization - 统一授权与安全回跳

- 请求级身份与权限缓存
- 基于权限表的访问控制装饰器
- 登录页与登录后回跳地址的白名单校验

未登录 API 返回 401；已登录但缺少权限的 API 返回 403；页面未登录跳转
/auth/sign-in，已登录但缺少权限返回受控 403 页面。
"""

from functools import wraps
from html import escape
from re import fullmatch
from urllib.parse import urlencode, urlsplit

from flask import g, jsonify, make_response, redirect, request

from app.auth.rbac import (
    PERMISSION_ADMIN_ACCESS,
    get_user_permissions,
)
from app.auth.session_guard import get_current_session_user
from app.request_context import get_request_id


AUTH_SIGN_IN_PATH = "/auth/sign-in"
DEFAULT_ADMIN_PAGE = "/admin/database"
DEFAULT_USER_PAGE = "/dashboard"
ADMIN_PAGE_PATHS = frozenset({
    "/admin",
    "/admin/overview",
    "/admin/users",
    "/admin/sessions",
    "/admin/jobs",
    "/admin/files",
    "/admin/database",
    "/admin/database/settings",
    "/admin/database/audit",
})
DASHBOARD_PAGE_PATHS = frozenset({
    "/dashboard",
    "/dashboard/settings",
})
RAG_EVAL_PAGE_PATH = "/rag-eval"
DASHBOARD_SESSION_PREFIX = "/dashboard/session/"


def _sanitize_local_path(raw_target):
    """把候选回跳地址收敛为同源路径，丢弃查询参数、片段和可疑编码。"""
    if not isinstance(raw_target, str) or not raw_target:
        return None
    if raw_target.startswith("//") or "\\" in raw_target:
        return None
    if any(ord(character) < 32 for character in raw_target):
        return None
    try:
        parsed = urlsplit(raw_target)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    return parsed.path.rstrip("/") or "/"


def _is_dashboard_session_path(path: str) -> bool:
    """确认会话详情路径的会话标识只包含安全字符。"""
    if not path.startswith(DASHBOARD_SESSION_PREFIX):
        return False
    session_id = path[len(DASHBOARD_SESSION_PREFIX):]
    return bool(fullmatch(r"[A-Za-z0-9._-]{1,64}", session_id))


def safe_return_target(raw_target):
    """只保留登录后允许回跳的同源页面路径。"""
    path = _sanitize_local_path(raw_target)
    if path is None:
        return None
    if path in ADMIN_PAGE_PATHS or path in DASHBOARD_PAGE_PATHS:
        return path
    if path == RAG_EVAL_PAGE_PATH:
        return path
    if _is_dashboard_session_path(path):
        return path
    return None


def safe_admin_return_target(raw_target):
    """只保留已知同源管理员页面路径。"""
    path = safe_return_target(raw_target)
    return path if path in ADMIN_PAGE_PATHS else None


def sign_in_url(raw_target=None):
    """返回统一登录入口，并只携带白名单校验通过的内部回跳路径。"""
    target = safe_return_target(raw_target)
    if target is None:
        return AUTH_SIGN_IN_PATH
    return f"{AUTH_SIGN_IN_PATH}?{urlencode({'next': target})}"


def admin_login_url(raw_target=None):
    """返回统一登录入口，并只携带经过白名单校验的管理员回跳路径。"""
    return sign_in_url(safe_admin_return_target(raw_target))


def get_current_permissions() -> frozenset:
    """返回当前请求用户的权限集合，同一请求内只查询一次。"""
    if hasattr(g, "current_permissions"):
        return g.current_permissions
    current_user = get_current_session_user()
    permissions = (
        frozenset()
        if current_user is None
        else get_user_permissions(current_user["id"])
    )
    g.current_permissions = permissions
    return permissions


def has_permission(permission_key: str) -> bool:
    """判断当前请求用户是否拥有指定权限。"""
    return permission_key in get_current_permissions()


def _auth_required_response():
    """构造 API 未登录响应。"""
    return jsonify({
        "success": False,
        "error": "用户未登录或会话已过期",
        "code": "auth_required",
        "request_id": get_request_id(),
    }), 401


def _permission_denied_response(permission_key: str):
    """构造 API 权限不足响应，管理员权限保留原有文案与错误码。"""
    if permission_key == PERMISSION_ADMIN_ACCESS:
        return jsonify({
            "success": False,
            "error": "需要管理员权限",
            "code": "admin_required",
            "request_id": get_request_id(),
        }), 403
    return jsonify({
        "success": False,
        "error": "权限不足",
        "code": "permission_denied",
        "request_id": get_request_id(),
    }), 403


def _html_forbidden_response(title: str, message: str):
    """构造统一的 403 HTML 响应头，禁止缓存并阻止内容嗅探。"""
    response = make_response(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
</head>
<body>
  <main>
    <h1>{title}</h1>
    {message}
  </main>
</body>
</html>""",
        403,
    )
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _admin_forbidden_page():
    """返回真实 403 拒绝页，再把浏览器送回普通用户首页显示提示。"""
    safe_home_url = escape("/?notice=admin_required", quote=True)
    response = _html_forbidden_response(
        "无管理员权限",
        (
            "<p>当前账号不能访问管理后台，正在返回普通用户首页。</p>"
            f"\n    <p><a href=\"{safe_home_url}\">立即返回首页</a></p>"
        ),
    )
    response.headers["Refresh"] = f"0; url={safe_home_url}"
    return response


def _permission_forbidden_page(permission_key: str):
    """返回普通权限不足的受控 403 页面。"""
    safe_permission = escape(permission_key, quote=True)
    return _html_forbidden_response(
        "无访问权限",
        (
            f"<p>当前账号缺少访问该功能所需的权限（{safe_permission}）。</p>"
            "\n    <p><a href=\"/\">返回官网首页</a></p>"
        ),
    )


def enforce_permission(permission_key: str, *, page: bool = False):
    """校验当前请求权限，通过时返回 None，否则返回可直接返回的响应。"""
    current_user = get_current_session_user()
    if current_user is None or not current_user.get("is_active"):
        if page:
            return redirect(sign_in_url(request.path))
        return _auth_required_response()
    g.current_user = current_user
    if permission_key in get_current_permissions():
        return None
    if page:
        if permission_key == PERMISSION_ADMIN_ACCESS:
            return _admin_forbidden_page()
        return _permission_forbidden_page(permission_key)
    return _permission_denied_response(permission_key)


def require_permission(permission_key: str, *, page: bool = False):
    """构造要求指定权限的视图包装器，页面与 API 使用不同的拒绝语义。"""

    def decorator(view_func):
        """包装目标视图并在进入前校验权限。"""

        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            """校验权限后调用原视图。"""
            denied = enforce_permission(permission_key, page=page)
            if denied is not None:
                return denied
            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


def require_authenticated_user(view_func):
    """要求请求来自有效登录用户，未登录 API 返回 401。"""

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        """校验登录状态后调用原视图。"""
        current_user = get_current_session_user()
        if current_user is None or not current_user.get("is_active"):
            return _auth_required_response()
        g.current_user = current_user
        return view_func(*args, **kwargs)

    return wrapped_view


def admin_required(view_func=None, *, page: bool = False):
    """要求当前请求来自拥有管理员权限的用户，并区分页面与 API 未登录响应。"""
    requirement = require_permission(PERMISSION_ADMIN_ACCESS, page=page)
    if view_func is None:
        return requirement
    return requirement(view_func)
