"""生产 RAG 的进程级显式资源生命周期。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from Agent.knowledge_base.multimodal.defaults import resolve_production_embedding_config as resolve_embedding_runtime_config
from Agent.knowledge_base.multimodal.production import has_frozen_production_identity
from Agent.knowledge_base.multimodal.release import MANIFEST_SCHEMA_NAME, MANIFEST_SCHEMA_VERSION, POINTER_SCHEMA_VERSION, compute_manifest_sha256, validate_manifest_artifacts, validate_manifest_contract, validate_manifest_counts
from Agent.knowledge_base.sparse_retriever import Bm25sSparseRetriever, SparseRetriever
from Agent.knowledge_base.embedding_runtime import (
    EmbeddingConfiguration,
    create_embedding_function,
)
from observability.logging_runtime import log_event


BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
DEFAULT_VECTOR_DB_DIR = BASE_DIR / "db"
DEFAULT_MULTIMODAL_INDEX_ROOT = BASE_DIR / "multimodal_indexes"
DEFAULT_MULTIMODAL_ACTIVE_INDEX_CONFIG = BASE_DIR / "multimodal_runtime" / "active_index.json"
DEFAULT_PRODUCTION_CONFIG_PATH = BASE_DIR / "rag" / "runtime" / "production_rag_config.json"
SAFE_EMBEDDING_CONFIG_KEYS = {
    "status",
    "mode",
    "provider",
    "provider_setting",
    "model",
    "dimension",
    "normalized",
    "path",
    "path_exists",
    "api_key_env",
    "base_url_env",
    "model_env",
    "model_revision",
    "endpoint_identity",
    "normalization",
    "distance_metric",
    "query_transform",
    "document_transform",
    "request_contract_version",
    "missing",
    "message",
}
_LOCAL_EMBEDDING_INIT_LOCK = threading.Lock()


@dataclass(frozen=True)
class RagRuntimeConfig:
    """RAG Runtime 的不可变、无密钥配置快照。"""

    vector_db_dir: str
    collection_name: str
    production_config_path: str
    embedding_config: Mapping[str, Any]
    release_id: str = "current"
    embedding_scope: str = "production"
    document_names: Mapping[str, str] = field(default_factory=dict)
    """document_id → release manifest 相对路径；只用于知识库来源展示名。"""

    def __post_init__(self) -> None:
        """冻结调用方传入的 embedding 配置副本，避免嵌套对象被修改。"""
        safe_config = EmbeddingConfiguration.from_mapping(self.embedding_config).to_runtime_dict()
        safe_config = {key: value for key, value in safe_config.items() if key in SAFE_EMBEDDING_CONFIG_KEYS}
        if "missing" in safe_config:
            safe_config["missing"] = tuple(safe_config["missing"])
        object.__setattr__(self, "embedding_config", MappingProxyType(safe_config))
        object.__setattr__(
            self,
            "document_names",
            MappingProxyType(
                {
                    str(key): str(value)
                    for key, value in dict(self.document_names or {}).items()
                    if str(key) and str(value)
                }
            ),
        )

    @classmethod
    def from_environment(cls) -> "RagRuntimeConfig":
        """从 active manifest 创建配置；只有旧 manifest 才兼容读取环境 resolver。"""
        release = _resolve_multimodal_release()
        if release.get("embedding_config") is None:
            resolved = resolve_embedding_runtime_config()
            release = _resolve_multimodal_release(resolved)
        safe_embedding_config = release["embedding_config"]
        return cls(
            vector_db_dir=str(release["vector_db_dir"]),
            collection_name=str(release["collection_name"]),
            production_config_path=os.environ.get(
                "RAG_PRODUCTION_CONFIG_PATH",
                str(DEFAULT_PRODUCTION_CONFIG_PATH),
            ),
            embedding_config=MappingProxyType(safe_embedding_config),
            release_id=str(release["index_version"]),
            document_names=release.get("document_names") or {},
        )


def _document_names_from_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    """投影 release manifest 的 document_id → 相对路径，作为知识库来源展示名。"""

    names: dict[str, str] = {}
    for source in manifest.get("sources") or []:
        if not isinstance(source, dict):
            continue
        document_id = source.get("document_id")
        relative_path = source.get("relative_path")
        if isinstance(document_id, str) and document_id and isinstance(relative_path, str) and relative_path:
            names[document_id] = relative_path
    return names


def _resolve_multimodal_release(embedding_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """校验 active pointer、manifest 和 embedding 指纹并解析索引位置。"""
    active_path = Path(
        os.environ.get("MULTIMODAL_ACTIVE_INDEX_CONFIG", str(DEFAULT_MULTIMODAL_ACTIVE_INDEX_CONFIG))
    )
    if not active_path.is_file():
        raise FileNotFoundError(f"多模态 active pointer 不存在: {active_path}")
    active = json.loads(active_path.read_text(encoding="utf-8"))

    index_root = Path(
        os.environ.get("MULTIMODAL_INDEX_ROOT", str(DEFAULT_MULTIMODAL_INDEX_ROOT))
    ).resolve()
    pointer_entry = active.get("active") if isinstance(active.get("active"), dict) else active
    if not isinstance(pointer_entry, dict):
        raise ValueError("多模态 active pointer 缺少 active release")
    if isinstance(active.get("active"), dict) and active.get("schema_version") != POINTER_SCHEMA_VERSION:
        raise ValueError("多模态 active pointer schema 不受支持")
    index_path = Path(str(pointer_entry.get("index_path", "")))
    if not index_path.parts or index_path.is_absolute():
        raise ValueError("多模态 active index_path 必须是相对路径")
    vector_db_dir = (index_root / index_path).resolve()
    try:
        vector_db_dir.relative_to(index_root)
    except ValueError as exc:
        raise ValueError("多模态 active index_path 越界") from exc

    manifest_path = vector_db_dir.parent / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"多模态 manifest 不存在: {manifest_path}")
    manifest_hash = compute_manifest_sha256(manifest_path)
    if manifest_hash != pointer_entry.get("manifest_sha256"):
        raise ValueError("多模态 active index manifest 哈希不匹配")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("identity_sha256") or manifest.get("schema_version") in (MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_NAME):
        validate_manifest_contract(manifest)
        release_dir = vector_db_dir.parent
        validate_manifest_artifacts(manifest, release_dir)
        if any(not (release_dir / name).is_file() for name in ("units.jsonl", "build_state.json")):
            raise FileNotFoundError("多模态 active release 必要索引产物缺失")
        if not vector_db_dir.is_dir():
            raise FileNotFoundError("多模态 active release 向量目录缺失")
        build_state = json.loads((release_dir / "build_state.json").read_text(encoding="utf-8"))
        unit_count = sum(1 for line in (release_dir / "units.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
        state_unit_count = int(build_state.get("unit_count", -1))
        state_vector_count = int(build_state.get("vector_count", -2))
        if build_state.get("status") != "staged_complete" or state_unit_count != state_vector_count or unit_count != state_unit_count:
            raise ValueError("多模态 active release 索引计数或构建状态无效")
        actual_counts: dict[str, Any] = {
            "unit_count": state_unit_count,
            "vector_count": state_vector_count,
        }
        issues_path = release_dir / "issues.jsonl"
        if issues_path.is_file():
            actual_counts["issues_count"] = sum(
                1 for line in issues_path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        validate_manifest_counts(manifest, actual_counts=actual_counts)
    if (
        os.environ.get("MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE", "").lower() != "true"
        and not has_frozen_production_identity(manifest)
    ):
        raise ValueError("多模态 active index 不是冻结的正式知识源")
    if os.environ.get("MULTIMODAL_ALLOW_NON_PRODUCTION_ACTIVE", "").lower() != "true":
        from Agent.knowledge_base.multimodal.production import validate_production_manifest

        policy_failures = validate_production_manifest(manifest)
        if policy_failures:
            raise ValueError("多模态 active release 正式策略不允许: " + ", ".join(policy_failures))
    manifest_config_payload = manifest.get("embedding_config")
    if not isinstance(manifest_config_payload, dict) and isinstance(manifest.get("embedding"), dict) and "distance_metric" in manifest["embedding"]:
        manifest_config_payload = manifest["embedding"]
    manifest_config = None
    if isinstance(manifest_config_payload, dict):
        manifest_config = EmbeddingConfiguration.from_manifest(manifest_config_payload)
        runtime_fingerprint = manifest_config.fingerprint()
        manifest_fingerprint = manifest.get("embedding")
        if isinstance(manifest_fingerprint, dict) and "distance_metric" not in manifest_fingerprint:
            expected_fingerprint = {
                key: runtime_fingerprint[key]
                for key in manifest_fingerprint
                if key in runtime_fingerprint
            }
            if manifest_fingerprint != expected_fingerprint:
                raise ValueError("多模态 embedding 指纹不匹配")
        if isinstance(pointer_entry.get("embedding"), dict) and pointer_entry["embedding"] != manifest_fingerprint:
            raise ValueError("多模态 active embedding 指纹不匹配")
    elif embedding_config is not None:
        runtime_fingerprint = {
            "provider": embedding_config.get("provider"),
            "model": embedding_config.get("model"),
            "mode": embedding_config.get("mode"),
            "normalized": embedding_config.get("normalized", embedding_config.get("mode") == "local"),
        }
        if "dimension" in embedding_config:
            runtime_fingerprint["dimension"] = embedding_config["dimension"]
        pointer_embedding = pointer_entry.get("embedding") or active.get("embedding")
        if (pointer_embedding is not None and pointer_embedding != manifest.get("embedding")) or manifest.get("embedding") != runtime_fingerprint:
            raise ValueError("多模态 embedding 指纹不匹配")
    else:
        runtime_fingerprint = None
    release_id = str(pointer_entry.get("release_id") or pointer_entry.get("index_version") or "")
    manifest_release_id = manifest.get("release_id")
    if manifest_release_id and release_id and manifest_release_id != release_id:
        raise ValueError("多模态 release id 不匹配")
    if manifest.get("index_version") and pointer_entry.get("index_version") and manifest.get("index_version") != pointer_entry.get("index_version"):
        raise ValueError("多模态 index version 不匹配")
    collection_name = str(
        pointer_entry.get("collection_name")
        or manifest.get("collection_name")
        or manifest.get("retrieval_index", {}).get("collection", "")
        or (f"causal_multimodal_{release_id}" if release_id else "")
    ).strip()
    if not collection_name:
        raise ValueError("多模态 active collection_name 为空")
    resolved_embedding = (
        manifest_config.to_runtime_dict()
        if manifest_config is not None
        else dict(embedding_config) if embedding_config is not None else None
    )
    return {
        "vector_db_dir": vector_db_dir,
        "collection_name": collection_name,
        "index_version": release_id or manifest.get("index_version"),
        "embedding_config": resolved_embedding,
        "document_names": _document_names_from_manifest(manifest),
    }


def recover_invalid_active_release() -> dict[str, Any]:
    """在 release 身份/产物损坏时把唯一 fallback 提升为 active。"""
    from Agent.knowledge_base.multimodal.release import ReleaseManager

    active_path = Path(
        os.environ.get("MULTIMODAL_ACTIVE_INDEX_CONFIG", str(DEFAULT_MULTIMODAL_ACTIVE_INDEX_CONFIG))
    )
    index_root = Path(
        os.environ.get("MULTIMODAL_INDEX_ROOT", str(DEFAULT_MULTIMODAL_INDEX_ROOT))
    )
    return ReleaseManager(index_root, active_path).validate_active(enforce_production_policy=True)


@dataclass(frozen=True)
class RagRuntime:
    """持有 worker 进程内共享的全部 RAG 重资源。"""

    config: RagRuntimeConfig
    embedding: Any
    vector_db: Any
    sparse_retriever: SparseRetriever
    answer_llm: Any
    chunk_count: int


class RagRuntimeInitializationError(RuntimeError):
    """标识严格 Runtime 初始化失败及其具体阶段。"""

    def __init__(self, stage: str, cause: BaseException):
        self.stage = stage
        self.cause = cause
        super().__init__(f"RAG Runtime 初始化失败，阶段={stage}: {cause}")


def _validate_vector_db_directory(config: RagRuntimeConfig) -> None:
    """确认向量库目录存在且确实是目录。"""
    path = Path(config.vector_db_dir)
    if not path.exists():
        raise FileNotFoundError(f"知识库持久化目录不存在: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"知识库持久化路径不是目录: {path}")


def _validate_embedding_config(config: RagRuntimeConfig) -> None:
    """在加载模型前验证脱敏后的 embedding 配置状态。"""
    embedding_config = config.embedding_config
    if embedding_config.get("status") != "ready":
        raise ValueError(str(embedding_config.get("message", "embedding 配置不可用")))


def _create_embedding_function(config: RagRuntimeConfig) -> Any:
    """按配置创建唯一的 embedding 实例。"""
    return create_embedding_function(config.embedding_config, scope=config.embedding_scope)


def _create_embedding_function_thread_safe(config: RagRuntimeConfig) -> Any:
    """串行化进程内本地模型构造，规避 Transformers/PyTorch 首次加载竞态。"""
    if config.embedding_config.get("mode") != "local":
        return _create_embedding_function(config)
    with _LOCAL_EMBEDDING_INIT_LOCK:
        return _create_embedding_function(config)


def _open_existing_vector_db(config: RagRuntimeConfig, embedding: Any) -> Any:
    """仅打开已存在的 Chroma collection，禁止缺失时自动创建。"""
    import chromadb
    from langchain_chroma import Chroma

    client = chromadb.PersistentClient(path=config.vector_db_dir)
    client.get_collection(name=config.collection_name)
    return Chroma(
        client=client,
        embedding_function=embedding,
        collection_name=config.collection_name,
        create_collection_if_not_exists=False,
    )


def _count_chunks(vector_db: Any) -> int:
    """读取 collection chunk 数并拒绝空知识库。"""
    chunk_count = int(vector_db._collection.count())
    if chunk_count <= 0:
        raise ValueError("RAG collection 为空")
    return chunk_count


def create_rag_runtime(config: RagRuntimeConfig, answer_llm: Any) -> RagRuntime:
    """按固定顺序严格创建完整 Runtime，失败时不返回半成品。"""
    started = time.perf_counter()
    stages = (
        ("vector_db_directory", lambda: _validate_vector_db_directory(config)),
        ("embedding_config", lambda: _validate_embedding_config(config)),
        ("embedding_instance", lambda: _create_embedding_function_thread_safe(config)),
    )
    embedding = None
    for stage, operation in stages:
        try:
            result = operation()
            if stage == "embedding_instance":
                embedding = result
        except Exception as exc:
            raise RagRuntimeInitializationError(stage, exc) from exc

    try:
        vector_db = _open_existing_vector_db(config, embedding)
    except Exception as exc:
        raise RagRuntimeInitializationError("chroma_collection", exc) from exc
    try:
        chunk_count = _count_chunks(vector_db)
    except Exception as exc:
        raise RagRuntimeInitializationError("chunk_count", exc) from exc
    try:
        sparse_retriever = Bm25sSparseRetriever.from_vector_db(vector_db)
    except Exception as exc:
        raise RagRuntimeInitializationError("sparse_corpus", exc) from exc

    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    embedding_config = config.embedding_config
    log_event(
        LOGGER,
        "rag.runtime.ready",
        details={
            "provider": str(embedding_config.get("provider", "unknown")),
            "model": str(embedding_config.get("model", "unknown")),
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "release_id": config.release_id,
        },
    )
    return RagRuntime(
        config=config,
        embedding=embedding,
        vector_db=vector_db,
        sparse_retriever=sparse_retriever,
        answer_llm=answer_llm,
        chunk_count=chunk_count,
    )
