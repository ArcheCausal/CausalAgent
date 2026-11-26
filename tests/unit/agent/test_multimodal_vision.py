"""多模态远程视觉与出站授权契约。

文档职责：验证远程视觉的来源白名单、模型与域名固定、预算与并发、schema 修复、审计脱敏和出站清单。

适用范围：修改 multimodal.vision、远程出站授权或视觉审计时使用；本地解析和索引发布由同目录其他测试覆盖。
"""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from Agent.knowledge_base.multimodal.assets import AssetStore
from Agent.knowledge_base.multimodal.contracts import (
    IngestionIssue,
    IssueSeverity,
    KnowledgeUnit,
    UnitStatus,
    VisionAnalysis,
    sha256_bytes,
    stable_id,
)
from Agent.knowledge_base.multimodal.pipeline import MultimodalKnowledgeBaseMaintenance
from Agent.knowledge_base.multimodal.parsers import (
    ParsedDocument,
    ParsedItem,
    _convert_docling_page,
    _rapidocr_probe,
    decide_page_route,
    inspect_source,
    parse_document,
)
from Agent.knowledge_base.rag_runtime import RagRuntimeConfig
from Agent.knowledge_base.multimodal.vision import (
    REQUIRED_MODEL,
    RESPONSE_ADAPTER_VERSION,
    VisionAnalyzer,
)
from Agent.tool_node.rag_questions import normalize_rag_question_output
from Agent.tool_node.rag_tool_registry import build_rag_tools
from tests.unit.agent.multimodal_fixtures import (
    REPOSITORY_ROOT,
    TEST_LOCAL_EMBEDDING,
    outbound_record as _outbound_record,
    png_bytes as _png_bytes,
    vision_payload as _vision_payload,
)


class MultimodalVisionTests(unittest.TestCase):
    """远程视觉与出站授权：只使用 fake SDK 和临时目录。"""

    def test_remote_vision_allowlist_rejects_arbitrary_local_file(self) -> None:
        """显式远程许可也不得扩大到非边界文件。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "private.png"; source.write_bytes(b"not-an-image")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "runtime" / "active.json")
            self.assertFalse(service._remote_resource_allowed(source, None))

    def test_remote_policy_allows_only_fixed_pearl_pages(self) -> None:
        """Pearl 资料必须同时匹配受控来源身份和固定页码。"""
        service = MultimodalKnowledgeBaseMaintenance()
        pearl = Path(__file__).resolve().parents[3] / "Agent" / "knowledge_base" / "source" / "Pearl_2009_Causality-mono.pdf"
        if not pearl.is_file():
            self.skipTest("正式 Pearl 来源未安装")
        self.assertTrue(service._remote_resource_allowed(pearl, 1))
        self.assertTrue(service._remote_resource_allowed(pearl, 7))
        self.assertTrue(service._remote_resource_allowed(pearl, 487))
        self.assertFalse(service._remote_resource_allowed(pearl, 0))
        self.assertFalse(service._remote_resource_allowed(pearl, 488))

    def test_remote_policy_does_not_guess_when_formal_source_resolution_fails(self) -> None:
        """正式来源零命中或多命中时，不能退回文件名猜测来放行 VLM。"""
        service = MultimodalKnowledgeBaseMaintenance()
        pearl = Path(__file__).resolve().parents[3] / "Agent" / "knowledge_base" / "source" / "Pearl_2009_Causality-mono.pdf"
        with patch("Agent.knowledge_base.multimodal.pipeline.resolve_production_sources", side_effect=ValueError("2 matches")):
            self.assertFalse(service._remote_resource_allowed(pearl, 1))

    def test_remote_authorization_maps_catalog_hash_id_to_formal_source_id(self) -> None:
        """隔离目录用内容哈希 ID，正式 manifest 仍保留 canonical source ID。"""
        service = MultimodalKnowledgeBaseMaintenance()
        entries = [{
            "source_id": "pearl-2009-causality",
            "catalog_source_id": "source_" + "a" * 64,
        }]
        self.assertEqual(
            service._authorized_source_ids(
                entries,
                allow_remote_data=True,
                authorized_source_ids=["source_" + "a" * 64],
            ),
            {"pearl-2009-causality"},
        )

    def test_vision_400_uses_json_prompt_fallback_and_audits_without_response(self) -> None:
        """结构化输出不兼容时只降级一次，并写入脱敏审计。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL}):
            image = _png_bytes(); context = "SENSITIVE_CONTEXT"
            response = MagicMock()
            response.choices = [MagicMock(message=MagicMock(content=_vision_payload(summary="RESPONSE_BODY")))]
            response.usage = MagicMock(prompt_tokens=3, completion_tokens=4)
            response._request_id = "request-1"
            client = MagicMock(); client.chat.completions.create.side_effect = [type("Error", (Exception,), {"status_code": 400})(), response]
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client):
                analysis = analyzer.analyze(image, "image/png", context, outbound_record=_outbound_record(analyzer, image, context))
            self.assertEqual(analysis.content_kind, "chart")
            self.assertEqual(client.chat.completions.create.call_count, 2)
            audit = (Path(directory) / "vision_audit.jsonl").read_text(encoding="utf-8")
            self.assertIn("success_json_prompt_fallback", audit)
            self.assertNotIn("content_kind", audit)
            for sensitive in (context, "RESPONSE_BODY", base64.b64encode(image).decode("ascii")[:32], "data:image"):
                self.assertNotIn(sensitive, audit)

    def test_vision_quota_billing_opens_circuit_without_fallback(self) -> None:
        """quota/billing 失败不得尝试普通 JSON fallback，并阻止同一运行后续外发。"""
        class QuotaError(Exception):
            status_code = 400
            body = {"error": {"code": "insufficient_quota", "type": "billing_error"}}

        self.assertEqual(VisionAnalyzer._failure_category(QuotaError()), "quota_billing")
        payment_required = type("PaymentRequired", (Exception,), {"status_code": 402})()
        self.assertEqual(VisionAnalyzer._failure_category(payment_required), "quota_billing")
        quota_rate_limit = type("QuotaRateLimit", (Exception,), {"status_code": 429, "body": {"error": {"code": "insufficient_quota"}}})()
        self.assertEqual(VisionAnalyzer._failure_category(quota_rate_limit), "quota_billing")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL, "VISION_MAX_RETRIES": "2"}):
            first_image, second_image = _png_bytes("white"), _png_bytes("black")
            client = MagicMock(); client.chat.completions.create.side_effect = QuotaError()
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=2)
            first_outbound = _outbound_record(analyzer, first_image)
            second_outbound = _outbound_record(analyzer, second_image, image_index=2)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client) as openai:
                with self.assertRaisesRegex(RuntimeError, "without persisting") as first_failure:
                    analyzer.analyze(first_image, "image/png", outbound_record=first_outbound)
                self.assertEqual(first_failure.exception.category, "quota_billing")
                self.assertFalse(first_failure.exception.fallback_attempted)
                with self.assertRaisesRegex(RuntimeError, "quota/billing circuit is open"):
                    analyzer.analyze(second_image, "image/png", outbound_record=second_outbound)
            self.assertEqual(client.chat.completions.create.call_count, 1)
            openai.assert_called_once()
            failure = next(Path(directory).glob("*.failure.json"))
            self.assertEqual(json.loads(failure.read_text(encoding="utf-8"))["failure_category"], "quota_billing")

    def test_vision_normalizes_rgb_and_enforces_outbound_hashes(self) -> None:
        """图片必须先真实解码、缩放、RGB 化，并与 outbound 记录逐字段匹配。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL, "VISION_MAX_PIXELS": "16"}):
            rgba = io.BytesIO(); Image.new("RGBA", (8, 8), (255, 0, 0, 80)).save(rgba, format="PNG"); image = rgba.getvalue()
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            prepared = analyzer.prepare_image(image)
            self.assertLessEqual(prepared.width * prepared.height, 16)
            self.assertEqual(prepared.media_type, "image/png")
            with Image.open(io.BytesIO(prepared.payload)) as decoded:
                self.assertEqual(decoded.mode, "RGB")
            with self.assertRaises(PermissionError):
                analyzer.analyze(image, "image/png")
            record = _outbound_record(analyzer, image).model_copy(update={"normalized_sha256": "0" * 64})
            with self.assertRaises(PermissionError):
                analyzer.analyze(image, "image/png", outbound_record=record)
            policy_drift = _outbound_record(analyzer, image).model_copy(update={"remote_policy_sha256": "0" * 64})
            with self.assertRaises(PermissionError):
                analyzer.analyze(image, "image/png", outbound_record=policy_drift)

    def test_vision_repairs_schema_once_and_audits_latency(self) -> None:
        """可提取但不符合 schema 的 JSON 只允许一次受控修复。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL}):
            image = _png_bytes(); broken = MagicMock(); repaired = MagicMock()
            broken.choices = [MagicMock(message=MagicMock(content='{"content_kind":"chart"}'))]
            repaired.choices = [MagicMock(message=MagicMock(content=_vision_payload()))]; repaired.usage = None; repaired._request_id = "repair"
            client = MagicMock(); client.chat.completions.create.side_effect = [broken, repaired]
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client):
                result = analyzer.analyze(image, "image/png", outbound_record=_outbound_record(analyzer, image))
            self.assertEqual(result.ocr_text, "图中文字")
            audit = json.loads((Path(directory) / "vision_audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(audit["status"], "success_schema_repair")
            self.assertEqual(audit["retry_count"], 1)
            self.assertIsInstance(audit["latency_ms"], int)

    def test_vision_normalizes_text_lists_but_rejects_non_text_formula_values(self) -> None:
        """字符串列表应无损拼接；任意对象不能直接转成字符串。"""
        parsed = VisionAnalyzer._parse_analysis(_vision_payload(formula_latex=["x", "y"]))
        self.assertEqual(parsed.formula_latex, "x\ny")
        with self.assertRaisesRegex(ValueError, "invalid_schema"):
            VisionAnalyzer._parse_analysis(_vision_payload(formula_latex={"value": "x"}))

    def test_vision_normalizes_uncertain_relation_objects(self) -> None:
        """不确定关系对象应转成可检索字符串，但不放宽任意对象。"""
        parsed = VisionAnalyzer._parse_analysis(_vision_payload(uncertain_relations=[{"source": "A", "target": "B", "condition": "可能"}]))
        self.assertEqual(parsed.uncertain_relations, ["A -> B (可能)"])
        with self.assertRaisesRegex(ValueError, "invalid_schema"):
            VisionAnalyzer._parse_analysis(_vision_payload(uncertain_relations=[{"source": "A"}]))

    def test_vision_normalizes_new_model_scalar_and_list_shapes(self) -> None:
        """新模型的对象/标量变体应保留内容并通过既有 vision schema。"""
        parsed = VisionAnalyzer._parse_analysis(_vision_payload(
            visible_facts="存在坐标轴",
            entities=[{"name": "A", "kind": "node"}],
            table_markdown=None,
            formula_latex=None,
        ))
        self.assertEqual(parsed.visible_facts, ["存在坐标轴"])
        self.assertIn('"name": "A"', parsed.entities[0])
        self.assertEqual(parsed.table_markdown, "")
        self.assertEqual(parsed.formula_latex, "")

    def test_vision_schema_failure_records_only_sanitized_validation_paths(self) -> None:
        """Schema 失败应暴露字段/类型路径用于诊断，不能暴露响应正文。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL}):
            image = _png_bytes()
            broken = MagicMock(); broken.choices = [MagicMock(message=MagicMock(content=_vision_payload(confidence="not-a-number")))]
            repair = MagicMock(); repair.choices = [MagicMock(message=MagicMock(content=_vision_payload(confidence="not-a-number")))]
            client = MagicMock(); client.chat.completions.create.side_effect = [broken, repair]
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client):
                with self.assertRaisesRegex(RuntimeError, "without persisting"):
                    analyzer.analyze(image, "image/png", outbound_record=_outbound_record(analyzer, image))
            failure = next(Path(directory).glob("*.failure.json"))
            payload = json.loads(failure.read_text(encoding="utf-8"))
            self.assertEqual(payload["failure_category"], "invalid_schema")
            self.assertIn("confidence:float_parsing", payload["validation_error_paths"])
            self.assertNotIn("not-a-number", failure.read_text(encoding="utf-8"))

    def test_vision_retries_429_with_jitter_and_redacts_failure(self) -> None:
        """429 必须抖动退避；最终失败审计不得包含异常正文或密钥。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "super-secret", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL, "VISION_MAX_RETRIES": "1"}):
            image = _png_bytes(); response = MagicMock(); response.choices = [MagicMock(message=MagicMock(content=_vision_payload()))]; response.usage = None; response._request_id = "ok"
            rate_limit = type("RateLimit", (Exception,), {"status_code": 429})()
            client = MagicMock(); client.chat.completions.create.side_effect = [rate_limit, response]
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client), patch("Agent.knowledge_base.multimodal.vision.random.uniform", return_value=0.1), patch("Agent.knowledge_base.multimodal.vision.time.sleep") as sleep:
                analyzer.analyze(image, "image/png", outbound_record=_outbound_record(analyzer, image))
            sleep.assert_called_once_with(1.1)
            audit = (Path(directory) / "vision_audit.jsonl").read_text(encoding="utf-8")
            self.assertIn('"retry_count":1', audit)
            self.assertNotIn("super-secret", audit)

    def test_vision_failure_categories_cover_retry_and_deterministic_errors(self) -> None:
        """408/429/5xx/timeout/连接错误与确定性 4xx 必须得到稳定脱敏分类。"""
        cases = {408: "timeout", 429: "rate_limited", 500: "server_error", 503: "server_error", 401: "http_401", 403: "http_403", 404: "http_404"}
        for status, expected in cases.items():
            error = type(f"Http{status}", (Exception,), {"status_code": status})()
            self.assertEqual(VisionAnalyzer._failure_category(error), expected)
        self.assertEqual(VisionAnalyzer._failure_category(TimeoutError()), "timeout")
        self.assertEqual(VisionAnalyzer._failure_category(ConnectionError()), "connection")

    def test_vision_422_falls_back_and_budget_stops_second_image(self) -> None:
        """422 回退普通 JSON 一次；成功缓存外的第二张图片受预算阻断。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL}):
            first_image, second_image = _png_bytes("white"), _png_bytes("black")
            response = MagicMock(); response.choices = [MagicMock(message=MagicMock(content=_vision_payload()))]; response.usage = None; response._request_id = "fallback"
            unsupported = type("Unprocessable", (Exception,), {"status_code": 422})()
            client = MagicMock(); client.chat.completions.create.side_effect = [unsupported, response]
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client):
                analyzer.analyze(first_image, "image/png", outbound_record=_outbound_record(analyzer, first_image))
                with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                    analyzer.analyze(second_image, "image/png", outbound_record=_outbound_record(analyzer, second_image, image_index=2))
            self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_vision_cache_prevents_second_sdk_call(self) -> None:
        """相同图片、模型和 Prompt 命中缓存后不得再次调用远程 SDK。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL}):
            image = _png_bytes(); response = MagicMock(); response.choices = [MagicMock(message=MagicMock(content=_vision_payload()))]; response.usage = None; response._request_id = "request"
            client = MagicMock(); client.chat.completions.create.return_value = response
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            outbound = _outbound_record(analyzer, image)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client):
                first = analyzer.analyze(image, "image/png", outbound_record=outbound)
                second = analyzer.analyze(image, "image/png", outbound_record=outbound)
            self.assertEqual(first, second)
            self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_vision_rejects_model_or_provider_drift(self) -> None:
        """远程视觉边界必须固定为获准的 WCode 模型和域名。"""
        self.assertEqual(REQUIRED_MODEL, "qwen-vl-plus")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": "other/model"}):
            self.assertFalse(VisionAnalyzer(Path(directory), allow_remote_data=True).configured())
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.example.test/v1", "VISION_MODEL": REQUIRED_MODEL}):
            self.assertFalse(VisionAnalyzer(Path(directory), allow_remote_data=True).configured())
        for base_url in ("http://api.wcode.net/v1", "https://evilwcode.net/v1", "https://wcode.net.evil.test/v1"):
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": base_url, "VISION_MODEL": REQUIRED_MODEL}):
                self.assertFalse(VisionAnalyzer(Path(directory), allow_remote_data=True).configured())

    def test_vision_concurrency_cap_serializes_extra_requests(self) -> None:
        """并发上限必须约束实际 SDK 调用，而不是只保留配置字段。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL, "VISION_MAX_CONCURRENCY": "1"}):
            response = MagicMock(); response.choices = [MagicMock(message=MagicMock(content=_vision_payload()))]; response.usage = None; response._request_id = "request"
            active = 0; peak = 0; lock = threading.Lock()
            def invoke(*_args, **_kwargs):
                """模拟可并发观测的单次远程 SDK 调用。"""
                nonlocal active, peak
                with lock:
                    active += 1; peak = max(peak, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
                return response
            client = MagicMock(); client.chat.completions.create.side_effect = invoke
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=2)
            images = [_png_bytes("white"), _png_bytes("black")]
            outbound_records = [_outbound_record(analyzer, image, image_index=index + 1) for index, image in enumerate(images)]
            errors = []

            def analyze(image, record):
                try:
                    analyzer.analyze(image, "image/png", outbound_record=record)
                except Exception as exc:  # 记录真实失败原因，避免线程静默吞掉
                    errors.append(repr(exc))

            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client):
                workers = [threading.Thread(target=analyze, args=(image, outbound_records[index])) for index, image in enumerate(images)]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join()
            failures = [json.loads(item.read_text(encoding="utf-8")) for item in Path(directory).glob("*.failure.json")]
            self.assertEqual(errors, [], failures)
            self.assertEqual(peak, 1)

    def test_remote_failure_blocks_image_unit(self) -> None:
        """统一远程分析失败时不得降级为本地 OCR 或 caption 单元。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = _png_bytes(); source = root / "page.png"; source.write_bytes(image)
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            store = AssetStore(root / "assets")
            preparer = VisionAnalyzer(root / "cache", allow_remote_data=False)
            analyzer = MagicMock(); analyzer.model = REQUIRED_MODEL; analyzer.response_adapter_version = RESPONSE_ADAPTER_VERSION; analyzer.remote_policy_sha256 = preparer.remote_policy_sha256
            analyzer.analyze.side_effect = RuntimeError("timeout")
            quality = {"eligible_images": 0, "enriched_images": 0, "vision_failed_images": 0, "skipped_images": 0, "low_value_images_skipped": 0, "filtered_short_text_units": 0}
            issues: list = []
            item = ParsedItem(modality="image", content_kind="image", raw_text="本地 caption", asset_bytes=image, asset_name="page_0001_image_0001.png", page_number=1)
            outbound = [_outbound_record(preparer, image, source_relative_path="page.png", source_sha256="b" * 64, document_id="doc_" + "a" * 64)]
            with patch.object(service, "_remote_resource_allowed", return_value=True):
                unit = service._build_unit(item, source, "doc_" + "a" * 64, 1, "docling", "2.115.0", embedding, store, analyzer, quality, issues, allow_remote_data=True, source_relative_path="page.png", source_sha256="b" * 64, outbound_records=outbound)
            self.assertIsNone(unit)
            self.assertTrue(any(issue.code == "remote_image_failed" and issue.blocking for issue in issues))

    def test_remote_image_without_frozen_record_is_blocked_before_sdk_call(self) -> None:
        """候选内不存在批准记录时，图片不得自行补写清单或调用远程 adapter。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = _png_bytes(); source = root / "page.png"; source.write_bytes(image)
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            analyzer = MagicMock(); analyzer.model = REQUIRED_MODEL; analyzer.response_adapter_version = RESPONSE_ADAPTER_VERSION; analyzer.remote_policy_sha256 = service.remote_policy.policy_sha256
            quality = {"eligible_images": 0, "enriched_images": 0, "vision_failed_images": 0, "skipped_images": 0, "low_value_images_skipped": 0, "filtered_short_text_units": 0}
            issues: list = []
            item = ParsedItem(modality="image", content_kind="image", raw_text="caption", asset_bytes=image, asset_name="page.png", page_number=1)
            with patch.object(service, "_remote_resource_allowed", return_value=True):
                unit = service._build_unit(item, source, "doc_" + "a" * 64, 1, "docling", "2.115.0", embedding, AssetStore(root / "assets"), analyzer, quality, issues, allow_remote_data=True, source_relative_path="page.png", source_sha256="b" * 64, outbound_records=[])
            self.assertIsNone(unit)
            analyzer.analyze.assert_not_called()
            self.assertTrue(any(issue.code == "remote_source_not_allowed" and issue.blocking for issue in issues))

    def test_remote_image_without_source_authorization_is_blocked_before_sdk_call(self) -> None:
        """即使有 outbound 记录，未获来源级授权也不得调用 VLM。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = _png_bytes(); source = root / "page.png"; source.write_bytes(image)
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            preparer = VisionAnalyzer(root / "cache", allow_remote_data=False)
            analyzer = MagicMock(); analyzer.model = REQUIRED_MODEL; analyzer.response_adapter_version = RESPONSE_ADAPTER_VERSION; analyzer.remote_policy_sha256 = preparer.remote_policy_sha256
            quality = {"eligible_images": 0, "enriched_images": 0, "vision_failed_images": 0, "skipped_images": 0, "low_value_images_skipped": 0, "filtered_short_text_units": 0}
            issues: list = []
            item = ParsedItem(modality="image", content_kind="image", raw_text="caption", asset_bytes=image, asset_name="page.png", page_number=1)
            outbound = [_outbound_record(preparer, image, source_relative_path="page.png", source_sha256="b" * 64, document_id="doc_" + "a" * 64)]
            unit = service._build_unit(item, source, "doc_" + "a" * 64, 1, "docling", "2.115.0", embedding, AssetStore(root / "assets"), analyzer, quality, issues, allow_remote_data=True, source_authorized=False, source_relative_path="page.png", source_sha256="b" * 64, outbound_records=outbound)
            self.assertIsNone(unit)
            analyzer.analyze.assert_not_called()
            self.assertTrue(any(issue.code == "remote_source_not_allowed" and issue.blocking for issue in issues))

    def test_remote_ingestion_requires_explicit_source_authorization(self) -> None:
        """远程摄取入口没有来源级授权时必须在调用 provider 前阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.md"
            source.write_text("正文", encoding="utf-8")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            with self.assertRaisesRegex(PermissionError, "source-level authorization"):
                service.ingest([str(source)], allow_remote_data=True)

    def test_recorded_vision_failure_blocks_uncontrolled_retry(self) -> None:
        """失败状态必须在后续普通构建中阻止再次外发。"""
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VISION_API_KEY": "key", "VISION_BASE_URL": "https://api.wcode.net/v1", "VISION_MODEL": REQUIRED_MODEL, "VISION_MAX_RETRIES": "0"}):
            image = _png_bytes()
            client = MagicMock(); client.chat.completions.create.side_effect = RuntimeError("timeout")
            analyzer = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            outbound = _outbound_record(analyzer, image)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=client):
                with self.assertRaises(RuntimeError): analyzer.analyze(image, "image/png", outbound_record=outbound)
            retry_client = MagicMock(); retry = VisionAnalyzer(Path(directory), allow_remote_data=True, max_images=1)
            with patch("Agent.knowledge_base.multimodal.vision.OpenAI", return_value=retry_client):
                with self.assertRaisesRegex(RuntimeError, "already recorded"): retry.analyze(image, "image/png", outbound_record=_outbound_record(retry, image))
            retry_client.assert_not_called()
            failure = next(Path(directory).glob("*.failure.json")).read_text(encoding="utf-8")
            self.assertIn("RuntimeError", failure); self.assertNotIn("timeout", failure)

    def test_informative_false_keeps_remote_ocr_unit(self) -> None:
        """语义不具信息量但远程 OCR 有文本时仍生成一个完整图片单元。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = _png_bytes(); source = root / "page.png"; source.write_bytes(image)
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            store = AssetStore(root / "assets")
            preparer = VisionAnalyzer(root / "cache", allow_remote_data=False)
            analyzer = MagicMock(); analyzer.model = REQUIRED_MODEL; analyzer.response_adapter_version = RESPONSE_ADAPTER_VERSION; analyzer.remote_policy_sha256 = preparer.remote_policy_sha256
            analyzer.analyze.return_value = VisionAnalysis(content_kind="illustration", ocr_text="远程 OCR 文本", visible_facts=[], summary="", entities=[], table_markdown="", formula_latex="", directed_relations=[], uncertain_relations=[], informative=False, confidence=0.3)
            quality = {"eligible_images": 0, "enriched_images": 0, "vision_failed_images": 0, "skipped_images": 0, "low_value_images_skipped": 0, "filtered_short_text_units": 0}
            issues: list = []
            item = ParsedItem(modality="image", content_kind="image", raw_text="本地 caption", asset_bytes=image, asset_name="page_0001_image_0001.png", page_number=1)
            outbound = [_outbound_record(preparer, image, source_relative_path="page.png", source_sha256="b" * 64, document_id="doc_" + "a" * 64)]
            with patch.object(service, "_remote_resource_allowed", return_value=True):
                unit = service._build_unit(item, source, "doc_" + "a" * 64, 1, "docling", "2.115.0", embedding, store, analyzer, quality, issues, allow_remote_data=True, source_relative_path="page.png", source_sha256="b" * 64, outbound_records=outbound)
            self.assertIsNotNone(unit)
            self.assertIn("远程 OCR 文本", unit.retrieval_text)
            self.assertIn("本地 caption", unit.raw_text)
            self.assertEqual(unit.vision_model, REQUIRED_MODEL)
            self.assertEqual(len(outbound), 1)
            manifest = outbound[0].model_dump(mode="json")
            self.assertEqual(manifest["source_sha256"], "b" * 64)
            self.assertEqual(manifest["page_number"], 1)
            self.assertEqual(manifest["original_sha256"], sha256_bytes(image))
            self.assertNotIn("本地 caption", json.dumps(manifest, ensure_ascii=False))

    def test_image_content_hash_uses_asset_bytes(self) -> None:
        """图片单元的 content_hash 必须基于图片字节而非 OCR 文本。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "page.png"; source.write_bytes(b"image-bytes")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            store = AssetStore(root / "assets")
            analyzer = MagicMock(); analyzer.model = ""
            quality = {"eligible_images": 0, "enriched_images": 0, "vision_failed_images": 0, "skipped_images": 0, "low_value_images_skipped": 0, "filtered_short_text_units": 0}
            issues: list = []
            item = ParsedItem(modality="image", content_kind="image", raw_text="OCR text", asset_bytes=b"image-bytes", asset_name="page.png", page_number=1)
            unit = service._build_unit(item, source, "doc_" + "a" * 64, 1, "docling", "2.115.0", embedding, store, analyzer, quality, issues, allow_remote_data=False)
            self.assertEqual(unit.content_hash, sha256_bytes(b"image-bytes"))
            self.assertNotEqual(unit.content_hash, sha256_bytes("OCR text".encode()))

    def test_pipeline_remote_context_is_same_page_and_capped(self) -> None:
        """远程请求上下文只取当前图片 caption 与同页正文，最长 2000 字。"""
        image = ParsedItem("image", "image", raw_text="图片标题", page_number=2, asset_bytes=_png_bytes())
        items = [
            image,
            ParsedItem("text", "paragraph", raw_text="同页正文" * 400, page_number=2),
            ParsedItem("text", "paragraph", raw_text="其他页正文", page_number=3),
            ParsedItem("image", "image", raw_text="其他图片", page_number=2, asset_bytes=_png_bytes("black")),
        ]
        context = MultimodalKnowledgeBaseMaintenance._same_page_context(image, items)
        self.assertTrue(context.startswith("图片标题"))
        self.assertLessEqual(len(context), 2000)
        self.assertNotIn("其他页正文", context)
        self.assertNotIn("其他图片", context)

    def test_image_unit_id_changes_with_prompt_fingerprint(self) -> None:
        """图片 unit ID 不再绑定 RapidOCR，但必须绑定远程提示词版本。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "page.png"; source.write_bytes(b"image-bytes")
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            embedding = __import__("Agent.knowledge_base.multimodal.index", fromlist=["embedding_fingerprint"]).embedding_fingerprint()
            item = ParsedItem(modality="image", content_kind="image", raw_text="OCR text", asset_bytes=b"image-bytes", asset_name="page.png", page_number=1)
            analyzer = MagicMock(); analyzer.model = ""
            quality = {"eligible_images": 0, "enriched_images": 0, "vision_failed_images": 0, "skipped_images": 0, "low_value_images_skipped": 0, "filtered_short_text_units": 0}
            with patch("Agent.knowledge_base.multimodal.pipeline.PROMPT_VERSION", "vision-v1"):
                first = service._build_unit(item, source, "doc_" + "a" * 64, 1, "docling", "2.115.0", embedding, AssetStore(root / "assets"), analyzer, quality, [], allow_remote_data=False)
            with patch("Agent.knowledge_base.multimodal.pipeline.PROMPT_VERSION", "vision-v2"):
                second = service._build_unit(item, source, "doc_" + "a" * 64, 1, "docling", "2.115.0", embedding, AssetStore(root / "assets"), analyzer, quality, [], allow_remote_data=False)
            self.assertNotEqual(first.unit_id, second.unit_id)

    def test_outbound_manifest_audit_detects_tampering(self) -> None:
        """候选门禁必须回读 outbound 文件并校验哈希、来源与唯一图片身份。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); analyzer = VisionAnalyzer(root / "cache", allow_remote_data=False); image = _png_bytes()
            record = _outbound_record(analyzer, image)
            path = root / "outbound_manifest.json"
            MultimodalKnowledgeBaseMaintenance._write_outbound_manifest(path, [record])
            manifest = {"sources": [{"relative_path": "source.pdf", "content_hash": "a" * 64}], "build_configuration": {"remote_policy_hash": analyzer.remote_policy_sha256, "vision": {"response_adapter_version": RESPONSE_ADAPTER_VERSION}}, "outbound_manifest_sha256": sha256_bytes(path.read_bytes()), "outbound_image_count": 1}
            self.assertEqual(MultimodalKnowledgeBaseMaintenance._audit_outbound_manifest(root, manifest), [])
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(MultimodalKnowledgeBaseMaintenance._audit_outbound_manifest(root, manifest), ["outbound_manifest_mismatch"])

    def test_frozen_outbound_manifest_requires_current_sources_and_policy(self) -> None:
        """远程运行只能使用预冻结、同来源且同策略的批准图片清单。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            analyzer = VisionAnalyzer(root / "cache", allow_remote_data=False, remote_policy_sha256=service.remote_policy.policy_sha256)
            record = _outbound_record(analyzer, _png_bytes())
            path = root / "approved.json"
            service._write_outbound_manifest(path, [record])
            entries = [{"relative_path": "source.pdf", "content_hash": "a" * 64}]
            self.assertEqual(service._frozen_outbound_records(path, entries), [record])
            self.assertEqual(service._frozen_outbound_records(None, entries), [])
            with self.assertRaisesRegex(ValueError, "current frozen sources"):
                service._frozen_outbound_records(path, [{"relative_path": "source.pdf", "content_hash": "c" * 64}])

    def test_prepare_outbound_manifest_is_local_and_consumable_by_r2(self) -> None:
        """本地准备命令生成的记录应可直接作为 R2 的冻结输入，不调用远程 adapter。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "page.png"; image = _png_bytes(); source.write_bytes(image)
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            parsed = ParsedDocument("builtin-2", "2", (
                ParsedItem("text", "paragraph", raw_text="同页正文", page_number=1),
                ParsedItem("image", "image", raw_text="图片标题", page_number=1, asset_bytes=image, asset_name="page.png"),
            ))
            output = root / "approved.json"
            with patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=parsed), patch.object(service, "_remote_resource_allowed", return_value=True):
                result = service.prepare_outbound_manifest([str(source)], output)
            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["outbound_image_count"], 1)
            self.assertEqual(result["prepared_page_count"], 1)
            entries, _ = service._scan([str(source)])
            records = service._frozen_outbound_records(output, entries)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].model, REQUIRED_MODEL)
            self.assertEqual(records[0].context_sha256, sha256_bytes("同页正文\n图片标题".encode("utf-8")))

    def test_prepare_outbound_manifest_stops_before_remote_on_parse_error(self) -> None:
        """本地解析失败时不得产出可被误用的远程外发清单。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "page.png"; source.write_bytes(_png_bytes())
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            failed = ParsedDocument("docling", "2.115.0", issues=(IngestionIssue(code="pdf_page_parse_failed", message="failed", severity=IssueSeverity.ERROR, blocking=True),))
            output = root / "approved.json"
            with patch.object(service, "_remote_resource_allowed", return_value=True), patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=failed):
                with self.assertRaisesRegex(ValueError, "local parsing failed"):
                    service.prepare_outbound_manifest([str(source)], output)
            self.assertFalse(output.exists())

    def test_prepare_outbound_manifest_limits_eligible_pages_before_parsing(self) -> None:
        """R2 smoke 的页数上限应在本地解析前生效，避免为找图片扫描完整 PDF。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "page.png"; source.write_bytes(_png_bytes())
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            parsed = ParsedDocument("docling", "2.115.0", ())
            output = root / "approved.json"
            with patch.object(service, "_source_page_count", return_value=20), patch.object(service, "_remote_resource_allowed", return_value=True), patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=parsed) as parse_page:
                result = service.prepare_outbound_manifest([str(source)], output, max_pages=2)
            self.assertEqual(result["prepared_page_count"], 2)
            self.assertEqual(parse_page.call_count, 2)

    def test_manifest_disables_local_ocr_and_versions_remote_limits(self) -> None:
        """manifest 必须移除生产 RapidOCR 依赖并版本化远程边界。"""
        service = MultimodalKnowledgeBaseMaintenance()
        manifest = service._manifest([], allow_remote_data=False)
        build_config = manifest["build_configuration"]
        self.assertNotIn("ocr", build_config)
        self.assertFalse(build_config["vision"]["local_ocr_enabled"])
        self.assertEqual(build_config["vision"]["prompt_version"], "vision-v2")
        self.assertGreater(build_config["vision"]["max_pixels"], 0)
        self.assertGreater(build_config["vision"]["max_image_bytes"], 0)
        self.assertIsNone(build_config["vision"]["max_images"])
        self.assertEqual(VisionAnalyzer(Path(tempfile.gettempdir()), allow_remote_data=False, max_images=101).max_images, 101)
        self.assertIn("remote_policy_hash", build_config)
        self.assertRegex(build_config["remote_policy_hash"], r"^[0-9a-f]{64}$")

    def test_prepare_outbound_manifest_records_page_fallback_without_picture_duplicate(self) -> None:
        """整页 fallback 只能形成一个 page_render 外发记录，且携带质量门证据。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "page.png"
            source.write_bytes(_png_bytes())
            service = MultimodalKnowledgeBaseMaintenance(asset_root=root / "assets", index_root=root / "indexes", active_config=root / "active.json")
            parsed = ParsedDocument("docling", "2", (ParsedItem("page", "page_render", asset_bytes=_png_bytes(), asset_name="page.png", page_number=1),))
            output = root / "approved.json"
            with patch.object(service, "_remote_resource_allowed", return_value=True), patch("Agent.knowledge_base.multimodal.pipeline.parse_document_page", return_value=parsed):
                result = service.prepare_outbound_manifest([str(source)], output)
            records = service._read_outbound_manifest(output)
            self.assertEqual(result["outbound_image_count"], 1)
            self.assertEqual(records[0].route, "remote_page_fallback")
            self.assertEqual(records[0].quality_gate_version, "page-quality-v1")
            self.assertTrue(records[0].quality_summary)

if __name__ == "__main__":
    unittest.main()
