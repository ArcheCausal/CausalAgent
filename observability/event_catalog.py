"""受管运行日志事件目录。

事件目录是运行日志级别、分类、固定消息和 ``details`` 白名单的唯一来源。
调用方不得在事件码之外自行决定这些属性。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


_STABLE_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_CONTEXT_FIELDS = frozenset(
    {
        "request_id",
        "user_id",
        "session_id",
        "job_id",
        "invocation_id",
        "worker_slot",
        "node",
        "tool",
        "instance",
    }
)

CONTRACT_VIOLATIONS = frozenset(
    {
        "unknown_event",
        "details_not_mapping",
        "unknown_detail",
        "context_in_details",
        "invalid_detail_type",
        "invalid_detail_value",
        "detail_too_large",
        "invalid_context_field",
        "invalid_context_value",
        "context_reset_failed",
    }
)

REASON_CODES = frozenset(
    {
        "account_disabled",
        "account_missing",
        "audit_write_failed",
        "auth_failed",
        "auth_version_changed",
        "canceled",
        "checkpoint_unavailable",
        "claim_failed",
        "cleanup_failed",
        "collection_failed",
        "config_read_failed",
        "connection_unavailable",
        "connection",
        "control_capacity_timeout",
        "csrf_invalid",
        "csrf_missing",
        "cache_write_failed",
        "database_missing",
        "database_unavailable",
        "active_release_missing",
        "active_release_invalid",
        "active_release_check_failed",
        "rag_dependency_missing",
        "fenced",
        "heartbeat_failed",
        "initialization_failed",
        "internal_error",
        "invalid_json",
        "invalid_schema",
        "invalid_result",
        "invalid_runtime_context",
        "invalid_response",
        "knowledge_base_missing",
        "lock_operation_failed",
        "lock_release_failed",
        "migration_precondition_failed",
        "node_error",
        "node_timeout",
        "ownership_mismatch",
        "persist_failed",
        "pool_capacity_below_snapshot_count",
        "pool_exhausted",
        "postprocess_failed",
        "protocol_error",
        "query_timeout",
        "quota_billing",
        "quota_billing_circuit_open",
        "rate_limited",
        "reauthentication_failed",
        "readiness_failed",
        "remote_image_failed",
        "replica_lag",
        "replica_status_unavailable",
        "replica_unhealthy",
        "response_timeout",
        "retry_exhausted",
        "runtime_failed",
        "snapshot_publish_failed",
        "table_recovery_asset_missing",
        "table_recovery_bbox_missing",
        "table_recovery_failed",
        "server_error",
        "timeout",
        "http_400",
        "http_401",
        "http_403",
        "http_404",
        "http_422",
        "tool_error",
        "transport_error",
        "unexpected_error",
        "unavailable",
        "waiting_input",
        "worker_shutdown",
    }
)


@dataclass(frozen=True)
class DetailRule:
    """一个受管详情字段的值约束。"""

    types: tuple[type, ...]
    choices: frozenset[Any] | None = None
    pattern: re.Pattern[str] | None = None
    max_bytes: int = 256
    minimum: float | None = None
    maximum: float | None = None
    max_items: int = 16


@dataclass(frozen=True)
class EventSpec:
    """稳定事件合同。"""

    event_code: str
    level: int
    category: str
    message: str
    details: Mapping[str, DetailRule]


TOKEN = DetailRule((str,), pattern=_STABLE_TOKEN, max_bytes=128)
TEXT = DetailRule((str,), max_bytes=256)
MAX_SAFE_NUMBER = 9_223_372_036_854_775_807
COUNT = DetailRule((int,), minimum=0, maximum=MAX_SAFE_NUMBER)
POSITIVE_COUNT = DetailRule((int,), minimum=1, maximum=MAX_SAFE_NUMBER)
DURATION = DetailRule((int, float), minimum=0, maximum=MAX_SAFE_NUMBER)
BOOLEAN = DetailRule((bool,))
REASON = DetailRule((str,), choices=REASON_CODES, max_bytes=64)
METHOD = DetailRule(
    (str,),
    choices=frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}),
    max_bytes=16,
)
STATUS_CODE = DetailRule((int,), minimum=100, maximum=599)
PHASES = DetailRule((list, tuple), max_items=16, max_bytes=1024)
SHA256 = DetailRule((str,), pattern=re.compile(r"^[a-f0-9]{64}$"), max_bytes=64)
STRUCTURED_LOCATION = DetailRule(
    (str,),
    pattern=re.compile(r"^[A-Za-z0-9_.-]{1,64}$"),
    max_bytes=64,
)
JOB_STATUS = DetailRule(
    (str,),
    choices=frozenset({
        "queued",
        "running",
        "waiting_input",
        "succeeded",
        "failed",
        "canceled",
        "missing",
        "unknown",
    }),
    max_bytes=32,
)
EXECUTION_STATE = DetailRule(
    (str,),
    choices=frozenset({"leased", "draining", "unknown"}),
    max_bytes=32,
)
CLAIM_KIND = DetailRule(
    (str,),
    choices=frozenset({"initial", "user_resume", "stale_recovery"}),
    max_bytes=32,
)
JOB_OUTCOME = DetailRule(
    (str,),
    choices=frozenset({"final_result", "implicit_completion"}),
    max_bytes=32,
)
RAG_STATUS = DetailRule(
    (str,),
    choices=frozenset({"unavailable", "protocol_error"}),
    max_bytes=32,
)
RESULT_KIND = DetailRule(
    (str,),
    choices=frozenset({"structured_result", "other_result"}),
    max_bytes=32,
)
ERROR_CATEGORY = DetailRule(
    (str,),
    choices=frozenset(
        {
            "provider_error",
            "protocol_error",
            "checkpoint_error",
            "runtime_contract_error",
            "internal_error",
        }
    ),
    max_bytes=32,
)

# 公开预览统计的固定取值：页面标识和示例标识由前端静态数据与后端接口共同使用，
# 目录是唯一来源，客户端只能上报这里登记过的值。
ANALYTICS_PAGE_IDS = frozenset({"home"})
ANALYTICS_DEMO_KEYS = frozenset({"overview", "report", "graph"})
PAGE_ID = DetailRule((str,), choices=ANALYTICS_PAGE_IDS, max_bytes=32)
DEMO_KEY = DetailRule((str,), choices=ANALYTICS_DEMO_KEYS, max_bytes=32)


def _details(**rules: DetailRule) -> Mapping[str, DetailRule]:
    return MappingProxyType(dict(rules))


def _spec(
    level: int,
    category: str,
    message: str,
    **rules: DetailRule,
) -> EventSpec:
    # event_code 在目录冻结时由映射键回填，避免调用处重复写两份易漂移的值。
    return EventSpec("", level, category, message, _details(**rules))


_events: dict[str, EventSpec] = {
    "logging.serialization_failed": _spec(
        logging.ERROR,
        "dependency",
        "日志记录序列化失败",
    ),
    "logging.contract_invalid": _spec(
        logging.ERROR,
        "dependency",
        "日志事件合同无效",
        violation=DetailRule((str,), choices=CONTRACT_VIOLATIONS, max_bytes=64),
    ),
    "worker.slot.ready": _spec(
        logging.INFO,
        "lifecycle",
        "Worker slot 已就绪",
        tool_count=COUNT,
    ),
    "worker.slot.failed": _spec(
        logging.CRITICAL,
        "dependency",
        "Worker slot 初始化或运行失败",
        phase=TOKEN,
        reason_code=REASON,
    ),
    "web.request.unhandled": _spec(
        logging.ERROR,
        "request",
        "请求发生未处理异常",
        method=METHOD,
        endpoint=TOKEN,
        route=TEXT,
    ),
    "web.request.failed": _spec(
        logging.ERROR,
        "request",
        "请求在受控边界失败",
        method=METHOD,
        endpoint=TOKEN,
        status_code=STATUS_CODE,
        reason_code=REASON,
    ),
    "job.create.accepted": _spec(
        logging.INFO,
        "request",
        "分析任务已创建",
        status=JOB_STATUS,
    ),
    "job.create.replayed": _spec(
        logging.INFO,
        "request",
        "分析任务幂等重放已接受",
        status=JOB_STATUS,
    ),
    "job.create.failed": _spec(
        logging.ERROR,
        "request",
        "分析任务创建失败",
        reason_code=REASON,
    ),
    "admin.audit.write_failed": _spec(
        logging.ERROR,
        "dependency",
        "管理员审计事件写入失败",
        action=TOKEN,
        reason_code=REASON,
    ),
    "security.login.disabled_account": _spec(
        logging.WARNING,
        "security",
        "禁用账号尝试登录",
    ),
    "auth.login.last_login_update_failed": _spec(
        logging.WARNING,
        "dependency",
        "登录后的最后登录时间记录失败",
        reason_code=REASON,
    ),
    "security.authorization.denied": _spec(
        logging.WARNING,
        "security",
        "已验证用户尝试跨归属访问资源",
        resource_type=TOKEN,
        action=TOKEN,
        reason_code=REASON,
    ),
    "security.csrf.rejected": _spec(
        logging.WARNING,
        "security",
        "CSRF 校验拒绝请求",
        method=METHOD,
        endpoint=TOKEN,
        reason_code=REASON,
    ),
    "security.reauthentication.failed": _spec(
        logging.WARNING,
        "security",
        "高风险管理员操作重新认证失败",
        action=TOKEN,
        reason_code=REASON,
    ),
    "security.session.revoked": _spec(
        logging.WARNING,
        "security",
        "失效安全会话已撤销",
        reason_code=REASON,
    ),
    "db.connection.failed": _spec(
        logging.ERROR,
        "dependency",
        "数据库连接失败",
        source_alias=TOKEN,
        operation=TOKEN,
        reason_code=REASON,
        suppressed_count=COUNT,
    ),
    "db.replica.fallback": _spec(
        logging.WARNING,
        "dependency",
        "数据库读取已回退主库",
        source_alias=TOKEN,
        reason_code=REASON,
        lag_seconds=DURATION,
        suppressed_count=COUNT,
    ),
    "db.replica.recovered": _spec(
        logging.INFO,
        "dependency",
        "数据库副本读取已恢复",
        source_alias=TOKEN,
        downtime_ms=DURATION,
        failure_count=POSITIVE_COUNT,
    ),
    "db.query.slow": _spec(
        logging.WARNING,
        "dependency",
        "数据库查询超过慢查询阈值",
        operation=TOKEN,
        duration_ms=DURATION,
        statement_digest=SHA256,
        suppressed_count=COUNT,
    ),
    "worker.job.claimed": _spec(
        logging.INFO,
        "lifecycle",
        "Worker 已领取分析任务",
        claim_kind=CLAIM_KIND,
        attempt=POSITIVE_COUNT,
        lease_epoch=COUNT,
    ),
    "worker.job.finished": _spec(
        logging.INFO,
        "lifecycle",
        "分析任务执行完成",
        attempt=POSITIVE_COUNT,
        duration_ms=DURATION,
        outcome=JOB_OUTCOME,
    ),
    "worker.job.interrupted": _spec(
        logging.INFO,
        "lifecycle",
        "分析任务已暂停并等待输入",
        attempt=POSITIVE_COUNT,
        duration_ms=DURATION,
        reason_code=REASON,
    ),
    "worker.job.revoked": _spec(
        logging.INFO,
        "lifecycle",
        "分析任务执行资格已撤销",
        reason_code=REASON,
        status=JOB_STATUS,
        execution_state=EXECUTION_STATE,
    ),
    "worker.job.failed": _spec(
        logging.ERROR,
        "lifecycle",
        "分析任务执行失败",
        failure_phase=TOKEN,
        reason_code=REASON,
        error_category=ERROR_CATEGORY,
        attempt=POSITIVE_COUNT,
        duration_ms=DURATION,
    ),
    "worker.job.cleanup_failed": _spec(
        logging.ERROR,
        "dependency",
        "分析任务执行资源清理失败",
        failure_count=POSITIVE_COUNT,
        phases=PHASES,
    ),
    "worker.lease.refresh_failed": _spec(
        logging.WARNING,
        "dependency",
        "Worker lease 刷新失败",
        consecutive_failures=POSITIVE_COUNT,
        suppressed_count=COUNT,
    ),
    "worker.lease.recovered": _spec(
        logging.INFO,
        "dependency",
        "Worker lease 刷新已恢复",
        failure_count=POSITIVE_COUNT,
        downtime_ms=DURATION,
    ),
    "job.node.timeout": _spec(
        logging.WARNING,
        "lifecycle",
        "Agent 节点最终超时并进入降级路径",
        final_attempt=POSITIVE_COUNT,
        timeout_ms=DURATION,
        fallback=TOKEN,
    ),
    "job.node.degraded": _spec(
        logging.WARNING,
        "lifecycle",
        "Agent 节点最终失败并进入降级路径",
        failure_kind=TOKEN,
        final_attempt=POSITIVE_COUNT,
        fallback=TOKEN,
        cause_code=TOKEN,
        schema_name=TEXT,
        structured_attempts=POSITIVE_COUNT,
        validation_error_count=COUNT,
        validation_first_type=TOKEN,
        validation_first_loc=STRUCTURED_LOCATION,
    ),
    "job.postprocess.degraded": _spec(
        logging.WARNING,
        "lifecycle",
        "因果分析后处理已降级",
        reason_code=REASON,
        affected_count=COUNT,
    ),
    "chat.attachment.degraded": _spec(
        logging.WARNING,
        "dependency",
        "聊天附件解析已降级",
        attachment_type=TOKEN,
        reason_code=REASON,
    ),
    "rag.runtime.ready": _spec(
        logging.INFO,
        "lifecycle",
        "RAG Runtime 初始化完成",
        provider=TEXT,
        model=TEXT,
        chunk_count=POSITIVE_COUNT,
        elapsed_ms=DURATION,
        release_id=TEXT,
    ),
    "rag.sparse.ready": _spec(
        logging.INFO,
        "lifecycle",
        "RAG sparse 索引构建完成",
        version=TEXT,
        documents=COUNT,
        vocabulary=COUNT,
        backend=TEXT,
        elapsed_ms=DURATION,
    ),
    "rag.startup.unavailable": _spec(
        logging.WARNING,
        "dependency",
        "RAG 知识库启动检查不可用",
        reason_code=REASON,
    ),
    "rag.enrichment.degraded": _spec(
        logging.WARNING,
        "dependency",
        "RAG 增强结果已降级",
        status=RAG_STATUS,
        reason_code=REASON,
        question_count=COUNT,
        evidence_count=COUNT,
    ),
    "rag.multimodal.parse_failed": _spec(
        logging.ERROR,
        "dependency",
        "多模态 RAG 解析失败",
        phase=TOKEN,
        reason_code=REASON,
        source_alias=TOKEN,
        page_number=COUNT,
        image_index=COUNT,
        table_index=COUNT,
        status_code=STATUS_CODE,
        fallback_attempted=BOOLEAN,
        circuit_breaker_open=BOOLEAN,
    ),
    "mcp.tool.finished": _spec(
        logging.INFO,
        "dependency",
        "MCP 工具调用完成",
        capability=TOKEN,
        retry_ordinal=COUNT,
        duration_ms=DURATION,
        input_bytes=COUNT,
        result_kind=RESULT_KIND,
    ),
    "mcp.tool.failed": _spec(
        logging.ERROR,
        "dependency",
        "MCP 工具调用失败",
        capability=TOKEN,
        retry_ordinal=COUNT,
        duration_ms=DURATION,
        input_bytes=COUNT,
        reason_code=REASON,
    ),
    "mcp.tool.canceled": _spec(
        logging.INFO,
        "dependency",
        "MCP 工具调用已取消",
        capability=TOKEN,
        retry_ordinal=COUNT,
        duration_ms=DURATION,
        reason_code=REASON,
    ),
    "mcp.tool.slow": _spec(
        logging.WARNING,
        "dependency",
        "MCP 工具调用持续时间过长",
        capability=TOKEN,
        duration_ms=DURATION,
        timeout_seconds=COUNT,
    ),
    "mcp.request.received": _spec(
        logging.INFO,
        "dependency",
        "MCP 请求已收到",
        capability=TOKEN,
        retry_ordinal=COUNT,
    ),
    "mcp.request.rejected": _spec(
        logging.WARNING,
        "dependency",
        "MCP 请求已拒绝",
        capability=TOKEN,
        reason_code=REASON,
    ),
    "mcp.request.accepted": _spec(
        logging.INFO,
        "dependency",
        "MCP 请求已接受",
        capability=TOKEN,
        retry_ordinal=COUNT,
        queue_wait_ms=DURATION,
        timeout_seconds=COUNT,
    ),
    "mcp.cancel.finished": _spec(
        logging.INFO,
        "dependency",
        "MCP 取消请求已处理",
        capability=TOKEN,
        cancellation_status=TOKEN,
    ),
    "mcp.client.call.started": _spec(
        logging.INFO,
        "dependency",
        "Worker 已发起 MCP 调用",
        capability=TOKEN,
        retry_ordinal=COUNT,
    ),
    "mcp.client.cancel.requested": _spec(
        logging.INFO,
        "dependency",
        "Worker 已请求取消 MCP 调用",
        capability=TOKEN,
        retry_ordinal=COUNT,
    ),
    "mcp.client.cancel.finished": _spec(
        logging.INFO,
        "dependency",
        "Worker MCP 取消请求已完成",
        capability=TOKEN,
        cancellation_status=TOKEN,
    ),
    "mcp.client.cancel.failed": _spec(
        logging.WARNING,
        "dependency",
        "Worker MCP 取消请求失败",
        capability=TOKEN,
        reason_code=REASON,
    ),
    "mcp.capacity.rejected": _spec(
        logging.WARNING,
        "dependency",
        "MCP 请求因容量限制被拒绝",
        reason_code=REASON,
        retry_after_seconds=COUNT,
    ),
    "mcp.process.recycled": _spec(
        logging.WARNING,
        "dependency",
        "MCP 算法进程池已回收",
        reason_code=REASON,
    ),
    "mcp.client.reconnected": _spec(
        logging.INFO,
        "dependency",
        "MCP 客户端成员已重连",
        generation=COUNT,
        pool_lane=TOKEN,
    ),
    "mcp.transport.failed": _spec(
        logging.WARNING,
        "dependency",
        "MCP transport 最终调用失败",
        reason_code=REASON,
        final_attempt=POSITIVE_COUNT,
        duration_ms=DURATION,
    ),
    "monitor.snapshot.failed": _spec(
        logging.ERROR,
        "dependency",
        "数据库监控快照采集失败",
        snapshot_key=TOKEN,
        reason_code=REASON,
        duration_ms=DURATION,
        suppressed_count=COUNT,
    ),
    "monitor.snapshot.recovered": _spec(
        logging.INFO,
        "dependency",
        "数据库监控快照采集已恢复",
        snapshot_key=TOKEN,
        downtime_ms=DURATION,
        failure_count=POSITIVE_COUNT,
    ),
    "monitor.config.degraded": _spec(
        logging.WARNING,
        "dependency",
        "数据库监控配置已降级",
        reason_code=REASON,
        suppressed_count=COUNT,
    ),
    "monitor.config.recovered": _spec(
        logging.INFO,
        "dependency",
        "数据库监控配置已恢复",
        downtime_ms=DURATION,
        failure_count=POSITIVE_COUNT,
    ),
    "monitor.lock.failed": _spec(
        logging.WARNING,
        "dependency",
        "数据库监控命名锁操作失败",
        snapshot_key=TOKEN,
        reason_code=REASON,
        suppressed_count=COUNT,
    ),
    "monitor.lock.recovered": _spec(
        logging.INFO,
        "dependency",
        "数据库监控命名锁操作已恢复",
        snapshot_key=TOKEN,
        downtime_ms=DURATION,
        failure_count=POSITIVE_COUNT,
    ),
    "agent.persistence.cleanup.succeeded": _spec(
        logging.INFO,
        "lifecycle",
        "Agent 持久化清理已完成",
        task_type=TOKEN,
        outbox_id=POSITIVE_COUNT,
        attempt=POSITIVE_COUNT,
        duration_ms=DURATION,
        deleted_count=COUNT,
    ),
    "agent.persistence.cleanup.failed": _spec(
        logging.ERROR,
        "dependency",
        "Agent 持久化清理执行失败",
        task_type=TOKEN,
        outbox_id=POSITIVE_COUNT,
        attempt=POSITIVE_COUNT,
        duration_ms=DURATION,
        reason_code=REASON,
    ),
    "agent.persistence.cleanup.runtime.degraded": _spec(
        logging.WARNING,
        "dependency",
        "Agent 持久化清理运行循环已降级",
        reason_code=REASON,
        suppressed_count=COUNT,
    ),
    "agent.persistence.cleanup.runtime.recovered": _spec(
        logging.INFO,
        "dependency",
        "Agent 持久化清理运行循环已恢复",
        downtime_ms=DURATION,
        failure_count=POSITIVE_COUNT,
    ),
    "analytics.public_preview.view": _spec(
        logging.INFO,
        "request",
        "公开预览已展示",
        visitor_hash=SHA256,
        page=PAGE_ID,
        demo_key=DEMO_KEY,
    ),
    "analytics.public_preview.demo_open": _spec(
        logging.INFO,
        "request",
        "公开预览示例已打开",
        visitor_hash=SHA256,
        page=PAGE_ID,
        demo_key=DEMO_KEY,
    ),
    "analytics.public_preview.send_click": _spec(
        logging.INFO,
        "request",
        "未登录访客点击发送",
        visitor_hash=SHA256,
        page=PAGE_ID,
        demo_key=DEMO_KEY,
    ),
    "analytics.auth.panel_open": _spec(
        logging.INFO,
        "request",
        "登录面板已打开",
        visitor_hash=SHA256,
        page=PAGE_ID,
    ),
    "analytics.auth.login_success": _spec(
        logging.INFO,
        "request",
        "用户登录成功",
    ),
}

for _service in ("web", "worker", "monitor", "mcp", "maintenance"):
    _events[f"{_service}.startup.ready"] = _spec(
        logging.INFO,
        "lifecycle",
        f"{_service} 进程启动检查完成",
    )
    _events[f"{_service}.startup.failed"] = _spec(
        logging.CRITICAL,
        "dependency",
        f"{_service} 进程启动失败",
        phase=TOKEN,
        dependency=TOKEN,
        reason_code=REASON,
    )

EVENT_SPECS: Mapping[str, EventSpec] = MappingProxyType(
    {
        event_code: EventSpec(
            event_code,
            spec.level,
            spec.category,
            spec.message,
            spec.details,
        )
        for event_code, spec in _events.items()
    }
)


def _is_expected_type(value: Any, rule: DetailRule) -> bool:
    if isinstance(value, bool) and bool not in rule.types:
        return False
    return isinstance(value, rule.types)


def _validate_value(value: Any, rule: DetailRule) -> str | None:
    if not _is_expected_type(value, rule):
        return "invalid_detail_type"
    if isinstance(value, float) and not math.isfinite(value):
        return "invalid_detail_value"
    if rule.choices is not None and value not in rule.choices:
        return "invalid_detail_value"
    if isinstance(value, str):
        if rule.pattern is not None and rule.pattern.fullmatch(value) is None:
            return "invalid_detail_value"
        if len(value.encode("utf-8")) > rule.max_bytes:
            return "detail_too_large"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if rule.minimum is not None and value < rule.minimum:
            return "invalid_detail_value"
        if rule.maximum is not None and value > rule.maximum:
            return "invalid_detail_value"
    if isinstance(value, (list, tuple)):
        if len(value) > rule.max_items:
            return "detail_too_large"
        if not all(isinstance(item, str) and _STABLE_TOKEN.fullmatch(item) for item in value):
            return "invalid_detail_value"
        if len("|".join(value).encode("utf-8")) > rule.max_bytes:
            return "detail_too_large"
    return None


def validate_event_details(
    event_code: str,
    details: Mapping[str, Any] | None,
) -> tuple[EventSpec | None, dict[str, Any] | None, str | None]:
    """返回事件合同、安全详情和可空违规码，不回显违规内容。"""

    spec = EVENT_SPECS.get(event_code)
    if spec is None:
        return None, None, "unknown_event"
    if details is None:
        return spec, None, None
    if not isinstance(details, Mapping):
        return spec, None, "details_not_mapping"

    safe: dict[str, Any] = {}
    for key, value in details.items():
        if key in _CONTEXT_FIELDS:
            return spec, None, "context_in_details"
        rule = spec.details.get(key)
        if rule is None:
            return spec, None, "unknown_detail"
        if value is None:
            continue
        violation = _validate_value(value, rule)
        if violation is not None:
            return spec, None, violation
        safe[key] = list(value) if isinstance(value, tuple) else value
    return spec, safe or None, None


__all__ = [
    "ANALYTICS_DEMO_KEYS",
    "ANALYTICS_PAGE_IDS",
    "CONTRACT_VIOLATIONS",
    "DetailRule",
    "EVENT_SPECS",
    "EventSpec",
    "REASON_CODES",
    "validate_event_details",
]
