"""
RAG评测管理路由 —— 面向开发者的HTTP API。
"""
import json
import logging
import math
import queue
import time
import uuid
from functools import wraps
from pathlib import Path
from flask import Blueprint, g, jsonify, make_response, request, Response, session

from app.rag_eval.service import (
    get_rag_eval_status,
    get_rag_eval_config,
    get_production_rag_config,
    publish_current_config_to_production,
    update_rag_eval_config,
    ConfigValidationError,
    get_step_descriptions,
    current_event_timestamp,
)
from app.rag_eval.isolated_runs import (
    delete_uploaded_source,
    isolated_run_manager,
    list_source_catalog,
    register_uploaded_source,
    ReleaseGateError,
    update_source_display_name,
)
from app.rag_eval.index_binding import IndexBindingError
from app.rag_eval.run_lifecycle import RunLifecycle
from app.rag_eval.dataset_registry import DatasetRegistry, DatasetRevisionConflict
from app.rag_eval import job_service
from app.rag_eval.profile_store import (
    create_custom_profile,
    delete_custom_profile,
    list_strategy_profiles,
    publish_custom_profile,
    update_custom_profile,
)
from config.settings import settings
from app.admin.audit_service import record_admin_audit_event
from app.request_context import get_request_id, log_request_failure
from app.auth.authorization import enforce_permission, require_permission
from app.auth.csrf import csrf_rejection_response, csrf_token_is_valid
from app.auth.rbac import (
    PERMISSION_RAG_EVAL_ACCESS,
    PERMISSION_RAG_EVAL_GOVERNANCE,
    PERMISSION_RAG_EVAL_PUBLISH,
    PERMISSION_RAG_EVAL_READ,
    PERMISSION_RAG_EVAL_ROLLBACK,
    PERMISSION_RAG_EVAL_RUN,
)

rag_eval_bp = Blueprint("rag_eval", __name__, url_prefix="/api/rag_eval")
dataset_registry = DatasetRegistry(Path(settings.RAG_EVAL_DATASET_ROOT))

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# 发布、回滚和治理使用独立权限，因此不由方法级读/运行检查兜底。
SPECIFIC_PERMISSION_ENDPOINTS = frozenset({
    "rag_eval.api_publish_strategy_profile",
    "rag_eval.api_publish_production_config",
    "rag_eval.api_multimodal_release_gate_check",
    "rag_eval.api_multimodal_release_publish",
    "rag_eval.api_multimodal_release_rollback",
    "rag_eval.api_freeze_gold_v2",
    "rag_eval.api_bind_baseline_v2",
    "rag_eval.api_start_gold_v2_governance",
})


@rag_eval_bp.before_request
def enforce_rag_eval_access():
    """RAG 评测台全部接口要求访问权限，写请求额外要求 CSRF 令牌。"""
    denied = enforce_permission(PERMISSION_RAG_EVAL_ACCESS)
    if denied is not None:
        return denied
    if request.method not in SAFE_METHODS and not csrf_token_is_valid():
        return csrf_rejection_response()
    if request.endpoint in SPECIFIC_PERMISSION_ENDPOINTS:
        return None
    permission_key = (
        PERMISSION_RAG_EVAL_READ
        if request.method in SAFE_METHODS
        else PERMISSION_RAG_EVAL_RUN
    )
    return enforce_permission(permission_key)


def _audit_target_id() -> str:
    """返回审计用的受限目标标识，优先使用路由变量。"""
    for value in request.view_args.values():
        if isinstance(value, str) and value:
            return value[:200]
    return request.path[:200]


def _record_rag_eval_audit(action: str, *, result: str, status_code: int) -> None:
    """记录发布、回滚和治理操作；审计写入失败只记日志，不改变原响应。"""
    actor = getattr(g, "current_user", None) or {}
    record_admin_audit_event(
        actor=actor,
        action=action,
        target_type="rag_eval_operation",
        target_id=_audit_target_id(),
        old_values=None,
        new_values={"method": request.method, "status_code": status_code},
        result=result,
        request_id=get_request_id(),
        error_code=None if status_code < 400 else f"http_{status_code}",
    )


def audit_rag_eval_operation(action: str):
    """按下发状态记录发布、回滚与治理操作审计结果。"""

    def decorator(view_func):
        """包装目标视图并在返回前写入审计事件。"""

        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            """调用原视图并按响应状态记录审计。"""
            response = make_response(view_func(*args, **kwargs))
            status_code = response.status_code
            if status_code < 400:
                result = "success"
            elif status_code < 500:
                result = "rejected"
            else:
                result = "failed"
            _record_rag_eval_audit(action, result=result, status_code=status_code)
            return response

        return wrapped_view

    return decorator


def _json_response(data, status=200):
    """统一JSON响应格式。"""
    return jsonify(_json_safe(data)), status


def _json_safe(value):
    """把 NaN/Infinity 转成 null，避免浏览器 JSON.parse 失败。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _parse_expected_generation(payload):
    """解析可选 active pointer generation，保留 0 作为初始版本。"""
    value = payload.get("expected_generation")
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected_generation must be a non-negative integer")
    return value


# ---- 状态与配置 ----

@rag_eval_bp.route("/isolated/capacity", methods=["GET"])
def api_get_isolated_capacity():
    """返回隔离评测持久队列的只读容量快照。"""
    try:
        return _json_response({"success": True, "data": job_service.get_capacity_snapshot()})
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)

@rag_eval_bp.route("/status", methods=["GET"])
def api_status():
    """获取当前benchmark、向量库和最新评测的汇总状态。"""
    try:
        return _json_response({"success": True, "data": get_rag_eval_status()})
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/config", methods=["GET"])
def api_get_config():
    """获取所有可调参数，按类别分组（retrieval_profile / ragas / pipeline）。"""
    try:
        return _json_response({"success": True, "data": get_rag_eval_config()})
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/config", methods=["PUT"])
def api_update_config():
    """更新运行时可调参数（内存中生效，restart后恢复默认）。"""
    try:
        overrides = request.get_json(silent=True) or {}
        result = update_rag_eval_config(overrides)
        return _json_response({"success": True, "data": result})
    except ConfigValidationError as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


def _current_profile_owner_id():
    """返回当前会话用户；未登录时使用本地开发共享 profile。"""
    return session.get("user_id")


@rag_eval_bp.route("/profiles", methods=["GET"])
def api_list_strategy_profiles():
    """列出只读内置 profile 和当前用户可访问的自定义 profile。"""
    try:
        return _json_response({"success": True, "data": list_strategy_profiles(_current_profile_owner_id())})
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/profiles", methods=["POST"])
def api_create_strategy_profile():
    """创建自定义 unified retrieval/Ragas profile。"""
    try:
        payload = request.get_json(silent=True) or {}
        profile = create_custom_profile(payload, _current_profile_owner_id())
        return _json_response({"success": True, "data": profile}, 201)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/profiles/<profile_id>", methods=["PUT"])
def api_update_strategy_profile(profile_id):
    """更新自定义 profile；内置 profile 不可修改。"""
    try:
        payload = request.get_json(silent=True) or {}
        profile = update_custom_profile(profile_id, payload, _current_profile_owner_id())
        return _json_response({"success": True, "data": profile})
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/profiles/<profile_id>", methods=["DELETE"])
def api_delete_strategy_profile(profile_id):
    """删除自定义 profile；内置和当前正式 profile 不可删除。"""
    try:
        delete_custom_profile(profile_id, _current_profile_owner_id())
        return _json_response({"success": True, "data": {"profile_id": profile_id}})
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/profiles/<profile_id>/publish", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_PUBLISH)
@audit_rag_eval_operation("rag_eval.profile.publish")
def api_publish_strategy_profile(profile_id):
    """发布自定义 profile 的 retrieval 快照，并切换正式 profile 指针。"""
    try:
        payload = request.get_json(silent=True) or {}
        profile = publish_custom_profile(
            profile_id,
            _current_profile_owner_id(),
            note=str(payload.get("note") or ""),
        )
        return _json_response({"success": True, "data": profile})
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/production-config", methods=["GET"])
def api_get_production_config():
    """获取正式 RAG 调用当前使用的检索配置。"""
    try:
        return _json_response({"success": True, "data": get_production_rag_config()})
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/production-config/publish", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_PUBLISH)
@audit_rag_eval_operation("rag_eval.production_config.publish")
def api_publish_production_config():
    """把当前评测检索配置发布为正式 RAG 调用配置。"""
    try:
        payload = request.get_json(silent=True) or {}
        return _json_response({"success": True, "data": publish_current_config_to_production(payload)})
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/steps", methods=["GET"])
def api_step_descriptions():
    """返回pipeline各步骤的中文描述。"""
    return _json_response({"success": True, "data": get_step_descriptions()})


# ---- 隔离知识源与 staged index ----

def _dataset_error_response(message, status, error_code):
    return _json_response({"success": False, "error": message, "error_code": error_code}, status)


def _dataset_not_found(exc):
    return "not registered" in str(exc).lower()


def _dataset_from_payload(payload, *, required):
    """统一解析注册题集引用或内联 rag_eval_v1 数据集。"""
    dataset_ref = payload.get("dataset_ref")
    inline = payload.get("eval_dataset") or payload.get("dataset")
    if dataset_ref is not None and inline is not None:
        raise ValueError("dataset_ref and inline dataset are mutually exclusive")
    if dataset_ref is not None:
        if not isinstance(dataset_ref, dict):
            raise ValueError("dataset_ref must be an object")
        resolved = dataset_registry.resolve(dataset_ref)
        return resolved["bundle"], resolved
    if inline is not None:
        return inline, None
    if required:
        raise ValueError("dataset_ref or eval_dataset is required")
    return None, None


def _dataset_page_args():
    try:
        return int(request.args.get("page", 1)), int(request.args.get("page_size", 50))
    except (TypeError, ValueError) as exc:
        raise ValueError("page and page_size must be integers") from exc


@rag_eval_bp.route("/datasets", methods=["POST"])
def api_register_dataset():
    """注册不可变 rag_eval_v1 数据集快照。"""
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return _json_response({"success": True, "data": dataset_registry.register(payload)}, 201)
    except DatasetRevisionConflict as exc:
        return _dataset_error_response(str(exc), 409, "dataset_revision_conflict")
    except ValueError as exc:
        return _dataset_error_response(str(exc), 400, "invalid_request")


@rag_eval_bp.route("/datasets", methods=["GET"])
def api_list_datasets():
    """分页读取注册数据集元数据。"""
    try:
        page, page_size = _dataset_page_args()
        data = dataset_registry.list_datasets(
            dataset_kind=request.args.get("dataset_kind"),
            lifecycle_status=request.args.get("lifecycle_status"),
            page=page,
            page_size=page_size,
        )
        return _json_response({"success": True, "data": data})
    except ValueError as exc:
        return _dataset_error_response(str(exc), 400, "invalid_request")


@rag_eval_bp.route("/datasets/<dataset_id>/revisions", methods=["GET"])
def api_list_dataset_revisions(dataset_id):
    """分页读取一个注册数据集的版本元数据。"""
    try:
        page, page_size = _dataset_page_args()
        data = dataset_registry.list_revisions(dataset_id, page=page, page_size=page_size)
        if not data.get("total"):
            return _dataset_error_response("dataset not found", 404, "dataset_not_found")
        return _json_response({"success": True, "data": data})
    except ValueError as exc:
        return _dataset_error_response(str(exc), 400, "invalid_request")


@rag_eval_bp.route("/datasets/<dataset_id>/revisions/<revision>", methods=["GET"])
def api_get_dataset_revision(dataset_id, revision):
    """返回注册版本的元数据和完整 canonical bundle。"""
    try:
        data = dataset_registry.resolve({"dataset_id": dataset_id, "dataset_revision": revision})
        return _json_response({"success": True, "data": data})
    except ValueError as exc:
        if _dataset_not_found(exc):
            return _dataset_error_response(str(exc), 404, "dataset_not_found")
        return _dataset_error_response(str(exc), 400, "invalid_request")

def _parse_isolated_rag_input(payload):
    """把单问题或内联 rag_eval_v1 题集转换为统一问题列表。"""
    dataset, resolved = _dataset_from_payload(payload, required=False)
    if dataset is not None:
        if not isinstance(dataset, dict) or dataset.get("schema_version") != "rag_eval_v1":
            raise ValueError("eval_dataset.schema_version must be rag_eval_v1")
        samples = dataset.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError("rag_eval_v1 dataset requires a non-empty samples list")
        questions = samples
        identity = {
            "schema_version": "rag_eval_v1",
            "dataset_id": str(dataset.get("dataset_id") or payload.get("dataset_id") or "inline"),
            "sample_count": len(samples),
        }
        if resolved is not None:
            identity.update({
                "dataset_id": resolved["dataset_id"],
                "dataset_revision": resolved["dataset_revision"],
                "content_sha256": resolved["content_sha256"],
            })
    elif isinstance(payload.get("questions"), list):
        questions = payload["questions"]
        identity = {"schema_version": "isolated_questions_v1", "question_count": len(questions)}
    else:
        questions = [{"question": payload.get("question", "")}]
        identity = {"schema_version": "isolated_question_v1"}

    normalized = [item if isinstance(item, dict) else {"question": item} for item in questions]
    return normalized, identity


def _run_lifecycle():
    return RunLifecycle(isolated_run_manager)


def _run_state_response(run_id):
    try:
        return _json_response({"success": True, "data": _run_lifecycle().get_state(run_id)})
    except (KeyError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)


def _run_result_response(run_id):
    try:
        return _json_response({"success": True, "data": _run_lifecycle().get_result(run_id)})
    except (KeyError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)


def _run_artifact_response(run_id, artifact_name):
    try:
        artifact = _run_lifecycle().get_artifact(run_id, artifact_name)
        if artifact.media_type == "application/json":
            return _json_response({
                "success": True,
                "data": json.loads(artifact.path.read_text(encoding="utf-8")),
            })
        return Response(artifact.path.read_text(encoding="utf-8"), mimetype=artifact.media_type)
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)


def _run_stream_response(run_id):
    try:
        return _isolated_stream(run_id)
    except (KeyError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)


def _run_cancel_response(run_id):
    try:
        return _json_response({"success": True, "data": _run_lifecycle().cancel(run_id)})
    except (KeyError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)


def _run_delete_response(run_id, delete_method, **kwargs):
    """统一返回终态运行删除结果，并把引用保护作为冲突响应。"""
    try:
        result = getattr(isolated_run_manager, delete_method)(run_id, **kwargs)
        if result.get("status") == "running":
            return _json_response({
                "success": False,
                "error": result.get("message") or "run is still running",
                "data": result,
            }, 409)
        return _json_response({"success": True, "data": result})
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except (RuntimeError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


def _deprecated_run_response(response, run_id):
    response = make_response(response)
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'</api/rag_eval/isolated/runs/{run_id}>; rel="successor-version"'
    )
    return response

def _isolated_stream(run_id):
    """为知识源摄取和 staged index RAG 查询提供统一 SSE 流。"""
    state = isolated_run_manager.get(run_id)
    persistent_worker = state.get("execution_backend") == "persistent_worker"
    event_queue = None if persistent_worker else isolated_run_manager.subscribe(run_id)

    def generate():
        try:
            connected_event = {
                "type": "connected",
                "message": "隔离运行 SSE 连接已建立",
                "timestamp": current_event_timestamp(),
                "run_id": run_id,
            }
            yield f"data: {json.dumps(connected_event, ensure_ascii=False)}\n\n"
            if persistent_worker:
                cursor = 0
                last_heartbeat = time.monotonic()
                while True:
                    events, cursor = isolated_run_manager.read_events(run_id, cursor)
                    for event in events:
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        if event.get("type") in {"run_done", "run_error", "run_cancelled"}:
                            return
                    current = isolated_run_manager.get(run_id)
                    if current.get("status") not in {"created", "queued", "running", "cancelling"}:
                        return
                    if time.monotonic() - last_heartbeat >= 15:
                        yield "data: {\"type\":\"heartbeat\"}\n\n"
                        last_heartbeat = time.monotonic()
                    time.sleep(1)
            while True:
                try:
                    event = event_queue.get(timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") in {"run_done", "run_error", "run_cancelled"}:
                        break
                except queue.Empty:
                    yield "data: {\"type\":\"heartbeat\"}\n\n"
        except GeneratorExit:
            pass
        finally:
            isolated_run_manager.unsubscribe(run_id)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@rag_eval_bp.route("/isolated/runs/<run_id>", methods=["GET"])
def api_isolated_run_state(run_id):
    return _run_state_response(run_id)


@rag_eval_bp.route("/isolated/runs/<run_id>", methods=["DELETE"])
def api_delete_isolated_run(run_id):
    """按运行类型删除终态隔离产物，统一入口保留与旧端点相同的保护。"""
    try:
        state = isolated_run_manager.get(run_id)
        kind = state.get("kind")
        if kind == "evaluation":
            payload = request.get_json(silent=True) or {}
            return _run_delete_response(run_id, "delete_evaluation_run", force=payload.get("force") is True)
        if kind == "ingestion":
            payload = request.get_json(silent=True) or {}
            return _run_delete_response(run_id, "delete_ingestion_run", cascade=payload.get("cascade") is True)
        if kind in {"rag_query", "candidate_generation", "dataset_governance", "tuning_dataset_governance"}:
            return _run_delete_response(run_id, "delete_derived_run")
        raise ValueError("run type does not support deletion")
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)


@rag_eval_bp.route("/isolated/runs/<run_id>/result", methods=["GET"])
def api_isolated_run_result(run_id):
    return _run_result_response(run_id)


@rag_eval_bp.route("/isolated/runs/<run_id>/artifacts/<artifact_name>", methods=["GET"])
def api_isolated_run_artifact(run_id, artifact_name):
    return _run_artifact_response(run_id, artifact_name)


@rag_eval_bp.route("/isolated/runs/<run_id>/stream", methods=["GET"])
def api_isolated_run_stream(run_id):
    return _run_stream_response(run_id)


@rag_eval_bp.route("/isolated/runs/<run_id>/cancel", methods=["POST"])
def api_cancel_isolated_run(run_id):
    return _run_cancel_response(run_id)


@rag_eval_bp.route("/isolated/ingestion-runs", methods=["GET"])
def api_isolated_ingestion_history():
    """列出持久化摄取运行，供工作台恢复最近状态。"""
    try:
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page_size", default=50, type=int)
        return _json_response({
            "success": True,
            "data": isolated_run_manager.list_ingestion_history(
                status=request.args.get("status") or None,
                source_id=request.args.get("source_id") or None,
                page=page,
                page_size=page_size,
            ),
        })
    except (KeyError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/ingestion-runs", methods=["POST"])
def api_start_isolated_ingestion():
    """启动显式来源的全新隔离摄取任务。"""
    try:
        payload = request.get_json(silent=True) or {}
        source_ids = payload.get("source_ids")
        sources = payload.get("sources")
        max_pages = payload.get("max_pages")
        page_ranges = payload.get("page_ranges")
        allow_remote_data = payload.get("allow_remote_data", False)
        authorized_source_ids = payload.get("authorized_source_ids", [])
        embedding_config = payload.get("embedding_config", payload.get("embedding"))
        if source_ids is not None and not isinstance(source_ids, list):
            raise ValueError("source_ids must be a list")
        if sources is not None and not isinstance(sources, list):
            raise ValueError("sources must be a list")
        if max_pages is not None and (isinstance(max_pages, bool) or not isinstance(max_pages, int)):
            raise ValueError("max_pages must be an integer")
        if page_ranges is not None and not isinstance(page_ranges, list):
            raise ValueError("page_ranges must be a list")
        if not isinstance(allow_remote_data, bool):
            raise ValueError("allow_remote_data must be a boolean")
        if not isinstance(authorized_source_ids, list) or not all(isinstance(item, str) for item in authorized_source_ids):
            raise ValueError("authorized_source_ids must be a list of strings")
        if embedding_config is not None and not isinstance(embedding_config, dict):
            raise ValueError("embedding_config must be an object")
        ingestion_options = {
            "source_ids": source_ids,
            "sources": sources,
            "max_pages": max_pages,
            "page_ranges": page_ranges,
            "allow_remote_data": allow_remote_data,
            "authorized_source_ids": authorized_source_ids,
        }
        if embedding_config is not None:
            ingestion_options["embedding_config"] = embedding_config
        result = isolated_run_manager.start_ingestion(
            **ingestion_options,
        )
        return _json_response({"success": True, "data": result}, 202)
    except PermissionError as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except (FileNotFoundError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/source-catalog", methods=["GET"])
def api_isolated_source_catalog():
    """返回可选来源的稳定 ID 和摘要，隐藏宿主文件路径。"""
    try:
        return _json_response({"success": True, "data": {"sources": list_source_catalog()}})
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/multimodal/releases/status", methods=["GET"])
def api_multimodal_release_status():
    """读取正式 active/previous pointer 和指定隔离候选的只读状态。"""
    try:
        return _json_response({
            "success": True,
            "data": isolated_run_manager.release_status(
                request.args.get("ingestion_run_id", ""),
                request.args.get("index_version", ""),
                request.args.get("evaluation_run_id") or None,
            ),
        })
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/multimodal/releases/gate-check", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_GOVERNANCE)
@audit_rag_eval_operation("rag_eval.release.gate_check")
def api_multimodal_release_gate_check():
    """重新执行指定 staged 候选的正式发布门禁，不切换 active pointer。"""
    try:
        payload = request.get_json(silent=True) or {}
        expected_generation = _parse_expected_generation(payload)
        kwargs = {"expected_generation": expected_generation} if expected_generation is not None else {}
        result = isolated_run_manager.check_release(
            str(payload.get("ingestion_run_id") or ""),
            str(payload.get("index_version") or ""),
            str(payload.get("evaluation_run_id") or "") or None,
            str(payload.get("expected_active_index_version") or "") or None,
            **kwargs,
        )
        return _json_response({"success": True, "data": result})
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/multimodal/releases/publish", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_PUBLISH)
@audit_rag_eval_operation("rag_eval.release.publish")
def api_multimodal_release_publish():
    """用户显式确认后晋级候选并切换正式 active pointer。"""
    try:
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm") is not True:
            raise ValueError("confirm=true is required to publish the active pointer")
        expected_generation = _parse_expected_generation(payload)
        kwargs = {"expected_generation": expected_generation} if expected_generation is not None else {}
        result = isolated_run_manager.publish_release(
            str(payload.get("ingestion_run_id") or ""),
            str(payload.get("index_version") or ""),
            str(payload.get("evaluation_run_id") or ""),
            str(payload.get("expected_active_index_version") or "") or None,
            **kwargs,
        )
        return _json_response({"success": True, "data": result})
    except ReleaseGateError as exc:
        return _json_response({"success": False, "error": str(exc), "data": exc.report}, 409)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/multimodal/releases/rollback", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_ROLLBACK)
@audit_rag_eval_operation("rag_eval.release.rollback")
def api_multimodal_release_rollback():
    """执行与 CLI 一致的正式多模态版本回滚。"""
    try:
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm") is not True:
            raise ValueError("confirm=true is required to rollback the active pointer")
        expected_generation = _parse_expected_generation(payload)
        kwargs = {"expected_generation": expected_generation} if expected_generation is not None else {}
        result = isolated_run_manager.rollback_release(
            str(payload.get("index_version") or ""),
            str(payload.get("expected_active_index_version") or "") or None,
            **kwargs,
        )
        return _json_response({"success": True, "data": result})
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/sources", methods=["POST"])
def api_upload_isolated_source():
    """登记一个前端上传的知识源，不自动启动摄取。"""
    try:
        uploaded = request.files.get("file")
        if uploaded is None:
            raise ValueError("没有选择文件")
        content = uploaded.read(settings.MAX_UPLOAD_SIZE_BYTES + 1)
        source = register_uploaded_source(uploaded.filename, content)
        return _json_response({"success": True, "data": {"source": source}}, 201)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except OSError as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": "知识源保存失败"}, 500)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/sources/<source_id>", methods=["DELETE", "PATCH"])
def api_delete_isolated_source(source_id):
    """更新来源显示名或删除用户上传的来源文件，不触碰历史隔离产物。"""
    if request.method == "PATCH":
        try:
            payload = request.get_json(silent=True) or {}
            return _json_response({
                "success": True,
                "data": update_source_display_name(source_id, payload.get("display_name")),
            })
        except KeyError as exc:
            return _json_response({"success": False, "error": str(exc)}, 404)
        except ValueError as exc:
            return _json_response({"success": False, "error": str(exc)}, 400)
        except Exception as exc:
            log_request_failure(logging.getLogger(__name__), exc_info=True)
            return _json_response({"success": False, "error": str(exc)}, 500)
    try:
        return _json_response({"success": True, "data": delete_uploaded_source(source_id)})
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except RuntimeError as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/ingestion-runs/<run_id>", methods=["GET"])
def api_isolated_ingestion_state(run_id):
    """读取隔离知识源摄取任务状态。"""
    return _deprecated_run_response(_run_state_response(run_id), run_id)


@rag_eval_bp.route("/isolated/ingestion-runs/<run_id>", methods=["DELETE"])
def api_delete_isolated_ingestion(run_id):
    """删除没有下游引用的终态摄取运行及其 staged index。"""
    payload = request.get_json(silent=True) or {}
    return _deprecated_run_response(
        _run_delete_response(run_id, "delete_ingestion_run", cascade=payload.get("cascade") is True),
        run_id,
    )

@rag_eval_bp.route("/isolated/ingestion-runs/<run_id>/stream", methods=["GET"])
def api_isolated_ingestion_stream(run_id):
    """订阅隔离知识源摄取事件。"""
    return _deprecated_run_response(_run_stream_response(run_id), run_id)


@rag_eval_bp.route("/isolated/ingestion-runs/<run_id>/cancel", methods=["POST"])
def api_cancel_isolated_ingestion(run_id):
    """请求取消隔离知识源摄取任务。"""
    return _deprecated_run_response(_run_cancel_response(run_id), run_id)


@rag_eval_bp.route("/isolated/candidate-runs", methods=["POST"])
def api_start_isolated_candidate_generation():
    """在已完成的 staged index 上按用户指定数量生成独立评测集。"""
    try:
        payload = request.get_json(silent=True) or {}
        integer_fields = ("question_count", "max_workers")
        values = {}
        for field in integer_fields:
            value = payload.get(field, {"question_count": 48, "max_workers": 1}[field])
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field} must be an integer")
            values[field] = value
        if "question_count" not in payload and ("max_units" in payload or "questions_per_unit" in payload):
            max_units = payload.get("max_units", 48)
            questions_per_unit = payload.get("questions_per_unit", 1)
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (max_units, questions_per_unit)):
                raise ValueError("max_units and questions_per_unit must be integers")
            values = {"max_units": max_units, "questions_per_unit": questions_per_unit, "max_workers": values["max_workers"]}
        result = isolated_run_manager.start_candidate_generation(
            payload.get("ingestion_run_id", ""),
            payload.get("index_version", ""),
            dataset_id=payload.get("dataset_id"),
            **values,
        )
        return _json_response({"success": True, "data": result}, 202)
    except IndexBindingError as exc:
        return _json_response({"success": False, "error": str(exc), "error_code": exc.code}, 409)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/tuning-dataset-runs", methods=["POST"])
def api_start_tuning_dataset_governance():
    """启动独立的索引绑定调参集闭环，不进入正式评测历史。"""
    try:
        payload = request.get_json(silent=True) or {}
        return _json_response({"success": True, "data": isolated_run_manager.start_tuning_dataset_governance(
            payload.get("ingestion_run_id", ""), payload.get("index_version", ""),
            target_count=payload.get("target_count", 48),
            minimum_metric=payload.get("minimum_metric", 0.2),
        )}, 202)
    except IndexBindingError as exc:
        return _json_response({"success": False, "error": str(exc), "error_code": exc.code}, 409)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/tuning-dataset-runs/<run_id>", methods=["GET"])
def api_tuning_dataset_governance_state(run_id):
    return _deprecated_run_response(_run_state_response(run_id), run_id)


@rag_eval_bp.route("/isolated/tuning-dataset-runs/<run_id>", methods=["DELETE"])
def api_delete_tuning_dataset_governance(run_id):
    """删除已结束且未被其他产物引用的调参集治理运行。"""
    return _deprecated_run_response(_run_delete_response(run_id, "delete_derived_run"), run_id)


@rag_eval_bp.route("/isolated/tuning-dataset-runs/<run_id>/stream", methods=["GET"])
def api_tuning_dataset_governance_stream(run_id):
    return _deprecated_run_response(_run_stream_response(run_id), run_id)


@rag_eval_bp.route("/isolated/tuning-dataset-runs/<run_id>/result", methods=["GET"])
def api_tuning_dataset_governance_result(run_id):
    """读取独立调参集治理摘要，不返回正式评测报告。"""
    return _deprecated_run_response(_run_result_response(run_id), run_id)


@rag_eval_bp.route("/isolated/tuning-dataset-runs/<run_id>/artifacts/<path:artifact_name>", methods=["GET"])
def api_tuning_dataset_governance_artifact(run_id, artifact_name):
    """读取已登记的调参集或机器审核产物。"""
    return _deprecated_run_response(_run_artifact_response(run_id, artifact_name), run_id)


@rag_eval_bp.route("/isolated/candidate-runs/rebound-import", methods=["POST"])
def api_import_rebound_candidate():
    """挂载当前索引的重绑候选复审产物，供前端逐题审核。"""
    try:
        return _json_response({"success": True, "data": isolated_run_manager.import_rebound_candidate()})
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>", methods=["GET"])
def api_isolated_candidate_state(run_id):
    """读取候选题生成任务状态。"""
    return _deprecated_run_response(_run_state_response(run_id), run_id)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>", methods=["DELETE"])
def api_delete_isolated_candidate(run_id):
    """删除已结束且未被其他产物引用的候选题运行。"""
    return _deprecated_run_response(_run_delete_response(run_id, "delete_derived_run"), run_id)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>/result", methods=["GET"])
def api_isolated_candidate_result(run_id):
    """读取候选题生成结果摘要。"""
    return _deprecated_run_response(_run_result_response(run_id), run_id)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>/artifacts/<path:artifact_name>", methods=["GET"])
def api_isolated_candidate_artifact(run_id, artifact_name):
    """读取候选数据集或审核清单等隔离产物。"""
    return _deprecated_run_response(_run_artifact_response(run_id, artifact_name), run_id)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>/stream", methods=["GET"])
def api_isolated_candidate_stream(run_id):
    """订阅候选题生成事件。"""
    return _deprecated_run_response(_run_stream_response(run_id), run_id)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>/cancel", methods=["POST"])
def api_cancel_isolated_candidate(run_id):
    """请求取消候选题生成。"""
    return _deprecated_run_response(_run_cancel_response(run_id), run_id)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>/review", methods=["POST"])
def api_review_isolated_candidate(run_id):
    """保存逐题审核和编辑，服务端生成新的 reviewed candidate revision。"""
    try:
        payload = request.get_json(silent=True) or {}
        decisions = payload.get("decisions")
        updates = payload.get("updates") or []
        if not isinstance(decisions, list) or not isinstance(updates, list):
            raise ValueError("decisions and updates must be lists")
        result = isolated_run_manager.save_candidate_review(
            run_id,
            reviewer=payload.get("reviewer", ""),
            decisions=decisions,
            updates=updates,
        )
        return _json_response({"success": True, "data": result})
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/candidate-runs/<run_id>/rebind", methods=["POST"])
def api_rebind_isolated_candidate(run_id):
    """将候选 Gold locator 重绑到请求明确指定的 staged index，并要求重新审核。"""
    try:
        payload = request.get_json(silent=True) or {}
        return _json_response({"success": True, "data": isolated_run_manager.rebind_candidate_to_current_index(
            run_id,
            ingestion_run_id=payload.get("ingestion_run_id", ""),
            index_version=payload.get("index_version", ""),
        )})
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/gold-v2/freeze", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_GOVERNANCE)
@audit_rag_eval_operation("rag_eval.gold.freeze")
def api_freeze_gold_v2():
    """只从服务端保存的候选审核 run 尝试冻结 Gold v2。"""
    try:
        payload = request.get_json(silent=True) or {}
        run_id = str(payload.get("candidate_run_id") or "").strip()
        if not run_id:
            raise ValueError("candidate_run_id is required")
        return _json_response({"success": True, "data": isolated_run_manager.freeze_candidate_gold_v2(
            run_id,
            expected_ingestion_run_id=str(payload.get("ingestion_run_id") or "").strip(),
            expected_index_version=str(payload.get("index_version") or "").strip(),
            replace_existing=bool(payload.get("replace_existing")),
        )})
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/gold-v2/status", methods=["GET"])
def api_gold_v2_status():
    """返回本地冻结 Gold 基准集的安全摘要，不返回宿主机路径。"""
    from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import DEFAULT_GOLD_V2_OUTPUT
    from Agent.knowledge_base.rag.rag_eval.contracts import load_eval_dataset_bundle

    if not DEFAULT_GOLD_V2_OUTPUT.is_file():
        return _json_response({"success": True, "data": {"exists": False}})
    dataset = load_eval_dataset_bundle(DEFAULT_GOLD_V2_OUTPUT)
    candidate_bindings = {
        str((sample.get("source") or {}).get("index_binding", {}).get("index_version") or "")
        for sample in dataset["samples"]
        if (sample.get("source") or {}).get("generator")
    }
    candidate_bindings.discard("")
    bound_index_version = next(iter(candidate_bindings)) if len(candidate_bindings) == 1 else ""
    fixed_sample_count = sum(1 for sample in dataset["samples"] if not (sample.get("source") or {}).get("generator"))
    generated_sample_count = len(dataset["samples"]) - fixed_sample_count
    review_status = str((dataset.get("review") or {}).get("status") or "")
    payload = {
        "exists": True,
        "dataset_id": dataset["dataset_id"],
        "dataset_revision": dataset.get("dataset_revision", ""),
        "sample_count": len(dataset["samples"]),
        "fixed_sample_count": fixed_sample_count,
        "generated_sample_count": generated_sample_count,
        "freeze_status": "frozen" if review_status == "frozen" and len(dataset["samples"]) == 72 else "invalid",
        "bound_index_version": bound_index_version,
        "compatibility": "unselected",
        "compatibility_message": "请选择 staged index 后检查该 Gold 题集是否可用于严格检索评测。",
        "index_status": "unselected",
        "checked_sample_count": 0,
        "checked_fixed_sample_count": 0,
        "checked_generated_sample_count": 0,
    }
    try:
        from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import (
            DEFAULT_ACTIVE_POINTER,
            _resolve_active_index,
        )

        payload["production_index_version"] = _resolve_active_index(DEFAULT_ACTIVE_POINTER)["index_version"]
    except (ValueError, FileNotFoundError):
        payload["production_index_version"] = ""
    ingestion_run_id = str(request.args.get("ingestion_run_id") or "").strip()
    index_version = str(request.args.get("index_version") or "").strip()
    if ingestion_run_id or index_version:
        if not ingestion_run_id or not index_version:
            return _json_response({
                "success": False,
                "error": "ingestion_run_id and index_version must be provided together",
            }, 400)
        from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import validate_frozen_gold_bundle

        try:
            identity = isolated_run_manager.resolve_staged_index(ingestion_run_id, index_version)
            validation = validate_frozen_gold_bundle(
                dataset,
                index_dir=identity.index_dir,
                expected_snapshot={
                    "index_version": identity.index_version,
                    "manifest_sha256": identity.manifest_sha256,
                },
                require_fixed_binding=True,
            )
            payload.update({
                "compatibility": "compatible",
                "compatibility_message": "Gold locator 已绑定并验证存在于当前索引，可运行严格检索评测。",
                "index_status": "local_available",
                "checked_sample_count": validation["checked_sample_count"],
                "checked_fixed_sample_count": validation["checked_fixed_sample_count"],
                "checked_generated_sample_count": validation["checked_generated_sample_count"],
            })
        except (IndexBindingError, ValueError, FileNotFoundError) as exc:
            message = str(exc)
            index_status = "missing" if any(
                marker in message
                for marker in ("unavailable", "incomplete", "does not belong", "not staged")
            ) else "incompatible"
            payload.update({
                "compatibility": "rebind_required",
                "compatibility_message": message,
                "index_status": index_status,
            })
    return _json_response({"success": True, "data": payload})

@rag_eval_bp.route("/baseline-v2/bind", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_GOVERNANCE)
@audit_rag_eval_operation("rag_eval.baseline.bind")
def api_bind_baseline_v2():
    """绑定只读 active pointer、active_current 和已冻结 Gold v2。"""
    try:
        return _json_response({"success": True, "data": isolated_run_manager.bind_baseline_v2()})
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/gold-v2/governance", methods=["POST"])
@require_permission(PERMISSION_RAG_EVAL_GOVERNANCE)
@audit_rag_eval_operation("rag_eval.gold.governance")
def api_start_gold_v2_governance():
    """确认后把已完成 evaluation run 放入无人值守 Gold 健康治理队列。"""
    try:
        payload = request.get_json(silent=True) or {}
        evaluation_run_id = str(payload.get("evaluation_run_id") or "").strip()
        if not evaluation_run_id:
            raise ValueError("evaluation_run_id is required")
        return _json_response({"success": True, "data": isolated_run_manager.start_dataset_governance(
            evaluation_run_id,
            confirm=payload.get("confirm") is True,
        )}, 202)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/gold-v2/governance-runs/<run_id>", methods=["GET"])
def api_gold_v2_governance_state(run_id):
    """读取 Gold 健康治理任务阶段和计数摘要。"""
    return _deprecated_run_response(_run_state_response(run_id), run_id)


@rag_eval_bp.route("/gold-v2/governance-runs/<run_id>", methods=["DELETE"])
def api_delete_gold_v2_governance(run_id):
    """删除已结束且未被其他产物引用的 Gold 治理运行。"""
    return _deprecated_run_response(_run_delete_response(run_id, "delete_derived_run"), run_id)


@rag_eval_bp.route("/gold-v2/governance-runs/<run_id>/result", methods=["GET"])
def api_gold_v2_governance_result(run_id):
    """读取 Gold 健康治理结果。"""
    return _deprecated_run_response(_run_result_response(run_id), run_id)


@rag_eval_bp.route("/gold-v2/governance-runs/<run_id>/artifacts/<path:artifact_name>", methods=["GET"])
def api_gold_v2_governance_artifact(run_id, artifact_name):
    """读取治理逐题报告或诊断产物。"""
    return _deprecated_run_response(_run_artifact_response(run_id, artifact_name), run_id)


@rag_eval_bp.route("/gold-v2/governance-runs/<run_id>/stream", methods=["GET"])
def api_gold_v2_governance_stream(run_id):
    """订阅 Gold 健康治理阶段事件。"""
    return _deprecated_run_response(_run_stream_response(run_id), run_id)


@rag_eval_bp.route("/gold-v2/governance-runs/<run_id>/cancel", methods=["POST"])
def api_cancel_gold_v2_governance(run_id):
    """请求取消 Gold 健康治理。"""
    return _deprecated_run_response(_run_cancel_response(run_id), run_id)


@rag_eval_bp.route("/isolated/rag-runs", methods=["POST"])
def api_start_isolated_rag_query():
    """在指定 staged index 上启动真实检索与回答任务。"""
    try:
        payload = request.get_json(silent=True) or {}
        questions, input_identity = _parse_isolated_rag_input(payload)
        result = isolated_run_manager.start_rag_query(
            payload.get("ingestion_run_id", ""),
            payload.get("index_version", ""),
            questions,
            input_identity=input_identity,
        )
        return _json_response({"success": True, "data": result}, 202)
    except IndexBindingError as exc:
        return _json_response({"success": False, "error": str(exc), "error_code": exc.code}, 409)
    except (KeyError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/rag-runs/<run_id>", methods=["GET"])
def api_isolated_rag_state(run_id):
    """读取 staged index RAG 测试任务状态。"""
    return _deprecated_run_response(_run_state_response(run_id), run_id)


@rag_eval_bp.route("/isolated/rag-runs/<run_id>/result", methods=["GET"])
def api_isolated_rag_result(run_id):
    """读取已完成的隔离 RAG 查询结果。"""
    return _deprecated_run_response(_run_result_response(run_id), run_id)


@rag_eval_bp.route("/isolated/rag-runs/<run_id>/stream", methods=["GET"])
def api_isolated_rag_stream(run_id):
    """订阅 staged index RAG 测试事件。"""
    return _deprecated_run_response(_run_stream_response(run_id), run_id)


@rag_eval_bp.route("/isolated/rag-runs/<run_id>/cancel", methods=["POST"])
def api_cancel_isolated_rag(run_id):
    """请求取消 staged index RAG 测试任务。"""
    return _deprecated_run_response(_run_cancel_response(run_id), run_id)


# ---- 评测运行 ----

def _evaluation_dataset_from_payload(payload):
    """解析并校验评测题集；单次与并行批次共用同一严格绑定逻辑。"""
    dataset_source = str(payload.get("dataset_source") or "").strip()
    if dataset_source != "gold_v2":
        dataset, _ = _dataset_from_payload(payload, required=True)
        if not isinstance(dataset, dict):
            raise ValueError("eval_dataset must be an object")
        return dataset

    dataset, _ = _dataset_from_payload(payload, required=False)
    if dataset is not None:
        raise ValueError("dataset_source=gold_v2 and dataset input are mutually exclusive")

    from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import (
        DEFAULT_GOLD_V2_OUTPUT,
        validate_frozen_gold_binding,
    )


    from app.rag_eval.isolated_runs import _run_dir

    if not DEFAULT_GOLD_V2_OUTPUT.is_file():
        raise ValueError("本地 Gold 基准集尚未冻结")
    ingestion_run_id = str(payload.get("ingestion_run_id") or "")
    index_version = str(payload.get("index_version") or "")
    validate_frozen_gold_binding(
        DEFAULT_GOLD_V2_OUTPUT,
        index_dir=_run_dir(ingestion_run_id) / "indexes" / index_version,
    )
    return json.loads(DEFAULT_GOLD_V2_OUTPUT.read_text(encoding="utf-8"))


def _evaluation_options(payload):
    """返回一次实验的配置快照，并在创建任何 run 前完成结构校验。"""
    retrieval = payload.get("retrieval") or {}
    ragas = payload.get("ragas") or {}
    steps = payload.get("steps")
    if not isinstance(retrieval, dict) or not isinstance(ragas, dict):
        raise ValueError("retrieval and ragas must be objects")
    if steps is not None and not isinstance(steps, list):
        raise ValueError("steps must be a list")
    strategy_profile = payload.get("strategy_profile") or {}
    if not isinstance(strategy_profile, dict):
        raise ValueError("strategy_profile must be an object")
    return retrieval, ragas, steps, strategy_profile

@rag_eval_bp.route("/isolated/evaluation-runs", methods=["POST"])
def api_start_isolated_evaluation():
    """在显式 staged index 上启动 retrieval、Ragas 和报告流水线。"""
    try:
        payload = request.get_json(silent=True) or {}
        dataset = _evaluation_dataset_from_payload(payload)
        retrieval, ragas, steps, strategy_profile = _evaluation_options(payload)
        result = isolated_run_manager.start_evaluation(
            payload.get("ingestion_run_id", ""),
            payload.get("index_version", ""),
            dataset,
            retrieval_options=retrieval,
            ragas_options=ragas,
            strategy_profile=strategy_profile,
            steps=steps,
        )
        return _json_response({"success": True, "data": result}, 202)
    except IndexBindingError as exc:
        return _json_response({"success": False, "error": str(exc), "error_code": exc.code}, 409)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/evaluation-batches", methods=["POST"])
def api_start_isolated_evaluation_batch():
    """一次校验并入队 2-4 个完整策略实验，由多个 worker slot 并行领取。"""
    try:
        payload = request.get_json(silent=True) or {}
        experiments = payload.get("experiments")
        if not isinstance(experiments, list) or not 2 <= len(experiments) <= 4:
            raise ValueError("experiments must contain 2 to 4 evaluation configurations")
        if not all(isinstance(experiment, dict) for experiment in experiments):
            raise ValueError("each experiment must be an object")

        dataset = _evaluation_dataset_from_payload(payload)
        normalized = [_evaluation_options(experiment) for experiment in experiments]
        profile_ids = [str(options[3].get("profile_id") or "").strip() for options in normalized]
        if any(not profile_id for profile_id in profile_ids) or len(set(profile_ids)) != len(profile_ids):
            raise ValueError("parallel experiments require unique non-empty strategy profile ids")

        batch_id = f"eval_batch_{uuid.uuid4().hex[:12]}"
        runs = []
        try:
            for position, (retrieval, ragas, steps, strategy_profile) in enumerate(normalized, start=1):
                runs.append(isolated_run_manager.start_evaluation(
                    payload.get("ingestion_run_id", ""),
                    payload.get("index_version", ""),
                    dataset,
                    retrieval_options=retrieval,
                    ragas_options=ragas,
                    strategy_profile=strategy_profile,
                    steps=steps,
                    batch_id=batch_id,
                    batch_position=position,
                    batch_size=len(normalized),
                ))
        except Exception:
            # 批次中途入队失败时取消已经创建的兄弟 run，避免用户误以为仍是完整对照组。
            for run in runs:
                try:
                    isolated_run_manager.cancel(str(run.get("run_id") or ""))
                except Exception:
                    log_request_failure(logging.getLogger(__name__), exc_info=True)
            raise
        return _json_response({
            "success": True,
            "data": {"batch_id": batch_id, "run_count": len(runs), "runs": runs},
        }, 202)
    except IndexBindingError as exc:
        return _json_response({"success": False, "error": str(exc), "error_code": exc.code}, 409)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/evaluation-runs/<run_id>", methods=["GET"])
def api_isolated_evaluation_state(run_id):
    """读取隔离评测任务状态。"""
    return _deprecated_run_response(_run_state_response(run_id), run_id)


@rag_eval_bp.route("/isolated/evaluation-runs/<run_id>", methods=["DELETE"])
def api_delete_isolated_evaluation(run_id):
    """删除已结束或确认失活的隔离评测及其报告目录。"""
    try:
        payload = request.get_json(silent=True) or {}
        force = payload.get("force") is True
        result = isolated_run_manager.delete_evaluation_run(run_id, force=force)
        if result.get("status") == "running":
            return _json_response({"success": False, "error": result.get("message") or "run is still running", "data": result}, 409)
        return _json_response({"success": True, "data": result})
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/evaluation-history", methods=["GET"])
def api_isolated_evaluation_history():
    """列出隔离评测摘要，供趋势和运行选择使用。"""
    try:
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page_size", default=50, type=int)
        return _json_response({
            "success": True,
            "data": isolated_run_manager.list_evaluation_history(
                dataset_id=request.args.get("dataset_id") or None,
                index_version=request.args.get("index_version") or None,
                status=request.args.get("status") or None,
                source_name=request.args.get("source_name") or None,
                since=request.args.get("since") or None,
                until=request.args.get("until") or None,
                page=page,
                page_size=page_size,
            ),
        })
    except (KeyError, ValueError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/evaluation-diff", methods=["GET"])
def api_isolated_evaluation_diff():
    """比较两个隔离评测运行，要求题集 identity 完全一致。"""
    try:
        base_run_id = request.args.get("base_run_id", "")
        candidate_run_id = request.args.get("candidate_run_id", "")
        if not base_run_id or not candidate_run_id:
            raise ValueError("base_run_id and candidate_run_id are required")
        return _json_response({
            "success": True,
            "data": isolated_run_manager.get_evaluation_diff(base_run_id, candidate_run_id),
        })
    except KeyError as exc:
        return _json_response({"success": False, "error": str(exc)}, 404)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 409)
    except Exception as exc:
        log_request_failure(logging.getLogger(__name__), exc_info=True)
        return _json_response({"success": False, "error": str(exc)}, 500)


@rag_eval_bp.route("/isolated/evaluation-runs/<run_id>/result", methods=["GET"])
def api_isolated_evaluation_result(run_id):
    """读取已完成隔离评测的汇总结果。"""
    return _deprecated_run_response(_run_result_response(run_id), run_id)


@rag_eval_bp.route("/isolated/evaluation-runs/<run_id>/artifacts/<path:artifact_name>", methods=["GET"])
def api_isolated_evaluation_artifact(run_id, artifact_name):
    """读取本次评测目录中的 JSON 或 Markdown 产物。"""
    return _deprecated_run_response(_run_artifact_response(run_id, artifact_name), run_id)


@rag_eval_bp.route("/isolated/evaluation-runs/<run_id>/stream", methods=["GET"])
def api_isolated_evaluation_stream(run_id):
    """订阅隔离评测事件。"""
    return _deprecated_run_response(_run_stream_response(run_id), run_id)


@rag_eval_bp.route("/isolated/evaluation-runs/<run_id>/cancel", methods=["POST"])
def api_cancel_isolated_evaluation(run_id):
    """请求取消隔离评测任务。"""
    return _deprecated_run_response(_run_cancel_response(run_id), run_id)
