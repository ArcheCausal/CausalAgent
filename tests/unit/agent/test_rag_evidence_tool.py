"""P2-U RAG evidence-only Tool 协议测试。"""

import asyncio
import builtins
from types import SimpleNamespace
from unittest.mock import patch

from Agent.deep_agent_tools import RagEvidenceTool, build_default_rag_evidence_tool
from Agent.knowledge_base.embedding_runtime import EmbeddingApiError
from Agent.knowledge_base.query_rag import RagRetrievalConfig
from Agent.knowledge_base.rag_service import RagService


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def get_evidence(self, query, *, max_contexts=None):
        self.calls.append((query, max_contexts))
        return {
            "status": "available",
            "query": query,
            "release_id": "release-1",
            "evidence": [
                {
                    "evidence_ref": "rag:release-1:E1",
                    "snippet": "evidence",
                    "source_title": "paper",
                    "source_url": "https://example.invalid/paper",
                    "locator": "p1",
                    "rerank_score": 0.8,
                    "dense_score": 0.7,
                    "sparse_score": 0.4,
                    "modality": "text",
                    "sufficiency": "sufficient",
                }
            ],
        }


def _rag_service() -> RagService:
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            release_id="release-1",
            production_config_path="production-rag-config.json",
            embedding_config={
                "mode": "api",
                "provider": "openai_compatible",
                "model": "text-embedding-test",
                "dimension": 3,
                "endpoint_identity": "https://example.invalid/v1",
            },
        ),
        vector_db=object(),
        embedding=object(),
        sparse_retriever=object(),
        answer_llm=object(),
    )
    return RagService(runtime)


def _retrieval_config() -> RagRetrievalConfig:
    return RagRetrievalConfig(max_evidence_chars=128)


def test_rag_tool_returns_evidence_without_answer_generation() -> None:
    retriever = FakeRetriever()
    result = asyncio.run(RagEvidenceTool(retriever).get_evidence("causal query"))
    assert result["status"] == "available"
    assert result["evidence"][0]["evidence_ref"] == "rag:release-1:E1"
    assert result["evidence"][0]["dense_score"] == 0.7
    assert result["evidence"][0]["sparse_score"] == 0.4
    assert result["evidence"][0]["rerank_score"] == 0.8
    assert result["sufficiency"] == "sufficient"
    assert retriever.calls == [("causal query", None)]


def test_default_rag_tool_defers_runtime_creation_until_first_query() -> None:
    """worker 构造工具时不能提前加载 Chroma、BM25 或 embedding。"""
    retriever = FakeRetriever()
    with patch(
        "Agent.knowledge_base.query_rag._get_rag_service",
        return_value=retriever,
    ) as get_service:
        tool = build_default_rag_evidence_tool()
        get_service.assert_not_called()

        result = asyncio.run(tool.get_evidence("causal query"))

    get_service.assert_called_once_with()
    assert result["status"] == "available"


def test_rag_tool_rejects_invalid_payload_as_protocol_error() -> None:
    class Invalid:
        def get_evidence(self, query, *, max_contexts=None):
            return {"status": "available", "evidence": [{"snippet": "missing ref"}]}

    result = asyncio.run(RagEvidenceTool(Invalid()).get_evidence("q"))
    assert result["status"] == "protocol_error"


def test_rag_tool_converts_retriever_exception_to_unavailable() -> None:
    class Broken:
        def get_evidence(self, query, *, max_contexts=None):
            raise RuntimeError("private retriever detail")

    result = asyncio.run(RagEvidenceTool(Broken()).get_evidence("q"))
    assert result["status"] == "unavailable"
    assert result["diagnostics"] == {"reason_code": "retrieval_unavailable"}
    assert "private retriever detail" not in str(result)


def test_rag_service_config_failure_is_safe_unavailable() -> None:
    service = _rag_service()

    def fail_config():
        raise RuntimeError("secret config path and credentials")

    service._load_retrieval_config = fail_config
    result = service.get_evidence("causal query")

    assert result["status"] == "unavailable"
    assert result["diagnostics"]["reason_code"] == "retrieval_config_unavailable"
    assert "secret config path and credentials" not in str(result)


def test_rag_service_query_rag_import_failure_is_safe_unavailable(monkeypatch) -> None:
    service = _rag_service()
    real_import = builtins.__import__

    def fail_query_rag_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "Agent.knowledge_base" and "query_rag" in (fromlist or ()):
            raise ImportError("private query_rag dependency detail")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_query_rag_import)
    result = service.get_evidence("causal query")

    assert result["status"] == "unavailable"
    assert result["diagnostics"]["reason_code"] == "retrieval_unavailable"
    assert "private query_rag dependency detail" not in str(result)


def test_rag_service_identity_failure_is_safe_readiness_unavailable() -> None:
    service = _rag_service()
    service._load_retrieval_config = lambda: _retrieval_config()
    calls = 0

    def fail_identity():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("private readiness and token detail")
        return {
            "readiness": "ready",
            "release_id": "release-1",
            "embedding_fingerprint": "fingerprint-1",
        }

    service._runtime_identity_diagnostics = fail_identity
    result = service.get_evidence("causal query")

    assert result["status"] == "unavailable"
    assert result["diagnostics"]["reason_code"] == "rag_readiness_unavailable"
    assert result["release_id"] is None
    assert "private readiness and token detail" not in str(result)


def test_rag_service_embedding_failure_is_safe_unavailable() -> None:
    service = _rag_service()
    service._load_retrieval_config = lambda: _retrieval_config()

    def fail_embedding(question, *, config):
        raise EmbeddingApiError("unavailable")

    service.build_retrieval_trace = fail_embedding
    result = service.get_evidence("causal query")

    assert result["status"] == "unavailable"
    assert result["diagnostics"]["reason_code"] == "embedding_unavailable"
    assert result["evidence"] == []


def test_rag_service_rejects_empty_query_as_protocol_error() -> None:
    service = _rag_service()

    result = service.get_evidence("  ")

    assert result["status"] == "protocol_error"
    assert result["diagnostics"]["reason_code"] == "empty_query"
    assert result["evidence"] == []


def test_rag_service_empty_result_keeps_no_relevant_evidence_status() -> None:
    service = _rag_service()
    service._load_retrieval_config = lambda: _retrieval_config()
    service.build_retrieval_trace = lambda question, *, config: {
        "timings_ms": {"final": 1.0},
        "stages": {"final": []},
    }

    result = service.get_evidence("causal query")

    assert result["status"] == "no_relevant_evidence"
    assert result["evidence"] == []
    assert result["sufficiency"] == "insufficient"


def test_rag_service_returns_evidence_without_answer_model_call() -> None:
    service = _rag_service()
    service._load_retrieval_config = lambda: _retrieval_config()
    service.build_retrieval_trace = lambda question, *, config: {
        "timings_ms": {"final": 1.0},
        "stages": {
            "final": [
                {
                    "metadata": {
                        "title": "paper",
                        "source_name": "paper.pdf",
                        "source_url": "https://example.invalid/paper",
                        "page": 1,
                        "chunk_id": "c1",
                        "modality": "text",
                    },
                    "page_content": "evidence text",
                    "dense_score": 0.7,
                    "sparse_score": 0.4,
                    "rerank_score": 0.8,
                    "retrieval_source": "dense+sparse",
                }
            ]
        },
    }

    result = service.get_evidence("causal query")

    assert result["status"] == "available"
    assert result["evidence"][0]["evidence_ref"] == "rag:release-1:E1"
    assert result["evidence"][0]["snippet"] == "evidence text"
    assert result["diagnostics"]["retrieval_trace"]["stage_counts"]["final"] == 1
