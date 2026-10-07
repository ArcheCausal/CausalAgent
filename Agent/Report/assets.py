"""报告资源构建：结构化图表、因果图业务模型、来源与证据清单。

文档职责：把预处理得到的 DataFrame、数据摘要、算法结果、检索证据转换为
``ReportDocument`` 需要的图表资源、因果图资源和来源/证据清单。

适用范围：Agent 报告生成；不负责报告文档校验装配、持久化和前端渲染。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from Agent.Report.document import (
    ChartAsset,
    CausalGraphEdge,
    CausalGraphModel,
    CausalGraphNode,
    ReportEvidence,
    ReportSource,
    new_evidence_id,
    new_source_id,
)


CORRELATION_HEATMAP_KEY = "chart_heatmap_correlation"

_CONTINUOUS_TYPES = frozenset({"continuous"})
_CATEGORICAL_TYPES = frozenset(
    {"binary", "categorical_numeric", "binary_categorical", "categorical"}
)
_MAX_BAR_CATEGORIES = 50
_MAX_EVIDENCE_DESCRIPTION_CHARS = 400


def _slug(value: Any) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(value or ""), flags=re.UNICODE)
    return text.strip("_")


def chart_asset_key(chart_type: str, variable: str | None = None) -> str:
    """返回稳定、可读的图表资源键。"""

    if chart_type == "histogram":
        return f"chart_histogram_{variable}"
    if chart_type == "bar":
        return f"chart_bar_{variable}"
    return CORRELATION_HEATMAP_KEY


def _histogram_bin_count(series: pd.Series) -> int:
    """给直方图固定的分箱数量上界，保证资源体积可预期。"""

    sample = int(series.shape[0])
    estimated = int(math.ceil(math.sqrt(max(sample, 1))))
    return max(5, min(20, estimated))


def build_chart_assets(
    df: pd.DataFrame,
    analysis_parameters: Mapping[str, Any] | None,
) -> dict[str, ChartAsset]:
    """根据数据摘要为报告生成直方图、分类柱状图和相关性热力图资源。"""

    params = dict(analysis_parameters or {})
    column_profiles = params.get("column_profiles") or {}
    if not isinstance(column_profiles, Mapping):
        return {}

    assets: dict[str, ChartAsset] = {}
    numeric_columns: list[str] = []

    for column, profile in column_profiles.items():
        if column not in df.columns or not isinstance(profile, Mapping):
            continue
        inferred_type = profile.get("inferred_type")
        if inferred_type in _CONTINUOUS_TYPES:
            asset = _histogram_asset(df, str(column), profile)
            if asset is not None:
                numeric_columns.append(str(column))
                assets[asset.asset_key] = asset
        elif inferred_type in _CATEGORICAL_TYPES:
            asset = _bar_asset(df, str(column), profile)
            if asset is not None:
                assets[asset.asset_key] = asset

    heatmap = _correlation_heatmap_asset(df, numeric_columns, params)
    if heatmap is not None:
        assets[heatmap.asset_key] = heatmap

    return assets


def _histogram_asset(
    df: pd.DataFrame,
    column: str,
    profile: Mapping[str, Any],
) -> ChartAsset | None:
    series = pd.to_numeric(df[column], errors="coerce").dropna()
    if series.empty:
        return None
    counts, edges = np.histogram(
        series.to_numpy(dtype=float),
        bins=_histogram_bin_count(series),
    )
    return ChartAsset(
        asset_key=chart_asset_key("histogram", column),
        chart_type="histogram",
        data={
            "bins": [float(edge) for edge in edges],
            "counts": [int(count) for count in counts],
        },
        metadata={
            "variable": column,
            "sample_count": int(series.shape[0]),
            "inferred_type": profile.get("inferred_type", ""),
            "unit": None,
        },
        options={"show_tooltip": True},
    )


def _bar_asset(
    df: pd.DataFrame,
    column: str,
    profile: Mapping[str, Any],
) -> ChartAsset | None:
    unique_count = profile.get("unique_count")
    if isinstance(unique_count, (int, float)) and not isinstance(unique_count, bool):
        if int(unique_count) >= _MAX_BAR_CATEGORIES:
            return None

    series = df[column].dropna().astype(str)
    if series.empty:
        return None
    counts = series.value_counts()
    if counts.shape[0] >= _MAX_BAR_CATEGORIES:
        return None
    # 同频类别按名称排序，保证同一份数据生成的资源稳定可复现。
    ordered = sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
    return ChartAsset(
        asset_key=chart_asset_key("bar", column),
        chart_type="bar",
        data={
            "categories": [str(category) for category, _ in ordered],
            "counts": [int(count) for _, count in ordered],
        },
        metadata={
            "variable": column,
            "sample_count": int(series.shape[0]),
            "inferred_type": profile.get("inferred_type", ""),
            "unit": None,
        },
        options={"show_tooltip": True},
    )


def _correlation_heatmap_asset(
    df: pd.DataFrame,
    numeric_columns: list[str],
    params: Mapping[str, Any],
) -> ChartAsset | None:
    usable = [
        column
        for column in numeric_columns
        if column in df.columns and pd.to_numeric(df[column], errors="coerce").notna().any()
    ]
    if len(usable) < 2:
        return None

    frame = df[usable].apply(pd.to_numeric, errors="coerce")
    correlation = frame.corr().fillna(0.0)
    matrix = [
        [round(float(correlation.iloc[row][column]), 6) for column in usable]
        for row in range(len(usable))
    ]
    return ChartAsset(
        asset_key=CORRELATION_HEATMAP_KEY,
        chart_type="heatmap",
        data={"variables": list(usable), "matrix": matrix},
        metadata={
            "variable": None,
            "sample_count": int(params.get("n_rows") or frame.shape[0]),
            "unit": None,
        },
        options={"show_tooltip": True},
    )


def coerce_chart_assets(raw_assets: Mapping[str, Any] | None) -> dict[str, ChartAsset]:
    """把 State 中保存的图表资源重新校验成 ``ChartAsset``，非法资源直接丢弃。"""

    assets: dict[str, ChartAsset] = {}
    for key, raw in dict(raw_assets or {}).items():
        if isinstance(raw, ChartAsset):
            assets[str(key)] = raw
            continue
        if isinstance(raw, Mapping):
            try:
                asset = ChartAsset.model_validate(dict(raw))
            except Exception:
                continue
            assets[asset.asset_key] = asset
    return assets


_VIS_EDGE_TYPE_BY_ARROWS = {
    "to": "directed",
    "to,from": "bidirected",
    "from": "directed",
    "": "undirected",
}


def _graph_node_variables(raw_nodes: Any) -> list[tuple[str, str]] | None:
    """把算法图或 vis-network 图的节点统一成 (variable, label) 列表。"""

    if not isinstance(raw_nodes, (list, tuple)) or not raw_nodes:
        return None
    variables: list[tuple[str, str]] = []
    for raw in raw_nodes:
        if isinstance(raw, str):
            variable = raw.strip()
            if not variable:
                return None
            label = variable
        elif isinstance(raw, Mapping):
            raw_variable = raw.get("variable") or raw.get("label") or raw.get("id")
            if not isinstance(raw_variable, str) or not raw_variable.strip():
                return None
            variable = raw_variable.strip()
            raw_label = raw.get("label")
            label = (
                raw_label.strip()
                if isinstance(raw_label, str) and raw_label.strip()
                else variable
            )
        else:
            return None
        variables.append((variable, label))
    return variables


def _assign_node_ids(variables: list[tuple[str, str]]) -> list[str]:
    """按变量名生成稳定、唯一的节点 ID，返回顺序与 ``variables`` 一一对应。"""

    used: set[str] = set()
    node_ids: list[str] = []
    for index, (variable, _) in enumerate(variables):
        base = _slug(variable) or str(index)
        candidate = f"node_{base}"
        suffix = 2
        while candidate in used:
            candidate = f"node_{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        node_ids.append(candidate)
    return node_ids


def _vis_edge_type(raw_edge: Mapping[str, Any]) -> str:
    if "edge_type" in raw_edge and isinstance(raw_edge["edge_type"], str):
        return raw_edge["edge_type"]
    arrows = raw_edge.get("arrows")
    if isinstance(arrows, str):
        return _VIS_EDGE_TYPE_BY_ARROWS.get(arrows.strip(), "directed")
    if raw_edge.get("dashes") is True:
        return "partially_directed"
    return "directed"


def build_causal_graph_model(
    graph: Any,
    *,
    graph_id: str,
    algorithm: str = "",
    graph_source: str = "original",
    revision_summary: str = "",
) -> CausalGraphModel | None:
    """把算法图或修订图转换成报告使用的因果图业务模型。"""

    if not isinstance(graph, Mapping):
        return None
    variables = _graph_node_variables(graph.get("nodes"))
    if variables is None:
        return None
    raw_edges = graph.get("edges")
    if not isinstance(raw_edges, (list, tuple)):
        return None

    node_ids = _assign_node_ids(variables)
    variable_by_node_id = {
        node_id: variable for (variable, _), node_id in zip(variables, node_ids)
    }
    nodes = [
        CausalGraphNode(
            id=node_id,
            variable=variable,
            label=label,
            metadata={},
            evidence_refs=[],
        )
        for (variable, label), node_id in zip(variables, node_ids)
    ]

    used_edge_ids: set[str] = set()
    edges: list[CausalGraphEdge] = []
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            return None
        source_id = _resolve_endpoint(
            raw_edge.get("source", raw_edge.get("from")),
            variables,
            node_ids,
        )
        target_id = _resolve_endpoint(
            raw_edge.get("target", raw_edge.get("to")),
            variables,
            node_ids,
        )
        if source_id is None or target_id is None:
            return None

        weight = raw_edge.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            weight = None

        edge_id = _unique_edge_id(
            _slug(variable_by_node_id.get(source_id, source_id)) or source_id,
            _slug(variable_by_node_id.get(target_id, target_id)) or target_id,
            used_edge_ids,
        )
        edges.append(
            CausalGraphEdge(
                id=edge_id,
                source=source_id,
                target=target_id,
                edge_type=_vis_edge_type(raw_edge),
                weight=float(weight) if weight is not None else None,
                metadata={},
                evidence_refs=[],
            )
        )

    metadata: dict[str, Any] = {"graph_source": graph_source}
    if algorithm:
        metadata["algorithm"] = algorithm
    semantics = graph.get("graph_semantics")
    if isinstance(semantics, str) and semantics.strip():
        metadata["graph_semantics"] = semantics
    if revision_summary:
        metadata["revision_summary"] = revision_summary

    return CausalGraphModel(
        graph_id=graph_id,
        nodes=nodes,
        edges=edges,
        metadata=metadata,
    )


def _resolve_endpoint(
    raw_value: Any,
    variables: list[tuple[str, str]],
    node_ids: list[str],
) -> str | None:
    """把边端点还原成报告图的节点 ID，同时接受变量名和 vis 节点 ID。"""

    if not isinstance(raw_value, str):
        return None
    candidate = raw_value.strip()
    if not candidate:
        return None
    if candidate in node_ids:
        return candidate
    for (variable, _), node_id in zip(variables, node_ids):
        if candidate == variable:
            return node_id
    return None


def _unique_edge_id(source: str, target: str, used: set[str]) -> str:
    candidate = f"edge_{source}_{target}"
    suffix = 2
    while candidate in used:
        candidate = f"edge_{source}_{target}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _truncate(value: Any, limit: int = _MAX_EVIDENCE_DESCRIPTION_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


class ReportResourceIndex:
    """报告可引用的来源与证据清单，同时提供 source_id 去重索引。"""

    def __init__(self) -> None:
        self.sources: list[ReportSource] = []
        self.evidence_refs: list[ReportEvidence] = []
        self._source_ids: dict[tuple[str, str, str], str] = {}

    def add_source(
        self,
        *,
        kind: str,
        title: str,
        file_id: int | None = None,
        url: str | None = None,
    ) -> str:
        normalized_url = (url or "").strip()
        key = (kind, str(file_id or ""), normalized_url or title)
        existing = self._source_ids.get(key)
        if existing is not None:
            return existing
        source = ReportSource(
            source_id=new_source_id(),
            kind=kind,
            title=title,
            file_id=file_id,
            url=normalized_url or None,
        )
        self.sources.append(source)
        self._source_ids[key] = source.source_id
        return source.source_id

    def add_evidence(
        self,
        *,
        source_ids: Iterable[str],
        locator: Mapping[str, Any] | None,
        description: str,
    ) -> ReportEvidence | None:
        text = _truncate(description)
        linked = [source_id for source_id in dict.fromkeys(source_ids) if source_id]
        if not text or not linked:
            return None
        evidence = ReportEvidence(
            evidence_id=new_evidence_id(),
            source_ids=linked,
            locator=dict(locator or {}),
            description=text,
        )
        self.evidence_refs.append(evidence)
        return evidence


def build_report_resources(
    *,
    file_summary: Mapping[str, Any] | None,
    web_search_result: Mapping[str, Any] | None,
    rag_evidence: Mapping[str, Any] | None,
    web_evidence: Mapping[str, Any] | None,
) -> ReportResourceIndex:
    """根据冻结文件、联网搜索和检索证据构建报告可引用的来源与证据清单。"""

    index = ReportResourceIndex()
    file_summary = dict(file_summary or {})
    file_source_id = _add_file_source(index, file_summary)
    _add_web_search_sources(index, web_search_result)
    _add_rag_evidence(index, rag_evidence)
    _add_web_evidence(index, web_evidence)
    _add_file_evidence(index, file_summary, file_source_id)
    return index


def _add_file_source(
    index: ReportResourceIndex,
    file_summary: Mapping[str, Any],
) -> str | None:
    user_file_id = file_summary.get("user_file_id")
    filename = file_summary.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        return None
    file_id = int(user_file_id) if isinstance(user_file_id, int) else None
    return index.add_source(kind="file", title=filename, file_id=file_id)


def _add_file_evidence(
    index: ReportResourceIndex,
    file_summary: Mapping[str, Any],
    file_source_id: str | None,
) -> None:
    if file_source_id is None:
        return
    columns = [
        str(column)
        for column in (file_summary.get("columns") or [])
        if str(column).strip()
    ]
    rows = file_summary.get("rows")
    locator: dict[str, Any] = {}
    if columns:
        locator["columns"] = columns
    if isinstance(rows, int) and not isinstance(rows, bool):
        locator["rows"] = [1, rows]
    filename = str(file_summary.get("filename") or "输入数据")
    index.add_evidence(
        source_ids=[file_source_id],
        locator=locator,
        description=f"{filename} 是本次因果分析使用的冻结数据文件。",
    )


def _add_web_search_sources(
    index: ReportResourceIndex,
    web_search_result: Mapping[str, Any] | None,
) -> None:
    if not isinstance(web_search_result, Mapping) or not web_search_result.get("success"):
        return
    content = web_search_result.get("content")
    if not isinstance(content, (list, tuple)):
        return
    for item in content:
        if not isinstance(item, Mapping):
            continue
        url = item.get("url")
        title = item.get("title") or url
        if not isinstance(url, str) or not url.strip():
            continue
        index.add_source(kind="web", title=str(title or url), url=url)


def _evidence_source_id(
    index: ReportResourceIndex,
    *,
    source_url: Any,
    source_title: Any,
    kind: str,
) -> str | None:
    """按证据通道构造来源；kind 由调用方显式给出，url 只作为可点击地址。"""

    url = source_url if isinstance(source_url, str) and source_url.strip() else None
    title = source_title if isinstance(source_title, str) and source_title.strip() else None
    if title:
        return index.add_source(kind=kind, title=title, url=url)
    if url:
        return index.add_source(kind=kind, title=url, url=url)
    return None


def _evidence_item(raw: Any) -> Mapping[str, Any] | None:
    """把 State 中的证据对象归一化成可读取的映射；不支持的形态返回 None。"""

    if isinstance(raw, Mapping):
        return raw
    model_dump = getattr(raw, "model_dump", None)
    if not callable(model_dump):
        return None
    dumped = model_dump(mode="json")
    return dumped if isinstance(dumped, Mapping) else None


def _add_rag_evidence(
    index: ReportResourceIndex,
    rag_evidence: Mapping[str, Any] | None,
) -> None:
    for raw in dict(rag_evidence or {}).values():
        item = _evidence_item(raw)
        if item is None:
            continue
        source_id = _evidence_source_id(
            index,
            source_url=item.get("source_url"),
            source_title=item.get("source_title"),
            kind="knowledge_base",
        )
        if source_id is None:
            continue
        locator: dict[str, Any] = {}
        if isinstance(item.get("locator"), str) and item["locator"].strip():
            locator["locator"] = item["locator"]
        if isinstance(item.get("modality"), str) and item["modality"].strip():
            locator["modality"] = item["modality"]
        index.add_evidence(
            source_ids=[source_id],
            locator=locator,
            description=item.get("snippet") or "",
        )


def _add_web_evidence(
    index: ReportResourceIndex,
    web_evidence: Mapping[str, Any] | None,
) -> None:
    for raw in dict(web_evidence or {}).values():
        item = _evidence_item(raw)
        if item is None:
            continue
        source_id = _evidence_source_id(
            index,
            source_url=item.get("source_url"),
            source_title=item.get("source_title"),
            kind="web",
        )
        if source_id is None:
            continue
        locator: dict[str, Any] = {}
        if isinstance(item.get("locator"), str) and item["locator"].strip():
            locator["locator"] = item["locator"]
        index.add_evidence(
            source_ids=[source_id],
            locator=locator,
            description=item.get("snippet") or "",
        )


def describe_chart_asset(asset: ChartAsset) -> str:
    """给模型看的图表资源简述，只描述变量和类型，不含数据点。"""

    variable = asset.metadata.get("variable")
    if asset.chart_type == "histogram":
        return f"{variable} 的分布直方图"
    if asset.chart_type == "bar":
        return f"{variable} 的类别分布柱状图"
    variables = asset.data.get("variables") or []
    return "变量相关性热力图: " + "、".join(str(item) for item in variables)
