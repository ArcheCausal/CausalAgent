"""RAG Runtime 之上的查询服务与可选能力降级。"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Mapping, Protocol, Union

from Agent.knowledge_base.embedding_runtime import EmbeddingApiError
from Agent.knowledge_base.rag_runtime import RagRuntime


UNAVAILABLE_RAG_RESULT = {
    "success": False,
    "status": "unavailable",
    "summary": "知识库暂不可用，当前结果未使用知识库增强。",
    "questions": [],
    "evidence_count": 0,
}


class RagQueryService(Protocol):
    """生产 RAG Tool 唯一依赖的最小接口。"""

    def get_response(self, questions: List[Union[str, Dict[str, Any]]]) -> Dict[str, Any]:
        """对标准化前的问题列表执行知识库查询。"""


class RagService:
    """使用同一 Runtime 执行检索、回答和元数据查询。"""

    def __init__(self, runtime: RagRuntime):
        """绑定已完整初始化的 Runtime。"""
        self._runtime = runtime

    @property
    def runtime(self) -> RagRuntime:
        """返回已绑定的 Runtime。"""
        return self._runtime

    def _load_retrieval_config(self) -> Any:
        """每次问题执行前读取当前发布的生产检索配置。"""
        from Agent.knowledge_base import query_rag

        return query_rag._load_rag_config(self.runtime.config.production_config_path)[0]

    def build_retrieval_trace(self, question_text: str, config: Any = None) -> Dict[str, Any]:
        """使用 Runtime 资源执行完整检索 trace。"""
        from Agent.knowledge_base import query_rag

        active_config = config or query_rag.RagRetrievalConfig()
        return query_rag._build_retrieval_trace_with_resources(
            question_text,
            active_config,
            vector_db=self.runtime.vector_db,
            embedding_function=self.runtime.embedding,
            sparse_retriever=self.runtime.sparse_retriever,
        )

    def _runtime_identity_diagnostics(self) -> Dict[str, Any]:
        """返回 evidence-only 必须携带的 active release 脱敏身份。"""

        from Agent.knowledge_base.embedding_runtime import EmbeddingConfiguration

        try:
            fingerprint = EmbeddingConfiguration.from_mapping(
                self.runtime.config.embedding_config
            ).fingerprint()
            release_id = str(self.runtime.config.release_id)
        except Exception as exc:
            # 诊断失败必须由 get_evidence 的 readiness 边界统一收口；
            # 异常正文可能包含路径或凭据，不能向协议层传播。
            raise RuntimeError("RAG runtime identity unavailable") from exc
        return {
            "readiness": "ready",
            "release_id": release_id,
            "embedding_fingerprint": fingerprint,
        }

    def _safe_runtime_identity_diagnostics(self) -> Dict[str, Any]:
        """在协议错误路径上返回不泄露异常的最小 Runtime 身份。"""
        try:
            return self._runtime_identity_diagnostics()
        except Exception:
            return {
                "readiness": "unavailable",
                "release_id": None,
                "embedding_fingerprint": None,
            }

    @staticmethod
    def _unavailable_evidence_result(
        query: str,
        identity: Dict[str, Any],
        reason_code: str,
    ) -> Dict[str, Any]:
        """构造不包含原始异常或运行时配置的稳定降级结果。"""
        return {
            "status": "unavailable",
            "query": query,
            "release_id": identity.get("release_id"),
            "evidence": [],
            "diagnostics": {**identity, "reason_code": reason_code},
        }

    def _document_display_name(self, metadata: Mapping[str, Any]) -> str | None:
        """用 release manifest 的 document_id → 相对路径解析展示名，缺失时回退检索元数据。"""

        document_names = getattr(self._runtime.config, "document_names", None) or {}
        return (
            document_names.get(str(metadata.get("document_id") or ""))
            or metadata.get("title")
            or metadata.get("source_name")
            or None
        )

    def _build_evidence_result(
        self,
        question: str,
        identity: Dict[str, Any],
        config: Any,
        payloads: List[Dict[str, Any]],
        trace: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把检索输出投影为稳定协议；调用方负责提供异常边界。"""
        release_id = identity["release_id"]
        evidence: list[dict[str, Any]] = []
        for payload in payloads:
            metadata = payload.get("metadata") or {}
            evidence_id = str(payload.get("evidence_id") or len(evidence) + 1)
            display_name = self._document_display_name(metadata)
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "evidence_ref": f"rag:{release_id}:{evidence_id}",
                    "snippet": str(payload.get("content") or ""),
                    "source_title": display_name,
                    "source_url": metadata.get("source_url"),
                    "locator": (
                        f"{display_name or ''}"
                        f"#page={metadata.get('page', '')}"
                        f"#chunk={metadata.get('chunk_id', '')}"
                    ).strip("#"),
                    "modality": metadata.get("modality") or metadata.get("content_kind"),
                    "score": payload.get("rerank_score"),
                    "release_id": release_id,
                    "degradation_flags": (),
                    "dense_score": payload.get("dense_score"),
                    "sparse_score": payload.get("sparse_score"),
                    "rerank_score": payload.get("rerank_score"),
                    "sufficiency": "sufficient",
                }
            )
        return {
            "status": "available" if evidence else "no_relevant_evidence",
            "query": question,
            "release_id": release_id,
            "evidence": evidence,
            "sufficiency": "sufficient" if evidence else "insufficient",
            "diagnostics": {
                **identity,
                "retrieval_config": config.to_dict(),
                "evidence_count": len(evidence),
                "retrieval_trace": {
                    "timings_ms": dict(trace.get("timings_ms") or {}),
                    "stage_counts": {
                        name: len(items) if isinstance(items, list) else 0
                        for name, items in (trace.get("stages") or {}).items()
                    },
                },
            },
        }

    def get_evidence(
        self,
        query: str,
        *,
        max_contexts: int | None = None,
    ) -> Dict[str, Any]:
        """只执行检索并返回证据，不调用 RAG answer model。

        Deep Agent 路径使用该入口；旧 ``get_response`` 继续保留回答模型兼容
        语义，避免把现有 RAG 评测/发布链路与新工具路径混在一起。
        """

        question = str(query or "").strip()
        if not question:
            identity = self._safe_runtime_identity_diagnostics()
            return {
                "status": "protocol_error",
                "query": question,
                "release_id": identity["release_id"],
                "evidence": [],
                "diagnostics": {**identity, "reason_code": "empty_query"},
            }

        identity = self._safe_runtime_identity_diagnostics()
        stage = "retrieval_import"
        try:
            from Agent.knowledge_base import query_rag

            stage = "retrieval_config"
            config = self._load_retrieval_config()
            stage = "readiness"
            identity = self._runtime_identity_diagnostics()
            stage = "retrieval"
            trace = self.build_retrieval_trace(question, config=config)
            payloads = query_rag._build_evidence_payloads(
                trace["stages"]["final"],
                max_chars=config.max_evidence_chars,
            )
            payloads = query_rag.compress_evidence_payloads(
                payloads,
                max_contexts=max_contexts,
                strategy=config.answer_context_compression,
            )
            return self._build_evidence_result(
                question, identity, config, payloads, trace
            )
        except EmbeddingApiError:
            return self._unavailable_evidence_result(
                question, identity, "embedding_unavailable"
            )
        except Exception:
            if stage == "readiness":
                identity = {
                    "readiness": "unavailable",
                    "release_id": None,
                    "embedding_fingerprint": None,
                }
            reason_code = {
                "retrieval_import": "retrieval_unavailable",
                "retrieval_config": "retrieval_config_unavailable",
                "readiness": "rag_readiness_unavailable",
                "retrieval": "retrieval_unavailable",
            }[stage]
            return self._unavailable_evidence_result(question, identity, reason_code)

    def get_vector_db_metadata_summary(self, limit: int = 10000) -> Dict[str, Any]:
        """汇总 Runtime 已打开 collection 的 metadata。"""
        from collections import Counter

        raw = self.runtime.vector_db.get(include=["metadatas"], limit=limit)
        metadatas = raw.get("metadatas") or []
        metadata_key_counts = Counter(
            key
            for metadata in metadatas
            for key in metadata
        )
        return {
            "exists": True,
            "persist_directory": self.runtime.config.vector_db_dir,
            "collection_name": self.runtime.config.collection_name,
            "release_id": self.runtime.config.release_id,
            "embedding_config": dict(self.runtime.config.embedding_config),
            "vector_count": len(raw.get("ids") or []),
            "metadata_key_counts": dict(metadata_key_counts),
        }

    def answer_question(
        self,
        question_payload: Dict[str, Any],
        evidence_payloads: List[Dict[str, Any]],
        answer_prompt: Any = None,
    ) -> Dict[str, Any]:
        """使用 Runtime 显式持有的回答 LLM 生成兼容回答。"""
        from Agent.knowledge_base import query_rag

        return query_rag._answer_question_with_llm(
            question_payload,
            evidence_payloads,
            answer_llm=self.runtime.answer_llm,
            answer_prompt=answer_prompt,
        )

    def get_response(
        self,
        questions: List[Union[str, Dict[str, Any]]],
        *,
        retrieve_candidates: Callable[..., List[Dict[str, Any]]] | None = None,
        answer_question: Callable[..., Dict[str, Any]] | None = None,
        summary_formatter: Callable[..., str] | None = None,
        config_loader: Callable[[], Any] | None = None,
    ) -> Dict[str, Any]:
        """逐问题热加载检索配置，并保持原有响应结构。"""
        from Agent.knowledge_base import query_rag

        if not questions:
            return {
                "success": True,
                "summary": "没有生成任何需要查询知识库的问题。",
                "questions": [],
                "evidence_count": 0,
            }

        question_results: List[Dict[str, Any]] = []
        total_evidence_count = 0
        for question in questions:
            question_payload = query_rag._normalize_question_payload(question)
            question_text = question_payload["question"].strip()
            if not question_text:
                continue

            production_config = (
                config_loader()[0] if config_loader is not None else self._load_retrieval_config()
            )
            try:
                if retrieve_candidates is None:
                    trace = self.build_retrieval_trace(question_text, config=production_config)
                    candidates = trace["stages"]["final"]
                else:
                    candidates = retrieve_candidates(question_text, config=production_config)
            except EmbeddingApiError:
                return dict(UNAVAILABLE_RAG_RESULT)
            evidence_payloads = query_rag._build_evidence_payloads(
                candidates,
                max_chars=production_config.max_evidence_chars,
            )
            answer_evidence_payloads = query_rag.compress_evidence_payloads(
                evidence_payloads,
                max_contexts=production_config.answer_max_contexts,
                strategy=production_config.answer_context_compression,
            )
            total_evidence_count += len(answer_evidence_payloads)
            if answer_question is None:
                answer_result = self.answer_question(question_payload, answer_evidence_payloads)
            else:
                answer_result = answer_question(question_payload, answer_evidence_payloads)
            question_results.append(answer_result)

        formatter = summary_formatter or query_rag.format_rag_summary_for_prompt
        summary = formatter(
            {"success": True, "questions": question_results},
            max_questions=len(question_results),
            include_evidence=True,
        )
        return {
            "success": True,
            "summary": summary,
            "questions": question_results,
            "evidence_count": total_evidence_count,
        }


class UnavailableRagService:
    """Runtime 初始化失败后绑定的稳定、无敏感信息降级服务。"""

    def get_response(self, questions: List[Union[str, Dict[str, Any]]]) -> Dict[str, Any]:
        """忽略输入并返回新的稳定降级对象。"""
        return dict(UNAVAILABLE_RAG_RESULT)

    def get_evidence(self, query: str, *, max_contexts: int | None = None) -> Dict[str, Any]:
        """返回 evidence-only 的稳定不可用结果，不访问模型或索引。"""
        del max_contexts
        return {
            "status": "unavailable",
            "query": str(query or ""),
            "release_id": None,
            "evidence": [],
            "diagnostics": {
                "readiness": "unavailable",
                "embedding_fingerprint": None,
                "reason_code": "rag_unavailable",
            },
        }


class CompatibilityRagService(RagService):
    """仅为遗留调用延迟创建严格 Runtime 的单一 Service。"""

    def __init__(self, runtime_factory: Callable[[], RagRuntime]):
        """保存 Runtime 工厂，但不在模块导入或纯 mock 调用时加载资源。"""
        self._runtime_factory = runtime_factory
        self._runtime = None
        self._runtime_lock = threading.Lock()

    @property
    def runtime(self) -> RagRuntime:
        """首次访问时严格创建完整 Runtime，失败直接向调用方抛出。"""
        if self._runtime is None:
            with self._runtime_lock:
                if self._runtime is None:
                    self._runtime = self._runtime_factory()
        return self._runtime
