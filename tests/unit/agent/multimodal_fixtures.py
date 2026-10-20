"""多模态契约测试共用的本地 fixture 与常量。

文档职责：集中生成合法 PNG、vision-v2 fake 响应和出站图片记录，避免各职责测试
各自复制同一套图像与哈希规则。

适用范围：tests/unit/agent 下拆分后的多模态解析、视觉、索引与发布测试；不包含
被测生产逻辑，也不访问网络。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from Agent.knowledge_base.multimodal.contracts import (
    OutboundImageRecord,
    sha256_bytes,
)
from Agent.knowledge_base.multimodal.vision import (
    PROMPT_VERSION,
    RESPONSE_ADAPTER_VERSION,
    VisionAnalyzer,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TEST_LOCAL_EMBEDDING = {
    "mode": "local",
    "provider": "huggingface",
    "model": "test-local-embedding",
    "dimension": 512,
    "normalized": True,
    "path": "test-local-embedding",
}


def vision_payload(**overrides: object) -> str:
    """生成完整的 vision-v2 fake 响应。"""
    payload = {
        "content_kind": "chart",
        "ocr_text": "图中文字",
        "visible_facts": ["存在坐标轴"],
        "summary": "图表摘要",
        "entities": ["A"],
        "table_markdown": "",
        "formula_latex": "",
        "directed_relations": [],
        "uncertain_relations": [],
        "confidence": 0.8,
        "informative": True,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def png_bytes(color: str = "white", size: tuple[int, int] = (8, 8)) -> bytes:
    """生成无需外部 fixture 的有效 PNG。"""
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def outbound_record(
    analyzer: VisionAnalyzer,
    payload: bytes,
    context: str = "",
    image_index: int = 1,
    *,
    source_relative_path: str = "source.pdf",
    source_sha256: str = "a" * 64,
    document_id: str = "doc_" + "b" * 64,
    page_number: int = 1,
) -> OutboundImageRecord:
    """按 adapter 的归一化结果生成 fake outbound 记录。"""
    prepared = analyzer.prepare_image(payload)
    return OutboundImageRecord(
        source_relative_path=source_relative_path,
        source_sha256=source_sha256,
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
        context_sha256=sha256_bytes(context[:2000].encode("utf-8")),
        provider="wcode",
        model=analyzer.model,
        prompt_version=PROMPT_VERSION,
        response_adapter_version=RESPONSE_ADAPTER_VERSION,
        remote_policy_sha256=analyzer.remote_policy_sha256,
    )
