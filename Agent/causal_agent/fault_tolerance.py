"""
LangGraph 节点级容错策略。

本模块只定义图运行时策略和错误恢复状态，不直接写数据库、不直接操作 SSE。
worker 仍负责把 graph 产出的终态或异常转换为 analysis_job_events。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Literal

from langchain_core.messages import AIMessage
from langgraph.errors import NodeError
from langgraph.types import Command, RetryPolicy, TimeoutPolicy, default_retry_on

from Agent.Report.document import build_degraded_report_document
from .state import CausalAgentState, RagSubgraphState
from app.agent.worker.execution_guard import (
    JobExecutionRevoked,
    current_execution_guard,
    raise_if_execution_revoked,
)
from observability.logging_runtime import log_event


LOGGER = logging.getLogger(__name__)


def retry_transient_errors(exc: BaseException) -> bool:
    """
    判断异常是否适合交给 LangGraph 进行节点级重试。

    默认策略已排除常见编程错误，并会重试 NodeTimeoutError 以及常见 5xx/网络类异常；
    这里单独排除 LangGraph interrupt 相关异常，避免“需要用户输入”的业务中断被当作故障重试。
    """
    if isinstance(exc, (JobExecutionRevoked, asyncio.CancelledError)):
        return False
    guard = current_execution_guard()
    if guard is not None and guard.revoked:
        if default_retry_on(exc):
            raise JobExecutionRevoked("执行已撤销，停止瞬态错误重试")
        return False
    exc_name = exc.__class__.__name__.lower()
    if "interrupt" in exc_name:
        return False
    return default_retry_on(exc)


def short_retry(max_attempts: int = 2) -> RetryPolicy:
    """生成短重试策略，适用于 LLM 路由、总结和普通回答节点。"""
    return RetryPolicy(
        max_attempts=max_attempts,
        initial_interval=0.5,
        backoff_factor=2.0,
        max_interval=4.0,
        retry_on=retry_transient_errors,
    )


def tool_retry(max_attempts: int = 3) -> RetryPolicy:
    """生成工具调用重试策略，适用于 MCP/RAG 等外部依赖节点。"""
    return RetryPolicy(
        max_attempts=max_attempts,
        initial_interval=1.0,
        backoff_factor=2.0,
        max_interval=8.0,
        retry_on=retry_transient_errors,
    )


def timeout(run_timeout: float, idle_timeout: float | None = None) -> TimeoutPolicy:
    """生成节点 timeout 策略，run_timeout 是单次 attempt 的硬上限。"""
    return TimeoutPolicy(run_timeout=run_timeout, idle_timeout=idle_timeout)


def _error_message(error: NodeError) -> str:
    """生成不包含异常原文的节点失败摘要。"""
    raise_if_execution_revoked(getattr(error, "error", error))
    return f"{error.node} 节点执行失败"


def sanitize_error(exc: BaseException) -> str:
    """把异常归类为有限的公开错误，避免泄露路径、连接串或文件正文。"""
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    raise_if_execution_revoked(exc)
    normalized = str(exc).lower()
    if "timeout" in normalized:
        return "调用超时"
    if any(token in normalized for token in ("connection", "connect", "network")):
        return "服务连接失败"
    if any(token in normalized for token in ("permission", "auth")):
        return "服务授权失败"
    if any(token in normalized for token in ("rate", "limit")):
        return "服务当前繁忙"
    return "节点执行失败"


RagStatus = Literal["available", "unavailable", "protocol_error"]
RAG_DEGRADATION_SUMMARY = "知识库增强暂不可用，报告将仅基于因果分析结果生成。"


def build_rag_degradation_result(
    error: Any = None,
    *,
    status: RagStatus = "unavailable",
) -> dict[str, Any]:
    """构造统一的 RAG 降级结果，并保留取消异常的控制流语义。"""
    if isinstance(error, asyncio.CancelledError):
        raise error
    if isinstance(error, BaseException):
        error_message = sanitize_error(error)
    elif error is None:
        error_message = "RAG 子图未生成有效结果。"
    else:
        error_message = str(error)
    return {
        "success": False,
        "status": status,
        "summary": RAG_DEGRADATION_SUMMARY,
        "questions": [],
        "evidence_count": 0,
        "error": error_message,
    }


def route_to_normal_chat(state: CausalAgentState, error: NodeError) -> Command:
    """Agent 路由失败后的保守恢复：进入普通问答兜底分支。"""
    return Command(
        update={
            "messages": [
                AIMessage(
                    content=f"决策：普通问答。路由节点降级原因：{_error_message(error)}",
                    name=error.node,
                )
            ],
            "route_decision": "normal_chat",
        },
        goto="normal_chat",
    )


def recover_fold_to_agent(state: CausalAgentState, error: NodeError) -> Command:
    """Fold 节点失败后的恢复：记录审计决策并回到 agent。"""
    return Command(
        update={
            "messages": [
                AIMessage(
                    content=f"决策：节点失败，返回 agent 重新判断。{_error_message(error)}",
                    name=error.node,
                )
            ],
            "tool_call_request": False,
            "fold_decision": "agent",
        },
        goto="agent",
    )


def recover_preprocess_to_agent(state: CausalAgentState, error: NodeError) -> Command:
    """Preprocess 节点失败后的恢复：回到 agent，但不伪造 fold 决策。"""
    return Command(
        update={
            "messages": [
                AIMessage(
                    content=f"决策：预处理失败，返回 agent 重新判断。{_error_message(error)}",
                    name=error.node,
                )
            ],
            "tool_call_request": False,
        },
        goto="agent",
    )


def recover_tools_to_agent(state: CausalAgentState, error: NodeError) -> Command:
    """工具节点失败后的恢复：保留结构化失败结果并回到 agent。"""
    message = _error_message(error)
    return Command(
        update={
            "messages": [
                AIMessage(content=f"决策：工具执行失败：{message}", name=error.node)
            ],
            "causal_analysis_result": {"success": False, "message": message},
            "knowledge_base_result": build_rag_degradation_result(message),
            "tool_call_request": False,
        },
        goto="agent",
    )


def recover_mcp_tool_failure(state: CausalAgentState, error: NodeError) -> dict:
    """MCP 子图节点失败后的恢复：写入标准失败结构，由父图决定后续路由。"""
    message = sanitize_error(error.error)
    return {
        "messages": [AIMessage(content=f"决策：MCP 工具执行失败：{message}", name=error.node)],
        "causal_analysis_result": {
            "success": False,
            "error": message,
            "error_type": type(error.error).__name__,
        },
        "tool_call_request": False,
    }


def _degrade_rag_subgraph_result(
    error: NodeError,
    *,
    status: RagStatus,
) -> Command:
    """把 RAG 子图节点异常转换成 Finalize 可消费的内部状态。"""
    result = build_rag_degradation_result(error.error, status=status)
    return Command(
        update={
            "rag_route": "finish",
            "rag_status": status,
            "rag_parse_result": result,
        },
        goto="rag_finalize",
    )


def degrade_rag_tool_result(state: RagSubgraphState, error: NodeError) -> Command:
    """RAG planner/ToolNode 失败后的降级：统一结束到 Finalize。"""
    return _degrade_rag_subgraph_result(error, status="unavailable")


def degrade_rag_parser_failure(state: RagSubgraphState, error: NodeError) -> Command:
    """RAG Parser 异常后的协议降级：仍然进入 Finalize。"""
    return _degrade_rag_subgraph_result(error, status="protocol_error")


def degrade_rag_finalize_failure(state: RagSubgraphState, error: NodeError) -> dict:
    """RAG Finalize 异常的最后一道内部兜底。"""
    return {
        "rag_output": build_rag_degradation_result(
            error.error,
            status="protocol_error",
        )
    }


def degrade_rag_adapter_result(state: CausalAgentState, error: NodeError) -> Command:
    """父图适配节点异常的最后一道兜底，只投影统一 RAG 字段。"""
    log_event(
        LOGGER,
        "rag.enrichment.degraded",
        details={
            "status": "unavailable",
            "reason_code": "unavailable",
            "question_count": 0,
            "evidence_count": 0,
        },
    )
    return Command(
        update={
            "knowledge_base_result": build_rag_degradation_result(error.error),
        },
        goto="agent",
    )


def degrade_web_search_planner(state: CausalAgentState, error: NodeError) -> dict:
    """web_search planner 失败后的降级：写 planner 组 success=False。"""
    return {
        "planner": {
            "success": False,
            "research_question": "",
            "query": "",
            "query_en": "",
            "reason": "",
            "error": sanitize_error(error.error),
        }
    }


def degrade_academic_search(state: CausalAgentState, error: NodeError) -> dict:
    """academic_search 失败后的降级：写 search 组 success=False。"""
    return {
        "search": {
            "success": False,
            "results": [],
            "number_of_results": 0,
            "error": sanitize_error(error.error),
        }
    }


WebSearchStatus = Literal["available", "unavailable", "protocol_error"]


def build_web_search_degradation_result(
    error: Any = None,
    *,
    status: WebSearchStatus = "unavailable",
) -> dict[str, Any]:
    """构造统一的联网搜索降级结果"""
    if isinstance(error, asyncio.CancelledError):
        raise error
    if isinstance(error, BaseException):
        error_message = sanitize_error(error)
    elif error is None:
        error_message = "联网搜索子图未生成有效结果。"
    else:
        error_message = str(error)
    return {
        "success": False,
        "status": status,
        "query": "",
        "results": [],
        "content": [],
        "error": error_message,
    }


def degrade_web_search_parser_failure(state: CausalAgentState, error: NodeError) -> dict:
    """web_search result_parser 失败后的协议降级：写统一 web_search_result。"""
    return {
        "web_search_result": build_web_search_degradation_result(
            error.error,
            status="protocol_error",
        )
    }


def degrade_web_search_adapter_result(state: CausalAgentState, error: NodeError) -> Command:
    """父图 web_search 节点失败的最后一道兜底：写统一结果并回到 agent。"""
    return Command(
        update={
            "web_search_result": build_web_search_degradation_result(
                error.error,
                status="protocol_error",
            )
        },
        goto="agent",
    )


def recover_postprocess_to_report(state: CausalAgentState, error: NodeError) -> Command:
    """后处理失败后的恢复：使用原始分析结果继续报告生成。"""
    message = f"{_error_message(error)}；将使用原始分析结果继续生成报告。"
    return Command(
        update={
            "messages": [AIMessage(content=message, name=error.node)],
            "postprocess_result": {"error": message},
        },
        goto="report",
    )


def recover_terminal_message(state: CausalAgentState, error: NodeError) -> dict:
    """终态回答节点失败后的恢复：返回可展示的失败消息，让 graph 正常结束。"""
    return {
        "messages": [
            AIMessage(
                content=f"抱歉，本次生成回答时发生错误：{_error_message(error)}",
                name=error.node,
            )
        ]
    }


def recover_report(state: CausalAgentState, error: NodeError) -> dict:
    """报告节点失败后的恢复：生成降级报告文档，让 worker 能走 final_result。"""
    message = f"报告生成失败：{_error_message(error)}"
    return {
        "messages": [AIMessage(content="决策：因果分析报告生成失败。", name=error.node)],
        "report_document": build_degraded_report_document(message),
    }


NodeErrorHandler = Callable[[CausalAgentState, NodeError], Command | dict]
