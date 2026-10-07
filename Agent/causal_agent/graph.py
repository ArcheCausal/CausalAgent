from langgraph.graph import StateGraph, END
from .state import CausalAgentState
from . import nodes, edges
from .graph_utils import (
    bind_node,
    bind_subgraph_node,
    guarded_context_router,
    guarded_error_handler,
    guarded_router,
)
from .tool_subgraphs import build_mcp_subgraph, build_rag_subgraph, build_web_search_subgraph
from .context import AgentRunContext
from .fault_tolerance import (
    recover_postprocess_to_report,
    recover_report,
    recover_terminal_message,
    recover_tools_to_agent,
    recover_fold_to_agent,
    recover_preprocess_to_agent,
    degrade_rag_adapter_result,
    degrade_web_search_adapter_result,
    route_to_normal_chat,
    short_retry,
    timeout,
    tool_retry,
)
import logging
from collections.abc import Mapping
import asyncio
import hashlib
import inspect
from dataclasses import replace
from typing import Any

from Agent.deep_agent.finalization import (
    FinalizationGate,
    StructuredResponseError,
    build_finalization_retry_instruction,
    finalization_rule_hint,
)
from Agent.deep_agent.memory import MEMORY_PATHS, MEMORY_TEMPLATES, trusted_memory_namespace
from Agent.deep_agent.state import (
    assert_checkpoint_state_safe,
    from_deep_agent_output,
    retry_deep_agent_from_checkpoint,
    to_deep_agent_input,
)
from Agent.deep_agent_tools.identity import (
    build_deep_agent_execution_scope,
    build_deep_agent_run_id,
    build_deep_agent_step_id,
)
from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    EvidenceResult,
    WebEvidenceResult,
    canonical_json_bytes,
)


DEEP_AGENT_CHECKPOINT_NAMESPACE = "deep_agent_v1"



def build_graph(
    llm: "ChatOpenAI",
    mcp_tools: list,
    rag_tools: list,
    checkpointer,
    rag_available: bool = True,
):
    """
    构建父图。

    父图只表达业务阶段顺序，MCP/RAG 的 tool-calling 细节封装在各自子图内。
    """
    workflow = StateGraph(CausalAgentState, context_schema=AgentRunContext)

    streaming_llm = llm.model_copy(update={"streaming": True})
    agent_node_with_llm = bind_node(nodes.agent_node, event_node_name="agent", llm=llm)#将普通节点函数绑定llm，这些普通节点函数内部要调用大模型，但 LangGraph 执行节点时主要只传一个参数：state
    fold_node_with_llm = bind_node(nodes.fold_node, event_node_name="fold", llm=llm)
    preprocess_node_with_llm = bind_node(nodes.preprocess_node, event_node_name="preprocess", llm=llm)
    mcp_subgraph = build_mcp_subgraph(llm=llm, mcp_tools=mcp_tools)#创建mcp子图
    rag_subgraph = build_rag_subgraph(
        llm=llm,
        rag_tools=rag_tools,
        rag_available=rag_available,
    )
    # 映射rag子state，隔离父子状态
    rag_adapter_node = bind_subgraph_node(
        nodes.rag_subgraph_adapter_node,
        event_node_name="rag",
        rag_subgraph=rag_subgraph,
    )
    web_search_subgraph = build_web_search_subgraph(llm=llm)
    postprocess_node_with_llm = bind_node(nodes.postprocess_node, event_node_name="postprocess", llm=llm)
    inquiry_answer_node_with_llm = bind_node(
        nodes.inquiry_answer_node,
        event_node_name="inquiry_answer",
        llm=streaming_llm,
    )
    # 报告草稿是结构化输出，模型正文恒为空，因此使用非流式模型，
    # 避免流式工具参数拼接带来的额外失败面。
    report_node_with_llm = bind_node(
        nodes.report_node,
        event_node_name="report",
        llm=llm,
    )
    normal_chat_node_with_llm = bind_node(
        nodes.normal_chat_node,
        event_node_name="normal_chat",
        llm=streaming_llm,
    )

#节点注册部分
    workflow.add_node(
        "agent",
        agent_node_with_llm,#这个节点真正执行的函数
        retry_policy=short_retry(),#出错怎么重试
        timeout=timeout(run_timeout=45, idle_timeout=20),#执行多久算超时
        error_handler=guarded_error_handler(
            route_to_normal_chat,
            event_node_name="agent",
            timeout_ms=45_000,
        ),#最后失败怎么兜底
    )
    workflow.add_node(
        "fold",
        fold_node_with_llm,
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=120, idle_timeout=45),
        error_handler=guarded_error_handler(
            recover_fold_to_agent,
            event_node_name="fold",
            timeout_ms=120_000,
        ),
    )
    workflow.add_node(
        "preprocess",
        preprocess_node_with_llm,
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=180, idle_timeout=80),
        error_handler=guarded_error_handler(
            recover_preprocess_to_agent,
            event_node_name="preprocess",
            timeout_ms=180_000,
        ),
    )

    workflow.add_node("mcp", mcp_subgraph)#mcp子图注册成节点
    workflow.add_node(
        "rag",
        rag_adapter_node,
        error_handler=guarded_error_handler(
            degrade_rag_adapter_result,
            event_node_name="rag",
        ),
    )
    workflow.add_node(
        "web_search",
        web_search_subgraph,
        error_handler=guarded_error_handler(
            degrade_web_search_adapter_result,
            event_node_name="web_search",
        ),
    )
    workflow.add_node(
        "postprocess",
        postprocess_node_with_llm,
        timeout=timeout(run_timeout=240, idle_timeout=90),
        error_handler=guarded_error_handler(
            recover_postprocess_to_report,
            event_node_name="postprocess",
            timeout_ms=240_000,
        ),
    )
    workflow.add_node(
        "report",
        report_node_with_llm,
        retry_policy=short_retry(),
        # 非流式调用只在开始和结束刷新空闲计时，放宽窗口以容纳慢响应。
        timeout=timeout(run_timeout=180, idle_timeout=120),
        error_handler=guarded_error_handler(
            recover_report,
            event_node_name="report",
            timeout_ms=180_000,
        ),
    )
    workflow.add_node(
        "normal_chat",
        normal_chat_node_with_llm,
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=60, idle_timeout=30),
        error_handler=guarded_error_handler(
            recover_terminal_message,
            event_node_name="normal_chat",
            timeout_ms=60_000,
        ),
    )
    workflow.add_node(
        "inquiry_answer",
        inquiry_answer_node_with_llm,
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=90, idle_timeout=30),
        error_handler=guarded_error_handler(
            recover_terminal_message,
            event_node_name="inquiry_answer",
            timeout_ms=90_000,
        ),
    )
    workflow.add_node(
        "context_switch",
        bind_subgraph_node(
            nodes.context_switch_node,
            event_node_name="context_switch",
        ),
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=60, idle_timeout=30),
        error_handler=guarded_error_handler(
            route_to_normal_chat,
            event_node_name="context_switch",
            timeout_ms=60_000,
        ),
    )

    workflow.set_entry_point("agent")#节点入口
    workflow.add_conditional_edges(#节点的边
        "agent",#从agent节点出发
        guarded_router(edges.decision_router),#由decision_router函数决定路由？
        {
            "fold": "fold",#"路由函数返回值": "要跳转到的节点名"
            "normal_chat": "normal_chat",
            "postprocess": "postprocess",
            "report": "report",
            "inquiry_answer": "inquiry_answer",
            "context_switch": "context_switch",
        }
    )
    workflow.add_conditional_edges(
        "context_switch",
        guarded_router(edges.decision_router),
        {
            "fold": "fold",
            "normal_chat": "normal_chat",
            "report": "report",
            "inquiry_answer": "inquiry_answer",
            # context_switch 正常只写入 fold/report/inquiry_answer/normal_chat；
            # 这里为异常状态保留一个确定性的终点，避免自环。
            "context_switch": "normal_chat",
            "postprocess": "report",
        },
    )
    workflow.add_conditional_edges(
        "fold",
        guarded_router(edges.fold_router),#由fold_router函数决定路由
        {
            "preprocess": "preprocess",
            "agent": "agent",
            "normal_chat": "normal_chat",
        }
    )

    workflow.add_edge("preprocess", "mcp")#从preprocess节点到mcp子图节点的边

    workflow.add_conditional_edges(
        "mcp", 
        guarded_router(edges.mcp_router),
        {
            "rag": "rag", 
            "agent": "agent",
            "normal_chat": "normal_chat",
        }
    )
    
    workflow.add_conditional_edges(
        "rag",
        guarded_context_router(edges.web_search_router),
        {
            "web_search": "web_search",
            "agent": "agent",
        },
    )
    workflow.add_edge("web_search", "agent")
    workflow.add_conditional_edges(
        "postprocess",
        guarded_router(edges.postprocess_router),
        {
            "report": "report"
        }
    )

    workflow.add_edge("report", END)
    workflow.add_edge("normal_chat", END)
    workflow.add_edge("inquiry_answer", END)

    if checkpointer is None or checkpointer is False:
        raise RuntimeError("必须提供 PostgreSQL Checkpointer，禁止无持久化降级")

    return workflow.compile(checkpointer=checkpointer)



def create_graph_from_tools(
    llm: "ChatOpenAI",
    mcp_tools: list,
    checkpointer,
    rag_available: bool = True,
):
    """使用已加载的 MCP tools 构建父图，返回可直接执行的 compiled graph。"""
    from Agent.tool_node.rag_tool_registry import build_rag_tools
    # graph 层不再关心 MCP session。
    rag_tools = build_rag_tools()
    return build_graph(
        llm=llm,
        mcp_tools=mcp_tools,
        rag_tools=rag_tools,
        checkpointer=checkpointer,
        rag_available=rag_available,
    )


def _safe_algorithm_result_summary(result: AlgorithmResult) -> dict[str, Any]:
    """把算法结果压缩为报告可读事实，不把 raw payload 带进提示词。"""

    return {
        "result_ref": result.result_ref,
        "capability_id": result.capability_id,
        "capability_version": result.capability_version,
        "status": result.status,
        "graph_semantics": result.graph_semantics,
        "summary": result.summary,
        "diagnostics": result.diagnostics.model_dump(mode="json"),
        "warnings": [warning.model_dump(mode="json") for warning in result.warnings],
    }


def _legacy_rag_evidence_result(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """把 Deep Agent evidence 投影为既有 report formatter 能消费的摘要。"""

    normalized: list[EvidenceResult] = []
    for key, value in evidence.items():
        try:
            item = EvidenceResult.model_validate(value)
        except Exception:
            continue
        if str(key) == item.evidence_ref:
            normalized.append(item)
    if not normalized:
        return {
            "success": False,
            "status": "unavailable",
            "summary": "Deep Agent 未返回可引用的知识库证据。",
            "questions": [],
        }
    answer = "\n".join(
        f"[{item.evidence_ref}] {item.snippet}" for item in normalized
    )
    retrieved_docs = [
        {
            "evidence_id": item.evidence_ref,
            "metadata": {
                "source_title": item.source_title,
                "source_url": item.source_url,
                "locator": item.locator,
                "release_id": item.release_id,
            },
            "rerank_score": item.rerank_score if item.rerank_score is not None else item.score,
        }
        for item in normalized
    ]
    return {
        "success": True,
        "status": "available",
        "questions": [
            {
                "question": "Deep Agent 检索的可引用证据",
                "intent": "为因果分析报告补充背景证据",
                "answer": answer,
                "confidence": "medium",
                "citations": [item.evidence_ref for item in normalized],
                "retrieved_docs": retrieved_docs,
            }
        ],
    }


def _legacy_web_evidence_result(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """把 Deep Agent Web evidence 投影为旧报告的 snippet-only 输入。"""

    normalized: list[WebEvidenceResult] = []
    for key, value in evidence.items():
        try:
            item = WebEvidenceResult.model_validate(value)
        except Exception:
            continue
        if str(key) == item.evidence_ref:
            normalized.append(item)
    if not normalized:
        return {
            "success": False,
            "status": "unavailable",
            "content": [],
        }
    return {
        "success": True,
        "status": "available",
        "content": [
            {
                "title": item.source_title or "",
                "url": item.source_url or "",
                "origin": item.locator or "学术来源",
                "text": item.snippet,
            }
            for item in normalized
        ],
    }


def _legacy_analysis_result(
    *,
    decision: Any | None,
    algorithm_results: Mapping[str, Any],
    action_ledger: Mapping[str, Any],
    finalization_status: str,
    finalization_error: str | None = None,
) -> dict[str, Any]:
    """为现有 report/presenter 提供兼容视图；权威仍是 Deep Agent 投影字段。"""

    normalized_results: dict[str, AlgorithmResult] = {}
    for key, value in algorithm_results.items():
        try:
            result = AlgorithmResult.model_validate(value)
        except Exception:
            continue
        if key == result.result_ref:
            normalized_results[key] = result

    payload: dict[str, Any] = {
        "success": False,
        "outcome": getattr(decision, "outcome", None),
        "finalization_status": finalization_status,
        "algorithm_results": [
            _safe_algorithm_result_summary(result)
            for result in normalized_results.values()
        ],
        "action_ledger": [
            {
                "tool_name": getattr(record, "tool_name", None),
                "final_status": getattr(record, "final_status", None),
                "result_ref": getattr(record, "result_ref", None),
            }
            for record in action_ledger.values()
        ],
    }
    if finalization_error:
        payload.update(
            {
                "error_type": "FinalizationDegraded",
                "error": finalization_error,
                "message": (
                    "程序无法验证最终算法选择；以下报告仅基于已验证的输入和执行摘要。"
                ),
            }
        )

    primary_ref = getattr(decision, "primary_result_ref", None)
    primary = normalized_results.get(primary_ref) if primary_ref else None
    if (
        finalization_status == "valid"
        and getattr(decision, "outcome", None) == "algorithm_supported"
        and primary is not None
        and primary.status == "valid"
        and primary.standardized_graph is not None
    ):
        payload["success"] = True
        payload["data"] = primary.standardized_graph.model_dump(mode="json")
        payload["primary_result_ref"] = primary.result_ref
    elif finalization_status == "valid" and getattr(decision, "outcome", None) == "evidence_only":
        payload["message"] = "本次分析未采用算法结果，仅返回证据型信息。"
    elif finalization_status == "valid" and getattr(decision, "outcome", None) == "no_valid_algorithm":
        payload["error_type"] = "NoValidAlgorithmResult"
        payload["message"] = "算法调用未产生有效结果，不能据此断言不存在因果关系。"
    return payload


async def _stream_deep_agent(
    deep_agent: Any,
    child_input: Any,
    *,
    child_config: dict[str, Any],
    child_context: AgentRunContext,
    runtime: Any,
) -> Mapping[str, Any]:
    """消费内层 messages 流，并保留 values 流提供的完整 State。"""

    stream = getattr(deep_agent, "astream", None)
    if not callable(stream):
        result = await deep_agent.ainvoke(
            child_input,
            config=child_config,
            context=child_context,
        )
        if not isinstance(result, Mapping):
            raise RuntimeError("Deep Agent returned an invalid State")
        return result

    stream_config = {
        "configurable": {
            "thread_id": child_config["configurable"]["thread_id"],
            "checkpoint_ns": child_config["configurable"]["checkpoint_ns"],
        },
        "metadata": dict(child_config.get("metadata") or {}),
    }
    checkpointer = child_config["configurable"].get("__pregel_checkpointer")
    if checkpointer is not None:
        stream_config["configurable"]["__pregel_checkpointer"] = checkpointer
    if "recursion_limit" in child_config:
        stream_config["recursion_limit"] = child_config["recursion_limit"]

    writer = getattr(runtime, "stream_writer", None)
    final_values: Mapping[str, Any] | None = None
    async for chunk in stream(
        child_input,
        config=stream_config,
        context=child_context,
        stream_mode=["messages", "values"],
        subgraphs=True,
        version="v2",
    ):
        if child_context is not None and hasattr(child_context, "ensure_active"):
            await child_context.ensure_active()
        if (
            isinstance(chunk, Mapping)
            and chunk.get("type") == "values"
            and isinstance(chunk.get("data"), Mapping)
        ):
            final_values = chunk["data"]
        if (
            isinstance(chunk, Mapping)
            and chunk.get("type") == "messages"
            and callable(writer)
        ):
            writer(
                {
                    "type": "message_chunk",
                    "ns": chunk.get("ns") or (),
                    "data": chunk.get("data"),
                }
            )
    if final_values is None:
        snapshot = await deep_agent.aget_state(child_config)
        final_values = getattr(snapshot, "values", None)
    result = final_values
    if not isinstance(result, Mapping):
        raise RuntimeError("Deep Agent returned an invalid State")
    return result


async def _deep_agent_parent_node(
    state,
    *,
    runtime,
    config,
    deep_agent,
    store,
    memory_init_lock,
):
    """显式投影父 State 到 Deep Agent，并只接收白名单输出。"""

    context = getattr(runtime, "context", None)
    if context is not None and hasattr(context, "ensure_active"):
        await context.ensure_active()
    await _ensure_official_memory_files(
        store=store,
        context=context,
        lock=memory_init_lock,
    )
    trusted_identity = getattr(context, "trusted_identity", None)
    if trusted_identity is None:
        raise RuntimeError("Deep Agent child requires a trusted Job identity")
    job_id = str(trusted_identity.job_id)
    expected_child_run_id = build_deep_agent_run_id(job_id=trusted_identity.job_id)
    supplied_child_run_id = state.get("deep_agent_run_id")
    if supplied_child_run_id and str(supplied_child_run_id) != expected_child_run_id:
        raise RuntimeError("Deep Agent child run identity is invalid")
    child_run_id = expected_child_run_id
    child_config = dict(config or {})
    child_config["configurable"] = dict(child_config.get("configurable") or {})
    child_config["configurable"]["thread_id"] = child_run_id
    child_config["configurable"]["checkpoint_ns"] = DEEP_AGENT_CHECKPOINT_NAMESPACE
    child_config["metadata"] = dict(child_config.get("metadata") or {})
    child_config["metadata"]["deep_agent_run_id"] = child_run_id

    child_context = context
    execution_info = getattr(runtime, "execution_info", None)
    parent_task_id = getattr(execution_info, "task_id", None)
    if isinstance(context, AgentRunContext) and parent_task_id:
        child_context = replace(
            context,
            deep_agent_step_id=build_deep_agent_step_id(
                job_id=trusted_identity.job_id,
                attempt_count=int(trusted_identity.attempt_count),
                task_id=str(parent_task_id),
            ),
        )

    current_scope = None
    if trusted_identity is not None:
        current_scope = build_deep_agent_execution_scope(
            job_id=trusted_identity.job_id,
            attempt_count=int(trusted_identity.attempt_count),
            lease_epoch=int(trusted_identity.lease_epoch),
            input_identity=trusted_identity.current_input_identity(),
        )

    # 子图 checkpoint 是内部 messages/官方 State 的真相源。父图重跑这个
    # 节点时，先读取稳定 child thread：未完成或已完成的 child 都可直接
    # 从 checkpoint 继续/取回；只有首次进入才从父图最小投影构造输入。
    child_snapshot = None
    get_child_state = getattr(deep_agent, "aget_state", None)
    if callable(get_child_state):
        child_snapshot = await get_child_state(child_config)
        if context is not None and hasattr(context, "check_after_call"):
            await context.check_after_call()
        elif context is not None and hasattr(context, "ensure_active"):
            await context.ensure_active()
    checkpoint_values = getattr(child_snapshot, "values", None)
    retry_instruction = state.get("deep_agent_retry_instruction")
    checkpoint_matches = bool(
        isinstance(checkpoint_values, Mapping)
        and checkpoint_values
        and current_scope
        and checkpoint_values.get("execution_scope") == current_scope
    )
    if checkpoint_matches:
        assert_checkpoint_state_safe(checkpoint_values)
        if isinstance(retry_instruction, str) and retry_instruction.strip():
            _emit_deep_agent_retry_notice(
                runtime=runtime,
                attempt_count=int(trusted_identity.attempt_count),
                step_id=getattr(child_context, "deep_agent_step_id", None),
            )
            child_input = retry_deep_agent_from_checkpoint(
                checkpoint_values,
                retry_instruction=retry_instruction,
            )
        else:
            child_input = None
    else:
        child_input = to_deep_agent_input(
            {
                **state,
                "deep_agent_run_id": child_run_id,
                "web_search_enabled": getattr(context, "web_search_enabled", None),
            },
            reset_execution_artifacts=True,
        )
    if isinstance(child_input, Mapping) and current_scope:
        child_input["execution_scope"] = current_scope
    if child_input is not None and context is not None and hasattr(context, "assert_state_safe"):
        context.assert_state_safe(child_input)
    child_state = await _stream_deep_agent(
        deep_agent,
        child_input,
        child_config=child_config,
        child_context=child_context,
        runtime=runtime,
    )
    if context is not None and hasattr(context, "check_after_call"):
        await context.check_after_call()
    elif context is not None and hasattr(context, "ensure_active"):
        await context.ensure_active()
    if not isinstance(child_state, Mapping):
        raise RuntimeError("Deep Agent returned an invalid State")
    assert_checkpoint_state_safe(child_state)
    if child_state.get("deep_agent_run_id") != child_run_id:
        raise RuntimeError("Deep Agent returned an invalid child run identity")
    if child_state.get("execution_scope") != current_scope:
        raise RuntimeError("Deep Agent returned an invalid execution scope")
    update = dict(from_deep_agent_output(child_state))
    update["deep_agent_run_id"] = child_run_id
    update["deep_agent_status"] = "completed"
    # data_profile 是外层 admission 的只读摘要副本，不是 runtime dependency。
    data_profile = (
        child_input.get("data_profile")
        if isinstance(child_input, Mapping)
        else child_state.get("data_profile")
    )
    if data_profile is not None:
        update["data_profile"] = data_profile
    # report_node 仍使用既有 formatter；这里仅投影结构化证据，不把
    # provider response、raw result 或运行时对象带回父 State。
    update["knowledge_base_result"] = _legacy_rag_evidence_result(
        update["deep_agent_rag_evidence"]
    )
    update["web_search_result"] = _legacy_web_evidence_result(
        update["deep_agent_web_evidence"]
    )
    return update


async def _ensure_official_memory_files(
    *,
    store: Any | None,
    context: Any,
    lock: asyncio.Lock,
) -> None:
    """首次 Job 使用时按可信 user namespace create-if-absent 初始化记忆。"""

    if store is None:
        return
    identity = getattr(context, "trusted_identity", None)
    if identity is None:
        raise RuntimeError("trusted runtime identity is required for memory initialization")
    namespace = trusted_memory_namespace(context)
    getter = getattr(store, "aget", None) or getattr(store, "get", None)
    writer = getattr(store, "aput", None) or getattr(store, "put", None)
    if not callable(getter) or not callable(writer):
        raise RuntimeError("PostgreSQL Store does not expose get/put operations")
    async with lock:
        for path in MEMORY_PATHS:
            existing = getter(namespace, path)
            if inspect.isawaitable(existing):
                existing = await existing
            if existing is not None:
                continue
            value = {
                "content": MEMORY_TEMPLATES[path].decode("utf-8"),
                "encoding": "utf-8",
            }
            result = writer(namespace, path, value)
            if inspect.isawaitable(result):
                await result


def _emit_public_progress_note(
    runtime: Any,
    *,
    node_name: str,
    summary: str,
    event_key: str,
    step_id: str | None = None,
) -> None:
    """按父图阶段发布一条脱敏说明；缺省 step_id 由事件适配器补齐。"""

    if not summary or len(summary) > 1200:
        return
    payload: dict[str, Any] = {
        "type": "progress",
        "node_name": node_name,
        "summary": summary,
        "_event_key": event_key,
    }
    if step_id:
        payload["step_id"] = step_id
    writer = getattr(runtime, "stream_writer", None)
    if callable(writer):
        writer(payload)


def _finalization_notice_summary(*, error: BaseException, retry: bool) -> str:
    """把 Gate 失败规则转成普通用户可见的说明文本。"""

    if retry:
        summary = "结构化最终决策未通过程序事实校验，已发起一次修正。"
    else:
        summary = (
            "结构化最终决策仍未通过程序事实校验，本次降级为仅基于已验证输入的报告。"
        )
    hint = finalization_rule_hint(getattr(error, "rule", None))
    if hint:
        summary = f"{summary}修正要求：{hint}"
    return summary


def _emit_finalization_progress_notice(
    *,
    runtime: Any,
    identity: Any,
    retry_ordinal: int | None,
    error: BaseException,
) -> None:
    """发布 Gate 失败说明；重试与降级使用互不覆盖的稳定事件键。"""

    attempt_count = int(getattr(identity, "attempt_count", 0))
    if retry_ordinal is None:
        event_key = f"finalization-gate-degraded:{attempt_count}"
    else:
        event_key = f"finalization-gate-retry:{attempt_count}:{retry_ordinal}"
    _emit_public_progress_note(
        runtime,
        node_name="finalization_gate",
        summary=_finalization_notice_summary(
            error=error,
            retry=retry_ordinal is not None,
        ),
        event_key=event_key,
    )


def _emit_deep_agent_retry_notice(
    *,
    runtime: Any,
    attempt_count: int,
    step_id: str | None,
) -> None:
    """在修正重试开始时说明第二次 Deep Agent 阶段的目的。"""

    _emit_public_progress_note(
        runtime,
        node_name="deep_agent",
        step_id=step_id,
        summary="正在按校验要求修正最终决策：沿用已有工具结果，不重复调用工具。",
        event_key=f"deep-agent-retry:{attempt_count}:{step_id or ''}",
    )


async def _finalization_gate_node(
    state,
    *,
    runtime,
    config,
    gate: FinalizationGate,
):
    """执行纯确定性 finalization；失败只消耗一次持久化修正机会。"""

    context = getattr(runtime, "context", None)
    identity = getattr(context, "trusted_identity", None)
    if identity is None:
        raise RuntimeError("trusted runtime identity is required for finalization")
    try:
        decision = gate.validate(
            decision=state.get("deep_agent_structured_response")
            or state.get("deep_agent_decision"),
            algorithm_results=state.get("deep_agent_algorithm_results"),
            action_ledger=state.get("deep_agent_action_ledger"),
            trusted_identity=identity,
            rag_evidence=state.get("deep_agent_rag_evidence"),
            web_evidence=state.get("deep_agent_web_evidence"),
            route_decision=state.get("route_decision"),
            web_search_enabled=getattr(context, "web_search_enabled", None),
        )
    except StructuredResponseError as exc:
        retry_count = int(state.get("finalization_retry_count") or 0)
        if retry_count < 1:
            _emit_finalization_progress_notice(
                runtime=runtime,
                identity=identity,
                retry_ordinal=retry_count + 1,
                error=exc,
            )
            return {
                "finalization_retry_count": retry_count + 1,
                "deep_agent_retry_instruction": build_finalization_retry_instruction(
                    exc
                ),
            }
        _emit_finalization_progress_notice(
            runtime=runtime,
            identity=identity,
            retry_ordinal=None,
            error=exc,
        )
        safe_error = "FINALIZATION_CONTRACT_INVALID"
        return {
            "finalization_status": "degraded",
            "finalization_error": safe_error,
            "deep_agent_retry_instruction": "",
            "causal_analysis_result": _legacy_analysis_result(
                decision=None,
                algorithm_results=state.get("deep_agent_algorithm_results") or {},
                action_ledger=state.get("deep_agent_action_ledger") or {},
                finalization_status="degraded",
                finalization_error=safe_error,
            ),
        }

    _emit_public_final_decision(
        runtime=runtime,
        identity=identity,
        decision=decision,
        algorithm_results=state.get("deep_agent_algorithm_results") or {},
        registry=getattr(gate, "registry", None),
    )
    return {
        "deep_agent_decision": decision,
        "deep_agent_structured_response": decision,
        "deep_agent_retry_instruction": "",
        "finalization_status": "valid",
        "causal_analysis_result": _legacy_analysis_result(
            decision=decision,
            algorithm_results=state.get("deep_agent_algorithm_results") or {},
            action_ledger=state.get("deep_agent_action_ledger") or {},
            finalization_status="valid",
        ),
    }


def _emit_public_final_decision(
    *,
    runtime: Any,
    identity: Any,
    decision: Any,
    algorithm_results: Mapping[str, Any],
    registry: Any,
) -> None:
    """把 Gate 已验证的内部引用转换成算法名称并发布最终决策说明。"""

    result_names: dict[str, str] = {}
    for result_ref, raw_result in algorithm_results.items():
        try:
            result = AlgorithmResult.model_validate(raw_result)
            entry = registry.get(result.capability_id)
        except Exception:
            continue
        result_names[str(result_ref)] = entry.spec.public_name

    disposition_labels = {
        "primary": "主结果",
        "supporting": "辅助结果",
        "discarded": "未采用",
    }
    selections = [
        (
            f"{result_names[assessment.result_ref]}："
            f"{disposition_labels[assessment.disposition]}（{assessment.rationale}）"
        )
        for assessment in decision.result_assessments
        if assessment.result_ref in result_names
    ]
    summary_parts = []
    if selections:
        summary_parts.append("；".join(selections) + "。")
    summary_parts.append(decision.selection_rationale)
    confidence_labels = {"low": "低", "medium": "中等", "high": "高"}
    summary_parts.append(f"置信度：{confidence_labels[decision.confidence]}")
    summary = " ".join(part for part in summary_parts if part).strip()
    if not summary or len(summary) > 1200:
        return
    payload = {
        "type": "decision",
        "decision_kind": "final",
        "summary": summary,
        "confidence": decision.confidence,
    }
    payload_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    attempt_count = int(identity.attempt_count)
    payload["_event_key"] = (
        f"deep-agent-final-decision:{attempt_count}:{payload_digest}"
    )
    writer = getattr(runtime, "stream_writer", None)
    if callable(writer):
        writer(payload)


def _finalization_router(state) -> str:
    """第一次 Gate 失败回到同一 Deep Agent，第二次或成功进入报告。"""

    return (
        "deep_agent"
        if state.get("finalization_status") is None
        and int(state.get("finalization_retry_count") or 0) == 1
        else "report"
    )


def build_deep_agent_parent_graph(
    *,
    llm: "ChatOpenAI",
    deep_agent: Any,
    registry: Any,
    checkpointer: Any,
    store: Any | None = None,
) -> Any:
    """构建新 Job 路径：admission/preprocess → Deep Agent → Gate → report。

    旧 ``build_graph`` 保留给兼容链路；worker 新运行时显式使用本构造器，
    因而不会再加载 slot stdio MCP 或固定 mcp→rag→web 流水线。
    """

    if checkpointer is None or checkpointer is False:
        raise RuntimeError("必须提供 PostgreSQL Checkpointer，禁止无持久化降级")
    workflow = StateGraph(CausalAgentState, context_schema=AgentRunContext)
    memory_init_lock = asyncio.Lock()
    streaming_llm = llm.model_copy(update={"streaming": True})
    workflow.add_node(
        "agent",
        bind_node(nodes.agent_node, event_node_name="agent", llm=llm),
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=45, idle_timeout=20),
        error_handler=guarded_error_handler(
            route_to_normal_chat,
            event_node_name="agent",
            timeout_ms=45_000,
        ),
    )
    workflow.add_node(
        "fold",
        bind_node(nodes.fold_node, event_node_name="fold", llm=llm),
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=120, idle_timeout=45),
        error_handler=guarded_error_handler(
            recover_fold_to_agent,
            event_node_name="fold",
            timeout_ms=120_000,
        ),
    )
    workflow.add_node(
        "preprocess",
        bind_node(nodes.preprocess_node, event_node_name="preprocess", llm=llm),
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=180, idle_timeout=80),
        error_handler=guarded_error_handler(
            recover_preprocess_to_agent,
            event_node_name="preprocess",
            timeout_ms=180_000,
        ),
    )
    workflow.add_node(
        "deep_agent",
        bind_subgraph_node(
            _deep_agent_parent_node,
            event_node_name="deep_agent",
            deep_agent=deep_agent,
            store=store,
            memory_init_lock=memory_init_lock,
        ),
    )
    workflow.add_node(
        "finalization_gate",
        bind_subgraph_node(
            _finalization_gate_node,
            event_node_name="finalization_gate",
            gate=FinalizationGate(registry=registry),
        ),
    )
    workflow.add_node(
        "report",
        bind_node(nodes.report_node, event_node_name="report", llm=llm),
        retry_policy=short_retry(),
        # 报告草稿是结构化输出且模型正文恒为空，非流式调用只在开始和结束
        # 刷新空闲计时，因此放宽窗口以容纳慢响应。
        timeout=timeout(run_timeout=180, idle_timeout=120),
        error_handler=guarded_error_handler(
            recover_report,
            event_node_name="report",
            timeout_ms=180_000,
        ),
    )
    workflow.add_node(
        "normal_chat",
        bind_node(nodes.normal_chat_node, event_node_name="normal_chat", llm=streaming_llm),
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=60, idle_timeout=30),
        error_handler=guarded_error_handler(
            recover_terminal_message,
            event_node_name="normal_chat",
            timeout_ms=60_000,
        ),
    )
    workflow.add_node(
        "inquiry_answer",
        bind_node(
            nodes.inquiry_answer_node,
            event_node_name="inquiry_answer",
            llm=streaming_llm,
        ),
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=90, idle_timeout=30),
        error_handler=guarded_error_handler(
            recover_terminal_message,
            event_node_name="inquiry_answer",
            timeout_ms=90_000,
        ),
    )
    workflow.add_node(
        "context_switch",
        bind_subgraph_node(
            nodes.context_switch_node,
            event_node_name="context_switch",
        ),
        retry_policy=short_retry(),
        timeout=timeout(run_timeout=60, idle_timeout=30),
        error_handler=guarded_error_handler(
            route_to_normal_chat,
            event_node_name="context_switch",
            timeout_ms=60_000,
        ),
    )

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent",
        guarded_router(edges.decision_router),
        {
            "fold": "fold",
            "normal_chat": "normal_chat",
            "postprocess": "report",
            "report": "report",
            "inquiry_answer": "inquiry_answer",
            "context_switch": "context_switch",
        },
    )
    workflow.add_conditional_edges(
        "context_switch",
        guarded_router(edges.decision_router),
        {
            "fold": "fold",
            "normal_chat": "normal_chat",
            "report": "report",
            "inquiry_answer": "inquiry_answer",
            "context_switch": "normal_chat",
            "postprocess": "report",
        },
    )
    workflow.add_conditional_edges(
        "fold",
        guarded_router(edges.fold_router),
        {
            "preprocess": "preprocess",
            "agent": "agent",
            "normal_chat": "normal_chat",
        },
    )
    workflow.add_edge("preprocess", "deep_agent")
    workflow.add_edge("deep_agent", "finalization_gate")
    workflow.add_conditional_edges(
        "finalization_gate",
        guarded_router(_finalization_router),
        {"deep_agent": "deep_agent", "report": "report"},
    )
    workflow.add_edge("report", END)
    workflow.add_edge("normal_chat", END)
    workflow.add_edge("inquiry_answer", END)
    return workflow.compile(checkpointer=checkpointer, store=store)


agent_graph = None
