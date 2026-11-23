"""Render the accepted time-series result directly into the public report."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from html import escape
from typing import Any

from Agent.deep_agent_tools.time_series import TimeSeriesResult
from Agent.Report.document import (
    CausalGraphBlock, CausalGraphEdge, CausalGraphModel, CausalGraphNode,
    MarkdownBlock, ReportBlock, iter_report_blocks,
)


_MARKDOWN_ENTITIES = str.maketrans({char: f"&#{ord(char)};" for char in "\\*_[]|" + chr(96)})


def _cell(value: Any) -> str:
    return escape(" ".join(str(value).split()), quote=False).translate(_MARKDOWN_ENTITIES)


def build_time_series_overview(primary: Mapping[str, Any] | None) -> CausalGraphModel | None:
    """Collapse repeated lags for display only; never publish a StandardizedGraph."""
    if primary is None or primary.get("time_series") is None:
        return None
    series = TimeSeriesResult.model_validate(primary["time_series"])
    directed = defaultdict(list)
    uncertain = {}
    for relation in series.relations:
        if relation.mark == "-->":
            directed[relation.source, relation.target].append(relation.lag_steps)
        else:
            uncertain[tuple(sorted((relation.source, relation.target)))] = relation.mark
    if not (directed or uncertain):
        return None
    edges = [CausalGraphEdge(
        id=f"lag_{i}", source=source, target=target, edge_type="directed",
        metadata={"lag_steps": sorted(lags)},
    ) for i, ((source, target), lags) in enumerate(sorted(directed.items()))]
    edges.extend(CausalGraphEdge(
        id=f"uncertain_{i}", source=source, target=target, edge_type="undirected",
        metadata={"mark": mark},
    ) for i, ((source, target), mark) in enumerate(sorted(uncertain.items())))
    return CausalGraphModel(
        graph_id="time_series_overview", edges=edges,
        nodes=[CausalGraphNode(id=name, variable=name, label=name) for name in series.columns],
        metadata={"algorithm": "causal.pcmci_plus", "purpose": "time_series_display_only"},
    )


def with_time_series_report_block(
    blocks: list[ReportBlock], *, discovery_goal: str | None, primary: Mapping[str, Any] | None,
    overview: CausalGraphModel | None = None,
) -> list[ReportBlock]:
    """Use the same relations and rendering for initial, regenerated and degraded reports."""
    if discovery_goal != "time_series":
        return blocks
    series = TimeSeriesResult.model_validate(primary["time_series"]) if primary is not None else None
    lines = ["### 时序因果发现", ""]
    if series is None:
        lines.append("本次时序目标未完成，没有可采纳的时序结果。静态结果只能作为补充，不能据此判断时间先后或滞后关系。")
    else:
        axis = series.time_axis
        minimum_seconds = series.tau_min * axis.sampling_period_seconds
        maximum_seconds = series.tau_max * axis.sampling_period_seconds
        lines.extend([
            f"- 时间列：{_cell(axis.time_column)}；片段：{axis.start_time.isoformat()} 至 {axis.end_time.isoformat()}。",
            f"- 选段规则：{_cell(axis.selection_rule)}；{axis.row_count} 行，采样周期 {axis.sampling_period_seconds} 秒。",
            f"- 搜索滞后：{series.tau_min} 至 {series.tau_max} 步（{minimum_seconds} 至 {maximum_seconds} 秒）；"
            f"PCMCI+ 使用 ParCorr，pc_alpha = {series.pc_alpha}。",
            f"- 保留 {len(series.relations)} 条候选关系。偏相关统计量不是干预效应；因果平稳性、线性检验条件和未观测混杂仍限制解释。",
            "",
        ])
        if not series.relations:
            lines.append("算法执行成功，在上述搜索范围与配置下未检出候选关系。这不能证明关系不存在。")
        else:
            lines.extend([
                "| 源指标 | 目标指标 | 滞后步数 | 滞后秒数 | 原始标记 | 方向状态 | p 值 | 偏相关统计量 |",
                "| --- | --- | ---: | ---: | --- | --- | ---: | ---: |",
            ])
            for relation in series.relations:
                direction = (
                    "滞后指向" if relation.lag_steps > 0 else
                    {"-->": "同期有向", "o-o": "同期未定向", "x-x": "同期方向冲突"}[relation.mark]
                )
                mark = f"{chr(96)}{relation.mark}{chr(96)}"
                lines.append(
                    f"| {_cell(relation.source)} | {_cell(relation.target)} | {relation.lag_steps} | "
                    f"{relation.lag_seconds} | {mark} | {direction} | {relation.p_value} | {relation.value} |"
                )

    occupied = {block.id for block in iter_report_blocks(blocks)}
    block_id = "time_series_findings"
    while block_id in occupied:
        block_id += "_"
    findings = MarkdownBlock(id=block_id, content="\n".join(lines))
    if overview is None:
        return [findings, *blocks]
    overview_id = "time_series_indicator_graph"
    while overview_id in occupied:
        overview_id += "_"
    return [CausalGraphBlock(id=overview_id, asset_key=overview.graph_id, title="指标关系总览（仅供展示）"), findings, *blocks]
