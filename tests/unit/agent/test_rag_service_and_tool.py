import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from Agent.knowledge_base.rag_service import (
    UNAVAILABLE_RAG_RESULT,
    RagService,
    UnavailableRagService,
)


class RagServiceTests(unittest.TestCase):
    def test_formal_answer_context_budget_and_page_dedup_are_applied(self):
        from Agent.knowledge_base import query_rag

        runtime = SimpleNamespace(
            config=SimpleNamespace(production_config_path="production.json"),
            vector_db=object(),
            embedding=object(),
            sparse_retriever=object(),
            answer_llm=object(),
        )
        service = RagService(runtime)
        config = query_rag.RagRetrievalConfig(
            answer_max_contexts=3,
            answer_context_compression="page_dedupe",
        )
        candidates = [
            {"metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "text", "content_hash": "a"}, "page_content": "a1"},
            {"metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "text", "content_hash": "b"}, "page_content": "a2"},
            {"metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "table", "content_hash": "c"}, "page_content": "table"},
            {"metadata": {"doc_id": "doc", "page_number": 2, "content_kind": "text", "content_hash": "d"}, "page_content": "b1"},
        ]
        captured = {}

        def capture_answer(_payload, evidence):
            captured["evidence"] = evidence
            return {"answer": "ok"}

        with patch.object(service, "_load_retrieval_config", return_value=config), patch.object(
            service, "build_retrieval_trace", return_value={"stages": {"final": candidates}}
        ), patch.object(
            query_rag, "_build_evidence_payloads", side_effect=query_rag._build_evidence_payloads
        ), patch.object(
            service, "answer_question", side_effect=capture_answer
        ), patch.object(query_rag, "format_rag_summary_for_prompt", return_value="summary"):
            service.get_response(["q"])

        self.assertEqual([item["content"] for item in captured["evidence"]], ["a1", "table", "b1"])

    def test_page_dedupe_keeps_distinct_content_kinds_and_honors_budget(self):
        from Agent.knowledge_base import query_rag

        payloads = [
            {"content": "a1", "metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "text"}},
            {"content": "a2", "metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "text"}},
            {"content": "table", "metadata": {"doc_id": "doc", "page_number": 1, "content_kind": "table"}},
            {"content": "b1", "metadata": {"doc_id": "doc", "page_number": 2, "content_kind": "text"}},
        ]

        result = query_rag.compress_evidence_payloads(payloads, max_contexts=2, strategy="page_dedupe")

        self.assertEqual([item["content"] for item in result], ["a1", "table"])

    def test_unavailable_service_returns_stable_fresh_result(self):
        service = UnavailableRagService()
        first = service.get_response(["secret question"])
        second = service.get_response([])

        self.assertEqual(first, UNAVAILABLE_RAG_RESULT)
        self.assertEqual(second, UNAVAILABLE_RAG_RESULT)
        self.assertIsNot(first, second)
        self.assertNotIn("secret question", str(first))

    def test_each_question_loads_current_production_config(self):
        from Agent.knowledge_base import query_rag

        configs = [
            query_rag.RagRetrievalConfig(final_top_k=1),
            query_rag.RagRetrievalConfig(final_top_k=2),
        ]
        runtime = SimpleNamespace(
            config=SimpleNamespace(production_config_path="production.json"),
            vector_db=object(),
            embedding=object(),
            sparse_retriever=object(),
            answer_llm=object(),
        )
        service = RagService(runtime)
        seen_configs = []

        def fake_trace(_question, config=None):
            seen_configs.append(config)
            return {"stages": {"final": []}}

        with patch.object(service, "_load_retrieval_config", side_effect=configs), patch.object(
            service, "build_retrieval_trace", side_effect=fake_trace
        ), patch.object(
            service, "answer_question", side_effect=lambda payload, _evidence: {"question": payload["question"]}
        ), patch.object(
            query_rag, "format_rag_summary_for_prompt", return_value="summary"
        ):
            result = service.get_response(["q1", "q2"])

        self.assertEqual(seen_configs, configs)
        self.assertEqual([item["question"] for item in result["questions"]], ["q1", "q2"])

    def test_missing_or_invalid_policy_uses_safe_source_marker(self):
        """缺失或非法 policy 应回退代码默认值并保留来源标记。"""
        from Agent.knowledge_base import query_rag

        with tempfile.TemporaryDirectory() as directory:
            missing = f"{directory}/missing.json"
            config, source = query_rag._load_rag_config(missing)
            self.assertEqual(source, "code_default")
            self.assertIsInstance(config, query_rag.RagRetrievalConfig)

            invalid = f"{directory}/invalid.json"
            with open(invalid, "w", encoding="utf-8") as handle:
                handle.write("{not valid json")
            _, source = query_rag._load_rag_config(invalid)
            self.assertEqual(source, "invalid_config_fallback")

    def test_compatibility_exports_remain_available(self):
        from Agent.knowledge_base import query_rag

        for name in (
            "RagRetrievalConfig",
            "build_retrieval_trace",
            "get_vector_db_metadata_summary",
            "get_rag_response",
            "_get_embedding_function",
            "_answer_question",
            "_normalize_question_payload",
            "COLLECTION_NAME",
            "PRODUCTION_RAG_CONFIG_PATH",
            "get_production_rag_config_status",
        ):
            self.assertTrue(hasattr(query_rag, name), name)

    def test_runtime_uses_unified_dense_retrieval_for_explicit_table_request(self):
        """即使问题提到表，也只执行统一 dense 检索，不额外建立模态召回分支。"""
        from Agent.knowledge_base import query_rag

        general = {
            "page_content": "正文",
            "metadata": {
                "unit_id": "text", "chunk_id": "text", "doc_id": "doc",
                "source_name": "source.pdf", "modality": "text",
            },
            "rerank_score": 1.0,
        }
        config = query_rag.RagRetrievalConfig()
        vector_db = object()
        dense_mock = MagicMock(return_value=[general])
        with patch.object(query_rag, "_dense_retrieve", dense_mock), patch.object(
            query_rag, "_select_mmr_candidates", return_value=[general]
        ), patch.object(query_rag, "_sparse_retrieve", return_value=[]), patch.object(
            query_rag, "_merge_candidates", return_value=[general]
        ):
            trace = query_rag._build_retrieval_trace_with_resources(
                "表 6.1 中的恢复率是什么？",
                config,
                vector_db=vector_db,
                embedding_function=object(),
                sparse_retriever=object(),
            )

        self.assertEqual(config.final_top_k, 5)
        self.assertEqual(trace["stages"]["final"][0]["metadata"]["unit_id"], "text")
        dense_mock.assert_called_once_with(
            "表 6.1 中的恢复率是什么？",
            fetch_k=config.dense_fetch_k,
            score_threshold=0.0,
            vector_db=vector_db,
        )

    def _evidence_service(self, *, document_names):
        from Agent.knowledge_base.rag_runtime import RagRuntimeConfig

        runtime = SimpleNamespace(
            config=RagRuntimeConfig(
                vector_db_dir="unit-db",
                collection_name="unit-collection",
                production_config_path="unit-config.json",
                embedding_config={
                    "status": "ready",
                    "mode": "local",
                    "provider": "huggingface",
                    "model": "unit-model",
                    "path": "unit-model-path",
                },
                release_id="mm_unit",
                document_names=document_names,
            ),
            vector_db=object(),
            embedding=object(),
            sparse_retriever=object(),
            answer_llm=object(),
        )
        return RagService(runtime)

    def _project_evidence(self, service, candidates):
        from Agent.knowledge_base import query_rag

        with patch.object(
            service,
            "_runtime_identity_diagnostics",
            return_value={"readiness": "ready", "release_id": "mm_unit", "embedding_fingerprint": None},
        ), patch.object(
            service, "_load_retrieval_config", return_value=query_rag.RagRetrievalConfig()
        ), patch.object(
            service, "build_retrieval_trace", return_value={"stages": {"final": candidates}}
        ):
            return service.get_evidence("恢复率是多少？")

    def test_evidence_projection_uses_release_document_name_and_drops_asset_uri_url(self):
        """知识库来源名来自 release manifest 的 document_id → 相对路径；asset_uri 不充当 URL。"""
        service = self._evidence_service(document_names={"doc_b": "Pearl_2009_Causality.pdf"})
        candidates = [
            {
                "page_content": "片段",
                "metadata": {
                    "document_id": "doc_b",
                    "unit_id": "unit_a",
                    "chunk_id": "unit_a",
                    "modality": "page",
                    "page": 12,
                    "asset_uri": "doc_bbb/pages/p12.png",
                },
                "dense_score": 0.5,
                "sparse_score": 0.4,
                "rerank_score": 0.9,
            }
        ]

        payload = self._project_evidence(service, candidates)

        self.assertEqual(payload["status"], "available")
        item = payload["evidence"][0]
        self.assertEqual(item["evidence_ref"], "rag:mm_unit:E1")
        self.assertEqual(item["source_title"], "Pearl_2009_Causality.pdf")
        self.assertIsNone(item["source_url"])
        self.assertEqual(item["locator"], "Pearl_2009_Causality.pdf#page=12#chunk=unit_a")

    def test_evidence_projection_falls_back_when_document_is_not_in_manifest(self):
        """manifest 未覆盖的 document_id 回退到检索元数据，不能因此丢失来源。"""
        service = self._evidence_service(document_names={})
        candidates = [
            {
                "page_content": "片段",
                "metadata": {
                    "document_id": "doc_unmapped",
                    "chunk_id": "unit_a",
                    "page": 12,
                    "title": "论文.pdf",
                    "source_name": "论文.pdf",
                },
                "rerank_score": 0.9,
            }
        ]

        payload = self._project_evidence(service, candidates)

        item = payload["evidence"][0]
        self.assertEqual(item["source_title"], "论文.pdf")
        self.assertIsNone(item["source_url"])
        self.assertEqual(item["locator"], "论文.pdf#page=12#chunk=unit_a")


class RagToolTests(unittest.TestCase):
    def test_rag_tool_registry_exposes_the_subgraph_tool(self):
        """RAG 子图使用固定工具名，不再注入普通节点的 Service。"""
        from Agent.tool_node.rag_tool_registry import build_rag_tools

        tools = build_rag_tools()
        self.assertEqual([tool.name for tool in tools], ["rag_enrichment_search"])


class RagNodeTests(unittest.TestCase):
    def test_mcp_adapter_text_block_list_is_parsed_as_success(self):
        """真实 MCP adapter 的文本块列表必须保留业务 success 字段。"""
        from langchain_core.messages import ToolMessage
        from Agent.tool_node.tool_message_adapter import parse_tool_message_json

        message = ToolMessage(
            content=[{"type": "text", "text": '{"success": true, "data": {"nodes": []}}'}],
            tool_call_id="call-1",
        )
        self.assertEqual(parse_tool_message_json(message), {"success": True, "data": {"nodes": []}})

    def test_mcp_adapter_text_block_dict_is_parsed_as_success(self):
        """真实 adapter 的单个 text block 也必须解出业务 success 字段。"""
        from langchain_core.messages import ToolMessage
        from Agent.tool_node.tool_message_adapter import parse_tool_message_json

        message = ToolMessage(
            content={"type": "text", "text": '{"success": true, "data": {"nodes": []}}'},
            tool_call_id="call-1",
        )
        self.assertEqual(parse_tool_message_json(message), {"success": True, "data": {"nodes": []}})

    def test_mcp_adapter_artifact_wrapper_is_parsed_as_success(self):
        """artifact structured_content 包装不得吞掉真实 MCP success 字段。"""
        from langchain_core.messages import ToolMessage
        from Agent.tool_node.tool_message_adapter import parse_tool_message_json

        message = ToolMessage(
            content="ignored",
            artifact={"structured_content": {"result": [{"type": "text", "text": '{"success": true}'}]}},
            tool_call_id="call-1",
        )
        self.assertEqual(parse_tool_message_json(message), {"success": True})

    def test_mcp_adapter_artifact_result_string_is_parsed_as_success(self):
        """真实 adapter 的 result 字符串必须递归还原为业务 JSON。"""
        from langchain_core.messages import ToolMessage
        from Agent.tool_node.tool_message_adapter import parse_tool_message_json

        message = ToolMessage(
            content="ignored",
            artifact={"structured_content": {"result": '{"success": true, "data": {}}'}},
            tool_call_id="call-1",
        )
        self.assertEqual(parse_tool_message_json(message), {"success": True, "data": {}})

    def test_mcp_failure_routes_to_terminal_normal_chat(self):
        """明确 MCP 失败不得回到 agent 形成无限循环。"""
        from Agent.causal_agent.edges import mcp_router

        self.assertEqual(mcp_router({"causal_analysis_result": {"success": False}}), "normal_chat")

    def test_successful_mcp_result_routes_to_rag(self):
        """成功 MCP 返回必须进入 RAG 子图阶段。"""
        from Agent.causal_agent.edges import mcp_router

        self.assertEqual(mcp_router({"causal_analysis_result": {"success": True}}), "rag")



if __name__ == "__main__":
    unittest.main()
