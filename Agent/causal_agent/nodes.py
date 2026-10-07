import asyncio
from .state import CausalAgentState, RagSubgraphState, WebSearchState
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from typing import Literal, Optional, Any, List, Tuple, Dict
import logging
import json
import re
import io
import pandas as pd
import numpy as np
import networkx as nx
from mcp import ClientSession
from langgraph.func import task
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from Agent.llm_structured_output import StructuredOutputError, ainvoke_structured
from .fault_tolerance import (
    RAG_DEGRADATION_SUMMARY,
    build_rag_degradation_result,
)
from .analysis_context import (
    MAX_CONTEXT_QUESTION_CHARS,
    build_context_index,
    context_algorithm_summary,
    context_facts_text,
    context_graph,
    index_prompt_view,
    match_context_hint,
)
from observability.logging_runtime import log_event


LOGGER = logging.getLogger(__name__)


def _log_node_degradation(
    failure_kind: str,
    fallback: str,
    *,
    exc_info: Any = None,
) -> None:
    """记录节点内部已经决定采用的最终降级，不包含业务正文。"""
    log_event(
        LOGGER,
        "job.node.degraded",
        details={
            "failure_kind": failure_kind,
            "final_attempt": 1,
            "fallback": fallback,
        },
        exc_info=exc_info,
    )

## 基本配置
from config.settings import settings

## 导入人设
## 因果分析人设
from .back_prompt import causal_prompt
## 数据分析人设
from .back_prompt import data_prompt
## 知识库查询人设
from .back_prompt import causal_rag_prompt
## 报告人设
from .back_prompt import causal_report_prompt

from Agent.causal_agent.web_search_node import (
    format_web_search_summary_for_prompt,
    generate_research_question,
    get_web_search_query,
    web_search,
    _merge_by_engine_top3,
)

# 数据库
from Database.agent_connect import require_frozen_file_for_job
from Database.analysis_contexts import (
    apply_fold_context,
    create_context_for_new_file,
    find_user_file_by_name,
    switch_to_context,
)


def resume_value_to_message_content(value: Any) -> str:
    """把 interrupt 恢复值转成消息可接受的文本，保留原值供节点逻辑继续使用。"""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return str(value)


def llm_prompt_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """过滤掉 ToolNode 内部转录，避免把工具调用协议消息回放给后续 LLM。"""
    safe_messages = []
    for message in messages:
        if isinstance(message, ToolMessage) or getattr(message, "type", None) == "tool":
            continue
        if getattr(message, "tool_calls", None) or getattr(message, "invalid_tool_calls", None):
            continue
        if getattr(message, "additional_kwargs", {}).get("tool_calls"):
            continue
        safe_messages.append(message)
    return safe_messages

class AgentIntentDecision(BaseModel):
    """agent 节点的结构化意图判断。

    模型只表达意图、用户提到的上下文线索和澄清问题；分析上下文 ID、用户文件 ID
    和最终图路由都由后端根据 State 与数据库解析后写入。
    """

    intent: Literal[
        "normal_chat",
        "start_analysis",
        "answer_report",
        "revise_report",
        "rerun_analysis",
        "switch_analysis_context",
        "clarify",
    ] = Field(..., description="用户在当前这一轮的意图分类。")
    context_hint: Optional[str] = Field(
        None,
        description=(
            "仅当用户提到某个文件名、报告主题或历史分析时填写，内容必须是用户原话里的"
            "描述性文字；不要填写编号、ID 或数据库标识。"
        ),
    )
    clarification_question: Optional[str] = Field(
        None,
        description="仅当 intent 为 clarify 时填写要反问用户的问题。",
    )


# 意图到路由的映射由后端固定，模型不能直接决定图路由。
INTENT_ROUTES: Dict[str, str] = {
    "normal_chat": "normal_chat",
    "start_analysis": "fold",
    "answer_report": "inquiry_answer",
    "revise_report": "report",
    "rerun_analysis": "fold",
    "switch_analysis_context": "context_switch",
    "clarify": "inquiry_answer",
}

DEFAULT_CONTEXT_CLARIFICATION = (
    "我需要先确认您指的是哪一次分析。请说明文件名、目标变量或报告主题，我再继续。"
)

# 上下文解析失败的固定澄清文案：保证 inquiry_answer 一定能反问用户，
# 而不是把澄清请求交回模型自由发挥。
CLARIFICATION_DEFAULTS = {
    "clarify": DEFAULT_CONTEXT_CLARIFICATION,
    "inactive_context": "这个历史分析已经不再有效，请重新选择要进行的分析。",
    "no_match": (
        "我没有在本次会话里找到匹配的历史分析。请说明文件名、目标变量或报告主题，"
        "我再继续。"
    ),
    "file_not_in_library": (
        "我按文件名没有在您的文件库里找到这个文件。请先确认上传成功，"
        "再说一次文件名，或者直接在输入框里选择该文件重新发起分析。"
    ),
}

_CSV_NAME_PATTERN = re.compile(r"[\w\-.]+\.csv", flags=re.IGNORECASE)

# 意图判断只需要少量上下文：单条消息和整体都设上限，避免上一轮的长报告或长解释
# 整段重复进入提示词。
INTENT_HISTORY_MESSAGE_CHARS = 400
INTENT_HISTORY_TOTAL_CHARS = 2000


def _latest_human_text(state: CausalAgentState) -> str:
    """Return the latest human message content from the graph state."""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return ""


def _recent_history_text(state: CausalAgentState, limit: int = 20) -> str:
    """把最近的有界聊天历史渲染成单行文本，供意图判断使用。

    每条消息限制长度，整体也限制长度并优先保留最近的对话；超出部分只保留开头，
    保证意图判断的输入规模稳定。
    """
    rendered: list[str] = []
    for message in list(state.get("messages", []))[-max(1, int(limit)) :]:
        content = getattr(message, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if len(text) > INTENT_HISTORY_MESSAGE_CHARS:
            text = text[:INTENT_HISTORY_MESSAGE_CHARS] + "…"
        role = "用户" if isinstance(message, HumanMessage) else "助手"
        rendered.append(f"{role}：{text}")
    kept: list[str] = []
    total = 0
    for line in reversed(rendered):
        if kept and total + len(line) > INTENT_HISTORY_TOTAL_CHARS:
            break
        kept.append(line)
        total += len(line)
    return "\n".join(reversed(kept))


def _frozen_filename(state: CausalAgentState) -> Optional[str]:
    """读取当前 Job 冻结文件的文件名。"""
    file_summary = state.get("file_summary") or {}
    filename = file_summary.get("filename")
    return filename if isinstance(filename, str) and filename.strip() else None


def _names_other_file(text: str, frozen_filename: Optional[str]) -> bool:
    """判断消息是否点名了当前 Job 冻结文件之外的其他 CSV 文件。"""
    for name in _CSV_NAME_PATTERN.findall(text):
        if frozen_filename and name.casefold() == frozen_filename.casefold():
            continue
        return True
    return False


def _is_explicit_causal_analysis_request(
    text: str,
    frozen_filename: Optional[str] = None,
) -> bool:
    """Detect requests that should deterministically enter the causal analysis flow."""
    normalized = text.lower()
    has_data_target = any(token in normalized for token in (".csv", "csv", "pc")) or any(
        token in text for token in ("文件", "数据", "读取", "上传")
    )
    has_causal_intent = any(
        token in text
        for token in ("因果分析", "因果推断", "因果边", "边合理性", "后处理", "因果发现")
    )
    has_action = any(
        token in text
        for token in ("分析", "执行", "运行", "读取", "处理", "生成报告", "立即", "使用")
    )
    if not (has_data_target and has_causal_intent and has_action):
        return False
    # 点名了另一个文件时必须交给意图模型与上下文切换处理，否则会拿当前 Job 冻结的
    # 文件去回答另一个文件的问题。
    return not _names_other_file(text, frozen_filename)


def _current_context(state: CausalAgentState) -> dict:
    """读取当前分析上下文的只读投影。"""
    context = state.get("analysis_context")
    return context if isinstance(context, dict) else {}


def _has_available_report(state: CausalAgentState) -> bool:
    """判断当前 Job 是否已有可回答的报告事实。"""
    if _report_document_from_state(state) is not None:
        return True
    context = _current_context(state)
    return bool(context.get("latest_report_message_id") or context.get("latest_report_title"))


def _coerce_agent_decision(value: Any) -> Optional[AgentIntentDecision]:
    """把 State 里的字典还原为结构化意图；非法内容按缺失处理。"""
    if isinstance(value, AgentIntentDecision):
        return value
    if not isinstance(value, dict):
        return None
    try:
        return AgentIntentDecision.model_validate(value)
    except Exception:
        return None


def _clarification_question(state: CausalAgentState) -> Optional[str]:
    """读取本轮需要反问用户的问题，优先使用上下文解析的结果。"""
    for source in (state.get("context_resolution"), state.get("agent_decision")):
        if not isinstance(source, dict):
            continue
        question = source.get("clarification_question")
        if isinstance(question, str) and question.strip():
            return question.strip()
    return None


def _resolve_agent_route(
    state: CausalAgentState,
    decision: Optional[AgentIntentDecision],
    *,
    has_report: bool,
) -> tuple[str, str, Optional[str]]:
    """把结构化意图映射为后端路由，返回路由、报告模式和澄清问题。"""
    if decision is None:
        return "normal_chat", "normal_generation", None
    intent = decision.intent
    if intent == "clarify":
        question = (decision.clarification_question or "").strip()
        return (
            "inquiry_answer",
            "normal_generation",
            question or DEFAULT_CONTEXT_CLARIFICATION,
        )
    if intent in {"answer_report", "revise_report"} and not has_report:
        # 没有可引用的报告时不能进入报告回答或报告修订。
        _log_node_degradation("missing_report_context", "normal_chat")
        return "normal_chat", "normal_generation", None
    if intent == "revise_report":
        return "report", "full_regeneration_from_context", None
    if intent == "switch_analysis_context":
        return "context_switch", "normal_generation", None
    if intent == "rerun_analysis":
        hint = (decision.context_hint or "").strip()
        route = "context_switch" if hint else "fold"
        return route, "normal_generation", None
    if intent == "start_analysis":
        return "fold", "normal_generation", None
    return INTENT_ROUTES.get(intent, "normal_chat"), "normal_generation", None


AGENT_INTENT_PROMPT = """
            你是一个专业的AI助手路由中枢，负责判断用户在当前这一轮想做什么。
            你只输出意图、上下文线索和必要的澄清问题；具体走哪个图节点、使用哪个
            分析上下文，都由后端根据你的意图和系统状态决定。

            # 用户这一轮的消息
            {messages}

            # 最近对话历史
            {recent_history}

            # 当前状态
            - 本轮是否已经拿到可用的分析结果：{has_tool_results}
            - 当前是否已有可回答的报告：{has_report}

            # 当前分析上下文
            {context_summary}

            # 同一会话里的历史分析
            {context_index}

            # 可选意图（只能选一个）
            1. start_analysis：用户要求对数据做新的因果分析。
            2. answer_report：用户针对已有报告或已有分析结果提问。
            3. revise_report：用户要求修改或重写已有报告。
            4. rerun_analysis：用户要求重新执行分析，或换参数重跑。
            5. switch_analysis_context：用户想回到另一个文件或另一次历史分析。
            6. normal_chat：与因果分析无关的普通对话。
            7. clarify：信息不足以判断用户指哪一次分析，需要反问用户。

            # 输出要求
            - 只按 AgentIntentDecision 的结构返回 JSON，不要包含 Markdown 或解释文字。
            - context_hint 只在用户提到文件名、报告主题或历史分析时填写，必须使用用户
              原话里的描述性文字。
            - 不要输出任何编号、ID、文件主键或数据库字段。
            - clarification_question 只在 intent 为 clarify 时填写，其他情况留空。
            """


async def agent_node(state: CausalAgentState, llm: ChatOpenAI) -> dict:
    """
    Agent 节点是图的起点：先用确定性规则处理明确的失败与明确的分析请求，
    再用一次结构化调用判断意图，最后由后端把意图映射为图路由。
    """
    causal_analysis_result = state.get('causal_analysis_result') or {}
    if causal_analysis_result and causal_analysis_result.get("success") is False:
        error_message = (
            causal_analysis_result.get("message")
            or causal_analysis_result.get("error")
            or "因果分析工具执行失败。"
        )
        response_message = AIMessage(
            content=f"决策：普通问答。工具执行失败：{error_message}",
            name="agent"
        )
        return {"messages": [response_message], "route_decision": "normal_chat"}

    # 检查生成报告所需的有效分析结果是否已存在。
    has_tool_results = causal_analysis_result.get("success") is True

    latest_human_text = _latest_human_text(state)
    if not has_tool_results and _is_explicit_causal_analysis_request(
        latest_human_text,
        _frozen_filename(state),
    ):
        response_message = AIMessage(content="决策：信息不全，启动文件加载模块。", name="agent")
        return {
            "messages": [response_message],
            "route_decision": "fold",
            "agent_decision": {"intent": "start_analysis"},
            "report_revision_mode": "normal_generation",
        }

    has_report = _has_available_report(state)
    context = _current_context(state)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", AGENT_INTENT_PROMPT),
            ("human", "请根据上述指示判断意图。"),
        ]
    )
    decision: Optional[AgentIntentDecision] = None
    try:
        decision = await ainvoke_structured(
            llm=llm,
            schema=AgentIntentDecision,
            prompt=prompt,
            inputs={
                "messages": latest_human_text,
                "recent_history": _recent_history_text(state),
                "has_tool_results": has_tool_results,
                "has_report": has_report,
                "context_summary": context_facts_text(context) if context else "当前没有已保存的分析上下文。",
                "context_index": index_prompt_view(state.get("analysis_context_index"))
                or "没有其他历史分析。",
            },
            node_name="agent",
        )
    except StructuredOutputError:
        _log_node_degradation("structured_output_error", "normal_chat")

    route_decision, revision_mode, _clarification = _resolve_agent_route(
        state,
        decision,
        has_report=has_report,
    )

    # 根据后端映射出的路由生成用于审计的展示消息。
    if route_decision == "fold":
        response_message = AIMessage(content="决策：进入因果分析流程。", name="agent")
    elif route_decision == "report":
        response_message = AIMessage(content="决策：按当前分析上下文重新生成报告。", name="agent")
    elif route_decision == "inquiry_answer":
        response_message = AIMessage(content="决策：根据当前分析上下文回答。", name="agent")
    elif route_decision == "context_switch":
        response_message = AIMessage(content="决策：切换分析上下文。", name="agent")
    else:  # 'normal_chat'
        response_message = AIMessage(content="决策：普通问答。", name="agent")

    update: dict = {
        "messages": [response_message],
        "route_decision": route_decision,
        "report_revision_mode": revision_mode,
    }
    if decision is not None:
        decision_dump = decision.model_dump()
        if _clarification:
            # 模型只给意图而没写澄清问题时，用后端默认问题补齐，
            # 保证 inquiry_answer 真的反问用户而不是当成普通追问。
            decision_dump["clarification_question"] = _clarification
        update["agent_decision"] = decision_dump
    return update


def _apply_frozen_input(runtime: Any, snapshot: Optional[dict]) -> None:
    """把服务端刚提交的冻结文件快照同步到本次 invocation 的输入引用。"""
    if not snapshot:
        return
    identity = getattr(getattr(runtime, "context", None), "trusted_identity", None)
    frozen_input = getattr(identity, "frozen_input", None)
    if frozen_input is None:
        return
    frozen_input.apply_snapshot(
        user_file_id=snapshot.get("input_user_file_id"),
        object_id=snapshot.get("input_object_id"),
        content_hash=snapshot.get("input_file_hash"),
        filename=snapshot.get("input_filename"),
    )


def _context_projection(context_row: dict) -> dict:
    """把上下文行投影成可写入 State 的只读视图。"""
    keys = (
        "analysis_context_id",
        "filename",
        "target",
        "treatment",
        "analysis_question",
        "latest_algorithm_summary",
        "latest_rag_evidence",
        "latest_web_evidence",
        "latest_report_message_id",
        "latest_report_id",
        "latest_report_title",
        "updated_at",
    )
    return {
        key: context_row[key]
        for key in keys
        if context_row.get(key) is not None
    }


def _context_has_confirmed_facts(context_row: dict) -> bool:
    """判断上下文是否已经有可以复用的分析结论或报告。"""
    return bool(
        context_row.get("latest_algorithm_summary")
        or context_row.get("latest_report_message_id")
        or context_row.get("latest_report_title")
    )


def _switch_followup_route(*, intent: Optional[str], context_row: dict) -> str:
    """按原意图和目标上下文的事实决定切换之后的下一步。"""
    if intent == "rerun_analysis":
        return "fold"
    if _context_has_confirmed_facts(context_row):
        return "inquiry_answer"
    return "fold"


def _context_candidates_text(candidates: list) -> str:
    """把歧义候选渲染成一条可读清单。"""
    lines = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        parts = [str(entry.get("filename") or "未记录")]
        if entry.get("target"):
            parts.append(f"目标变量 {entry['target']}")
        if entry.get("treatment"):
            parts.append(f"处理变量 {entry['treatment']}")
        if entry.get("latest_report_title"):
            parts.append(f"报告 {entry['latest_report_title']}")
        lines.append("、".join(parts))
    return "\n".join(f"- {line}" for line in lines)


def _rebuild_context_index(
    previous_index: Any,
    previous_context: Any,
    switched_to_id: Optional[str],
    *,
    limit: int = 8,
) -> list:
    """切换后用内存中的旧索引与旧当前上下文重建历史索引，不额外读库。"""
    entries: list = []
    if isinstance(previous_context, dict) and previous_context.get("analysis_context_id"):
        entries.extend(build_context_index([previous_context]))
    if isinstance(previous_index, list):
        entries.extend(entry for entry in previous_index if isinstance(entry, dict))
    unique: list = []
    seen: set = set()
    for entry in entries:
        context_id = entry.get("analysis_context_id")
        if not context_id or context_id in seen or context_id == switched_to_id:
            continue
        seen.add(context_id)
        unique.append(entry)
    return unique[: max(0, int(limit))]


def _switched_context_state(
    *,
    context_row: dict,
    previous_context: Any,
    previous_index: Any,
    file_snapshot: Optional[dict],
    route: str,
    resolution: dict,
) -> dict:
    """构造切换成功后的状态更新，只保留目标上下文确认过的事实。"""
    projection = _context_projection(context_row)
    update: dict = {
        "analysis_context": projection,
        "analysis_context_index": _rebuild_context_index(
            previous_index,
            previous_context,
            projection.get("analysis_context_id"),
        ),
        "context_resolution": resolution,
        "route_decision": route,
        "report_revision_mode": "normal_generation",
        "messages": [
            AIMessage(content="决策：已切换分析上下文。", name="context_switch")
        ],
        "analysis_parameters": None,
        "causal_analysis_result": None,
        "knowledge_base_result": None,
        "web_search_result": None,
        "postprocess_result": None,
        "preprocess_summary": None,
        "chart_assets": None,
        "report_document": None,
        "deep_agent_algorithm_results": {},
        "deep_agent_action_ledger": {},
        "deep_agent_rag_evidence": {},
        "deep_agent_web_evidence": {},
        "deep_agent_decision": None,
        "deep_agent_structured_response": None,
        "deep_agent_retry_instruction": "",
        "finalization_retry_count": 0,
        "finalization_status": None,
        "finalization_error": None,
    }
    if file_snapshot:
        update["file_summary"] = {
            "user_file_id": file_snapshot.get("input_user_file_id"),
            "object_id": file_snapshot.get("input_object_id"),
            "file_hash": file_snapshot.get("input_file_hash"),
            "filename": file_snapshot.get("input_filename"),
        }
    return update


def _clarification_update(
    *,
    question: Optional[str],
    status: str,
    details: Optional[dict] = None,
) -> dict:
    """构造回到澄清回答的状态更新；不修改 active 分析上下文。"""
    resolution: dict = {
        "status": status,
        "clarification_question": (
            question
            or CLARIFICATION_DEFAULTS.get(status)
            or DEFAULT_CONTEXT_CLARIFICATION
        ),
    }
    if details:
        resolution.update(details)
    return {
        "messages": [
            AIMessage(content="决策：需要先向用户澄清分析上下文。", name="context_switch")
        ],
        "route_decision": "inquiry_answer",
        "context_resolution": resolution,
    }


def _extract_filename_hint(hint: Any) -> Optional[str]:
    """从用户描述里取出疑似文件名。"""
    text = hint if isinstance(hint, str) else ""
    match = _CSV_NAME_PATTERN.search(text)
    return match.group(0) if match else None


async def context_switch_node(
    state: CausalAgentState,
    *,
    runtime: Any,
    config: Any = None,
) -> dict:
    """解析用户提到的历史分析或文件，并切换 Session 默认分析上下文。

    唯一命中才切换；多个命中、文件缺失、没有命中都会回到澄清问题，并且在用户确认前
    不修改 active_analysis_context_id。切换由带 Job fencing 的服务函数在一个事务里
    完成，节点重复执行得到同一个结果。
    """
    run_context = getattr(runtime, "context", None)
    identity = getattr(run_context, "trusted_identity", None)
    decision = state.get("agent_decision")
    decision_dict = decision if isinstance(decision, dict) else {}
    hint = decision_dict.get("context_hint")
    intent = decision_dict.get("intent")
    if identity is None:
        _log_node_degradation("missing_trusted_identity", "clarify")
        return _clarification_update(question=None, status="clarify")

    user_id = int(identity.user_id)
    session_id = str(identity.session_id)
    previous_context = state.get("analysis_context")
    previous_index = state.get("analysis_context_index")
    candidates = match_context_hint(hint, previous_index)
    # 目标可能就是当前上下文：用户没有点名别处，或点名的就是当前上下文时，
    # 不新建上下文，但需要确认当前 Job 的冻结输入与这个上下文一致。
    hint_text = hint.strip() if isinstance(hint, str) else ""
    current_context = previous_context if isinstance(previous_context, dict) else {}
    current_entry = (
        build_context_index([current_context])[0]
        if current_context.get("analysis_context_id")
        else {}
    )
    targets_current = bool(current_entry) and (
        (not hint_text and intent == "rerun_analysis")
        or bool(hint_text and match_context_hint(hint_text, [current_entry]))
    )
    if not candidates and targets_current:
        candidates = [current_entry]
    if len(candidates) > 1:
        return _clarification_update(
            question=(
                "同一份文件下存在多个分析，我不确定您指哪一次：\n"
                f"{_context_candidates_text(candidates)}\n"
                "请说明目标变量或报告标题，我再继续。"
            ),
            status="ambiguous_context",
            details={"candidate_count": len(candidates)},
        )
    if len(candidates) == 1:
        target_context_id = str(candidates[0].get("analysis_context_id") or "")
        if not target_context_id:
            return _clarification_update(question=None, status="clarify")
        result = await asyncio.to_thread(
            switch_to_context,
            user_id=user_id,
            session_id=session_id,
            job_id=str(identity.job_id),
            worker_id=identity.worker_id,
            attempt_count=int(identity.attempt_count),
            lease_epoch=int(identity.lease_epoch),
            context_id=target_context_id,
        )
        status = result.get("status")
        if status == "switched":
            context_row = result["context"]
            _apply_frozen_input(runtime, result.get("file_snapshot"))
            route = _switch_followup_route(intent=intent, context_row=context_row)
            return _switched_context_state(
                context_row=context_row,
                previous_context=previous_context,
                previous_index=previous_index,
                file_snapshot=result.get("file_snapshot"),
                route=route,
                resolution={
                    "status": "switched",
                    "analysis_context_id": target_context_id,
                    "file_changed": bool(result.get("file_changed")),
                    "followup_route": route,
                },
            )
        if status == "file_missing":
            return _clarification_update(
                question=(
                    "这次历史分析对应的文件已经不在您的文件库里，我无法继续使用它。"
                    "请重新上传该文件后再发起分析。"
                ),
                status="file_missing",
            )
        return _clarification_update(question=None, status="inactive_context")

    filename = _extract_filename_hint(hint)
    if filename:
        files = await asyncio.to_thread(find_user_file_by_name, user_id, filename)
        if len(files) == 1:
            result = await asyncio.to_thread(
                create_context_for_new_file,
                user_id=user_id,
                session_id=session_id,
                job_id=str(identity.job_id),
                worker_id=identity.worker_id,
                attempt_count=int(identity.attempt_count),
                lease_epoch=int(identity.lease_epoch),
                user_file_id=int(files[0]["user_file_id"]),
            )
            if result.get("status") in {"created", "reused"}:
                _apply_frozen_input(runtime, result.get("file_snapshot"))
                return _switched_context_state(
                    context_row=(
                        result.get("context")
                        or {
                            "analysis_context_id": result["analysis_context_id"],
                            "filename": (result.get("file_snapshot") or {}).get(
                                "input_filename"
                            ),
                        }
                    ),
                    previous_context=previous_context,
                    previous_index=previous_index,
                    file_snapshot=result.get("file_snapshot"),
                    route="fold",
                    resolution={
                        "status": result.get("status"),
                        "analysis_context_id": result["analysis_context_id"],
                        "followup_route": "fold",
                    },
                )
        if len(files) > 1:
            return _clarification_update(
                question=(
                    f"您的文件库里有多个名为 {filename} 的文件，请说明要使用哪一个。"
                ),
                status="ambiguous_file",
                details={"candidate_count": len(files)},
            )
        if not files:
            return _clarification_update(
                question=None,
                status="file_not_in_library",
                details={"filename": filename},
            )
    return _clarification_update(question=None, status="no_match")

async def _bind_analysis_context(
    state: CausalAgentState,
    runtime: Any,
    *,
    target: Optional[str],
    treatment: Optional[str],
) -> dict:
    """fold 确定参数后把 Job 绑定到正确的分析上下文。

    绑定上下文还没有参数时回填；参数与绑定上下文不同时优先复用同一文件上参数一致的
    历史上下文，没有才新建并切换 active 指针。没有可信 Job 身份时不做任何写入。
    """
    identity = getattr(getattr(runtime, "context", None), "trusted_identity", None)
    if identity is None:
        return {}
    question = _latest_human_text(state).strip()
    result = await asyncio.to_thread(
        apply_fold_context,
        user_id=int(identity.user_id),
        session_id=str(identity.session_id),
        job_id=str(identity.job_id),
        worker_id=identity.worker_id,
        attempt_count=int(identity.attempt_count),
        lease_epoch=int(identity.lease_epoch),
        target=target,
        treatment=treatment,
        analysis_question=question[:MAX_CONTEXT_QUESTION_CHARS] or None,
    )
    if not result:
        return {}
    update: dict = {
        "context_resolution": {
            "status": "fold_bound",
            "analysis_context_id": result.get("analysis_context_id"),
            "action": result.get("action"),
        },
        "analysis_context_index": _rebuild_context_index(
            state.get("analysis_context_index"),
            state.get("analysis_context"),
            result.get("analysis_context_id"),
        ),
    }
    context = result.get("context")
    if isinstance(context, dict):
        update["analysis_context"] = context
    return update

class foldQuery(BaseModel):
    """只从用户对话中提取因果分析所需的关键参数。"""
    target: Optional[str] = Field(
        None,
        description="从用户对话中识别出的目标变量(target)或结果变量(outcome)。如果未提及，则留空。"
    )
    treatment: Optional[str] = Field(
        None,
        description="从用户对话中识别出的处理变量(treatment)或干预变量(intervention)。如果未提及，则留空。"
    )

## fold节点用到的函数
from Agent.Processing.fold_processing import get_data_summary
from Agent.Processing.fold_verify import validate_analysis
from Agent.Processing.nonlinearity import measure_nonlinearity
from Agent.Report.assets import (
    build_causal_graph_model,
    build_chart_assets,
    build_report_resources,
    coerce_chart_assets,
    describe_chart_asset,
)
from Agent.Report.document import (
    ChartAsset,
    ReportDraft,
    ReportDocument,
    build_report_document,
    report_document_summary,
)


FILE_LOAD_INTERRUPT_MESSAGE = (
    "当前任务冻结的 CSV 文件无法读取或解析。请取消任务，重新上传可用的 CSV 文件后再试。"
)


def _normalize_optional_llm_text(value: str | None) -> str | None:
    """将 LLM 可空文本中的明确缺失哨兵转换为 None。"""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized.casefold() in {"none", "null"}:
        return None
    return value


async def fold_node(
    state: CausalAgentState,
    llm: ChatOpenAI,
    *,
    runtime: Any = None,
) -> dict:
    """
    文件加载、解析与验证节点。
    1.  使用LLM从对话中提取目标和处理变量。
    2.  按 Job 冻结的对象 ID 从数据库加载文件内容。
    3.  运行 get_data_summary 进行全面的数据分析。
    4.  调用 validate_analysis 进行严格的条件验证。
    5.  根据验证结果，决策进入 'preprocess' 节点或 'ask_human' 节点。
    """
    user_id = state.get("user_id")
    file_summary = state.get("file_summary") or {}
    input_user_file_id = file_summary.get("user_file_id")
    input_object_id = file_summary.get("object_id")

    if not input_user_file_id or not input_object_id:
        return {
            "messages": [
                AIMessage(
                    content="请先上传并选择一个 CSV 文件，再开始因果分析。",
                    name="fold",
                )
            ],
            "fold_decision": "normal_chat",
        }

    # 1. 使用 LLM 提取分析参数，不从自然语言选择文件
    prompt = ChatPromptTemplate.from_messages([
            ("system",
            """你是一个智能助手，你的任务是从用户的最新消息中识别出以下信息，并以JSON格式返回：
            1. 用户关心的目标变量 (target/outcome)。
            2. 用户想要评估效果的处理变量 (treatment/intervention)。

            文件已经由当前 Job 的冻结输入确定，严禁从自然语言选择文件。
            如果用户没有提到目标或处理变量，请将对应字段设为 null。

            示例:
            - 用户: "帮我分析销售额和促销活动的关系..."
            -> 提取: `target='销售额'`, `treatment='促销活动'`
            - 用户: "分析一下我的数据，看看是什么影响了客户流失"
            -> 提取: `target='客户流失'`, `treatment=null`
            
            你必须严格按照 `foldQuery` 的 schema 返回一个 JSON 对象。
            **绝对不要**在你的回复中包含任何Markdown格式或解释性文字。
            **重要：如果某个字段为空，请使用 JSON 的 null 值，而不是字符串 "None"。**

            示例输出（所有字段都有值）:
            {{
                "target": "销售额",
                "treatment": "促销活动"
            }}
            
            示例输出（部分字段为空）:
            {{
                "target": "客户流失",
                "treatment": null
            }}
            
            """),
            MessagesPlaceholder(variable_name="messages"),
        ])
    try:
        structured_response = await ainvoke_structured(
            llm=llm,
            schema=foldQuery,
            prompt=prompt,
            inputs={"messages": llm_prompt_messages(state["messages"])},
            node_name="fold",
        )

        target = _normalize_optional_llm_text(structured_response.target)
        treatment = _normalize_optional_llm_text(structured_response.treatment)
        
    except StructuredOutputError:
        _log_node_degradation("structured_output_error", "empty_parameters")
        target = None
        treatment = None

    try:
        file_row = await asyncio.to_thread(
            require_frozen_file_for_job,
            user_id,
            state.get("job_id", ""),
            input_user_file_id,
            input_object_id,
        )
        file_content_str = file_row["file_content"].decode("utf-8")
        df = await asyncio.to_thread(pd.read_csv, io.StringIO(file_content_str))
        data_summary = await asyncio.to_thread(get_data_summary, df)
    
    except Exception:
        _log_node_degradation(
            "file_load_error",
            "waiting_input",
            exc_info=True,
        )
        
        # 使用 interrupt() 暂停并等待用户输入
        user_response = interrupt(FILE_LOAD_INTERRUPT_MESSAGE)
        new_message = HumanMessage(content=resume_value_to_message_content(user_response))
        
        return {"messages": [new_message], "fold_decision": "agent"}

    state['analysis_parameters'] = data_summary
    # 刻意放在上面的 try 块之外：那块抛异常会走 interrupt() 挂起等用户输入。
    # measure_nonlinearity 内部全兜、绝不抛异常，但仍不放进那条路径。
    state['analysis_parameters']['nonlinearity'] = measure_nonlinearity(
        df, state['analysis_parameters']
    )
    file_summary = {
        "user_file_id": input_user_file_id,
        "object_id": input_object_id,
        "file_hash": file_summary.get("file_hash"),
        "filename": file_summary.get("filename"),
        "rows": data_summary.get("n_rows"),
        "columns": data_summary.get("columns", []),
    }
    state["file_summary"] = file_summary
    
    # 运行确定性验证
    is_ready, issues, recommends = await asyncio.to_thread(
        validate_analysis,
        data_summary, 
        target=target,
        treatment=treatment,
    )

    # 根据验证结果决策
    if is_ready == 0 or is_ready == 1:
        state['analysis_parameters'].update({"target": target, "treatment": treatment})

        # 收集需要返回的新消息
        new_messages = []
        recommend_message = AIMessage(content = "决策：信息完备，进入预处理节点。", name="fold")
        new_messages.append(recommend_message)

        # 针对建议，生成提示
        if recommends:
            recommend_message = AIMessage(content=f"决策：信息完备，进入预处理节点。提示：\n- {recommends}")
            new_messages.append(recommend_message)
        
        update = {"messages": new_messages,
                "analysis_parameters": state['analysis_parameters'],
                "file_summary": file_summary,
                "tool_call_request": False,
                "fold_decision": "preprocess",
                }
        # 参数确定后再把 Job 绑定到正确的分析上下文；同一文件、不同目标变量
        # 会得到不同的上下文，而不是覆盖历史上下文。
        update.update(
            await _bind_analysis_context(
                state,
                runtime,
                target=target,
                treatment=treatment,
            )
        )
        return update
    
    else:
        # 对于issue中有存在变量缺失的情况的，进行修正询问，对于数据有问题，进行数据补充询问
        has_param_issue = any("目标变量" in issue or "处理变量" in issue for issue in issues)
        has_data_quality_issue = any("缺失" in issue or "样本量" in issue or "常数列" in issue or "高基数" in issue or "ID列" in issue for issue in issues)

        call_to_action = "请您根据上述问题进行调整。" # 通用备用方案
        if has_param_issue:
            call_to_action = "请您根据上述问题，明确或修正'目标变量'和'处理变量'的指定。"
        elif has_data_quality_issue:
            call_to_action = "您的数据似乎存在一些质量问题。请您考虑对数据进行清洗，或上传一份新的文件。"
        
        columns_list = data_summary.get('columns', [])
        question = (
            "为了开始因果分析，我需要您的帮助来解决以下问题：\n"
            f"- {issues}\n\n"
            f"作为参考，您的数据中包含以下可用列：\n`{', '.join(columns_list)}`\n\n"
            f"**{call_to_action}**"
        )
        
        # interrupt() 会立即暂停节点执行，返回 question 给调用者
        # 当用户提供输入后，interrupt() 会返回用户的输入值
        user_response = interrupt(question)
        
        # 将用户的响应添加到消息历史，并返回一个路由消息
        # 让 fold_router 知道需要回到 agent 重新判断
        return {
            "messages": [
                HumanMessage(content=resume_value_to_message_content(user_response)),
                AIMessage(content="决策：已收到用户输入，返回 agent 重新判断", name="fold")
            ],
            "analysis_parameters": state['analysis_parameters'],
            "file_summary": state.get("file_summary"),
            "fold_decision": "agent",
        }


async def preprocess_node(state: CausalAgentState, llm: ChatOpenAI) -> dict:
    """
    项目预处理模块:
    1.  从状态(state)中加载 DataFrame 和数据摘要。
    2.  调用 `build_chart_assets` 生成结构化图表资源。
        - 图表资源生成失败时会跳过此步并记录日志，不阻断后续流程。
    3.  调用 LLM 对数据摘要进行自然语言总结。
    4.  将图表和总结存入状态，然后直接进入下一步。
    """
    analysis_parameters = state.get("analysis_parameters", {})

    file_summary = state.get("file_summary") or {}
    input_user_file_id = file_summary.get("user_file_id")
    input_object_id = file_summary.get("object_id")

    if (
        not analysis_parameters
        or not input_user_file_id
        or not input_object_id
    ):
        error_msg = "无法执行预处理，因为数据或其摘要信息在状态中丢失。"
        raise RuntimeError(error_msg)

    file_row = await asyncio.to_thread(
        require_frozen_file_for_job,
        state.get("user_id"),
        state.get("job_id", ""),
        input_user_file_id,
        input_object_id,
    )
    df = await asyncio.to_thread(
        pd.read_csv,
        io.BytesIO(file_row["file_content"]),
    )
    # 生成结构化图表资源
    try:
        chart_assets = await asyncio.to_thread(build_chart_assets, df, analysis_parameters)
        state["chart_assets"] = {
            asset_key: asset.model_dump(mode="json")
            for asset_key, asset in chart_assets.items()
        }
    except Exception:
        _log_node_degradation(
            "chart_asset_error",
            "skip_chart_assets",
            exc_info=True,
        )
        # 图表资源生成失败不阻断流程，记录日志后继续执行后续步骤
    
    # 3. 调用LLM进行自然语言总结
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system",
             """
             system role: {system_role}
             mission:
            你的任务是根据提供的数据摘要信息，为即将进行的因果分析撰写一段简洁明了的自然语言总结。

            # 数据摘要信息:
            {data_summary}

            # 你的任务:
            1.  **开篇总结**: 简要说明数据集的规模（行数和列数）。
            2.  **目标变量和处理变量的摘录**: 对输入数据中的“target”和“treatment”进行摘取，并告知用户目前处理的变量是这两个变量。
            3.  **风险提示**: 提及数据中存在的潜在问题，例如高缺失值列、常数列、高基数分类变量或疑似ID列。
            4.  **结论**: 给出一个总体评价，说明数据是否已准备好进行下一步的因果分析。
            5.  **线性/非线性说明**: 根据 `nonlinearity` 字段说明变量间关系以线性还是非线性机制为主及强度；`verdict` 为 `linear` 只表示未检出显著非线性结构，不等于确定线性；`verdict` 为 `insufficient` 时说明本次未能评估。

            请使用清晰、专业的语言，让非技术人员也能理解数据的基本状况。
            """),
            ("human", "请根据上述指示和提供的数据摘要，生成总结报告。")
        ]
    )
    
    
    runnable = prompt | llm | StrOutputParser()
    
    preprocess_summary = await runnable.ainvoke({
        "data_summary": json.dumps(analysis_parameters, indent=2, ensure_ascii=False),
        "system_role": data_prompt()
    })
    # 4. 更新状态并返回新消息
    state["preprocess_summary"] = preprocess_summary
    
    summary_message = AIMessage(
        content= "决策：数据预处理完成，进入工具处理路由",
        name="preprocess"
    )

    # 只返回新消息和需要更新的状态
    return {
        "messages": [summary_message],
        "preprocess_summary": state["preprocess_summary"],
        "chart_assets": state.get("chart_assets", {}),
    }


from Agent.knowledge_base.query_rag import format_rag_summary_for_prompt
from Agent.tool_node.rag_query_task import rag_query_task
from Agent.tool_node.rag_questions import get_rag_questions
from Agent.tool_node.mcp_tool_call_adapter import normalize_mcp_tool_call_message
from Agent.tool_node.tool_message_adapter import (
    attach_tool_call_metadata,
    latest_ai_tool_call_ids,
    latest_matching_tool_result,
    parse_tool_message_json,
)


def _mcp_tool_name(tool: Any) -> str | None:
    """从 MCP/LangChain tool 对象或 OpenAI-style dict 中读取工具名。"""
    if isinstance(tool, dict):
        function = tool.get("function", {})
        return function.get("name") if isinstance(function, dict) else None
    return getattr(tool, "name", None)


def _has_mcp_tool(mcp_tools: list, tool_name: str) -> bool:
    """判断当前 MCP tool 列表是否包含指定工具。"""
    return any(_mcp_tool_name(tool) == tool_name for tool in mcp_tools)


def _explicit_direct_lingam_requested(state: CausalAgentState) -> bool:
    """检测用户是否明确要求使用 DirectLiNGAM。"""
    latest_text = _latest_human_text(state)
    normalized = latest_text.lower()
    compact = "".join(char for char in normalized if char.isalnum())
    return any(
        token in normalized
        for token in ("directlingam", "direct lingam", "direct-lingam", "direct_lingam")
    ) or "directlingam" in compact


def _direct_mcp_tool_call(
    tool_name: str,
    state: CausalAgentState,
    mcp_tools: list,
) -> AIMessage:
    """为确定性工具选择构造 ToolNode 可消费的标准 AIMessage。"""
    ai_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": tool_name,
                "args": {},
                "id": f"planner-{tool_name}-1",
                "type": "tool_call",
            }
        ],
    )
    return normalize_mcp_tool_call_message(ai_message, state, mcp_tools)


async def mcp_planner_node(state: CausalAgentState, llm: ChatOpenAI, mcp_tools: list) -> dict:
    """强制模型从可用 MCP tools 中选择一个，并返回标准 Tool Call。"""
    if not mcp_tools:
        raise RuntimeError("No MCP tools are available for causal analysis.")
    if (
        _explicit_direct_lingam_requested(state)
        and _has_mcp_tool(mcp_tools, "causal_direct_lingam")
    ):
        return {
            "messages": [
                _direct_mcp_tool_call("causal_direct_lingam", state, mcp_tools)
            ]
        }

    # 固定 tool_choice 的请求使用关闭 Thinking 的隔离副本。
    planner_llm = llm.model_copy(
        update={
            "extra_body": {
                **(llm.extra_body or {}),
                "thinking": {"type": "disabled"},
            }
        }
    )
    mcp_llm = planner_llm.bind_tools(
        mcp_tools,
        tool_choice="required",
        parallel_tool_calls=False,
    )
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """你是 MCP 因果分析工具规划器。

            你的任务是你必须通过 tool call 选择一个最合适的因果分析工具；并且通过 tool calling 协议返回tool_calls
            返回不要普通回答，不要输出 JSON 文本。
            根据用户需求、数据摘要和预处理总结选择工具.""",
        ),
        (
            "human",
            "用户上下文：{messages}\n\n数据摘要：{data_summary}\n\n预处理总结：{preprocess_summary},请完成任务。",
        ),
    ])

    prompt_value = await prompt.ainvoke({
        "messages": llm_prompt_messages(state.get("messages", [])),
        "data_summary": json.dumps(state.get("analysis_parameters", {}), indent=2, ensure_ascii=False),
        "preprocess_summary": state.get("preprocess_summary", ""),
    })
    ai_message = await mcp_llm.ainvoke(prompt_value.to_messages())
    ai_message = normalize_mcp_tool_call_message(ai_message, state, mcp_tools)
    return {"messages": [ai_message]}


async def mcp_result_parser_node(state: CausalAgentState) -> dict:
    """解析 MCP ToolNode 产生的 ToolMessage，并把结果注入状态。"""
    messages = state.get("messages", [])
    latest_tool_message, latest_tool_call = latest_matching_tool_result(messages)
    if latest_tool_message is None:
        existing_result = state.get("causal_analysis_result")
        if isinstance(existing_result, dict) and existing_result.get("success") is False:
            return {
                "causal_analysis_result": existing_result,
                "tool_call_request": False,
            }
        return {
            "causal_analysis_result": {
                "success": False,
                "error": "MCP 工具没有返回与本次调用匹配的 ToolMessage。",
                "error_type": "MCPProtocolError",
            },
            "tool_call_request": False,
        }

    parsed = parse_tool_message_json(latest_tool_message)
    if parsed.get("error_type") == "ToolMessageProtocolError":
        parsed = {
            "success": False,
            "error": parsed.get("error", "MCP ToolMessage 不是合法 JSON。"),
            "error_type": "MCPProtocolError",
        }
    if type(parsed.get("success")) is not bool:
        _log_node_degradation("protocol_error", "mcp_failure_result")
        parsed = {
            "success": False,
            "error": "MCP 返回结果不符合协议：缺少布尔型 success 字段。",
            "error_type": "MCPProtocolError",
        }
    elif parsed.get("success") is True and set(parsed) == {"success", "data"}:
        _log_node_degradation("protocol_error", "mcp_failure_result")
        parsed = {
            "success": False,
            "error": "MCP 返回结果不符合协议：未返回结构化因果分析结果。",
            "error_type": "MCPProtocolError",
        }

    result = attach_tool_call_metadata(
        parsed,
        latest_tool_message,
        latest_tool_call,
    )
    return {
        "causal_analysis_result": result,
        "tool_call_request": bool(parsed.get("success")),
    }


async def rag_question_planner_node(
    state: RagSubgraphState,
    llm: ChatOpenAI,
    rag_tools: list,
    rag_available: bool = True,
) -> dict:
    """生成 RAG 问题，并通过私有 route 字段决定是否调用工具。"""
    if not rag_available or not rag_tools:
        return {
            "rag_route": "finish",
            "rag_status": "unavailable",
            "rag_questions": [],
            "rag_parse_result": build_rag_degradation_result(
                "RAG 知识库未初始化或工具未注册。"
            ),
        }

    max_questions = 3
    try:
        rag_questions = await get_rag_questions(state, llm, max_questions=max_questions)
    except StructuredOutputError as exc:
        return {
            "rag_route": "finish",
            "rag_status": "unavailable",
            "rag_questions": [],
            "rag_parse_result": build_rag_degradation_result(str(exc)),
        }
    tool_name = "rag_enrichment_search"
    tool_name = getattr(rag_tools[0], "name", tool_name)

    ai_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": tool_name,
                "args": {
                    "questions": rag_questions,
                    "max_results": 5,
                },
                "id": "rag_enrichment_search_1",
            }
        ],
    )
    return {
        "messages": [ai_message],
        "rag_questions": rag_questions,
        "rag_route": "call_tool",
        "rag_status": "available",
    }


async def rag_result_parser_node(state: RagSubgraphState) -> dict:
    """解析 RAG ToolMessage，只写入子图私有的中间结果。"""
    messages = state.get("messages", [])
    latest_tool_message, latest_tool_call = latest_matching_tool_result(messages)
    if latest_tool_message is None:
        error_message = (
            "No RAG tool result was produced."
            if latest_ai_tool_call_ids(messages)
            else "RAG ToolMessage 与本次调用不匹配。"
        )
        return {
            "rag_route": "finish",
            "rag_status": "protocol_error",
            "rag_tool_message": None,
            "rag_parse_result": build_rag_degradation_result(
                error_message,
                status="protocol_error",
            ),
        }

    parsed = parse_tool_message_json(latest_tool_message)
    if parsed.get("error_type") == "ToolMessageProtocolError":
        parsed = build_rag_degradation_result(
            parsed.get("error", "RAG ToolMessage 不是合法 JSON。"),
            status="protocol_error",
        )
        parsed["error_type"] = "ToolMessageProtocolError"
        rag_status = "protocol_error"
    elif type(parsed.get("success")) is not bool:
        parsed = build_rag_degradation_result(
            "RAG 工具返回结果缺少布尔型 success 字段。",
            status="protocol_error",
        )
        rag_status = "protocol_error"
    elif not parsed.get("success"):
        error_type = parsed.get("error_type")
        parsed = build_rag_degradation_result(
            parsed.get("error", "RAG 工具返回失败结果。"),
            status="unavailable",
        )
        if error_type:
            parsed["error_type"] = error_type
        rag_status = "unavailable"
    else:
        parsed["status"] = "available"
        rag_status = "available"
    result = attach_tool_call_metadata(
        parsed,
        latest_tool_message,
        latest_tool_call,
    )
    return {
        "rag_route": "finish",
        "rag_status": rag_status,
        "rag_tool_message": latest_tool_message,
        "rag_parse_result": result,
    }


async def rag_finalize_node(state: RagSubgraphState) -> dict:
    """统一生成子图最终输出，确保任何降级都具有稳定结构。"""
    status = state.get("rag_status")
    if status not in {"available", "unavailable", "protocol_error"}:
        status = "unavailable"

    parsed_result = state.get("rag_parse_result")
    if isinstance(parsed_result, dict):
        rag_output = dict(parsed_result)
        if rag_output.get("success") is False:
            if rag_output.get("status") not in {"unavailable", "protocol_error"}:
                rag_output["status"] = status
            rag_output.setdefault("summary", RAG_DEGRADATION_SUMMARY)
            rag_output.setdefault("questions", [])
            rag_output.setdefault("evidence_count", 0)
            rag_output.setdefault("error", "RAG 子图未生成有效结果。")
        else:
            rag_output.setdefault("status", "available")
            status = "available"
    else:
        rag_output = build_rag_degradation_result(
            "RAG 子图未生成有效解析结果。",
            status=status,
        )

    return {
        "rag_route": "finish",
        "rag_status": status,
        "rag_output": rag_output,
    }


async def rag_subgraph_adapter_node(
    state: CausalAgentState,
    *,
    rag_subgraph: Any,
    runtime: Runtime,
    config: RunnableConfig,
) -> dict:
    """把父 State 映射为 RAG 子图输入，并只投影最终结果回父图。"""
    rag_input: RagSubgraphState = {
        "messages": list(state.get("messages", [])),
        "analysis_parameters": state.get("analysis_parameters"),
        "preprocess_summary": state.get("preprocess_summary"),
        "causal_analysis_result": state.get("causal_analysis_result"),
    }
    subgraph_result = await rag_subgraph.ainvoke(
        rag_input,
        config=config,
        context=getattr(runtime, "context", None),
    )
    rag_output = (
        subgraph_result.get("rag_output")
        if isinstance(subgraph_result, dict)
        else None
    )
    if not isinstance(rag_output, dict):
        raise RuntimeError("RAG 子图未返回有效 rag_output。")
    status = rag_output.get("status")
    if status != "available":
        questions = rag_output.get("questions")
        question_count = len(questions) if isinstance(questions, list) else 0
        evidence_count = rag_output.get("evidence_count")
        if not isinstance(evidence_count, int) or isinstance(evidence_count, bool):
            evidence_count = 0
        log_event(
            LOGGER,
            "rag.enrichment.degraded",
            details={
                "status": status if status in {"unavailable", "protocol_error"} else "unavailable",
                "reason_code": "protocol_error" if status == "protocol_error" else "unavailable",
                "question_count": question_count,
                "evidence_count": max(0, evidence_count),
            },
        )
    return {"knowledge_base_result": rag_output}


async def web_search_planner_node(state: WebSearchState, llm: ChatOpenAI) -> dict:
    """两步生成联网搜索 query：先提炼具体问题，再针对问题生成 query。"""
    try:
        question = await generate_research_question(state, llm)
        r = await get_web_search_query(state, llm, question["question"])
        return {
            "planner": {
                "success": True,
                "research_question": question["question"],
                "query": r["query"],
                "query_en": r.get("query_en", ""),
                "reason": r.get("reason", ""),
                "error": None,
            }
        }
    except StructuredOutputError:
        _log_node_degradation(
            "structured_output_error",
            "web_search_planner_fallback",
        )
        return {
            "planner": {
                "success": False,
                "research_question": "",
                "query": "",
                "query_en": "",
                "reason": "",
                "error": "无法生成搜索 query",
            }
        }


async def academic_search_node(state: WebSearchState) -> dict:
    """单节点：一次 SearXNG 查询统一走 arxiv/crossref/openalex，按引擎分组各取 top-3。"""
    if not state.get("planner", {}).get("success"):
        return {
            "search": {
                "success": False,
                "results": [],
                "number_of_results": 0,
                "error": None,
            }
        }
    search_query = state["planner"].get("query_en") or state["planner"].get("query", "")
    payload = await asyncio.to_thread(web_search, search_query)
    results = _merge_by_engine_top3(payload["results"])
    return {
        "search": {
            "success": True,
            "results": results,
            "number_of_results": len(results),
            "error": None,
        }
    }


async def web_search_result_parser_node(state: WebSearchState) -> dict:
    """纯 snippet 出口：把 search 结果投影为 web_search_result，无 LLM 总结。"""
    p = state.get("planner", {})
    search = state.get("search", {})
    results = search.get("results", [])
    content = [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "text": r.get("snippet", ""),
            "source": "snippet",
            "origin": r.get("source", ""),
        }
        for r in results
    ]
    return {
        "web_search_result": {
            "success": bool(search.get("success")),
            "query": p.get("query", ""),
            "results": results,
            "content": content,
        }
    }


# 环路检测模块
from Agent.Postprocessing.cycles_check.detect_cycles import detect_cycles
from Agent.Postprocessing.cycles_check.extract_causal_return import extract_adjacency_matrix
from Agent.Postprocessing.cycles_check.fix_cycles import fix_cycles_with_llm

# 边评估模块
from Agent.Postprocessing.evaluate_edge.evaluate_edge_llm import evaluate_edges_with_llm
from Agent.Postprocessing.evaluate_edge.edge_utils import extract_critical_edges


def _matrix_convention_for_analysis(analysis_result: Dict[str, Any]) -> str:
    """根据显式字段、算法标识或 OLC 元数据确定邻接矩阵方向。"""
    explicit_convention = str(
        analysis_result.get("matrix_convention", "")
    ).strip().lower()
    if explicit_convention in {"target_to_source", "causallearn", "olc"}:
        return explicit_convention

    algorithm = str(analysis_result.get("algorithm", "")).strip().lower()
    raw_results = analysis_result.get("raw_results", {})
    if algorithm == "direct_lingam":
        return "target_to_source"
    if algorithm == "olc" or "coefficient_matrix" in raw_results:
        return "olc"
    return "causallearn"


def _as_revised_graph(
    graph_nodes: List[Dict[str, Any]],
    revised_edges: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """把内部 EdgeRecord 集合序列化成前端可直接渲染的 vis-network 图。"""
    vis_edges = []
    for edge in revised_edges:
        edge_type = edge.get("edge_type", "directed")
        if edge_type == "bidirected":
            arrows = "to,from"
        elif edge_type in {"directed", "partially_oriented"}:
            arrows = "to"
        else:
            arrows = ""

        vis_edge = {
            "from": edge["source"],
            "to": edge["target"],
            "arrows": arrows,
            "dashes": edge_type in {"undirected", "partially_oriented"},
            "label": edge.get("label", ""),
        }
        if "weight" in edge:
            vis_edge["weight"] = edge["weight"]
        vis_edges.append(vis_edge)

    return {"nodes": list(graph_nodes), "edges": vis_edges}


def _without_removed_edges(
    candidate_edges: List[Dict[str, Any]],
    removed_edges: List[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """从 LLM 边评估候选中排除已经由环路修复真实删除的边。"""
    removed_keys = set(removed_edges)
    return [
        edge
        for edge in candidate_edges
        if (edge.get("source"), edge.get("target")) not in removed_keys
    ]


async def postprocess_node(state: CausalAgentState, llm: ChatOpenAI) -> dict:
    """
    后处理模块：
    1. 提取并验证因果图结构
    2. 环路检测和修正
    3. LLM辅助评估关键边的合理性
    4. 准备修正记录和格式化数据供报告使用
    
    技术说明：
        - 使用networkx进行图结构分析
        - 使用LLM进行环路修正和边评估决策
        - 所有修正操作都会被详细记录
    """
    degradation_logged = False

    def record_degradation(
        reason_code: str,
        affected_count: Any,
        *,
        exc_info: Any = None,
    ) -> None:
        """同一次后处理最多记录一个最终降级事件。"""
        nonlocal degradation_logged
        if degradation_logged:
            return
        try:
            safe_count = max(0, int(affected_count))
        except (TypeError, ValueError):
            safe_count = 0
        log_event(
            LOGGER,
            "job.postprocess.degraded",
            details={
                "reason_code": reason_code,
                "affected_count": safe_count,
            },
            exc_info=exc_info,
        )
        degradation_logged = True

    try:
        # 提取原始因果图
        analysis_result = state["causal_analysis_result"]
        adjacency_matrix, node_names = extract_adjacency_matrix(analysis_result)
        
        # 如果提取失败，返回错误
        if adjacency_matrix.size == 0:
            error_msg = "无法从分析结果中提取有效的因果图数据。"
            record_degradation("invalid_result", 0)
            # 只返回新消息和错误状态
            return {
                "messages": [AIMessage(content=f"决策：{error_msg}", name="postprocess")],
                "postprocess_result": {"error": error_msg}
            }
        
        # 创建原始图的副本用于修正
        working_matrix = adjacency_matrix.copy()
        matrix_convention = _matrix_convention_for_analysis(analysis_result)
        cycle_removed_edges: List[Tuple[str, str]] = []
        
        # 环路检测和修正
        has_cycle, cycles = detect_cycles(
            working_matrix,
            node_names,
            matrix_convention=matrix_convention,
        )
        if has_cycle:
            working_matrix, cycle_removed_edges = await asyncio.to_thread(
                fix_cycles_with_llm,
                working_matrix, 
                cycles, 
                node_names,
                llm, 
                state,
                matrix_convention=matrix_convention,
            )
            # 再次检测以确认环路已被消除
            has_cycle_after, _ = detect_cycles(
                working_matrix,
                node_names,
                matrix_convention=matrix_convention,
            )
            if has_cycle_after:
                record_degradation("postprocess_failed", len(cycles))
        
        # LLM评估关键边
        critical_edges, edge_debug_info = extract_critical_edges(analysis_result)
        candidate_edge_count = edge_debug_info.get("candidate_edge_count", 0)
        normalized_edge_count = edge_debug_info.get("normalized_edge_count", 0)
        if candidate_edge_count != normalized_edge_count:
            error_msg = (
                "因果边结构校验失败："
                f"候选边 {candidate_edge_count} 条，仅成功规范化 {normalized_edge_count} 条。"
            )
            try:
                affected_count = int(candidate_edge_count) - int(normalized_edge_count)
            except (TypeError, ValueError):
                affected_count = 0
            record_degradation("invalid_result", affected_count)
            return {
                "messages": [
                    AIMessage(
                        content=f"后处理遇到问题: {error_msg}\n\n将使用原始分析结果继续生成报告。",
                        name="postprocess",
                    )
                ],
                "postprocess_result": {
                    "error": error_msg,
                    "original_graph": analysis_result.get("data", {}),
                    "edge_evaluation_debug": edge_debug_info,
                },
            }
        critical_edges = _without_removed_edges(critical_edges, cycle_removed_edges)
        
        edge_evaluations: Dict[str, Any] = {}
        if critical_edges:
            edge_evaluations = await asyncio.to_thread(evaluate_edges_with_llm, critical_edges, state, llm)
        else:
            edge_evaluations = {
                "schema_version": "edge_evaluation_v2",
                "decisions": [],
                "revised_edges": [],
                "revision_summary": "",
                "confidence": "low",
            }

        serialized_cycle_removals = [
            {"source": source, "target": target}
            for source, target in cycle_removed_edges
        ]
        edge_evaluations = {
            **edge_evaluations,
            "cycle_removed_edges": serialized_cycle_removals,
        }
        
        
        revised_edges = edge_evaluations.get(
            "revised_edges",
            edge_evaluations.get("decision", []),
        )
        revision_summary = edge_evaluations.get(
            "revision_summary",
            edge_evaluations.get("reason", ""),
        )
        if cycle_removed_edges:
            cycle_summary = "环路修订删除边：" + "、".join(
                f"{source} -> {target}" for source, target in cycle_removed_edges
            )
            revision_summary = "；".join(
                summary for summary in (cycle_summary, revision_summary) if summary
            )

        # 准备结构化输出
        postprocess_result = {
            "original_graph": state["causal_analysis_result"].get("data", {}),
            "revised_graph": _as_revised_graph(
                analysis_result.get("data", {}).get("nodes", []),
                revised_edges,
            ),
            "revision_summary": revision_summary,
            "edge_evaluation": edge_evaluations,
            "edge_evaluation_debug": {
                **edge_debug_info,
                "ran": bool(critical_edges),
                "schema_version": edge_evaluations.get("schema_version", ""),
                "decision_count": len(edge_evaluations.get("decisions", [])),
                "revised_edge_count": len(revised_edges),
                "cycle_removed_count": len(cycle_removed_edges),
                "confidence": edge_evaluations.get("confidence", ""),
            },
            "had_cycles": has_cycle,
            "num_cycles_fixed": len(cycle_removed_edges),
            "matrix_convention": matrix_convention,
        }
        
        state["postprocess_result"] = postprocess_result
        
        
        # 收集需要返回的新消息
        new_messages = []
        
        # 如果有环路被修正，添加额外说明
        if has_cycle:
            explanation = f"\n\n**注意**：原始图中检测到 {len(cycles)} 个环路，部分已通过LLM辅助决策进行修正。理由如下：{revision_summary}"
            new_messages.append(AIMessage(content=explanation, name="postprocess"))
        
        new_messages.append(AIMessage(
            content="决策：后处理完成，准备进入报告生成阶段",
            name="postprocess"
        ))

        # 只返回新消息和后处理结果
        return {
            "messages": new_messages,
            "postprocess_result": state["postprocess_result"]
        }
        
    except Exception as e:
        record_degradation("postprocess_failed", 0, exc_info=True)
        postprocess_result = {"error": str(e) + f"\n\n将使用原始分析结果继续生成报告。"}
        # 异常处理：记录错误但不中断流程
        error_message = AIMessage(
            content=f"后处理遇到问题: {str(e)}\n\n将使用原始分析结果继续生成报告。",
            name="postprocess"
        )
        
        # 只返回新消息和错误状态
        return {
            "messages": [error_message],
            "postprocess_result": postprocess_result
        }

## 调用元数据
def _causal_method_context_for_report(analysis_result: Dict[str, Any]) -> str:
    """为报告节点生成算法专用解释边界。"""
    if not isinstance(analysis_result, dict):
        return "No structured causal analysis result is available."

    algorithm = str(analysis_result.get("algorithm", "")).strip().lower()
    if algorithm != "direct_lingam":
        return "No additional algorithm-specific reporting guidance is required."

    implementation = analysis_result.get("implementation", {})
    parameters = analysis_result.get("parameters", {})
    raw_results = analysis_result.get("raw_results", {})
    diagnostics = analysis_result.get("diagnostics", {})
    causal_order_names = raw_results.get("causal_order_names", [])
    causal_order_text = (
        " -> ".join(str(name) for name in causal_order_names)
        if isinstance(causal_order_names, list) and causal_order_names
        else "not available"
    )

    return (
        "DirectLiNGAM reporting guidance:\n"
        f"- Implementation: causal-learn {implementation.get('version', 'unknown')} "
        f"with embedded LiNGAM {implementation.get('embedded_version', 'unknown')}.\n"
        f"- Parameters: measure={parameters.get('measure', 'unknown')}.\n"
        f"- Samples/features: n_samples={diagnostics.get('n_samples', 'unknown')}, "
        f"n_features={diagnostics.get('n_features', 'unknown')}.\n"
        f"- Matrix convention: {analysis_result.get('matrix_convention', 'target_to_source')}; "
        "B[target, source] means source -> target.\n"
        f"- Estimated causal order: {causal_order_text}.\n"
        "- Required assumptions: continuous numeric variables, linear structural equation model, "
        "non-Gaussian and mutually independent errors, acyclic causal graph, and no unmodeled "
        "latent confounders among the observed variables.\n"
        "- Interpretation rule: weighted directed edges are candidate causal relations under these "
        "assumptions, not experimentally verified causal facts."
    )


def _report_language_instruction() -> str:
    """返回报告语言优先级，交由模型按用户请求决定具体语言。"""

    return (
        "报告输出语言规则（按优先级）：用户明确指定报告语言时，严格使用该语言；"
        "未指定时，使用用户当前请求的主要语言；无法判断时默认使用中文。"
        "不要因为系统提示、知识库、工具结果或变量名的语言改变报告语言。"
    )


REPORT_GRAPH_ASSET_KEY = "graph_main"


def _report_revision_instruction(regeneration: bool) -> str:
    """按报告模式给出受控说明：修订只能复用当前上下文已确认的事实。"""
    if not regeneration:
        return "本次是首次生成报告，请根据当前分析结果完整撰写。"
    return (
        "本次是按当前分析上下文重新生成完整报告。只能使用上面列出的当前分析上下文事实"
        "（文件、分析参数、算法结果、因果边和检索证据）；不得修改算法图，不得新增上下文"
        "中没有的因果结论、数值或数据；上下文没有图表资源时不要生成 chart 块。"
    )


def _report_graph_asset(state: CausalAgentState):
    """选择报告使用的因果图资源：优先采用通过校验的修订图，否则回退原始算法图。"""
    analysis_result = state.get("causal_analysis_result")
    if not isinstance(analysis_result, dict) or not analysis_result.get("success"):
        # 报告修订只使用当前分析上下文里已经确认的规范化图。
        context = _current_context(state)
        graph_data = context_graph(context)
        if graph_data is None:
            return None
        summary = context_algorithm_summary(context) or {}
        return build_causal_graph_model(
            graph_data,
            graph_id=REPORT_GRAPH_ASSET_KEY,
            algorithm=str(summary.get("algorithm") or ""),
            graph_source="analysis_context",
        )
    postprocess_result = state.get("postprocess_result") or {}
    revised_graph = postprocess_result.get("revised_graph")
    has_valid_revised_graph = (
        isinstance(revised_graph, dict)
        and isinstance(revised_graph.get("nodes"), list)
        and isinstance(revised_graph.get("edges"), list)
        and not postprocess_result.get("error")
    )
    selected_graph = (
        revised_graph if has_valid_revised_graph else analysis_result.get("data")
    )
    algorithm = analysis_result.get("algorithm")
    return build_causal_graph_model(
        selected_graph,
        graph_id=REPORT_GRAPH_ASSET_KEY,
        algorithm=str(algorithm) if algorithm else "",
        graph_source="postprocessed" if has_valid_revised_graph else "original",
        revision_summary=str(postprocess_result.get("revision_summary") or ""),
    )


def _prompt_json(value: Any) -> str:
    """把结构化提示词输入渲染成稳定的 JSON 文本。

    ChatPromptTemplate 对字典和列表只做 str()，会得到单引号、None 和无缩进的 Python
    字面量；这里统一转成 JSON，和预处理节点的 data_summary 写法保持一致。
    """

    return json.dumps(value, ensure_ascii=False, indent=2)


_REPORT_METADATA_COLUMN_FIELDS = (
    "inferred_type",
    "unique_count",
    "is_constant",
    "missing_ratio",
    "causal_suitability",
    "possible_id",
    "stats",
    "issues",
)

_REPORT_METADATA_QUALITY_FIELDS = (
    "total_missing_ratio",
    "constant_columns",
    "high_missing_columns",
)


def _report_metadata_for_prompt(analysis_parameters: Any) -> str:
    """给报告模型的数据概览：规模、列清单和每列的类型与质量标记。

    只保留报告需要的事实，去掉 ``value_counts`` 等取值分布，使提示词长度随列数线性
    增长而不是随每列的取值数量增长。
    """

    params = analysis_parameters if isinstance(analysis_parameters, dict) else {}
    column_profiles = params.get("column_profiles")
    columns = [str(column) for column in (params.get("columns") or [])]
    if not columns and isinstance(column_profiles, dict):
        columns = [str(column) for column in column_profiles]

    profiles: dict[str, dict] = {}
    if isinstance(column_profiles, dict):
        for column, profile in column_profiles.items():
            if not isinstance(profile, dict):
                continue
            profiles[str(column)] = {
                field: profile[field]
                for field in _REPORT_METADATA_COLUMN_FIELDS
                if field in profile
            }

    quality = params.get("quality_assessment")
    quality_summary: dict[str, Any] = {}
    if isinstance(quality, dict):
        quality_summary = {
            field: quality[field]
            for field in _REPORT_METADATA_QUALITY_FIELDS
            if field in quality
        }

    return _prompt_json({
        "n_rows": params.get("n_rows"),
        "n_cols": params.get("n_cols", len(columns) or None),
        "columns": columns,
        "column_profiles": profiles,
        "quality_assessment": quality_summary,
    })


def _report_asset_manifest(assets: dict[str, Any]) -> list[dict[str, str]]:
    """给模型看的资源清单：只有资源键、块类型和简短描述，不含数据点。"""
    manifest: list[dict[str, str]] = []
    for asset_key, asset in assets.items():
        if isinstance(asset, ChartAsset):
            manifest.append(
                {
                    "asset_key": asset_key,
                    "block_type": "chart",
                    "description": describe_chart_asset(asset),
                }
            )
            continue
        algorithm = asset.metadata.get("algorithm") or "未知算法"
        graph_source = asset.metadata.get("graph_source") or "original"
        manifest.append(
            {
                "asset_key": asset_key,
                "block_type": "causal_graph",
                "description": f"因果图（算法 {algorithm}，来源 {graph_source}）",
            }
        )
    return manifest


def _report_evidence_manifest(evidence_refs: list[Any]) -> list[dict[str, str]]:
    """给模型看的证据清单：只有证据 ID 和简短描述。"""
    return [
        {"evidence_id": evidence.evidence_id, "description": evidence.description}
        for evidence in evidence_refs
    ]


def _report_document_from_state(state: CausalAgentState):
    """从 State 读取结构化报告文档；缺失或类型不符时返回 None。"""
    document = state.get("report_document")
    return document if isinstance(document, ReportDocument) else None


def _report_followup_context(state: CausalAgentState) -> dict[str, str]:
    """报告追问只使用摘要、资源和证据说明，不把图表数据和图模型塞进提示词。"""
    document = _report_document_from_state(state)
    if document is None:
        context = _current_context(state)
        return {
            "report_summary": context_facts_text(context) if context else "",
            "asset_notes": "",
            "source_notes": "",
            "evidence_notes": "",
        }
    asset_notes: list[str] = []
    for asset_key, asset in document.assets.items():
        if isinstance(asset, ChartAsset):
            asset_notes.append(f"- {asset_key}: {describe_chart_asset(asset)}")
        else:
            algorithm = asset.metadata.get("algorithm") or "未知算法"
            asset_notes.append(f"- {asset_key}: 因果图（算法 {algorithm}）")
    source_notes = [
        f"- {source.source_id}: {source.kind} {source.title}"
        + (f" {source.url}" if source.url else "")
        for source in document.sources
    ]
    evidence_notes = [
        f"- {evidence.evidence_id}: {evidence.description}"
        for evidence in document.evidence_refs
    ]
    return {
        "report_summary": report_document_summary(document),
        "asset_notes": "\n".join(asset_notes),
        "source_notes": "\n".join(source_notes),
        "evidence_notes": "\n".join(evidence_notes),
    }


async def report_node(state: CausalAgentState, llm: ChatOpenAI) -> dict:
    """报告模块：生成结构化报告文档。

    模型只产出 ReportDraft（报告标题、块结构、Markdown 文本和资源/证据引用）；
    图表资源、因果图模型、来源和证据全部由后端注入并校验。资源引用、块 ID 或
    证据引用非法时抛出 ReportSchemaError，进入图节点受控错误路径，不保存部分报告。
    """
    chart_assets = coerce_chart_assets(state.get("chart_assets"))
    assets: dict[str, Any] = dict(chart_assets)
    graph_asset = _report_graph_asset(state)
    if graph_asset is not None:
        assets[graph_asset.graph_id] = graph_asset

    regeneration = state.get("report_revision_mode") == "full_regeneration_from_context"
    context = _current_context(state)

    resources = build_report_resources(
        file_summary=state.get("file_summary"),
        web_search_result=state.get("web_search_result"),
        rag_evidence=state.get("deep_agent_rag_evidence"),
        web_evidence=state.get("deep_agent_web_evidence"),
    )

    system_prompt_template = """
         system role: {system_role}
         #输出语言：{report_language}

         你的任务是根据用户的对话历史和当前状态，生成一份综合、完整的因果领域报告。
         # 当前状态摘要
         1. 预处理结果：{preprocess_summary}
         2. 预处理元数据：{preprocess_meta_data}
         3. 因果分析结果：{causal_analysis_result}
         4. 知识库结果：{knowledge_base_result}
         5. 联网搜索结果：{web_search_result}
         6. 后处理结果：{postprocess_result}
         7. 算法解释补充：{method_context}
         8. 当前分析上下文事实：{analysis_context_facts}

        ## 报告模式
        {revision_instruction}

        ## 因果分析结果解读规则
        - 如果因果分析结果包含 error_type，请明确说明算法未能产生有效因果图，不要声称“没有因果关系”。
        - 如果因果分析结果包含 fallback_from 和 fallback_reason，请说明原算法不适用并已改用 fallback_tool 的结果。
        - 只有当算法 success 为 true 且边列表为空时，才可以表述为“未发现显著因果边/因果关系”。
        - 如果算法解释补充中出现 DirectLiNGAM，请明确说明线性、非高斯、误差独立、DAG 和无潜在混杂假设。
        - DirectLiNGAM 的带权边只能解释为模型假设下的候选因果关系，不得写成实验已验证事实。

        ## 报告文档结构
        你必须返回一个 JSON 对象，只包含 title 和 blocks 两个字段：
        - title：报告标题。
        - blocks：报告块数组。每个块必须有唯一的 id 和 type，允许的类型只有四种：
          1. section：{{"id": "...", "type": "section", "title": "章节标题", "children": [子块]}}
          2. markdown：{{"id": "...", "type": "markdown", "content": "Markdown 文本", "evidence_refs": []}}
          3. chart：{{"id": "...", "type": "chart", "title": "图表标题", "asset_key": "资源键"}}
          4. causal_graph：{{"id": "...", "type": "causal_graph", "title": "图标题", "asset_key": "资源键"}}

        ## 硬性规则
        - 只有 markdown 块的 content 字段可以包含 Markdown，例如标题、列表、有序列表、表格、引用、代码块、加粗和链接。
        - 禁止生成 HTML 标签、CSS、Base64 图片、图片标签或图表占位符，图表与因果图一律使用资源块表达。
        - chart 和 causal_graph 块的 asset_key 必须来自下面“可用资源”列出的资源键，不得自行编造。
        - markdown 块的 evidence_refs 只能引用下面“可用证据”列出的证据 ID；没有可用证据时请留空数组。
        - 块 id 在整篇报告中必须唯一，使用稳定的英文或拼音短名。
        - 不要重新计算或编造数据，图表数据由系统根据真实数据注入。

        ## 可用资源
        {asset_manifest}

        ## 可用证据
        {evidence_manifest}

        ## 报告结构要求
        1. 数据概览：基于数据概览进行总结。
        2. 数据可视化：在合适的位置使用 chart 块展示数据分布，并在图表前后补充文字说明。
           - 如果用户没有提到具体的变量类型，必须包含“可用资源”中的全部图表。
           - 如果用户提到了具体的变量类型，则只包含该类型的图表。
        3. 分析过程：详细描述因果分析的步骤和方法。
        4. 分析结果：总结主要发现和因果关系；当“可用资源”中存在因果图时，使用 causal_graph 块展示。
        """

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt_template),
            MessagesPlaceholder(variable_name="messages"),
        ]
    )

    knowledge_summary = format_rag_summary_for_prompt(
        state.get("knowledge_base_result", {}),
        max_questions=3,
        include_evidence=True
    )
    web_summary = format_web_search_summary_for_prompt(
        state.get("web_search_result", {}),
    )

    draft = await ainvoke_structured(
        llm=llm,
        schema=ReportDraft,
        prompt=prompt,
        inputs={
            "messages": llm_prompt_messages(state["messages"]),
            "preprocess_meta_data": _report_metadata_for_prompt(
                state.get("analysis_parameters")
            ),
            "preprocess_summary": state.get("preprocess_summary", {}),
            "causal_analysis_result": state.get("causal_analysis_result", {}),
            "knowledge_base_result": knowledge_summary,
            "web_search_result": web_summary,
            "postprocess_result": state.get("postprocess_result", {}),
            "method_context": _causal_method_context_for_report(
                state.get("causal_analysis_result", {})
            ),
            "report_language": _report_language_instruction(),
            "system_role": causal_report_prompt(),
            "analysis_context_facts": (
                context_facts_text(context) if context else "当前没有已保存的分析上下文。"
            ),
            "revision_instruction": _report_revision_instruction(regeneration),
            "asset_manifest": _prompt_json(_report_asset_manifest(assets)),
            "evidence_manifest": _prompt_json(
                _report_evidence_manifest(resources.evidence_refs)
            ),
        },
        node_name="report",
    )

    document = build_report_document(
        draft,
        assets=assets,
        sources=resources.sources,
        evidence_refs=resources.evidence_refs,
    )

    return {
        "report_document": document,
        "messages": [
            AIMessage(content="决策：因果分析报告已生成完成。", name="report")
        ],
    }

async def normal_chat_node(state: CausalAgentState,llm: ChatOpenAI) -> dict:
    """
    Represents "正常问答".
    This is for when the agent determines it's a simple chat conversation.
    """
    prompt_template = (
        """
        system role: 你是日常聊天助手，你的任务是根据用户的对话历史，回答用户的问题。
        
        """
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", prompt_template),
            MessagesPlaceholder(variable_name="messages"),
        ]
    )
    runnable = prompt | llm | StrOutputParser()
    response = await runnable.ainvoke({
        "messages": llm_prompt_messages(state["messages"]),
    })
    # 只返回新消息
    return {"messages": [AIMessage(content=response, name="normal_chat")]}

async def inquiry_answer_node(state: CausalAgentState, llm: ChatOpenAI) -> dict:
    """
    根据当前分析上下文和报告回答用户问题，或转达上下文澄清问题。

    本节点不决定是否重新执行算法，也不修改报告；澄清问题由后端解析结果直接输出，
    不经过模型改写。
    """
    clarification = _clarification_question(state)
    if clarification:
        return {"messages": [AIMessage(content=clarification, name="inquiry_answer")]}
    prompt_template = (
        """
        system role: {system_role}
        # 当前状态摘要
        1. 因果分析结果：{causal_analysis_result}
        2. 知识库结果：{knowledge_base_result}
        3. 联网搜索结果：{web_search_result}
        4. 后处理结果：{postprocess_result}
        5. 报告摘要：{report_summary}
        6. 报告资源说明：{asset_notes}
        7. 报告来源说明：{source_notes}
        8. 报告证据说明：{evidence_notes}
        9. 当前分析上下文事实：{analysis_context_facts}
        
        # 你的任务：根据历史摘要和所有分析结果，回答用户问题
        - 只使用上面列出的事实回答，不要推测没有出现过的因果结论或数值。
        - 不要决定重新执行算法，也不要改写报告正文。
        - 用户的问题：{messages}
        
        """
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", prompt_template),
            MessagesPlaceholder(variable_name="messages"),
        ]
    )
    runnable = prompt | llm | StrOutputParser()
    knowledge_summary = format_rag_summary_for_prompt(
        state.get("knowledge_base_result", {}),
        max_questions=2,
        include_evidence=True
    )
    web_summary = format_web_search_summary_for_prompt(
        state.get("web_search_result", {}),
    )
    followup_context = _report_followup_context(state)
    context = _current_context(state)

    response = await runnable.ainvoke({
        "messages": llm_prompt_messages(state["messages"]),
        "causal_analysis_result": state.get("causal_analysis_result", {}),
        "knowledge_base_result": knowledge_summary,
        "web_search_result": web_summary,
        "postprocess_result": state.get("postprocess_result", {}),
        "report_summary": followup_context["report_summary"],
        "asset_notes": followup_context["asset_notes"],
        "source_notes": followup_context["source_notes"],
        "evidence_notes": followup_context["evidence_notes"],
        "analysis_context_facts": (
            context_facts_text(context) if context else "当前没有已保存的分析上下文。"
        ),
        "system_role": causal_prompt()
    })
    # 只返回新消息
    return {"messages": [AIMessage(content=response, name="inquiry_answer")]}
