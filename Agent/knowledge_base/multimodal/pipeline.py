"""多模态知识库 inspect、ingest、evaluate、publish 与 rollback 编排。"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from Agent.knowledge_base.embedding_runtime import EmbeddingConfiguration, resolve_production_embedding_configuration
from config.secret_file import get_secret
from .assets import AssetStore
from .contracts import IngestionIssue, IssueSeverity, KnowledgeUnit, OutboundImageRecord, UnitStatus, VisionAnalysis, canonical_json, content_document_id, content_source_id, render_retrieval_text, sha256_bytes, stable_id
from .defaults import load_production_defaults, production_source_paths, resolve_production_sources
from .index import ActiveIndexRegistry, StagedIndex, embedding_fingerprint, replace_with_retry
from .parsers import IMAGE_SUFFIXES, PAGE_QUALITY_GATE_VERSION, PAGE_QUALITY_MIN_TEXT_COVERAGE, TEXT_SPLIT, ParsedDocument, ParsedItem, clear_docling_batch_cache, decide_page_route, docling_configuration, inspect_source, parse_document_page
from .remote_policy import RemoteSamplePolicy
from .release import MANIFEST_SCHEMA_NAME, MANIFEST_SCHEMA_VERSION, ReleaseManager, compute_manifest_sha256, directory_sha256, manifest_bytes, release_id_for_manifest, seal_manifest, validate_manifest_artifacts, validate_manifest_contract, validate_manifest_counts
from .table_recovery import TABLE_RECOVERY_ADAPTER_VERSION, RemoteVlmTableRecoveryProvider, TableRecoveryProvider, TableRecoveryResult, looks_like_markdown_table
from .vision import PROMPT_VERSION, REQUIRED_MODEL, RESPONSE_ADAPTER_VERSION, VisionAnalyzer
from observability.logging_runtime import log_event

LOCAL_PARSE_CHECKPOINT_SCHEMA = "local-parse-v2"
R3_DISCOVERY_CHECKPOINT_SCHEMA = "r3-discovery-v2"
MAX_RETRY_GENERATION = 2
CHECKPOINT_DATABASE_NAME = "checkpoints.sqlite3"
_MISSING_VISION_RESULT = object()
_NON_BLOCKING_ENRICHMENT_ISSUES = frozenset({"remote_image_failed", "table_recovery_failed"})
LOGGER = logging.getLogger(__name__)
_MULTIMODAL_FAILURE_CATEGORIES = frozenset(
    {
        "cache_write_failed",
        "connection",
        "http_400",
        "http_401",
        "http_403",
        "http_404",
        "http_422",
        "invalid_json",
        "invalid_schema",
        "quota_billing",
        "quota_billing_circuit_open",
        "rate_limited",
        "server_error",
        "timeout",
        "unexpected_error",
    }
)


def _exception_chain(exc: BaseException | None):
    """遍历脱敏所需的异常链，不读取异常文本。"""
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _safe_status_code(exc: BaseException | None) -> int | None:
    """只提取异常对象上已结构化且在 HTTP 范围内的状态码。"""
    for candidate in _exception_chain(exc):
        value = getattr(candidate, "status_code", None)
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
    return None


def _stable_failure_reason(exc: BaseException | None, fallback: str) -> str:
    """从异常链提取已注册的失败类别，未知类别回退到调用点错误码。"""
    for candidate in _exception_chain(exc):
        category = getattr(candidate, "category", None)
        if category in _MULTIMODAL_FAILURE_CATEGORIES:
            return category
        if candidate is exc or not isinstance(candidate, Exception):
            continue
        try:
            category = VisionAnalyzer._failure_category(candidate)
        except Exception:
            continue
        if category in _MULTIMODAL_FAILURE_CATEGORIES and category != "unexpected_error":
            return category
    return fallback


def _log_multimodal_parse_failure(
    *,
    phase: str,
    reason_code: str,
    source_alias: str | None = None,
    page_number: int | None = None,
    sequence_index: int | None = None,
    sequence_kind: str | None = None,
    exc: BaseException | None = None,
) -> None:
    """在解析边界写入固定目录事件，不携带异常文本、正文或路径。"""
    effective_reason_code = _stable_failure_reason(exc, reason_code)
    status_code = _safe_status_code(exc)
    log_event(
        LOGGER,
        "rag.multimodal.parse_failed",
        details={
            "phase": phase,
            "reason_code": effective_reason_code,
            "source_alias": source_alias,
            "page_number": max(0, int(page_number)) if page_number is not None else None,
            "image_index": max(0, int(sequence_index)) if sequence_kind == "image" and sequence_index is not None else None,
            "table_index": max(0, int(sequence_index)) if sequence_kind == "table" and sequence_index is not None else None,
            "status_code": status_code,
            "fallback_attempted": any(bool(getattr(candidate, "fallback_attempted", False)) for candidate in _exception_chain(exc)),
            "circuit_breaker_open": any(
                getattr(candidate, "category", None) in {"quota_billing", "quota_billing_circuit_open"}
                or type(candidate).__name__ == "VisionCircuitOpenError"
                for candidate in _exception_chain(exc)
            ),
        },
    )


def _log_page_parse_issues(issues: list[IngestionIssue], page_number: int, *, source_alias: str | None = None) -> None:
    """只把资产缺失类解析 issue 提升为一次稳定事件。"""
    reason_codes = {
        "table_recovery_asset_missing",
        "table_recovery_bbox_missing",
    }
    for issue in issues:
        if issue.code in reason_codes:
            _log_multimodal_parse_failure(
                phase="local_parse",
                reason_code=issue.code,
                source_alias=source_alias,
                page_number=page_number,
            )

class MultimodalKnowledgeBaseMaintenance:
    """为 CLI 与未来 HTTP adapter 提供单一维护入口。"""

    def __init__(self, *, asset_root: Path | None = None, index_root: Path | None = None, active_config: Path | None = None, table_recovery_provider: TableRecoveryProvider | None = None, embedding_scope: str = "production") -> None:
        """使用隔离默认目录，绝不写入现有 db 或 PubMedQA collection。"""
        base = Path(__file__).resolve().parents[1]
        self.asset_root = asset_root or Path(os.getenv("MULTIMODAL_ASSET_DIR", base / "multimodal_assets"))
        self.index_root = index_root or Path(os.getenv("MULTIMODAL_INDEX_ROOT", base / "multimodal_indexes"))
        self.registry = ActiveIndexRegistry(active_config or Path(os.getenv("MULTIMODAL_ACTIVE_INDEX_CONFIG", base / "multimodal_runtime" / "active_index.json")))
        self.collection_prefix = os.getenv("MULTIMODAL_COLLECTION_PREFIX", "causal_multimodal")
        self.remote_policy = RemoteSamplePolicy()
        self._frozen_production_paths: set[Path] | None = None
        self.table_recovery_provider = table_recovery_provider
        self.embedding_scope = embedding_scope

    def inspect(self, sources: list[str]) -> dict[str, Any]:
        """生成确定性来源 manifest，不调用远程服务且不写 Chroma。"""
        entries, issues = self._scan(sources)
        return {"status": "inspected", "manifest": self._manifest(entries), "configuration": self._configuration_status(), "issues": [issue.model_dump(mode="json") for issue in issues]}

    def prepare_outbound_manifest(
        self,
        sources: list[str],
        output_path: str | Path,
        *,
        max_images: int | None = None,
        max_pages: int | None = None,
        all_production_pages: bool = False,
        checkpoint_dir: str | Path | None = None,
        authorized_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """仅本地解析来源并生成供人工审阅的远程图片清单。"""
        if max_images is not None and max_images < 1:
            raise ValueError("--max-images must be positive when preparing an outbound manifest")
        if max_pages is not None and max_pages < 1:
            raise ValueError("--max-pages must be positive when preparing an outbound manifest")
        if all_production_pages and (max_images is not None or max_pages is not None):
            raise ValueError("full production discovery cannot be combined with page or image limits")
        if all_production_pages and checkpoint_dir is None:
            raise ValueError("full production discovery requires a checkpoint directory")
        entries, issues = self._scan(sources)
        if all_production_pages and not authorized_source_ids:
            raise PermissionError("full production discovery requires explicit source-level authorization")
        authorized = (
            {entry["source_id"] for entry in entries}
            if authorized_source_ids is None and not all_production_pages
            else self._authorized_source_ids(entries, allow_remote_data=True, authorized_source_ids=authorized_source_ids)
        )
        if any(issue.severity is IssueSeverity.ERROR for issue in issues):
            raise ValueError("source scan contains blocking issues")
        production_page_counts = self._validate_full_production_entries(entries) if all_production_pages else {}
        analyzer = VisionAnalyzer(self.asset_root / "vision_cache", allow_remote_data=False, remote_policy_sha256=self.remote_policy.policy_sha256, model=REQUIRED_MODEL)
        records: list[OutboundImageRecord] = []
        prepared_pages = 0
        source_page_counts = {str(Path(entry["path"]).resolve()): self._source_page_count(Path(entry["path"])) for entry in entries}
        if all_production_pages and source_page_counts != production_page_counts:
            raise ValueError("full production discovery page counts do not match the frozen production configuration")
        expected_page_count = sum(source_page_counts.values())
        checkpoint_root = Path(checkpoint_dir) if checkpoint_dir is not None else None
        parser_name = os.getenv("MULTIMODAL_PARSER", "docling")
        for entry in entries:
            path = Path(entry["path"])
            if entry["source_id"] not in authorized:
                continue
            page_count = source_page_counts[str(path.resolve())]
            document_id = entry["document_id"]
            for page_number in range(1, page_count + 1):
                if not all_production_pages and not self._remote_resource_allowed(path, page_number):
                    continue
                if max_pages is not None and prepared_pages >= max_pages:
                    break
                contract = self._r3_discovery_contract(entry, page_count, parser_name)
                checkpoint = self._read_r3_discovery_checkpoint(checkpoint_root, document_id, contract, page_number) if checkpoint_root else None
                if checkpoint is not None:
                    records.extend(OutboundImageRecord.model_validate(record) for record in checkpoint["records"])
                    issues.extend(IngestionIssue.model_validate(issue) for issue in checkpoint["issues"])
                    prepared_pages += 1
                    continue
                parsed = parse_document_page(path, parser_name, page_number)
                prepared_pages += 1
                page_issues = list(parsed.issues)
                issues.extend(page_issues)
                if any(issue.severity is IssueSeverity.ERROR for issue in parsed.issues):
                    raise ValueError(f"local parsing failed before outbound manifest preparation: {path.name} page {page_number}")
                decision = decide_page_route(path, page_number, parsed)
                if decision.route == "blocked":
                    raise ValueError(f"local page quality gate blocked outbound manifest preparation: {path.name} page {page_number}")
                page_items, _ = self._prepare_page_items(self._routed_page_items(parsed.items, decision.route))
                page_records: list[OutboundImageRecord] = []
                for item_index, item in enumerate(page_items, 1):
                    if not item.asset_bytes:
                        continue
                    prepared = analyzer.prepare_image(item.asset_bytes)
                    page_records.append(OutboundImageRecord(
                        source_relative_path=entry["relative_path"], source_sha256=entry["content_hash"], source_id=entry["source_id"],
                        document_id=document_id, page_number=item.page_number or page_number,
                        image_index=max(1, (page_number * 10000 + item_index) % 10000),
                        original_sha256=prepared.original_sha256, normalized_sha256=prepared.normalized_sha256,
                        media_type=prepared.media_type, width=prepared.width, height=prepared.height,
                        original_bytes=prepared.original_bytes, normalized_bytes=len(prepared.payload),
                        transformation=prepared.transformation,
                        context_sha256=sha256_bytes(self._same_page_context(item, page_items).encode("utf-8")),
                        provider="wcode", model=analyzer.model, prompt_version=PROMPT_VERSION, response_adapter_version=analyzer.response_adapter_version,
                        remote_policy_sha256=self.remote_policy.policy_sha256,
                        route=decision.route,
                        quality_gate_version=decision.quality_gate_version,
                        route_reason=decision.reason,
                        quality_summary=decision.input_summary,
                    ))
                    if max_images is not None and len(records) + len(page_records) >= max_images:
                        break
                records.extend(page_records)
                if checkpoint_root is not None:
                    self._write_r3_discovery_checkpoint(checkpoint_root, document_id, contract, page_number, page_records, page_issues)
                if max_images is not None and len(records) >= max_images:
                    break
            if (max_images is not None and len(records) >= max_images) or (max_pages is not None and prepared_pages >= max_pages):
                break
        if all_production_pages and prepared_pages != expected_page_count:
            raise ValueError(f"full production discovery is incomplete: {prepared_pages}/{expected_page_count} pages")
        output = Path(output_path)
        self._write_outbound_manifest(output, records)
        return {
            "status": "prepared",
            "outbound_manifest": str(output),
            "outbound_manifest_sha256": sha256_bytes(output.read_bytes()),
            "outbound_image_count": len(records),
            "prepared_page_count": prepared_pages,
            "expected_page_count": expected_page_count,
            "issues": [issue.model_dump(mode="json") for issue in issues],
        }

    @staticmethod
    def _validate_full_production_entries(entries: list[dict[str, str]]) -> dict[str, int]:
        """全量发现只接受当前哈希已冻结的生产来源。"""
        config = load_production_defaults()
        paths = production_source_paths(config)
        expected = {str(path.resolve()): sha256_bytes(path.read_bytes()) for path in paths}
        actual = {str(Path(entry["path"]).resolve()): entry["content_hash"] for entry in entries}
        if actual != expected:
            raise ValueError("full production discovery requires exactly the frozen production sources")
        page_counts = {str(item["path"].resolve()): int(item["page_count"]) for item in resolve_production_sources(config)}
        if any(count < 1 for count in page_counts.values()):
            raise ValueError("frozen production page counts must be positive")
        return page_counts

    def _r3_discovery_contract(self, entry: dict[str, str], page_count: int, parser_name: str) -> dict[str, Any]:
        """绑定可恢复 R3a 页记录所依赖的全部确定性配置。"""
        parser_version = self._docling_version() if Path(entry["path"]).suffix.lower() == ".pdf" else "builtin"
        return {
            "source_path": str(Path(entry["path"]).resolve()),
            "source_sha256": entry["content_hash"],
            "source_id": entry["source_id"],
            "document_id": entry["document_id"],
            "page_count": page_count,
            "parser": {"name": parser_name, "version": parser_version},
            "quality_gate_version": PAGE_QUALITY_GATE_VERSION,
            "remote_policy_sha256": self.remote_policy.policy_sha256,
            "model": REQUIRED_MODEL,
            "prompt_version": PROMPT_VERSION,
            "response_adapter_version": RESPONSE_ADAPTER_VERSION,
            "table_recovery_adapter_version": TABLE_RECOVERY_ADAPTER_VERSION,
        }

    @staticmethod
    def _r3_discovery_checkpoint_path(checkpoint_root: Path, document_id: str, page_number: int) -> Path:
        """返回隔离的 R3a 页级发现记录路径。"""
        return checkpoint_root / document_id / f"page_{page_number:04d}.json"

    @staticmethod
    def _r3_discovery_uses_sqlite(checkpoint_root: Path) -> bool:
        """以 .sqlite3 后缀选择单文件 R3a checkpoint 格式。"""
        return checkpoint_root.suffix.lower() == ".sqlite3"

    def _open_r3_discovery_database(self, checkpoint_root: Path, *, writable: bool) -> sqlite3.Connection | None:
        """打开 R3a 单文件 checkpoint；旧目录输入保持原 JSON 格式。"""
        if not self._r3_discovery_uses_sqlite(checkpoint_root):
            return None
        if not writable and not checkpoint_root.is_file():
            return None
        if writable:
            checkpoint_root.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(checkpoint_root)
        if writable:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("CREATE TABLE IF NOT EXISTS r3_discovery_checkpoints (document_id TEXT NOT NULL, page_number INTEGER NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY (document_id, page_number))")
        return connection

    @staticmethod
    def _validate_r3_discovery_checkpoint(payload: dict[str, Any], contract: dict[str, Any], page_number: int) -> dict[str, Any] | None:
        """仅接受 schema、页码和冻结契约完全匹配的发现记录。"""
        try:
            if (
                payload.get("schema_version") != R3_DISCOVERY_CHECKPOINT_SCHEMA
                or payload.get("page_number") != page_number
                or payload.get("contract") != contract
                or not isinstance(payload.get("records"), list)
                or not isinstance(payload.get("issues"), list)
            ):
                return None
            [OutboundImageRecord.model_validate(record) for record in payload["records"]]
            [IngestionIssue.model_validate(issue) for issue in payload["issues"]]
            return payload
        except (TypeError, ValueError):
            return None

    def _read_r3_discovery_checkpoint(self, checkpoint_root: Path, document_id: str, contract: dict[str, Any], page_number: int) -> dict[str, Any] | None:
        """读取 SQLite R3a checkpoint，或兼容读取旧单页 JSON。"""
        connection = self._open_r3_discovery_database(checkpoint_root, writable=False)
        if connection is not None:
            try:
                row = connection.execute("SELECT payload_json FROM r3_discovery_checkpoints WHERE document_id = ? AND page_number = ?", (document_id, page_number)).fetchone()
                return self._validate_r3_discovery_checkpoint(json.loads(row[0]), contract, page_number) if row else None
            except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
                return None
            finally:
                connection.close()
        path = self._r3_discovery_checkpoint_path(checkpoint_root, document_id, page_number)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return self._validate_r3_discovery_checkpoint(payload, contract, page_number)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_r3_discovery_checkpoint(
        self,
        checkpoint_root: Path,
        document_id: str,
        contract: dict[str, Any],
        page_number: int,
        records: list[OutboundImageRecord],
        issues: list[IngestionIssue],
    ) -> None:
        """原子保存一页 R3a 发现结果，优先使用单文件 SQLite。"""
        payload = {
            "schema_version": R3_DISCOVERY_CHECKPOINT_SCHEMA,
            "page_number": page_number,
            "contract": contract,
            "records": [record.model_dump(mode="json") for record in records],
            "issues": [issue.model_dump(mode="json") for issue in issues],
        }
        connection = self._open_r3_discovery_database(checkpoint_root, writable=True)
        if connection is not None:
            try:
                with connection:
                    connection.execute("INSERT OR REPLACE INTO r3_discovery_checkpoints (document_id, page_number, payload_json) VALUES (?, ?, ?)", (document_id, page_number, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
            finally:
                connection.close()
            return
        path = self._r3_discovery_checkpoint_path(checkpoint_root, document_id, page_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_with_retry(temporary, path)

    def run_r2_smoke(self, sources: list[str], outbound_manifest: str | Path, output_path: str | Path, *, authorized_source_ids: list[str] | None = None, concurrency_levels: tuple[int, ...] = (4, 8, 16)) -> dict[str, Any]:
        """只对预冻结图片执行远程 smoke，不创建候选、索引或 active pointer 变更。"""
        if not concurrency_levels or any(level < 1 for level in concurrency_levels):
            raise ValueError("R2 concurrency levels must be positive")
        entries, issues = self._scan(sources)
        if not authorized_source_ids:
            raise PermissionError("R2 smoke requires explicit source-level authorization")
        authorized = self._authorized_source_ids(entries, allow_remote_data=True, authorized_source_ids=authorized_source_ids)
        if any(issue.severity is IssueSeverity.ERROR for issue in issues):
            raise ValueError("source scan contains blocking issues")
        records = self._frozen_outbound_records(outbound_manifest, entries)
        if not records:
            raise ValueError("R2 smoke requires a non-empty frozen outbound manifest")
        if any(record.source_id not in authorized for record in records):
            raise PermissionError("R2 smoke manifest contains an unauthorized source")
        output = Path(output_path)
        if output.exists():
            raise ValueError("R2 smoke output already exists")
        configuration = VisionAnalyzer(output.parent / f"{output.stem}_preflight", allow_remote_data=True, remote_policy_sha256=self.remote_policy.policy_sha256, model=REQUIRED_MODEL)
        if not configuration.configured():
            raise RuntimeError("WCode vision configuration is missing, invalid, or not approved")
        targets = self._r2_smoke_targets(entries, records)
        runs: list[dict[str, Any]] = []
        for level in concurrency_levels:
            analyzer = VisionAnalyzer(output.parent / f"{output.stem}_cache_{level}", allow_remote_data=True, max_images=len(targets), remote_policy_sha256=self.remote_policy.policy_sha256, model=REQUIRED_MODEL)
            analyzer.max_concurrency = level
            analyzer._semaphore = threading.BoundedSemaphore(level)
            started = time.monotonic()
            results: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=min(level, len(targets))) as executor:
                futures = {executor.submit(analyzer.analyze, payload, record.media_type, context, outbound_record=record): record for record, payload, context in targets}
                for future in as_completed(futures):
                    record = futures[future]
                    try:
                        analysis = future.result()
                        results.append({"document_id": record.document_id, "page_number": record.page_number, "image_index": record.image_index, "status": "success", "analysis": analysis.model_dump(mode="json")})
                    except Exception as exc:
                        results.append({"document_id": record.document_id, "page_number": record.page_number, "image_index": record.image_index, "status": "failed", "failure_type": type(exc).__name__})
            runs.append({"configured_concurrency": level, "effective_concurrency": min(level, len(targets)), "elapsed_ms": int((time.monotonic() - started) * 1000), "results": sorted(results, key=lambda result: (result["document_id"], result["page_number"], result["image_index"]))})
        failed = sum(result["status"] == "failed" for run in runs for result in run["results"])
        result = {"status": "requires_manual_review" if not failed else "blocked", "approved_image_count": len(targets), "outbound_manifest_sha256": sha256_bytes(Path(outbound_manifest).read_bytes()), "runs": runs, "failed_attempts": failed, "limitations": ["approved image count is below configured concurrency" for level in concurrency_levels if len(targets) < level]}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _r2_smoke_targets(self, entries: list[dict[str, str]], records: list[OutboundImageRecord]) -> list[tuple[OutboundImageRecord, bytes, str]]:
        """重建清单图片并逐字段匹配，拒绝任何动态扩展的外发目标。"""
        wanted = {(record.document_id, record.page_number, record.image_index): record for record in records}
        targets: list[tuple[OutboundImageRecord, bytes, str]] = []
        parser_name = os.getenv("MULTIMODAL_PARSER", "docling")
        for entry in entries:
            path = Path(entry["path"])
            document_id = entry["document_id"]
            pages = sorted(record.page_number for record in records if record.document_id == document_id)
            for page_number in dict.fromkeys(pages):
                parsed = parse_document_page(path, parser_name, page_number)
                if any(issue.severity is IssueSeverity.ERROR or issue.blocking for issue in parsed.issues):
                    raise ValueError(f"R2 local parsing failed: {path.name} page {page_number}")
                decision = decide_page_route(path, page_number, parsed)
                if decision.route == "blocked":
                    raise ValueError(f"R2 page quality gate blocked: {path.name} page {page_number}")
                page_items, _ = self._prepare_page_items(self._routed_page_items(parsed.items, decision.route))
                for item_index, item in enumerate(page_items, 1):
                    record = wanted.get((document_id, page_number, item_index))
                    if record is not None and item.asset_bytes:
                        targets.append((record, item.asset_bytes, self._same_page_context(item, page_items)))
        if {(record.document_id, record.page_number, record.image_index) for record, _, _ in targets} != {(record.document_id, record.page_number, record.image_index) for record in records}:
            raise ValueError("R2 manifest images do not match the current local parser output")
        return targets

    def ingest(self, sources: list[str], *, allow_remote_data: bool = False, authorized_source_ids: list[str] | None = None, max_images: int | None = None, retry_failed: bool = False, retry_generation: int = 0, retry_from_index_version: str | None = None, reuse_local_from_index_version: str | None = None, outbound_manifest: str | Path | None = None, auto_outbound_manifest: bool = False, progress_callback: Callable[[dict[str, Any]], None] | None = None, cancel_check: Callable[[], bool] | None = None, max_pages: int | None = None, page_ranges: dict[str, tuple[int, int]] | None = None, embedding_config: Mapping[str, Any] | EmbeddingConfiguration | None = None) -> dict[str, Any]:
        """逐页解析、原子保存 checkpoint，并分批写入不可变暂存索引。"""
        if retry_from_index_version is not None and reuse_local_from_index_version is not None:
            raise ValueError("--reuse-local-checkpoints-from cannot be combined with --retry-from-index-version")
        if auto_outbound_manifest and (not allow_remote_data or outbound_manifest is not None):
            raise ValueError("auto outbound manifest requires remote data without an external manifest")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be positive")
        if max_pages is not None and page_ranges is not None:
            raise ValueError("max_pages and page_ranges cannot be combined")
        clear_docling_batch_cache()
        embedding_configuration = (
            resolve_production_embedding_configuration()
            if embedding_config is None
            else EmbeddingConfiguration.from_mapping(embedding_config)
        )
        entries, issues = self._scan(sources)
        if allow_remote_data and not authorized_source_ids:
            raise PermissionError("remote ingestion requires explicit source-level authorization")
        authorized = self._authorized_source_ids(entries, allow_remote_data=allow_remote_data, authorized_source_ids=authorized_source_ids)
        normalized_page_ranges = {
            str(Path(path).resolve()): (int(start), int(end))
            for path, (start, end) in (page_ranges or {}).items()
        }
        outbound_records = self._frozen_outbound_records(outbound_manifest, entries) if authorized else []
        if allow_remote_data and max_images is not None and max_images < len(outbound_records):
            raise ValueError("--max-images cannot be smaller than the frozen outbound manifest")
        total_pages = 0
        for entry in entries:
            source_key = str(Path(entry["path"]).resolve())
            start_page, end_page = normalized_page_ranges.get(source_key, (1, self._source_page_count(Path(entry["path"]))))
            total_pages += end_page - start_page + 1
        progress_total_pages = min(total_pages, max_pages) if max_pages is not None else total_pages
        self._check_cancel(cancel_check)
        self._notify_progress(progress_callback, {
            "stage": "scan",
            "completed_pages": 0,
            "total_pages": progress_total_pages,
            "unit_count": 0,
            "message": "知识源扫描完成，开始逐页解析",
        })
        manifest = self._manifest(entries, allow_remote_data=bool(authorized), authorized_source_ids=authorized, max_images=max_images, retry_failed=retry_failed, retry_generation=retry_generation, outbound_records=outbound_records, max_pages=max_pages, auto_outbound_manifest=auto_outbound_manifest, page_ranges=normalized_page_ranges, embedding_config=embedding_configuration)
        version = self._index_version(manifest)
        version_dir = self.index_root / version
        retry_source = self._retry_source_directory(retry_from_index_version, manifest, retry_failed)
        local_reuse_source = self._local_reuse_source_directory(reuse_local_from_index_version, manifest, retry_source)
        if version_dir.exists() and not self._is_resumable_build(version_dir):
            raise ValueError("same source and configuration already has a staged version")
        version_dir.mkdir(parents=True, exist_ok=True)
        self._write_build_state(version_dir, {"status": "building", "unit_count": 0, "attempted_pages": 0})
        embedding = embedding_fingerprint(embedding_configuration)
        store = AssetStore(self.asset_root)
        analyzer = VisionAnalyzer(self.asset_root / "vision_cache", allow_remote_data=allow_remote_data, max_images=max_images, retry_failed=retry_failed, remote_policy_sha256=self.remote_policy.policy_sha256)
        table_recovery_provider = self._resolve_table_recovery_provider(analyzer, allow_remote_data)
        quality_keys = ("eligible_images", "enriched_images", "vision_failed_images", "skipped_images", "low_value_images_skipped", "filtered_short_text_units")
        quality = {key: 0 for key in quality_keys}
        documents: list[dict[str, Any]] = []
        outbound_manifest_path = version_dir / "outbound_manifest.json"
        if auto_outbound_manifest and outbound_manifest_path.exists():
            outbound_records = self._read_outbound_manifest(outbound_manifest_path)
        self._write_outbound_manifest(outbound_manifest_path, outbound_records)
        parser_name = os.getenv("MULTIMODAL_PARSER", "docling")
        unit_count = 0
        attempted_pages = 0
        try:
            for entry in entries:
                path = Path(entry["path"])
                document_id = entry["document_id"]
                source_authorized = entry["source_id"] in authorized
                source_asset_uri = store.put(document_id, path.name, path.read_bytes(), category="source")
                parser_artifacts: list[dict[str, str]] = []
                document_units = 0
                document_page_count = 0
                document_page_numbers: list[int] = []
                expected_pages = self._source_page_count(path)
                parsed_name = parser_name
                parsed_version = "unknown"
                selected_range = normalized_page_ranges.get(str(path.resolve()), (1, expected_pages))
                if page_ranges is not None and str(path.resolve()) not in normalized_page_ranges:
                    raise ValueError(f"missing page range for source: {entry['relative_path']}")
                for page_number in range(selected_range[0], selected_range[1] + 1):
                    if max_pages is not None and attempted_pages >= max_pages:
                        break
                    self._check_cancel(cancel_check)
                    checkpoint = self._read_page_checkpoint(version_dir, document_id, page_number)
                    if checkpoint is None and retry_source is not None:
                        checkpoint = self._reusable_retry_checkpoint(retry_source, document_id, page_number)
                        if checkpoint is not None:
                            self._write_page_checkpoint(
                                version_dir,
                                document_id,
                                page_number,
                                checkpoint,
                                self._read_page_units(retry_source, document_id, page_number),
                            )
                    if checkpoint is None:
                        local_checkpoint = self._reusable_local_parse_checkpoint(version_dir, document_id, page_number, entry, expected_pages, store)
                        if local_checkpoint is None and local_reuse_source is not None:
                            local_checkpoint = self._reusable_local_parse_checkpoint(local_reuse_source, document_id, page_number, entry, expected_pages, store)
                            if local_checkpoint is not None:
                                self._write_local_parse_checkpoint(version_dir, document_id, page_number, local_checkpoint)
                        if local_checkpoint is not None:
                            parsed = self._parsed_document_from_local_checkpoint(local_checkpoint, store)
                        else:
                            parse_kwargs = {"cancel_check": cancel_check} if cancel_check is not None else {}
                            parsed = parse_document_page(path, parser_name, page_number, **parse_kwargs)
                            local_checkpoint = self._build_local_parse_checkpoint(path, document_id, page_number, parsed, entry, expected_pages, store)
                            self._write_local_parse_checkpoint(version_dir, document_id, page_number, local_checkpoint)
                        page_quality = {key: 0 for key in quality_keys}
                        page_issues = list(parsed.issues)
                        _log_page_parse_issues(page_issues, page_number, source_alias=document_id)
                        page_artifacts = [
                            {"name": name, "asset_uri": store.put(document_id, name, payload, category="parsed"), "content_hash": sha256_bytes(payload)}
                            for name, payload in parsed.raw_artifacts
                        ]
                        decision = decide_page_route(path, page_number, parsed)
                        if decision.route == "blocked":
                            page_issues.append(IngestionIssue(
                                code="page_quality_gate_failed",
                                message=f"第 {page_number} 页无法安全路由：{decision.reason}",
                                severity=IssueSeverity.ERROR,
                                blocking=True,
                                source_path=str(path),
                            ))
                        page_items, filtered = self._prepare_page_items(self._routed_page_items(parsed.items, decision.route))
                        if auto_outbound_manifest:
                            self._ensure_auto_outbound_records(
                                outbound_records,
                                page_items,
                                document_id,
                                entry["source_id"],
                                entry["relative_path"],
                                entry["content_hash"],
                                analyzer,
                                route=decision.route,
                                route_reason=decision.reason,
                                quality_gate_version=decision.quality_gate_version,
                                quality_summary=decision.input_summary,
                            )
                        page_quality["filtered_short_text_units"] = filtered
                        page_units: list[KnowledgeUnit] = []
                        prefetched_vision = self._prefetch_remote_analyses(
                            page_items,
                            path,
                            document_id,
                            analyzer,
                            allow_remote_data,
                            outbound_records,
                            source_authorized=source_authorized,
                        )
                        for item_index, item in enumerate(page_items, 1):
                            unit = self._build_unit(
                                item, path, document_id, page_number * 10000 + item_index,
                                parsed.parser_name, parsed.parser_version, embedding, store, analyzer,
                                page_quality, page_issues, allow_remote_data,
                                source_authorized=source_authorized,
                                context=self._same_page_context(item, page_items),
                                source_relative_path=entry["relative_path"],
                                source_sha256=entry["content_hash"],
                                outbound_records=outbound_records,
                                table_recovery_provider=table_recovery_provider,
                                prefetched_vision=prefetched_vision.get(item_index, _MISSING_VISION_RESULT),
                            )
                            if unit is not None:
                                page_units.append(unit)
                        checkpoint = {"document_id": document_id, "page_number": page_number, "parser_name": parsed.parser_name, "parser_version": parsed.parser_version, "parser_artifacts": page_artifacts, "route": decision.route, "quality_gate_version": decision.quality_gate_version, "route_reason": decision.reason, "quality_input_summary": decision.input_summary, "issues": [issue.model_dump(mode="json") for issue in page_issues], "quality": page_quality, "unit_count": len(page_units)}
                        self._write_page_checkpoint(version_dir, document_id, page_number, checkpoint, page_units)
                    attempted_pages += 1
                    parsed_name, parsed_version = checkpoint["parser_name"], checkpoint["parser_version"]
                    parser_artifacts.extend(checkpoint["parser_artifacts"])
                    page_issues = [IngestionIssue.model_validate(issue) for issue in checkpoint["issues"]]
                    issues.extend(page_issues)
                    for key in quality_keys:
                        quality[key] += int(checkpoint["quality"].get(key, 0))
                    page_units = int(checkpoint["unit_count"])
                    unit_count += page_units
                    document_units += page_units
                    document_page_count += 1
                    document_page_numbers.append(page_number)
                    self._write_build_state(version_dir, {"status": "building", "unit_count": unit_count, "attempted_pages": attempted_pages})
                    if auto_outbound_manifest:
                        self._write_outbound_manifest(outbound_manifest_path, outbound_records)
                    self._notify_progress(progress_callback, {
                        "stage": "parse_embed",
                        "completed_pages": attempted_pages,
                        "total_pages": progress_total_pages,
                        "unit_count": unit_count,
                        "message": f"已完成第 {attempted_pages}/{progress_total_pages} 页解析",
                    })
                    self._check_cancel(cancel_check)
                page_routes = []
                for page_number in document_page_numbers:
                    page_checkpoint = self._read_page_checkpoint(version_dir, document_id, page_number) or {}
                    page_routes.append({key: page_checkpoint.get(key) for key in ("page_number", "route", "quality_gate_version", "route_reason", "quality_input_summary")})
                documents.append({"source_id": entry["source_id"], "document_id": document_id, "relative_path": entry["relative_path"], "content_hash": entry["content_hash"], "source_asset_uri": source_asset_uri, "parser_artifact_uris": [artifact["asset_uri"] for artifact in parser_artifacts], "parser_artifacts": parser_artifacts, "parser_name": parsed_name, "parser_version": parsed_version, "source_page_count": expected_pages, "page_start": selected_range[0], "page_end": selected_range[1], "page_numbers": document_page_numbers, "expected_page_count": document_page_count, "attempted_page_count": document_page_count, "unit_count": document_units, "page_routes": page_routes})
                if max_pages is not None and attempted_pages >= max_pages:
                    break
            self._check_cancel(cancel_check)
            self._materialize_units(version_dir, documents)
            (version_dir / "issues.jsonl").write_text("".join(issue.model_dump_json() + "\n" for issue in issues), encoding="utf-8")
            self._write_outbound_manifest(outbound_manifest_path, outbound_records)
            partial_ingestion = page_ranges is not None or (max_pages is not None and max_pages < total_pages)
            final_build_state = {"status": "staged_complete", "unit_count": unit_count, "vector_count": unit_count, "attempted_pages": attempted_pages}
            manifest.update({"index_version": version, "embedding": embedding, "embedding_config": embedding_configuration.to_manifest(), "unit_count": unit_count, "issues_count": len(issues), "quality_policy": self._quality_policy(), "quality_observations": quality, "documents": documents, "source_page_limit": max_pages, "partial_ingestion": partial_ingestion, "outbound_manifest_sha256": sha256_bytes(outbound_manifest_path.read_bytes()), "outbound_image_count": len(outbound_records), "counts": {"document_count": len(documents), "page_count": sum(int(document.get("attempted_page_count") or 0) for document in documents), "unit_count": unit_count, "vector_count": unit_count, "issues_count": len(issues), "partial": partial_ingestion}})
            for artifact in manifest.get("artifacts", []):
                artifact_path = version_dir / str(artifact.get("path") or "")
                if artifact.get("path") in {"units.jsonl", "issues.jsonl"} and artifact_path.is_file():
                    artifact.update({"size_bytes": artifact_path.stat().st_size, "sha256": sha256_bytes(artifact_path.read_bytes())})
                elif artifact.get("path") == "build_state.json":
                    serialized_state = json.dumps(final_build_state, ensure_ascii=False, indent=2).encode("utf-8")
                    artifact.update({"size_bytes": len(serialized_state), "sha256": sha256_bytes(serialized_state)})
            manifest = seal_manifest(manifest)
            final_version = str(manifest["release_id"])
            # 在向量构建完成前保留 provisional 目录，失败时可从页 checkpoint 续跑。
            manifest["index_version"] = version
            (version_dir / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
            self._notify_progress(progress_callback, {
                "stage": "chroma",
                "completed_pages": attempted_pages,
                "total_pages": progress_total_pages,
                "unit_count": unit_count,
                "message": "解析完成，开始写入 Chroma",
            })
            self._check_cancel(cancel_check)
            vector_count = self._build_staged_vectors(version_dir, final_version, unit_count, embedding_config=embedding_configuration)
            if vector_count != unit_count:
                raise ValueError("staged vector count does not match unit count")
            self._check_cancel(cancel_check)
            chroma_sha256, chroma_size_bytes = directory_sha256(version_dir / "chroma")
            manifest["artifact_integrity"] = {
                "chroma": {
                    "sha256": chroma_sha256,
                    "size_bytes": chroma_size_bytes,
                },
            }
            manifest = seal_manifest(manifest)
            if manifest["release_id"] != final_version:
                raise ValueError("post-build artifact integrity changed release identity")
            self._write_build_state(version_dir, final_build_state)
            if final_version != version:
                final_dir = self.index_root / final_version
                if final_dir.exists():
                    raise ValueError("sealed release identity already exists as another staged version")
                replace_with_retry(version_dir, final_dir)
                version = final_version
                version_dir = final_dir
            manifest["index_version"] = version
            (version_dir / "manifest.json").write_bytes(manifest_bytes(manifest))
            self._notify_progress(progress_callback, {
                "stage": "staged",
                "completed_pages": attempted_pages,
                "total_pages": progress_total_pages,
                "unit_count": unit_count,
                "vector_count": vector_count,
                "message": "Chroma 写入完成，staged index 已就绪",
            })
            return {"status": "staged", "index_version": version, "unit_count": unit_count, "vector_count": vector_count, "issues": [issue.model_dump(mode="json") for issue in issues]}
        except Exception as exc:
            (version_dir / "issues.jsonl").write_text("".join(issue.model_dump_json() + "\n" for issue in issues), encoding="utf-8")
            self._write_build_state(version_dir, {"status": "failed", "unit_count": unit_count, "attempted_pages": attempted_pages, "error_type": type(exc).__name__})
            raise
        finally:
            clear_docling_batch_cache()

    @staticmethod
    def _notify_progress(callback: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
        """发送可选摄取进度；观察者异常不能破坏知识库构建。"""
        if callback is None:
            return
        try:
            callback(dict(event))
        except Exception:
            return

    @staticmethod
    def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
        """在页级和索引阶段边界执行协作式取消检查。"""
        if cancel_check and cancel_check():
            raise InterruptedError("multimodal ingestion was cancelled")

    def run(self, sources: list[str], *, allow_remote_data: bool = False, authorized_source_ids: list[str] | None = None, max_images: int | None = None, retry_failed: bool = False, retry_generation: int = 0, retry_from_index_version: str | None = None, reuse_local_from_index_version: str | None = None, outbound_manifest: str | Path | None = None, timeout_seconds: int | None = None, cancel_check: Callable[[], bool] | None = None, publish_on_pass: bool = False, embedding_config: Mapping[str, Any] | EmbeddingConfiguration | None = None) -> dict[str, Any]:
        """以版本锁执行 ingest 和评测；仅在显式授权时发布。"""
        started = time.monotonic()
        entries, _ = self._scan(sources)
        if allow_remote_data and not authorized_source_ids:
            raise PermissionError("remote ingestion requires explicit source-level authorization")
        authorized = self._authorized_source_ids(entries, allow_remote_data=allow_remote_data, authorized_source_ids=authorized_source_ids)
        outbound_records = self._frozen_outbound_records(outbound_manifest, entries) if authorized else []
        if allow_remote_data and max_images is not None and max_images < len(outbound_records):
            raise ValueError("--max-images cannot be smaller than the frozen outbound manifest")
        embedding_configuration = (
            resolve_production_embedding_configuration()
            if embedding_config is None
            else EmbeddingConfiguration.from_mapping(embedding_config)
        )
        manifest = self._manifest(entries, allow_remote_data=bool(authorized), authorized_source_ids=authorized, max_images=max_images, retry_failed=retry_failed, retry_generation=retry_generation, outbound_records=outbound_records, embedding_config=embedding_configuration)
        version = self._index_version(manifest)
        lock = self.index_root / ".locks" / version
        try:
            lock.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return {"status": "already_running", "index_version": version, "published": False}
        try:
            self._check_run_control(started, timeout_seconds, cancel_check)
            version_exists = (self.index_root / version).is_dir()
            reused = self._is_reusable_staged_version(version)
            resumable = self._is_resumable_build(self.index_root / version)
            if version_exists and not reused and not resumable:
                return {"status": "incomplete_staged", "index_version": version, "published": False}
            staged = {"status": "reused_staged", "index_version": version} if reused else self.ingest(sources, allow_remote_data=allow_remote_data, authorized_source_ids=list(authorized), max_images=max_images, retry_failed=retry_failed, retry_generation=retry_generation, retry_from_index_version=retry_from_index_version, reuse_local_from_index_version=reuse_local_from_index_version, outbound_manifest=outbound_manifest, embedding_config=embedding_configuration)
            version = str(staged.get("index_version") or version)
            self._check_run_control(started, timeout_seconds, cancel_check)
            evaluation = self.evaluate(version)
            self._check_run_control(started, timeout_seconds, cancel_check)
            if not evaluation["passed"]:
                return {"status": "gate_failed", "index_version": version, "staged": staged, "evaluation": evaluation, "published": False}
            if not publish_on_pass:
                return {"status": "ready_to_publish", "index_version": version, "staged": staged, "evaluation": evaluation, "published": False}
            published = self.publish(version)
            return {"status": "published", "index_version": version, "staged": staged, "evaluation": evaluation, "publish": published, "published": True}
        except TimeoutError as exc:
            return {"status": "timed_out", "index_version": version, "published": False, "error": str(exc)}
        except InterruptedError as exc:
            return {"status": "cancelled", "index_version": version, "published": False, "error": str(exc)}
        finally:
            shutil.rmtree(lock, ignore_errors=True)

    def _check_run_control(self, started: float, timeout_seconds: int | None, cancel_check: Callable[[], bool] | None) -> None:
        """只在阶段边界检查取消和总超时，确保失败时不切换 active pointer。"""
        if cancel_check and cancel_check():
            raise InterruptedError("multimodal maintenance run was cancelled")
        if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
            raise TimeoutError("multimodal maintenance run timed out")

    def evaluate(self, index_version: str, *, require_assets: bool = True) -> dict[str, Any]:
        """执行完整性门禁；正式候选复核可不依赖外置解析资产。"""
        directory = self._version_dir(index_version)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        units = [KnowledgeUnit.model_validate_json(line) for line in (directory / "units.jsonl").read_text(encoding="utf-8").splitlines() if line]
        issues = [IngestionIssue.model_validate_json(line) for line in (directory / "issues.jsonl").read_text(encoding="utf-8").splitlines() if line]
        collection = f"{self.collection_prefix}_{index_version}"
        failures: list[str] = []
        if manifest.get("identity_sha256") or manifest.get("schema_version") in (MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_NAME):
            try:
                validate_manifest_contract(manifest)
                validate_manifest_artifacts(manifest, directory)
            except (OSError, ValueError):
                failures.append("artifact_integrity_invalid")
        if manifest.get("partial_ingestion"):
            failures.append("partial_ingestion")
        if not units: failures.append("no_valid_units")
        vector_count = StagedIndex(directory, collection).count()
        if vector_count != len(units): failures.append("vector_count_mismatch")
        if manifest.get("identity_sha256") or manifest.get("schema_version") in (MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_NAME):
            try:
                validate_manifest_counts(
                    manifest,
                    actual_counts={
                        "unit_count": len(units),
                        "vector_count": vector_count,
                        "issues_count": len(issues),
                    },
                )
            except (TypeError, ValueError):
                failures.append("manifest_counts_mismatch")
        gate_issues = [issue for issue in issues if issue.code not in _NON_BLOCKING_ENRICHMENT_ISSUES]
        if any(issue.severity is IssueSeverity.ERROR for issue in gate_issues): failures.append("required_source_failed")
        if any(issue.blocking for issue in gate_issues): failures.append("blocking_issue")
        store = AssetStore(self.asset_root)
        if require_assets and any(unit.asset_uri and not store.exists(unit.asset_uri) for unit in units): failures.append("missing_asset")
        failures.extend(self._audit_manifest_chain(manifest, units, store, require_assets=require_assets))
        if manifest.get("schema_version", 0) >= 4:
            failures.extend(self._audit_outbound_manifest(directory, manifest))
        if any(not unit.retrieval_text.strip() for unit in units): failures.append("empty_retrieval_text")
        if "embedding_config" in manifest:
            try:
                configured_embedding = EmbeddingConfiguration.from_manifest(manifest["embedding_config"])
                if manifest.get("embedding") != configured_embedding.fingerprint():
                    failures.append("embedding_fingerprint_mismatch")
            except (TypeError, ValueError):
                failures.append("embedding_config_invalid")
        elif manifest.get("embedding") != embedding_fingerprint():
            failures.append("embedding_fingerprint_mismatch")
        if "build_configuration" not in manifest:
            failures.append("legacy_manifest_missing_build_configuration")
        if manifest.get("schema_version", 0) >= 3:
            if not self._is_reusable_staged_version(index_version):
                failures.append("staged_build_incomplete")
            documents = manifest.get("documents", [])
            if any(document.get("expected_page_count") != document.get("attempted_page_count") for document in documents):
                failures.append("source_page_count_mismatch")
            if require_assets and any(
                document.get("parser_name") == "docling"
                and len(document.get("parser_artifacts", [])) != document.get("expected_page_count")
                for document in documents
            ):
                failures.append("parser_page_artifact_count_mismatch")
        quality = self._quality_evaluation(manifest, directory)
        if not quality["passed"]:
            failures.append("quality_gate_failed")
        production_evaluation = None
        from .production import evaluate_staged_index, is_production_manifest
        if is_production_manifest(manifest):
            from .production import validate_production_manifest
            failures.extend(validate_production_manifest(manifest))
            if not failures:
                production_evaluation = evaluate_staged_index(
                    directory,
                    collection,
                    embedding_config=manifest.get("embedding_config"),
                    embedding_scope=self.embedding_scope,
                )
                if not production_evaluation["gate"]["passed"]:
                    failures.append("production_retrieval_gate_failed")
        result = {"index_version": index_version, "passed": not failures, "failures": failures, "manifest_sha256": compute_manifest_sha256(directory / "manifest.json"), "quality": quality, "production_evaluation": production_evaluation, "evaluated_at": datetime.now(timezone.utc).isoformat()}
        (directory / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def publish(self, index_version: str, *, expected_active_index_version: str | None = None, expected_generation: int | None = None) -> dict[str, Any]:
        """重新执行全部门禁后，才原子切换正式 active pointer。"""
        directory = self._version_dir(index_version)
        evaluation_path = directory / "evaluation.json"
        if not evaluation_path.exists(): raise ValueError("index must be evaluated before publish")
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        if not evaluation.get("passed"): raise ValueError("blocking evaluation failures prevent publication")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        manifest_sha256 = compute_manifest_sha256(directory / "manifest.json")
        from .production import is_production_manifest, validate_production_manifest
        if not is_production_manifest(manifest):
            raise ValueError("non-production manifest cannot be published")
        try:
            resolve_production_sources()
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ValueError("current controlled production sources are not valid") from exc
        fresh_evaluation = self.evaluate(index_version, require_assets=False)
        if not fresh_evaluation.get("passed"):
            raise ValueError("blocking evaluation failures prevent publication")
        if fresh_evaluation.get("manifest_sha256") != manifest_sha256:
            raise ValueError("manifest changed during publication revalidation")
        if manifest.get("schema_version", 0) >= 3 and not self._is_reusable_staged_version(index_version):
            raise ValueError("staged build is incomplete and cannot be published")
        policy_failures = validate_production_manifest(manifest)
        if policy_failures:
            raise ValueError(f"production strategy mismatch prevents publication: {', '.join(policy_failures)}")
        production_evaluation = directory / "production_evaluation.json"
        if not production_evaluation.exists() or not json.loads(production_evaluation.read_text(encoding="utf-8")).get("gate", {}).get("passed"):
            raise ValueError("production retrieval evaluation must pass before publication")
        if "build_configuration" not in manifest or "quality_policy" not in manifest or "quality_observations" not in manifest:
            raise ValueError("legacy manifest is not eligible for publication under P0 gates")
        current = self.registry.read_release_pointer()
        current_version = str((current.get("active") or {}).get("release_id") or (current.get("active") or {}).get("index_version") or "")
        if expected_active_index_version not in {None, "", current_version}:
            raise ValueError("active pointer changed during publication")
        published = ReleaseManager(self.index_root, self.registry.path).publish(
            directory,
            expected_generation=expected_generation,
            expected_active_release_id=expected_active_index_version,
            allow_legacy=True,
        )
        return {**published, "status": "published", "index_version": index_version}

    def rollback(self, index_version: str, *, expected_active_index_version: str | None = None, expected_generation: int | None = None) -> dict[str, Any]:
        """只允许回滚至当前 pointer 的唯一 fallback。"""
        result = ReleaseManager(self.index_root, self.registry.path).rollback(
            index_version or None,
            expected_generation=expected_generation,
            expected_active_release_id=expected_active_index_version,
            enforce_production_policy=True,
        )
        return {**result, "status": "rolled_back", "index_version": index_version}

    def promote_staged(
        self,
        *,
        source_index_root: Path,
        source_asset_root: Path,
        index_version: str,
    ) -> dict[str, Any]:
        """只物化索引到正式候选目录，解析资产参数保留兼容但不复制。"""
        if not isinstance(index_version, str) or not index_version.startswith("mm_") or "/" in index_version or "\\" in index_version:
            raise ValueError("invalid multimodal index version")
        source_root = Path(source_index_root).resolve()
        source_dir = (source_root / index_version).resolve()
        source_dir.relative_to(source_root)
        if not source_dir.is_dir() or not (source_dir / "manifest.json").is_file():
            raise FileNotFoundError("staged index is unavailable")
        target_root = self.index_root.resolve()
        target_root.mkdir(parents=True, exist_ok=True)
        target_dir = (target_root / index_version).resolve()
        target_dir.relative_to(target_root)
        source_manifest_sha = compute_manifest_sha256(source_dir / "manifest.json")
        if target_dir.exists():
            target_manifest = target_dir / "manifest.json"
            if target_manifest.is_file() and compute_manifest_sha256(target_manifest) == source_manifest_sha:
                return {"status": "reused", "index_version": index_version, "manifest_sha256": source_manifest_sha}
            raise ValueError("formal candidate version already exists with a different manifest")

        temporary = target_root / f".{index_version}.promote-{uuid.uuid4().hex}"
        try:
            from .release import ReleaseManager

            ReleaseManager._copy_formal_artifacts(source_dir, temporary)
            temporary.replace(target_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return {"status": "promoted", "index_version": index_version, "manifest_sha256": source_manifest_sha}

    def publish_staged(
        self,
        *,
        source_index_root: Path,
        index_version: str,
        expected_active_index_version: str | None = None,
        expected_manifest_sha256: str | None = None,
        expected_generation: int | None = None,
        expected_evaluation_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """从隔离 candidate 直接执行物化与单 pointer 事务。"""
        if not isinstance(index_version, str) or not index_version.startswith("mm_") or "/" in index_version or "\\" in index_version:
            raise ValueError("invalid multimodal index version")
        source_root = Path(source_index_root).resolve()
        source_dir = (source_root / index_version).resolve()
        source_dir.relative_to(source_root)
        if not source_dir.is_dir():
            raise FileNotFoundError("staged index is unavailable")
        manifest_path = source_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("staged release manifest is unavailable")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest_contract(manifest)
        validate_manifest_artifacts(manifest, source_dir)
        if expected_evaluation_binding is not None and manifest.get("evaluation_binding") != dict(expected_evaluation_binding):
            raise ValueError("evaluation binding changed before publication")
        evaluation_path = source_dir / "evaluation.json"
        if not evaluation_path.is_file() or not json.loads(evaluation_path.read_text(encoding="utf-8")).get("passed"):
            raise ValueError("blocking evaluation failures prevent publication")
        from .production import is_production_manifest, validate_production_manifest

        if not is_production_manifest(manifest):
            raise ValueError("non-production manifest cannot be published")
        policy_failures = validate_production_manifest(manifest)
        if policy_failures:
            raise ValueError(f"production strategy mismatch prevents publication: {', '.join(policy_failures)}")
        production_evaluation = source_dir / "production_evaluation.json"
        if not production_evaluation.is_file() or not json.loads(production_evaluation.read_text(encoding="utf-8")).get("gate", {}).get("passed"):
            raise ValueError("production retrieval evaluation must pass before publication")
        result = ReleaseManager(self.index_root, self.registry.path).publish(
            source_dir,
            expected_generation=expected_generation,
            expected_active_release_id=expected_active_index_version,
            expected_manifest_sha256=expected_manifest_sha256,
            allow_legacy=True,
        )
        return {**result, "index_version": index_version}

    def status(self, index_version: str | None = None) -> dict[str, Any]:
        """返回 active pointer 或一个版本的可审计状态。"""
        if index_version is None:
            snapshot = self.registry.retention_snapshot(self.index_root)
            return {
                "active": snapshot["active"],
                "previous": snapshot["previous"],
                "fallback": snapshot.get("fallback"),
                "generation": snapshot.get("generation", 0),
                "candidates": snapshot["candidates"],
                "candidate_overflow": snapshot["candidate_overflow"],
                "retention": snapshot,
            }
        directory = self._version_dir(index_version)
        return {"manifest": json.loads((directory / "manifest.json").read_text(encoding="utf-8")), "evaluation": json.loads((directory / "evaluation.json").read_text(encoding="utf-8")) if (directory / "evaluation.json").exists() else None}

    def _scan(self, sources: list[str]) -> tuple[list[dict[str, str]], list[IngestionIssue]]:
        """扫描文件并绑定 canonical 来源身份；正式身份只来自受控目录解析结果。"""
        entries: list[dict[str, str]] = []; issues: list[IngestionIssue] = []
        try:
            formal_sources = {str(item["path"].resolve()): item for item in resolve_production_sources()}
        except (FileNotFoundError, ValueError, OSError):
            config = load_production_defaults()
            controlled = [(Path(__file__).resolve().parents[3] / Path(raw)).resolve() for raw in config["controlled_source_directories"]]
            requested = [Path(raw).resolve() for raw in sources]
            if any(root == path or root in path.parents for root in controlled for path in requested):
                raise
            formal_sources = {}
        for raw in sources:
            path = Path(raw)
            paths = sorted(item for item in path.rglob("*") if item.is_file()) if path.is_dir() else [path]
            for item in paths:
                issue = inspect_source(item)
                if issue: issues.append(issue); continue
                relative_path = (Path(path.name) / item.relative_to(path)).as_posix() if path.is_dir() else item.name
                resolved = formal_sources.get(str(item.resolve()))
                content_hash = sha256_bytes(item.read_bytes())
                catalog_source_id = content_source_id(
                    content_hash,
                    uploaded=item.name.startswith("upload_") and "__" in item.name,
                )
                entries.append({
                    "path": str(item.resolve()),
                    "relative_path": relative_path,
                    "controlled_path": resolved["relative_path"] if resolved else "",
                    "content_hash": content_hash,
                    "source_id": resolved["source_id"] if resolved else catalog_source_id,
                    "catalog_source_id": catalog_source_id,
                    "canonical_source_id": resolved["source_id"] if resolved else "",
                    "document_id": resolved["document_id"] if resolved else content_document_id(content_hash),
                    "formal": "true" if resolved else "false",
                })
        return sorted(entries, key=lambda item: (item["relative_path"].casefold(), item["content_hash"])), issues

    @staticmethod
    def _docling_configuration() -> dict[str, Any]:
        """返回 Docling 页级超时、批量进程和图像输出的不可变配置。"""
        return dict(docling_configuration())

    def _authorized_source_ids(self, entries: list[dict[str, str]], *, allow_remote_data: bool, authorized_source_ids: list[str] | None) -> set[str]:
        """把显式运行授权收缩为来源级集合；正式来源不接受隐式全选。"""
        if not allow_remote_data:
            return set()
        if authorized_source_ids is None:
            return set()
        authorized = set(authorized_source_ids)
        matched: set[str] = set()
        for entry in entries:
            if authorized.intersection({entry["source_id"], entry.get("catalog_source_id", "")}):
                matched.add(entry["source_id"])
        if len(matched) != len(authorized):
            raise ValueError("authorized source ids must match the current sources")
        return matched

    def _manifest(self, entries: list[dict[str, str]], *, allow_remote_data: bool = False, authorized_source_ids: set[str] | None = None, max_images: int | None = None, retry_failed: bool = False, retry_generation: int = 0, outbound_records: list[OutboundImageRecord] | None = None, max_pages: int | None = None, auto_outbound_manifest: bool = False, page_ranges: dict[str, tuple[int, int]] | None = None, embedding_config: Mapping[str, Any] | EmbeddingConfiguration | None = None) -> dict[str, Any]:
        """构造不包含宿主绝对路径的来源清单。"""
        if retry_generation < 0 or retry_generation > MAX_RETRY_GENERATION:
            raise ValueError(f"retry generation must be between 0 and {MAX_RETRY_GENERATION}")
        for entry in entries:
            entry.setdefault("source_id", content_source_id(entry["content_hash"]))
            entry.setdefault("document_id", content_document_id(entry["content_hash"]))
        public = [{"source_id": entry["source_id"], "document_id": entry["document_id"], "relative_path": entry["relative_path"], "controlled_path": entry.get("controlled_path", ""), "content_hash": entry["content_hash"]} for entry in entries]
        source_page_ranges = [
            {"relative_path": entry["relative_path"], "start_page": page_ranges[str(Path(entry["path"]).resolve())][0], "end_page": page_ranges[str(Path(entry["path"]).resolve())][1]}
            for entry in entries
            if page_ranges and str(Path(entry["path"]).resolve()) in page_ranges
        ]
        resolved_embedding = (
            resolve_production_embedding_configuration()
            if embedding_config is None
            else EmbeddingConfiguration.from_mapping(embedding_config)
        )
        return {
            "schema_version": 6,
            "sources": public,
            "source_page_limit": max_pages,
            "source_page_ranges": source_page_ranges,
            "parser": os.getenv("MULTIMODAL_PARSER", "docling"),
            "parser_identity": {
                "name": os.getenv("MULTIMODAL_PARSER", "docling"),
                "version": "runtime-resolved",
            },
            "chunking": {
                "schema": "retrieval_text-v1",
                "splitter_version": "builtin",
                **dict(TEXT_SPLIT),
            },
            "multimodal_policy": {
                "remote_policy_sha256": self.remote_policy.policy_sha256,
                "vision": {
                    "model": os.getenv("VISION_MODEL", REQUIRED_MODEL),
                    "prompt_version": PROMPT_VERSION,
                    "response_adapter_version": RESPONSE_ADAPTER_VERSION,
                },
                "table_recovery": self._table_recovery_configuration(),
            },
            "retrieval_index": {
                "vector_backend": "chroma",
                "collection_template": f"{self.collection_prefix}_<release_id>",
                "dimension": resolved_embedding.dimension,
                "distance_metric": resolved_embedding.distance_metric,
                "sparse_backend": "bm25s",
                "unit_schema": "knowledge-unit-v1",
            },
            "artifacts": [
                {"path": "units.jsonl", "type": "units", "required": True},
                {"path": "issues.jsonl", "type": "issues", "required": True},
                {"path": "build_state.json", "type": "build_state", "required": True},
                {"path": "chroma", "type": "directory", "required": True},
            ],
            "build_configuration": {
                "ingestion_schema": "streaming-page-v1",
                "embedding": embedding_fingerprint(resolved_embedding),
                "embedding_config": resolved_embedding.to_manifest(),
                "pdf_parser": self._docling_configuration(),
                "text_split": dict(TEXT_SPLIT),
                "page_quality_gate": {"version": PAGE_QUALITY_GATE_VERSION, "min_text_coverage": PAGE_QUALITY_MIN_TEXT_COVERAGE, "native_text_min_chars": 80},
                "remote_policy_hash": self.remote_policy.policy_sha256,
                "table_recovery": self._table_recovery_configuration(),
                "vision": {"enabled": allow_remote_data, "authorized_source_ids": sorted(authorized_source_ids or set()), "local_ocr_enabled": False, "model": os.getenv("VISION_MODEL", REQUIRED_MODEL), "prompt_version": PROMPT_VERSION, "response_adapter_version": RESPONSE_ADAPTER_VERSION, "max_images": max_images, "max_retries": int(os.getenv("VISION_MAX_RETRIES", "2")), "max_pixels": int(os.getenv("VISION_MAX_PIXELS", str(16000000))), "max_image_bytes": int(os.getenv("VISION_MAX_IMAGE_BYTES", str(10 * 1024 * 1024))), "retry_failed": retry_failed, "retry_generation": retry_generation, "outbound_manifest_mode": "auto_generated_isolated" if auto_outbound_manifest else "external_or_empty"},
            },
            "outbound_manifest_sha256": sha256_bytes(self._outbound_manifest_payload(outbound_records or [])),
            "outbound_image_count": len(outbound_records or []),
            "embedding_config": resolved_embedding.to_manifest(),
        }

    def _is_reusable_staged_version(self, index_version: str) -> bool:
        """仅接受带完整状态、必要产物且单位/向量计数一致的暂存版本。"""
        directory = self.index_root / index_version
        required = ("manifest.json", "units.jsonl", "issues.jsonl", "build_state.json")
        if not directory.is_dir() or any(not (directory / name).is_file() for name in required):
            return False
        try:
            state = json.loads((directory / "build_state.json").read_text(encoding="utf-8"))
            unit_count = int(state.get("unit_count", -1))
            vector_count = int(state.get("vector_count", -1))
            persisted_units = sum(1 for line in (directory / "units.jsonl").read_text(encoding="utf-8").splitlines() if line)
            if state.get("status") != "staged_complete" or unit_count < 0 or unit_count != vector_count or persisted_units != unit_count:
                return False
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("identity_sha256") or manifest.get("schema_version") in (MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_NAME):
                validate_manifest_contract(manifest)
                validate_manifest_artifacts(manifest, directory)
                validate_manifest_counts(
                    manifest,
                    actual_counts={
                        "unit_count": unit_count,
                        "vector_count": vector_count,
                        "issues_count": sum(
                            1
                            for line in (directory / "issues.jsonl").read_text(encoding="utf-8").splitlines()
                            if line.strip()
                        ),
                    },
                )
            if vector_count == 0:
                return True
            if not (directory / "chroma").is_dir():
                return False
            return StagedIndex(directory, f"{self.collection_prefix}_{index_version}").count() == vector_count
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _write_build_state(self, directory: Path, state: dict[str, Any]) -> None:
        """原子写入当前构建阶段，供崩溃诊断和复用判定使用。"""
        temporary = directory / "build_state.tmp"
        temporary.write_bytes(json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))
        replace_with_retry(temporary, directory / "build_state.json")

    def _is_resumable_build(self, directory: Path) -> bool:
        """判断目录是否为本 schema 可安全续跑的未完成构建。"""
        state_path = directory / "build_state.json"
        if not state_path.is_file():
            return False
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            return state.get("status") in {"building", "failed"} and self._checkpoint_storage_exists(directory)
        except (OSError, json.JSONDecodeError):
            return False

    def _page_checkpoint_paths(self, directory: Path, document_id: str, page_number: int) -> tuple[Path, Path]:
        """返回仅供读取历史候选的页级 JSON/JSONL 路径。"""
        page_root = directory / "page_checkpoints" / document_id
        return page_root / f"page_{page_number:04d}.json", page_root / f"page_{page_number:04d}.units.jsonl"

    @staticmethod
    def _checkpoint_database_path(directory: Path) -> Path:
        """返回候选版本唯一的 SQLite checkpoint 数据库路径。"""
        return directory / CHECKPOINT_DATABASE_NAME

    def _checkpoint_storage_exists(self, directory: Path) -> bool:
        """识别新 SQLite 格式或旧页级文件格式的可恢复 checkpoint。"""
        return self._checkpoint_database_path(directory).is_file() or (directory / "page_checkpoints").is_dir()

    def _open_checkpoint_database(self, directory: Path, *, writable: bool) -> sqlite3.Connection | None:
        """打开新 checkpoint 数据库；只读路径绝不为历史候选创建空文件。"""
        path = self._checkpoint_database_path(directory)
        if not writable and not path.is_file():
            return None
        if writable:
            directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        if writable:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("CREATE TABLE IF NOT EXISTS page_checkpoints (document_id TEXT NOT NULL, page_number INTEGER NOT NULL, checkpoint_json TEXT NOT NULL, unit_count INTEGER NOT NULL, PRIMARY KEY (document_id, page_number))")
            connection.execute("CREATE TABLE IF NOT EXISTS page_units (document_id TEXT NOT NULL, page_number INTEGER NOT NULL, unit_index INTEGER NOT NULL, unit_json TEXT NOT NULL, PRIMARY KEY (document_id, page_number, unit_index))")
            connection.execute("CREATE TABLE IF NOT EXISTS local_parse_checkpoints (document_id TEXT NOT NULL, page_number INTEGER NOT NULL, checkpoint_json TEXT NOT NULL, PRIMARY KEY (document_id, page_number))")
        return connection

    def _read_page_checkpoint(self, directory: Path, document_id: str, page_number: int) -> dict[str, Any] | None:
        """读取 SQLite checkpoint，或兼容读取旧 JSON/JSONL checkpoint。"""
        connection = self._open_checkpoint_database(directory, writable=False)
        if connection is not None:
            try:
                row = connection.execute("SELECT checkpoint_json, unit_count FROM page_checkpoints WHERE document_id = ? AND page_number = ?", (document_id, page_number)).fetchone()
                if row is not None:
                    unit_count = connection.execute("SELECT COUNT(*) FROM page_units WHERE document_id = ? AND page_number = ?", (document_id, page_number)).fetchone()[0]
                    checkpoint = json.loads(row[0])
                    return checkpoint if int(row[1]) == unit_count == checkpoint.get("unit_count") else None
            except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
                return None
            finally:
                connection.close()
        metadata_path, units_path = self._page_checkpoint_paths(directory, document_id, page_number)
        if not metadata_path.is_file() or not units_path.is_file():
            return None
        try:
            checkpoint = json.loads(metadata_path.read_text(encoding="utf-8"))
            unit_count = sum(1 for line in units_path.read_text(encoding="utf-8").splitlines() if line)
            return checkpoint if checkpoint.get("unit_count") == unit_count else None
        except (OSError, json.JSONDecodeError):
            return None

    def _read_page_units(self, directory: Path, document_id: str, page_number: int) -> list[KnowledgeUnit]:
        """读取 SQLite 页单元，或兼容读取旧 JSONL 页单元。"""
        connection = self._open_checkpoint_database(directory, writable=False)
        if connection is not None:
            try:
                rows = connection.execute("SELECT unit_json FROM page_units WHERE document_id = ? AND page_number = ? ORDER BY unit_index", (document_id, page_number)).fetchall()
                page_exists = connection.execute("SELECT 1 FROM page_checkpoints WHERE document_id = ? AND page_number = ?", (document_id, page_number)).fetchone()
                if page_exists is not None:
                    return [KnowledgeUnit.model_validate_json(row[0]) for row in rows]
            except (sqlite3.Error, TypeError, ValueError):
                return []
            finally:
                connection.close()
        _, units_path = self._page_checkpoint_paths(directory, document_id, page_number)
        return [KnowledgeUnit.model_validate_json(line) for line in units_path.read_text(encoding="utf-8").splitlines() if line]

    def _reusable_retry_checkpoint(self, directory: Path, document_id: str, page_number: int) -> dict[str, Any] | None:
        """Reuse only a source page that has no blocking parsing issue."""
        checkpoint = self._read_page_checkpoint(directory, document_id, page_number)
        if checkpoint is None:
            return None
        issues = [IngestionIssue.model_validate(issue) for issue in checkpoint.get("issues", [])]
        return None if any(issue.severity is IssueSeverity.ERROR for issue in issues) else checkpoint

    def _local_parse_checkpoint_path(self, directory: Path, document_id: str, page_number: int) -> Path:
        """返回仅供读取历史候选的本地解析 JSON 路径。"""
        return directory / "local_parse_checkpoints" / document_id / f"page_{page_number:04d}.json"

    def _read_local_parse_checkpoint(self, directory: Path, document_id: str, page_number: int) -> dict[str, Any] | None:
        """读取 SQLite 本地解析 checkpoint，或兼容读取历史 JSON。"""
        connection = self._open_checkpoint_database(directory, writable=False)
        if connection is not None:
            try:
                row = connection.execute("SELECT checkpoint_json FROM local_parse_checkpoints WHERE document_id = ? AND page_number = ?", (document_id, page_number)).fetchone()
                if row is not None:
                    return json.loads(row[0])
            except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
                return None
            finally:
                connection.close()
        path = self._local_parse_checkpoint_path(directory, document_id, page_number)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_local_parse_checkpoint(self, directory: Path, document_id: str, page_number: int, checkpoint: dict[str, Any]) -> None:
        """以单事务写入本地解析 checkpoint，绑定 source/Docling/OCR 契约。"""
        connection = self._open_checkpoint_database(directory, writable=True)
        assert connection is not None
        try:
            with connection:
                connection.execute("INSERT OR REPLACE INTO local_parse_checkpoints (document_id, page_number, checkpoint_json) VALUES (?, ?, ?)", (document_id, page_number, json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":"))))
        finally:
            connection.close()

    def _build_local_parse_checkpoint(self, path: Path, document_id: str, page_number: int, parsed: ParsedDocument, entry: dict[str, str], page_count: int, store: AssetStore) -> dict[str, Any]:
        """把 ParsedDocument 序列化为不含 asset_bytes 的可复用本地解析结果。"""
        serializable_items: list[dict[str, Any]] = []
        for item in parsed.items:
            asset_uri = None
            asset_content_hash = None
            if item.asset_bytes:
                asset_uri = store.put(document_id, item.asset_name or f"asset_{page_number}", item.asset_bytes)
                asset_content_hash = sha256_bytes(item.asset_bytes)
            serializable_items.append({"modality": item.modality, "content_kind": item.content_kind, "raw_text": item.raw_text, "page_number": item.page_number, "bbox": item.bbox, "asset_uri": asset_uri, "asset_content_hash": asset_content_hash, "asset_name": item.asset_name, "parent_key": item.parent_key})
        return {
            "schema_version": LOCAL_PARSE_CHECKPOINT_SCHEMA,
            "document_id": document_id,
            "page_number": page_number,
            "parser_name": parsed.parser_name,
            "parser_version": parsed.parser_version,
            "parser_artifacts": [{"name": name, "asset_uri": store.put(document_id, name, payload, category="parsed"), "content_hash": sha256_bytes(payload)} for name, payload in parsed.raw_artifacts],
            "issues": [issue.model_dump(mode="json") for issue in parsed.issues],
            "items": serializable_items,
            "contract": {
                "source_content_hash": entry["content_hash"],
                "page_count": page_count,
                "parser": {"name": parsed.parser_name, "version": parsed.parser_version},
                "docling_config": self._docling_configuration(),
                "image_text_strategy": "remote-vision-v2",
                "table_recovery_adapter_version": TABLE_RECOVERY_ADAPTER_VERSION,
            },
        }

    def _parsed_document_from_local_checkpoint(self, checkpoint: dict[str, Any], store: AssetStore) -> ParsedDocument:
        """从已校验的本地解析 checkpoint 重建 ParsedDocument。"""
        items: list[ParsedItem] = []
        for item_dict in checkpoint.get("items", []):
            asset_uri = item_dict.get("asset_uri")
            asset_bytes = store.read(asset_uri) if asset_uri else None
            items.append(ParsedItem(modality=item_dict["modality"], content_kind=item_dict["content_kind"], raw_text=item_dict.get("raw_text", ""), page_number=item_dict.get("page_number"), bbox=item_dict.get("bbox"), asset_bytes=asset_bytes, asset_name=item_dict.get("asset_name"), parent_key=item_dict.get("parent_key")))
        raw_artifacts: list[tuple[str, bytes]] = []
        for artifact in checkpoint.get("parser_artifacts", []):
            asset_uri = artifact.get("asset_uri")
            if asset_uri:
                raw_artifacts.append((artifact["name"], store.read(asset_uri)))
        issues = [IngestionIssue.model_validate(issue) for issue in checkpoint.get("issues", [])]
        return ParsedDocument(parser_name=checkpoint.get("parser_name", "docling"), parser_version=checkpoint.get("parser_version", "unknown"), items=tuple(items), issues=tuple(issues), raw_artifacts=tuple(raw_artifacts))

    def _reusable_local_parse_checkpoint(self, directory: Path, document_id: str, page_number: int, entry: dict[str, str], page_count: int, store: AssetStore) -> dict[str, Any] | None:
        """校验契约后返回可复用的本地解析 checkpoint；不一致则返回 None 触发重解析。"""
        checkpoint = self._read_local_parse_checkpoint(directory, document_id, page_number)
        if checkpoint is None:
            return None
        if checkpoint.get("schema_version") != LOCAL_PARSE_CHECKPOINT_SCHEMA:
            return None
        if checkpoint.get("document_id") != document_id or checkpoint.get("page_number") != page_number:
            return None
        contract = checkpoint.get("contract", {})
        if contract.get("source_content_hash") != entry["content_hash"]:
            return None
        if contract.get("page_count") != page_count:
            return None
        expected_docling = self._docling_configuration()
        if contract.get("docling_config") != expected_docling:
            return None
        if contract.get("image_text_strategy") != "remote-vision-v2":
            return None
        if contract.get("table_recovery_adapter_version") != TABLE_RECOVERY_ADAPTER_VERSION:
            return None
        if contract.get("parser") != {"name": checkpoint.get("parser_name"), "version": checkpoint.get("parser_version")}:
            return None
        if Path(entry["relative_path"]).suffix.lower() == ".pdf" and contract.get("parser", {}).get("version") != self._docling_version():
            return None
        try:
            for item in checkpoint.get("items", []):
                if not isinstance(item, dict) or not isinstance(item.get("modality"), str) or not isinstance(item.get("content_kind"), str):
                    return None
                asset_uri, asset_hash = item.get("asset_uri"), item.get("asset_content_hash")
                if bool(asset_uri) != bool(asset_hash) or (asset_uri and not self._asset_matches(store, asset_uri, asset_hash)):
                    return None
            for artifact in checkpoint.get("parser_artifacts", []):
                if not isinstance(artifact, dict) or not isinstance(artifact.get("name"), str) or not self._asset_matches(store, artifact.get("asset_uri"), artifact.get("content_hash")):
                    return None
            [IngestionIssue.model_validate(issue) for issue in checkpoint.get("issues", [])]
        except (OSError, TypeError, ValueError):
            return None
        return checkpoint

    @staticmethod
    def _asset_matches(store: AssetStore, asset_uri: object, content_hash: object) -> bool:
        """确认 checkpoint 引用的资源存在且仍是记录时的不可变内容。"""
        return isinstance(asset_uri, str) and isinstance(content_hash, str) and sha256_bytes(store.read(asset_uri)) == content_hash

    @staticmethod
    def _docling_version() -> str:
        """读取当前 Docling 包版本，供 PDF 本地解析 checkpoint 复用校验。"""
        try:
            return importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def _local_reuse_source_directory(self, index_version: str | None, manifest: dict[str, Any], retry_source: Path | None) -> Path | None:
        """校验并返回跨版本本地解析复用源；与 --retry-from-index-version 互斥。"""
        if index_version is None:
            return None
        if retry_source is not None:
            raise ValueError("--reuse-local-checkpoints-from cannot be combined with --retry-from-index-version")
        directory = self._version_dir(index_version)
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file() or not self._checkpoint_storage_exists(directory):
            return None
        try:
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if self._local_parse_contract(source_manifest) != self._local_parse_contract(manifest):
            return None
        return directory

    @staticmethod
    def _local_parse_contract(manifest: dict[str, Any]) -> dict[str, Any]:
        """返回只绑定本地解析（不含视觉策略）的版本契约。"""
        configuration = json.loads(json.dumps(manifest.get("build_configuration", {})))
        configuration.pop("vision", None)
        return {
            "schema_version": manifest.get("schema_version"),
            "sources": manifest.get("sources"),
            "parser": manifest.get("parser"),
            "build_configuration": configuration,
        }

    def _retry_source_directory(self, index_version: str | None, manifest: dict[str, Any], retry_failed: bool) -> Path | None:
        """Validate an immutable checkpoint source before cross-version reuse."""
        if index_version is None:
            return None
        if not retry_failed:
            raise ValueError("retry source requires --retry-failed")
        directory = self.index_root / index_version
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file() or not self._checkpoint_storage_exists(directory):
            raise ValueError("retry source has no page checkpoints")
        try:
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("retry source manifest is unreadable") from exc
        if self._retry_contract(source_manifest) != self._retry_contract(manifest):
            raise ValueError("retry source contract does not match this build")
        return directory

    @staticmethod
    def _retry_contract(manifest: dict[str, Any]) -> dict[str, Any]:
        """Return the content-affecting build contract, excluding retry controls."""
        configuration = json.loads(json.dumps(manifest.get("build_configuration", {})))
        vision = configuration.get("vision")
        if isinstance(vision, dict):
            vision.pop("retry_failed", None)
            vision.pop("retry_generation", None)
            vision.pop("outbound_manifest_mode", None)
        return {
            "schema_version": manifest.get("schema_version"),
            "sources": manifest.get("sources"),
            "parser": manifest.get("parser"),
            "build_configuration": configuration,
        }

    def _write_page_checkpoint(self, directory: Path, document_id: str, page_number: int, checkpoint: dict[str, Any], units: list[KnowledgeUnit]) -> None:
        """在一个 SQLite 事务中写入页元数据和单元，禁止半页 checkpoint 复用。"""
        connection = self._open_checkpoint_database(directory, writable=True)
        assert connection is not None
        try:
            with connection:
                connection.execute("DELETE FROM page_units WHERE document_id = ? AND page_number = ?", (document_id, page_number))
                connection.execute("DELETE FROM page_checkpoints WHERE document_id = ? AND page_number = ?", (document_id, page_number))
                connection.executemany("INSERT INTO page_units (document_id, page_number, unit_index, unit_json) VALUES (?, ?, ?, ?)", [(document_id, page_number, index, unit.model_dump_json()) for index, unit in enumerate(units)])
                connection.execute("INSERT INTO page_checkpoints (document_id, page_number, checkpoint_json, unit_count) VALUES (?, ?, ?, ?)", (document_id, page_number, json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":")), len(units)))
        finally:
            connection.close()

    def _materialize_units(self, directory: Path, documents: list[dict[str, Any]]) -> None:
        """按文档和页码顺序流式汇总页级单元，生成最终 units.jsonl。"""
        temporary = directory / "units.tmp"
        with temporary.open("w", encoding="utf-8") as output:
            for document in documents:
                page_numbers = document.get("page_numbers") or range(1, int(document["expected_page_count"]) + 1)
                for page_number in page_numbers:
                    for unit in self._read_page_units(directory, document["document_id"], page_number):
                        output.write(unit.model_dump_json() + "\n")
        replace_with_retry(temporary, directory / "units.jsonl")

    def _build_staged_vectors(
        self,
        directory: Path,
        version: str,
        unit_count: int,
        *,
        embedding_config: Mapping[str, Any] | EmbeddingConfiguration | None = None,
    ) -> int:
        """在独立 attempt 目录构建 Chroma，成功后再提交为正式目录。"""
        final_path = directory / "chroma"
        if not unit_count:
            final_path.mkdir(parents=True, exist_ok=True)
            return 0
        attempt_number = 1
        while (directory / f"chroma_attempt_{attempt_number}").exists():
            attempt_number += 1
        attempt_name = f"chroma_attempt_{attempt_number}"
        count = StagedIndex(
            directory,
            f"{self.collection_prefix}_{version}",
            directory_name=attempt_name,
            embedding_config=embedding_config,
            embedding_scope=self.embedding_scope,
        ).write(self._read_units(directory))
        attempt_path = directory / attempt_name
        if attempt_path.exists():
            if final_path.exists():
                raise ValueError("completed chroma directory already exists")
            replace_with_retry(attempt_path, final_path)
        elif count == unit_count:
            # 测试替身可能只返回计数；正式 evaluate 仍会校验 collection 计数。
            final_path.mkdir(parents=True, exist_ok=True)
        return count

    def _source_page_count(self, path: Path) -> int:
        """返回来源的物理页数；非 PDF 来源按一个逻辑页计。"""
        if path.suffix.lower() != ".pdf":
            return 1
        from pypdf import PdfReader

        return len(PdfReader(path).pages)

    def _read_units(self, directory: Path):
        """从 JSONL 流式回读标准化单元，避免构建向量时全量驻留内存。"""
        with (directory / "units.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield KnowledgeUnit.model_validate_json(line)

    def _prepare_page_items(self, items: tuple[ParsedItem, ...]) -> tuple[list[ParsedItem], int]:
        """把短标题并入同页后续正文，避免产生孤立标题向量。"""
        prepared: list[ParsedItem] = []
        pending: list[str] = []
        pending_item: ParsedItem | None = None
        filtered = 0
        for item in items:
            short_text = item.modality == "text" and 0 < len(item.raw_text.strip()) < 30
            heading = item.content_kind in {"title", "section_header", "page_header", "page_footer"}
            if short_text or heading:
                pending.append(item.raw_text.strip())
                pending_item = item
                filtered += 1
                continue
            if pending and item.raw_text:
                item = replace(item, raw_text="\n".join(pending + [item.raw_text]))
                pending.clear()
                pending_item = None
            prepared.append(item)
        if pending and pending_item is not None:
            prepared.append(replace(pending_item, content_kind="paragraph", raw_text="\n".join(pending)))
        return prepared, filtered

    @staticmethod
    def _routed_page_items(items: tuple[ParsedItem, ...], route: str) -> tuple[ParsedItem, ...]:
        """按已冻结页路由保留唯一候选集合，避免整页与逐图重复外发。"""
        if route == "remote_page_fallback":
            return tuple(item for item in items if item.content_kind == "page_render" and item.asset_bytes)
        if route == "remote_pictures":
            return tuple(item for item in items if item.content_kind != "page_render")
        if route == "table_recovery":
            return tuple(item for item in items if item.content_kind != "page_render")
        if route == "local_objects":
            return tuple(item for item in items if not item.asset_bytes)
        return ()

    def _ensure_auto_outbound_records(
        self,
        records: list[OutboundImageRecord],
        page_items: list[ParsedItem],
        document_id: str,
        source_id: str,
        source_relative_path: str,
        source_sha256: str,
        analyzer: VisionAnalyzer,
        *,
        route: str,
        route_reason: str,
        quality_gate_version: str,
        quality_summary: dict[str, int],
    ) -> None:
        """为当前运行来源的当前页生成远程调用绑定，避免重复 Docling 预扫描。"""
        known = {(record.document_id, record.page_number, record.image_index) for record in records}
        for item_index, item in enumerate(page_items, 1):
            if not item.asset_bytes:
                continue
            page_number = item.page_number or 1
            image_index = max(1, (page_number * 10000 + item_index) % 10000)
            identity = (document_id, page_number, image_index)
            if identity in known:
                continue
            prepared = analyzer.prepare_image(item.asset_bytes)
            context = self._same_page_context(item, page_items)
            records.append(OutboundImageRecord(
                source_relative_path=source_relative_path,
                source_sha256=source_sha256,
                source_id=source_id,
                document_id=document_id,
                page_number=page_number,
                image_index=image_index,
                original_sha256=prepared.original_sha256,
                normalized_sha256=prepared.normalized_sha256,
                media_type=prepared.media_type,
                width=prepared.width,
                height=prepared.height,
                original_bytes=prepared.original_bytes,
                normalized_bytes=len(prepared.payload),
                transformation=prepared.transformation,
                context_sha256=sha256_bytes(context.encode("utf-8")),
                provider="wcode",
                model=analyzer.model,
                prompt_version=PROMPT_VERSION,
                response_adapter_version=analyzer.response_adapter_version,
                remote_policy_sha256=self.remote_policy.policy_sha256,
                route=route,
                quality_gate_version=quality_gate_version,
                route_reason=route_reason,
                quality_summary=quality_summary,
            ))
            known.add(identity)

    def _prefetch_remote_analyses(
        self,
        page_items: list[ParsedItem],
        path: Path,
        document_id: str,
        analyzer: VisionAnalyzer,
        allow_remote_data: bool,
        records: list[OutboundImageRecord],
        *,
        source_authorized: bool = True,
    ) -> dict[int, VisionAnalysis | Exception]:
        """并发执行当前页图片 VLM 调用，返回按页内 item 序号绑定的结果。"""
        if not allow_remote_data or not source_authorized:
            return {}
        candidates: dict[int, tuple[ParsedItem, OutboundImageRecord, str]] = {}
        for item_index, item in enumerate(page_items, 1):
            if not item.asset_bytes or item.content_kind == "table_recovery":
                continue
            page_number = item.page_number or 1
            image_index = max(1, (page_number * 10000 + item_index) % 10000)
            record = next((candidate for candidate in records if (candidate.document_id, candidate.page_number, candidate.image_index) == (document_id, page_number, image_index)), None)
            if record is None:
                continue
            candidates[item_index] = (item, record, self._same_page_context(item, page_items))
        if not candidates:
            return {}
        max_workers = min(max(1, int(getattr(analyzer, "max_concurrency", 1))), len(candidates))
        results: dict[int, VisionAnalysis | Exception] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(analyzer.analyze, item.asset_bytes, record.media_type, context, outbound_record=record): item_index
                for item_index, (item, record, context) in candidates.items()
            }
            for future in as_completed(futures):
                item_index = futures[future]
                try:
                    results[item_index] = future.result()
                except Exception as exc:
                    results[item_index] = exc
        return results

    def _build_unit(
        self,
        item: ParsedItem,
        path: Path,
        document_id: str,
        position: int,
        parser_name: str,
        parser_version: str,
        embedding: dict[str, Any],
        store: AssetStore,
        analyzer: VisionAnalyzer,
        quality: dict[str, int],
        issues: list[IngestionIssue],
        allow_remote_data: bool,
        *,
        source_authorized: bool = True,
        context: str = "",
        source_relative_path: str | None = None,
        source_sha256: str | None = None,
        outbound_records: list[OutboundImageRecord] | None = None,
        table_recovery_provider: TableRecoveryProvider | None = None,
        prefetched_vision: VisionAnalysis | Exception | object = _MISSING_VISION_RESULT,
    ) -> KnowledgeUnit | None:
        """把解析项转为单索引单元；获准图片必须完整通过远程 OCR+VLM。"""
        analysis = None
        table_recovery_result: TableRecoveryResult | None = None
        outbound_record = None
        asset_uri = None
        if item.asset_bytes:
            asset_uri = store.put(document_id, item.asset_name or f"asset_{position}", item.asset_bytes)
            identity = (document_id, item.page_number or 1, max(1, position % 10000))
            record = next((candidate for candidate in outbound_records or [] if (candidate.document_id, candidate.page_number, candidate.image_index) == identity), None)
            manifest_authorized = record is not None
            if item.content_kind == "table_recovery":
                provider = table_recovery_provider
                if provider is None and allow_remote_data and source_authorized:
                    provider = RemoteVlmTableRecoveryProvider(analyzer)
                requires_manifest = bool(getattr(provider, "requires_outbound_manifest", False))
                if provider is None or (requires_manifest and not allow_remote_data) or (requires_manifest and not source_authorized):
                    quality["vision_failed_images"] += 1
                    issues.append(IngestionIssue(code="table_recovery_unavailable", message="空表没有可用的表格恢复 provider", severity=IssueSeverity.ERROR, blocking=True, source_path=str(path)))
                    return None
                if requires_manifest and not manifest_authorized:
                    quality["skipped_images"] += 1
                    issues.append(IngestionIssue(code="remote_source_not_allowed", message="表格恢复图片不在批准的远程外发清单中", severity=IssueSeverity.ERROR, blocking=True, source_path=str(path)))
                    return None
                if requires_manifest:
                    quality["eligible_images"] += 1
                try:
                    if requires_manifest:
                        if not source_relative_path or not source_sha256 or outbound_records is None or record is None:
                            raise PermissionError("table recovery image is absent from the frozen outbound manifest")
                        if record.source_relative_path != source_relative_path or record.source_sha256 != source_sha256:
                            raise PermissionError("outbound manifest source does not match the current source")
                    table_recovery_result = provider.recover(item.asset_bytes, record.media_type if record else "image/png", context, outbound_record=record)
                    if table_recovery_result.status != "success" or not looks_like_markdown_table(table_recovery_result.table_markdown.strip()):
                        raise ValueError("table_recovery_provider_failed")
                    analysis = table_recovery_result.as_vision_analysis()
                    outbound_record = record
                except Exception as exc:
                    quality["vision_failed_images"] += 1
                    _log_multimodal_parse_failure(
                        phase="table_recovery",
                        reason_code="table_recovery_failed",
                        source_alias=document_id,
                        page_number=item.page_number,
                        sequence_index=max(1, position % 10000),
                        sequence_kind="table",
                        exc=exc,
                    )
                    issues.append(IngestionIssue(code="table_recovery_failed", message=f"表格恢复失败：{type(exc).__name__}", severity=IssueSeverity.ERROR, blocking=True, source_path=str(path)))
                    return None
            elif allow_remote_data and source_authorized and manifest_authorized:
                quality["eligible_images"] += 1
                try:
                    if not source_relative_path or not source_sha256 or outbound_records is None:
                        raise ValueError("outbound manifest metadata is incomplete")
                    if record is None:
                        raise PermissionError("image is absent from the frozen outbound manifest")
                    if record.source_relative_path != source_relative_path or record.source_sha256 != source_sha256:
                        raise PermissionError("outbound manifest source does not match the current source")
                    if isinstance(prefetched_vision, Exception):
                        raise prefetched_vision
                    if prefetched_vision is _MISSING_VISION_RESULT:
                        analysis = analyzer.analyze(item.asset_bytes, record.media_type, context, outbound_record=record)
                    else:
                        analysis = prefetched_vision
                    outbound_record = record
                except Exception as exc:
                    quality["vision_failed_images"] += 1
                    _log_multimodal_parse_failure(
                        phase="remote_image",
                        reason_code="remote_image_failed",
                        source_alias=document_id,
                        page_number=item.page_number,
                        sequence_index=max(1, position % 10000),
                        sequence_kind="image",
                        exc=exc,
                    )
                    issues.append(IngestionIssue(code="remote_image_failed", message=f"远程图片分析失败：{type(exc).__name__}", severity=IssueSeverity.ERROR, blocking=True, source_path=str(path)))
                    return None
            elif allow_remote_data:
                quality["skipped_images"] += 1
                issues.append(IngestionIssue(code="remote_source_not_allowed", message="该图片不在批准的远程外发清单中，禁止生成候选图片单元", severity=IssueSeverity.ERROR, blocking=True, source_path=str(path)))
                return None
            else:
                quality["skipped_images"] += 1
            if analysis:
                quality["enriched_images"] += 1
                has_semantics = any((analysis.summary.strip(), analysis.visible_facts, analysis.entities, analysis.table_markdown.strip(), analysis.formula_latex.strip(), analysis.directed_relations, analysis.uncertain_relations))
                if not analysis.ocr_text.strip() and not has_semantics:
                    quality["low_value_images_skipped"] += 1
                    issues.append(IngestionIssue(code="image_low_information", message=f"第 {item.page_number or 0} 页远程 OCR 与视觉语义均为空", severity=IssueSeverity.WARNING, blocking=False, source_path=str(path)))
                    return None
        if item.modality == "image" and not analysis and not item.raw_text.strip():
            quality["low_value_images_skipped"] += 1
            issues.append(IngestionIssue(code="image_text_missing", message=f"第 {item.page_number or 0} 页图片尚未完成远程 OCR 与视觉分析", severity=IssueSeverity.WARNING, blocking=False, source_path=str(path)))
            return None
        content_kind = analysis.content_kind if analysis else item.content_kind
        text = render_retrieval_text(content_kind=content_kind, title=path.name, analysis=analysis, raw_text=item.raw_text)
        if not text.strip():
            return None
        raw_text = "\n".join(dict.fromkeys(part for part in (item.raw_text.strip(), analysis.ocr_text.strip() if analysis else "") if part))
        if item.modality == "image" and item.asset_bytes:
            content_hash = sha256_bytes(item.asset_bytes)
        else:
            content_hash = sha256_bytes(item.raw_text.encode("utf-8") if item.raw_text else item.asset_bytes or b"")
        policy_fingerprint = getattr(analyzer, "remote_policy_sha256", "")
        if not isinstance(policy_fingerprint, str):
            policy_fingerprint = ""
        if table_recovery_result is not None and table_recovery_result.provider != "remote_vlm":
            policy_fingerprint = ""
        response_adapter_version = getattr(analyzer, "response_adapter_version", "")
        vision_model = getattr(analyzer, "model", "") if analysis else ""
        vision_prompt_version = PROMPT_VERSION if analysis else ""
        if table_recovery_result is not None:
            vision_model = table_recovery_result.model
            vision_prompt_version = table_recovery_result.prompt_version
            response_adapter_version = table_recovery_result.response_adapter_version or TABLE_RECOVERY_ADAPTER_VERSION
        if analysis and (not isinstance(response_adapter_version, str) or not response_adapter_version):
            raise ValueError("vision response adapter version must be a non-empty string")
        unit_id = stable_id("unit", {"document_id": document_id, "position": position, "modality": item.modality, "content_hash": content_hash, "parser": [parser_name, parser_version], "vision": [vision_model, vision_prompt_version if analysis else PROMPT_VERSION, response_adapter_version if analysis else "", policy_fingerprint, sha256_bytes(context.encode("utf-8")) if analysis else "", outbound_record.normalized_sha256 if outbound_record else "", outbound_record.transformation if outbound_record else ""]})
        return KnowledgeUnit(unit_id=unit_id, document_id=document_id, parent_id=item.parent_key, modality=item.modality, content_kind=content_kind, page_number=item.page_number, bbox=item.bbox, raw_text=raw_text, retrieval_text=text, asset_uri=asset_uri, content_hash=content_hash, parser_name=parser_name, parser_version=parser_version, vision_model=vision_model, vision_prompt_version=vision_prompt_version, embedding_provider=embedding["provider"], embedding_model=embedding["model"], status=UnitStatus.ENRICHED if analysis else UnitStatus.COMPLETED)

    @staticmethod
    def _same_page_context(image: ParsedItem, items: list[ParsedItem]) -> str:
        """只拼接同一物理页的 caption、正文、表格和公式，最多 2000 字。"""
        parts = [image.raw_text.strip()] if image.raw_text.strip() else []
        parts.extend(
            item.raw_text.strip()
            for item in items
            if item is not image and item.page_number == image.page_number and item.modality in {"text", "table", "equation"} and item.raw_text.strip()
        )
        return "\n\n".join(dict.fromkeys(parts))[:2000]

    @staticmethod
    def _outbound_manifest_payload(records: list[OutboundImageRecord]) -> bytes:
        """把已验证记录稳定序列化为候选内冻结清单。"""
        return canonical_json({"schema_version": "outbound-v1", "images": [record.model_dump(mode="json") for record in records]}).encode("utf-8")

    def _frozen_outbound_records(self, outbound_manifest: str | Path | None, entries: list[dict[str, str]]) -> list[OutboundImageRecord]:
        """读取并验证外部预冻结清单，拒绝在远程运行中动态扩展授权范围。"""
        if outbound_manifest is None:
            return []
        path = Path(outbound_manifest)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = [OutboundImageRecord.model_validate(record) for record in payload.get("images", [])]
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("outbound manifest is unreadable or invalid") from exc
        if payload.get("schema_version") != "outbound-v1":
            raise ValueError("outbound manifest schema is unsupported")
        for entry in entries:
            entry.setdefault("source_id", content_source_id(entry["content_hash"]))
        sources = {
            (entry["source_id"], entry["relative_path"], entry["content_hash"])
            for entry in entries
        } | {
            ("", entry["relative_path"], entry["content_hash"])
            for entry in entries
        }
        identities = {(record.document_id, record.page_number, record.image_index) for record in records}
        if len(identities) != len(records):
            raise ValueError("outbound manifest contains duplicate image identities")
        if any(
            (record.source_id, record.source_relative_path, record.source_sha256) not in sources
            and ("", record.source_relative_path, record.source_sha256) not in sources
            and (None, record.source_relative_path, record.source_sha256) not in sources
            for record in records
        ):
            raise ValueError("outbound manifest does not match the current frozen sources")
        if any(record.remote_policy_sha256 != self.remote_policy.policy_sha256 for record in records):
            raise ValueError("outbound manifest does not match the current remote policy")
        if any(record.response_adapter_version != RESPONSE_ADAPTER_VERSION for record in records):
            raise ValueError("outbound manifest does not match the current response adapter")
        return records

    @staticmethod
    def _write_outbound_manifest(path: Path, records: list[OutboundImageRecord]) -> None:
        """把已批准记录原子复制到候选目录，不保存图片、上下文或密钥。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = MultimodalKnowledgeBaseMaintenance._outbound_manifest_payload(records).decode("utf-8")
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        replace_with_retry(temporary, path)

    @staticmethod
    def _read_outbound_manifest(path: Path) -> list[OutboundImageRecord]:
        """恢复已原子写入的 outbound 记录，供同一不可变候选断点续跑。"""
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "outbound-v1" or not isinstance(payload.get("images"), list):
            raise ValueError("invalid outbound manifest")
        return [OutboundImageRecord.model_validate(record) for record in payload["images"]]

    def _index_version(self, manifest: dict[str, Any]) -> str:
        """从完整配置和来源生成不含时钟的可重现版本名。"""
        return release_id_for_manifest(manifest)

    def _quality_policy(self) -> dict[str, Any]:
        """读取显式质量阈值；默认零阈值只记录质量而不把 smoke 当生产标准。"""
        return {
            "min_eligible_images": int(os.getenv("MULTIMODAL_QUALITY_MIN_ELIGIBLE_IMAGES", "0")),
            "min_enriched_images": int(os.getenv("MULTIMODAL_QUALITY_MIN_ENRICHED_IMAGES", "0")),
            "min_enrichment_rate": float(os.getenv("MULTIMODAL_QUALITY_MIN_ENRICHMENT_RATE", "0")),
        }

    def _quality_evaluation(self, manifest: dict[str, Any], directory: Path) -> dict[str, Any]:
        """将构建观察与可选 OCR probe 报告按 manifest 中的阈值判定。"""
        missing_contracts = [name for name in ("quality_policy", "quality_observations") if name not in manifest]
        if missing_contracts:
            return {"passed": False, "policy": manifest.get("quality_policy"), "observed": manifest.get("quality_observations"), "enrichment_rate": None, "failures": [f"legacy_manifest_missing_{name}" for name in missing_contracts]}
        policy = manifest["quality_policy"]
        observed = dict(manifest["quality_observations"])
        eligible = int(observed.get("eligible_images", 0)); enriched = int(observed.get("enriched_images", 0))
        observed.update({"eligible_images": eligible, "enriched_images": enriched, "vision_failed_images": int(observed.get("vision_failed_images", 0)), "skipped_images": int(observed.get("skipped_images", 0))})
        rate = enriched / eligible if eligible else 0.0
        failures: list[str] = []
        if eligible < int(policy["min_eligible_images"]): failures.append("eligible_images_below_minimum")
        if enriched < int(policy["min_enriched_images"]): failures.append("enriched_images_below_minimum")
        if rate < float(policy["min_enrichment_rate"]): failures.append("enrichment_rate_below_minimum")
        return {"passed": not failures, "policy": policy, "observed": observed, "enrichment_rate": rate, "failures": failures}

    def _audit_manifest_chain(
        self,
        manifest: dict[str, Any],
        units: list[KnowledgeUnit],
        store: AssetStore,
        *,
        require_assets: bool = True,
    ) -> list[str]:
        """校验身份链；仅严格 staged 复核时读取外置 source/解析资产。"""
        documents = manifest.get("documents")
        sources = manifest.get("sources")
        if not isinstance(documents, list) or not documents or not isinstance(sources, list):
            return ["missing_document_audit_chain"]
        expected_sources = {
            (source.get("source_id"), source.get("content_hash"))
            for source in sources
            if isinstance(source, dict) and source.get("source_id")
        }
        legacy_sources = {(source.get("relative_path"), source.get("content_hash")) for source in sources if isinstance(source, dict)}
        document_ids: set[str] = set()
        for document in documents:
            if not isinstance(document, dict):
                return ["missing_audit_asset"]
            relative_path = document.get("relative_path")
            content_hash = document.get("content_hash")
            source_id = document.get("source_id")
            document_id = document.get("document_id")
            source_asset_uri = document.get("source_asset_uri")
            source_match = (
                (source_id, content_hash) in expected_sources
                if isinstance(source_id, str)
                else (relative_path, content_hash) in legacy_sources
            )
            expected_document_id = next(
                (
                    source.get("document_id")
                    for source in sources
                    if isinstance(source, dict)
                    and isinstance(source.get("document_id"), str)
                    and (
                        (source_id and source.get("source_id") == source_id)
                        or (not source_id and source.get("relative_path") == relative_path)
                    )
                    and source.get("content_hash") == content_hash
                ),
                stable_id("doc", {"path": relative_path, "content_hash": content_hash}) if isinstance(relative_path, str) else None,
            )
            if (
                not isinstance(relative_path, str)
                or not isinstance(content_hash, str)
                or not isinstance(document_id, str)
                or document_id != expected_document_id
                or not source_match
                or document_id in document_ids
            ):
                return ["missing_audit_asset"]
            if require_assets and (
                not isinstance(source_asset_uri, str)
                or not store.exists(source_asset_uri)
                or sha256_bytes(store.read(source_asset_uri)) != content_hash
            ):
                return ["missing_audit_asset"]
            document_ids.add(document_id)
            artifacts = document.get("parser_artifacts", [])
            if require_assets and not isinstance(artifacts, list):
                return ["missing_audit_asset"]
            if require_assets:
                for artifact in artifacts:
                    if not isinstance(artifact, dict) or not isinstance(artifact.get("asset_uri"), str) or not isinstance(artifact.get("content_hash"), str):
                        return ["missing_audit_asset"]
                    if not store.exists(artifact["asset_uri"]) or sha256_bytes(store.read(artifact["asset_uri"])) != artifact["content_hash"]:
                        return ["missing_audit_asset"]
        actual_sources = {
            (document.get("source_id"), document.get("content_hash"))
            for document in documents
            if document.get("source_id")
        }
        if actual_sources != expected_sources and {
            (document.get("relative_path"), document.get("content_hash"))
            for document in documents
        } != legacy_sources:
            return ["missing_audit_asset"]
        if any(unit.document_id not in document_ids for unit in units):
            return ["orphaned_unit_document"]
        if require_assets:
            for unit in units:
                if unit.asset_uri and not store.exists(unit.asset_uri):
                    continue
                if unit.modality == "image" and unit.asset_uri:
                    if sha256_bytes(store.read(unit.asset_uri)) != unit.content_hash:
                        return ["unit_asset_hash_mismatch"]
                elif unit.asset_uri and not unit.raw_text and sha256_bytes(store.read(unit.asset_uri)) != unit.content_hash:
                    return ["unit_asset_hash_mismatch"]
        return []

    @staticmethod
    def _audit_outbound_manifest(directory: Path, manifest: dict[str, Any]) -> list[str]:
        """校验 outbound 文件哈希、数量、来源和唯一图片身份。"""
        path = directory / "outbound_manifest.json"
        expected_hash = manifest.get("outbound_manifest_sha256")
        if not path.is_file() or not isinstance(expected_hash, str) or sha256_bytes(path.read_bytes()) != expected_hash:
            return ["outbound_manifest_mismatch"]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
                return ["invalid_outbound_manifest"]
            records = [OutboundImageRecord.model_validate(record) for record in payload.get("images", [])]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return ["invalid_outbound_manifest"]
        policy_hash = manifest.get("build_configuration", {}).get("remote_policy_hash")
        response_adapter_version = manifest.get("build_configuration", {}).get("vision", {}).get("response_adapter_version")
        if payload.get("schema_version") != "outbound-v1" or len(records) != manifest.get("outbound_image_count"):
            return ["invalid_outbound_manifest"]
        sources = {
            (source.get("source_id"), source.get("relative_path"), source.get("content_hash"))
            for source in manifest.get("sources", [])
            if isinstance(source, dict)
        }
        identities = {(record.document_id, record.page_number, record.image_index) for record in records}
        if len(identities) != len(records) or any(
            (record.source_id, record.source_relative_path, record.source_sha256) not in sources
            and ("", record.source_relative_path, record.source_sha256) not in sources
            and (None, record.source_relative_path, record.source_sha256) not in sources
            for record in records
        ):
            return ["invalid_outbound_manifest"]
        if not isinstance(policy_hash, str) or any(record.remote_policy_sha256 != policy_hash for record in records):
            return ["invalid_outbound_manifest"]
        if not isinstance(response_adapter_version, str) or any(record.response_adapter_version != response_adapter_version for record in records):
            return ["invalid_outbound_manifest"]
        return []

    def _configuration_status(self) -> dict[str, Any]:
        """返回不含密钥或地址值的阶段零配置检查结果。"""
        parser_status = self._parser_status()
        return {
            "embedding": embedding_fingerprint(),
            "vision_provider": "wcode",
            "vision_model": os.getenv("VISION_MODEL", REQUIRED_MODEL),
            "vision_api_key_configured": bool(get_secret("VISION_API_KEY")),
            "vision_base_url_configured": bool(os.getenv("VISION_BASE_URL")),
            "allowed_remote_sources": ["Pearl fixed samples"],
            "asset_root": str(self.asset_root),
            "index_root": str(self.index_root),
            "parser": parser_status,
        }

    def _parser_status(self) -> dict[str, Any]:
        """报告解析器包与 Docling 布局模型是否同时可用，不执行下载。"""
        docling_installed = importlib.util.find_spec("docling") is not None
        artifacts_path = Path(os.getenv("MULTIMODAL_DOCLING_ARTIFACTS_DIR", Path.home() / ".cache" / "docling" / "models"))
        legacy_layout_model = Path.home() / ".cache" / "huggingface" / "hub" / "models--docling-project--docling-layout-heron"
        return {
            "preferred": os.getenv("MULTIMODAL_PARSER", "docling"),
            "docling_installed": docling_installed,
            "docling_artifacts_path": str(artifacts_path),
            "docling_layout_model_available": any(artifacts_path.rglob("model.safetensors")) or (legacy_layout_model.exists() and any(legacy_layout_model.rglob("model.safetensors"))),
        }

    def _remote_resource_allowed(self, source_path: Path, page_number: int | None) -> bool:
        """只允许唯一哈希解析出的受控来源页进入远程视觉。"""
        resolved = source_path.resolve()
        try:
            sources = resolve_production_sources()
        except (FileNotFoundError, OSError, ValueError):
            return False
        for source in sources:
            if source["path"] == resolved:
                return self.remote_policy.allows_pearl_page(Path(source["configured_path"]).name, page_number)
        return False

    def _resolve_table_recovery_provider(self, analyzer: VisionAnalyzer, allow_remote_data: bool) -> TableRecoveryProvider | None:
        """Resolve the replaceable table adapter without exposing transport details to parsing."""
        if self.table_recovery_provider is not None:
            return self.table_recovery_provider
        if allow_remote_data and os.getenv("MULTIMODAL_TABLE_RECOVERY_PROVIDER", "remote_vlm") == "remote_vlm":
            return RemoteVlmTableRecoveryProvider(analyzer)
        return None

    def _table_recovery_provider_name(self) -> str:
        """Return the provider identity that participates in immutable versioning."""
        if self.table_recovery_provider is not None:
            return str(getattr(self.table_recovery_provider, "provider_name", "custom"))
        return os.getenv("MULTIMODAL_TABLE_RECOVERY_PROVIDER", "remote_vlm")

    def _table_recovery_configuration(self) -> dict[str, str]:
        """Return provider, model, prompt, and adapter identities for versioning."""
        provider = self.table_recovery_provider
        return {
            "provider": self._table_recovery_provider_name(),
            "model": str(getattr(provider, "model", os.getenv("MULTIMODAL_TABLE_RECOVERY_MODEL", os.getenv("VISION_MODEL", REQUIRED_MODEL)))),
            "prompt_version": str(getattr(provider, "prompt_version", PROMPT_VERSION)),
            "response_adapter_version": str(getattr(provider, "response_adapter_version", TABLE_RECOVERY_ADAPTER_VERSION)),
            "adapter_version": TABLE_RECOVERY_ADAPTER_VERSION,
        }

    def _is_frozen_production_source(self, source_path: Path) -> bool:
        """确认来源是当前哈希匹配的冻结生产文件。"""
        if self._frozen_production_paths is None:
            try:
                self._frozen_production_paths = {path.resolve() for path in production_source_paths()}
            except (FileNotFoundError, ValueError):
                self._frozen_production_paths = set()
        return source_path.resolve() in self._frozen_production_paths

    def _version_dir(self, index_version: str) -> Path:
        """校验版本名并解析到隔离暂存根目录。"""
        if not index_version.startswith("mm_") or "/" in index_version or "\\" in index_version: raise ValueError("invalid multimodal index version")
        path = self.index_root / index_version
        if not path.is_dir(): raise FileNotFoundError("multimodal index version does not exist")
        return path
