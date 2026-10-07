"""最终报告结果、历史图投影与报告附件持久化合同测试。"""

import json
import os

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


from langchain_core.messages import AIMessage  # noqa: E402

from Agent.Report.assets import build_causal_graph_model  # noqa: E402
from Agent.Report.document import (  # noqa: E402
    ChartAsset,
    ReportDraft,
    ReportSource,
    build_report_document,
)
from app.agent.worker.result_presenter import process_final_result  # noqa: E402
from app.chat.response_storage import (  # noqa: E402
    prepare_ai_response_for_storage,
    project_causal_graph,
)


ORIGINAL_GRAPH = {
    "graph_semantics": "dag_target_to_source",
    "nodes": ["A", "B"],
    "edges": [
        {"source": "A", "target": "B", "edge_type": "directed", "weight": 1.5},
    ],
}
PROJECTED_ORIGINAL_GRAPH = {
    "nodes": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
    "edges": [
        {
            "from": "A",
            "to": "B",
            "arrows": "to",
            "dashes": False,
            "weight": 1.5,
            "label": "1.5",
        }
    ],
}
REVISED_GRAPH = {
    "nodes": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
    "edges": [
        {"from": "B", "to": "A", "arrows": "to", "dashes": False, "label": "1.5"}
    ],
}


def _histogram_asset() -> ChartAsset:
    return ChartAsset(
        asset_key="chart_histogram_age",
        chart_type="histogram",
        data={"bins": [0, 1, 2], "counts": [3, 4]},
        metadata={"variable": "age"},
        options={"show_tooltip": True},
    )


def _document(graph=ORIGINAL_GRAPH, *, graph_source="original"):
    """构造一份包含图表和因果图的完整报告文档。"""
    graph_model = build_causal_graph_model(
        graph,
        graph_id="graph_main",
        algorithm="causal_pc",
        graph_source=graph_source,
    )
    assert graph_model is not None
    return build_report_document(
        ReportDraft.model_validate({
            "title": "因果分析报告",
            "blocks": [
                {"id": "markdown_result", "type": "markdown", "content": "主要发现", "evidence_refs": []},
                {"id": "chart_age", "type": "chart", "title": "年龄分布", "asset_key": "chart_histogram_age"},
                {"id": "graph_main_block", "type": "causal_graph", "title": "主要因果关系", "asset_key": "graph_main"},
            ],
        }),
        assets={"chart_histogram_age": _histogram_asset(), "graph_main": graph_model},
        sources=[ReportSource(source_id="src_1", kind="file", title="data.csv", file_id=12)],
        evidence_refs=[],
    )


def _final_state(document, **overrides):
    state = {
        "messages": [AIMessage(content="决策：因果分析报告已生成完成。", name="report")],
        "report_document": document,
    }
    state.update(overrides)
    return state


def test_final_result_publishes_structured_report_document():
    document = _document()
    result = process_final_result(_final_state(document))

    assert result["type"] == "report"
    assert result["layout"] == "report"
    assert result["render_mode"] == "structured"
    assert result["document"]["report_id"] == document.report_id
    assert result["document"]["assets"]["graph_main"]["graph_id"] == "graph_main"
    assert result["document"]["assets"]["chart_histogram_age"]["chart_type"] == "histogram"
    assert result["document"]["sources"][0]["source_id"] == "src_1"

    payload = json.dumps(result["document"], ensure_ascii=False)
    assert "data:image" not in payload
    assert "<img" not in payload
    assert "[[CHART:" not in payload


def test_postprocessed_revision_is_kept_inside_the_report_document():
    document = _document(REVISED_GRAPH, graph_source="postprocessed")
    result = process_final_result(_final_state(document))

    graph_asset = result["document"]["assets"]["graph_main"]
    assert graph_asset["metadata"]["graph_source"] == "postprocessed"
    assert graph_asset["edges"][0]["source"] == "node_B"
    assert graph_asset["edges"][0]["target"] == "node_A"


def test_final_result_and_history_attachment_persist_the_same_document():
    document = _document()
    result = process_final_result(_final_state(document))

    content, attachments = prepare_ai_response_for_storage(result)

    assert content == "因果分析报告"
    assert [attachment["type"] for attachment in attachments] == ["report_document"]
    assert json.loads(attachments[0]["content"]) == result["document"]


def test_report_storage_never_writes_visualization_attachment():
    result = process_final_result(_final_state(_document()))
    _content, attachments = prepare_ai_response_for_storage(result)

    assert "visualization" not in [attachment["type"] for attachment in attachments]
    assert "causal_graph" not in [attachment["type"] for attachment in attachments]


def test_final_result_exposes_only_finalization_status() -> None:
    result = process_final_result(_final_state(_document(), finalization_status="degraded"))

    assert result["finalization_status"] == "degraded"
    assert "finalization_error" not in result


def test_project_causal_graph_keeps_vis_network_payload_stable():
    """已转换过的载荷重复投影结果不变，旧附件与兼容链路才能继续使用。"""
    assert project_causal_graph(REVISED_GRAPH) == REVISED_GRAPH
    assert project_causal_graph(PROJECTED_ORIGINAL_GRAPH) == PROJECTED_ORIGINAL_GRAPH


def test_project_causal_graph_rejects_unknown_shapes():
    assert project_causal_graph(None) is None
    assert project_causal_graph("A -> B") is None
    assert project_causal_graph({"nodes": ["A"]}) is None
    assert project_causal_graph({"nodes": ["A"], "edges": [{"source": "A"}]}) is None


@pytest.mark.parametrize(
    "document",
    [None, {"type": "text", "summary": "正文"}, "不是报告文档"],
)
def test_final_result_without_valid_document_returns_controlled_default(document):
    result = process_final_result({
        "messages": [AIMessage(content="报告", name="report")],
        "report_document": document,
    })

    assert result == {"type": "text", "summary": "抱歉，我在处理时遇到了问题。"}
