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


from langchain_core.messages import AIMessage

from Agent.Report.document import build_degraded_report_document
from Agent.causal_agent.web_search_node import WEB_SEARCH_MAX_RESULTS
from app.agent.worker.result_presenter import _extract_references, process_final_result
from app.chat.response_storage import prepare_ai_response_for_storage


def _content_items(n):
    return [
        {
            "url": f"https://example.org/{i}",
            "title": f"title {i}",
            "text": f"text {i}",
            "source": "snippet",
            "origin": "arxiv",
        }
        for i in range(n)
    ]


def _web_search_result(n, success=True):
    return {"success": success, "content": _content_items(n)}


def _projected(n):
    return [{"title": f"title {i}", "url": f"https://example.org/{i}"} for i in range(n)]


class TestExtractReferences:
    def test_caps_at_web_search_max_results(self):
        assert _extract_references(
            _web_search_result(WEB_SEARCH_MAX_RESULTS + 3)
        ) == _projected(WEB_SEARCH_MAX_RESULTS)

    def test_returns_fewer_when_less_than_5(self):
        assert _extract_references(_web_search_result(3)) == _projected(3)

    def test_returns_empty_for_none(self):
        assert _extract_references(None) == []

    def test_returns_empty_when_success_false(self):
        assert _extract_references(_web_search_result(5, success=False)) == []

    def test_returns_empty_for_empty_content(self):
        assert _extract_references({"success": True, "content": []}) == []


class TestProcessFinalResultReferences:
    def test_report_mounts_references(self):
        state = {
            "messages": [AIMessage(content="report", name="report")],
            "report_document": build_degraded_report_document("body"),
            "web_search_result": _web_search_result(5),
        }
        result = process_final_result(state)
        assert result["type"] == "report"
        assert result["render_mode"] == "structured"
        assert result["references"] == _projected(5)

    def test_report_omits_references_when_absent(self):
        state = {
            "messages": [AIMessage(content="report", name="report")],
            "report_document": build_degraded_report_document("body"),
        }
        result = process_final_result(state)
        assert "references" not in result

    def test_report_without_document_falls_back_to_controlled_message(self):
        """没有结构化报告文档时走受控默认消息，不再挂载旧报告引用。"""
        state = {
            "messages": [AIMessage(content="report", name="report")],
            "web_search_result": _web_search_result(2),
        }
        result = process_final_result(state)
        assert result["type"] == "text"
        assert result["summary"] == "抱歉，我在处理时遇到了问题。"
        assert "references" not in result


class TestPrepareStorageReferences:
    def test_report_writes_document_and_reference_attachments(self):
        ai_response = {
            "type": "report",
            "layout": "report",
            "render_mode": "structured",
            "document": build_degraded_report_document("报告正文").model_dump(mode="json"),
            "references": _projected(5),
        }
        content, attachments = prepare_ai_response_for_storage(ai_response)
        assert content == "因果分析报告"
        assert [a["type"] for a in attachments] == ["report_document", "web_search_references"]

    def test_creates_web_search_references_attachment(self):
        ai_response = {
            "type": "causal_graph",
            "summary": "summary",
            "data": {"nodes": [], "edges": []},
            "references": _projected(5),
        }
        _content, attachments = prepare_ai_response_for_storage(ai_response)
        assert "web_search_references" in [a["type"] for a in attachments]
        refs_attachment = next(
            a for a in attachments if a["type"] == "web_search_references"
        )
        assert json.loads(refs_attachment["content"]) == _projected(5)

    def test_causal_graph_attachment_excludes_references(self):
        ai_response = {
            "type": "causal_graph",
            "summary": "summary",
            "data": {"nodes": [], "edges": []},
            "references": _projected(5),
        }
        _content, attachments = prepare_ai_response_for_storage(ai_response)
        causal_attachment = next(a for a in attachments if a["type"] == "causal_graph")
        assert "references" not in json.loads(causal_attachment["content"])

    def test_no_references_attachment_when_absent(self):
        ai_response = {
            "type": "causal_graph",
            "summary": "summary",
            "data": {"nodes": [], "edges": []},
        }
        _content, attachments = prepare_ai_response_for_storage(ai_response)
        assert all(a["type"] != "web_search_references" for a in attachments)
