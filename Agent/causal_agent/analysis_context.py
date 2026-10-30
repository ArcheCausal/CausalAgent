"""AnalysisContext 的规范化投影与提示词视图。

文档职责：把 Deep Agent 的执行结果压成可以跨 Job 复用的结构化事实，并把 MySQL 中的
上下文行投影成 agent、报告和追问节点可以读取的只读视图。

适用范围：Agent 父图的上下文加载、上下文切换匹配、报告与追问提示词构造。本模块只做
纯函数转换，不访问数据库、不做权限校验、不决定最终路由；数据库读写见
Database/analysis_contexts.py，路由由 agent 节点根据后端解析结果生成。
"""

from __future__ import annotations

import re
import copy
import json
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from langchain_core.messages import HumanMessage

from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    EvidenceResult,
    FinalAnalysisDecision,
    InvocationRecord,
    StandardizedGraph,
)
from Agent.deep_agent_tools.algorithm_specs import get_algorithm_spec
from Agent.data_processing.parameters import execution_parameters
from Agent.deep_agent_tools.functional_form import (
    estimation_pair,
    compare_effect_results,
)
from Agent.deep_agent_tools.graph_validation import validate_graph_structure


MAX_ALGORITHM_SUMMARY_CHARS = 800
MAX_EVIDENCE_SNIPPET_CHARS = 400
MAX_CONTEXT_EVIDENCE_ITEMS = 8
MAX_CONTEXT_QUESTION_CHARS = 1000
MAX_INDEX_SUMMARY_CHARS = 160
MAX_INDEX_ENTRY_QUESTION_CHARS = 200


def latest_user_message_text(state: Mapping[str, Any]) -> str:
    """读取当前用户原文，不混入历史提问、路由消息或模型生成的参数。"""
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return ""

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
    research_roles: Mapping[str, Any] | None = None,
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
        "method": result.method.model_dump(mode="json") if result.method else None,
        "implementation": result.implementation.model_dump(mode="json") if result.implementation else None,
        "data_view": result.data_view.model_dump(mode="json") if result.data_view else None,
        "graph_validation": result.graph_validation.model_dump(mode="json") if result.graph_validation else None,
        "status": result.status,
        "input_identity": result.provenance.input_identity,
        "parameters": {
            "target": _optional_text(params.get("target")),
            "treatment": _optional_text(params.get("treatment")),
        },
        # 执行参数使用实际列名；原始研究角色只用于展示，不能替换执行列。
        "research_roles": {
            "target": _optional_text((research_roles or {}).get("target")),
            "treatment": _optional_text((research_roles or {}).get("treatment")),
        },
        "summary": _clip(result.summary, MAX_ALGORITHM_SUMMARY_CHARS),
        "graph_semantics": _optional_text(
            result.graph_semantics
            or (standardized.graph_semantics if standardized is not None else None)
        ),
        "graph": graph,
        "causal_effect": (
            result.causal_effect.model_dump(mode="json")
            if result.causal_effect is not None
            else None
        ),
        "ate_check": result.ate_check.model_dump(mode="json") if result.ate_check else None,
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


def rebuild_context_index(
    previous_index: Any, previous_context: Any, switched_to_id: str | None, *, limit: int = 8,
) -> list:
    """切换后保留旧上下文索引，并移除当前上下文，不额外读库。"""
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
        facts = summary.get("analysis_facts")
        if isinstance(facts, Mapping):
            decision = facts.get("decision") or {}
            lines.append("结果选择依据：" + _text(decision.get("selection_rationale") or facts.get("no_primary_reason")))
            lines.append("置信依据：" + "；".join(decision.get("confidence_basis") or []))
            for result in facts.get("results", []):
                method = result.get("method") or {}
                lines.append(f"方法：{method.get('public_name') or result.get('algorithm')}；契约版本：{result.get('capability_version')}；"
                             f"处理：{result.get('disposition')}；依据：{result.get('assessment_rationale')}")
            if decision.get("conflicts"):
                lines.append("结果冲突：" + "；".join(item["summary"] for item in decision["conflicts"]))
        graph = summary.get("graph")
        if isinstance(graph, Mapping) and isinstance(graph.get("edges"), list):
            edges = [
                graph_edge_text(edge)
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


def graph_edge_text(edge: Mapping[str, Any]) -> str:
    edge_type = edge.get("edge_type")
    symbol = {"directed": "→", "undirected": "—", "bidirected": "↔", "partially_directed": "o→"}.get(edge_type, "?")
    suffix = "（潜在共同原因候选，尚未证实）" if edge_type == "bidirected" else ""
    return f"{_text(edge.get('source'))} {symbol} {_text(edge.get('target'))}{suffix}"
def _primary_algorithm_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """挑选本次执行的主算法结果并压成结构化事实。"""
    raw_results = state.get("deep_agent_algorithm_results")
    if state.get("finalization_status") != "valid" or not isinstance(raw_results, Mapping) or not raw_results:
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
    decision = state.get("deep_agent_accepted_decision")
    primary_ref = getattr(decision, "primary_graph_result_ref", None)
    if not primary_ref and isinstance(decision, Mapping):
        primary_ref = decision.get("primary_graph_result_ref")
    selected = results.get(str(primary_ref)) if primary_ref else None
    if selected is None or selected.status != "valid":
        return None
    parameters = state.get("analysis_parameters")
    return normalize_algorithm_summary(
        selected,
        parameters=parameters if isinstance(parameters, Mapping) else None,
    )


_REPORT_PROFILE_FIELDS = (
    "storage_type", "inferred_type", "missing_count", "missing_ratio", "unique_count",
    "is_constant", "statistical_features", "measurement_semantics", "stats", "issues",
)
_REPORT_METRICS = frozenset({
    "n_samples", "n_features", "missing_values", "runtime_sec", "threshold",
    "standardize", "missing_handling", "independence_test", "alpha",
    "residual_spearman_max", "residual_abs_spearman_max",
    "linear_estimate", "effect_difference", "difference_se",
    "difference_ci95_low", "difference_ci95_high",
})

# v3 分开保存主图、主效应和 Gate 已接受决策，旧事实不能直接复用。
ANALYSIS_FACTS_VERSION = "analysis-facts-v3"


def report_data_metadata(parameters: Any) -> dict[str, Any]:
    """报告所需的数据概览；不带原始取值、CSV、文件身份或控制字段。"""
    params = parameters if isinstance(parameters, Mapping) else {}
    profiles = params.get("column_profiles") or {}
    profiles = profiles if isinstance(profiles, Mapping) else {}
    result = {
        "n_rows": params.get("n_rows"), "n_cols": params.get("n_cols"),
        "columns": list(params.get("columns") or profiles.keys()),
        "column_profiles": {str(column): {key: copy.deepcopy(profile[key]) for key in _REPORT_PROFILE_FIELDS if key in profile}
                            for column, profile in profiles.items() if isinstance(profile, Mapping)},
        "profile_thresholds": copy.deepcopy(params.get("profile_thresholds") or {}),
        "quality_assessment": {key: copy.deepcopy(value) for key, value in (params.get("quality_assessment") or {}).items()
                               if key in {"total_missing_ratio", "constant_columns", "high_missing_columns"}},
        "nonlinearity": copy.deepcopy(params.get("nonlinearity")),
    }
    preparation = params.get("data_preparation")
    if isinstance(preparation, Mapping):
        result["data_preparation"] = {
            key: copy.deepcopy(preparation[key])
            for key in (
                "source_filename",
                "analysis_filename",
                "row_count",
                "column_count",
                "observation_unit",
                "role_columns",
                "processing_summary",
                "transformations",
                "limitations",
            )
            if key in preparation
        }
    return result


def _report_result(result: AlgorithmResult, parameters: Any) -> dict[str, Any]:
    summary = normalize_algorithm_summary(
        result,
        parameters=parameters,
        research_roles=(
            parameters.get("research_roles")
            if isinstance(parameters, Mapping)
            and isinstance(parameters.get("research_roles"), Mapping)
            else None
        ),
    )
    graph = summary.get("graph")
    if graph is not None:
        edges = graph["edges"]
        summary["edge_counts"] = {
            "total": len(edges),
            **{kind: sum(edge["edge_type"] == kind for edge in edges)
               for kind in ("directed", "undirected", "bidirected", "partially_directed")},
        }
    else:
        summary["edge_counts"] = None
    for key in ("input_identity", "provenance"):
        summary.pop(key, None)
    summary["diagnostics"]["metrics"] = {
        key: value for key, value in summary["diagnostics"]["metrics"].items() if key in _REPORT_METRICS
    }
    return summary


def _data_view_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    views = [result.get("data_view") for result in results]
    missing = [result["result_ref"] for result, view in zip(results, views) if not view]
    distinct = {json.dumps(view, ensure_ascii=False, sort_keys=True) for view in views if view}
    runner_processing = {result["result_ref"]: {key: value for key, value in result.get("diagnostics", {}).get("metrics", {}).items()
                         if key in {"standardize", "missing_handling"}} for result in results}
    return {
        "status": "insufficient" if missing else "different" if len(distinct) > 1 else "same",
        "data_views_differ": None if missing else len(distinct) > 1,
        "compared_fields": ["row_count", "column_count", "columns"],
        "missing_result_refs": missing,
        "runner_processing": runner_processing,
        "comparison_boundary": "data_view 比较所有算法共同使用的已校验分析数据集；算法不得按各自参数改写数据。",
    }


def _graph_comparisons(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐变量对比较实际边，不把缺边误记为反向，不合并图或裁定真实方向。"""
    def group_edges(graph: Mapping[str, Any]) -> dict[tuple[str, str], list[tuple[str, str, str]]]:
        grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for edge in graph["edges"]:
            source, target, kind = edge["source"], edge["target"], edge["edge_type"]
            pair = tuple(sorted((source, target)))
            if kind in {"undirected", "bidirected"}:
                source, target = pair
            grouped.setdefault(pair, []).append((kind, source, target))
        return {pair: sorted(edges) for pair, edges in grouped.items()}

    comparisons = []
    valid = [result for result in results if result["status"] == "valid" and result.get("graph")]
    for left, right in combinations(valid, 2):
        left_pairs, right_pairs = group_edges(left["graph"]), group_edges(right["graph"])
        pairs = []
        for variables in sorted(left_pairs.keys() | right_pairs.keys()):
            first, second = left_pairs.get(variables, []), right_pairs.get(variables, [])
            if first == second:
                relation = "same"
            elif not first:
                relation = "right_only"
            elif not second:
                relation = "left_only"
            elif len(first) == len(second) == 1 and first[0][0] == second[0][0] == "directed":
                relation = "opposite_direction"
            else:
                relation = "edge_type_difference"
            pairs.append({"variables": list(variables), "relation": relation,
                "left_edges": [{"edge_type": kind, "source": source, "target": target} for kind, source, target in first],
                "right_edges": [{"edge_type": kind, "source": source, "target": target} for kind, source, target in second]})
        comparisons.append({"left_result_ref": left["result_ref"], "right_result_ref": right["result_ref"],
            "left_algorithm": left["algorithm"], "right_algorithm": right["algorithm"],
            "relation_counts": {kind: sum(pair["relation"] == kind for pair in pairs)
                for kind in ("same", "opposite_direction", "edge_type_difference", "left_only", "right_only")},
            "pairs": pairs, "comparison_boundary": "仅比较本次候选边；未合并结果，不验证真实方向或方法假设。"})
    return comparisons


def _primary_graph_semantics(result: Mapping[str, Any]) -> Any:
    """估计结果展示的是入参源图，只能按源图自身的语义校验。"""

    method = result.get("method")
    semantics = method.get("graph_semantics") if isinstance(method, Mapping) else None
    try:
        spec = get_algorithm_spec(str(result.get("algorithm") or ""))
    except ValueError:
        return semantics
    if spec.kind == "causal_estimation":
        return result.get("graph_semantics") or semantics
    return semantics


def _functional_form_facts(
    parameters: Mapping[str, Any],
    results: list[AlgorithmResult],
    *,
    current_queries: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if estimation_pair(parameters) is None:
        return None
    return {"verdict": "agent_assessed", "comparisons": compare_effect_results(results),
            "reason": "方法差异仅作为诊断事实，不能据此判定非线性或强制选择估计器。"}


def _queries_for_pair(
    results: list[AlgorithmResult],
    queries: Mapping[str, Any],
    pair: tuple[str, str],
) -> list[AlgorithmResult]:
    """只保留当前查询、带效应请求证明的估计结果。"""

    selected: list[AlgorithmResult] = []
    for result in results:
        query = queries.get(result.result_ref)
        if not isinstance(query, Mapping):
            continue
        if (query.get("treatment"), query.get("outcome")) != pair:
            continue
        selected.append(result)
    return selected


def _result_queries(ledger: Any) -> dict[str, dict[str, Any]]:
    """从账本读取每个估计结果对应的效应请求；缺少证明的记录不产生条目。"""

    queries: dict[str, dict[str, Any]] = {}
    if not isinstance(ledger, Mapping):
        return queries
    for raw in ledger.values():
        try:
            record = InvocationRecord.model_validate(raw)
        except (TypeError, ValueError):
            continue
        query = record.effect_query
        if query is None:
            continue
        queries[record.result_ref or record.invocation_id] = {
            "tool_name": record.tool_name,
            "graph_result_ref": query.graph_result_ref,
            "treatment": query.treatment,
            "outcome": query.outcome,
            "contrast": list(query.contrast) if query.contrast is not None else None,
            "outcome_model": query.outcome_model,
            "effect_request": copy.deepcopy(query.effect_request),
            "estimator_config": copy.deepcopy(query.estimator_config),
        }
    return queries


def build_report_analysis_context(state: Mapping[str, Any]) -> dict[str, Any]:
    """完整 State 到报告事实的唯一安全投影；重新生成只读取对应保存快照。"""
    if state.get("report_revision_mode") == "full_regeneration_from_context":
        stored = context_algorithm_summary(state.get("analysis_context")) or {}
        facts = stored.get("analysis_facts")
        if isinstance(facts, Mapping) and facts.get("schema_version") == ANALYSIS_FACTS_VERSION:
            restored = copy.deepcopy(dict(facts))
            # 同版本的早期快照已有成对方向和区间；补充保存过的同方向点值，
            # 不重新计算结果，也不把 right-minus-left 点值配上反方向区间。
            for comparison in restored.get("effect_comparisons", []):
                if (comparison.get("comparable")
                    and comparison.get("paired_difference_direction") == "linear_minus_flexible"
                    and "paired_effect_difference" not in comparison):
                    refs = {comparison.get("left_result_ref"), comparison.get("right_result_ref")}
                    flexible = next((item for item in restored.get("results", [])
                        if item.get("result_ref") in refs and item.get("algorithm") == "causal.g_formula"), {})
                    comparison["paired_effect_difference"] = flexible.get("diagnostics", {}).get("metrics", {}).get("effect_difference")
            return restored
        return _unrecorded_analysis_facts()

    raw_params = state.get("analysis_parameters") or {}
    raw_params = raw_params if isinstance(raw_params, Mapping) else {}
    if "effect_request" not in raw_params:
        return _unrecorded_analysis_facts()
    research_roles = {
        "target": raw_params.get("target"),
        "treatment": raw_params.get("treatment"),
    }
    # 报告与门禁使用同一份执行参数：角色映射后的实际列名用于结果匹配，
    # 原始研究角色只用于展示。
    params = execution_parameters(
        raw_params, state.get("prepared_dataset_metadata") or {},
    )
    params["research_roles"] = research_roles
    result_queries = _result_queries(state.get("deep_agent_action_ledger"))
    raw_results = state.get("deep_agent_algorithm_results") or {}
    raw_results = raw_results if isinstance(raw_results, Mapping) else {}
    results: list[dict[str, Any]] = []
    parsed_results: list[AlgorithmResult] = []
    for ref, raw in raw_results.items():
        try:
            result = AlgorithmResult.model_validate(raw)
        except (TypeError, ValueError):
            continue
        if ref == result.result_ref:
            parsed_results.append(result)
            results.append(_report_result(result, params))
    decision = None
    raw_decision = state.get("deep_agent_accepted_decision")
    if state.get("finalization_status") == "valid" and raw_decision is not None:
        try:
            decision = FinalAnalysisDecision.model_validate(raw_decision).model_dump(mode="json")
        except (TypeError, ValueError):
            pass
    assessments = {item["result_ref"]: item for item in decision["result_assessments"]} if decision else {}
    for result in results:
        assessment = assessments.get(result["result_ref"])
        result["disposition"] = assessment["disposition"] if assessment else ("discarded" if result["status"] != "valid" else "unassessed")
        result["assessment_rationale"] = assessment["rationale"] if assessment else "最终决策没有采纳该结果，或尚未提供取舍说明。"
    primary_ref = decision["primary_graph_result_ref"] if decision else None
    primary = next((result for result in results if result["result_ref"] == primary_ref and result["status"] == "valid"), None)
    if primary is not None:
        graph = primary.get("graph")
        method = primary.get("method")
        if not isinstance(graph, Mapping) or not isinstance(method, Mapping):
            primary = None
        else:
            validated = validate_graph_structure(StandardizedGraph(
                graph_semantics=primary["graph_semantics"], **graph),
                graph_semantics=_primary_graph_semantics(primary))
            if not validated.valid:
                primary = None
    return {
        "schema_version": ANALYSIS_FACTS_VERSION, "finalization_status": state.get("finalization_status") or "not_recorded",
        "primary_result_ref": decision["primary_result_ref"] if decision and primary is not None else None,
        "primary_graph_result_ref": decision["primary_graph_result_ref"] if decision else None,
        "primary_effect_result_ref": decision["primary_effect_result_ref"] if decision else None,
        "effect_request": copy.deepcopy(raw_params.get("effect_request")),
        "effect_request_status": "recorded" if "effect_request" in raw_params else "not_recorded",
        "effect_status": decision["effect_status"] if decision else "not_recorded",
        "effect_reason": decision["effect_reason"] if decision else "没有通过门禁的效应决策。",
        "effect_comparisons": compare_effect_results(parsed_results) if decision else [],
        "no_primary_reason": None if primary is not None else (
            "所选结果缺少本次方法定义或未满足图约束，不能用于主图。" if primary_ref else
            decision["selection_rationale"] if decision else "本次没有通过门禁的主结果选择，不能展示任意结果或复用旧图。"),
        "decision": decision, "results": results,
        "graph_comparisons": _graph_comparisons(results),
        "data_view_comparison": _data_view_comparison(results),
        "data_metadata": report_data_metadata(params),
        "result_queries": result_queries,
        "functional_form": _functional_form_facts(
            params, parsed_results, current_queries=result_queries,
        ),
        "analysis_limitations": copy.deepcopy(state.get("analysis_limitations") or []),
        "budget_exhausted": bool(state.get("deep_agent_budget_exhausted")),
    }


def _unrecorded_analysis_facts() -> dict[str, Any]:
    return {"schema_version": ANALYSIS_FACTS_VERSION, "finalization_status": "not_recorded",
            "primary_result_ref": None, "primary_graph_result_ref": None,
            "primary_effect_result_ref": None, "effect_request": None,
            "effect_request_status": "not_recorded", "effect_status": "not_recorded",
            "effect_reason": "历史记录没有保存完整效应定义，不能推断为未请求。",
            "effect_comparisons": [], "results": [], "decision": None,
            "no_primary_reason": "本分析保存的是旧版计算规则或缺少完整映射与结果证据，必须重新分析。",
            "data_metadata": {}, "functional_form": None, "result_queries": {}}


def report_primary_result(facts: Mapping[str, Any]) -> dict[str, Any] | None:
    primary = facts.get("primary_graph_result_ref")
    if not primary or facts.get("finalization_status") != "valid":
        return None
    selected = next((result for result in facts.get("results", [])
                     if result.get("result_ref") == primary and result.get("status") == "valid"), None)
    if selected is None or not selected.get("method") or not selected.get("graph"):
        return None
    try:
        graph = StandardizedGraph(graph_semantics=selected["graph_semantics"], **selected["graph"])
        validation = validate_graph_structure(graph, graph_semantics=_primary_graph_semantics(selected))
    except (KeyError, TypeError, ValueError):
        return None
    return selected if validation.valid else None


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

    包含主结果及各算法的精简分析事实、RAG/Web 证据摘要和报告 ID；没有可写事实时返回 None。
    该结果由 worker 作为内部字段传递，不进入公共事件或 checkpoint。
    """
    context = state.get("analysis_context")
    context_id = (
        context.get("analysis_context_id") if isinstance(context, Mapping) else None
    )
    algorithm_summary = None
    if state.get("report_revision_mode") != "full_regeneration_from_context" and (
        state.get("deep_agent_algorithm_results") or state.get("finalization_status")
    ):
        facts = build_report_analysis_context(state)
        primary = report_primary_result(facts)
        algorithm_summary = copy.deepcopy(primary) if primary else {
            "status": "no_primary", "graph": None, "summary": facts["no_primary_reason"],
        }
        algorithm_summary["analysis_facts"] = facts
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
