"""结构化报告文档的 schema、资源投影、持久化与受控错误路径测试。"""

import asyncio
import json
import os

import pandas as pd
import pytest


for key, value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "mysql",
    "MYSQL_USER": "app",
    "MYSQL_PASSWORD": "password",
    "MYSQL_DATABASE": "causalagent",
}.items():
    os.environ.setdefault(key, value)


from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402
from langgraph.errors import NodeError  # noqa: E402

from Agent.Report.assets import (  # noqa: E402
    build_causal_graph_model,
    build_chart_assets,
    build_report_resources,
    coerce_chart_assets,
)
from Agent.Report.document import (  # noqa: E402
    ChartAsset,
    ReportDraft,
    ReportDocument,
    ReportEvidence,
    ReportSchemaError,
    ReportSource,
    build_degraded_report_document,
    build_report_document,
    parse_report_document,
    report_document_summary,
)
from Agent.causal_agent import nodes  # noqa: E402
from Agent.causal_agent.fault_tolerance import recover_report  # noqa: E402
from Agent.llm_structured_output import StructuredOutputError  # noqa: E402
from app.agent.worker.result_presenter import process_final_result  # noqa: E402
from app.chat.response_storage import prepare_ai_response_for_storage  # noqa: E402


ORIGINAL_GRAPH = {
    "graph_semantics": "dag_target_to_source",
    "nodes": ["age", "income"],
    "edges": [
        {"source": "age", "target": "income", "edge_type": "directed", "weight": 1.5},
    ],
}

REVISED_GRAPH = {
    "nodes": [{"id": "age", "label": "age"}, {"id": "income", "label": "income"}],
    "edges": [{"from": "income", "to": "age", "arrows": "to", "dashes": False}],
}


def _source(source_id: str = "src_1") -> ReportSource:
    return ReportSource(source_id=source_id, kind="file", title="data.csv", file_id=7)


def _evidence(evidence_id: str = "ev_1") -> ReportEvidence:
    return ReportEvidence(
        evidence_id=evidence_id,
        source_ids=["src_1"],
        locator={"columns": ["age"], "rows": [1, 1200]},
        description="年龄和收入字段的相关性统计结果",
    )


def _histogram_asset() -> ChartAsset:
    return ChartAsset(
        asset_key="chart_histogram_age",
        chart_type="histogram",
        data={"bins": [0, 10, 20, 30], "counts": [4, 5, 6]},
        metadata={"variable": "age", "sample_count": 15, "unit": None},
        options={"show_tooltip": True},
    )


def _draft(blocks: list[dict]) -> ReportDraft:
    return ReportDraft.model_validate({"title": "因果分析报告", "blocks": blocks})


class _StructuredModel:
    """最小模型替身：记录提示词并返回固定的结构化草稿，不发起网络调用。"""

    def __init__(self, draft: dict, captured: dict | None = None) -> None:
        self.draft = draft
        self.captured = captured if captured is not None else {}
        self.extra_body: dict = {}

    def model_copy(self, *, update=None, **kwargs):
        return _StructuredModel(self.draft, self.captured)

    def with_structured_output(self, schema, **_kwargs):
        async def invoke(prompt_value):
            self.captured["messages"] = prompt_value.to_messages()
            return schema.model_validate(self.draft)

        return RunnableLambda(invoke)


class _FailingStructuredModel(_StructuredModel):
    def with_structured_output(self, schema, **_kwargs):
        async def invoke(_prompt_value):
            raise RuntimeError("provider rejected tool call")

        return RunnableLambda(invoke)


def _report_state(**overrides) -> dict:
    state = {
        "messages": [HumanMessage(content="分析 age 与 income 的关系")],
        "analysis_parameters": {
            "n_rows": 15,
            "n_cols": 1,
            "columns": ["age"],
            "column_profiles": {
                "age": {
                    "inferred_type": "continuous",
                    "unique_count": 15,
                    "missing_ratio": 0.0,
                    "stats": {"mean": 33.0, "min": 22.0, "max": 50.0},
                    "value_counts": {"25.0": 2, "30.0": 2},
                }
            },
            "quality_assessment": {
                "total_missing_ratio": 0.0,
                "constant_columns": [],
                "high_missing_columns": [],
                "suitable_for_causal": {"good": ["age"]},
            },
        },
        "chart_assets": {"chart_histogram_age": _histogram_asset().model_dump(mode="json")},
        "file_summary": {
            "user_file_id": 7,
            "filename": "data.csv",
            "rows": 15,
            "columns": ["age", "income"],
        },
        "preprocess_summary": "数据概况",
        "causal_analysis_result": {
            "success": True,
            "algorithm": "causal_pc",
            "data": ORIGINAL_GRAPH,
        },
        "knowledge_base_result": {},
        "web_search_result": {},
        "postprocess_result": {},
    }
    state.update(overrides)
    return state


def test_build_report_document_injects_backend_resources() -> None:
    graph = build_causal_graph_model(ORIGINAL_GRAPH, graph_id="graph_main", algorithm="causal_pc")
    document = build_report_document(
        _draft([
            {
                "id": "section_summary",
                "type": "section",
                "title": "结论摘要",
                "children": [
                    {
                        "id": "markdown_summary",
                        "type": "markdown",
                        "content": "## 结论\n\n- 变量 X 与变量 Y 呈正相关",
                        "evidence_refs": ["ev_1"],
                    }
                ],
            },
            {"id": "chart_age", "type": "chart", "title": "年龄分布", "asset_key": "chart_histogram_age"},
            {"id": "graph_main_block", "type": "causal_graph", "title": "主要因果关系", "asset_key": "graph_main"},
        ]),
        assets={"chart_histogram_age": _histogram_asset(), "graph_main": graph},
        sources=[_source()],
        evidence_refs=[_evidence()],
    )

    assert isinstance(document, ReportDocument)
    assert document.report_id.startswith("report_")
    assert document.schema_version == 1
    assert set(document.assets) == {"chart_histogram_age", "graph_main"}
    assert [source.source_id for source in document.sources] == ["src_1"]
    assert [item.evidence_id for item in document.evidence_refs] == ["ev_1"]
    assert parse_report_document(document.model_dump(mode="json")) == document.model_dump(mode="json")


def test_report_draft_rejects_unknown_block_types_and_extra_fields() -> None:
    with pytest.raises(Exception):
        _draft([{"id": "x", "type": "metric_cards", "value": 1}])
    with pytest.raises(Exception):
        _draft([{"id": "x", "type": "markdown", "content": "正文", "bogus": True}])


def test_duplicate_block_ids_are_rejected() -> None:
    with pytest.raises(ReportSchemaError):
        build_report_document(_draft([
            {"id": "dup", "type": "markdown", "content": "a"},
            {"id": "dup", "type": "markdown", "content": "b"},
        ]))


def test_unknown_asset_key_and_resource_type_mismatch_are_rejected() -> None:
    with pytest.raises(ReportSchemaError):
        build_report_document(
            _draft([{"id": "c", "type": "chart", "asset_key": "chart_missing"}]),
            assets={"chart_histogram_age": _histogram_asset()},
        )

    graph = build_causal_graph_model(ORIGINAL_GRAPH, graph_id="graph_main")
    with pytest.raises(ReportSchemaError):
        build_report_document(
            _draft([{"id": "c", "type": "chart", "asset_key": "graph_main"}]),
            assets={"graph_main": graph},
        )
    with pytest.raises(ReportSchemaError):
        build_report_document(
            _draft([{"id": "g", "type": "causal_graph", "asset_key": "chart_histogram_age"}]),
            assets={"chart_histogram_age": _histogram_asset()},
        )


def test_unknown_evidence_reference_is_rejected() -> None:
    with pytest.raises(ReportSchemaError):
        build_report_document(
            _draft([{"id": "m", "type": "markdown", "content": "正文", "evidence_refs": ["ev_unknown"]}]),
            sources=[_source()],
            evidence_refs=[_evidence()],
        )


def test_chart_asset_data_shape_is_validated() -> None:
    with pytest.raises(Exception):
        ChartAsset(asset_key="c", chart_type="histogram", data={"bins": [0, 1, 2], "counts": [1]})
    with pytest.raises(Exception):
        ChartAsset(asset_key="c", chart_type="pie", data={})
    with pytest.raises(Exception):
        ChartAsset(
            asset_key="c",
            chart_type="heatmap",
            data={"variables": ["a", "b"], "matrix": [[1, 2]]},
        )


def test_build_chart_assets_emits_structured_data_without_images() -> None:
    frame = pd.DataFrame({
        "age": [25, 30, 35, 40, 25, 30, 45, 50, 22, 33],
        "income": [50000, 60000, 75000, 80000, 52000, 65000, 90000, 120000, 48000, 70000],
        "city": ["北京", "伦敦", "巴黎", "北京", "伦敦", "东京", "巴黎", "伦敦", "北京", "东京"],
    })
    params = {
        "n_rows": len(frame),
        "columns": list(frame.columns),
        "column_profiles": {
            "age": {"inferred_type": "continuous", "unique_count": 10},
            "income": {"inferred_type": "continuous", "unique_count": 10},
            "city": {"inferred_type": "categorical", "unique_count": 4},
        },
    }

    assets = build_chart_assets(frame, params)
    serialized = json.dumps(
        {key: asset.model_dump(mode="json") for key, asset in assets.items()},
        ensure_ascii=False,
    )

    assert set(assets) == {
        "chart_histogram_age",
        "chart_histogram_income",
        "chart_bar_city",
        "chart_heatmap_correlation",
    }
    assert "data:image" not in serialized
    assert "<img" not in serialized
    histogram = assets["chart_histogram_age"]
    assert len(histogram.data["counts"]) == len(histogram.data["bins"]) - 1
    assert assets["chart_heatmap_correlation"].data["variables"] == ["age", "income"]

    coerced = coerce_chart_assets({key: asset.model_dump(mode="json") for key, asset in assets.items()})
    assert set(coerced) == set(assets)
    assert coerce_chart_assets({"broken": {"asset_key": "broken", "chart_type": "pie"}}) == {}


def test_report_resources_carry_file_web_and_knowledge_base_sources() -> None:
    index = build_report_resources(
        file_summary={"user_file_id": 7, "filename": "data.csv", "rows": 15, "columns": ["age"]},
        web_search_result={"success": True, "content": [{"title": "参考", "url": "https://example.org/1"}]},
        rag_evidence={
            "rag_1": {"evidence_ref": "rag_1", "snippet": "知识库片段", "source_title": "论文 A"}
        },
        web_evidence={
            "web_1": {
                "evidence_ref": "web_1",
                "snippet": "网页片段",
                "source_url": "https://example.org/1",
            }
        },
    )

    assert [source.kind for source in index.sources] == ["file", "web", "knowledge_base"]
    assert [item.description for item in index.evidence_refs] == [
        "知识库片段",
        "网页片段",
        "data.csv 是本次因果分析使用的冻结数据文件。",
    ]
    assert all(item.evidence_id.startswith("ev_") for item in index.evidence_refs)
    assert all(source.source_id.startswith("src_") for source in index.sources)


def test_report_resources_accept_state_evidence_objects_and_dicts_equally() -> None:
    """State 中保存的是 pydantic 证据对象；两种形态必须产出同一份来源与证据。"""
    from Agent.deep_agent_tools.models import EvidenceResult

    evidence = EvidenceResult(
        evidence_ref="rag:mm_unit:1",
        evidence_id="1",
        snippet="知识库片段",
        source_title="Pearl_2009_Causality.pdf",
        locator="Pearl_2009_Causality.pdf#page=12#chunk=unit_a",
        modality="page",
        release_id="mm_unit",
    )
    shared = {
        "file_summary": {"user_file_id": 7, "filename": "data.csv", "rows": 15, "columns": ["age"]},
        "web_search_result": None,
        "web_evidence": None,
    }

    as_object = build_report_resources(rag_evidence={"rag:mm_unit:1": evidence}, **shared)
    as_mapping = build_report_resources(
        rag_evidence={"rag:mm_unit:1": evidence.model_dump(mode="json")}, **shared
    )

    assert [source.kind for source in as_object.sources] == ["file", "knowledge_base"]
    assert [source.model_dump(exclude={"source_id"}) for source in as_object.sources] == [
        source.model_dump(exclude={"source_id"}) for source in as_mapping.sources
    ]
    assert [item.description for item in as_object.evidence_refs] == [
        "知识库片段",
        "data.csv 是本次因果分析使用的冻结数据文件。",
    ]
    assert [item.model_dump(exclude={"evidence_id", "source_ids"}) for item in as_object.evidence_refs] == [
        item.model_dump(exclude={"evidence_id", "source_ids"}) for item in as_mapping.evidence_refs
    ]
    assert as_object.evidence_refs[0].locator == {
        "locator": "Pearl_2009_Causality.pdf#page=12#chunk=unit_a",
        "modality": "page",
    }


def test_knowledge_base_evidence_keeps_its_kind_when_it_has_a_url() -> None:
    """知识库来源即使带可点击地址也保持 knowledge_base，不因 url 存在被判成 web。"""
    index = build_report_resources(
        file_summary=None,
        web_search_result=None,
        rag_evidence={
            "rag:mm_unit:1": {
                "evidence_ref": "rag:mm_unit:1",
                "snippet": "知识库片段",
                "source_title": "论文.pdf",
                "source_url": "https://example.org/paper.pdf",
            }
        },
        web_evidence=None,
    )

    assert [(source.kind, source.title, source.url) for source in index.sources] == [
        ("knowledge_base", "论文.pdf", "https://example.org/paper.pdf")
    ]


def test_unsupported_evidence_payload_is_skipped_without_sources() -> None:
    """无法归一化的证据载荷不产生来源，也不影响其它通道。"""
    index = build_report_resources(
        file_summary=None,
        web_search_result=None,
        rag_evidence={"broken": object()},
        web_evidence=None,
    )

    assert index.sources == []
    assert index.evidence_refs == []


def test_report_graph_asset_prefers_postprocessed_revision() -> None:
    revised = nodes._report_graph_asset(_report_state(postprocess_result={"revised_graph": REVISED_GRAPH}))
    assert revised is not None
    assert revised.graph_id == "graph_main"
    assert revised.metadata["graph_source"] == "postprocessed"
    assert [(edge.source, edge.target) for edge in revised.edges] == [("node_income", "node_age")]
    assert [edge.id for edge in revised.edges] == ["edge_income_age"]

    for postprocess_result in (None, {}, {"revised_graph": []}, {"revised_graph": REVISED_GRAPH, "error": "failed"}):
        original = nodes._report_graph_asset(_report_state(postprocess_result=postprocess_result))
        assert original is not None
        assert original.metadata["graph_source"] == "original"
        assert original.metadata["algorithm"] == "causal_pc"
        assert [(edge.source, edge.target) for edge in original.edges] == [("node_age", "node_income")]
        assert [edge.id for edge in original.edges] == ["edge_age_income"]

    assert nodes._report_graph_asset(_report_state(causal_analysis_result={"success": False})) is None


def test_report_node_injects_assets_and_evidence_without_sending_data_points() -> None:
    captured: dict = {}
    model = _StructuredModel(
        {
            "title": "因果分析报告",
            "blocks": [
                {"id": "markdown_result", "type": "markdown", "content": "结论", "evidence_refs": []},
                {"id": "chart_age", "type": "chart", "title": "年龄分布", "asset_key": "chart_histogram_age"},
                {"id": "graph_main_block", "type": "causal_graph", "title": "主要因果关系", "asset_key": "graph_main"},
            ],
        },
        captured,
    )

    result = asyncio.run(nodes.report_node(_report_state(), model))
    document = result["report_document"]

    assert isinstance(document, ReportDocument)
    assert set(document.assets) == {"chart_histogram_age", "graph_main"}
    assert [source.kind for source in document.sources] == ["file"]
    assert document.evidence_refs
    payload = json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
    assert "data:image" not in payload and "<img" not in payload
    assert "[[CHART:" not in payload

    prompt = "\n".join(str(message.content) for message in captured["messages"])
    assert "chart_histogram_age" in prompt
    assert "graph_main" in prompt
    assert document.evidence_refs[0].evidence_id in prompt
    assert result["messages"][0].name == "report"

    # 结构化输入必须是 JSON 文本，而不是单引号、无缩进的 Python 字典字面量。
    assert '"asset_key": "chart_histogram_age"' in prompt
    assert f'"evidence_id": "{document.evidence_refs[0].evidence_id}"' in prompt
    assert '"inferred_type": "continuous"' in prompt
    assert "'asset_key'" not in prompt
    assert "value_counts" not in prompt


def test_build_report_document_recovers_textual_evidence_ids_for_navigation() -> None:
    document = build_report_document(
        _draft([
            {
                "id": "markdown_result",
                "type": "markdown",
                "content": "结论正文（证据 ev_1）",
            }
        ]),
        sources=[_source()],
        evidence_refs=[_evidence()],
    )

    assert document.blocks[0].evidence_refs == ["ev_1"]


def test_report_metadata_prompt_is_compact_json() -> None:
    metadata = nodes._report_metadata_for_prompt({
        "n_rows": 15,
        "n_cols": 2,
        "columns": ["age", "city"],
        "column_profiles": {
            "age": {
                "inferred_type": "continuous",
                "unique_count": 15,
                "is_constant": False,
                "missing_ratio": 0.0,
                "causal_suitability": "good",
                "stats": {"mean": 33.0, "min": 22.0, "max": 50.0},
                "value_counts": {"25.0": 2, "30.0": 2},
            },
            "city": {
                "inferred_type": "categorical",
                "unique_count": 4,
                "issues": ["缺失率过高(35.0%)"],
                "value_counts": {"北京": 4},
            },
        },
        "quality_assessment": {
            "total_missing_ratio": 0.0,
            "constant_columns": [],
            "high_missing_columns": ["city"],
            "suitable_for_causal": {"good": ["age", "city"]},
        },
    })

    parsed = json.loads(metadata)
    assert parsed["n_rows"] == 15
    assert parsed["n_cols"] == 2
    assert parsed["columns"] == ["age", "city"]
    assert parsed["column_profiles"]["age"]["inferred_type"] == "continuous"
    assert parsed["column_profiles"]["age"]["stats"]["mean"] == 33.0
    assert parsed["column_profiles"]["city"]["issues"] == ["缺失率过高(35.0%)"]
    assert parsed["quality_assessment"]["high_missing_columns"] == ["city"]
    assert "value_counts" not in parsed["column_profiles"]["age"]
    assert "value_counts" not in parsed["column_profiles"]["city"]
    assert "suitable_for_causal" not in parsed["quality_assessment"]
    assert '"inferred_type": "continuous"' in metadata
    assert "'inferred_type'" not in metadata


def test_report_metadata_prompt_handles_missing_or_malformed_input() -> None:
    assert json.loads(nodes._report_metadata_for_prompt(None))["columns"] == []
    assert json.loads(nodes._report_metadata_for_prompt("not-a-dict"))["n_rows"] is None

    fallback = json.loads(nodes._report_metadata_for_prompt({
        "column_profiles": {"age": {"inferred_type": "continuous"}},
    }))
    assert fallback["columns"] == ["age"]
    assert fallback["column_profiles"] == {"age": {"inferred_type": "continuous"}}


def test_report_node_rejects_unknown_asset_from_model_output() -> None:
    model = _StructuredModel({
        "title": "因果分析报告",
        "blocks": [{"id": "chart_bad", "type": "chart", "asset_key": "chart_unknown"}],
    })

    with pytest.raises(ReportSchemaError):
        asyncio.run(nodes.report_node(_report_state(), model))


def test_report_structured_output_failure_enters_degraded_document_path() -> None:
    with pytest.raises(StructuredOutputError):
        asyncio.run(nodes.report_node(_report_state(), _FailingStructuredModel({})))

    recovered = recover_report(_report_state(), NodeError("report", RuntimeError("boom")))
    document = recovered["report_document"]
    assert isinstance(document, ReportDocument)
    assert document.blocks[0].type == "markdown"
    assert "报告生成失败" in document.blocks[0].content
    assert document.assets == {}

    result = process_final_result({
        "messages": recovered["messages"],
        "report_document": document,
    })
    assert result["type"] == "report"
    assert result["render_mode"] == "structured"
    assert result["document"]["report_id"] == document.report_id


def test_final_result_and_storage_share_one_document_without_visualization_attachment() -> None:
    document = build_degraded_report_document("报告正文")
    result = process_final_result({
        "messages": [AIMessage(content="决策：因果分析报告已生成完成。", name="report")],
        "report_document": document,
        "finalization_status": "valid",
    })

    assert result["type"] == "report"
    assert result["layout"] == "report"
    assert result["render_mode"] == "structured"
    assert result["finalization_status"] == "valid"
    assert "finalization_error" not in result

    content, attachments = prepare_ai_response_for_storage(result)
    assert content == "因果分析报告"
    assert [attachment["type"] for attachment in attachments] == ["report_document"]
    assert json.loads(attachments[0]["content"]) == result["document"]


def test_final_result_without_document_returns_controlled_default_message() -> None:

    result = process_final_result({"messages": [AIMessage(content="报告", name="report")]})

    assert result == {"type": "text", "summary": "抱歉，我在处理时遇到了问题。"}


def test_markdown_list_content_survives_storage_and_history_parsing() -> None:
    markdown = "## 结论\n\n- 变量 X 与变量 Y 呈正相关\n- 该结论基于 1200 条样本"
    document = build_report_document(_draft([
        {"id": "markdown_summary", "type": "markdown", "content": markdown, "evidence_refs": []},
    ]))
    result = process_final_result({
        "messages": [AIMessage(content="报告", name="report")],
        "report_document": document,
    })

    _content, attachments = prepare_ai_response_for_storage(result)
    stored = json.loads(attachments[0]["content"])
    restored = parse_report_document(stored)

    assert stored["blocks"][0]["content"] == markdown
    assert restored["blocks"][0]["content"] == markdown


def test_plain_chat_responses_keep_their_storage_behavior() -> None:
    assert prepare_ai_response_for_storage("纯文本回复") == ("纯文本回复", [])
    assert prepare_ai_response_for_storage(
        {"type": "text", "summary": "# 标题\n\n- 列表项"}
    ) == ("# 标题\n\n- 列表项", [])
    content, attachments = prepare_ai_response_for_storage({
        "type": "causal_graph",
        "summary": "旧格式报告",
        "data": {"nodes": ["A"], "edges": []},
    })
    assert content == "旧格式报告"
    assert [attachment["type"] for attachment in attachments] == ["causal_graph"]


def test_report_document_summary_omits_chart_data() -> None:
    graph = build_causal_graph_model(ORIGINAL_GRAPH, graph_id="graph_main")
    document = build_report_document(
        _draft([
            {
                "id": "section_summary",
                "type": "section",
                "title": "结论摘要",
                "children": [{"id": "m", "type": "markdown", "content": "变量 X 与变量 Y 呈正相关"}],
            },
            {"id": "chart_age", "type": "chart", "title": "年龄分布", "asset_key": "chart_histogram_age"},
            {"id": "graph_main_block", "type": "causal_graph", "title": "主要因果关系", "asset_key": "graph_main"},
        ]),
        assets={"chart_histogram_age": _histogram_asset(), "graph_main": graph},
    )

    summary = report_document_summary(document)
    assert "结论摘要" in summary
    assert "变量 X 与变量 Y 呈正相关" in summary
    assert "[图表] 年龄分布" in summary
    assert "[因果图] 主要因果关系" in summary
    assert "bins" not in summary and "node_age" not in summary
