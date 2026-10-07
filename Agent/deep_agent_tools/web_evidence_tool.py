"""SearXNG arXiv/Crossref/OpenAlex snippet-only Web evidence Tool。"""

from __future__ import annotations

import inspect
import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

from Agent.execution_control import JobExecutionRevoked

from pydantic import BaseModel, ConfigDict, Field

from .models import WebEvidenceResult, canonical_json_bytes
from .algorithm_specs import build_model_tool_input_schema
from .runtime_updates import (
    build_terminal_invocation,
    build_tool_command,
    emit_public_decision_event,
    emit_tool_lifecycle_event,
    resolve_runtime_invocation,
    scope_evidence_payload,
    with_tool_runtime_schema,
)


class WebEvidenceSearcher(Protocol):
    def search(self, query: str, *, max_results: int = 9) -> Mapping[str, Any]:
        ...


class _WebEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    query_en: str | None = None
    max_results: int = Field(default=9, ge=1, le=9)


class ExistingSearXNGWebSearcher:
    """复用当前受控 SearXNG 三来源函数，不把 provider URL 暴露给模型。"""

    def search(self, query: str, *, max_results: int = 9) -> Mapping[str, Any]:
        from Agent.causal_agent.web_search_node import _merge_by_engine_top3, web_search

        payload = web_search(query)
        results = payload.get("results", []) if isinstance(payload, Mapping) else []
        return {
            "results": _merge_by_engine_top3(
                results,
                top_per_engine=3,
                max_results=min(max_results, 9),
            )
        }


@dataclass(frozen=True)
class WebEvidenceQuery:
    query: str
    query_en: str | None = None
    max_results: int = 9

    def __post_init__(self) -> None:
        if not self.query or not self.query.strip():
            raise ValueError("query must be non-blank")
        if self.max_results <= 0:
            raise ValueError("max_results must be positive")
        if self.max_results > 9:
            object.__setattr__(self, "max_results", 9)


def _evidence_ref(item: Mapping[str, Any]) -> str:
    payload = {
        "source": item.get("source"),
        "url": item.get("url"),
        "title": item.get("title"),
        "snippet": item.get("snippet"),
    }
    return "web:" + sha256(canonical_json_bytes(payload)).hexdigest()[:32]


@dataclass
class WebEvidenceTool:
    """可信开关在对象构造时注入，模型不能通过 query 覆盖。"""

    searcher: WebEvidenceSearcher | Callable[..., Mapping[str, Any]]
    web_search_enabled: bool | Callable[[], bool] = True

    name: str = "web_evidence_search"
    description: str = (
        "从 arXiv、Crossref 和 OpenAlex 检索学术 snippet 与来源元数据；"
        "不阅读全文，不把 snippet 自动提升为论文结论。"
    )

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": build_model_tool_input_schema(
                _WebEvidenceInput
            ).model_json_schema(),
        }

    def _enabled(self) -> bool:
        return bool(self.web_search_enabled() if callable(self.web_search_enabled) else self.web_search_enabled)

    async def get_evidence(
        self,
        query: str,
        *,
        query_en: str | None = None,
        max_results: int = 9,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        request = WebEvidenceQuery(query=query, query_en=query_en, max_results=max_results)
        if not (self._enabled() if enabled is None else enabled):
            return {
                "status": "disabled",
                "query": request.query,
                "query_en": request.query_en,
                "evidence": [],
                "diagnostics": {"reason_code": "web_search_disabled"},
            }

        try:
            search_query = request.query_en or request.query
            if hasattr(self.searcher, "search"):
                raw = self.searcher.search(search_query, max_results=request.max_results)
            else:
                raw = self.searcher(search_query, max_results=request.max_results)
            if inspect.isawaitable(raw):
                raw = await raw
        except (asyncio.CancelledError, JobExecutionRevoked):
            raise
        except Exception:
            return {
                "status": "unavailable",
                "query": request.query,
                "query_en": request.query_en,
                "evidence": [],
                "diagnostics": {"reason_code": "web_search_unavailable"},
            }

        if not isinstance(raw, Mapping) or not isinstance(raw.get("results", []), list):
            return {
                "status": "protocol_error",
                "query": request.query,
                "query_en": request.query_en,
                "evidence": [],
                "diagnostics": {"reason_code": "invalid_search_payload"},
            }

        evidence: list[WebEvidenceResult] = []
        fetched_at = datetime.now(timezone.utc)
        for item in raw.get("results", [])[: request.max_results]:
            if not isinstance(item, Mapping):
                continue
            snippet = str(item.get("snippet") or "").strip()
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not snippet or not title or not url:
                continue
            try:
                score = None if item.get("score") is None else float(item.get("score"))
            except (TypeError, ValueError):
                score = None
            evidence.append(
                WebEvidenceResult(
                    evidence_ref=_evidence_ref(item),
                    snippet=snippet,
                    source_title=title,
                    source_url=url,
                    locator=str(item.get("source") or "") or None,
                    score=score,
                    provider_status="available",
                    fetched_at=fetched_at,
                )
            )
        return {
            "status": "available" if evidence else "no_results",
            "query": request.query,
            "query_en": request.query_en,
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "evidence_by_ref": {
                item.evidence_ref: item.model_dump(mode="json") for item in evidence
            },
            "diagnostics": {"provider_result_count": len(raw.get("results", []))},
        }

    async def ainvoke(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await self.get_evidence(
            str(arguments.get("query") or ""),
            query_en=arguments.get("query_en"),
            max_results=int(arguments.get("max_results") or 9),
        )

    def to_langchain_tool(self) -> Any:
        """包装为把 Web evidence 与 terminal Ledger 同步写回 State 的 Tool。"""

        try:
            from langchain.tools import ToolRuntime
            from langchain_core.tools import StructuredTool
        except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
            raise RuntimeError("LangChain is not installed") from exc

        async def call(
            query: str,
            runtime: Any,
            query_en: str | None = None,
            max_results: int = 9,
            public_decision: Any = None,
        ) -> Any:
            identity = resolve_runtime_invocation(runtime)
            await identity.runtime_context.ensure_active()
            started_at = datetime.now(timezone.utc)
            runtime_enabled = getattr(
                identity.runtime_context,
                "web_search_enabled",
                None,
            )
            if public_decision is not None:
                await emit_public_decision_event(
                    runtime,
                    identity=identity,
                    tool_name=self.name,
                    public_decision=public_decision,
                    decision_kind="evidence",
                )
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_start",
                identity=identity,
                tool_name=self.name,
            )
            try:
                payload = await self.get_evidence(
                    query,
                    query_en=query_en,
                    max_results=max_results,
                    enabled=runtime_enabled if isinstance(runtime_enabled, bool) else None,
                )
                await identity.runtime_context.ensure_active()
            except (asyncio.CancelledError, JobExecutionRevoked):
                await emit_tool_lifecycle_event(
                    runtime,
                    event_type="tool_call_result",
                    identity=identity,
                    tool_name=self.name,
                    status="canceled",
                )
                raise
            except Exception:
                await emit_tool_lifecycle_event(
                    runtime,
                    event_type="tool_call_result",
                    identity=identity,
                    tool_name=self.name,
                    status="failed",
                    safe_error_code="WEB_SEARCH_PROTOCOL_ERROR",
                )
                raise
            status = str(payload.get("status") or "protocol_error")
            attempt_status = (
                "succeeded"
                if status in {"available", "no_results"}
                else "not_ready"
                if status == "disabled"
                else "failed"
            )
            safe_error_code = {
                "disabled": "WEB_SEARCH_DISABLED",
                "unavailable": "WEB_SEARCH_UNAVAILABLE",
                "protocol_error": "WEB_SEARCH_PROTOCOL_ERROR",
            }.get(status)
            await emit_tool_lifecycle_event(
                runtime,
                event_type="tool_call_result",
                identity=identity,
                tool_name=self.name,
                status=attempt_status,
                safe_error_code=safe_error_code,
            )
            terminal = build_terminal_invocation(
                identity=identity,
                tool_name=self.name,
                attempt_status=attempt_status,
                started_at=started_at,
                safe_error_code=safe_error_code,
            )
            payload = scope_evidence_payload(
                payload,
                invocation_id=terminal.invocation_id,
            )
            evidence_by_ref = {
                str(ref): WebEvidenceResult.model_validate(value)
                for ref, value in dict(payload.get("evidence_by_ref") or {}).items()
            }
            return build_tool_command(
                identity=identity,
                tool_name=self.name,
                payload=payload,
                ledger_record=terminal,
                state_updates={"web_evidence": evidence_by_ref},
            )

        call.__annotations__["runtime"] = ToolRuntime
        runtime_args_schema = with_tool_runtime_schema(
            build_model_tool_input_schema(_WebEvidenceInput),
            tool_name=self.name,
            tool_runtime_type=ToolRuntime,
        )
        return StructuredTool.from_function(
            coroutine=call,
            name=self.name,
            description=self.description,
            args_schema=runtime_args_schema,
        )


def build_default_web_evidence_tool(
    *,
    web_search_enabled: bool | Callable[[], bool] = True,
) -> WebEvidenceTool:
    """绑定现有 SearXNG 学术检索函数；不接受模型提供的 backend URL。"""

    return WebEvidenceTool(
        ExistingSearXNGWebSearcher(),
        web_search_enabled=web_search_enabled,
    )
