"""把 Agent 最终状态转换为聊天与 SSE 共用的公开结果。"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage

from Agent.causal_agent.web_search_node import WEB_SEARCH_MAX_RESULTS
from Agent.Report.document import ReportDocument
from observability.logging_runtime import log_context, log_event


LOGGER = logging.getLogger(__name__)


def _extract_references(web_search_result: Any) -> list[dict]:
    """从联网搜索结果投影引用（仅 title + url），数量与注入报告的一致。"""
    if not web_search_result or not web_search_result.get("success"):
        return []
    return [
        {"title": c.get("title", ""), "url": c.get("url", "")}
        for c in web_search_result.get("content", [])[:WEB_SEARCH_MAX_RESULTS]
    ]


def _report_document_payload(document: Any) -> dict[str, Any] | None:
    """把报告文档模型或等价 JSON 转成公开的 structured 报告载荷。"""
    if isinstance(document, ReportDocument):
        payload = document.model_dump(mode="json")
    elif isinstance(document, dict):
        payload = document
    else:
        return None
    if not isinstance(payload, dict) or "report_id" not in payload:
        return None
    return {
        "type": "report",
        "layout": "report",
        "render_mode": "structured",
        "document": payload,
    }


def process_final_result(final_state_data: dict[str, Any]) -> dict[str, Any]:
    """按消息和结构化报告文档优先级生成稳定的最终响应。"""
    messages = final_state_data.get("messages", [])
    if messages:
        last_message = messages[-1]
        if isinstance(last_message, AIMessage):
            message_name = getattr(last_message, "name", None)
            if message_name in {"normal_chat", "inquiry_answer"}:
                return {"type": "text", "summary": last_message.content}

    result = _report_document_payload(final_state_data.get("report_document"))
    if result is not None:
        finalization_status = final_state_data.get("finalization_status")
        if finalization_status in {"valid", "degraded"}:
            result["finalization_status"] = finalization_status
        references = _extract_references(final_state_data.get("web_search_result"))
        if references:
            result["references"] = references
        return result

    with log_context(node="result_presenter"):
        log_event(
            LOGGER,
            "job.node.degraded",
            details={
                "failure_kind": "missing_result",
                "final_attempt": 1,
                "fallback": "default_message",
            },
        )
    return {"type": "text", "summary": "抱歉，我在处理时遇到了问题。"}
