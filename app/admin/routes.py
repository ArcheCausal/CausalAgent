"""管理员数据库看板、在线配置 API 与 Vue 静态入口。"""

from pathlib import Path

from flask import (
    Blueprint,
    g,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
)
import logging

from Database.inspection import MAX_SLOW_QUERY_LIMIT
from Database.monitor_settings import (
    MonitorSettingsValidationError,
    MonitorSettingsVersionConflict,
    get_monitor_settings,
    reset_monitor_settings,
    save_monitor_settings,
)
from Database.monitoring import (
    DEFAULT_REFRESH_GROUPS,
    get_dashboard_snapshots,
    get_database_overview_snapshot,
    get_deep_audit_snapshot,
    get_db_health,
    get_integrity_snapshot,
    get_sql_performance_snapshot,
    get_worker_snapshot_from_cache,
    request_snapshot_refresh,
)
from app.admin.audit_service import (
    list_monitor_setting_events,
    record_admin_audit_event,
)
from app.request_context import log_request_failure
from app.admin.business_service import (
    download_file,
    get_attachment_content,
    get_business_overview,
    get_file_detail,
    get_job_content,
    get_job_detail,
    get_message_content,
    get_session_detail,
    get_user_detail,
    list_files,
    list_job_checkpoints,
    list_job_events,
    list_jobs,
    list_message_attachments,
    list_session_messages,
    list_sessions,
    list_users,
    preview_file_csv,
)
from app.admin.contracts import (
    AdminApiError,
    admin_api_endpoint,
    api_success,
    audited_access,
    content_chunk_limit,
    parse_limit,
    parse_non_negative_int,
)
from app.admin.write_service import (
    delete_file as delete_managed_file,
    delete_user as delete_managed_user,
    execute_user_operation,
    get_file_delete_impact,
    get_user_delete_impact,
    get_operation,
    preview_user_operation,
)
from app.auth.authorization import admin_required
from app.auth.csrf import admin_write_required
from app.request_context import get_request_id
from config.settings import settings


LOGGER = logging.getLogger(__name__)


admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")
admin_page_bp = Blueprint("admin_page", __name__, url_prefix="/admin")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _admin_dist_dir() -> Path:
    """返回本地或容器内管理员 Vue 构建产物目录。"""
    configured = settings.ADMIN_FRONTEND_DIST_DIR
    return Path(configured) if configured else PROJECT_ROOT / "admin-frontend" / "dist"


def _serve_admin_index():
    """按显式开发配置跳转 Vite，否则返回同源生产 index。"""
    if settings.ADMIN_VITE_DEV_SERVER_URL:
        return redirect(f"{settings.ADMIN_VITE_DEV_SERVER_URL}{request.path}")
    dist_dir = _admin_dist_dir()
    if not (dist_dir / "index.html").is_file():
        log_request_failure(
            LOGGER,
            status_code=503,
            reason_code="unavailable",
        )
        return jsonify({
            "success": False,
            "error": "管理员前端尚未构建",
            "code": "admin_frontend_missing",
            "request_id": get_request_id(),
        }), 503
    return send_from_directory(dist_dir, "index.html")


@admin_page_bp.route("")
@admin_page_bp.route("/")
@admin_required(page=True)
def admin_root_page():
    """实时确认管理员身份后，把后台根路径送到固定默认落点。"""
    return redirect("/admin/database")


@admin_page_bp.route("/overview")
@admin_page_bp.route("/users")
@admin_page_bp.route("/sessions")
@admin_page_bp.route("/jobs")
@admin_page_bp.route("/files")
@admin_page_bp.route("/database")
@admin_page_bp.route("/database/settings")
@admin_page_bp.route("/database/audit")
@admin_required(page=True)
def database_dashboard_page():
    """仅向实时校验通过的管理员返回任一 Vue 管理端页面。"""
    return _serve_admin_index()


@admin_page_bp.route("/assets/<path:filename>")
@admin_required(page=True)
def admin_asset(filename: str):
    """仅向实时校验通过的管理员返回 Vue 哈希静态资源。"""
    return send_from_directory(_admin_dist_dir() / "assets", filename)


@admin_page_bp.route("/brand/<path:filename>")
@admin_required(page=True)
def admin_brand_asset(filename: str):
    """仅向实时校验通过的管理员返回 Vue 品牌图标与应用清单。"""
    return send_from_directory(_admin_dist_dir() / "brand", filename)


@admin_bp.route("/brand/logo")
@admin_required
def admin_brand_logo():
    """从仓库唯一品牌原图返回管理员侧栏 Logo。"""
    response = send_file(
        PROJECT_ROOT / "README" / "CausalAgent.png",
        mimetype="image/png",
        conditional=True,
        max_age=86400,
    )
    response.headers["Cache-Control"] = "private, max-age=86400"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@admin_bp.route("/db/health")
@admin_required
def db_health():
    """返回仅管理员可读的数据库健康状态。"""
    try:
        return jsonify({"success": True, "data": get_db_health()})
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "读取数据库健康状态失败"}), 500


@admin_bp.route("/db/overview")
@admin_required
def db_overview():
    """从共享快照返回 revision、节点、连接和表容量事实。"""
    return jsonify({"success": True, "data": get_database_overview_snapshot()})


@admin_bp.route("/db/dashboard")
@admin_required
def db_dashboard():
    """一次返回看板所需的全部共享快照和服务端刷新策略。"""
    return jsonify({"success": True, "data": get_dashboard_snapshots()})


@admin_bp.route("/db/refresh", methods=["POST"])
@admin_write_required
def db_refresh():
    """登记实时、SQL 性能和容量快照的共享手动刷新请求。"""
    try:
        requested = request_snapshot_refresh(DEFAULT_REFRESH_GROUPS)
        return jsonify({"success": True, "data": requested}), 202
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "登记数据库看板刷新请求失败"}), 500


@admin_bp.route("/db/integrity")
@admin_required
def db_integrity():
    """返回最近一次运行期完整性审计共享快照。"""
    mode = request.args.get("mode", "quick")
    if mode != "quick":
        return jsonify({"success": False, "error": "mode 仅支持 quick"}), 400
    return jsonify({"success": True, "data": get_integrity_snapshot()})


@admin_bp.route("/db/integrity/run", methods=["POST"])
@admin_write_required
def db_integrity_run():
    """登记独立完整性审计请求，不受定时审计开关限制。"""
    try:
        requested = request_snapshot_refresh(("integrity",))
        return jsonify({"success": True, "data": requested}), 202
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "登记数据库完整性审计请求失败"}), 500


@admin_bp.route("/db/slow-queries")
@admin_required
def db_slow_queries():
    """从共享快照返回 SQL 性能摘要，并保留旧慢查询字段。"""
    raw_limit = request.args.get("limit", "20")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "limit 必须是整数"}), 400
    if not 1 <= limit <= MAX_SLOW_QUERY_LIMIT:
        return jsonify({
            "success": False,
            "error": f"limit 必须在 1 到 {MAX_SLOW_QUERY_LIMIT} 之间",
        }), 400
    try:
        return jsonify({"success": True, "data": get_sql_performance_snapshot(limit=limit)})
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "读取慢查询摘要失败"}), 500


@admin_bp.route("/jobs/workers")
@admin_required
def job_workers():
    """从实时共享快照返回 worker 与任务状态。"""
    try:
        report = get_worker_snapshot_from_cache()
        return jsonify({
            "success": True,
            "data": report["jobs"],
            "summary": report["summary"],
            "meta": {
                key: report[key]
                for key in (
                    "status",
                    "observed_at",
                    "source_role",
                    "source_alias",
                    "is_estimate",
                    "warning",
                )
            },
        })
    except Exception:
        log_request_failure(LOGGER)
        return jsonify({"success": False, "error": "读取 worker 任务状态失败"}), 500


def _settings_error(
    *,
    message: str,
    code: str,
    status: int,
    fields: dict | None = None,
    current: dict | None = None,
):
    """构造带 request ID 的稳定监控配置错误响应。"""
    payload = {
        "success": False,
        "error": message,
        "code": code,
        "request_id": get_request_id(),
    }
    if fields is not None:
        payload["fields"] = fields
    if current is not None:
        payload["current"] = current
    return jsonify(payload), status


@admin_bp.route("/db/settings")
@admin_required
def db_settings():
    """返回七项数据库监控覆盖值、有效值、来源和版本。"""
    return jsonify({
        "success": True,
        "data": get_monitor_settings(force_refresh=True),
    })


def _record_failed_setting_write(
    *,
    action: str,
    submitted: object,
    error_code: str,
) -> None:
    """尽力记录配置写入的未处理失败结果。"""
    record_admin_audit_event(
        actor=g.current_user,
        action=action,
        target_type="database_monitor_settings",
        target_id="1",
        old_values=None,
        new_values=submitted,
        result="failed",
        error_code=error_code,
        request_id=get_request_id(),
    )


@admin_bp.route("/db/settings", methods=["PUT"])
@admin_write_required
def db_settings_update():
    """校验乐观版本并在主库事务内保存七项覆盖值。"""
    body = request.get_json(silent=True) or {}
    try:
        data = save_monitor_settings(
            version=body.get("version"),
            overrides=body.get("overrides"),
            actor=g.current_user,
            request_id=get_request_id(),
        )
        return jsonify({"success": True, "data": data})
    except MonitorSettingsValidationError as exc:
        return _settings_error(
            message="数据库监控配置校验失败",
            code="validation_error",
            status=400,
            fields=exc.errors,
        )
    except MonitorSettingsVersionConflict as exc:
        return _settings_error(
            message="配置已被其他管理员修改，请重新加载",
            code="version_conflict",
            status=409,
            current=exc.current,
        )
    except Exception:
        log_request_failure(LOGGER)
        _record_failed_setting_write(
            action="db_monitor_settings.update",
            submitted=body.get("overrides"),
            error_code="settings_write_failed",
        )
        return _settings_error(
            message="保存数据库监控配置失败",
            code="settings_write_failed",
            status=500,
        )


@admin_bp.route("/db/settings/reset", methods=["POST"])
@admin_write_required
def db_settings_reset():
    """按乐观版本把全部数据库监控覆盖值重置为空。"""
    body = request.get_json(silent=True) or {}
    try:
        data = reset_monitor_settings(
            version=body.get("version"),
            actor=g.current_user,
            request_id=get_request_id(),
        )
        return jsonify({"success": True, "data": data})
    except MonitorSettingsValidationError as exc:
        return _settings_error(
            message="数据库监控配置校验失败",
            code="validation_error",
            status=400,
            fields=exc.errors,
        )
    except MonitorSettingsVersionConflict as exc:
        return _settings_error(
            message="配置已被其他管理员修改，请重新加载",
            code="version_conflict",
            status=409,
            current=exc.current,
        )
    except Exception:
        log_request_failure(LOGGER)
        _record_failed_setting_write(
            action="db_monitor_settings.reset",
            submitted=None,
            error_code="settings_reset_failed",
        )
        return _settings_error(
            message="重置数据库监控配置失败",
            code="settings_reset_failed",
            status=500,
        )


@admin_bp.route("/db/settings/history")
@admin_required
def db_settings_history():
    """有界游标返回数据库监控配置变更记录。"""
    try:
        limit = int(request.args.get("limit", "20"))
        if not 1 <= limit <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return _settings_error(
            message="limit 必须在 1 到 100 之间",
            code="validation_error",
            status=400,
            fields={"limit": "必须在 1 到 100 之间"},
        )
    raw_before_id = request.args.get("before_id")
    try:
        before_id = int(raw_before_id) if raw_before_id is not None else None
        if before_id is not None and before_id < 1:
            raise ValueError
    except (TypeError, ValueError):
        return _settings_error(
            message="before_id 必须是正整数",
            code="validation_error",
            status=400,
            fields={"before_id": "必须是正整数"},
        )
    try:
        return jsonify({
            "success": True,
            "data": list_monitor_setting_events(
                limit=limit,
                before_id=before_id,
            ),
        })
    except Exception:
        log_request_failure(LOGGER)
        return _settings_error(
            message="读取数据库监控配置历史失败",
            code="settings_history_failed",
            status=500,
        )


@admin_bp.route("/business/overview")
@admin_api_endpoint
@admin_required
def business_overview():
    """返回业务表估算数量和共享监控快照摘要。"""
    return api_success(get_business_overview())


@admin_bp.route("/business/users")
@admin_api_endpoint
@admin_required
def business_users():
    """分页、搜索并筛选脱敏用户记录。"""
    data = list_users(
        limit=parse_limit(request.args.get("limit")),
        cursor=request.args.get("cursor"),
        q=request.args.get("q"),
        role=request.args.get("role"),
        is_active=request.args.get("is_active"),
    )
    return api_success(data)


def _user_operation_action(_values: dict) -> str:
    """为统一用户写接口生成不含秘密的审计动作名。"""
    body = request.get_json(silent=True)
    action = body.get("action", "unknown") if isinstance(body, dict) else "invalid"
    return f"business.user.{action}"


def _user_operation_targets(_values: dict) -> str:
    """为批量写接口生成有界用户 ID 审计目标。"""
    body = request.get_json(silent=True)
    raw_ids = body.get("target_ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list):
        return "invalid"
    return ",".join(str(value) for value in raw_ids[:50])


@admin_bp.route("/business/users/operations/preview", methods=["POST"])
@admin_api_endpoint
@audited_access(
    action=lambda values: f"{_user_operation_action(values)}.preview",
    target_type="user_batch",
    target_id=_user_operation_targets,
)
@admin_write_required
def business_user_operation_preview():
    """返回用户批量操作的主库强一致预览，不执行变更。"""
    return api_success(preview_user_operation(
        request.get_json(silent=True),
        actor=g.current_user,
    ))


@admin_bp.route("/business/users/operations", methods=["POST"])
@admin_api_endpoint
@audited_access(
    action=_user_operation_action,
    target_type="user_batch",
    target_id=_user_operation_targets,
    audit_success=False,
)
@admin_write_required
def business_user_operation():
    """执行带重新认证、幂等和逐目标审计的用户批量操作。"""
    return api_success(execute_user_operation(
        request.get_json(silent=True),
        actor=g.current_user,
        idempotency_key=request.headers.get("Idempotency-Key"),
    ))


@admin_bp.route("/business/users/<int:user_id>/delete-impact")
@admin_api_endpoint
@audited_access(
    action="business.user.delete.preview",
    target_type="user",
    target_id=lambda values: str(values["user_id"]),
)
@admin_required
def business_user_delete_impact(user_id: int):
    """返回用户物理删除的完整影响计数与阻断原因。"""
    return api_success(get_user_delete_impact(user_id, actor=g.current_user))


@admin_bp.route("/business/users/<int:user_id>", methods=["DELETE"])
@admin_api_endpoint
@audited_access(
    action="business.user.delete",
    target_type="user",
    target_id=lambda values: str(values["user_id"]),
    audit_success=False,
)
@admin_write_required
def business_user_delete(user_id: int):
    """在主库事务中执行用户删除，并等待跨库 checkpoint cleanup。"""
    result = delete_managed_user(
        user_id,
        request.get_json(silent=True),
        actor=g.current_user,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    return api_success(result, status=202 if result.get("status") == "running" else 200)


@admin_bp.route("/operations/<operation_id>")
@admin_api_endpoint
@admin_required
def admin_operation(operation_id: str):
    """返回当前管理员发起的受控操作及异步 checkpoint 清理状态。"""
    return api_success(get_operation(operation_id, actor=g.current_user))


@admin_bp.route("/business/users/<int:user_id>")
@admin_api_endpoint
@audited_access(
    action="business.user.detail.view",
    target_type="user",
    target_id=lambda values: str(values["user_id"]),
)
@admin_required
def business_user_detail(user_id: int):
    """返回单个用户的只读详情并记录敏感访问。"""
    return api_success(get_user_detail(user_id))


@admin_bp.route("/business/sessions")
@admin_api_endpoint
@admin_required
def business_sessions():
    """分页、搜索并筛选会话摘要。"""
    data = list_sessions(
        limit=parse_limit(request.args.get("limit")),
        cursor=request.args.get("cursor"),
        q=request.args.get("q"),
        user_id=request.args.get("user_id"),
        is_archived=request.args.get("is_archived"),
    )
    return api_success(data)


@admin_bp.route("/business/sessions/<session_id>")
@admin_api_endpoint
@audited_access(
    action="business.session.detail.view",
    target_type="session",
    target_id=lambda values: str(values["session_id"]),
)
@admin_required
def business_session_detail(session_id: str):
    """返回会话元数据且不夹带消息正文。"""
    return api_success(get_session_detail(session_id))


@admin_bp.route("/business/sessions/<session_id>/messages")
@admin_api_endpoint
@audited_access(
    action="business.session.messages.list",
    target_type="session",
    target_id=lambda values: str(values["session_id"]),
)
@admin_required
def business_session_messages(session_id: str):
    """返回指定会话的有界消息摘要列表。"""
    data = list_session_messages(
        session_id=session_id,
        limit=parse_limit(request.args.get("limit")),
        cursor=request.args.get("cursor"),
        message_type=request.args.get("message_type"),
    )
    return api_success(data)


@admin_bp.route("/business/messages/<int:message_id>/attachments")
@admin_api_endpoint
@audited_access(
    action="business.message.attachments.list",
    target_type="chat_message",
    target_id=lambda values: str(values["message_id"]),
)
@admin_required
def business_message_attachments(message_id: int):
    """返回消息附件元数据，附件正文继续延迟读取。"""
    return api_success({"items": list_message_attachments(message_id)})


@admin_bp.route("/business/messages/<int:message_id>/content")
@admin_api_endpoint
@audited_access(
    action="business.message.content.view",
    target_type="chat_message",
    target_id=lambda values: str(values["message_id"]),
)
@admin_required
def business_message_content(message_id: int):
    """按 64 KiB 上限读取一段聊天正文。"""
    data = get_message_content(
        message_id,
        offset=parse_non_negative_int(
            request.args.get("offset"),
            field="offset",
        ),
        limit=content_chunk_limit(request.args.get("limit")),
    )
    return api_success(data)


@admin_bp.route("/business/attachments/<int:attachment_id>/content")
@admin_api_endpoint
@audited_access(
    action="business.attachment.content.view",
    target_type="chat_attachment",
    target_id=lambda values: str(values["attachment_id"]),
)
@admin_required
def business_attachment_content(attachment_id: int):
    """按 64 KiB 上限读取一段聊天附件正文。"""
    data = get_attachment_content(
        attachment_id,
        offset=parse_non_negative_int(
            request.args.get("offset"),
            field="offset",
        ),
        limit=content_chunk_limit(request.args.get("limit")),
    )
    return api_success(data)


@admin_bp.route("/business/jobs")
@admin_api_endpoint
@admin_required
def business_jobs():
    """分页、搜索并筛选分析任务摘要。"""
    data = list_jobs(
        limit=parse_limit(request.args.get("limit")),
        cursor=request.args.get("cursor"),
        q=request.args.get("q"),
        status=request.args.get("status"),
        user_id=request.args.get("user_id"),
        session_id=request.args.get("session_id"),
    )
    return api_success(data)


@admin_bp.route("/business/jobs/<job_id>")
@admin_api_endpoint
@audited_access(
    action="business.job.detail.view",
    target_type="analysis_job",
    target_id=lambda values: str(values["job_id"]),
)
@admin_required
def business_job_detail(job_id: str):
    """返回任务元数据且不夹带输入、结果和错误正文。"""
    return api_success(get_job_detail(job_id))


@admin_bp.route("/business/jobs/<job_id>/events")
@admin_api_endpoint
@audited_access(
    action="business.job.events.list",
    target_type="analysis_job",
    target_id=lambda values: str(values["job_id"]),
)
@admin_required
def business_job_events(job_id: str):
    """返回指定任务的有界事件时间线。"""
    data = list_job_events(
        job_id=job_id,
        limit=parse_limit(request.args.get("limit")),
        cursor=request.args.get("cursor"),
    )
    return api_success(data)


@admin_bp.route("/business/jobs/<job_id>/checkpoints")
@admin_api_endpoint
@audited_access(
    action="business.job.checkpoints.list",
    target_type="analysis_job",
    target_id=lambda values: str(values["job_id"]),
)
@admin_required
def business_job_checkpoints(job_id: str):
    """返回 PostgreSQL 中可精确归属当前任务的 checkpoint 安全摘要。"""
    data = list_job_checkpoints(
        job_id=job_id,
        limit=parse_limit(request.args.get("limit")),
        cursor=request.args.get("cursor"),
    )
    return api_success(data)


@admin_bp.route("/business/jobs/<job_id>/content")
@admin_api_endpoint
@audited_access(
    action=lambda _values: (
        f"business.job.{request.args.get('kind') or 'unknown'}.view"
    ),
    target_type="analysis_job",
    target_id=lambda values: str(values["job_id"]),
)
@admin_required
def business_job_content(job_id: str):
    """按类别和 64 KiB 上限读取任务敏感正文。"""
    data = get_job_content(
        job_id,
        kind=request.args.get("kind"),
        offset=parse_non_negative_int(
            request.args.get("offset"),
            field="offset",
        ),
        limit=content_chunk_limit(request.args.get("limit")),
        sequence=parse_non_negative_int(
            request.args.get("sequence"),
            field="sequence",
        ),
    )
    return api_success(data)


@admin_bp.route("/business/files")
@admin_api_endpoint
@admin_required
def business_files():
    """分页、搜索并筛选文件元数据。"""
    data = list_files(
        limit=parse_limit(request.args.get("limit")),
        cursor=request.args.get("cursor"),
        q=request.args.get("q"),
        user_id=request.args.get("user_id"),
        mime_type=request.args.get("mime_type"),
    )
    return api_success(data)


@admin_bp.route("/business/files/<int:file_id>")
@admin_api_endpoint
@audited_access(
    action="business.file.detail.view",
    target_type="user_file",
    target_id=lambda values: str(values["file_id"]),
)
@admin_required
def business_file_detail(file_id: int):
    """返回文件元数据且不夹带 BLOB 或内部对象 ID。"""
    return api_success(get_file_detail(file_id))


@admin_bp.route("/business/files/<int:file_id>/preview")
@admin_api_endpoint
@audited_access(
    action="business.file.preview",
    target_type="user_file",
    target_id=lambda values: str(values["file_id"]),
    audit_success=False,
)
@admin_required
def business_file_preview(file_id: int):
    """安全预览 CSV，并原子更新访问计数和审计。"""
    return api_success(preview_file_csv(file_id, actor=g.current_user))


@admin_bp.route("/business/files/<int:file_id>/download")
@admin_api_endpoint
@audited_access(
    action="business.file.download",
    target_type="user_file",
    target_id=lambda values: str(values["file_id"]),
    audit_success=False,
)
@admin_required
def business_file_download(file_id: int):
    """以附件方式下载文件，并在返回前原子记录访问。"""
    content, metadata = download_file(file_id, actor=g.current_user)
    response = send_file(
        content,
        mimetype=metadata.get("mime_type") or "application/octet-stream",
        as_attachment=True,
        download_name=Path(str(metadata.get("original_filename") or "download")).name,
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@admin_bp.route("/business/files/<int:file_id>/delete-impact")
@admin_api_endpoint
@audited_access(
    action="business.file.delete.preview",
    target_type="user_file",
    target_id=lambda values: str(values["file_id"]),
)
@admin_required
def business_file_delete_impact(file_id: int):
    """返回文件行、BLOB 和活动任务保护的删除预览。"""
    return api_success(get_file_delete_impact(file_id))


@admin_bp.route("/business/files/<int:file_id>", methods=["DELETE"])
@admin_api_endpoint
@audited_access(
    action="business.file.delete",
    target_type="user_file",
    target_id=lambda values: str(values["file_id"]),
    audit_success=False,
)
@admin_write_required
def business_file_delete(file_id: int):
    """删除 user_files 逻辑记录，并按引用计数清理不可变 BLOB。"""
    return api_success(delete_managed_file(
        file_id,
        request.get_json(silent=True),
        actor=g.current_user,
        idempotency_key=request.headers.get("Idempotency-Key"),
    ))


@admin_bp.route("/db/audit")
@admin_api_endpoint
@audited_access(
    action=lambda _values: (
        f"database.audit.{request.args.get('mode', 'quick')}.view"
    ),
    target_type="database_audit",
    target_id=lambda _values: request.args.get("mode", "quick"),
)
@admin_required
def db_audit():
    """返回 quick 或最近一次 deep 共享审计快照。"""
    mode = request.args.get("mode", "quick")
    if mode == "quick":
        return api_success(get_integrity_snapshot())
    if mode == "deep":
        return api_success(get_deep_audit_snapshot())
    raise AdminApiError(
        code="invalid_query",
        message="mode 仅支持 quick/deep",
        fields={"mode": "仅支持 quick/deep"},
    )


@admin_bp.route("/db/audit/run", methods=["POST"])
@admin_api_endpoint
@audited_access(
    action="database.audit.run",
    target_type="database_audit",
    target_id=lambda _values: str(
        (request.get_json(silent=True) or {}).get("mode", "deep")
    ),
)
@admin_write_required
def db_audit_run():
    """登记 quick 或 deep 共享审计请求，真正执行仍由 monitor 完成。"""
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "deep")
    groups = {
        "quick": ("integrity",),
        "deep": ("deep_audit",),
    }.get(mode)
    if groups is None:
        raise AdminApiError(
            code="invalid_body",
            message="mode 仅支持 quick/deep",
            fields={"mode": "仅支持 quick/deep"},
        )
    return api_success(request_snapshot_refresh(groups), status=202)
