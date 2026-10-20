"""WCode OpenAI-compatible 视觉增强适配器。"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import random
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI
from PIL import Image, ImageOps
from pydantic import ValidationError

from config.secret_file import get_secret, require_secret
from .contracts import OutboundImageRecord, VisionAnalysis, canonical_json, sha256_bytes
from .remote_policy import RemoteSamplePolicy

PROMPT_VERSION = "vision-v2"
RESPONSE_ADAPTER_VERSION = "response-normalize-v3"
REQUIRED_MODEL = "qwen-vl-plus"
DEFAULT_MAX_PIXELS = 16_000_000
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_PROMPT = """只转写和描述图片中直接可见的内容，不得根据邻近正文补画面中不存在的事实或箭头方向。返回一个 JSON 对象且必须完整包含：content_kind（causal_graph|chart|table|formula|illustration|other）、ocr_text（逐行可见文字，不确定字符不要猜）、visible_facts、summary、entities、table_markdown、formula_latex、directed_relations（source/target/condition）、uncertain_relations、confidence（0 到 1）、informative。方向不清晰的关系只能写入 uncertain_relations。"""
_REPAIR_PROMPT = "上一次输出未通过 vision-v2 schema。重新观察同一图片并只返回完整、合法的 JSON 对象；不要解释。"


@dataclass(frozen=True)
class NormalizedImage:
    """经过真实解码、方向校正、RGB 化和上限约束的远程图片。"""

    payload: bytes
    media_type: str
    width: int
    height: int
    original_sha256: str
    normalized_sha256: str
    original_bytes: int
    transformation: str


class VisionResponseError(ValueError):
    """携带脱敏失败类别的响应契约错误。"""

    def __init__(self, category: str, *, validation_error_paths: list[str] | None = None) -> None:
        """保存不会泄露原始响应的稳定失败分类。"""
        super().__init__(category)
        self.category = category
        self.validation_error_paths = tuple(validation_error_paths or ())


class VisionCircuitOpenError(RuntimeError):
    """quota/billing 失败后阻止本次运行继续外发。"""

    category = "quota_billing_circuit_open"

    def __init__(self) -> None:
        super().__init__("vision quota/billing circuit is open")


class VisionAnalyzer:
    """限制外发范围、重试次数与预算的 WCode 视觉客户端。"""

    def __init__(self, cache_dir: Path, *, allow_remote_data: bool, max_images: int | None = None, retry_failed: bool = False, remote_policy_sha256: str | None = None, model: str | None = None) -> None:
        """仅读取环境配置，不持久化密钥或原始响应。"""
        self.cache_dir = cache_dir
        self.allow_remote_data = allow_remote_data
        self.model = model or os.getenv("VISION_MODEL", REQUIRED_MODEL)
        self.timeout = int(os.getenv("VISION_TIMEOUT_SECONDS", "30"))
        self.max_retries = int(os.getenv("VISION_MAX_RETRIES", "2"))
        self.max_images = max_images if max_images is not None else max(
            1, int(os.getenv("VISION_MAX_IMAGES_PER_RUN", "100"))
        )
        self.max_concurrency = max(1, int(os.getenv("VISION_MAX_CONCURRENCY", "2")))
        self.max_pixels = max(1, int(os.getenv("VISION_MAX_PIXELS", str(DEFAULT_MAX_PIXELS))))
        self.max_bytes = max(1, int(os.getenv("VISION_MAX_IMAGE_BYTES", str(DEFAULT_MAX_BYTES))))
        self.retry_failed = retry_failed
        self.response_adapter_version = RESPONSE_ADAPTER_VERSION
        self.remote_policy_sha256 = remote_policy_sha256 or RemoteSamplePolicy().policy_sha256
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)
        self._calls_lock = threading.Lock()
        self._quota_billing_circuit_open = False
        self.calls = 0

    def configured(self) -> bool:
        """报告远程调用所需配置是否完整且非占位。"""
        api_key = get_secret("VISION_API_KEY") or ""
        base_url = os.getenv("VISION_BASE_URL", "")
        parsed = urlparse(base_url)
        hostname = (parsed.hostname or "").lower()
        return (
            self.model == REQUIRED_MODEL
            and parsed.scheme.lower() == "https"
            and (hostname == "wcode.net" or hostname.endswith(".wcode.net"))
            and all(value and "placeholder" not in value.lower() for value in (api_key, base_url))
        )

    def prepare_image(self, image_bytes: bytes) -> NormalizedImage:
        """真实解码图片并确定性转成受像素和字节上限约束的 RGB PNG。"""
        original_hash = sha256_bytes(image_bytes)
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                source.verify()
            with Image.open(io.BytesIO(image_bytes)) as source:
                source = ImageOps.exif_transpose(source)
                width, height = source.size
                if width < 1 or height < 1:
                    raise ValueError("empty image dimensions")
                if width * height > self.max_pixels:
                    scale = math.sqrt(self.max_pixels / (width * height))
                    source = source.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
                image = source.convert("RGB")
        except Exception as exc:
            raise ValueError("image_decode_failed") from exc
        resized = image.size != (width, height)
        payload = self._encode_png(image)
        while len(payload) > self.max_bytes and image.width > 1 and image.height > 1:
            scale = min(0.9, math.sqrt(self.max_bytes / len(payload)))
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
            payload = self._encode_png(image)
            resized = True
        if len(payload) > self.max_bytes:
            raise ValueError("image_byte_limit_exceeded")
        transformation = f"rgb_png:{width}x{height}->{image.width}x{image.height};resized={str(resized).lower()}"
        return NormalizedImage(payload, "image/png", image.width, image.height, original_hash, sha256_bytes(payload), len(image_bytes), transformation)

    @staticmethod
    def _encode_png(image: Image.Image) -> bytes:
        """把 RGB 图片确定性编码为 PNG。"""
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    def analyze(self, image_bytes: bytes, media_type: str, context: str = "", *, outbound_record: OutboundImageRecord | dict[str, Any] | None = None) -> VisionAnalysis:
        """只对 outbound manifest 中哈希完全匹配的图片执行远程分析。"""
        if not self.allow_remote_data:
            raise PermissionError("remote vision requires explicit allow_remote_data")
        if not self.configured():
            raise RuntimeError("WCode vision configuration is missing, invalid, or not approved")
        prepared = self.prepare_image(image_bytes)
        record = OutboundImageRecord.model_validate(outbound_record) if outbound_record is not None else None
        if record is None or not self._record_matches(record, prepared, context):
            raise PermissionError("image is not bound to the approved outbound manifest")
        key = sha256_bytes(canonical_json({"outbound": record.model_dump(mode="json"), "model": self.model, "prompt": PROMPT_VERSION, "response_adapter": self.response_adapter_version}).encode())
        cache_path = self.cache_dir / f"{key}.json"
        failure_path = self.cache_dir / f"{key}.failure.json"
        if cache_path.exists():
            analysis = VisionAnalysis.model_validate_json(cache_path.read_text(encoding="utf-8"))
            self._append_audit(None, "cache_hit", cache_path.name, 0, 0, None)
            return analysis
        if failure_path.exists() and not self.retry_failed:
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            raise RuntimeError(f"vision failure is already recorded: {failure.get('failure_type', 'unknown')}")
        with self._calls_lock:
            if self._quota_billing_circuit_open:
                raise VisionCircuitOpenError()
            if self.max_images is not None and self.calls >= self.max_images:
                raise RuntimeError("vision image budget exhausted")
            self.calls += 1
        payload = base64.b64encode(prepared.payload).decode("ascii")
        context = context[:2000]
        message = [{"type": "text", "text": _PROMPT + (f"\n邻近正文（仅用于消歧）：{context}" if context else "")}, {"type": "image_url", "image_url": {"url": f"data:{prepared.media_type};base64,{payload}"}}]
        client = OpenAI(api_key=require_secret("VISION_API_KEY"), base_url=os.environ["VISION_BASE_URL"], timeout=self.timeout)
        last_error: Exception | None = None
        failure_category = "unknown"
        request_count = 0
        fallback_attempted = False
        started = time.monotonic()
        with self._semaphore:
            for attempt in range(self.max_retries + 1):
                try:
                    self._raise_if_quota_billing_circuit_open()
                    request_count += 1
                    response = self._invoke(client, message, structured=True)
                    status = "success"
                except Exception as exc:
                    last_error = exc
                    failure_category = self._failure_category(exc)
                    if failure_category == "quota_billing":
                        self._trip_quota_billing_circuit()
                        break
                    if getattr(exc, "status_code", 0) not in {400, 422}:
                        if failure_category not in {"timeout", "connection", "rate_limited", "server_error"} or attempt >= self.max_retries:
                            break
                        time.sleep((2 ** attempt) + random.uniform(0, 0.25))
                        continue
                    try:
                        self._raise_if_quota_billing_circuit_open()
                        fallback_attempted = True
                        request_count += 1
                        response = self._invoke(client, message, structured=False)
                        status = "success_json_prompt_fallback"
                    except Exception as fallback_error:
                        last_error = fallback_error
                        failure_category = self._failure_category(fallback_error)
                        if failure_category == "quota_billing":
                            self._trip_quota_billing_circuit()
                            break
                        if self._retryable(failure_category) and attempt < self.max_retries:
                            time.sleep((2 ** attempt) + random.uniform(0, 0.25))
                            continue
                        break
                try:
                    analysis = self._parse_analysis(response.choices[0].message.content or "")
                except VisionResponseError:
                    try:
                        self._raise_if_quota_billing_circuit_open()
                        fallback_attempted = True
                        request_count += 1
                        response = self._invoke(client, message + [{"type": "text", "text": _REPAIR_PROMPT}], structured=False)
                        analysis = self._parse_analysis(response.choices[0].message.content or "")
                        status = "success_schema_repair"
                    except Exception as repair_error:
                        last_error = repair_error
                        failure_category = self._failure_category(repair_error)
                        if failure_category == "quota_billing":
                            self._trip_quota_billing_circuit()
                            break
                        if self._retryable(failure_category) and attempt < self.max_retries:
                            time.sleep((2 ** attempt) + random.uniform(0, 0.25))
                            continue
                        break
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
                    self._append_audit(response, status, cache_path.name, int((time.monotonic() - started) * 1000), max(0, request_count - 1), None)
                    return analysis
                except OSError as exc:
                    last_error = exc
                    failure_category = "cache_write_failed"
                    break
        self._record_failure(failure_path, last_error, failure_category)
        self._append_audit(None, "failed", cache_path.name, int((time.monotonic() - started) * 1000), max(0, request_count - 1), failure_category)
        failure = RuntimeError("vision analysis failed without persisting response content")
        failure.category = failure_category  # type: ignore[attr-defined]
        failure.fallback_attempted = fallback_attempted  # type: ignore[attr-defined]
        status_code = getattr(last_error, "status_code", None)
        if isinstance(status_code, int) and not isinstance(status_code, bool) and 100 <= status_code <= 599:
            failure.status_code = status_code  # type: ignore[attr-defined]
        raise failure from last_error

    def _record_matches(self, record: OutboundImageRecord, prepared: NormalizedImage, context: str) -> bool:
        """校验调用数据与已批准 outbound 记录完全一致。"""
        return (
            record.original_sha256 == prepared.original_sha256
            and record.normalized_sha256 == prepared.normalized_sha256
            and record.media_type == prepared.media_type
            and record.width == prepared.width
            and record.height == prepared.height
            and record.original_bytes == prepared.original_bytes
            and record.normalized_bytes == len(prepared.payload)
            and record.transformation == prepared.transformation
            and record.context_sha256 == sha256_bytes(context[:2000].encode("utf-8"))
            and record.model == self.model
            and record.prompt_version == PROMPT_VERSION
            and record.response_adapter_version == self.response_adapter_version
            and record.remote_policy_sha256 == self.remote_policy_sha256
        )

    @staticmethod
    def _parse_analysis(content: str) -> VisionAnalysis:
        """从普通或 fenced JSON 输出提取并严格校验 vision-v2。"""
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            candidate = "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else ""
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end < start:
            raise VisionResponseError("invalid_json")
        try:
            payload = json.loads(candidate[start:end + 1])
            if isinstance(payload, dict):
                payload = dict(payload)
                for field in ("ocr_text", "table_markdown", "formula_latex"):
                    if field not in payload:
                        continue
                    value = payload.get(field)
                    if isinstance(value, list) and all(isinstance(line, str) for line in value):
                        payload[field] = "\n".join(value)
                    elif value is None:
                        payload[field] = ""
                    elif not isinstance(value, str):
                        # 保持公式/表格的严格类型边界，未知对象交给 schema 拒绝。
                        continue
                for field in ("visible_facts", "entities"):
                    if field not in payload:
                        continue
                    value = payload.get(field)
                    if isinstance(value, list):
                        payload[field] = [
                            item if isinstance(item, str) else VisionAnalyzer._coerce_text(item)
                            for item in value
                        ]
                    elif isinstance(value, str):
                        payload[field] = [value] if value.strip() else []
                    elif value is None:
                        payload[field] = []
                    else:
                        payload[field] = [VisionAnalyzer._coerce_text(value)]
                uncertain_relations = payload.get("uncertain_relations")
                if isinstance(uncertain_relations, list):
                    normalized_relations = []
                    for relation in uncertain_relations:
                        source = relation.get("source") if isinstance(relation, dict) else None
                        target = relation.get("target") if isinstance(relation, dict) else None
                        condition = relation.get("condition") if isinstance(relation, dict) else None
                        if (
                            isinstance(relation, dict)
                            and set(relation) <= {"source", "target", "condition"}
                            and isinstance(source, str) and source.strip()
                            and isinstance(target, str) and target.strip()
                            and (condition is None or isinstance(condition, str))
                        ):
                            relation_text = f"{source.strip()} -> {target.strip()}"
                            if isinstance(condition, str) and condition.strip():
                                relation_text += f" ({condition.strip()})"
                            normalized_relations.append(relation_text)
                        else:
                            normalized_relations.append(relation)
                    payload["uncertain_relations"] = normalized_relations
            return VisionAnalysis.model_validate(payload)
        except ValidationError as exc:
            paths = {
                f"{'.'.join(str(part) for part in error.get('loc', ()))}:{error.get('type', 'validation_error')}"
                for error in exc.errors()
            }
            allowed_fields = {
                "content_kind", "ocr_text", "visible_facts", "summary", "entities",
                "table_markdown", "formula_latex", "directed_relations", "uncertain_relations",
                "confidence", "informative", "<root>",
            }
            safe_paths = sorted(
                path for path in paths if path.split(":", 1)[0].split(".", 1)[0] in allowed_fields
            )[:16]
            raise VisionResponseError("invalid_schema", validation_error_paths=safe_paths) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise VisionResponseError("invalid_schema") from exc

    @staticmethod
    def _coerce_text(value: Any) -> str:
        """将新模型的非字符串字段稳定保留为可检索文本。"""
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    @staticmethod
    def _failure_category(error: Exception) -> str:
        """把 SDK/协议异常归一化为不含正文的稳定类别。"""
        if isinstance(error, VisionResponseError):
            return error.category
        if isinstance(error, VisionCircuitOpenError):
            return error.category
        status = int(getattr(error, "status_code", 0) or 0)
        error_details = " ".join(
            str(value)
            for value in (
                type(error).__name__,
                str(error),
                getattr(error, "code", None),
                getattr(error, "error_code", None),
                getattr(error, "type", None),
                getattr(error, "message", None),
                getattr(error, "body", None),
            )
            if value is not None
        ).lower()
        quota_markers = (
            "insufficient_quota", "quota_exceeded", "quota exceeded", "quota exhausted", "out of quota",
            "billing", "payment required", "payment_required", "insufficient balance", "balance exceeded",
            "credits exhausted", "credit exhausted", "spending limit", "余额不足", "额度耗尽", "欠费", "计费",
        )
        if status == 402 or any(marker in error_details for marker in quota_markers) or (
            "quota" in error_details and any(term in error_details for term in ("exceed", "exhaust", "insufficient", "limit"))
        ):
            return "quota_billing"
        if status == 408:
            return "timeout"
        if status == 429:
            return "rate_limited"
        if status >= 500:
            return "server_error"
        if status in {400, 401, 403, 404, 422}:
            return f"http_{status}"
        name = type(error).__name__.lower()
        if "timeout" in name:
            return "timeout"
        if "connection" in name:
            return "connection"
        return "unexpected_error"

    @staticmethod
    def _retryable(category: str) -> bool:
        """仅网络、限流、超时和服务端错误允许重试。"""
        return category in {"timeout", "connection", "rate_limited", "server_error"}

    def _raise_if_quota_billing_circuit_open(self) -> None:
        """熔断打开后不再发起结构化、fallback 或修复请求。"""
        with self._calls_lock:
            if self._quota_billing_circuit_open:
                raise VisionCircuitOpenError()

    def _trip_quota_billing_circuit(self) -> None:
        """记录本次分析运行的 quota/billing 熔断状态。"""
        with self._calls_lock:
            self._quota_billing_circuit_open = True

    def _invoke(self, client: OpenAI, message: list[dict[str, Any]], *, structured: bool) -> Any:
        """用结构化输出或兼容 JSON Prompt 请求一次完整响应。"""
        kwargs: dict[str, Any] = {"model": self.model, "messages": [{"role": "user", "content": message}], "stream": False}
        if structured:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    def _append_audit(self, response: Any | None, status: str, cache_key: str, latency_ms: int, retry_count: int, failure_category: str | None) -> None:
        """追加不含图片、Prompt、响应正文与密钥的调用审计记录。"""
        usage = getattr(response, "usage", None)
        record = {"model": self.model, "status": status, "cache_key": cache_key, "request_id": getattr(response, "_request_id", None), "prompt_tokens": getattr(usage, "prompt_tokens", None), "completion_tokens": getattr(usage, "completion_tokens", None), "latency_ms": latency_ms, "retry_count": retry_count, "failure_category": failure_category}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with (self.cache_dir / "vision_audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _record_failure(self, failure_path: Path, error: Exception | None, failure_category: str) -> None:
        """持久化可复用的脱敏失败状态，不保存响应或异常正文。"""
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"status": "failed", "failure_type": type(error).__name__ if error else "UnknownError", "failure_category": failure_category, "model": self.model, "prompt_version": PROMPT_VERSION}
        if isinstance(error, VisionResponseError) and error.validation_error_paths:
            payload["validation_error_paths"] = list(error.validation_error_paths)
        failure_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
