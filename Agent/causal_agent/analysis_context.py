"""AnalysisContext 的规范化投影与提示词视图。

文档职责：把 Deep Agent 的执行结果压成可以跨 Job 复用的结构化事实，并把 MySQL 中的
上下文行投影成 agent、报告和追问节点可以读取的只读视图。

适用范围：Agent 父图的上下文加载、上下文切换匹配、报告与追问提示词构造。本模块只做
纯函数转换，不访问数据库、不做权限校验、不决定最终路由；数据库读写见
Database/analysis_contexts.py，路由由 agent 节点根据后端解析结果生成。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from Agent.deep_agent_tools.models import AlgorithmResult, EvidenceResult


MAX_ALGORITHM_SUMMARY_CHARS = 800
MAX_EVIDENCE_SNIPPET_CHARS = 400
MAX_CONTEXT_EVIDENCE_ITEMS = 8
MAX_CONTEXT_QUESTION_CHARS = 1000
MAX_INDEX_SUMMARY_CHARS = 160
MAX_INDEX_ENTRY_QUESTION_CHARS = 200

_ORDINAL_PATTERN = re.compile(r"(?:第\s*)?(\d{1,2})\s*(?:个|条|项|次)?")
_ORDINAL_WORD_PATTERN = re.compile(r"第\s*([一二三四五六七八九十]{1,3})\s*(?:个|条|项|次)")
_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _text(value: Any) -> str:
    """把任意标量渲染成去掉首尾空白的单行文本。"""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _clip(value: Any, limit: int) -> str:
    """按字符上限截断文本，避免把长正文写进跨 Job 事实。"""
    text = _text(value)
    return text if len(text) <= limit else text[:limit]


def _optional_text(value: Any) -> str | None:
    """把空值规范化为 None，避免写入空字符串。"""
    text = _text(value)
    return text or None


def normalize_algorithm_summary(
    result: AlgorithmResult,
    *,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把一次算法结果压成报告与追问所需的结构化事实。

    保存规范化后的节点和边、方向语义、诊断、warning 和假设；不保存供应商原始返回，
    原始结果仍由已有的 raw artifact 引用、hash 和序列化版本表示。
    """
    standardized = result.standardized_graph
    graph: dict[str, Any] | None = None
    if standardized is not None:
        graph = {
            "nodes": list(standardized.nodes),
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "edge_type": edge.edge_type,
                    "weight": edge.weight,
                }
                for edge in standardized.edges
            ],
        }
    diagnostics = result.diagnostics
    params = dict(parameters or {})
    return {
        "result_ref": result.result_ref,
        "algorithm": result.capability_id,
        "capability_version": result.capability_version,
        "status": result.status,
        "input_identity": result.provenance.input_identity,
        "parameters": {
            "target": _optional_text(params.get("target")),
            "treatment": _optional_text(params.get("treatment")),
        },
        "summary": _clip(result.summary, MAX_ALGORITHM_SUMMARY_CHARS),
        "graph_semantics": _optional_text(
            result.graph_semantics
            or (standardized.graph_semantics if standardized is not None else None)
        ),
        "graph": graph,
        "diagnostics": diagnostics.model_dump(mode="json"),
        "warnings": [warning.model_dump(mode="json") for warning in result.warnings],
        "assumptions": list(diagnostics.assumptions_checked),
        "provenance": {
            "job_id": result.provenance.job_id,
            "spec_digest": result.provenance.spec_digest,
            "algorithm_runner_version": result.provenance.algorithm_runner_version,
            "invocation_id": result.provenance.invocation_id,
        },
    }


def normalize_evidence_items(
    evidence: Mapping[str, Any] | None,
    *,
    limit: int = MAX_CONTEXT_EVIDENCE_ITEMS,
) -> list[dict[str, Any]]:
    """把 RAG 或 Web 证据压成有限条目的公共证据。

    只保存可被报告引用的稳定 evidence_ref、限长 snippet、来源标题、URL、定位信息、
    release 和分数。没有 URL 的内部证据保持为空，不生成占位 URL。
    """
    items: list[dict[str, Any]] = []
    for key, raw in dict(evidence or {}).items():
        try:
            item = EvidenceResult.model_validate(raw)
        except Exception:
            continue
        if str(key) != item.evidence_ref:
            continue
        items.append(
            {
                "evidence_ref": item.evidence_ref,
                "snippet": _clip(item.snippet, MAX_EVIDENCE_SNIPPET_CHARS),
                "source_title": _optional_text(item.source_title),
                "source_url": _optional_text(item.source_url),
                "locator": _optional_text(item.locator),
                "release_id": _optional_text(item.release_id),
                "score": item.score,
            }
        )
    items.sort(key=lambda item: item["evidence_ref"])
    return items[: max(0, int(limit))]


def context_short_summary(context: Mapping[str, Any]) -> str:
    """给历史上下文索引生成一句简短摘要。"""
    summary = context.get("latest_algorithm_summary")
    if isinstance(summary, Mapping):
        text = _clip(summary.get("summary"), MAX_INDEX_SUMMARY_CHARS)
        if text:
            return text
        parameters = summary.get("parameters")
        if isinstance(parameters, Mapping) and parameters.get("target"):
            return f"目标变量 {_text(parameters['target'])}"
    return _clip(context.get("analysis_question"), MAX_INDEX_SUMMARY_CHARS)


def build_context_index(
    contexts: Sequence[Mapping[str, Any]] | None,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """构造上下文索引；内部 ID 只用于后端解析，不进入提示词。"""
    entries: list[dict[str, Any]] = []
    for context in list(contexts or ())[: max(0, int(limit))]:
        entries.append(
            {
                "analysis_context_id": context.get("analysis_context_id"),
                "filename": _optional_text(context.get("filename")),
                "target": _optional_text(context.get("target")),
                "treatment": _optional_text(context.get("treatment")),
                "analysis_question": _clip(
                    context.get("analysis_question"),
                    MAX_INDEX_ENTRY_QUESTION_CHARS,
                ),
                "latest_report_title": _optional_text(context.get("latest_report_title")),
                "updated_at": context.get("updated_at"),
                "short_summary": context_short_summary(context),
            }
        )
    return entries


def index_prompt_view(index: Sequence[Mapping[str, Any]] | None) -> str:
    """把历史上下文索引渲染成模型可读文本，只使用展示信息。"""
    lines: list[str] = []
    for ordinal, entry in enumerate(list(index or ()), start=1):
        parts = [f"历史分析 {ordinal}", f"文件 {entry.get('filename') or '未记录'}"]
        if entry.get("target"):
            parts.append(f"目标变量 {entry['target']}")
        if entry.get("treatment"):
            parts.append(f"处理变量 {entry['treatment']}")
        if entry.get("latest_report_title"):
            parts.append(f"报告 {entry['latest_report_title']}")
        if entry.get("short_summary"):
            parts.append(str(entry["short_summary"]))
        if entry.get("updated_at"):
            parts.append(f"更新时间 {entry['updated_at']}")
        lines.append("；".join(parts))
    return "\n".join(lines)


def _ordinal_from_hint(hint: str) -> int | None:
    """从用户表述中读取历史分析序号，读不到时返回 None。"""
    word_match = _ORDINAL_WORD_PATTERN.search(hint)
    if word_match:
        digits = word_match.group(1)
        if digits in _CHINESE_DIGITS:
            return _CHINESE_DIGITS[digits]
        if digits.startswith("十") and len(digits) > 1:
            tail = _CHINESE_DIGITS.get(digits[1:])
            return 10 + tail if tail else None
    digit_match = _ORDINAL_PATTERN.search(hint)
    if digit_match:
        return int(digit_match.group(1))
    return None


def match_context_hint(
    hint: str | None,
    index: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """按用户表述匹配历史上下文，返回全部候选而不做猜测。

    匹配只依据用户表达的文件名、目标变量、处理变量、报告标题和问题描述，以及展示用
    的历史分析序号。调用方在候选不唯一时必须回到澄清问题，不能任选一个。
    """
    entries = list(index or ())
    normalized_hint = _text(hint).casefold()
    if not normalized_hint or not entries:
        return []

    ordinal = _ordinal_from_hint(normalized_hint)
    if ordinal is not None and 1 <= ordinal <= len(entries):
        return [entries[ordinal - 1]]

    matched: list[dict[str, Any]] = []
    for entry in entries:
        fields = (
            entry.get("filename"),
            entry.get("target"),
            entry.get("treatment"),
            entry.get("latest_report_title"),
            entry.get("analysis_question"),
            entry.get("short_summary"),
        )
        for field in fields:
            text = _text(field).casefold()
            if len(text) < 2:
                continue
            if text in normalized_hint or normalized_hint in text:
                matched.append(entry)
                break
    return matched


def context_algorithm_summary(context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """读取当前上下文保存的算法事实。"""
    if not isinstance(context, Mapping):
        return None
    summary = context.get("latest_algorithm_summary")
    return dict(summary) if isinstance(summary, Mapping) else None


def context_graph(context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """读取当前上下文的规范化图，用于报告重建因果图资源。"""
    summary = context_algorithm_summary(context)
    if not summary:
        return None
    graph = summary.get("graph")
    if not isinstance(graph, Mapping):
        return None
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        return None
    return {
        "nodes": list(nodes),
        "edges": list(edges),
        "graph_semantics": summary.get("graph_semantics"),
    }


def context_evidence_items(context: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """合并 RAG 与 Web 证据摘要，保持各自已有的顺序。"""
    if not isinstance(context, Mapping):
        return []
    items: list[dict[str, Any]] = []
    for key in ("latest_rag_evidence", "latest_web_evidence"):
        value = context.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, Mapping))
    return items


def context_facts_text(context: Mapping[str, Any] | None) -> str:
    """把当前上下文渲染成报告与追问可读的事实文本。"""
    if not isinstance(context, Mapping) or not context:
        return "当前没有可用的分析上下文。"
    lines: list[str] = []
    lines.append(f"文件：{_optional_text(context.get('filename')) or '未记录'}")
    if context.get("target"):
        lines.append(f"目标变量：{_text(context['target'])}")
    if context.get("treatment"):
        lines.append(f"处理变量：{_text(context['treatment'])}")
    if context.get("analysis_question"):
        lines.append(
            f"分析问题：{_clip(context['analysis_question'], MAX_CONTEXT_QUESTION_CHARS)}"
        )
    summary = context_algorithm_summary(context)
    if summary:
        lines.append(
            "算法结果："
            f"{_text(summary.get('algorithm')) or '未知算法'}"
            f"（{_text(summary.get('status')) or 'unknown'}）"
        )
        if summary.get("summary"):
            lines.append(f"算法结论：{_text(summary['summary'])}")
        if summary.get("graph_semantics"):
            lines.append(f"图方向语义：{_text(summary['graph_semantics'])}")
        graph = summary.get("graph")
        if isinstance(graph, Mapping) and isinstance(graph.get("edges"), list):
            edges = [
                f"{_text(edge.get('source'))} -> {_text(edge.get('target'))}"
                for edge in graph["edges"]
                if isinstance(edge, Mapping)
            ]
            lines.append("因果边：" + ("；".join(edges) if edges else "未发现因果边"))
        warnings = summary.get("warnings")
        if isinstance(warnings, list) and warnings:
            lines.append(
                "算法提示："
                + "；".join(
                    _text(
                        warning.get("message")
                        if isinstance(warning, Mapping)
                        else warning
                    )
                    for warning in warnings
                )
            )
    evidence = context_evidence_items(context)
    if evidence:
        lines.append("检索证据：")
        for item in evidence:
            title = _optional_text(item.get("source_title")) or "未命名来源"
            locator = _optional_text(item.get("locator"))
            suffix = f"（{locator}）" if locator else ""
            ref = _text(item.get("evidence_ref"))
            snippet = _text(item.get("snippet"))
            lines.append(f"- [{ref}] {title}{suffix}：{snippet}")
    if context.get("latest_report_title"):
        lines.append(f"最新报告标题：{_text(context['latest_report_title'])}")
    return "\n".join(lines)
def _primary_algorithm_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """挑选本次执行的主算法结果并压成结构化事实。"""
    raw_results = state.get("deep_agent_algorithm_results")
    if not isinstance(raw_results, Mapping) or not raw_results:
        return None
    results: dict[str, AlgorithmResult] = {}
    for key, raw in raw_results.items():
        try:
            result = AlgorithmResult.model_validate(raw)
        except Exception:
            continue
        if str(key) == result.result_ref:
            results[str(key)] = result
    if not results:
        return None
    decision = state.get("deep_agent_decision")
    primary_ref = getattr(decision, "primary_result_ref", None)
    if not primary_ref and isinstance(decision, Mapping):
        primary_ref = decision.get("primary_result_ref")
    selected = results.get(str(primary_ref)) if primary_ref else None
    if selected is None:
        valid = [result for result in results.values() if result.status == "valid"]
        selected = valid[0] if len(valid) == 1 else None
    if selected is None:
        return None
    parameters = state.get("analysis_parameters")
    return normalize_algorithm_summary(
        selected,
        parameters=parameters if isinstance(parameters, Mapping) else None,
    )


def _report_id_from_state(state: Mapping[str, Any]) -> str | None:
    """读取本轮报告文档的 ID。"""
    document = state.get("report_document")
    if isinstance(document, Mapping):
        value = document.get("report_id")
    else:
        value = getattr(document, "report_id", None)
    return str(value) if value else None


def build_context_commit(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """从最终父图 State 生成要写回分析上下文的已确认事实。

    只包含主算法结果、RAG/Web 证据摘要和报告 ID；没有可写事实时返回 None。
    该结果由 worker 作为内部字段传递，不进入公共事件或 checkpoint。
    """
    context = state.get("analysis_context")
    context_id = (
        context.get("analysis_context_id") if isinstance(context, Mapping) else None
    )
    algorithm_summary = _primary_algorithm_summary(state)
    rag_evidence = normalize_evidence_items(state.get("deep_agent_rag_evidence"))
    web_evidence = normalize_evidence_items(state.get("deep_agent_web_evidence"))
    report_id = _report_id_from_state(state)
    if not algorithm_summary and not rag_evidence and not web_evidence and not report_id:
        return None
    return {
        "analysis_context_id": str(context_id) if context_id else None,
        "algorithm_summary": algorithm_summary,
        "rag_evidence": rag_evidence or None,
        "web_evidence": web_evidence or None,
        "report_id": report_id,
    }
