"""数据处理方案的稳定身份与确认回答校验。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


PLAN_CONFIRMATION_INTERACTION_TYPE = "data_processing_plan_confirmation"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _validated_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value.lower()) is None:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return value.lower()


def build_processing_plan_version(
    *,
    job_id: str,
    source_hash: str,
    semantic_identity: str,
    metadata: dict[str, Any],
) -> str:
    """绑定 Job、冻结文件、研究语义和经确认的转换说明。"""
    if not isinstance(job_id, str) or not job_id or job_id != job_id.strip():
        raise ValueError("job_id must be a non-blank string")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")

    payload = {
        "schema": "causalagent-data-processing-plan-v2",
        "job_id": job_id,
        "source_hash": _validated_sha256(source_hash, field_name="source_hash"),
        "semantic_identity": _validated_sha256(
            semantic_identity, field_name="semantic_identity"
        ),
        "metadata": metadata,
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("plan contains invalid JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()


def extend_data_processing_semantic_identity(
    semantic_identity: str,
    answer: Any,
) -> str:
    """把用户的语义答复并入方案身份，只保存答复摘要而不重复保存正文。"""
    previous = _validated_sha256(
        semantic_identity, field_name="semantic_identity"
    )
    answer_value = _answer_object(answer)
    try:
        encoded = json.dumps(
            {"previous": previous, "answer": answer_value},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic answer contains invalid JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()


def _answer_object(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
        return parsed
    return value


def plan_confirmation_version(value: Any) -> str | None:
    """从已保存或刚提交的恢复输入中读取明确确认的方案版本。"""
    answer = _answer_object(value)
    if not isinstance(answer, Mapping) or answer.get("action") != "confirm":
        return None
    version = answer.get("plan_version")
    if not isinstance(version, str) or _SHA256_PATTERN.fullmatch(version.lower()) is None:
        return None
    return version.lower()


def is_plan_confirmation_action(value: Any) -> bool:
    """判断回答是否试图确认方案，即使其版本字段无效也返回 True。"""
    answer = _answer_object(value)
    return isinstance(answer, Mapping) and answer.get("action") == "confirm"


def plan_confirmation_matches(interaction: Any, answer: Any) -> bool | None:
    """返回确认是否匹配；非方案确认或普通文字回答返回 None。"""
    if not isinstance(interaction, Mapping):
        return None
    if interaction.get("interaction_type") != PLAN_CONFIRMATION_INTERACTION_TYPE:
        return None
    if not is_plan_confirmation_action(answer):
        return None
    expected = interaction.get("plan_version")
    supplied = plan_confirmation_version(answer)
    if not isinstance(expected, str) or _SHA256_PATTERN.fullmatch(expected.lower()) is None:
        return False
    return supplied == expected.lower()
