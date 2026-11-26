"""多模态知识库的核心契约与检索身份测试。

文档职责：验证边界框、检索文本、资源仓库和候选身份与混合排序等核心规则。

适用范围：修改 multimodal.contracts、资源存储或原 RAG 检索身份时使用；文档解析、
远程视觉和索引发布分别在 test_multimodal_parsing.py、test_multimodal_vision.py 和
test_multimodal_indexing.py 中维护。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from Agent.knowledge_base.multimodal.assets import AssetStore
from Agent.knowledge_base.multimodal.contracts import (
    BoundingBox,
    VisionAnalysis,
    render_retrieval_text,
)
from Agent.knowledge_base.query_rag import (
    RagRetrievalConfig,
    _build_retrieval_trace_with_resources,
    _merge_candidates,
)
from Agent.knowledge_base.sparse_retriever import candidate_identity


class MultimodalContractTests(unittest.TestCase):
    """核心规则与检索身份：不访问网络，不使用真实向量库。"""

    def test_candidate_identity_prefers_stable_unit_id_across_metadata_enrichment(self) -> None:
        """Dense metadata 增强不能在混合合并时重复 Chroma 单元。"""
        content = "unit content"
        sparse_metadata = {"unit_id": "unit_123", "page_number": 3}
        dense_metadata = {**sparse_metadata, "source": "source.pdf", "chunk_index": 7}
        self.assertEqual(candidate_identity(content, sparse_metadata), candidate_identity(content, dense_metadata))
        self.assertEqual(candidate_identity(content, sparse_metadata), "unit:unit_123")

    def test_hybrid_rerank_rewards_candidates_seen_by_both_retrievers(self) -> None:
        """混合证据在没有 gold 知识时也能获得共享检索器加分。"""
        metadata = {"unit_id": "unit_123", "modality": "text"}
        dense = {"page_content": "unit content", "metadata": metadata, "candidate_key": "unit:unit_123", "dense_score_norm": 0.8, "dense_score": 0.8, "sparse_score_norm": 0.0, "sparse_score": 0.0, "retrieval_sources": {"dense"}}
        sparse = {"page_content": "unit content", "metadata": metadata, "candidate_key": "unit:unit_123", "dense_score_norm": 0.0, "dense_score": 0.0, "sparse_score_norm": 0.4, "sparse_score": 0.4, "retrieval_sources": {"sparse"}}
        merged = _merge_candidates([dense], [sparse], return_before_final=True)
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0]["rerank_score"], 0.65 * 0.8 + 0.25 * 0.4 + 0.2)

    def test_figure_word_does_not_create_a_modality_retrieval_branch(self) -> None:
        """用户提到图时仍走统一 dense 检索，不按内部 modality 改写结果。"""
        vector_db = MagicMock()
        vector_db.similarity_search_with_relevance_scores.return_value = [
            (MagicMock(page_content="图中内容", metadata={"unit_id": "unit_1", "modality": "image"}), 0.9),
        ]
        sparse_retriever = MagicMock()
        sparse_retriever.search.return_value = []

        trace = _build_retrieval_trace_with_resources(
            "图中是什么？",
            RagRetrievalConfig(),
            vector_db=vector_db,
            embedding_function=MagicMock(),
            sparse_retriever=sparse_retriever,
        )

        vector_db.similarity_search_with_relevance_scores.assert_called_once_with("图中是什么？", k=10)
        self.assertNotIn("dense_modality", {item["retrieval_source"] for item in trace["evidence_payload"]})

    def test_bbox_rejects_reverse_coordinates(self) -> None:
        """边界框必须使用统一的左上至右下方向。"""
        with self.assertRaises(ValueError):
            BoundingBox(x0=0.8, y0=0.1, x1=0.2, y1=0.9)

    def test_retrieval_text_keeps_uncertain_relation_out_of_directed_relation(self) -> None:
        """不确定箭头只能作为不确定关系输出。"""
        analysis = VisionAnalysis(content_kind="causal_graph", ocr_text="", visible_facts=["存在连接线"], summary="", entities=[], table_markdown="", formula_latex="", directed_relations=[], uncertain_relations=["A ? B"], confidence=0.5, informative=True)
        text = render_retrieval_text(content_kind="causal_graph", analysis=analysis)
        self.assertIn("A ? B", text)
        self.assertNotIn("A -> B", text)

    def test_asset_store_rejects_escape_and_preserves_existing_asset(self) -> None:
        """资源存储不得允许目录逃逸或同 URI 覆盖。"""
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(Path(directory))
            uri = store.put("doc_" + "a" * 64, "figure.png", b"one")
            self.assertEqual(store.read(uri), b"one")
            with self.assertRaises(ValueError):
                store.read("../secret")
            self.assertNotEqual(uri, store.put("doc_" + "a" * 64, "figure.png", b"two"))

    def test_asset_store_shortens_long_names_without_losing_content(self) -> None:
        """Windows 路径限制下，长资源名必须写入可读的确定性 URI。"""
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(Path(directory) / ("nested_" * 12))
            name = "page_" + "a" * 180 + ".png"
            uri = store.put("doc_" + "a" * 64, name, b"image")
            self.assertLess(len(Path(uri).name), 80)
            self.assertEqual(store.read(uri), b"image")
