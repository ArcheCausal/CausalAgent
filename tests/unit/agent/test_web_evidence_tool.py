"""P2-U Web evidence Tool 的开关、上限和来源元数据测试。"""

import asyncio

from Agent.deep_agent_tools import WebEvidenceTool


def test_disabled_web_search_does_not_touch_searcher() -> None:
    calls = []

    def search(query, *, max_results):
        calls.append(query)
        return {"results": []}

    result = asyncio.run(WebEvidenceTool(search, web_search_enabled=False).get_evidence("q"))
    assert result["status"] == "disabled"
    assert calls == []


def test_web_search_returns_snippet_only_and_caps_results() -> None:
    def search(query, *, max_results):
        return {
            "results": [
                {
                    "source": "arxiv",
                    "title": "Paper",
                    "url": "https://example.invalid/paper",
                    "snippet": "snippet",
                    "score": 1.0,
                }
            ]
        }

    result = asyncio.run(
        WebEvidenceTool(search).get_evidence("q", query_en="causal inference", max_results=99)
    )
    assert result["status"] == "available"
    assert result["evidence"][0]["snippet"] == "snippet"
    assert result["evidence"][0]["source_url"] == "https://example.invalid/paper"
    assert result["query_en"] == "causal inference"

