"""公开预览统计事件入口。

这个接口只把匿名浏览器上报的固定交互事件转成受管运行日志。它不创建用户、
Session、消息、文件或分析 Job，也不读写任何业务表，因此是不参与核心事务的
旁路能力：前端不等待它完成，日志写入失败也不会改变页面或登录行为。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from typing import Any, Callable, Mapping

from flask import Blueprint, jsonify, request

from config.settings import settings
from observability.event_catalog import ANALYTICS_DEMO_KEYS, ANALYTICS_PAGE_IDS
from observability.logging_runtime import log_event


analytics_bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")
LOGGER = logging.getLogger(__name__)

MAX_EVENT_COUNT = 10
MAX_BODY_BYTES = 8 * 1024
# 目的标签让同一个密钥在不同用途下产生互不相干的摘要值。
VISITOR_HASH_PURPOSE = b"causalagent.analytics.visitor:"
VISITOR_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
REQUEST_FIELDS = frozenset({"visitor_id", "events"})

_Emitter = Callable[[str, str, "str | None"], None]


class _AnalyticsRequestRejected(Exception):
    """统计请求不合法时使用的内部拒绝信号，不进入任何业务控制流。"""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _emit_public_preview_view(visitor_hash: str, page: str, demo_key: str | None) -> None:
    log_event(
        LOGGER,
        "analytics.public_preview.view",
        details={"visitor_hash": visitor_hash, "page": page, "demo_key": demo_key},
    )


def _emit_public_preview_demo_open(visitor_hash: str, page: str, demo_key: str | None) -> None:
    log_event(
        LOGGER,
        "analytics.public_preview.demo_open",
        details={"visitor_hash": visitor_hash, "page": page, "demo_key": demo_key},
    )


def _emit_public_preview_send_click(visitor_hash: str, page: str, demo_key: str | None) -> None:
    log_event(
        LOGGER,
        "analytics.public_preview.send_click",
        details={"visitor_hash": visitor_hash, "page": page, "demo_key": demo_key},
    )


def _emit_auth_panel_open(visitor_hash: str, page: str, demo_key: str | None) -> None:
    # 登录面板与示例无关，详情里不含 demo_key。
    log_event(
        LOGGER,
        "analytics.auth.panel_open",
        details={"visitor_hash": visitor_hash, "page": page},
    )


# 客户端可上报的事件、各自允许的字段和写入函数；登录成功事件由认证路由自己记录，
# 因此不出现在这里。
_CLIENT_EVENTS: Mapping[str, tuple[frozenset[str], _Emitter]] = {
    "analytics.public_preview.view": (
        frozenset({"event", "page", "demo_key"}),
        _emit_public_preview_view,
    ),
    "analytics.public_preview.demo_open": (
        frozenset({"event", "page", "demo_key"}),
        _emit_public_preview_demo_open,
    ),
    "analytics.public_preview.send_click": (
        frozenset({"event", "page", "demo_key"}),
        _emit_public_preview_send_click,
    ),
    "analytics.auth.panel_open": (
        frozenset({"event", "page"}),
        _emit_auth_panel_open,
    ),
}


def _visitor_hash(visitor_id: str) -> str:
    """把可重置的匿名浏览器标识单向映射成稳定的日志关联字段。"""

    secret = str(settings.SECRET_KEY).encode("utf-8")
    return hmac.new(
        secret,
        VISITOR_HASH_PURPOSE + visitor_id.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _validated_event(event: Any) -> tuple[str, str, str | None]:
    if not isinstance(event, dict):
        raise _AnalyticsRequestRejected("invalid_event", "每个事件必须是 JSON 对象。")
    name = event.get("event")
    handler = _CLIENT_EVENTS.get(name) if isinstance(name, str) else None
    if handler is None:
        raise _AnalyticsRequestRejected("unknown_event", "事件名称不在公开统计白名单内。")
    allowed_fields, _emitter = handler
    keys = set(event)
    if keys - allowed_fields:
        raise _AnalyticsRequestRejected("unknown_field", "事件包含未登记的字段。")
    if allowed_fields - keys:
        raise _AnalyticsRequestRejected("missing_field", "事件缺少必需字段。")
    page = event["page"]
    if not isinstance(page, str) or page not in ANALYTICS_PAGE_IDS:
        raise _AnalyticsRequestRejected("invalid_page", "page 不是已登记的页面标识。")
    demo_key = event.get("demo_key")
    if demo_key is not None and (
        not isinstance(demo_key, str) or demo_key not in ANALYTICS_DEMO_KEYS
    ):
        raise _AnalyticsRequestRejected("invalid_demo_key", "demo_key 不是已登记的示例标识。")
    return str(name), page, demo_key


def _validated_request(raw: bytes) -> tuple[str, list[tuple[str, str, str | None]]]:
    """校验整批事件；任何一项不合法都整体拒绝，不产生部分日志。"""

    if len(raw) > MAX_BODY_BYTES:
        raise _AnalyticsRequestRejected(
            "payload_too_large",
            "统计请求体超过上限。",
            413,
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _AnalyticsRequestRejected("invalid_json", "统计请求体必须是 JSON 对象。") from error
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise _AnalyticsRequestRejected(
            "invalid_request",
            "统计请求只接受 visitor_id 和 events 两个字段。",
        )
    visitor_id = payload["visitor_id"]
    if not isinstance(visitor_id, str) or VISITOR_ID_PATTERN.fullmatch(visitor_id) is None:
        raise _AnalyticsRequestRejected("invalid_visitor_id", "visitor_id 必须是 UUID。")
    events = payload["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENT_COUNT:
        raise _AnalyticsRequestRejected(
            "invalid_events",
            f"events 必须是 1 到 {MAX_EVENT_COUNT} 个事件的数组。",
        )
    return visitor_id, [_validated_event(event) for event in events]


@analytics_bp.route("/events", methods=["POST"])
def record_public_events():
    """接收未登录用户的公开预览事件，只返回已接受数量。"""

    try:
        visitor_id, events = _validated_request(request.get_data(cache=True))
    except _AnalyticsRequestRejected as rejection:
        return jsonify({
            "success": False,
            "error": rejection.message,
            "code": rejection.code,
        }), rejection.status

    visitor_hash = _visitor_hash(visitor_id)
    for event_name, page, demo_key in events:
        _CLIENT_EVENTS[event_name][1](visitor_hash, page, demo_key)
    return jsonify({"success": True, "accepted": len(events)}), 202


__all__ = ["analytics_bp"]
