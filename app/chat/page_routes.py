"""
app.chat.page_routes - 普通用户应用页面入口

- /dashboard、/dashboard/session/<session_id>、/dashboard/settings
- /dashboard-assets/ 构建资源

页面和页面资源都要求 dashboard.access；未登录访客统一跳转 /auth/sign-in。
"""

import logging
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, send_from_directory

from app.auth.authorization import enforce_permission
from app.auth.rbac import PERMISSION_DASHBOARD_ACCESS
from app.request_context import get_request_id, log_request_failure
from config.settings import settings


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

dashboard_page_bp = Blueprint("dashboard_page", __name__, url_prefix="/dashboard")
dashboard_asset_bp = Blueprint("dashboard_asset", __name__, url_prefix="/dashboard-assets")


def dashboard_dist_dir() -> Path:
    """返回普通用户应用的 Vue 构建产物目录。"""
    configured = settings.CHAT_FRONTEND_DIST_DIR
    if configured:
        return Path(configured)
    return PROJECT_ROOT / "chat-frontend" / "dist"


def _dashboard_frontend_missing_response():
    """构造稳定的构建缺失响应，并记录不含路径细节的请求失败。"""
    log_request_failure(
        LOGGER,
        status_code=503,
        reason_code="unavailable",
    )
    return jsonify({
        "success": False,
        "error": "普通用户应用前端尚未构建",
        "code": "chat_frontend_missing",
        "request_id": get_request_id(),
    }), 503


def _serve_dashboard_index():
    """按显式开发配置跳转 Vite，否则同源返回普通用户应用入口。"""
    if settings.CHAT_VITE_DEV_SERVER_URL:
        return redirect(f"{settings.CHAT_VITE_DEV_SERVER_URL}/dashboard-assets{request.path}")
    dist_dir = dashboard_dist_dir()
    if not (dist_dir / "index.html").is_file():
        return _dashboard_frontend_missing_response()
    response = send_from_directory(dist_dir, "index.html")
    response.cache_control.no_cache = True
    response.cache_control.max_age = 0
    return response


@dashboard_page_bp.before_request
def enforce_dashboard_page_access():
    """页面入口统一要求 dashboard.access，未登录跳转统一登录入口。"""
    return enforce_permission(PERMISSION_DASHBOARD_ACCESS, page=True)


@dashboard_page_bp.route("")
@dashboard_page_bp.route("/")
@dashboard_page_bp.route("/settings")
@dashboard_page_bp.route("/session/<session_id>")
def dashboard_page(session_id: str | None = None):
    """向拥有普通用户应用权限的登录用户返回工作区入口；会话由前端按地址加载。"""
    return _serve_dashboard_index()


@dashboard_asset_bp.before_request
def enforce_dashboard_asset_access():
    """页面资源沿用同一权限边界，避免绕过入口直接读取构建产物。"""
    return enforce_permission(PERMISSION_DASHBOARD_ACCESS, page=True)


@dashboard_asset_bp.route("/<path:filename>")
def dashboard_asset(filename: str):
    """返回普通用户应用的哈希资源，入口缺失时不返回半成品。"""
    dist_dir = dashboard_dist_dir()
    if not (dist_dir / "index.html").is_file():
        return _dashboard_frontend_missing_response()
    response = send_from_directory(dist_dir, filename)
    if filename.startswith("assets/"):
        response.cache_control.public = True
        response.cache_control.max_age = 31536000
        response.cache_control.immutable = True
    else:
        response.cache_control.no_cache = True
        response.cache_control.max_age = 0
    return response
