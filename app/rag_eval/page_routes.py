"""
app.rag_eval.page_routes - RAG 评测台页面入口

- /rag-eval 页面入口
- /rag-eval/assets/ 构建资源
- /rag-eval/brand/ 品牌图标与应用清单

页面与资源共用同一权限边界，避免绕过页面校验直接读取构建产物。
"""

import logging
from pathlib import Path

from flask import Blueprint, jsonify, send_from_directory

from app.auth.authorization import enforce_permission
from app.auth.rbac import PERMISSION_RAG_EVAL_ACCESS
from app.request_context import get_request_id, log_request_failure
from config.settings import settings


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

rag_eval_page_bp = Blueprint("rag_eval_page", __name__, url_prefix="/rag-eval")


def rag_eval_dist_dir() -> Path:
    """返回本地或容器内 RAG 评测台构建产物目录。"""
    configured = settings.RAG_EVAL_FRONTEND_DIST_DIR
    if configured:
        return Path(configured)
    return PROJECT_ROOT / "app" / "rag_eval" / "frontend_dist"


def _rag_eval_frontend_missing_response():
    """构造稳定的构建缺失响应，并记录不含路径细节的请求失败。"""
    log_request_failure(
        LOGGER,
        status_code=503,
        reason_code="unavailable",
    )
    return jsonify({
        "success": False,
        "error": "RAG 评测台前端尚未构建",
        "code": "rag_eval_frontend_missing",
        "request_id": get_request_id(),
    }), 503


@rag_eval_page_bp.before_request
def enforce_rag_eval_page_access():
    """页面与页面资源统一要求 rag_eval.access，未登录跳转统一登录入口。"""
    return enforce_permission(PERMISSION_RAG_EVAL_ACCESS, page=True)


@rag_eval_page_bp.route("")
@rag_eval_page_bp.route("/")
def rag_eval_page():
    """只向拥有访问权限的用户返回 RAG 评测台入口。"""
    dist_dir = rag_eval_dist_dir()
    if not (dist_dir / "index.html").is_file():
        return _rag_eval_frontend_missing_response()
    response = send_from_directory(dist_dir, "index.html")
    response.cache_control.no_cache = True
    response.cache_control.max_age = 0
    return response


@rag_eval_page_bp.route("/assets/<path:filename>")
def rag_eval_asset(filename: str):
    """只在页面权限校验通过后返回带 hash 的构建资源。"""
    dist_dir = rag_eval_dist_dir()
    if not (dist_dir / "index.html").is_file():
        return _rag_eval_frontend_missing_response()
    response = send_from_directory(dist_dir / "assets", filename)
    response.cache_control.public = True
    response.cache_control.max_age = 31536000
    response.cache_control.immutable = True
    return response


@rag_eval_page_bp.route("/brand/<path:filename>")
def rag_eval_brand_asset(filename: str):
    """只在页面权限校验通过后返回评测台品牌图标与应用清单。"""
    dist_dir = rag_eval_dist_dir()
    if not (dist_dir / "index.html").is_file():
        return _rag_eval_frontend_missing_response()
    response = send_from_directory(dist_dir / "brand", filename)
    response.cache_control.no_cache = True
    response.cache_control.max_age = 0
    return response
