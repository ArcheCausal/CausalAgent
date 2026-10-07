"""
app.main.routes - 官网页面与共享设置接口

- 官网公开页面：/、/product、/pricing、/about、/docs、/changelog
- 官网认证页面：/auth/sign-in、/auth/sign-up
- 官网构建产物：/site-assets/
- 共享设置接口：/api/setting

登录后的普通用户应用入口由 app.chat.page_routes 提供。
"""
import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, send_from_directory

from app.request_context import get_request_id, log_request_failure
from config.settings import settings


main_bp = Blueprint('main', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTING_DIR = os.path.join(BASE_DIR, "setting")
PROJECT_ROOT = Path(BASE_DIR)
LOGGER = logging.getLogger(__name__)


## 设置
@main_bp.route('/api/setting')
def setting():
    """按主题返回用户协议或使用手册正文。"""
    topic = request.args.get('topic')
    topic_to_file = {
        "userAgreement": "Userprivacy.md",
        "userManual": "manual.md",
    }
    filename = topic_to_file.get(topic)
    if filename is None:
        return jsonify({"success": False, "error": "请求的内容主题不存在"}), 404
    file_path = os.path.join(SETTING_DIR, filename)
    if not os.path.exists(file_path):
        return jsonify({"success": False, "error": f"请求的内容文件 '{filename}' 未找到"}), 404
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return jsonify({"success": True, "messages": content})


def _site_dist_dir() -> Path:
    """返回官网 Vue 构建产物目录。"""
    configured = settings.WEBSITE_FRONTEND_DIST_DIR
    if configured:
        return Path(configured)
    return PROJECT_ROOT / "website-frontend" / "dist"


def _site_frontend_missing_response():
    """构造稳定的官网构建缺失响应，并记录不含路径细节的请求失败。"""
    log_request_failure(
        LOGGER,
        status_code=503,
        reason_code="unavailable",
    )
    return jsonify({
        "success": False,
        "error": "官网 Vue 前端尚未构建",
        "code": "website_frontend_missing",
        "request_id": get_request_id(),
    }), 503


def _serve_site_page():
    """按显式开发配置跳转 Vite，否则同源返回官网入口页面。"""
    if settings.WEBSITE_VITE_DEV_SERVER_URL:
        query = request.query_string.decode("ascii")
        suffix = f"?{query}" if query else ""
        return redirect(f"{settings.WEBSITE_VITE_DEV_SERVER_URL}/site-assets{request.path}{suffix}")

    dist_dir = _site_dist_dir()
    if not (dist_dir / "index.html").is_file():
        return _site_frontend_missing_response()
    response = send_from_directory(dist_dir, "index.html")
    response.cache_control.no_cache = True
    response.cache_control.max_age = 0
    return response


@main_bp.route('/')
@main_bp.route('/product')
@main_bp.route('/pricing')
@main_bp.route('/about')
@main_bp.route('/docs')
@main_bp.route('/changelog')
def site_page():
    """提供官网公开页面；未登录访客与已登录用户看到同一份公开内容。"""
    return _serve_site_page()


@main_bp.route('/auth/sign-in')
@main_bp.route('/auth/sign-up')
def auth_page():
    """提供官网登录与注册页面，登录后按白名单回跳内部路径。"""
    return _serve_site_page()


@main_bp.route('/site-assets/<path:filename>')
def site_asset(filename: str):
    """从官网 dist 提供同源资源；缺少完整构建时不返回半成品。"""
    dist_dir = _site_dist_dir()
    if not (dist_dir / "index.html").is_file():
        return _site_frontend_missing_response()
    response = send_from_directory(dist_dir, filename)
    if filename.startswith("assets/"):
        response.cache_control.public = True
        response.cache_control.max_age = 31536000
        response.cache_control.immutable = True
    else:
        response.cache_control.no_cache = True
        response.cache_control.max_age = 0
    return response
