"""多模态解析失败日志的最小脱敏合同测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from Agent.knowledge_base.multimodal.assets import AssetStore
from Agent.knowledge_base.multimodal.contracts import IngestionIssue, IssueSeverity
from Agent.knowledge_base.multimodal.parsers import ParsedItem
from Agent.knowledge_base.multimodal.pipeline import (
    MultimodalKnowledgeBaseMaintenance,
    _log_page_parse_issues,
)


def _failure_inputs(tmp_path: Path, item: ParsedItem):
    maintenance = MultimodalKnowledgeBaseMaintenance(asset_root=tmp_path / "assets", index_root=tmp_path / "indexes")
    analyzer = MagicMock()
    quality = {key: 0 for key in ("eligible_images", "enriched_images", "vision_failed_images", "skipped_images", "low_value_images_skipped", "filtered_short_text_units")}
    issue_list: list[IngestionIssue] = []
    record = SimpleNamespace(
        document_id="doc_" + "b" * 64,
        page_number=3,
        image_index=2,
        source_relative_path="source.pdf",
        source_sha256="a" * 64,
        media_type="image/png",
    )
    return maintenance, analyzer, quality, issue_list, record, item


def test_remote_image_failure_logs_only_stable_fields_and_keeps_issue(tmp_path: Path):
    item = ParsedItem("image", "image", page_number=3, asset_bytes=b"image", asset_name="image.png")
    maintenance, analyzer, quality, issues, record, item = _failure_inputs(tmp_path, item)
    secret = "response-body prompt=secret /private/patient.pdf"

    with patch("Agent.knowledge_base.multimodal.pipeline.log_event") as log_event:
        unit = maintenance._build_unit(
            item,
            Path("C:/private/patient.pdf"),
            record.document_id,
            30002,
            "parser",
            "1",
            {"provider": "embedding", "model": "model"},
            AssetStore(tmp_path / "assets"),
            analyzer,
            quality,
            issues,
            True,
            source_authorized=True,
            source_relative_path=record.source_relative_path,
            source_sha256=record.source_sha256,
            outbound_records=[record],
            prefetched_vision=RuntimeError(secret),
        )

    assert unit is None
    assert [issue.code for issue in issues] == ["remote_image_failed"]
    assert log_event.call_count == 1
    details = log_event.call_args.kwargs["details"]
    assert details["reason_code"] == "remote_image_failed"
    assert details["source_alias"] == record.document_id
    assert details["page_number"] == 3
    assert details["image_index"] == 2
    assert set(details) == {"phase", "reason_code", "source_alias", "page_number", "image_index", "table_index", "status_code", "fallback_attempted", "circuit_breaker_open"}
    assert secret not in repr(log_event.call_args)


def test_remote_failure_logs_stable_category_from_exception_chain(tmp_path: Path):
    item = ParsedItem("image", "image", page_number=3, asset_bytes=b"image", asset_name="image.png")
    maintenance, analyzer, quality, issues, record, item = _failure_inputs(tmp_path, item)

    class BadRequest(Exception):
        status_code = 400

    wrapped = RuntimeError("vision analysis failed without persisting response content")
    wrapped.__cause__ = BadRequest("raw response body")
    wrapped.fallback_attempted = True

    with patch("Agent.knowledge_base.multimodal.pipeline.log_event") as log_event:
        maintenance._build_unit(
            item,
            Path("C:/private/patient.pdf"),
            record.document_id,
            30002,
            "parser",
            "1",
            {"provider": "embedding", "model": "model"},
            AssetStore(tmp_path / "assets"),
            analyzer,
            quality,
            issues,
            True,
            source_authorized=True,
            source_relative_path=record.source_relative_path,
            source_sha256=record.source_sha256,
            outbound_records=[record],
            prefetched_vision=wrapped,
        )

    details = log_event.call_args.kwargs["details"]
    assert details["reason_code"] == "http_400"
    assert details["source_alias"] == record.document_id
    assert details["status_code"] == 400
    assert details["fallback_attempted"] is True
    assert "raw response body" not in repr(log_event.call_args)


def test_table_recovery_failure_logs_once_and_keeps_issue(tmp_path: Path):
    class FailingProvider:
        requires_outbound_manifest = False

        def recover(self, *args, **kwargs):
            raise RuntimeError("table response secret /private/table.pdf")

    item = ParsedItem("table", "table_recovery", page_number=4, asset_bytes=b"table", asset_name="table.png")
    maintenance, analyzer, quality, issues, _record, item = _failure_inputs(tmp_path, item)

    with patch("Agent.knowledge_base.multimodal.pipeline.log_event") as log_event:
        unit = maintenance._build_unit(
            item,
            Path("C:/private/table.pdf"),
            "doc_" + "b" * 64,
            40001,
            "parser",
            "1",
            {"provider": "embedding", "model": "model"},
            AssetStore(tmp_path / "assets"),
            analyzer,
            quality,
            issues,
            True,
            table_recovery_provider=FailingProvider(),
        )

    assert unit is None
    assert [issue.code for issue in issues] == ["table_recovery_failed"]
    assert log_event.call_count == 1
    details = log_event.call_args.kwargs["details"]
    assert details["reason_code"] == "table_recovery_failed"
    assert details["source_alias"] == "doc_" + "b" * 64
    assert details["page_number"] == 4
    assert details["table_index"] == 1
    assert "table response secret" not in repr(log_event.call_args)


def test_asset_missing_is_logged_without_cmap_warning(tmp_path: Path):
    issue = IngestionIssue(
        code="table_recovery_asset_missing",
        message="Docling 第 2 页的空表无法提取裁剪图 /private/secret.pdf",
        severity=IssueSeverity.ERROR,
        blocking=True,
        source_path="C:/private/secret.pdf",
    )
    cmap_warning = IngestionIssue(
        code="pdf_cmap_warning",
        message="CMap warning: private document text",
        severity=IssueSeverity.WARNING,
        source_path="C:/private/secret.pdf",
    )

    with patch("Agent.knowledge_base.multimodal.pipeline.log_event") as log_event:
        _log_page_parse_issues([issue, cmap_warning], 2, source_alias="doc_" + "b" * 64)

    assert log_event.call_count == 1
    assert log_event.call_args.args[1] == "rag.multimodal.parse_failed"
    details = log_event.call_args.kwargs["details"]
    assert details == {
        "phase": "local_parse",
        "reason_code": "table_recovery_asset_missing",
        "source_alias": "doc_" + "b" * 64,
        "page_number": 2,
        "image_index": None,
        "table_index": None,
        "status_code": None,
        "fallback_attempted": False,
        "circuit_breaker_open": False,
    }
    assert "private document" not in repr(log_event.call_args)
    assert "secret.pdf" not in repr(log_event.call_args)
