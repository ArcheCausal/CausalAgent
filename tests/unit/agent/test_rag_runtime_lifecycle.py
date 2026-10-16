import dataclasses
import json
import logging
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bm25s

from Agent.knowledge_base.rag_runtime import (
    RagRuntimeConfig,
    RagRuntimeInitializationError,
    _resolve_multimodal_release,
    create_rag_runtime,
)
from Agent.knowledge_base.multimodal.defaults import load_production_defaults
from Agent.knowledge_base.sparse_retriever import (
    Bm25sSparseRetriever,
    tokenize_text,
)


def _runtime_config(**embedding_overrides):
    """创建不含真实资源路径的 Runtime 单测配置。"""
    embedding_config = {
        "status": "ready",
        "mode": "local",
        "provider": "huggingface",
        "model": "unit-model",
        "path": "unit-model-path",
    }
    embedding_config.update(embedding_overrides)
    return RagRuntimeConfig(
        vector_db_dir="unit-db",
        collection_name="unit-collection",
        production_config_path="unit-config.json",
        embedding_config=embedding_config,
    )


class RagRuntimeLifecycleTests(unittest.TestCase):
    @staticmethod
    def _write_active_release(root: Path) -> tuple[Path, Path, dict[str, object]]:
        """构造不含任何 PDF 的冻结 release fixture。"""
        config = load_production_defaults()
        version = "mm_" + "a" * 20
        index_root = root / "indexes"
        index_dir = index_root / version
        (index_dir / "chroma").mkdir(parents=True)
        embedding = dict(config["embedding"])
        manifest = {
            "index_version": version,
            "embedding": embedding,
            "parser": config["parser"],
            "build_configuration": {
                "pdf_parser": config["pdf_parser"],
                "vision": {
                    "enabled": False,
                    "local_ocr_enabled": config["vision"]["local_ocr_enabled"],
                },
            },
            "sources": [
                {
                    "source_id": source["source_id"],
                    "document_id": source["document_id"],
                    "relative_path": Path(source["path"]).name,
                    "controlled_path": source["path"],
                    "content_hash": source["sha256"],
                }
                for source in config["sources"]
            ],
        }
        manifest_path = index_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        active_path = root / "active_index.json"
        active_path.write_text(
            json.dumps(
                {
                    "index_version": version,
                    "index_path": f"{version}/chroma",
                    "collection_name": f"causal_multimodal_{version}",
                    "manifest_sha256": __import__("hashlib").sha256(manifest_path.read_bytes()).hexdigest(),
                    "embedding": embedding,
                }
            ),
            encoding="utf-8",
        )
        return active_path, index_root, embedding

    def test_active_release_resolves_without_local_source_files(self):
        """运行期只消费冻结 release，来源 PDF 缺失不能阻断解析。"""
        with tempfile.TemporaryDirectory() as temporary:
            active_path, index_root, embedding = self._write_active_release(Path(temporary))
            with patch.dict(
                os.environ,
                {
                    "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(active_path),
                    "MULTIMODAL_INDEX_ROOT": str(index_root),
                    "MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE": "",
                },
            ):
                release = _resolve_multimodal_release(embedding)

        self.assertEqual(release["index_version"], "mm_" + "a" * 20)

    def test_active_release_rejects_manifest_hash_embedding_and_index_path_drift(self):
        """运行期仍拒绝 manifest 哈希、embedding 与 index path 漂移。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active_path, index_root, embedding = self._write_active_release(root)
            active = json.loads(active_path.read_text(encoding="utf-8"))

            active["manifest_sha256"] = "0" * 64
            active_path.write_text(json.dumps(active), encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(active_path),
                    "MULTIMODAL_INDEX_ROOT": str(index_root),
                    "MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE": "",
                },
            ), self.assertRaisesRegex(ValueError, "manifest 哈希"):
                _resolve_multimodal_release(embedding)

            active = json.loads(active_path.read_text(encoding="utf-8"))
            manifest_path = index_root / ("mm_" + "a" * 20) / "manifest.json"
            active["manifest_sha256"] = __import__("hashlib").sha256(manifest_path.read_bytes()).hexdigest()
            active["embedding"] = {**embedding, "dimension": 384}
            active_path.write_text(json.dumps(active), encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(active_path),
                    "MULTIMODAL_INDEX_ROOT": str(index_root),
                    "MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE": "",
                },
            ), self.assertRaisesRegex(ValueError, "embedding 指纹"):
                _resolve_multimodal_release(embedding)

            active["embedding"] = embedding
            active["index_path"] = "../outside/chroma"
            active_path.write_text(json.dumps(active), encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "MULTIMODAL_ACTIVE_INDEX_CONFIG": str(active_path),
                    "MULTIMODAL_INDEX_ROOT": str(index_root),
                    "MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE": "",
                },
            ), self.assertRaisesRegex(ValueError, "index_path"):
                _resolve_multimodal_release(embedding)

    def test_concurrent_runtime_creation_serializes_local_embedding_initialization(self):
        active = 0
        max_active = 0
        counter_lock = threading.Lock()

        def create_embedding(_config):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1
            return object()

        with patch("Agent.knowledge_base.rag_runtime._validate_vector_db_directory"), patch(
            "Agent.knowledge_base.rag_runtime._validate_embedding_config"
        ), patch(
            "Agent.knowledge_base.rag_runtime._create_embedding_function",
            side_effect=create_embedding,
        ), patch(
            "Agent.knowledge_base.rag_runtime._open_existing_vector_db",
            return_value=object(),
        ), patch(
            "Agent.knowledge_base.rag_runtime._count_chunks",
            return_value=1,
        ), patch(
            "Agent.knowledge_base.rag_runtime.Bm25sSparseRetriever.from_vector_db",
            return_value=object(),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                runtimes = list(executor.map(
                    lambda _index: create_rag_runtime(_runtime_config(), answer_llm="llm"),
                    range(2),
                ))

        self.assertEqual(len(runtimes), 2)
        self.assertEqual(max_active, 1)

    def test_config_is_frozen_and_strips_secret_values(self):
        config = _runtime_config(api_key="must-not-survive")

        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.collection_name = "other"
        with self.assertRaises(TypeError):
            config.embedding_config["model"] = "other"
        self.assertNotIn("api_key", config.embedding_config)

    def test_runtime_initializes_resources_once_in_fixed_order(self):
        order = []
        embedding = object()
        vector_db = SimpleNamespace()
        sparse = object()

        with patch(
            "Agent.knowledge_base.rag_runtime._validate_vector_db_directory",
            side_effect=lambda _config: order.append("directory"),
        ) as validate_directory, patch(
            "Agent.knowledge_base.rag_runtime._validate_embedding_config",
            side_effect=lambda _config: order.append("embedding_config"),
        ) as validate_embedding, patch(
            "Agent.knowledge_base.rag_runtime._create_embedding_function",
            side_effect=lambda _config: order.append("embedding") or embedding,
        ) as create_embedding, patch(
            "Agent.knowledge_base.rag_runtime._open_existing_vector_db",
            side_effect=lambda _config, _embedding: order.append("chroma") or vector_db,
        ) as open_vector_db, patch(
            "Agent.knowledge_base.rag_runtime._count_chunks",
            side_effect=lambda _db: order.append("count") or 3,
        ) as count_chunks, patch(
            "Agent.knowledge_base.rag_runtime.Bm25sSparseRetriever.from_vector_db",
            side_effect=lambda _db: order.append("sparse") or sparse,
        ) as build_sparse:
            runtime = create_rag_runtime(_runtime_config(), answer_llm="llm")

        self.assertEqual(
            order,
            ["directory", "embedding_config", "embedding", "chroma", "count", "sparse"],
        )
        self.assertIs(runtime.embedding, embedding)
        self.assertIs(runtime.vector_db, vector_db)
        self.assertIs(runtime.sparse_retriever, sparse)
        for mocked in (
            validate_directory,
            validate_embedding,
            create_embedding,
            open_vector_db,
            count_chunks,
            build_sparse,
        ):
            mocked.assert_called_once()

    def test_failure_reports_stage_and_returns_no_partial_runtime(self):
        with patch(
            "Agent.knowledge_base.rag_runtime._validate_vector_db_directory"
        ), patch(
            "Agent.knowledge_base.rag_runtime._validate_embedding_config",
            side_effect=ValueError("invalid"),
        ), patch(
            "Agent.knowledge_base.rag_runtime._create_embedding_function"
        ) as create_embedding:
            with self.assertRaises(RagRuntimeInitializationError) as raised:
                create_rag_runtime(_runtime_config(), answer_llm="llm")

        self.assertEqual(raised.exception.stage, "embedding_config")
        create_embedding.assert_not_called()

    def test_missing_collection_and_empty_collection_fail_strictly(self):
        common_patches = (
            patch("Agent.knowledge_base.rag_runtime._validate_vector_db_directory"),
            patch("Agent.knowledge_base.rag_runtime._validate_embedding_config"),
            patch("Agent.knowledge_base.rag_runtime._create_embedding_function", return_value=object()),
        )
        with common_patches[0], common_patches[1], common_patches[2], patch(
            "Agent.knowledge_base.rag_runtime._open_existing_vector_db",
            side_effect=RuntimeError("collection missing"),
        ):
            with self.assertRaises(RagRuntimeInitializationError) as missing:
                create_rag_runtime(_runtime_config(), answer_llm="llm")
        self.assertEqual(missing.exception.stage, "chroma_collection")

        vector_db = object()
        with patch("Agent.knowledge_base.rag_runtime._validate_vector_db_directory"), patch(
            "Agent.knowledge_base.rag_runtime._validate_embedding_config"
        ), patch(
            "Agent.knowledge_base.rag_runtime._create_embedding_function", return_value=object()
        ), patch(
            "Agent.knowledge_base.rag_runtime._open_existing_vector_db", return_value=vector_db
        ), patch(
            "Agent.knowledge_base.rag_runtime._count_chunks", side_effect=ValueError("empty")
        ):
            with self.assertRaises(RagRuntimeInitializationError) as empty:
                create_rag_runtime(_runtime_config(), answer_llm="llm")
        self.assertEqual(empty.exception.stage, "chunk_count")

    def test_startup_log_does_not_include_secret(self):
        secret = "unit-secret-value"
        config = _runtime_config(api_key=secret)
        with patch("Agent.knowledge_base.rag_runtime._validate_vector_db_directory"), patch(
            "Agent.knowledge_base.rag_runtime._validate_embedding_config"
        ), patch(
            "Agent.knowledge_base.rag_runtime._create_embedding_function", return_value=object()
        ), patch(
            "Agent.knowledge_base.rag_runtime._open_existing_vector_db", return_value=object()
        ), patch(
            "Agent.knowledge_base.rag_runtime._count_chunks", return_value=1
        ), patch(
            "Agent.knowledge_base.rag_runtime.Bm25sSparseRetriever.from_vector_db",
            return_value=object(),
        ), self.assertLogs(level=logging.INFO) as captured:
            create_rag_runtime(config, answer_llm="llm")

        self.assertTrue(any(record.event_code == "rag.runtime.ready" for record in captured.records))
        self.assertNotIn(secret, repr(captured.records))

    def test_sparse_index_failure_keeps_runtime_failure_stage(self):
        """确认 BM25s 索引构建异常仍归类为 sparse_corpus。"""
        vector_db = object()
        with patch("Agent.knowledge_base.rag_runtime._validate_vector_db_directory"), patch(
            "Agent.knowledge_base.rag_runtime._validate_embedding_config"
        ), patch(
            "Agent.knowledge_base.rag_runtime._create_embedding_function", return_value=object()
        ), patch(
            "Agent.knowledge_base.rag_runtime._open_existing_vector_db", return_value=vector_db
        ), patch(
            "Agent.knowledge_base.rag_runtime._count_chunks", return_value=1
        ), patch(
            "Agent.knowledge_base.rag_runtime.Bm25sSparseRetriever.from_vector_db",
            side_effect=RuntimeError("bm25s failed"),
        ):
            with self.assertRaises(RagRuntimeInitializationError) as raised:
                create_rag_runtime(_runtime_config(), answer_llm="llm")

        self.assertEqual(raised.exception.stage, "sparse_corpus")


class SparseRetrieverBehaviorTests(unittest.TestCase):
    def _retriever(self):
        """通过伪 Chroma collection 构造固定语料的 BM25s 检索器。"""
        texts = ["因果 推断 treatment_effect", "相关性不是因果", "unrelated"]
        fake_query_rag = types.ModuleType("Agent.knowledge_base.query_rag")
        fake_query_rag._normalize_chunk_metadata = (
            lambda metadata, _content, fallback_index=0: {
                **metadata,
                "chunk_id": metadata.get("chunk_id", f"c{fallback_index}"),
            }
        )
        vector_db = Mock()
        vector_db.get.return_value = {
            "documents": texts,
            "metadatas": [{"chunk_id": f"c{index}"} for index in range(len(texts))],
            "ids": [f"v{index}" for index in range(len(texts))],
        }
        with patch.dict(sys.modules, {"Agent.knowledge_base.query_rag": fake_query_rag}):
            return Bm25sSparseRetriever.from_vector_db(vector_db)

    def test_tokenizer_truncation_is_preserved(self):
        """确认迁移后继续使用项目原有中英文分词和长度上限。"""
        self.assertEqual(tokenize_text("因果_test"), ["因", "果", "因果", "test"])
        self.assertEqual(len(tokenize_text("因" * 600)), 512)

    def test_from_vector_db_reads_once_and_freezes_internal_corpus(self):
        fake_query_rag = types.ModuleType("Agent.knowledge_base.query_rag")
        fake_query_rag._normalize_chunk_metadata = (
            lambda metadata, _content, fallback_index=0: {
                **metadata,
                "chunk_id": metadata.get("chunk_id", f"c{fallback_index}"),
            }
        )
        vector_db = Mock()
        vector_db.get.return_value = {
            "documents": ["因果推断", "相关性"],
            "metadatas": [{"chunk_id": "c0"}, {"chunk_id": "c1"}],
            "ids": ["v0", "v1"],
        }

        with patch.dict(
            sys.modules, {"Agent.knowledge_base.query_rag": fake_query_rag}
        ), self.assertLogs(
            "Agent.knowledge_base.sparse_retriever", level="INFO"
        ) as captured:
            retriever = Bm25sSparseRetriever.from_vector_db(vector_db)

        vector_db.get.assert_called_once_with(include=["documents", "metadatas"])
        self.assertEqual(retriever.size, 2)
        self.assertEqual(retriever._index.k1, 1.5)
        self.assertEqual(retriever._index.b, 0.75)
        self.assertEqual(retriever._index.method, "lucene")
        self.assertEqual(retriever._index.dtype, "float64")
        self.assertEqual(retriever._index.backend, "numpy")
        self.assertEqual(retriever._index.csc_backend, "numpy")
        ready_records = [
            record for record in captured.records if record.event_code == "rag.sparse.ready"
        ]
        self.assertEqual(len(ready_records), 1)
        self.assertEqual(ready_records[0].details["version"], bm25s.__version__)
        self.assertEqual(ready_records[0].details["documents"], 2)
        self.assertEqual(ready_records[0].details["backend"], "numpy")
        self.assertNotIn("因果推断", repr(ready_records[0].details))
        with self.assertRaises(TypeError):
            retriever.entries[0].metadata["changed"] = True
        result = retriever.search("因果", 1)
        result[0]["metadata"]["changed"] = True
        self.assertNotIn("changed", retriever.entries[0].metadata)

    def test_search_order_top_k_and_results_do_not_share_mutable_state(self):
        retriever = self._retriever()
        first = retriever.search("因果", top_k=2)
        second = retriever.search("因果", top_k=2)

        self.assertEqual([item["metadata"]["chunk_id"] for item in first], ["c0", "c1"])
        self.assertEqual(len(first), 2)
        first[0]["metadata"]["changed"] = True
        first[0]["retrieval_sources"].add("dense")
        self.assertNotIn("changed", second[0]["metadata"])
        self.assertEqual(second[0]["retrieval_sources"], {"sparse"})

    def test_search_restores_legacy_raw_score_scale(self):
        """确认适配层恢复 BM25s Lucene 省略的旧分数常数。"""
        retriever = self._retriever()
        query_tokens = tokenize_text("因果")
        _, raw_scores = retriever._index.retrieve(
            [query_tokens],
            k=1,
            sorted=True,
            show_progress=False,
            n_threads=0,
            backend_selection="numpy",
        )

        result = retriever.search("因果", top_k=1)

        self.assertAlmostEqual(result[0]["sparse_score"], float(raw_scores[0][0]) * 2.5)

    def test_search_handles_empty_no_match_and_top_k_boundaries(self):
        """确认空查询、零分补位和 top_k 边界遵循现有接口合同。"""
        retriever = self._retriever()

        self.assertEqual(retriever.search("", top_k=2), [])
        self.assertEqual(retriever.search("zzznomatch", top_k=2), [])
        self.assertEqual(retriever.search("因果", top_k=0), [])
        self.assertEqual(retriever.search("因果", top_k=-1), [])
        oversized = retriever.search("因果", top_k=99)
        self.assertEqual([item["metadata"]["chunk_id"] for item in oversized], ["c0", "c1"])
        self.assertTrue(all(item["sparse_score"] > 0 for item in oversized))
        self.assertTrue(all("sparse_score_norm" in item for item in oversized))

    def test_concurrent_searches_return_independent_candidates(self):
        retriever = self._retriever()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: retriever.search("因果", 2), range(2)))

        self.assertIsNot(results[0], results[1])
        self.assertIsNot(results[0][0]["metadata"], results[1][0]["metadata"])
        self.assertIsNot(results[0][0]["retrieval_sources"], results[1][0]["retrieval_sources"])


if __name__ == "__main__":
    unittest.main()
