"""结构化报告文档模型、块校验和后端装配入口。

文档职责：定义新报告的 ``ReportDraft``/``ReportDocument`` 结构、图表与因果图资源
模型、来源与证据模型，并提供把模型草稿装配成完整报告文档的唯一校验入口。

适用范围：Agent 报告生成、报告附件持久化与历史读取；不涉及数据库迁移、前端渲染
和旧报告格式的兼容渲染。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REPORT_SCHEMA_VERSION = 1

CHART_TYPES: tuple[str, ...] = ("histogram", "bar", "heatmap")

GRAPH_EDGE_TYPES: tuple[str, ...] = (
    "directed",
    "undirected",
    "bidirected",
    "partially_directed",
    "partially_oriented",
)


class ReportSchemaError(ValueError):
    """报告草稿、资源清单或报告附件不符合报告 schema。"""


class ReportModel(BaseModel):
    """报告文档模型的公共严格配置：不接受未知字段。"""

    model_config = ConfigDict(extra="forbid")


def _non_blank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def new_report_id() -> str:
    """生成报告 ID；前缀加随机 UUID，不编码标题或内容。"""

    return f"report_{uuid.uuid4().hex}"


def new_source_id() -> str:
    """生成来源 ID；前缀加随机 UUID，不编码文件名或数组位置。"""

    return f"src_{uuid.uuid4().hex}"


def new_evidence_id() -> str:
    """生成证据 ID；前缀加随机 UUID，不编码标题或数组位置。"""

    return f"ev_{uuid.uuid4().hex}"


class MarkdownBlock(ReportModel):
    """报告中唯一允许出现 Markdown 的块。"""

    id: str
    type: Literal["markdown"] = "markdown"
    content: str
    evidence_refs: list[str] = Field(default_factory=list)

    _validate_id = field_validator("id")(lambda value: _non_blank(value, field_name="id"))


class ChartBlock(ReportModel):
    """引用一份动态图表资源。"""

    id: str
    type: Literal["chart"] = "chart"
    title: str | None = None
    asset_key: str

    _validate_id = field_validator("id")(lambda value: _non_blank(value, field_name="id"))
    _validate_asset_key = field_validator("asset_key")(
        lambda value: _non_blank(value, field_name="asset_key")
    )


class CausalGraphBlock(ReportModel):
    """引用一份因果图资源。"""

    id: str
    type: Literal["causal_graph"] = "causal_graph"
    title: str | None = None
    asset_key: str

    _validate_id = field_validator("id")(lambda value: _non_blank(value, field_name="id"))
    _validate_asset_key = field_validator("asset_key")(
        lambda value: _non_blank(value, field_name="asset_key")
    )


class SectionBlock(ReportModel):
    """包含其他报告块的章节。"""

    id: str
    type: Literal["section"] = "section"
    title: str | None = None
    children: list["ReportBlock"] = Field(default_factory=list)

    _validate_id = field_validator("id")(lambda value: _non_blank(value, field_name="id"))


ReportBlock = Annotated[
    Union[SectionBlock, MarkdownBlock, ChartBlock, CausalGraphBlock],
    Field(discriminator="type"),
]

SectionBlock.model_rebuild()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_sequence(value: Any, *, field_name: str, allow_empty: bool = False) -> list[float]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{field_name} 必须是数值列表")
    if not all(_is_number(item) for item in value):
        raise ValueError(f"{field_name} 只能包含数值")
    return value


def validate_chart_data(chart_type: str, data: dict[str, Any]) -> None:
    """校验图表资源的数据结构与图表类型一致。"""

    if chart_type not in CHART_TYPES:
        raise ValueError(f"不支持的图表类型: {chart_type}")
    if not isinstance(data, dict):
        raise ValueError("图表数据必须是对象")

    if chart_type == "histogram":
        bins = _number_sequence(data.get("bins"), field_name="bins")
        counts = _number_sequence(data.get("counts"), field_name="counts", allow_empty=True)
        if len(counts) != len(bins) - 1:
            raise ValueError("直方图 counts 数量必须等于 bins 数量减一")
        return

    if chart_type == "bar":
        categories = data.get("categories")
        if not isinstance(categories, list) or not categories:
            raise ValueError("柱状图 categories 必须是非空列表")
        if not all(isinstance(item, str) and item.strip() for item in categories):
            raise ValueError("柱状图 categories 只能包含非空字符串")
        counts = _number_sequence(data.get("counts"), field_name="counts", allow_empty=True)
        if len(counts) != len(categories):
            raise ValueError("柱状图 counts 数量必须等于 categories 数量")
        return

    variables = data.get("variables")
    matrix = data.get("matrix")
    if not isinstance(variables, list) or len(variables) < 2:
        raise ValueError("热力图 variables 至少需要两个变量")
    if not all(isinstance(item, str) and item.strip() for item in variables):
        raise ValueError("热力图 variables 只能包含非空字符串")
    if not isinstance(matrix, list) or len(matrix) != len(variables):
        raise ValueError("热力图 matrix 行数必须等于 variables 数量")
    for row in matrix:
        values = _number_sequence(row, field_name="matrix", allow_empty=True)
        if len(values) != len(variables):
            raise ValueError("热力图 matrix 列数必须等于 variables 数量")


class ChartAsset(ReportModel):
    """报告引用的动态图表资源；只保存经过校验的图表数据，不保存 Base64 图片。"""

    asset_key: str
    type: Literal["chart"] = "chart"
    chart_type: Literal["histogram", "bar", "heatmap"]
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    _validate_asset_key = field_validator("asset_key")(
        lambda value: _non_blank(value, field_name="asset_key")
    )

    @model_validator(mode="after")
    def validate_data(self) -> "ChartAsset":
        validate_chart_data(self.chart_type, self.data)
        return self


class CausalGraphNode(ReportModel):
    """因果图中的一个变量节点。"""

    id: str
    variable: str
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class CausalGraphEdge(ReportModel):
    """因果图中的一条边。"""

    id: str
    source: str
    target: str
    edge_type: Literal[
        "directed",
        "undirected",
        "bidirected",
        "partially_directed",
        "partially_oriented",
    ] = "directed"
    weight: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class CausalGraphModel(ReportModel):
    """报告引用的因果图业务模型；前端通过投影函数转换为 vis-network 数据。"""

    graph_id: str
    schema_version: int = REPORT_SCHEMA_VERSION
    nodes: list[CausalGraphNode] = Field(default_factory=list)
    edges: list[CausalGraphEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> "CausalGraphModel":
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("因果图节点 ID 必须唯一")
        edge_ids = [edge.id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("因果图边 ID 必须唯一")
        known_nodes = set(node_ids)
        missing = {
            endpoint
            for edge in self.edges
            for endpoint in (edge.source, edge.target)
            if endpoint not in known_nodes
        }
        if missing:
            raise ValueError("因果图边引用了不存在的节点: " + ", ".join(sorted(missing)))
        return self


class ReportSource(ReportModel):
    """报告的原始来源；不保存原始文件正文。"""

    source_id: str
    kind: Literal["file", "web", "knowledge_base"]
    title: str
    file_id: int | None = None
    url: str | None = None


class ReportEvidence(ReportModel):
    """来源中的一条具体证据；报告块只引用 evidence_id。"""

    evidence_id: str
    source_ids: list[str] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)
    description: str

    _validate_description = field_validator("description")(
        lambda value: _non_blank(value, field_name="description")
    )


class ReportDraft(ReportModel):
    """模型唯一被允许产出的报告内容：标题、块结构、文本、资源与证据引用。"""

    title: str
    blocks: list[ReportBlock] = Field(default_factory=list)

    _validate_title = field_validator("title")(
        lambda value: _non_blank(value, field_name="title")
    )


class ReportDocument(ReportModel):
    """后端装配完成、可以持久化的完整报告文档。"""

    schema_version: int = REPORT_SCHEMA_VERSION
    report_id: str
    title: str
    blocks: list[ReportBlock] = Field(default_factory=list)
    assets: dict[str, ChartAsset | CausalGraphModel] = Field(default_factory=dict)
    sources: list[ReportSource] = Field(default_factory=list)
    evidence_refs: list[ReportEvidence] = Field(default_factory=list)


def iter_report_blocks(blocks: list[ReportBlock]):
    """按先父后子的顺序遍历报告块。"""

    for block in blocks:
        yield block
        if isinstance(block, SectionBlock):
            yield from iter_report_blocks(block.children)


_EVIDENCE_ID_PATTERN = re.compile(r"\bev_[A-Za-z0-9_-]+\b")


def _bind_textual_evidence_refs(
    blocks: list[ReportBlock],
    evidence_ids: set[str],
) -> list[ReportBlock]:
    """把正文中已经写出的合法证据 ID 绑定回 markdown 块。

    模型有时会在正文中写出 ``ev_...``，但漏填结构化的 ``evidence_refs``。
    只从本次报告的证据清单中取 ID，避免把正文里的任意字符串变成引用。
    """

    def bind(block: ReportBlock) -> ReportBlock:
        if isinstance(block, MarkdownBlock):
            textual_refs = [
                evidence_id
                for evidence_id in _EVIDENCE_ID_PATTERN.findall(block.content)
                if evidence_id in evidence_ids
            ]
            merged_refs = list(dict.fromkeys([*block.evidence_refs, *textual_refs]))
            if merged_refs == block.evidence_refs:
                return block
            return block.model_copy(update={"evidence_refs": merged_refs})

        if isinstance(block, SectionBlock):
            children = [bind(child) for child in block.children]
            if children == block.children:
                return block
            return block.model_copy(update={"children": children})

        return block

    return [bind(block) for block in blocks]


def build_report_document(
    draft: ReportDraft,
    *,
    assets: dict[str, ChartAsset | CausalGraphModel] | None = None,
    sources: list[ReportSource] | None = None,
    evidence_refs: list[ReportEvidence] | None = None,
) -> ReportDocument:
    """校验报告草稿和资源引用，装配出完整的 ``ReportDocument``。

    校验失败抛 ``ReportSchemaError``；调用方负责进入受控错误路径，不保存部分报告。
    """

    validated_draft = (
        draft if isinstance(draft, ReportDraft) else ReportDraft.model_validate(draft)
    )
    available_assets = dict(assets or {})
    available_evidence = list(evidence_refs or [])
    available_sources = list(sources or [])
    evidence_ids = {item.evidence_id for item in available_evidence}
    source_ids = {item.source_id for item in available_sources}
    normalized_blocks = _bind_textual_evidence_refs(validated_draft.blocks, evidence_ids)

    for evidence in available_evidence:
        unknown_sources = [item for item in evidence.source_ids if item not in source_ids]
        if unknown_sources:
            raise ReportSchemaError("证据引用了不存在的来源")

    seen_block_ids: set[str] = set()
    for block in iter_report_blocks(normalized_blocks):
        if block.id in seen_block_ids:
            raise ReportSchemaError(f"报告块 ID 重复: {block.id}")
        seen_block_ids.add(block.id)

        if isinstance(block, ChartBlock):
            asset = available_assets.get(block.asset_key)
            if not isinstance(asset, ChartAsset):
                raise ReportSchemaError(f"未知的图表资源: {block.asset_key}")
        elif isinstance(block, CausalGraphBlock):
            asset = available_assets.get(block.asset_key)
            if not isinstance(asset, CausalGraphModel):
                raise ReportSchemaError(f"未知的因果图资源: {block.asset_key}")
        elif isinstance(block, MarkdownBlock):
            unknown_evidence = [
                item for item in block.evidence_refs if item not in evidence_ids
            ]
            if unknown_evidence:
                raise ReportSchemaError(
                    "报告块引用了不存在的证据: " + ", ".join(unknown_evidence)
                )

    return ReportDocument(
        report_id=new_report_id(),
        title=validated_draft.title,
        blocks=normalized_blocks,
        assets=available_assets,
        sources=available_sources,
        evidence_refs=available_evidence,
    )


def report_document_summary(document: ReportDocument, *, max_chars: int = 2400) -> str:
    """把报告文档压缩成面向模型和路由的文本摘要，不包含图表数据。"""

    lines: list[str] = [f"# {document.title}"]

    def render_blocks(blocks: list[ReportBlock], depth: int) -> None:
        for block in blocks:
            prefix = "  " * depth
            if isinstance(block, SectionBlock):
                if block.title:
                    lines.append(f"{prefix}## {block.title}")
                render_blocks(block.children, depth + 1)
            elif isinstance(block, MarkdownBlock):
                lines.append(prefix + block.content.strip())
            elif isinstance(block, ChartBlock):
                lines.append(f"{prefix}[图表] {block.title or block.asset_key}")
            else:
                lines.append(f"{prefix}[因果图] {block.title or block.asset_key}")

    render_blocks(document.blocks, 0)
    summary = "\n".join(line for line in lines if line.strip())
    if len(summary) > max_chars:
        return summary[:max_chars] + "\n…（摘要已截断）"
    return summary


def build_degraded_report_document(
    message: str,
    *,
    title: str = "因果分析报告",
) -> ReportDocument:
    """构造只含失败说明的降级报告文档，供报告节点错误路径使用。"""

    return ReportDocument(
        report_id=new_report_id(),
        title=title,
        blocks=[MarkdownBlock(id="markdown_degraded", content=message)],
        assets={},
        sources=[],
        evidence_refs=[],
    )


def parse_report_document(payload: Any) -> dict[str, Any]:
    """校验持久化的报告文档 JSON，返回可发送给前端的字典。

    非法结构抛 ``ReportSchemaError``，调用方负责记录降级事件并回退到消息预览正文。
    """

    if isinstance(payload, ReportDocument):
        return payload.model_dump(mode="json")
    if not isinstance(payload, Mapping):
        raise ReportSchemaError("报告文档必须是 JSON 对象")
    try:
        document = ReportDocument.model_validate(dict(payload))
    except Exception as exc:
        raise ReportSchemaError("报告文档 schema 校验失败") from exc
    return document.model_dump(mode="json")
