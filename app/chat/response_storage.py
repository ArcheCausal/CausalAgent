"""
app.chat.response_storage - AI 响应展示与持久化格式转换。
"""

import json
from typing import Any

from Agent.Report.Metadata_sum import replace_placeholders
from Agent.Report.document import ReportDocument, parse_report_document


def render_summary_for_display(summary: str | None, visualization_mapping: dict | None) -> str | None:
    """把原始报告占位符渲染成图片标签，并避免已经渲染过的 HTML 被二次替换。"""
    if not summary or not visualization_mapping:
        return summary

    if "data:image/" in summary or "<img" in summary.lower():
        return summary

    return replace_placeholders(summary, visualization_mapping)


# 前端 vis-network 只认识 {id,label} 节点和 {from,to} 边；这里固定白名单，
# 避免把 Agent 内部图的字段带到浏览器。
_VIS_EDGE_FIELDS = ("from", "to", "arrows", "dashes", "label", "weight")

_EDGE_ARROWS = {
    "directed": "to",
    "partially_directed": "to",
    "partially_oriented": "to",
    "bidirected": "to,from",
}

# 方向不确定或未定向的边用虚线表示，与因果图的一般画法一致。
_DASHED_EDGE_TYPES = {"undirected", "partially_directed", "partially_oriented"}


def _vis_node(raw_node: Any) -> dict[str, Any] | None:
    """把节点名或已有节点对象统一成 vis-network 的 {id,label}。"""
    if isinstance(raw_node, str):
        name = raw_node.strip()
        return {"id": name, "label": name} if name else None
    if isinstance(raw_node, dict):
        node_id = raw_node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            return None
        label = raw_node.get("label")
        if isinstance(label, str) and label.strip():
            return {"id": node_id, "label": label}
        return {"id": node_id, "label": node_id}
    return None


def _vis_edge(raw_edge: Any) -> dict[str, Any] | None:
    """把内部标准边或已有 vis 边统一成 {from,to,...}，权重按边标签显示。"""
    if not isinstance(raw_edge, dict):
        return None

    if "from" in raw_edge or "to" in raw_edge:
        from_node = str(raw_edge.get("from") or "").strip()
        to_node = str(raw_edge.get("to") or "").strip()
        if not from_node or not to_node:
            return None
        edge = {
            field: raw_edge[field]
            for field in _VIS_EDGE_FIELDS
            if field in raw_edge
        }
        edge["from"] = from_node
        edge["to"] = to_node
        return edge

    source = str(raw_edge.get("source") or "").strip()
    target = str(raw_edge.get("target") or "").strip()
    if not source or not target:
        return None

    edge_type = str(raw_edge.get("edge_type") or "directed").strip()
    vis_edge: dict[str, Any] = {
        "from": source,
        "to": target,
        "arrows": _EDGE_ARROWS.get(edge_type, ""),
        "dashes": edge_type in _DASHED_EDGE_TYPES,
    }
    weight = raw_edge.get("weight")
    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
        vis_edge["weight"] = weight
        vis_edge["label"] = format(weight, ".6g")
    return vis_edge


def project_causal_graph(graph: Any) -> dict[str, list[dict[str, Any]]] | None:
    """把内部因果图投影成前端 vis-network 可渲染的节点和边。

    Agent 内部使用 StandardizedGraph 表达算法结果：节点是变量名列表，边用
    source/target 和 edge_type 描述。前端只接受 vis-network 的 {id,label} 节点与
    {from,to} 边，因此公开发给浏览器的图必须经过这一层投影。已经是 vis 格式的
    载荷按原样通过，保证同一份数据重复投影时结果不变。

    返回 None 表示载荷不是可识别的图，调用方需要自行决定回退方式。
    """
    if not isinstance(graph, dict):
        return None
    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return None

    nodes: list[dict[str, Any]] = []
    for raw_node in raw_nodes:
        node = _vis_node(raw_node)
        if node is None:
            return None
        nodes.append(node)

    edges: list[dict[str, Any]] = []
    for raw_edge in raw_edges:
        edge = _vis_edge(raw_edge)
        if edge is None:
            return None
        edges.append(edge)

    return {"nodes": nodes, "edges": edges}


def prepare_ai_response_for_storage(ai_response: Any) -> tuple[str, list[dict[str, str]]]:
    """把 AI 响应转换为聊天主表内容和附件列表。

    结构化报告只写入 ``report_document`` 附件，主表保存报告标题作为会话预览；
    新报告不再写入 ``visualization`` 附件，也不把完整报告 JSON 放进主表正文。
    旧报告附件格式仍由历史读取路径兼容。
    """
    attachment_to_save: list[dict[str, str]] = []

    if isinstance(ai_response, dict):
        if ai_response.get("type") == "report" and "document" in ai_response:
            document = ai_response["document"]
            if isinstance(document, ReportDocument):
                payload = document.model_dump(mode="json")
            else:
                payload = parse_report_document(document)
            attachment_to_save.append({
                "type": "report_document",
                "content": json.dumps(payload, ensure_ascii=False),
            })
            if ai_response.get("references"):
                attachment_to_save.append({
                    "type": "web_search_references",
                    "content": json.dumps(ai_response["references"], ensure_ascii=False),
                })
            preview = payload.get("title")
            return (
                preview if isinstance(preview, str) and preview.strip() else "因果分析报告",
                attachment_to_save,
            )

        raw_summary = ai_response.get("raw_summary") or ai_response.get("summary")
        ai_content = raw_summary

        if ai_content is None:
            ai_content = json.dumps(ai_response, ensure_ascii=False)

        if ai_response.get("type") == "causal_graph" and "data" in ai_response:
            persisted_response = dict(ai_response)
            if raw_summary is not None:
                persisted_response["summary"] = raw_summary
            persisted_response.pop("raw_summary", None)
            persisted_response.pop("visualization_mapping", None)
            persisted_response.pop("references", None)
            attachment_to_save.append({
                "type": "causal_graph",
                "content": json.dumps(persisted_response, ensure_ascii=False),
            })

        if ai_response.get("visualization_mapping"):
            attachment_to_save.append({
                "type": "visualization",
                "content": json.dumps(ai_response["visualization_mapping"], ensure_ascii=False),
            })

        if ai_response.get("references"):
            attachment_to_save.append({
                "type": "web_search_references",
                "content": json.dumps(ai_response["references"], ensure_ascii=False),
            })

        return ai_content, attachment_to_save

    if isinstance(ai_response, str):
        return ai_response, attachment_to_save

    return json.dumps(ai_response, ensure_ascii=False), attachment_to_save
