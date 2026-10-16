"""隔离评测知识源摄取、候选题生成与 staged index RAG 查询任务。

本文件负责运行目录状态、跨进程事件、任务排队和结果产物的生命周期；长任务
由独立 worker 执行，Web 进程只负责创建、读取和取消。
"""

from __future__ import annotations

import contextlib
import json
import hashlib
from io import BytesIO
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from werkzeug.utils import secure_filename

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from Agent.knowledge_base.embedding_runtime import (
    EmbeddingConfiguration,
    resolve_production_embedding_configuration,
    validate_embedding_env_references,
)
from Agent.knowledge_base.multimodal.defaults import resolve_production_source_metadata
from Agent.knowledge_base.multimodal.contracts import canonical_json, content_source_id
from Agent.knowledge_base.multimodal.index import embedding_fingerprint, replace_with_retry
from Agent.knowledge_base.multimodal.parsers import SUPPORTED_SUFFIXES, inspect_source
from Agent.knowledge_base.multimodal.pipeline import MultimodalKnowledgeBaseMaintenance
from Agent.knowledge_base.multimodal.release import compute_manifest_sha256, embedding_config_from_manifest, seal_evaluation_binding
from Agent.knowledge_base.rag_runtime import RagRuntimeConfig, create_rag_runtime
from Agent.knowledge_base.rag_service import RagService
from Agent.knowledge_base.rag.operation_datasets.index_bound_tuning import (
    TUNING_METRICS,
    build_ledger,
    question_hash,
)
from Agent.knowledge_base.query_rag import (
    PRODUCTION_RAG_CONFIG_PATH,
    _normalize_question_payload,
)
from config.settings import settings
from config.rag_eval_paths import (
    RAG_EVAL_ISOLATED_RUN_ROOT,
    RAG_EVAL_SOURCE_ROOT,
    RAG_EVAL_TUNING_DATASET_ROOT,
)
from app.rag_eval.index_binding import IndexBindingGate, IndexIdentity


ISOLATED_RUN_ROOT = RAG_EVAL_ISOLATED_RUN_ROOT
TUNING_DATASET_ROOT = RAG_EVAL_TUNING_DATASET_ROOT
_SOURCE_METADATA_LOCK = threading.RLock()
_RUN_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{8,80}$")
_MAX_EVENTS = 500
_MAX_SOURCES = 20
_MAX_QUESTIONS = 100
try:
    _EVALUATION_STALE_AFTER_SECONDS = max(
        int(os.getenv("RAG_EVAL_EVALUATION_STALE_AFTER_SECONDS", "1800")),
        60,
    )
except ValueError:
    _EVALUATION_STALE_AFTER_SECONDS = 1800
_ACTIVE_EVALUATION_STATUSES = {"created", "queued", "running", "cancelling"}
_ACTIVE_INGESTION_STATUSES = {"created", "queued", "running", "cancelling"}
_TERMINAL_RUN_STATUSES = {"staged", "succeeded", "cancelled", "failed"}
_DELETABLE_DERIVED_KINDS = {
    "rag_query",
    "candidate_generation",
    "dataset_governance",
    "tuning_dataset_governance",
}
_CASCADE_RUN_KINDS = _DELETABLE_DERIVED_KINDS | {"evaluation"}
_PERSISTENT_WORKER_KINDS = {"ingestion", "candidate_generation", "rag_query", "evaluation", "dataset_governance", "tuning_dataset_governance"}
_GOVERNANCE_CANDIDATE_BATCH_SIZE = 8
_GOVERNANCE_CANDIDATE_MAX_ATTEMPTS = 3


class ReleaseGateError(ValueError):
    """正式 release 门禁未通过或发布前置条件已变化。"""

    def __init__(self, message: str, report: Dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report

# 单测和离线验收可注入确定性实现；生产默认路径仍使用结构化模型审核。
GOVERNANCE_REVIEWER = None
GOVERNANCE_CANDIDATE_PROVIDER = None
# 可注入的替补候选改写器；契约与 calibrated_candidate_audit.Rewriter 一致。
GOVERNANCE_CANDIDATE_REWRITER = None


def _staged_embedding_config(index_dir: Path) -> dict[str, Any]:
    """返回 staged manifest 的完整 embedding 配置供隔离绑定比较。"""
    manifest = _read_json(Path(index_dir) / "manifest.json")
    configured = manifest.get("embedding_config")
    if isinstance(configured, dict):
        return dict(configured)
    legacy = manifest.get("embedding")
    if isinstance(legacy, dict):
        return dict(legacy)
    raise ValueError("staged index embedding configuration is missing")


def _remote_data_enabled() -> bool:
    """读取远程视觉能力开关；用户授权仍由每次摄取的来源集合决定。"""
    return os.getenv("VISION_ALLOW_REMOTE_DATA", "false").strip().lower() in {"1", "true", "yes", "on"}


def _remote_data_enabled_for_sources(source_paths: List[Path]) -> bool:
    """内测环境中，远程开关开启时允许所有显式选中的隔离来源外发。

    调用方仍必须显式选源，且摄取层会生成 outbound manifest。设置
    ``VISION_ALLOW_REMOTE_DATA=false`` 会在所有来源类型上关闭远程视觉。
    正式环境必须在上线前替换为独立的来源授权策略。
    """
    return bool(source_paths) and _remote_data_enabled()


def _timestamp() -> str:
    """生成可展示的本地时间戳。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_run_id(prefix: str) -> str:
    """生成不会命中旧 staged 版本的运行身份。"""
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"


def _safe_run_id(run_id: str) -> str:
    """校验外部传入的运行身份，阻止路径逃逸。"""
    value = str(run_id or "").strip()
    if not _RUN_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid isolated run id")
    return value


def _run_dir(run_id: str) -> Path:
    """返回隔离运行目录，并确认它仍位于固定根目录内。"""
    safe_id = _safe_run_id(run_id)
    target = (ISOLATED_RUN_ROOT / safe_id).resolve()
    target.relative_to(ISOLATED_RUN_ROOT)
    return target


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    """原子写入运行状态或结果文件，并吸收 Windows 短暂文件占用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    replace_with_retry(temporary, path)


def _dataset_content_sha256(payload: Dict[str, Any]) -> str:
    """返回 rag_eval_v1 题集的格式无关内容指纹。"""
    from app.rag_eval.isolated_evaluation import normalize_dataset_payload

    canonical_dataset, _ = normalize_dataset_payload(payload)
    serialized = json.dumps(
        canonical_dataset,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _is_retryable_governance_generation_error(exc: Exception) -> bool:
    """只重试未生成任何题目的短暂候选生成连接失败。"""
    message = str(exc)
    return (
        "no candidates passed quality screening" in message
        and "generated=0, rejected=0, generation_errors=" in message
        and "generation_errors=0" not in message
    )


@contextlib.contextmanager
def _run_file_lock(run_id: str):
    """跨进程文件锁，串行化 app 与 worker 对 run.json 的读-改-写。

    线程级 `threading.RLock` 只在单进程内有效；而 app 与 rag-eval-worker 分属
    不同进程，却都对共享卷中的 run.json 执行「读取—修改—原子替换」。没有跨进程
    锁时，取消、进度事件和终态并发写入会互相覆盖，丢失 cancel_requested、event_seq
    或最终状态。这里用操作系统文件锁把整个读-改-写变为临界区。
    """
    lock_path = _run_dir(run_id) / "run.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _path_file_lock(lock_path: Path):
    """对共享文件的发布临界区使用跨进程独占锁。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _json_default(value: Any) -> Any:
    """将检索 trace 中的集合稳定转换为 JSON 数组。"""
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _read_json(path: Path) -> Dict[str, Any]:
    """读取隔离运行目录中的 JSON；缺失或损坏时返回空对象。"""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_contains_value(payload: Any, values: set[str]) -> bool:
    """在已读取的 JSON 结构中查找明确的运行或题集身份。"""
    if isinstance(payload, dict):
        return any(_json_contains_value(value, values) for value in payload.values())
    if isinstance(payload, list):
        return any(_json_contains_value(value, values) for value in payload)
    return isinstance(payload, str) and payload in values


def _read_reference_json(path: Path) -> Any:
    """读取删除门禁依赖的 JSON；存在但损坏时必须 fail closed。"""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"引用门禁文件损坏，暂不能删除: {path.name}") from exc


def _default_governance_reviewer(sample: Dict[str, Any], *, purpose: str) -> Dict[str, Any]:
    """调用独立结构化审核器；调用/解析失败由上层治理逻辑 fail closed。"""
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel, Field
    from typing import Literal

    from Agent.llm_structured_output import invoke_structured

    class GovernanceReview(BaseModel):
        verdict: Literal["replace", "retain", "accept", "reject"]
        confidence: float = Field(ge=0.0, le=1.0)
        reason: str = Field(min_length=1, max_length=1000)

    llm = ChatOpenAI(
        api_key=settings.API_KEY,
        base_url=settings.BASE_URL,
        model=settings.MODEL,
        temperature=0,
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是 RAG Gold 题集治理审核器。只能根据题目、参考答案、声明证据和结构风险做判断；审核失败时调用方会保留原题。purpose=retire 时只有确定题目本身有问题才返回 replace；purpose=accept 时只有新候选完整、可追溯且不重复才返回 accept。confidence 必须反映确定性。"),
        ("human", "purpose={purpose}\nsample={sample}"),
    ])
    result = invoke_structured(
        llm=llm,
        schema=GovernanceReview,
        prompt=prompt,
        inputs={"purpose": purpose, "sample": json.dumps(sample, ensure_ascii=False, sort_keys=True)},
        node_name="rag_dataset_governance",
    )
    return result.model_dump()


def _read_source_display_names() -> Dict[str, str]:
    """读取本地来源显示名；来源身份仍由 source_id/content hash 决定。"""
    payload = _read_json(RAG_EVAL_SOURCE_ROOT / "source_metadata.json")
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return {}
    return {
        str(source_id): str(value.get("display_name") or "").strip()
        for source_id, value in sources.items()
        if isinstance(value, dict) and str(value.get("display_name") or "").strip()
    }


def _write_source_display_names(display_names: Dict[str, str]) -> None:
    """原子保存来源显示名元数据，不触碰来源文件和隔离运行产物。"""
    with _SOURCE_METADATA_LOCK:
        _write_json(
            RAG_EVAL_SOURCE_ROOT / "source_metadata.json",
            {
                "schema_version": "rag_eval_source_metadata_v1",
                "sources": {
                    source_id: {"display_name": display_name}
                    for source_id, display_name in sorted(display_names.items())
                    if display_name
                },
            },
        )


def _validate_source_display_name(value: Any) -> str:
    """校验用户可见来源名称，避免把空白或超长内容写入目录元数据。"""
    display_name = str(value or "").strip()
    if not 1 <= len(display_name) <= 120:
        raise ValueError("display_name must contain 1 to 120 characters")
    return display_name


def _public_dataset_identity(identity: Dict[str, Any]) -> Dict[str, Any]:
    """返回题集身份，但不把隔离目录的宿主机路径暴露给 API。"""
    result = dict(identity or {})
    result.pop("dataset_path", None)
    return result


def _parse_timestamp(value: str) -> datetime:
    """解析前端 ISO 时间，并兼容 Python 3.10 对 Z 后缀的限制。"""
    normalized = str(value).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def _evaluation_last_activity(state: Dict[str, Any]) -> datetime | None:
    """读取评测最近活动时间，兼容旧 run.json 的事件记录。"""
    candidates: List[Any] = [state.get("last_activity_at")]
    if state.get("execution_backend") == "persistent_worker":
        heartbeat_path = _run_dir(str(state.get("run_id") or "")) / "worker_heartbeat.json"
        heartbeat = _read_json(heartbeat_path)
        candidates.insert(0, heartbeat.get("heartbeat_at"))
    events = state.get("events")
    if isinstance(events, list) and events:
        last_event = events[-1]
        if isinstance(last_event, dict):
            candidates.append(last_event.get("timestamp"))
    candidates.extend([state.get("started_at"), state.get("created_at")])
    for value in candidates:
        if not value:
            continue
        try:
            return _parse_timestamp(str(value))
        except (TypeError, ValueError):
            continue
    return None


def _is_stale_evaluation(state: Dict[str, Any], now: datetime | None = None) -> bool:
    """判断运行中评测是否超过无事件活动窗口。"""
    if (
        state.get("kind") not in _PERSISTENT_WORKER_KINDS
        or state.get("execution_backend") != "persistent_worker"
        or state.get("status") not in _ACTIVE_EVALUATION_STATUSES
    ):
        return False
    last_activity = _evaluation_last_activity(state)
    if last_activity is None:
        return False
    current = now or datetime.now().astimezone()
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=current.tzinfo)
    return current - last_activity >= timedelta(seconds=_EVALUATION_STALE_AFTER_SECONDS)


def _evaluation_history_record(run_dir: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    """从单次隔离评测目录构造可供 history 和前端比较的摘要。"""
    summary = _read_json(run_dir / "summary.json")
    manifest = _read_json(run_dir / "run_manifest.json")
    config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    retrieval = config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
    ragas = config.get("ragas") if isinstance(config.get("ragas"), dict) else {}
    identity = summary.get("dataset_identity") or state.get("input_identity") or {}
    ingestion_state = _read_json(_run_dir(str(state.get("ingestion_run_id") or "")) / "run.json") if state.get("ingestion_run_id") else {}
    source_ids = ingestion_state.get("source_ids", [])
    source_names = ingestion_state.get("source_display_names") or ingestion_state.get("source_names", [])
    return {
        "run_id": state.get("run_id", run_dir.name),
        "kind": "evaluation",
        "status": summary.get("status", state.get("status", "unknown")),
        "status_reason": summary.get("status_reason", ""),
        "created_at": state.get("created_at", ""),
        "started_at": state.get("started_at", ""),
        "finished_at": state.get("finished_at", ""),
        "stale": _is_stale_evaluation(state),
        "ingestion_run_id": summary.get("ingestion_run_id", state.get("ingestion_run_id", "")),
        "index_version": summary.get("index_version", state.get("index_version", "")),
        "source_ids": source_ids if isinstance(source_ids, list) else [],
        "source_names": source_names if isinstance(source_names, list) else [],
        "source_label": ingestion_state.get("source_label") or ("、".join(str(item) for item in source_names) if isinstance(source_names, list) else ""),
        "question_count": identity.get("sample_count", state.get("question_count", 0)),
        "dataset_identity": _public_dataset_identity(identity),
        "config_identity": summary.get("config_identity", manifest.get("config_identity", "")),
        "strategy": {
            "profile_id": (
                (config.get("strategy_profile") or {}).get("profile_id", "")
                if isinstance(config.get("strategy_profile"), dict)
                else ""
            ),
            "profile_name": (
                (config.get("strategy_profile") or {}).get("name", "")
                if isinstance(config.get("strategy_profile"), dict)
                else ""
            ),
            "retrieval_profile": retrieval.get("profile", ""),
            "retrieval_overrides": retrieval.get("overrides", {}),
            "ragas_profile": ragas.get("profile", ""),
            "judge_profile": ragas.get("judge_profile", ""),
            "metrics": ragas.get("selected_metrics", []),
        },
        "key_metrics": summary.get("key_metrics", {}),
        "steps": summary.get("steps", []),
        "error": state.get("error", ""),
    }


def _is_number(value: Any) -> bool:
    """判断值是否可安全参与评测指标差值计算。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _metric_deltas(base: Dict[str, Any], candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    """对两个指标对象做通用数值 diff，保留未评分的 null。"""
    rows = []
    for name in sorted(set(base or {}) | set(candidate or {})):
        base_value = (base or {}).get(name)
        candidate_value = (candidate or {}).get(name)
        rows.append({
            "metric": name,
            "base": base_value,
            "candidate": candidate_value,
            "delta": round(float(candidate_value) - float(base_value), 4)
            if _is_number(base_value) and _is_number(candidate_value)
            else None,
        })
    return rows


def _sample_result_map(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    """按通用 sample_id 聚合单次运行的 retrieval 与 Ragas 逐题结果。"""
    retrieval = _read_json(run_dir / "machine" / "rag_eval_result.json")
    ragas = _read_json(run_dir / "machine" / "ragas_eval_result.json")
    rows: Dict[str, Dict[str, Any]] = {}

    for detail in retrieval.get("details", []):
        if not isinstance(detail, dict):
            continue
        key = str(detail.get("sample_id") or detail.get("question") or "")
        if not key:
            continue
        rows.setdefault(key, {"sample_id": key, "question": detail.get("question", "")})["retrieval"] = {
            "recall": detail.get("recall"),
            "reciprocal_rank": detail.get("reciprocal_rank"),
            "match_mode": detail.get("retrieval_match_mode", ""),
        }

    metadata = ragas.get("metadata", [])
    records = ragas.get("score_records", [])
    bad_cases = {
        str(case.get("question") or ""): case
        for case in (ragas.get("cross_metric_bad_cases", {}) or {}).get("cases", [])
        if isinstance(case, dict)
    }
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        meta = metadata[index] if index < len(metadata) and isinstance(metadata[index], dict) else {}
        key = str(meta.get("sample_id") or meta.get("question") or record.get("user_input") or "")
        if not key:
            continue
        question = str(meta.get("question") or record.get("user_input") or "")
        scores = {name: value for name, value in record.items() if _is_number(value)}
        rows.setdefault(key, {"sample_id": key, "question": question})["ragas"] = scores
        if question in bad_cases:
            rows[key]["bad_case"] = bad_cases[question]
    return rows


_TUNING_LEDGER_SOURCE_KINDS = {"evaluation", "tuning_round"}


def _sample_generated(sample: Dict[str, Any]) -> bool:
    """判断样本是否为自动生成题；人工保护题没有 generator 字段。"""
    return bool(str((sample.get("source") or {}).get("generator") or "").strip())


def _sample_locator_keys(samples: List[Dict[str, Any]]) -> set:
    """提取样本 gold_evidence 的全部定位键变体，用于替补生成的排除与优先。

    一个 locator 可能同时携带 unit、asset、页面多个维度；单元记录恒有
    unit_id，因此匹配必须按键集合求交，而不是单键严格优先。
    """
    keys: set = set()
    for sample in samples:
        for locator in sample.get("gold_evidence") or []:
            if not isinstance(locator, dict):
                continue
            unit_id = str(locator.get("unit_id") or "").strip()
            if unit_id:
                keys.add(("unit", unit_id))
            asset_uri = str(locator.get("asset_uri") or "").strip()
            if asset_uri:
                keys.add(("asset", asset_uri))
            document_id = str(locator.get("document_id") or "").strip()
            page_number = locator.get("page_number")
            if document_id and page_number not in (None, ""):
                keys.add(("page", document_id, str(page_number)))
            elif document_id:
                keys.add(("doc", document_id))
    return keys


def _unit_record_keys(record: Dict[str, Any]) -> set:
    """返回 staged 单元记录的全部定位键，与 locator 键变体按交集匹配。"""
    metadata = dict(record.get("metadata") or {})
    keys: set = set()
    unit_id = str(record.get("unit_id") or metadata.get("unit_id") or "").strip()
    if unit_id:
        keys.add(("unit", unit_id))
    asset_uri = str(metadata.get("asset_uri") or "").strip()
    if asset_uri:
        keys.add(("asset", asset_uri))
    document_id = str(metadata.get("document_id") or "").strip()
    page_number = metadata.get("page_number")
    if document_id and page_number not in (None, 0, ""):
        keys.add(("page", document_id, str(page_number)))
    elif document_id:
        keys.add(("doc", document_id))
    return keys


def _prioritize_unit_records(records: List[Dict[str, Any]], priority_keys: set) -> List[Dict[str, Any]]:
    """把命中优先定位键的单元稳定前移，保留原有顺序以维持模态轮询多样性。"""
    if not priority_keys:
        return records
    matched = [record for record in records if _unit_record_keys(record) & priority_keys]
    if not matched:
        return records
    matched_ids = {id(record) for record in matched}
    return matched + [record for record in records if id(record) not in matched_ids]


def _tuning_evidence_from_artifacts(
    retrieval_payload: Dict[str, Any],
    ragas_payload: Dict[str, Any],
    *,
    source_run_id: str,
    source_kind: str,
    source_round: int,
    recorded_at: str,
) -> List[Dict[str, Any]]:
    """从一次评测的机器产物提取逐题证据记录；缺 sample_id 的记录直接丢弃。"""
    retrieval_by_sid: Dict[str, Dict[str, Any]] = {}
    for detail in retrieval_payload.get("details") or []:
        if not isinstance(detail, dict):
            continue
        sample_id = str(detail.get("sample_id") or "").strip()
        if sample_id:
            retrieval_by_sid[sample_id] = {
                "recall": detail.get("recall"),
                "reciprocal_rank": detail.get("reciprocal_rank"),
            }
    config = retrieval_payload.get("config")
    config_sha256 = ""
    if isinstance(config, dict) and config:
        config_sha256 = hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:16]
    metadata = ragas_payload.get("metadata") if isinstance(ragas_payload.get("metadata"), list) else []
    ragas_profile = str(
        ragas_payload.get("active_profile")
        or ragas_payload.get("profile")
        or ""
    )
    records: List[Dict[str, Any]] = []
    for position, raw_record in enumerate(ragas_payload.get("score_records") or []):
        if not isinstance(raw_record, dict):
            continue
        meta = metadata[position] if position < len(metadata) and isinstance(metadata[position], dict) else {}
        sample_id = str(meta.get("sample_id") or "").strip()
        if not sample_id:
            continue
        question_text = str(meta.get("question") or raw_record.get("user_input") or "")
        record = {
            "sample_id": sample_id,
            "question_hash": question_hash(question_text),
            **{metric: raw_record.get(metric) for metric in TUNING_METRICS},
            "recall": retrieval_by_sid.get(sample_id, {}).get("recall"),
            "reciprocal_rank": retrieval_by_sid.get(sample_id, {}).get("reciprocal_rank"),
            "source_run_id": source_run_id,
            "source_kind": source_kind,
            "source_round": int(source_round or 0),
            "recorded_at": recorded_at,
            "retrieval_config_sha256": config_sha256,
            "ragas_profile": ragas_profile,
            "judge_profile": str(ragas_payload.get("judge_profile") or ""),
            "ragas_version": str(ragas_payload.get("ragas_version") or ""),
        }
        records.append(record)
    return records


def _iter_tuning_ledger_candidates(
    ingestion_run_id: str,
    index_version: str,
    *,
    exclude_run_id: str = "",
) -> List[tuple[str, Path, Dict[str, Any]]]:
    """收集同索引下可作为证据来源的历史运行，按完成时间升序排列。"""
    candidates: List[tuple[str, Path, Dict[str, Any]]] = []
    if not ISOLATED_RUN_ROOT.is_dir():
        return candidates
    for child in ISOLATED_RUN_ROOT.iterdir():
        if not child.is_dir():
            continue
        try:
            state = _read_json(child / "run.json")
        except Exception:
            continue
        kind = str(state.get("kind") or "")
        if kind not in {"evaluation", "tuning_dataset_governance"}:
            continue
        if exclude_run_id and child.name == exclude_run_id:
            continue
        if str(state.get("ingestion_run_id") or "") != ingestion_run_id:
            continue
        if str(state.get("index_version") or "") != index_version:
            continue
        finished_at = str(state.get("finished_at") or state.get("created_at") or "")
        candidates.append((finished_at, child, state))
    candidates.sort(key=lambda entry: (entry[0], entry[1].name))
    return candidates


def collect_tuning_question_ledger(
    ingestion_run_id: str,
    index_version: str,
    *,
    exclude_run_id: str = "",
) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
    """扫描同索引历史评测产物构建逐题证据账本。

    宽松语义：不比较检索或 judge 配置，只要求题目身份（sample_id + 题面哈希）
    一致；配置摘要随每条记录保存供结果展示。单个产物解析失败只跳过该产物。
    """
    chunks: List[List[Dict[str, Any]]] = []
    for finished_at, run_path, state in _iter_tuning_ledger_candidates(
        ingestion_run_id, index_version, exclude_run_id=exclude_run_id
    ):
        run_id = run_path.name
        kind = str(state.get("kind") or "")
        if kind == "evaluation":
            if state.get("status") != "succeeded":
                continue
            machine_dir = run_path / "machine"
            if not (machine_dir / "ragas_eval_result.json").is_file():
                continue
            try:
                retrieval_payload = _read_json(machine_dir / "rag_eval_result.json")
                ragas_payload = _read_json(machine_dir / "ragas_eval_result.json")
            except Exception:
                continue
            chunks.append(_tuning_evidence_from_artifacts(
                retrieval_payload, ragas_payload,
                source_run_id=run_id, source_kind="evaluation",
                source_round=0, recorded_at=finished_at,
            ))
            continue
        for round_dir in sorted(run_path.glob("round_*")):
            match = re.fullmatch(r"round_(\d+)", round_dir.name)
            machine_dir = round_dir / "machine"
            if not match or not (machine_dir / "ragas_eval_result.json").is_file():
                continue
            try:
                retrieval_payload = _read_json(machine_dir / "rag_eval_result.json")
                ragas_payload = _read_json(machine_dir / "ragas_eval_result.json")
            except Exception:
                continue
            chunks.append(_tuning_evidence_from_artifacts(
                retrieval_payload, ragas_payload,
                source_run_id=run_id, source_kind="tuning_round",
                source_round=int(match.group(1)), recorded_at=state.get("finished_at") or finished_at,
            ))
    flat_records = [record for chunk in chunks for record in chunk]
    ledger = build_ledger(flat_records)
    source_runs = sorted({str(record.get("source_run_id") or "") for record in flat_records} - {""})
    return ledger, source_runs


def _collect_rescued_generated_samples(
    ingestion_run_id: str,
    index_version: str,
    *,
    exclude_run_id: str = "",
) -> Dict[str, Dict[str, Any]]:
    """收集历史 tuning 轮次快照中的自动生成题全文样本，按 sample_id 取最新。

    只读各轮次 dataset_snapshot.json；样本是否可沿用由调用方结合证据账本
    与门槛判定。按完成时间升序遍历，后写覆盖先写。
    """
    collected: Dict[str, Dict[str, Any]] = {}
    for _finished_at, run_path, state in _iter_tuning_ledger_candidates(
        ingestion_run_id, index_version, exclude_run_id=exclude_run_id
    ):
        if str(state.get("kind") or "") != "tuning_dataset_governance":
            continue
        for round_dir in sorted(run_path.glob("round_*"), key=lambda item: item.name):
            snapshot_path = round_dir / "dataset_snapshot.json"
            if not snapshot_path.is_file():
                continue
            try:
                snapshot = _read_json(snapshot_path)
            except Exception:
                continue
            for sample in snapshot.get("samples") or []:
                if not isinstance(sample, dict) or not _sample_generated(sample):
                    continue
                sample_id = str(sample.get("sample_id") or "").strip()
                if sample_id:
                    collected[sample_id] = dict(sample)
    return collected


def _latest_registered_tuning_baseline(index_version: str) -> tuple[Dict[str, Any] | None, List[str]]:
    """返回同索引最近登记且可解析的调参集；损坏文件按新旧顺序跳过并记录原因。"""
    errors: List[str] = []
    root = TUNING_DATASET_ROOT / str(index_version)
    if not root.is_dir():
        return None, errors
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = _read_json(path)
            samples = payload.get("samples")
            if not isinstance(samples, list) or not any(_sample_generated(sample) for sample in samples):
                raise ValueError("registered dataset has no governable generated samples")
            return payload, errors
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return None, errors


def _config_deltas(base: Dict[str, Any], candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按路径比较 retrieval/Ragas 配置，避免绑定任何题集字段。"""
    def flatten(value: Any, prefix: str = "") -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {prefix: value}
        result: Dict[str, Any] = {}
        for key, child in value.items():
            result.update(flatten(child, f"{prefix}.{key}" if prefix else str(key)))
        return result

    base_flat = flatten(base)
    candidate_flat = flatten(candidate)
    return [
        {"field": name, "base": base_flat.get(name), "candidate": candidate_flat.get(name)}
        for name in sorted(set(base_flat) | set(candidate_flat))
        if base_flat.get(name) != candidate_flat.get(name)
    ]


def _pdf_page_count(content: bytes) -> int | None:
    """读取 PDF 页数；上传目录中的损坏文件只返回未知页数，不阻塞目录展示。"""
    try:
        from pypdf import PdfReader

        return len(PdfReader(BytesIO(content)).pages)
    except Exception:
        return None


def _uploaded_source_catalog_records() -> List[tuple[Path, Dict[str, Any]]]:
    """读取独立上传目录中的用户来源，不扫描其它运行产物目录。"""
    if not RAG_EVAL_SOURCE_ROOT.is_dir():
        return []

    display_names = _read_source_display_names()
    catalog: List[tuple[Path, Dict[str, Any]]] = []
    for path in sorted(RAG_EVAL_SOURCE_ROOT.glob("upload_*__*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        content_hash = hashlib.sha256(content).hexdigest()
        source_id = f"upload_{content_hash[:24]}"
        _, display_name = path.name.split("__", 1)
        catalog.append((path, {
            "source_id": source_id,
            "name": display_name or path.name,
            "display_name": display_names.get(source_id) or display_name or path.name,
            "size_bytes": len(content),
            "content_sha256": content_hash,
            "source_kind": "uploaded",
            "page_count": _pdf_page_count(content) if path.suffix.lower() == ".pdf" else 1,
        }))
    return catalog


def _source_catalog_records() -> List[tuple[Path, Dict[str, Any]]]:
    """构造来源目录及其内部路径，内部路径不会进入 HTTP 响应。"""
    display_names = _read_source_display_names()
    catalog: List[tuple[Path, Dict[str, Any]]] = []
    for source in resolve_production_source_metadata():
        content_hash = str(source["content_hash"])
        source_id = content_source_id(content_hash)
        name = Path(str(source["configured_path"])).name
        available = bool(source["available"]) and source["path"] is not None
        path = source["path"] if available else (_PROJECT_ROOT / str(source["configured_path"]))
        catalog.append((path, {
                "source_id": source_id,
                "name": name,
                "display_name": display_names.get(source_id) or name,
                "size_bytes": source["size_bytes"],
                "content_sha256": content_hash,
                "source_kind": "frozen",
                "page_count": source["page_count"],
                "available": available,
            }))
    return catalog + _uploaded_source_catalog_records()


def _legacy_frozen_source_id(path: Path, content_hash: str) -> str:
    """重建内容哈希迁移前的固定来源 ID，仅用于读取历史运行。"""
    return "source_" + hashlib.sha256(f"{path.name}:{content_hash}".encode("utf-8")).hexdigest()[:20]


def _source_id_lookup(records: List[tuple[Path, Dict[str, Any]]]) -> Dict[str, tuple[Path, Dict[str, Any]]]:
    """建立当前 ID 与历史固定来源 ID 到同一来源记录的兼容映射。"""
    lookup: Dict[str, tuple[Path, Dict[str, Any]]] = {}
    for path, entry in records:
        aliases = [str(entry["source_id"])]
        if entry.get("source_kind") == "frozen":
            aliases.append(_legacy_frozen_source_id(path, str(entry["content_sha256"])))
        for alias in aliases:
            previous = lookup.get(alias)
            if previous is not None and previous[1]["source_id"] != entry["source_id"]:
                raise ValueError(f"source_id alias is ambiguous: {alias}")
            lookup[alias] = (path, entry)
    return lookup


def _canonical_catalog_source_ids(
    source_ids: List[str],
    records: List[tuple[Path, Dict[str, Any]]],
    *,
    keep_unknown: bool = False,
) -> List[str]:
    """把目录来源 ID（含历史别名）规范化为当前来源 ID。"""
    lookup = _source_id_lookup(records)
    canonical: List[str] = []
    for source_id in source_ids:
        match = lookup.get(source_id)
        if match is None:
            if keep_unknown:
                canonical.append(source_id)
                continue
            raise ValueError(f"unknown source_id: {source_id}")
        canonical.append(str(match[1]["source_id"]))
    return canonical


def _source_catalog() -> List[Dict[str, Any]]:
    """返回可供页面选择的来源目录，不把宿主路径暴露给调用方。"""
    return [entry for _, entry in _source_catalog_records()]


def list_source_catalog() -> List[Dict[str, Any]]:
    """列出当前可选择的来源，供前端提交显式 source_id。"""
    return _source_catalog()


def update_source_display_name(source_id: str, display_name: Any) -> Dict[str, Any]:
    """保存来源显示名；只改本地元数据，不改来源文件、hash 或历史运行。"""
    normalized_id = str(source_id or "").strip()
    display_name = _validate_source_display_name(display_name)
    match = next(
        ((path, entry) for path, entry in _source_catalog_records() if entry["source_id"] == normalized_id),
        None,
    )
    if match is None:
        raise KeyError(f"source not found: {normalized_id}")
    display_names = _read_source_display_names()
    display_names[normalized_id] = display_name
    _write_source_display_names(display_names)
    _path, entry = match
    return {**entry, "display_name": display_name}


def register_uploaded_source(filename: str | None, content: bytes) -> Dict[str, Any]:
    """校验并幂等保存一个上传来源，但不启动摄取或远程视觉调用。"""
    original_name = str(filename or "").strip()
    if not original_name:
        raise ValueError("没有选择文件")
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"不支持该文件格式，可上传：{supported}")
    if len(content) == 0:
        raise ValueError("上传文件为空")
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(f"文件大小不能超过 {settings.MAX_UPLOAD_SIZE_MB}MB")

    content_hash = hashlib.sha256(content).hexdigest()
    source_id = f"upload_{content_hash[:24]}"
    safe_name = secure_filename(Path(original_name).name) or f"document{suffix}"
    RAG_EVAL_SOURCE_ROOT.mkdir(parents=True, exist_ok=True)

    for existing_path, existing in _uploaded_source_catalog_records():
        if existing["source_id"] == source_id:
            return existing

    target = RAG_EVAL_SOURCE_ROOT / f"{source_id}__{safe_name}"
    temporary = RAG_EVAL_SOURCE_ROOT / f".{uuid.uuid4().hex}.{safe_name}"
    temporary.write_bytes(content)
    try:
        issue = inspect_source(temporary)
        if issue is not None:
            raise ValueError(issue.message)
        replace_with_retry(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    page_count = _pdf_page_count(content) if suffix == ".pdf" else 1
    if suffix == ".pdf" and (page_count is None or page_count < 1):
        target.unlink(missing_ok=True)
        raise ValueError("上传文件不是可读取的 PDF")
    return {
        "source_id": source_id,
        "name": safe_name,
        "size_bytes": len(content),
        "content_sha256": content_hash,
        "source_kind": "uploaded",
        "page_count": page_count,
    }


def register_uploaded_pdf(filename: str | None, content: bytes) -> Dict[str, Any]:
    """兼容旧调用名；新上传入口统一使用 register_uploaded_source。"""
    return register_uploaded_source(filename, content)


def delete_uploaded_source(source_id: str) -> Dict[str, Any]:
    """删除一个用户上传的来源文件，不删除 staged index、评测或共享知识库。"""
    normalized_id = str(source_id or "").strip()
    if not normalized_id.startswith("upload_"):
        raise ValueError("只能删除用户上传的知识源")

    match = next(
        ((path, entry) for path, entry in _uploaded_source_catalog_records() if entry["source_id"] == normalized_id),
        None,
    )
    if match is None:
        raise KeyError(f"uploaded source not found: {normalized_id}")

    with _path_file_lock(RAG_EVAL_SOURCE_ROOT / ".lifecycle.lock"):
        active_runs = []
        if ISOLATED_RUN_ROOT.is_dir():
            for run_dir in ISOLATED_RUN_ROOT.iterdir():
                if not run_dir.is_dir():
                    continue
                state = _read_json(run_dir / "run.json")
                if (
                    state.get("kind") == "ingestion"
                    and state.get("status") in _ACTIVE_INGESTION_STATUSES
                    and normalized_id in state.get("source_ids", [])
                ):
                    active_runs.append(str(state.get("run_id") or run_dir.name))
        if active_runs:
            raise RuntimeError(f"知识源正在摄取，暂不能删除：{active_runs[0]}")

        path, entry = match
        path.resolve().relative_to(RAG_EVAL_SOURCE_ROOT.resolve())
        path.unlink()
        display_names = _read_source_display_names()
        if normalized_id in display_names:
            display_names.pop(normalized_id, None)
            try:
                _write_source_display_names(display_names)
            except OSError:
                # 显示名只是可选元数据；来源文件已删除时不应因元数据写回失败回滚。
                # 隔离 RAG 内部不重复写运行日志，后续目录刷新会重建该元数据。
                pass
    return {"source_id": normalized_id, "name": entry["name"], "status": "deleted", "deleted": True}


def _resolve_source_inputs(
    source_ids: List[str] | None,
    sources: List[Dict[str, Any]] | None,
) -> tuple[List[Path], List[str], List[str]]:
    """解析来源目录 ID 或显式 URI，格式判断交给摄取解析层。"""
    if source_ids and sources:
        raise ValueError("source_ids and sources cannot be used together")
    if source_ids:
        requested = [str(value).strip() for value in source_ids]
        if not requested or len(requested) > _MAX_SOURCES or any(not value for value in requested):
            raise ValueError(f"source_ids must contain 1 to {_MAX_SOURCES} items")
        records = _source_catalog_records()
        entries = _source_id_lookup(records)
        missing = [value for value in requested if value not in entries]
        if missing:
            raise ValueError(f"unknown source_id: {missing[0]}")
        ordered = [entries[source_id] for source_id in requested]
        ordered_paths = [match[0] for match in ordered]
        unavailable = [
            source_id
            for source_id, match in zip(requested, ordered)
            if match[1].get("available") is False or not match[0].is_file()
        ]
        if unavailable:
            raise ValueError(
                f"该来源在当前环境不可用: {unavailable[0]}"
            )
        resolved_ids = [str(match[1]["source_id"]) for match in ordered]
        display_names = [str(match[1].get("display_name") or source_id) for source_id, match in zip(requested, ordered)]
        return ordered_paths, resolved_ids, display_names

    if not sources or len(sources) > _MAX_SOURCES:
        raise ValueError(f"sources must contain 1 to {_MAX_SOURCES} items")
    paths: List[Path] = []
    source_names: List[str] = []
    display_names: List[str] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("each source must be an object")
        uri = str(source.get("uri") or "").strip()
        if not uri:
            raise ValueError("each source requires a uri")
        path = Path(uri).expanduser().resolve()
        if not path.is_file() and not path.is_dir():
            raise FileNotFoundError(f"source uri is unavailable: {uri}")
        paths.append(path)
        source_id = str(source.get("source_id") or path.name)
        source_names.append(source_id)
        display_names.append(str(source.get("display_name") or source.get("name") or source_id))
    return paths, source_names, display_names


def _normalize_page_ranges(
    page_ranges: List[Dict[str, Any]] | None,
    source_paths: List[Path],
    source_names: List[str],
) -> tuple[List[Dict[str, int | str]], Dict[str, tuple[int, int]]]:
    """校验每个来源的物理页范围，并转换为解析层使用的路径映射。"""
    if not page_ranges:
        return [], {}
    if not isinstance(page_ranges, list) or len(page_ranges) != len(source_paths):
        raise ValueError("page_ranges must contain exactly one range for each selected source")
    selected = dict(zip(source_names, source_paths))
    normalized: Dict[str, Dict[str, int | str]] = {}
    for item in page_ranges:
        if not isinstance(item, dict):
            raise ValueError("each page range must be an object")
        source_id = str(item.get("source_id") or "").strip()
        if source_id in normalized or source_id not in selected:
            raise ValueError(f"page range source_id is not selected: {source_id}")
        start_page = item.get("start_page")
        end_page = item.get("end_page")
        if (
            isinstance(start_page, bool) or not isinstance(start_page, int)
            or isinstance(end_page, bool) or not isinstance(end_page, int)
            or start_page < 1 or end_page < start_page
        ):
            raise ValueError("page range must use positive integers with start_page <= end_page")
        path = selected[source_id]
        page_count = 1
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            page_count = len(PdfReader(str(path)).pages)
        if end_page > page_count:
            raise ValueError(f"page range exceeds {source_id} page count {page_count}")
        normalized[source_id] = {"source_id": source_id, "start_page": start_page, "end_page": end_page}
    if set(normalized) != set(selected):
        missing = next(source_id for source_id in selected if source_id not in normalized)
        raise ValueError(f"missing page range for selected source: {missing}")
    ordered = [normalized[source_id] for source_id in source_names]
    by_path = {
        str(selected[item["source_id"]].resolve()): (int(item["start_page"]), int(item["end_page"]))
        for item in ordered
    }
    return ordered, by_path


class IsolatedRunManager:
    """管理隔离运行状态、SSE 事件和 staged index 查询。"""

    def __init__(self) -> None:
        """初始化进程内事件缓存；实际索引产物写入每次独立运行目录。"""
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._queues: Dict[str, queue.Queue] = {}
        self._lock = threading.RLock()

    def start_ingestion(
        self,
        *,
        source_ids: List[str] | None = None,
        sources: List[Dict[str, Any]] | None = None,
        max_pages: int | None = None,
        page_ranges: List[Dict[str, Any]] | None = None,
        allow_remote_data: bool = False,
        authorized_source_ids: List[str] | None = None,
        embedding_config: Dict[str, Any] | EmbeddingConfiguration | None = None,
    ) -> Dict[str, Any]:
        """异步摄取显式来源，并返回新的隔离运行身份。"""
        if max_pages is not None and (isinstance(max_pages, bool) or max_pages < 1):
            raise ValueError("max_pages must be positive")
        if max_pages is not None and page_ranges:
            raise ValueError("max_pages and page_ranges cannot be combined")
        source_paths, resolved_source_ids, source_display_names = _resolve_source_inputs(source_ids, sources)
        authorized_requested = sorted(set(authorized_source_ids or []))
        catalog_records = _source_catalog_records() if source_ids else []
        authorized = (
            sorted(set(_canonical_catalog_source_ids(authorized_requested, catalog_records, keep_unknown=True)))
            if source_ids and authorized_requested
            else authorized_requested
        )
        if source_ids and page_ranges:
            normalized_page_ranges = []
            for item in page_ranges:
                if not isinstance(item, dict):
                    normalized_page_ranges.append(item)
                    continue
                normalized_page_ranges.append({
                    **item,
                    "source_id": _canonical_catalog_source_ids([str(item.get("source_id") or "")], catalog_records)[0],
                })
            page_ranges = normalized_page_ranges
        if not isinstance(allow_remote_data, bool):
            raise ValueError("allow_remote_data must be a boolean")
        if not allow_remote_data and authorized:
            raise ValueError("authorized_source_ids require allow_remote_data=true")
        if not set(authorized) <= set(resolved_source_ids):
            raise ValueError("authorized_source_ids must match the current sources")
        if allow_remote_data and not authorized:
            raise PermissionError("remote VLM requires explicit source-level authorization")
        if allow_remote_data and not _remote_data_enabled():
            raise PermissionError("remote VLM is disabled by server policy")
        normalized_ranges, page_range_map = _normalize_page_ranges(page_ranges, source_paths, resolved_source_ids)
        frozen_embedding = (
            resolve_production_embedding_configuration()
            if embedding_config is None
            else validate_embedding_env_references(embedding_config)
        )

        run_id = _new_run_id("ingest")
        run_dir = _run_dir(run_id)
        state = {
            "run_id": run_id,
            "kind": "ingestion",
            "source_ids": list(resolved_source_ids),
            "source_names": source_display_names,
            "source_display_names": source_display_names,
            "source_label": "、".join(source_display_names),
            "max_pages": max_pages,
            "page_ranges": normalized_ranges,
            "remote_enabled": bool(allow_remote_data and authorized),
            "authorized_source_ids": authorized,
            "status": "queued",
            "created_at": _timestamp(),
            "started_at": "",
            "finished_at": "",
            "current_stage": "",
            "cancel_requested": False,
            "execution_backend": "persistent_worker",
            "job_id": run_id,
            "sources": list(sources or []),
            "index_version": "",
            "collection_name": "",
            "manifest_sha256": "",
            "embedding_config": frozen_embedding.to_runtime_dict(),
            "embedding_fingerprint": frozen_embedding.fingerprint(),
            "unit_count": 0,
            "vector_count": 0,
            "events": [],
        }
        with _path_file_lock(RAG_EVAL_SOURCE_ROOT / ".lifecycle.lock"):
            if any(not path.is_file() for path in source_paths):
                raise ValueError("选定的知识源在摄取任务创建前已被删除")
            with self._lock:
                self._runs[run_id] = state
                _write_json(run_dir / "run.json", state)
        self._emit(run_id, "run_created", "创建隔离知识源摄取任务", {
            "source_ids": list(source_ids or []),
            "source_count": len(source_paths),
        })

        self._enqueue_persistent_run(run_id, "ingestion")
        return self.get(run_id)

    def release_status(
        self,
        ingestion_run_id: str = "",
        index_version: str = "",
        evaluation_run_id: str | None = None,
    ) -> Dict[str, Any]:
        """返回候选 release 与正式 active/previous pointer 的只读快照。"""
        formal = MultimodalKnowledgeBaseMaintenance()
        pointer = formal.status()
        result: Dict[str, Any] = {
            "active": pointer.get("active"),
            "previous": pointer.get("previous"),
            "fallback": pointer.get("fallback") or pointer.get("previous"),
            "generation": pointer.get("generation", 0),
            "candidates": pointer.get("candidates", []),
            "candidate_overflow": pointer.get("candidate_overflow", False),
            "quarantine": pointer.get("quarantine", []),
            "release": None,
        }
        if ingestion_run_id or index_version:
            context = self._release_context(ingestion_run_id, index_version, evaluation_run_id)
            result["release"] = context["release"]
        return result

    def check_release(
        self,
        ingestion_run_id: str,
        index_version: str,
        evaluation_run_id: str | None = None,
        expected_active_index_version: str | None = None,
        expected_generation: int | None = None,
    ) -> Dict[str, Any]:
        """执行可审计的正式发布门禁，但不复制产物也不切换 pointer。"""
        context = self._release_context(ingestion_run_id, index_version, evaluation_run_id)
        manifest = context["manifest"]
        checks: list[Dict[str, Any]] = []

        def add_check(key: str, label: str, passed: bool, detail: str) -> None:
            checks.append({
                "key": key,
                "label": label,
                "status": "pass" if passed else "fail",
                "blocking": True,
                "detail": detail,
            })

        from Agent.knowledge_base.multimodal.production import (
            is_production_manifest,
            validate_production_manifest,
        )

        production_manifest = is_production_manifest(manifest)
        add_check(
            "controlled_source_identity",
            "正式来源身份",
            production_manifest,
            "source_id、document_id、哈希和受控路径必须完全匹配当前配置"
            if not production_manifest
            else "所有来源均在受控目录内唯一哈希命中",
        )

        isolated_maintenance = MultimodalKnowledgeBaseMaintenance(
            asset_root=context["run_dir"] / "assets",
            index_root=context["run_dir"] / "indexes",
            active_config=context["run_dir"] / "active_index.json",
            embedding_scope="evaluation",
        )
        try:
            evaluation = isolated_maintenance.evaluate(index_version)
            add_check(
                "staged_integrity",
                "staged 索引完整性",
                bool(evaluation.get("passed")),
                "；".join(evaluation.get("failures") or []) or "manifest、资源、units 和向量数量一致",
            )
            production_evaluation = evaluation.get("production_evaluation") or {}
            add_check(
                "production_retrieval",
                "正式检索门禁",
                bool(production_evaluation.get("gate", {}).get("passed")),
                "；".join(production_evaluation.get("gate", {}).get("failures") or []) or "正式检索评测通过",
            )
        except Exception as exc:
            evaluation = {"passed": False, "failures": [str(exc)]}
            add_check("staged_integrity", "staged 索引完整性", False, str(exc))
            add_check("production_retrieval", "正式检索门禁", False, "尚未生成正式检索评测")

        policy_failures = validate_production_manifest(manifest) if production_manifest else ["non_production_manifest"]
        add_check(
            "production_policy",
            "正式策略一致性",
            not policy_failures,
            "；".join(policy_failures) or "解析器、VLM 和 embedding 策略与正式配置一致",
        )

        evaluation_state = context["evaluation_state"]
        evaluation_bound = bool(
            evaluation_state
            and evaluation_state.get("kind") == "evaluation"
            and evaluation_state.get("status") == "succeeded"
            and evaluation_state.get("ingestion_run_id") == ingestion_run_id
            and evaluation_state.get("index_version") == index_version
            and (evaluation_state.get("summary") or {}).get("status") == "pass"
        )
        if evaluation_run_id:
            evaluation_dir = _run_dir(evaluation_run_id)
            evaluation_bound = evaluation_bound and bool(
                str((evaluation_state or {}).get("input_content_sha256") or "")
                and str(((evaluation_state or {}).get("input_identity") or {}).get("dataset_revision") or "")
                and (evaluation_dir / "result.json").is_file()
            )
        add_check(
            "ragas",
            "自动 Ragas 评测",
            evaluation_bound,
            "当前索引绑定的 Ragas 评测未通过或不存在"
            if not evaluation_bound
            else "当前索引绑定的自动 Ragas 评测已通过",
        )

        formal = MultimodalKnowledgeBaseMaintenance()
        pointer = formal.status()
        active_version = str((pointer.get("active") or {}).get("index_version") or "")
        pointer_ok = expected_active_index_version in {None, "", active_version}
        add_check(
            "active_pointer_consistency",
            "active pointer 并发一致性",
            pointer_ok,
            "发布前 active pointer 已发生变化"
            if not pointer_ok
            else "发布目标仍基于当前 active pointer 快照",
        )
        generation_ok = expected_generation in {None, int(pointer.get("generation") or 0)}
        add_check(
            "active_pointer_generation",
            "active pointer generation",
            generation_ok,
            "发布前 active pointer generation 已发生变化"
            if not generation_ok
            else "发布目标仍基于当前 active pointer generation 快照",
        )
        current_manifest_sha = compute_manifest_sha256(context["manifest_path"])
        add_check(
            "manifest_unchanged",
            "manifest 不可变",
            current_manifest_sha == context["release"]["manifest_sha256"],
            "manifest 在门禁期间发生变化"
            if current_manifest_sha != context["release"]["manifest_sha256"]
            else "manifest 哈希与候选身份一致",
        )
        publishable = all(item["status"] == "pass" for item in checks if item["blocking"])
        return {
            "state": "ready_to_publish" if publishable else "blocked",
            "publishable": publishable,
            "checked_at": _timestamp(),
            "release": context["release"],
            "checks": checks,
            "evaluation": evaluation,
            "active": pointer.get("active"),
            "previous": pointer.get("previous"),
            "requires_worker_restart": True,
        }

    def publish_release(
        self,
        ingestion_run_id: str,
        index_version: str,
        evaluation_run_id: str,
        expected_active_index_version: str | None = None,
        expected_generation: int | None = None,
    ) -> Dict[str, Any]:
        """晋级隔离 staged 产物并复用正式 CLI publish 门禁切换 active pointer。"""
        report = self.check_release(
            ingestion_run_id,
            index_version,
            evaluation_run_id,
            expected_active_index_version,
            expected_generation,
        )
        if not report["publishable"]:
            raise ReleaseGateError("正式发布门禁未通过", report)
        context = self._release_context(ingestion_run_id, index_version, evaluation_run_id)
        evaluation_state = context["evaluation_state"] or {}
        evaluation_dir = _run_dir(evaluation_run_id)
        dataset_identity = evaluation_state.get("input_identity") or {}
        binding = {
            "evaluation_run_id": evaluation_run_id,
            "dataset_revision": str(dataset_identity.get("dataset_revision") or ""),
            "dataset_sha256": str(evaluation_state.get("input_content_sha256") or ""),
            "report_sha256": hashlib.sha256((evaluation_dir / "result.json").read_bytes()).hexdigest(),
            "gate_sha256": hashlib.sha256(canonical_json(report.get("checks") or []).encode("utf-8")).hexdigest(),
        }
        seal_evaluation_binding(context["manifest_path"], binding)
        bound_manifest_sha256 = compute_manifest_sha256(context["manifest_path"])
        self._set_status(ingestion_run_id, "staged", manifest_sha256=bound_manifest_sha256)
        report["release"]["manifest_sha256"] = bound_manifest_sha256
        report["release"]["evaluation_binding"] = binding
        formal = MultimodalKnowledgeBaseMaintenance()
        published = formal.publish_staged(
            source_index_root=context["run_dir"] / "indexes",
            index_version=index_version,
            expected_active_index_version=expected_active_index_version,
            expected_manifest_sha256=bound_manifest_sha256,
            expected_generation=expected_generation,
            expected_evaluation_binding=binding,
        )
        pointer = formal.status()
        return {
            "status": published.get("status", "published"),
            "release": report["release"],
            "gate": report,
            "promotion": {"status": "managed_by_release_manager"},
            "publish": published,
            "active": pointer.get("active"),
            "previous": pointer.get("previous"),
            "requires_worker_restart": True,
        }

    def rollback_release(
        self,
        index_version: str,
        expected_active_index_version: str | None = None,
        expected_generation: int | None = None,
    ) -> Dict[str, Any]:
        """调用与 CLI 相同的正式回滚门禁；不自动删除其他版本。"""
        formal = MultimodalKnowledgeBaseMaintenance()
        result = formal.rollback(
            index_version,
            expected_active_index_version=expected_active_index_version,
            expected_generation=expected_generation,
        )
        pointer = formal.status()
        return {
            **result,
            "active": pointer.get("active"),
            "previous": pointer.get("previous"),
            "requires_worker_restart": True,
        }

    def _release_context(
        self,
        ingestion_run_id: str,
        index_version: str,
        evaluation_run_id: str | None,
    ) -> Dict[str, Any]:
        """解析候选 release 的唯一身份并隐藏宿主绝对路径。"""
        ingestion = self._load(ingestion_run_id)
        if ingestion.get("kind") != "ingestion" or ingestion.get("status") != "staged":
            raise ValueError("ingestion run is not staged and cannot be released")
        if ingestion.get("index_version") != index_version:
            raise ValueError("index_version does not belong to ingestion run")
        run_dir = _run_dir(ingestion_run_id)
        index_dir = (run_dir / "indexes" / index_version).resolve()
        index_dir.relative_to(run_dir.resolve())
        manifest_path = index_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("staged release manifest is unavailable")
        manifest = _read_json(manifest_path)
        manifest_sha = compute_manifest_sha256(manifest_path)
        evaluation_state = None
        if evaluation_run_id:
            evaluation_state = self._load(evaluation_run_id)
        return {
            "run_dir": run_dir,
            "manifest_path": manifest_path,
            "manifest": manifest,
            "evaluation_state": evaluation_state,
            "release": {
                "release_id": str(manifest.get("release_id") or index_version),
                "identity_sha256": str(manifest.get("identity_sha256") or ""),
                "ingestion_run_id": ingestion_run_id,
                "index_version": index_version,
                "manifest_sha256": manifest_sha,
                "embedding": manifest.get("embedding"),
                "embedding_config": manifest.get("embedding_config"),
                "source_count": len(manifest.get("sources") or []),
                "sources": [
                    {
                        key: source.get(key)
                        for key in ("source_id", "document_id", "relative_path", "controlled_path", "content_hash")
                    }
                    for source in manifest.get("sources", [])
                    if isinstance(source, dict)
                ],
                "evaluation_run_id": evaluation_run_id or "",
            },
        }

    def start_rag_query(
        self,
        ingestion_run_id: str,
        index_version: str,
        questions: List[Dict[str, Any]],
        input_identity: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """在指定摄取运行的 staged index 上异步执行真实检索与回答。"""
        if not questions or len(questions) > _MAX_QUESTIONS:
            raise ValueError(f"questions must contain 1 to {_MAX_QUESTIONS} items")
        for question in questions:
            if not str(question.get("question") or "").strip():
                raise ValueError("each question requires non-empty question text")

        ingestion = self._load(ingestion_run_id)
        if ingestion.get("kind") != "ingestion" or ingestion.get("status") != "staged":
            raise ValueError("ingestion run is not staged and cannot be queried")
        if ingestion.get("index_version") != index_version:
            raise ValueError("index_version does not belong to ingestion run")
        index_dir = _run_dir(ingestion_run_id) / "indexes" / index_version
        self._validate_staged_index(index_dir, ingestion)

        run_id = _new_run_id("rag")
        state = {
            "run_id": run_id,
            "kind": "rag_query",
            "status": "queued",
            "created_at": _timestamp(),
            "started_at": "",
            "finished_at": "",
            "current_stage": "",
            "cancel_requested": False,
            "execution_backend": "persistent_worker",
            "job_id": run_id,
            "ingestion_run_id": ingestion_run_id,
            "index_version": index_version,
            "question_count": len(questions),
            "questions": [dict(question) for question in questions],
            "input_identity": dict(input_identity or {"schema_version": "isolated_question_v1"}),
            "result_path": str(_run_dir(run_id) / "result.json"),
            "events": [],
        }
        with self._lock:
            self._runs[run_id] = state
            _write_json(_run_dir(run_id) / "run.json", state)
        self._emit(run_id, "run_created", "创建 staged index RAG 测试任务", {"index_version": index_version})
        self._enqueue_persistent_run(run_id, "rag_query")
        return self.get(run_id)

    def start_candidate_generation(
        self,
        ingestion_run_id: str,
        index_version: str,
        *,
        dataset_id: str | None = None,
        question_count: int | None = None,
        max_workers: int = 1,
        max_units: int | None = None,
        questions_per_unit: int = 1,
    ) -> Dict[str, Any]:
        """在 staged index 上按指定题数启动候选评测集生成。"""
        if question_count is None:
            max_units = 48 if max_units is None else max_units
            if isinstance(max_units, bool) or not isinstance(max_units, int) or not 1 <= max_units <= 128:
                raise ValueError("max_units must be an integer from 1 to 128")
            if isinstance(questions_per_unit, bool) or not isinstance(questions_per_unit, int) or not 1 <= questions_per_unit <= 3:
                raise ValueError("questions_per_unit must be an integer from 1 to 3")
            requested_count = max_units * questions_per_unit
        else:
            if isinstance(question_count, bool) or not isinstance(question_count, int) or not 1 <= question_count <= 128:
                raise ValueError("question_count must be an integer from 1 to 128")
            max_units = question_count
            questions_per_unit = 1
            requested_count = question_count
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 4:
            raise ValueError("max_workers must be an integer from 1 to 4")
        ingestion = self._load(ingestion_run_id)
        if ingestion.get("kind") != "ingestion" or ingestion.get("status") != "staged":
            raise ValueError("ingestion run is not staged and cannot generate candidates")
        if ingestion.get("index_version") != index_version:
            raise ValueError("index_version does not belong to ingestion run")
        index_dir = _run_dir(ingestion_run_id) / "indexes" / index_version
        self._validate_staged_index(index_dir, ingestion)
        staged_unit_count = int(ingestion.get("unit_count") or 0)
        if max_units > staged_unit_count:
            raise ValueError(
                f"question_count={requested_count} exceeds staged index capacity of {staged_unit_count} questions "
                "with one question per unit; generate a larger index before requesting more"
            )

        run_id = _new_run_id("candidate")
        run_dir = _run_dir(run_id)
        source_display_names = ingestion.get("source_display_names") or ingestion.get("source_names") or []
        state = {
            "run_id": run_id,
            "kind": "candidate_generation",
            "status": "queued",
            "created_at": _timestamp(),
            "started_at": "",
            "finished_at": "",
            "current_stage": "",
            "cancel_requested": False,
            "execution_backend": "persistent_worker",
            "job_id": run_id,
            "ingestion_run_id": ingestion_run_id,
            "index_version": index_version,
            "source_names": list(source_display_names),
            "source_display_names": list(source_display_names),
            "source_label": ingestion.get("source_label") or "、".join(str(item) for item in source_display_names),
            "dataset_id": dataset_id or "",
            "question_count": requested_count,
            "max_units": max_units,
            "questions_per_unit": questions_per_unit,
            "staged_unit_count": staged_unit_count,
            "requested_candidate_count": requested_count,
            "candidate_capacity": staged_unit_count,
            "max_workers": max_workers,
            "candidate_artifact_name": "candidate.json",
            "audit_artifact_name": "candidate.json.audit.json",
            "result_path": str(run_dir / "result.json"),
            "candidate_path": str(run_dir / "candidate.json"),
            "events": [],
        }
        with self._lock:
            self._runs[run_id] = state
            _write_json(run_dir / "run.json", state)
        self._emit(run_id, "run_created", "创建候选题生成任务", {
            "index_version": index_version,
            "question_count": requested_count,
            "max_workers": max_workers,
        })
        self._enqueue_persistent_run(run_id, "candidate_generation")
        return self.get(run_id)

    def start_tuning_dataset_governance(
        self,
        ingestion_run_id: str,
        index_version: str,
        *,
        target_count: int = 48,
        minimum_metric: float = 0.2,
    ) -> Dict[str, Any]:
        """启动只登记索引绑定调参集的独立闭环任务。"""
        if not isinstance(target_count, int) or isinstance(target_count, bool) or not 1 <= target_count <= 128:
            raise ValueError("target_count must be an integer from 1 to 128")
        if isinstance(minimum_metric, bool) or not isinstance(minimum_metric, (int, float)) or not 0 <= minimum_metric <= 1:
            raise ValueError("minimum_metric must be between 0 and 1")
        ingestion = self._load(ingestion_run_id)
        if ingestion.get("kind") != "ingestion" or ingestion.get("status") != "staged":
            raise ValueError("ingestion run is not staged and cannot govern a tuning dataset")
        if ingestion.get("index_version") != index_version:
            raise ValueError("index_version does not belong to ingestion run")
        index_dir = _run_dir(ingestion_run_id) / "indexes" / index_version
        self._validate_staged_index(index_dir, ingestion)
        run_id = _new_run_id("tuning_dataset")
        run_dir = _run_dir(run_id)
        state = {
            "run_id": run_id, "kind": "tuning_dataset_governance", "status": "queued",
            "created_at": _timestamp(), "started_at": "", "finished_at": "",
            "current_stage": "queued", "cancel_requested": False,
            "execution_backend": "persistent_worker", "job_id": run_id,
            "ingestion_run_id": ingestion_run_id, "index_version": index_version,
            "target_count": target_count, "minimum_metric": float(minimum_metric),
            "result_path": str(run_dir / "result.json"), "events": [],
        }
        with self._lock:
            self._runs[run_id] = state
            _write_json(run_dir / "run.json", state)
        self._emit(run_id, "run_created", "创建索引绑定调参测试集治理任务", {"index_version": index_version, "target_count": target_count})
        self._enqueue_persistent_run(run_id, "tuning_dataset_governance")
        return self.get(run_id)

    def import_rebound_candidate(self) -> Dict[str, Any]:
        """把仓库内的重绑候选复审产物挂载为一个只读完成 run，供前端逐题复审。"""
        source_root = _PROJECT_ROOT / "Agent" / "knowledge_base" / "rag" / "data" / "eval"
        candidate_source = source_root / "pearl_candidate_mm_f956e532ed6d49ae1f0e_48_rebound_requires_reapproval_v3.json"
        review_source = source_root / "pearl_candidate_mm_f956e532ed6d49ae1f0e_48_reapproval_manifest_v3.json"
        if not candidate_source.is_file() or not review_source.is_file():
            raise FileNotFoundError("rebound candidate review artifact is unavailable")
        candidate = _read_json(candidate_source)
        index_version = str((candidate.get("source_snapshot") or {}).get("index_version") or "")
        ingestion_candidates = []
        if ISOLATED_RUN_ROOT.is_dir():
            for existing_run_dir in ISOLATED_RUN_ROOT.iterdir():
                if not existing_run_dir.is_dir():
                    continue
                existing = _read_json(existing_run_dir / "run.json")
                if (
                    existing.get("kind") == "ingestion"
                    and existing.get("status") == "staged"
                    and existing.get("index_version") == index_version
                ):
                    ingestion_candidates.append(existing)
        if not ingestion_candidates:
            raise ValueError("rebound candidate does not belong to an available staged ingestion")
        ingestion_candidates.sort(key=lambda item: str(item.get("finished_at") or item.get("created_at") or ""), reverse=True)
        ingestion_run_id = str(ingestion_candidates[0].get("run_id") or "")
        run_id = _new_run_id("candidate_rebind")
        run_dir = _run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = run_dir / "candidate_rebound.json"
        review_path = run_dir / "candidate_rebind_review.json"
        shutil.copy2(candidate_source, candidate_path)
        shutil.copy2(review_source, review_path)
        state = {
            "run_id": run_id,
            "kind": "candidate_generation",
            "status": "succeeded",
            "created_at": _timestamp(),
            "started_at": _timestamp(),
            "finished_at": _timestamp(),
            "current_stage": "requires_reapproval",
            "cancel_requested": False,
            "execution_backend": "review_artifact",
            "ingestion_run_id": ingestion_run_id,
            "index_version": index_version,
            "question_count": len(candidate.get("samples") or []),
            "candidate_artifact_name": candidate_path.name,
            "candidate_path": str(candidate_path),
            "review_manifest_artifact_name": review_path.name,
            "review_status": "requires_reapproval",
            "events": [],
        }
        _write_json(run_dir / "run.json", state)
        with self._lock:
            self._runs[run_id] = state
        return self.get(run_id)

    def start_dataset_governance(self, evaluation_run_id: str, *, confirm: bool) -> Dict[str, Any]:
        """从已完成的 Gold v2 evaluation run 创建一次可恢复的治理任务。"""
        if confirm is not True:
            raise ValueError("confirm must be true")
        source_run = self._load(str(evaluation_run_id or "").strip())
        if source_run.get("kind") != "evaluation" or source_run.get("status") != "succeeded":
            raise ValueError("dataset governance requires a succeeded evaluation run")
        identity = source_run.get("input_identity") or {}
        if identity.get("dataset_id") != "pearl_gold_v2" or identity.get("dataset_kind") != "gold_regression":
            raise ValueError("dataset governance only supports pearl_gold_v2 gold_regression")

        run_id = _new_run_id("governance")
        run_dir = _run_dir(run_id)
        state = {
            "run_id": run_id,
            "kind": "dataset_governance",
            "status": "queued",
            "created_at": _timestamp(),
            "started_at": "",
            "finished_at": "",
            "current_stage": "queued",
            "cancel_requested": False,
            "execution_backend": "persistent_worker",
            "job_id": run_id,
            "evaluation_run_id": str(evaluation_run_id),
            "ingestion_run_id": str(source_run.get("ingestion_run_id") or ""),
            "index_version": str(source_run.get("index_version") or ""),
            "source_dataset_revision": str(identity.get("dataset_revision") or ""),
            "result_path": str(run_dir / "result.json"),
            "events": [],
        }
        with self._lock:
            self._runs[run_id] = state
            _write_json(run_dir / "run.json", state)
        self._emit(run_id, "run_created", "创建 Gold 题目健康治理任务", {
            "evaluation_run_id": evaluation_run_id,
            "index_version": state["index_version"],
        })
        self._enqueue_persistent_run(run_id, "dataset_governance")
        return self.get(run_id)

    def start_evaluation(
        self,
        ingestion_run_id: str,
        index_version: str,
        eval_dataset: Dict[str, Any],
        retrieval_options: Dict[str, Any] | None = None,
        ragas_options: Dict[str, Any] | None = None,
        strategy_profile: Dict[str, Any] | None = None,
        steps: List[str] | None = None,
        batch_id: str = "",
        batch_position: int = 0,
        batch_size: int = 0,
    ) -> Dict[str, Any]:
        """在指定 staged index 上启动完整 评测流程。"""
        from app.rag_eval.isolated_evaluation import normalize_dataset_payload
        from Agent.knowledge_base.rag.rag_eval.contracts import DATASET_KINDS, evaluation_identity

        canonical_dataset, samples = normalize_dataset_payload(eval_dataset)
        if len(samples) > _MAX_QUESTIONS:
            raise ValueError(f"eval_dataset.samples must contain at most {_MAX_QUESTIONS} items")
        ingestion = self._load(ingestion_run_id)
        if ingestion.get("kind") != "ingestion" or ingestion.get("status") != "staged":
            raise ValueError("ingestion run is not staged and cannot be evaluated")
        if ingestion.get("index_version") != index_version:
            raise ValueError("index_version does not belong to ingestion run")
        index_dir = _run_dir(ingestion_run_id) / "indexes" / index_version
        identity = self._validate_staged_index(index_dir, ingestion)
        if isinstance(identity, IndexIdentity):
            canonical_dataset = IndexBindingGate(
                self._load,
                _run_dir,
                lambda: ingestion.get("embedding_config") or _staged_embedding_config(index_dir),
            ).validate_dataset(canonical_dataset, identity, DATASET_KINDS)
        elif canonical_dataset.get("dataset_kind") == "generated_candidate":
            from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import validate_candidate_gold_binding

            validate_candidate_gold_binding(canonical_dataset, index_dir=index_dir)

        run_id = _new_run_id("eval")
        run_dir = _run_dir(run_id)
        dataset_path = run_dir / "dataset_snapshot.json"
        _write_json(dataset_path, canonical_dataset)
        dataset_identity = evaluation_identity(dataset_path)
        dataset_identity.pop("dataset_path", None)
        dataset_content_sha256 = _dataset_content_sha256(canonical_dataset)
        state = {
            "run_id": run_id,
            "kind": "evaluation",
            "status": "queued",
            "created_at": _timestamp(),
            "started_at": "",
            "finished_at": "",
            "current_stage": "queued",
            "cancel_requested": False,
            "execution_backend": "persistent_worker",
            "job_id": run_id,
            "ingestion_run_id": ingestion_run_id,
            "index_version": index_version,
            "question_count": len(samples),
            "input_identity": dataset_identity,
            "input_content_sha256": dataset_content_sha256,
            "strategy_profile": dict(strategy_profile or {}),
            "batch_id": str(batch_id or ""),
            "batch_position": int(batch_position or 0),
            "batch_size": int(batch_size or 0),
            "retrieval_options": dict(retrieval_options or {}),
            "ragas_options": dict(ragas_options or {}),
            "steps": list(steps or []),
            "dataset_path": str(dataset_path),
            "result_path": str(run_dir / "result.json"),
            "events": [],
        }
        with self._lock:
            self._runs[run_id] = state
            _write_json(run_dir / "run.json", state)
        self._emit(run_id, "run_created", "创建 staged index RAG 评测任务", {
            "index_version": index_version,
            "question_count": len(samples),
        })

        self._enqueue_persistent_run(run_id, "evaluation")
        return self.get(run_id)

    def _enqueue_persistent_run(self, run_id: str, job_kind: str) -> None:
        """将已落盘的隔离评测长任务加入统一队列，入队失败时失败关闭。"""
        try:
            from app.rag_eval.job_service import enqueue_job

            enqueue_job(run_id, job_kind, {"run_id": run_id, "job_kind": job_kind})
        except Exception as exc:
            self._set_status(
                run_id,
                "failed",
                finished_at=_timestamp(),
                current_stage="failed",
                error=f"RAG 任务入队失败: {exc}",
            )
            self._emit(run_id, "run_error", "RAG 任务入队失败", {"error": str(exc)})
            raise

    def get(self, run_id: str) -> Dict[str, Any]:
        """返回单次隔离运行状态。"""
        state = self._load(run_id)
        if state.get("execution_backend") == "persistent_worker" and _is_stale_evaluation(state):
            self._mark_stale_evaluation(run_id)
            state = self._load(run_id)
        return self._public_state(state)

    def mark_worker_started(self, run_id: str, worker_id: str) -> None:
        """记录评测由哪个持久 worker 接管，并立即建立心跳文件。"""
        self._set_status(
            run_id,
            "running",
            started_at=_timestamp(),
            current_stage={
                "ingestion": "ingest",
                "candidate_generation": "generation",
                "rag_query": "runtime",
                "evaluation": "evaluation",
                "dataset_governance": "governance",
                "tuning_dataset_governance": "preflight",
            }.get(str(self._load(run_id).get("kind") or ""), "running"),
            worker_id=worker_id,
        )
        self.touch_worker_heartbeat(run_id, worker_id)

    def touch_worker_heartbeat(self, run_id: str, worker_id: str) -> None:
        """更新独立心跳文件，避免和 run.json 事件写入互相覆盖。"""
        _write_json(
            _run_dir(run_id) / "worker_heartbeat.json",
            {"run_id": run_id, "worker_id": worker_id, "heartbeat_at": _timestamp()},
        )

    def mark_worker_timeout(self, run_id: str, message: str) -> None:
        """把 worker 失联的评测落盘为失败，并让 SSE 收到终止事件。"""
        state = self._load(run_id)
        if (
            state.get("kind") not in _PERSISTENT_WORKER_KINDS
            or state.get("execution_backend") != "persistent_worker"
            or state.get("status") not in _ACTIVE_EVALUATION_STATUSES
        ):
            return
        self._set_status(
            run_id,
            "failed",
            finished_at=_timestamp(),
            current_stage="failed",
            error=message,
            status_reason="worker heartbeat timeout",
        )
        self._emit(run_id, "run_error", "RAG 任务 worker 异常退出或心跳超时", {"error": message})

    def request_lease_abort(self, run_id: str, worker_id: str) -> None:
        """租约失效时请求长任务在下一个检查点停止，不直接触碰 SQL 终态。"""
        del worker_id
        state = self._load(run_id)
        if state.get("status") not in _ACTIVE_EVALUATION_STATUSES:
            return
        self._set_status(run_id, "cancelling", cancel_requested=True)

    def mark_worker_fenced(self, run_id: str, message: str) -> None:
        """租约失效时把 run.json 收敛为 failed，与 SQL 终态对齐，防止成功/失败不一致。"""
        self._set_status(
            run_id,
            "failed",
            finished_at=_timestamp(),
            current_stage="failed",
            error=message,
            status_reason="lease lost",
        )
        self._emit(run_id, "run_error", "RAG 任务租约失效，结果被丢弃", {"error": message})

    def mark_worker_cancelled(self, run_id: str, message: str) -> None:
        """把 run.json 收敛为 cancelled，与 SQL 终态对齐，避免成功/取消分裂。"""
        self._set_status(
            run_id,
            "cancelled",
            finished_at=_timestamp(),
            current_stage="cancelled",
            error=message,
            status_reason="cancelled by user",
        )
        self._emit(run_id, "run_cancelled", "RAG 任务已取消", {"error": message})

    def _mark_stale_evaluation(self, run_id: str) -> None:
        """在读取失活评测时，将其从 active 收敛为 failed。"""
        message = "评测任务长时间没有活动，已自动标记为失败"
        state = self._load(run_id)
        if state.get("execution_backend") == "persistent_worker":
            try:
                from app.rag_eval.job_service import fail_job

                fail_job(run_id, message)
            except Exception:
                # run.json 仍然必须收敛；SQL 会由下一次 worker reconcile 再检查。
                pass
        self.mark_worker_timeout(run_id, message)

    def delete_evaluation_run(self, run_id: str, *, force: bool = False) -> Dict[str, Any]:
        """删除已结束或确认失活的隔离评测，不触碰关联摄取目录。"""
        state = self._load(run_id)
        if state.get("kind") != "evaluation":
            raise ValueError("run is not an evaluation")
        stale = _is_stale_evaluation(state)
        if stale and state.get("execution_backend") == "persistent_worker":
            try:
                from app.rag_eval.job_service import fail_evaluation

                fail_evaluation(run_id, "评测任务长时间没有活动，已自动标记为失败")
            except Exception:
                # 删除主记录仍由下方文件状态决定；SQL 状态由下一次 worker reconcile 收敛。
                pass
        if state.get("status") in _ACTIVE_EVALUATION_STATUSES and not (force and stale):
            message = "run is stale; force delete is required" if stale else "run is still running"
            return {"run_id": run_id, "status": "running", "stale": stale, "message": message}

        target = _run_dir(run_id)
        if not target.is_dir():
            raise KeyError(f"isolated evaluation not found: {run_id}")
        with self._lock:
            if state.get("status") in _ACTIVE_EVALUATION_STATUSES:
                state["cancel_requested"] = True
            shutil.rmtree(target)
            self._runs.pop(run_id, None)
            self._queues.pop(run_id, None)
        return {"run_id": run_id, "status": "deleted", "deleted": True}

    def delete_ingestion_run(self, run_id: str, *, cascade: bool = False) -> Dict[str, Any]:
        """删除终态摄取运行；级联模式只清理终态隔离下游产物。"""
        state = self._load(run_id)
        if state.get("kind") != "ingestion":
            raise ValueError("run is not an ingestion")
        if state.get("status") in _ACTIVE_INGESTION_STATUSES:
            return {
                "run_id": run_id,
                "status": "running",
                "message": "摄取任务仍在运行，请先取消并等待终态",
            }
        if state.get("status") not in _TERMINAL_RUN_STATUSES:
            raise ValueError("只能删除已结束的摄取运行")
        references = self._collect_run_references(state)
        if not cascade:
            self._raise_if_referenced(references)
            return self._remove_run_directory(run_id)

        blockers = [
            reference
            for reference in references
            if reference["type"] in {"active_pointer", "gold"}
            or reference["type"] == "run"
            and (
                reference["status"] in _ACTIVE_INGESTION_STATUSES
                or reference["kind"] not in _CASCADE_RUN_KINDS
            )
        ]
        self._raise_if_referenced(blockers, cascade=True)
        deleted_run_ids = [run_id] + [reference["run_id"] for reference in references if reference["type"] == "run"]
        result = self._remove_run_directories(deleted_run_ids)
        result["cascade"] = True
        return result

    def delete_derived_run(self, run_id: str) -> Dict[str, Any]:
        """删除没有下游引用的终态候选、治理或 staged RAG 运行。"""
        state = self._load(run_id)
        if state.get("kind") not in _DELETABLE_DERIVED_KINDS:
            raise ValueError("run type does not support derived artifact deletion")
        if state.get("status") in _ACTIVE_INGESTION_STATUSES:
            return {
                "run_id": run_id,
                "status": "running",
                "message": "运行仍在进行，请先取消并等待终态",
            }
        if state.get("status") not in _TERMINAL_RUN_STATUSES:
            raise ValueError("只能删除已结束的运行")
        self._ensure_run_has_no_references(state)
        return self._remove_run_directory(run_id)

    def _ensure_run_has_no_references(self, target_state: Dict[str, Any]) -> None:
        """删除前阻止下游运行、正式 Gold 或生产 active pointer 失去来源。"""
        self._raise_if_referenced(self._collect_run_references(target_state))

    @staticmethod
    def _raise_if_referenced(references: List[Dict[str, str]], *, cascade: bool = False) -> None:
        """把引用集合转换为稳定的冲突提示；级联模式只用于区分提示语。"""
        if not references:
            return
        labels = [
            f"{reference['run_id']}({reference['kind']}/{reference['status']})"
            if reference["type"] == "run"
            else reference["label"]
            for reference in references
        ]
        prefix = "级联删除被阻断，仍存在引用：" if cascade else "运行仍被下游或正式产物引用："
        raise ValueError(prefix + "、".join(dict.fromkeys(labels)))

    def _collect_run_references(self, target_state: Dict[str, Any]) -> List[Dict[str, str]]:
        """收集下游运行、正式 Gold 和 active pointer 引用，不执行任何删除。"""
        run_id = str(target_state.get("run_id") or "")
        kind = str(target_state.get("kind") or "")
        index_version = str(target_state.get("index_version") or "")
        dataset_id = str(target_state.get("dataset_id") or "")
        references: List[Dict[str, str]] = []

        if ISOLATED_RUN_ROOT.is_dir():
            for run_dir in ISOLATED_RUN_ROOT.iterdir():
                if not run_dir.is_dir() or run_dir.name == run_id:
                    continue
                state = _read_json(run_dir / "run.json")
                if not state:
                    continue
                if kind == "ingestion":
                    matches = (
                        state.get("ingestion_run_id") == run_id
                        or (index_version and state.get("index_version") == index_version)
                    )
                else:
                    identity_values = {value for value in (run_id, dataset_id) if value}
                    matches = bool(identity_values) and _json_contains_value(state, identity_values)
                if matches:
                    references.append(
                        {
                            "type": "run",
                            "run_id": str(state.get("run_id") or run_dir.name),
                            "kind": str(state.get("kind") or "unknown"),
                            "status": str(state.get("status") or "unknown"),
                        }
                    )

        try:
            from Agent.knowledge_base.rag.operation_datasets import benchmark_v2

            if kind == "ingestion" and index_version:
                active_pointer = _read_reference_json(Path(benchmark_v2.DEFAULT_ACTIVE_POINTER))
                if _json_contains_value(active_pointer, {index_version}):
                    references.append({"type": "active_pointer", "label": "production_active_index"})
            gold_payload = _read_reference_json(Path(benchmark_v2.DEFAULT_GOLD_V2_OUTPUT))
            identity_values = {value for value in (run_id, index_version, dataset_id) if value}
            if identity_values and _json_contains_value(gold_payload, identity_values):
                references.append({"type": "gold", "label": "gold_v2"})
        except (ImportError, OSError, TypeError, ValueError):
            raise ValueError("无法确认正式索引或 Gold 引用状态，暂不能删除")
        return references

    def _remove_run_directory(self, run_id: str) -> Dict[str, Any]:
        """在终态检查完成后删除一个受路径校验保护的运行目录。"""
        result = self._remove_run_directories([run_id])
        result.pop("deleted_run_ids", None)
        return result

    def _remove_run_directories(self, run_ids: List[str]) -> Dict[str, Any]:
        """在同一内存锁内删除一组已完成引用检查的运行目录。"""
        if not run_ids:
            raise ValueError("at least one isolated run is required")
        targets = [(run_id, _run_dir(run_id)) for run_id in run_ids]
        if any(not target.is_dir() for _, target in targets):
            missing = next(run_id for run_id, target in targets if not target.is_dir())
            raise KeyError(f"isolated run not found: {missing}")
        with self._lock:
            for run_id, target in targets:
                shutil.rmtree(target)
                self._runs.pop(run_id, None)
                self._queues.pop(run_id, None)
        return {"run_id": run_ids[0], "status": "deleted", "deleted": True, "deleted_run_ids": list(run_ids)}

    def list_ingestion_history(
        self,
        *,
        status: Optional[str] = None,
        source_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """从持久化 run.json 枚举摄取运行，供页面在丢失本地 run id 时恢复。"""
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 50), 1), 100)
        records: List[Dict[str, Any]] = []
        root = ISOLATED_RUN_ROOT
        if root.is_dir():
            for run_dir in root.iterdir():
                if not run_dir.is_dir():
                    continue
                state = _read_json(run_dir / "run.json")
                if state.get("kind") != "ingestion":
                    continue
                if status and state.get("status") != status:
                    continue
                source_ids = state.get("source_ids", [])
                if source_id and source_id not in source_ids:
                    continue
                records.append(self._public_state(state))

        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        total = len(records)
        total_pages = max((total + safe_page_size - 1) // safe_page_size, 1)
        safe_page = min(safe_page, total_pages)
        start = (safe_page - 1) * safe_page_size
        return {
            "items": records[start:start + safe_page_size],
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
        }

    def get_result(self, run_id: str) -> Dict[str, Any]:
        """读取已完成 RAG 任务的结果，不向调用方暴露内部文件路径。"""
        state = self._load(run_id)
        if state.get("kind") not in {"rag_query", "evaluation", "candidate_generation", "dataset_governance", "tuning_dataset_governance"}:
            raise ValueError("run does not expose a result")
        result_path = Path(str(state.get("result_path") or ""))
        result_statuses = {"succeeded", "failed"} if state.get("kind") == "evaluation" else {"succeeded"}
        if state.get("status") not in result_statuses or not result_path.is_file():
            raise ValueError("run result is not available")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if state.get("kind") == "candidate_generation":
            result.pop("dataset_path", None)
            result.pop("audit_path", None)
            result["candidate_artifact_name"] = state.get("candidate_artifact_name", "candidate.json")
            result["audit_artifact_name"] = state.get("audit_artifact_name", "candidate.json.audit.json")
        return result

    def save_candidate_review(
        self,
        run_id: str,
        *,
        reviewer: str,
        decisions: List[Dict[str, Any]],
        updates: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """保存逐题审核，并把编辑结果写成新的候选数据集 revision。"""
        state = self._load(run_id)
        if state.get("kind") != "candidate_generation" or state.get("status") != "succeeded":
            raise ValueError("candidate generation must succeed before review")
        reviewer = str(reviewer or "").strip()
        if not reviewer or len(reviewer) > 120:
            raise ValueError("reviewer is required and must be at most 120 characters")
        if not isinstance(decisions, list) or not decisions:
            raise ValueError("decisions must be a non-empty list")
        candidate_path = Path(str(state.get("candidate_path") or ""))
        if not candidate_path.is_file():
            raise ValueError("candidate dataset is unavailable")
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        samples = candidate.get("samples")
        if not isinstance(samples, list):
            raise ValueError("candidate dataset samples are invalid")
        by_id = {str(sample.get("sample_id")): sample for sample in samples if isinstance(sample, dict)}
        normalized_decisions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("each review decision must be an object")
            sample_id = str(decision.get("sample_id") or "").strip()
            status = str(decision.get("decision") or "").strip()
            if sample_id not in by_id or sample_id in seen:
                raise ValueError("review decision contains an unknown or duplicate sample_id")
            if status not in {"approved", "rejected", "needs_revision"}:
                raise ValueError("review decision has an unsupported decision")
            seen.add(sample_id)
            normalized_decisions.append({"sample_id": sample_id, "decision": status, "note": str(decision.get("note") or "")[:1000]})

        for update in updates or []:
            if not isinstance(update, dict):
                raise ValueError("each candidate update must be an object")
            sample_id = str(update.get("sample_id") or "").strip()
            sample = by_id.get(sample_id)
            if sample is None:
                raise ValueError("candidate update contains an unknown sample_id")
            for field in ("question", "reference_answer", "expected_claims", "gold_evidence"):
                if field not in update:
                    continue
                value = update[field]
                if field in {"question", "reference_answer"}:
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"{field} must be a non-empty string")
                    sample[field] = value.strip()
                elif not isinstance(value, list):
                    raise ValueError(f"{field} must be a list")
                else:
                    sample[field] = value

        revision = f"{candidate.get('dataset_revision', 'unversioned')}_reviewed_{uuid.uuid4().hex[:10]}"
        reviewed = dict(candidate)
        reviewed["dataset_revision"] = revision
        reviewed["review"] = {
            "schema_version": "rag_candidate_review_v1",
            "reviewer": reviewer,
            "decision_count": len(normalized_decisions),
            "updated_sample_count": len(updates or []),
        }
        for sample in samples:
            source = dict(sample.get("source") or {})
            source.update({"review_status": "reviewed", "reviewer": reviewer})
            sample["source"] = source
        reviewed_path = _run_dir(run_id) / f"candidate_reviewed_{uuid.uuid4().hex[:10]}.json"
        from Agent.knowledge_base.rag.operation_datasets.candidate_generation import _write_dataset

        _write_dataset(reviewed_path, reviewed)
        review_path = _run_dir(run_id) / f"candidate_review_{uuid.uuid4().hex[:10]}.json"
        review_manifest = {
            "schema_version": "rag_candidate_review_v1",
            "reviewer": reviewer,
            "candidate_dataset_id": reviewed.get("dataset_id"),
            "candidate_dataset_revision": revision,
            "candidate_sha256": hashlib.sha256(reviewed_path.read_bytes()).hexdigest(),
            "decisions": normalized_decisions,
        }
        _write_json(review_path, review_manifest)
        self._set_status(
            run_id,
            "succeeded",
            candidate_path=str(reviewed_path),
            candidate_dataset_revision=revision,
            candidate_artifact_name=reviewed_path.name,
            review_manifest_artifact_name=review_path.name,
            review_status="reviewed",
        )
        return {
            "run_id": run_id,
            "candidate_dataset_id": reviewed.get("dataset_id"),
            "candidate_dataset_revision": revision,
            "candidate_artifact_name": reviewed_path.name,
            "review_manifest_artifact_name": review_path.name,
            "decision_count": len(normalized_decisions),
            "updated_sample_count": len(updates or []),
        }

    def rebind_candidate_to_current_index(
        self,
        run_id: str,
        *,
        ingestion_run_id: str,
        index_version: str,
    ) -> Dict[str, Any]:
        """将历史候选题 locator 重绑到调用方明确选择的 staged index。"""
        state = self._load(run_id)
        if state.get("kind") != "candidate_generation" or state.get("status") != "succeeded":
            raise ValueError("candidate generation must succeed before locator rebind")
        candidate_path = Path(str(state.get("candidate_path") or ""))
        if not candidate_path.is_file():
            raise ValueError("candidate dataset is unavailable")
        ingestion_run_id = str(ingestion_run_id or "").strip()
        index_version = str(index_version or "").strip()
        identity = self.resolve_staged_index(ingestion_run_id, index_version)
        index_dir = identity.index_dir
        rebound_path = _run_dir(run_id) / f"candidate_rebound_{uuid.uuid4().hex[:10]}.json"
        from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import rebind_candidate_dataset_to_index

        result = rebind_candidate_dataset_to_index(
            candidate_path=candidate_path,
            index_dir=index_dir,
            output_path=rebound_path,
        )
        rebound = json.loads(rebound_path.read_text(encoding="utf-8"))
        previous_reviewer = ""
        previous_review_name = str(state.get("review_manifest_artifact_name") or "")
        if previous_review_name:
            previous_review = _read_json(_run_dir(run_id) / previous_review_name)
            previous_reviewer = str(previous_review.get("reviewer") or "")
        review_path = _run_dir(run_id) / f"candidate_rebind_review_{uuid.uuid4().hex[:10]}.json"
        _write_json(review_path, {
            "schema_version": "rag_candidate_review_v1",
            "reviewer": previous_reviewer or "rebind-pending-review",
            "candidate_dataset_id": rebound["dataset_id"],
            "candidate_dataset_revision": rebound["dataset_revision"],
            "candidate_sha256": hashlib.sha256(rebound_path.read_bytes()).hexdigest(),
            "decisions": [
                {
                    "sample_id": sample["sample_id"],
                    "decision": "needs_revision",
                    "note": "Gold locator 已重绑到当前索引，需重新核对问题、答案和证据后再批准。",
                }
                for sample in rebound["samples"]
            ],
        })
        self._set_status(
            run_id,
            "succeeded",
            candidate_path=str(rebound_path),
            candidate_dataset_revision=rebound["dataset_revision"],
            candidate_artifact_name=rebound_path.name,
            review_manifest_artifact_name=review_path.name,
            review_status="requires_reapproval",
            ingestion_run_id=ingestion_run_id,
            index_version=index_version,
        )
        return {
            "run_id": run_id,
            "ingestion_run_id": ingestion_run_id,
            "index_version": index_version,
            "candidate_artifact_name": rebound_path.name,
            "review_manifest_artifact_name": review_path.name,
            **result,
        }

    def freeze_candidate_gold_v2(
        self,
        run_id: str,
        *,
        expected_ingestion_run_id: str,
        expected_index_version: str,
        replace_existing: bool = False,
    ) -> Dict[str, Any]:
        """用已保存的候选审核清单尝试冻结 Gold v2；失败时保持 fail-closed。"""
        state = self._load(run_id)
        if state.get("kind") != "candidate_generation":
            raise ValueError("run is not a candidate generation")
        if (
            str(state.get("ingestion_run_id") or "") != str(expected_ingestion_run_id or "")
            or str(state.get("index_version") or "") != str(expected_index_version or "")
        ):
            raise ValueError("candidate review is not rebound to the selected staged index; rebind and reapprove it first")
        candidate_path = Path(str(state.get("candidate_path") or ""))
        review_name = str(state.get("review_manifest_artifact_name") or "")
        review_path = _run_dir(run_id) / review_name
        if not candidate_path.is_file() or not review_path.is_file():
            raise ValueError("candidate review must be saved before Gold v2 freeze")
        from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import freeze_pearl_gold_v2

        ingestion_run_id = str(state.get("ingestion_run_id") or "")
        index_version = str(state.get("index_version") or "")
        if not ingestion_run_id:
            candidates = []
            if ISOLATED_RUN_ROOT.is_dir():
                for existing_run_dir in ISOLATED_RUN_ROOT.iterdir():
                    if not existing_run_dir.is_dir():
                        continue
                    existing = _read_json(existing_run_dir / "run.json")
                    if (
                        existing.get("kind") == "ingestion"
                        and existing.get("status") == "staged"
                        and existing.get("index_version") == index_version
                    ):
                        candidates.append(existing)
            if not candidates:
                raise ValueError("candidate review does not belong to an available staged ingestion")
            candidates.sort(key=lambda item: str(item.get("finished_at") or item.get("created_at") or ""), reverse=True)
            ingestion_run_id = str(candidates[0].get("run_id") or "")
        identity = self.resolve_staged_index(ingestion_run_id, index_version)
        result = freeze_pearl_gold_v2(
            candidate_path=candidate_path,
            review_manifest_path=review_path,
            index_dir=identity.index_dir,
            replace_existing=replace_existing,
        )
        result.pop("dataset_path", None)
        _write_json(_run_dir(run_id) / "gold_v2_freeze.json", result)
        return {**result, "artifact_name": "gold_v2_freeze.json"}

    def bind_baseline_v2(self) -> Dict[str, Any]:
        """绑定默认 Gold v2、只读 active pointer 与 active_current retrieval。"""
        from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import (
            DEFAULT_GOLD_V2_OUTPUT,
            bind_baseline_v2,
        )

        result = bind_baseline_v2(dataset_path=DEFAULT_GOLD_V2_OUTPUT)
        baseline_path = str(result.get("baseline_path") or "")
        result.pop("baseline_path", None)
        result["baseline_artifact_name"] = Path(baseline_path).name or "baseline_v2.json"
        return result

    def list_evaluation_history(
        self,
        *,
        dataset_id: Optional[str] = None,
        index_version: Optional[str] = None,
        status: Optional[str] = None,
        source_name: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """列出隔离 evaluation run 摘要，不读取 legacy output 或 active pointer。"""
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 50), 1), 100)
        since_dt = _parse_timestamp(since) if since else None
        until_dt = _parse_timestamp(until) if until else None
        records: List[Dict[str, Any]] = []
        root = ISOLATED_RUN_ROOT
        if root.is_dir():
            for run_dir in root.iterdir():
                if not run_dir.is_dir():
                    continue
                state = _read_json(run_dir / "run.json")
                if state.get("kind") != "evaluation":
                    continue
                record = _evaluation_history_record(run_dir, state)
                identity = record.get("dataset_identity", {})
                if dataset_id and identity.get("dataset_id") != dataset_id:
                    continue
                if index_version and record.get("index_version") != index_version:
                    continue
                if status and record.get("status") != status:
                    continue
                if source_name:
                    source_names = [str(item) for item in record.get("source_names", [])]
                    if source_name not in source_names:
                        continue
                created_at = record.get("created_at", "")
                created_dt = _parse_timestamp(created_at) if created_at else None
                if since_dt and (created_dt is None or created_dt < since_dt):
                    continue
                if until_dt and (created_dt is None or created_dt > until_dt):
                    continue
                records.append(record)

        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        total = len(records)
        total_pages = max((total + safe_page_size - 1) // safe_page_size, 1)
        safe_page = min(safe_page, total_pages)
        start = (safe_page - 1) * safe_page_size
        return {
            "items": records[start:start + safe_page_size],
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
        }

    def get_evaluation_diff(self, base_run_id: str, candidate_run_id: str) -> Dict[str, Any]:
        """比较两个通用 rag_eval_v1 运行，拒绝跨题集身份的伪 A/B。"""
        base_state = self._load(base_run_id)
        candidate_state = self._load(candidate_run_id)
        if base_state.get("kind") != "evaluation" or candidate_state.get("kind") != "evaluation":
            raise ValueError("both runs must be evaluations")

        base_dir = _run_dir(base_run_id)
        candidate_dir = _run_dir(candidate_run_id)
        base_record = _evaluation_history_record(base_dir, base_state)
        candidate_record = _evaluation_history_record(candidate_dir, candidate_state)
        if base_record.get("dataset_identity") != candidate_record.get("dataset_identity"):
            raise ValueError("evaluation runs use different dataset identities")

        base_samples = _sample_result_map(base_dir)
        candidate_samples = _sample_result_map(candidate_dir)
        sample_deltas = []
        for sample_id in sorted(set(base_samples) | set(candidate_samples)):
            base_sample = base_samples.get(sample_id, {})
            candidate_sample = candidate_samples.get(sample_id, {})
            metric_rows = _metric_deltas(
                {
                    **(base_sample.get("retrieval") or {}),
                    **(base_sample.get("ragas") or {}),
                },
                {
                    **(candidate_sample.get("retrieval") or {}),
                    **(candidate_sample.get("ragas") or {}),
                },
            )
            deltas = [row["delta"] for row in metric_rows if row["delta"] is not None]
            if not deltas:
                classification = "unscored"
            elif all(delta == 0 for delta in deltas):
                classification = "unchanged"
            elif any(delta > 0 for delta in deltas) and any(delta < 0 for delta in deltas):
                classification = "mixed"
            elif any(delta > 0 for delta in deltas):
                classification = "improved"
            else:
                classification = "regressed"
            sample_deltas.append({
                "sample_id": sample_id,
                "question": candidate_sample.get("question") or base_sample.get("question", ""),
                "classification": classification,
                "metrics": metric_rows,
                "base_bad_case": bool(base_sample.get("bad_case")),
                "candidate_bad_case": bool(candidate_sample.get("bad_case")),
            })

        base_config = _read_json(base_dir / "run_manifest.json").get("config", {})
        candidate_config = _read_json(candidate_dir / "run_manifest.json").get("config", {})
        return {
            "available": True,
            "base": base_record,
            "candidate": candidate_record,
            "metric_deltas": _metric_deltas(
                base_record.get("key_metrics", {}),
                candidate_record.get("key_metrics", {}),
            ),
            "config_deltas": _config_deltas(base_config, candidate_config),
            "sample_deltas": sample_deltas,
            "summary": {
                "sample_count": len(sample_deltas),
                "improved_count": sum(item["classification"] == "improved" for item in sample_deltas),
                "regressed_count": sum(item["classification"] == "regressed" for item in sample_deltas),
                "persistent_bad_case_count": sum(
                    item["base_bad_case"] and item["candidate_bad_case"] for item in sample_deltas
                ),
            },
        }

    def get_artifact_path(self, run_id: str, artifact_name: str) -> Path:
        """解析评测产物路径并阻止跳出该次评测目录。"""
        state = self._load(run_id)
        if state.get("kind") not in {"evaluation", "candidate_generation", "dataset_governance", "tuning_dataset_governance"}:
            raise ValueError("run does not expose artifacts")
        name = str(artifact_name or "").replace("\\", "/").lstrip("/")
        if not name or ".." in Path(name).parts:
            raise ValueError("invalid artifact name")
        root = _run_dir(run_id).resolve()
        path = (root / name).resolve()
        path.relative_to(root)
        if not path.is_file():
            raise KeyError(f"run artifact not found: {name}")
        return path

    def subscribe(self, run_id: str) -> queue.Queue:
        """订阅并回放隔离运行事件。"""
        state = self._load(run_id)
        with self._lock:
            event_queue: queue.Queue = queue.Queue(maxsize=512)
            self._queues[run_id] = event_queue
            for event in state.get("events", []):
                try:
                    event_queue.put_nowait(event)
                except queue.Full:
                    break
            return event_queue

    def unsubscribe(self, run_id: str) -> None:
        """解除 SSE 订阅。"""
        with self._lock:
            self._queues.pop(run_id, None)

    def read_events(self, run_id: str, after_event_id: int = 0) -> tuple[list[Dict[str, Any]], int]:
        """按单调递增 event_id 游标读取跨进程事件，返回事件和新的游标。

        事件数组只保留最近 _MAX_EVENTS 条，游标不再依赖数组长度，
        而是跟随单调递增的 event_id，避免事件数超过上限后新事件永久漏读。
        """
        state = self._load(run_id)
        events = state.get("events") if isinstance(state.get("events"), list) else []
        threshold = max(int(after_event_id or 0), 0)
        selected = [
            event for event in events
            if isinstance(event, dict) and int(event.get("event_id") or 0) > threshold
        ]
        next_cursor = max([threshold] + [int(event.get("event_id") or 0) for event in selected])
        return selected, next_cursor

    def cancel(self, run_id: str) -> Dict[str, Any]:
        """请求在当前页或当前问题完成后取消任务。"""
        state = self._load(run_id)
        if state.get("status") not in {"created", "queued", "running", "cancelling"}:
            return self._public_state(state)
        self._set_status(run_id, "cancelling", cancel_requested=True)
        self._emit(run_id, "cancel_requested", "已请求取消，等待当前阶段结束", {})
        if state.get("kind") in _PERSISTENT_WORKER_KINDS and state.get("execution_backend") == "persistent_worker":
            from app.rag_eval.job_service import cancel_job

            job = cancel_job(run_id)
            if job and job.get("status") == "cancelled":
                self._set_status(
                    run_id,
                    "cancelled",
                    finished_at=_timestamp(),
                    current_stage="cancelled",
                    error="cancelled by user",
                )
                self._emit(run_id, "run_cancelled", "RAG 任务已取消", {"error": "cancelled by user"})
        return self.get(run_id)

    def _run_candidate_generation(self, run_id: str, ingestion_run_id: str, index_version: str) -> None:
        """执行候选生成；Ragas 调用期间的取消在当前调用返回后收敛。"""
        state = self._load(run_id)
        options = {
            "dataset_id": state.get("dataset_id") or None,
            "max_units": int(state.get("max_units") or 48),
            "questions_per_unit": int(state.get("questions_per_unit") or 1),
            "max_workers": int(state.get("max_workers") or 1),
        }
        self._set_status(run_id, "running", started_at=_timestamp(), current_stage="generation")
        self._emit(run_id, "stage_start", "开始使用 Ragas generate_with_chunks 生成候选题", {
            "stage": "generation",
            "generator": "ragas_0.4.3_generate_with_chunks",
        })
        try:
            if self._load(run_id).get("cancel_requested"):
                raise InterruptedError("candidate generation was cancelled")
            from Agent.knowledge_base.rag.operation_datasets.candidate_generation import expand_candidate_dataset

            result = expand_candidate_dataset(
                _run_dir(ingestion_run_id) / "indexes" / index_version,
                output_path=_run_dir(run_id) / "candidate.json",
                **options,
                progress_callback=lambda data: self._emit(
                    run_id,
                    "candidate_progress",
                    f"候选生成阶段：{data.get('stage', 'running')}",
                    data,
                ),
                cancel_checker=lambda: bool(self._load(run_id).get("cancel_requested")),
            )
            result_path = _run_dir(run_id) / "result.json"
            _write_json(result_path, {"schema_version": "rag_candidate_run_v1", "run_id": run_id, **result})
            if self._load(run_id).get("cancel_requested"):
                raise InterruptedError("candidate generation was cancelled")
            self._set_status(
                run_id,
                "succeeded",
                finished_at=_timestamp(),
                current_stage="done",
                result_path=str(result_path),
                dataset_id=result.get("dataset_id", ""),
                dataset_revision=result.get("dataset_revision", ""),
                question_count=int(result.get("accepted_count", 0)),
                candidate_dataset_revision=result.get("dataset_revision", ""),
            )
            self._emit(run_id, "run_done", "候选题生成和基础筛选完成", {
                "status": "succeeded",
                "result_available": True,
                "accepted_count": result.get("accepted_count", 0),
                "rejected_count": result.get("rejected_count", 0),
            })
        except InterruptedError as exc:
            self._set_status(run_id, "cancelled", finished_at=_timestamp(), current_stage="cancelled", error=str(exc))
            self._emit(run_id, "run_cancelled", "候选题生成已取消", {"error": str(exc)})
        except Exception as exc:
            self._set_status(run_id, "failed", finished_at=_timestamp(), current_stage="failed", error=str(exc))
            self._emit(run_id, "run_error", f"候选题生成失败: {type(exc).__name__}", {"error": str(exc)})

    def _run_tuning_dataset_governance(self, run_id: str) -> None:
        """在单个 staged index 内循环替换低分题，不创建正式 evaluation run。

        治理前先建立同索引逐题证据账本：历史已通过题沿用证据直接保留，
        已缓存失败题直接淘汰等待替补，只有无可靠证据的题进入首轮评测。
        基线优先链式取同索引最近一次登记的调参集；人工保护题始终来自当前
        Gold。宽松复用语义不比较检索或 judge 配置，但每条沿用记录都保留出处。
        """
        state = self._load(run_id)
        ingestion_run_id = str(state.get("ingestion_run_id") or "")
        index_version = str(state.get("index_version") or "")
        run_dir = _run_dir(run_id)
        self._set_status(run_id, "running", started_at=state.get("started_at") or _timestamp(), current_stage="preflight")
        try:
            from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import DEFAULT_GOLD_V2_OUTPUT, validate_frozen_gold_binding
            from Agent.knowledge_base.rag.operation_datasets.calibrated_candidate_audit import audit_candidate_dataset, default_calibrated_reviewer
            from Agent.knowledge_base.rag.operation_datasets.index_bound_tuning import (
                TuningPolicy,
                partition_by_ledger,
                run_tuning_loop,
            )
            from Agent.knowledge_base.rag.rag_eval.contracts import load_eval_dataset_bundle
            from app.rag_eval.isolated_evaluation import execute_isolated_evaluation

            index_dir = _run_dir(ingestion_run_id) / "indexes" / index_version
            validate_frozen_gold_binding(DEFAULT_GOLD_V2_OUTPUT, index_dir=index_dir)
            gold_bundle = load_eval_dataset_bundle(DEFAULT_GOLD_V2_OUTPUT)
            gold_samples = [dict(item) for item in gold_bundle.get("samples") or []]
            protected_samples = [item for item in gold_samples if not _sample_generated(item)]
            gold_auto = [item for item in gold_samples if _sample_generated(item)]

            baseline_payload, baseline_errors = _latest_registered_tuning_baseline(index_version)
            if baseline_payload is not None:
                registered_auto = [
                    dict(item) for item in baseline_payload.get("samples") or []
                    if isinstance(item, dict) and _sample_generated(dict(item))
                ]
                if registered_auto:
                    baseline_source = f"registered:{baseline_payload.get('dataset_revision') or 'unknown'}"
                    source_dataset = dict(baseline_payload)
                    auto_pool = registered_auto
                else:
                    baseline_source = "pearl_gold_v2"
                    source_dataset = dict(gold_bundle)
                    auto_pool = list(gold_auto)
            else:
                baseline_source = "pearl_gold_v2"
                source_dataset = dict(gold_bundle)
                auto_pool = list(gold_auto)
            if not auto_pool:
                raise ValueError("基线中没有可治理的自动生成题")

            policy = TuningPolicy(target_count=int(state.get("target_count") or 48), minimum_metric=float(state.get("minimum_metric") or 0.2))
            ledger, ledger_sources = collect_tuning_question_ledger(
                ingestion_run_id, index_version, exclude_run_id=run_id,
            )
            passed_cached, unknown_samples, dropped_cached = partition_by_ledger(
                auto_pool, ledger, minimum_metric=policy.minimum_metric,
            )
            sample_by_id = {str(item.get("sample_id") or ""): dict(item) for item in auto_pool}
            # 救援：历史失败运行中已实测达标的替补题不进入 Gold 基线，这里按
            # 同样门槛从轮次快照找回全文样本，使跨运行净进展可以累积。
            rescued_pool = _collect_rescued_generated_samples(
                ingestion_run_id, index_version, exclude_run_id=run_id,
            )
            baseline_question_hashes = {question_hash(item.get("question")) for item in auto_pool}
            rescued_candidates: Dict[str, Dict[str, Any]] = {}
            for rescued_id, rescued_sample in rescued_pool.items():
                if rescued_id in sample_by_id or rescued_id in passed_cached:
                    continue
                if question_hash(rescued_sample.get("question")) in baseline_question_hashes:
                    continue
                rescued_candidates[rescued_id] = rescued_sample
            rescued_passed, _rescued_unknown, _rescued_failed = partition_by_ledger(
                list(rescued_candidates.values()), ledger, minimum_metric=policy.minimum_metric,
            )
            initial_samples = (
                [sample_by_id[sample_id] for sample_id in passed_cached]
                + [rescued_candidates[rescued_id] for rescued_id in rescued_passed]
                + unknown_samples
            )
            self._emit(run_id, "governance_progress", "已构建逐题证据账本并完成播种", {
                "stage": "ledger_seed",
                "baseline_source": baseline_source,
                "baseline_count": len(auto_pool),
                "carried_pass_count": len(passed_cached),
                "rescued_carried_count": len(rescued_passed),
                "dropped_fail_count": len(dropped_cached),
                "to_evaluate_count": len(unknown_samples),
                "ledger_source_runs": ledger_sources,
            })
            for baseline_error in baseline_errors:
                self._emit(run_id, "governance_progress", f"跳过无法解析的历史调参集登记文件：{baseline_error}", {"stage": "ledger_seed"})
            _write_json(run_dir / "carried_evidence.json", {
                "schema_version": "rag_tuning_carried_evidence_v1",
                "run_id": run_id,
                "index_version": index_version,
                "baseline_source": baseline_source,
                "ledger_source_runs": ledger_sources,
                "minimum_metric": policy.minimum_metric,
                "records": {**passed_cached, **rescued_passed},
                "carried_sample_ids": sorted(passed_cached),
                "rescued_sample_ids": sorted(rescued_passed),
                "dropped_fail_sample_ids": dropped_cached,
            })
            self._set_status(
                run_id, "running",
                baseline_source=baseline_source,
                carried_evidence_count=len(passed_cached) + len(rescued_passed),
                dropped_fail_count=len(dropped_cached),
            )
            round_reports: list[dict[str, Any]] = []
            evaluated_ids: set[str] = set()
            rewrite_totals: Dict[str, int] = {"requested": 0, "rewritten": 0, "reapproved": 0}

            def generate(required: int, current_round: int, context: Dict[str, Any] | None = None) -> list[dict[str, Any]]:
                self._set_status(run_id, "running", current_stage="generation", round=current_round, missing_count=required)
                kept = list((context or {}).get("kept") or [])
                failed_ids = set((context or {}).get("failed_ids") or [])
                exclude_keys = _sample_locator_keys(kept + protected_samples)
                priority_keys = _sample_locator_keys(
                    [sample_by_id[sample_id] for sample_id in sorted(failed_ids) if sample_id in sample_by_id]
                    + [sample_by_id[sample_id] for sample_id in dropped_cached if sample_id in sample_by_id]
                )
                return [
                    dict(item)
                    for item in self._generate_governance_candidates(
                        state,
                        required,
                        round_number=current_round,
                        exclude_keys=exclude_keys,
                        priority_keys=priority_keys,
                    )[:required]
                ]

            def review(candidates: List[Dict[str, Any]], round_number: int) -> list[dict[str, Any]]:
                if not candidates:
                    return []
                self._set_status(run_id, "running", current_stage="review", round=round_number)
                candidate_path = run_dir / f"round_{round_number:03d}" / "candidates.json"
                _write_json(candidate_path, {
                    "schema_version": "rag_eval_v1", "dataset_id": f"tuning_{index_version}",
                    "dataset_kind": "generated_candidate", "dataset_revision": f"{run_id}_round_{round_number:03d}",
                    "source_snapshot": {"index_version": index_version}, "samples": [dict(item) for item in candidates],
                })
                reviewer = default_calibrated_reviewer
                if callable(GOVERNANCE_REVIEWER):
                    def injected_reviewer(sample: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
                        result = GOVERNANCE_REVIEWER(dict(sample), purpose="accept")
                        accepted = str(result.get("verdict") or "").strip().lower() in {"accept", "retain"}
                        confidence = float(result.get("confidence") or 0.0)
                        quote = str((evidence[0] if evidence else {}).get("content") or "").strip()
                        return {
                            "verdict": "retain" if accepted else "reject", "direct_entailment": "pass" if accepted else "fail",
                            "unsupported_inference": not accepted, "cross_evidence_relation": "not_applicable",
                            "question_quality": "pass" if accepted else "reject", "answer_scope": "evidence_only" if accepted else "overclaim",
                            "metric_contract": "aligned" if accepted else "mismatched", "confidence": confidence,
                            "reason": str(result.get("reason") or "injected reviewer"), "supporting_quotes": [quote[:500]] if quote else [],
                        }
                    reviewer = injected_reviewer
                audit = audit_candidate_dataset(candidate_path, index_dir=index_dir, reviewer=reviewer)
                _write_json(candidate_path.with_suffix(".audit.json"), audit)
                approved = {str(item.get("sample_id") or "") for item in audit.get("items") or [] if item.get("decision") == "approved"}
                accepted_samples = [dict(item) for item in candidates if str(item.get("sample_id") or "") in approved]
                # B：needs_revision 候选做一轮证据锚定改写后，用同一审核器复审。
                # 改写失败只降级为"沿用首轮审核结论"，不影响 fail-closed 主链路。
                rewrite_summary: Dict[str, Any] = {"requested": sum(
                    1 for item in audit.get("items") or [] if item.get("decision") == "needs_revision"
                ), "rewritten": 0, "reapproved": 0}
                try:
                    if rewrite_summary["requested"]:
                        from Agent.knowledge_base.rag.operation_datasets.calibrated_candidate_audit import (
                            default_evidence_rewriter,
                            rewrite_rejected_candidates,
                        )

                        rewrite_path = candidate_path.parent / "candidates_rewrite.json"
                        rewrite_result = rewrite_rejected_candidates(
                            candidate_path,
                            audit,
                            index_dir=index_dir,
                            output_path=rewrite_path,
                            rewriter=GOVERNANCE_CANDIDATE_REWRITER or default_evidence_rewriter,
                        )
                        rewrite_summary["rewritten"] = int(rewrite_result.get("rewritten_count") or 0)
                        if rewrite_summary["rewritten"]:
                            self._set_status(run_id, "running", current_stage="review_rewrite", round=round_number)
                            rewrite_audit = audit_candidate_dataset(rewrite_path, index_dir=index_dir, reviewer=reviewer)
                            _write_json(rewrite_path.with_suffix(".audit.json"), rewrite_audit)
                            reapproved_ids = {
                                str(item.get("sample_id") or "")
                                for item in rewrite_audit.get("items") or []
                                if item.get("decision") == "approved"
                            }
                            rewrite_payload = _read_json(rewrite_path)
                            for sample in rewrite_payload.get("samples") or []:
                                if isinstance(sample, dict) and str(sample.get("sample_id") or "") in reapproved_ids:
                                    accepted_samples.append(dict(sample))
                            rewrite_summary["reapproved"] = len(reapproved_ids)
                except Exception as exc:
                    rewrite_summary["error"] = f"{type(exc).__name__}: {exc}"
                rewrite_totals["requested"] += int(rewrite_summary.get("requested") or 0)
                rewrite_totals["rewritten"] += int(rewrite_summary.get("rewritten") or 0)
                rewrite_totals["reapproved"] += int(rewrite_summary.get("reapproved") or 0)
                self._emit(run_id, "governance_progress", "候选替换审核完成", {
                    "stage": "review", "round": round_number,
                    "approved": len(approved), **rewrite_summary,
                })
                return accepted_samples

            def evaluate(samples: List[Dict[str, Any]], round_number: int) -> dict[str, Any]:
                evaluated_ids.update(str(item.get("sample_id") or "") for item in samples)
                self._set_status(run_id, "running", current_stage="evaluation", round=round_number, question_count=len(samples))
                round_dir = run_dir / f"round_{round_number:03d}"
                dataset_path = round_dir / "dataset_snapshot.json"
                payload = dict(source_dataset)
                payload["dataset_revision"] = f"{run_id}_round_{round_number:03d}"
                payload["source_snapshot"] = {**dict(source_dataset.get("source_snapshot") or {}), "index_version": index_version, "tuning_run_id": run_id}
                payload["samples"] = [dict(item) for item in samples]
                _write_json(dataset_path, payload)
                execute_isolated_evaluation(
                    run_id=f"{run_id}_r{round_number}", ingestion_run_id=ingestion_run_id, index_version=index_version,
                    dataset_path=dataset_path, output_dir=round_dir, service=self._create_isolated_rag_service(ingestion_run_id, index_version),
                    retrieval_options={}, ragas_options={"profile": "reviewed_all_core_metrics", "run": True},
                    steps=["validate_datasets", "retrieval_eval", "ragas_eval"],
                    event_callback=lambda event_type, message, data: self._emit(run_id, event_type, message, {**data, "round": round_number}),
                    cancel_checker=lambda: bool(self._load(run_id).get("cancel_requested")),
                )
                retrieval = _read_json(round_dir / "machine" / "rag_eval_result.json")
                ragas = _read_json(round_dir / "machine" / "ragas_eval_result.json")
                metadata = ragas.get("metadata") if isinstance(ragas.get("metadata"), list) else []
                score_records: list[dict[str, Any]] = []
                for position, raw_record in enumerate(ragas.get("score_records") or []):
                    if not isinstance(raw_record, dict):
                        continue
                    meta = metadata[position] if position < len(metadata) and isinstance(metadata[position], dict) else {}
                    sample_id = str(meta.get("sample_id") or "").strip()
                    if not sample_id:
                        continue
                    record = dict(raw_record)
                    record["sample_id"] = sample_id
                    score_records.append(record)
                retrieval_records: list[dict[str, Any]] = []
                for detail in retrieval.get("details") or []:
                    if not isinstance(detail, dict):
                        continue
                    sample_id = str(detail.get("sample_id") or "").strip()
                    if not sample_id:
                        continue
                    retrieval_records.append({
                        "sample_id": sample_id,
                        "recall": detail.get("recall"),
                        "reciprocal_rank": detail.get("reciprocal_rank"),
                    })
                report = {
                    "round": round_number,
                    "evaluated_count": len(score_records),
                    "score_records": score_records,
                    "retrieval_records": retrieval_records,
                    "status": _read_json(round_dir / "summary.json").get("status", "unknown"),
                }
                round_reports.append(report)
                _write_json(round_dir / "tuning_machine_result.json", report)
                return report

            loop_result = run_tuning_loop(
                initial_samples,
                policy=policy,
                generate=generate,
                review=review,
                evaluate=evaluate,
                precomputed_evidence={**passed_cached, **rescued_passed},
            )
            if loop_result.get("status") != "passed":
                error_code = str(loop_result.get("error_code") or "").strip()
                detail_text = str(loop_result.get("error") or "tuning dataset did not pass")
                raise RuntimeError(f"{error_code}: {detail_text}" if error_code else detail_text)
            evidence_map: Dict[str, Dict[str, Any]] = dict(loop_result.get("evidence") or {})
            final_payload = dict(source_dataset)
            final_payload.update({
                "dataset_id": f"tuning_{index_version}", "dataset_kind": "generated_candidate",
                "dataset_revision": f"{run_id}_published",
                "source_snapshot": {**dict(source_dataset.get("source_snapshot") or {}), "index_version": index_version, "tuning_run_id": run_id},
                "samples": protected_samples + [dict(item) for item in loop_result["samples"]],
            })
            config_hashes: set[str] = set()
            evidence_sources: set[str] = set()
            for sample in final_payload.get("samples") or []:
                record = evidence_map.get(str(sample.get("sample_id") or ""))
                if not isinstance(record, dict):
                    continue
                sample["tuning_evidence"] = {
                    "source_run_id": record.get("source_run_id"),
                    "source_kind": record.get("source_kind"),
                    "source_round": record.get("source_round"),
                    "recorded_at": record.get("recorded_at"),
                    "metrics": {metric: record.get(metric) for metric in TUNING_METRICS},
                    "retrieval": {"recall": record.get("recall"), "reciprocal_rank": record.get("reciprocal_rank")},
                    "retrieval_config_sha256": record.get("retrieval_config_sha256"),
                    "ragas_profile": record.get("ragas_profile"),
                    "judge_profile": record.get("judge_profile"),
                }
                if record.get("retrieval_config_sha256"):
                    config_hashes.add(str(record["retrieval_config_sha256"]))
                if record.get("source_run_id"):
                    evidence_sources.add(str(record["source_run_id"]))
            registered_path = run_dir / "registered_dataset.json"
            _write_json(registered_path, final_payload)
            # 复制到按索引版本分区的登记目录，供后续同索引任务发现；不覆盖历史 revision。
            catalog_path = TUNING_DATASET_ROOT / index_version / f"{run_id}.json"
            _write_json(catalog_path, final_payload)
            result = {
                "schema_version": "rag_index_bound_tuning_result_v1", "run_id": run_id, "index_version": index_version,
                "status": "passed", "target_count": policy.target_count, "minimum_metric": policy.minimum_metric,
                "rounds": loop_result.get("rounds", []), "round_reports": round_reports,
                "dataset_artifact_name": registered_path.name, "registered_dataset_id": f"{index_version}/{run_id}.json",
                "question_count": len(final_payload.get("samples") or []),
                "protected_count": len(protected_samples),
                "baseline_source": baseline_source,
                "carried_evidence_count": len(passed_cached),
                "rescued_evidence_count": len(rescued_passed),
                "dropped_fail_count": len(dropped_cached),
                "fresh_evaluated_count": len(evaluated_ids - set(passed_cached) - set(rescued_passed)),
                "reused_across_configs": len(config_hashes) > 1,
                "evidence_source_runs": sorted(evidence_sources),
                "ledger_source_runs": ledger_sources,
                "rewrite_totals": dict(rewrite_totals),
            }
            _write_json(run_dir / "result.json", result)
            self._set_status(
                run_id, "succeeded", finished_at=_timestamp(), current_stage="done",
                result_path=str(run_dir / "result.json"), question_count=result["question_count"],
                dataset_artifact_name=registered_path.name,
                baseline_source=baseline_source,
                carried_evidence_count=result["carried_evidence_count"] + result["rescued_evidence_count"],
                fresh_evaluated_count=result["fresh_evaluated_count"],
                reused_across_configs=result["reused_across_configs"],
                rewrite_reapproved_total=rewrite_totals.get("reapproved", 0),
            )
            self._emit(run_id, "run_done", "索引绑定调参集治理完成，已登记新题集", {"status": "passed", "question_count": result["question_count"]})
        except InterruptedError as exc:
            self._set_status(run_id, "cancelled", finished_at=_timestamp(), current_stage="cancelled", error=str(exc))
            self._emit(run_id, "run_cancelled", "索引绑定调参集治理已取消", {"error": str(exc)})
        except Exception as exc:
            self._set_status(run_id, "failed", finished_at=_timestamp(), current_stage="failed", error=str(exc))
            error_text = str(exc).strip() or type(exc).__name__
            display = error_text if len(error_text) <= 240 else f"{error_text[:237]}..."
            self._emit(
                run_id,
                "run_error",
                f"索引绑定调参集治理失败: {type(exc).__name__}: {display}",
                {"error": str(exc)},
            )

    def _run_ingestion(
        self,
        run_id: str,
        sources: List[str],
        max_pages: int | None = None,
        page_ranges: Dict[str, tuple[int, int]] | None = None,
        allow_remote_data: bool = False,
        authorized_source_ids: List[str] | None = None,
    ) -> None:
        """执行隔离摄取；旧 index_root、asset_root 和 active pointer 不参与。"""
        run_dir = _run_dir(run_id)
        state = self._load(run_id)
        maintenance = MultimodalKnowledgeBaseMaintenance(
            asset_root=run_dir / "assets",
            index_root=run_dir / "indexes",
            active_config=run_dir / "active_index.json",
            embedding_scope="evaluation",
        )
        remote_enabled = bool(allow_remote_data and authorized_source_ids)
        self._emit(
            run_id,
            "stage_start",
            "开始解析并构建 staged Chroma",
            {"stage": "ingest"},
        )
        self._set_status(run_id, "running", started_at=_timestamp(), current_stage="ingest")
        try:
            result = maintenance.ingest(
                sources,
                allow_remote_data=remote_enabled,
                embedding_config=state.get("embedding_config"),
                auto_outbound_manifest=remote_enabled,
                authorized_source_ids=list(authorized_source_ids or []),
                progress_callback=lambda event: self._emit(
                    run_id,
                    "ingestion_progress",
                    event.get("message", "摄取处理中"),
                    event,
                ),
                cancel_check=lambda: bool(self._load(run_id).get("cancel_requested")),
                max_pages=max_pages,
                page_ranges=page_ranges or None,
            )
            index_version = str(result["index_version"])
            collection_name = f"{maintenance.collection_prefix}_{index_version}"
            manifest_path = run_dir / "indexes" / index_version / "manifest.json"
            self._set_status(
                run_id,
                "staged",
                finished_at=_timestamp(),
                current_stage="staged",
                index_version=index_version,
                collection_name=collection_name,
                manifest_sha256=compute_manifest_sha256(manifest_path),
                unit_count=int(result.get("unit_count", 0)),
                vector_count=int(result.get("vector_count", 0)),
            )
            self._emit(run_id, "run_done", "知识源已完成解析、嵌入和 staged Chroma 写入", {
                "status": "staged",
                "index_version": index_version,
                "unit_count": result.get("unit_count", 0),
                "vector_count": result.get("vector_count", 0),
            })
        except InterruptedError as exc:
            self._set_status(run_id, "cancelled", finished_at=_timestamp(), current_stage="cancelled", error=str(exc))
            self._emit(run_id, "run_cancelled", "知识源摄取已取消", {"error": str(exc)})
        except Exception as exc:
            self._set_status(run_id, "failed", finished_at=_timestamp(), current_stage="failed", error=str(exc))
            self._emit(run_id, "run_error", f"知识源摄取失败: {type(exc).__name__}", {"error": str(exc)})

    def _generate_governance_candidates(
        self,
        state: Dict[str, Any],
        required: int,
        *,
        round_number: int | None = None,
        exclude_keys: set | None = None,
        priority_keys: set | None = None,
    ) -> List[Dict[str, Any]]:
        """分批复用候选生成 hard screen，保留成功批次并返回替换候选。

        exclude_keys 命中的证据单元不参与取样（已被现有题目覆盖）；
        priority_keys 命中的薄弱单元在各模态组内稳定前移，定向补缺口。
        可重试的连接类失败按 15s/60s 退避重试；凑不齐 required 时不再整体
        失败，而是记录缺口审计后返回已收集部分，由闭环的零采纳快速失败
        与 max_rounds 兜底。
        """
        if callable(GOVERNANCE_CANDIDATE_PROVIDER):
            return [dict(item) for item in GOVERNANCE_CANDIDATE_PROVIDER(dict(state), required)]
        from Agent.knowledge_base.rag.operation_datasets.candidate_generation import (
            _select_unit_records,
            expand_candidate_dataset,
            load_staged_unit_records,
        )

        index_dir = _run_dir(str(state["ingestion_run_id"])) / "indexes" / str(state["index_version"])
        run_id = str(state["run_id"])
        self._set_status(run_id, "running", current_stage="generation")
        selected_limit = max(16, min(128, required * 3))
        records, source_snapshot = load_staged_unit_records(index_dir)
        if exclude_keys:
            filtered_records = [record for record in records if _unit_record_keys(record) & exclude_keys]
            if not filtered_records:
                raise RuntimeError("existing questions already cover every staged unit; no replacement unit is available")
            records = filtered_records
        if priority_keys:
            records = _prioritize_unit_records(records, priority_keys)
        selected_records = _select_unit_records(records, selected_limit)
        batch_root = _run_dir(run_id) / "replacement_candidate_batches"
        if round_number is not None:
            round_value = int(round_number)
            if round_value < 1:
                raise ValueError("round_number must be positive")
            batch_root = batch_root / f"round_{round_value:03d}"
        collected: list[dict[str, Any]] = []
        seen_questions: set[str] = set()
        batch_audit: list[dict[str, Any]] = []
        stopped_early = False

        for offset in range(0, len(selected_records), _GOVERNANCE_CANDIDATE_BATCH_SIZE):
            if len(collected) >= required:
                stopped_early = True
                break
            if bool(self._load(run_id).get("cancel_requested")):
                raise InterruptedError("candidate generation was cancelled")

            batch_number = offset // _GOVERNANCE_CANDIDATE_BATCH_SIZE + 1
            batch_records = selected_records[offset:offset + _GOVERNANCE_CANDIDATE_BATCH_SIZE]
            audit_entry: dict[str, Any] = {
                "batch": batch_number,
                "unit_ids": [str(record.get("unit_id", "")) for record in batch_records],
                "attempts": [],
                "accepted_count": 0,
            }
            batch_audit.append(audit_entry)
            batch_succeeded = False

            for attempt in range(1, _GOVERNANCE_CANDIDATE_MAX_ATTEMPTS + 1):
                candidate_path = batch_root / f"batch_{batch_number:03d}_attempt_{attempt}.json"
                try:
                    expand_candidate_dataset(
                        index_dir,
                        output_path=candidate_path,
                        selected_records=batch_records,
                        questions_per_unit=1,
                        max_workers=1,
                        progress_callback=lambda data, batch=batch_number, current_attempt=attempt: self._emit(
                            run_id,
                            "governance_progress",
                            f"候选替换生成：{data.get('stage', 'running')}",
                            {**data, "batch": batch, "attempt": current_attempt},
                        ),
                        cancel_checker=lambda: bool(self._load(run_id).get("cancel_requested")),
                    )
                    payload = _read_json(candidate_path)
                    samples = payload.get("samples")
                    if not isinstance(samples, list):
                        raise ValueError("replacement candidate dataset samples are invalid")
                    accepted_count = 0
                    for item in samples:
                        if not isinstance(item, dict):
                            continue
                        candidate = dict(item)
                        question_key = str(candidate.get("question", "")).strip().casefold()
                        if not question_key or question_key in seen_questions:
                            continue
                        seen_questions.add(question_key)
                        source = dict(candidate.get("source") or {})
                        source["index_binding"] = {"index_version": str(state["index_version"])}
                        candidate["source"] = source
                        collected.append(candidate)
                        accepted_count += 1
                    audit_entry["attempts"].append({
                        "attempt": attempt,
                        "status": "succeeded",
                        "candidate_path": str(candidate_path),
                        "accepted_count": accepted_count,
                    })
                    audit_entry["accepted_count"] = accepted_count
                    batch_succeeded = True
                    break
                except Exception as exc:
                    retryable = _is_retryable_governance_generation_error(exc)
                    audit_entry["attempts"].append({
                        "attempt": attempt,
                        "status": "failed",
                        "candidate_path": str(candidate_path),
                        "retryable_connection_failure": retryable,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    if not retryable or attempt == _GOVERNANCE_CANDIDATE_MAX_ATTEMPTS:
                        break
                    delay_seconds = 15 * (4 ** (attempt - 1))
                    self._emit(run_id, "governance_progress", "候选生成连接失败，保留已有批次后自动重试", {
                        "stage": "generation_retry",
                        "batch": batch_number,
                        "attempt": attempt,
                        "retry_in_seconds": delay_seconds,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    time.sleep(delay_seconds)

        aggregate_path = batch_root / "aggregate.audit.json" if round_number is not None else _run_dir(run_id) / "replacement_candidates.aggregate.audit.json"
        shortfall = max(0, required - len(collected))
        aggregate_payload: dict[str, Any] = {
            "schema_version": "rag_governance_candidate_batch_audit_v1",
            "source_snapshot": source_snapshot,
            "required_count": required,
            "selected_unit_count": len(selected_records),
            "batch_size": _GOVERNANCE_CANDIDATE_BATCH_SIZE,
            "max_attempts_per_batch": _GOVERNANCE_CANDIDATE_MAX_ATTEMPTS,
            "accepted_count": len(collected),
            "shortfall": shortfall,
            "stopped_early": stopped_early,
            "batches": batch_audit,
        }
        if round_number is not None:
            aggregate_payload["round"] = int(round_number)
        _write_json(aggregate_path, aggregate_payload)
        # 缺口尽力而为：把已接受的候选交给 review/改写管线；整轮零采纳仍由
        # loop 的 replacement_generation_exhausted 快速失败兜底。
        if shortfall:
            self._emit(run_id, "governance_progress", f"候选生成存在缺口：需要 {required} 道，实际筛得 {len(collected)} 道，缺口 {shortfall} 道已记入审计", {
                "stage": "generation_shortfall",
                "required_count": required,
                "accepted_count": len(collected),
                "shortfall": shortfall,
                "audit_path": str(aggregate_path.name),
            })
        return collected

    @staticmethod
    def _persist_governed_gold(
        dataset: Dict[str, Any],
        old_path: Path,
        *,
        expected_sha256: str,
    ) -> str:
        """在同一跨进程锁内复核、归档并原子写入，拒绝 stale run 覆盖新 Gold。"""
        lock_path = old_path.with_suffix(old_path.suffix + ".governance.lock")
        with _path_file_lock(lock_path):
            current_hash = hashlib.sha256(old_path.read_bytes()).hexdigest()
            if current_hash != expected_sha256:
                raise ValueError("current Gold changed while governance was running; stale run refused")
            history_dir = old_path.parent / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            archive_path = history_dir / (
                f"{old_path.stem}_{datetime.now().strftime('%Y%m%dT%H%M%S%fZ')}_{current_hash[:12]}{old_path.suffix}"
            )
            shutil.copy2(old_path, archive_path)
            _write_json(old_path, dataset)
            return str(archive_path.resolve())

    def run_dataset_governance_sync(self, run_id: str) -> None:
        """执行一次 Gold 健康治理；所有门禁失败都不触碰正式 Gold。"""
        state = self._load(run_id)
        self._set_status(run_id, "running", started_at=_timestamp(), current_stage="preflight")
        try:
            from Agent.knowledge_base.rag.operation_datasets.benchmark_v2 import (
                DEFAULT_GOLD_V2_OUTPUT,
                validate_frozen_gold_binding,
            )
            from Agent.knowledge_base.rag.operation_datasets.candidate_quality_audit import audit_candidate_quality
            from Agent.knowledge_base.rag.operation_datasets.dataset_governance import govern_dataset
            from Agent.knowledge_base.rag.rag_eval.contracts import evaluation_identity, load_eval_dataset_bundle

            evaluation_run_id = str(state.get("evaluation_run_id") or "")
            evaluation_state = self._load(evaluation_run_id)
            if evaluation_state.get("kind") != "evaluation" or evaluation_state.get("status") != "succeeded":
                raise ValueError("source evaluation run is not completed successfully")
            if not DEFAULT_GOLD_V2_OUTPUT.is_file():
                raise ValueError("pearl_gold_v2 is unavailable")
            current_identity = evaluation_identity(DEFAULT_GOLD_V2_OUTPUT)
            source_identity = evaluation_state.get("input_identity") or {}
            evaluation_dir = _run_dir(evaluation_run_id)
            current_dataset = _read_json(DEFAULT_GOLD_V2_OUTPUT)
            source_dataset = _read_json(evaluation_dir / "dataset_snapshot.json")
            source_content_sha256 = str(evaluation_state.get("input_content_sha256") or _dataset_content_sha256(source_dataset))
            if source_content_sha256 != _dataset_content_sha256(current_dataset):
                raise ValueError("source evaluation dataset content does not match the current Gold revision")

            index_dir = _run_dir(str(state.get("ingestion_run_id") or "")) / "indexes" / str(state.get("index_version") or "")
            validate_frozen_gold_binding(DEFAULT_GOLD_V2_OUTPUT, index_dir=index_dir)
            dataset = load_eval_dataset_bundle(DEFAULT_GOLD_V2_OUTPUT)
            self._emit(run_id, "stage_start", "开始读取评测坏例并执行题目结构诊断", {"stage": "diagnosis"})
            quality_report = audit_candidate_quality(DEFAULT_GOLD_V2_OUTPUT, evaluation_dir=evaluation_dir)
            _write_json(_run_dir(run_id) / "quality_audit.json", quality_report)
            intrinsic_count = sum(
                1 for item in quality_report.get("items", [])
                if any(not str(flag).startswith("ragas_") for flag in item.get("flags", []))
            )
            candidates = self._generate_governance_candidates(state, intrinsic_count) if intrinsic_count else []
            _write_json(_run_dir(run_id) / "replacement_candidates.json", {"samples": candidates})
            if self._load(run_id).get("cancel_requested"):
                raise InterruptedError("dataset governance was cancelled")

            self._set_status(run_id, "running", current_stage="review")
            reviewer = GOVERNANCE_REVIEWER or _default_governance_reviewer
            governed, report = govern_dataset(dataset, quality_report, candidates, reviewer)
            if len(governed.get("samples") or []) != len(dataset.get("samples") or []):
                raise ValueError("governance changed the question count")
            governed_candidate_path = _run_dir(run_id) / "governed_gold_candidate.json"
            if report["replaced_count"]:
                _write_json(governed_candidate_path, governed)
                validate_frozen_gold_binding(governed_candidate_path, index_dir=index_dir)
            report.update({
                "evaluation_run_id": evaluation_run_id,
                "old_revision": dataset.get("dataset_revision", ""),
                "source_index_version": state.get("index_version", ""),
                "candidate_pool_count": len(candidates),
                "governed_candidate_artifact_name": governed_candidate_path.name if report["replaced_count"] else "",
            })
            archive_path = ""
            if report["replaced_count"]:
                self._set_status(run_id, "running", current_stage="publish")
                archive_path = self._persist_governed_gold(
                    governed,
                    DEFAULT_GOLD_V2_OUTPUT,
                    expected_sha256=str(current_identity.get("dataset_sha256") or ""),
                )
                report["archived_dataset_path"] = archive_path
                report["publish_status"] = "published"
            else:
                report["archived_dataset_path"] = ""
                report["publish_status"] = "no_change"
            _write_json(_run_dir(run_id) / "governance_report.json", report)
            result = {"schema_version": "rag_dataset_governance_result_v1", **report}
            _write_json(_run_dir(run_id) / "result.json", result)
            self._set_status(
                run_id,
                "succeeded",
                finished_at=_timestamp(),
                current_stage="done",
                result_path=str(_run_dir(run_id) / "result.json"),
                old_revision=report["old_revision"],
                new_revision=report["new_revision"],
                archived_dataset_path=archive_path,
                protected_count=report["protected_count"],
                diagnosed_count=report["diagnosed_count"],
                replaced_count=report["replaced_count"],
                rejected_candidate_count=report["rejected_candidate_count"],
            )
            done_message = (
                "Gold 题目健康治理完成，旧 revision 已归档"
                if report["replaced_count"]
                else "Gold 题目健康治理完成，本次无需替换"
            )
            self._emit(run_id, "run_done", done_message, result)
        except InterruptedError as exc:
            self._set_status(run_id, "cancelled", finished_at=_timestamp(), current_stage="cancelled", error=str(exc))
            self._emit(run_id, "run_cancelled", "Gold 题目健康治理已取消", {"error": str(exc)})
        except Exception as exc:
            self._set_status(run_id, "failed", finished_at=_timestamp(), current_stage="failed", error=str(exc))
            self._emit(run_id, "run_error", f"Gold 题目健康治理失败: {type(exc).__name__}", {"error": str(exc)})

    def run_queued_sync(self, run_id: str) -> None:
        """由隔离评测常驻 worker 按已落盘的任务类型同步分发执行。"""
        state = self._load(run_id)
        kind = str(state.get("kind") or "")
        if kind == "ingestion":
            source_paths, source_ids, _ = _resolve_source_inputs(
                state.get("source_ids"),
                state.get("sources"),
            )
            normalized_ranges, page_range_map = _normalize_page_ranges(
                state.get("page_ranges"), source_paths, source_ids
            )
            if normalized_ranges != state.get("page_ranges", []):
                raise ValueError("ingestion page range snapshot is invalid")
            self._run_ingestion(
                run_id,
                [str(path) for path in source_paths],
                state.get("max_pages"),
                page_range_map,
                bool(state.get("remote_enabled")),
                list(state.get("authorized_source_ids") or []),
            )
            return
        if kind == "candidate_generation":
            self._run_candidate_generation(
                run_id,
                str(state.get("ingestion_run_id") or ""),
                str(state.get("index_version") or ""),
            )
            return
        if kind == "rag_query":
            self._run_rag_query(
                run_id,
                str(state.get("ingestion_run_id") or ""),
                str(state.get("index_version") or ""),
                list(state.get("questions") or []),
            )
            return
        if kind == "evaluation":
            self.run_evaluation_sync(run_id)
            return
        if kind == "dataset_governance":
            self.run_dataset_governance_sync(run_id)
            return
        if kind == "tuning_dataset_governance":
            self._run_tuning_dataset_governance(run_id)
            return
        raise ValueError(f"不支持的隔离评测队列任务类型: {kind}")

    def _run_rag_query(
        self,
        run_id: str,
        ingestion_run_id: str,
        index_version: str,
        questions: List[Dict[str, Any]],
    ) -> None:
        """在隔离 staged index 上执行检索、回答和结果落盘。"""
        self._set_status(run_id, "running", started_at=_timestamp(), current_stage="runtime")
        try:
            service = self._create_isolated_rag_service(ingestion_run_id, index_version)
            retrieval_config = service._load_retrieval_config()
            results: List[Dict[str, Any]] = []
            for index, raw_question in enumerate(questions, start=1):
                if self._load(run_id).get("cancel_requested"):
                    raise InterruptedError("isolated RAG query was cancelled")
                question_payload = _normalize_question_payload(raw_question)
                question = question_payload["question"].strip()
                self._set_status(run_id, "running", current_stage="retrieval", question_index=index)
                self._emit(run_id, "question_start", f"检索第 {index}/{len(questions)} 个问题", {"question_index": index, "question": question})
                trace = service.build_retrieval_trace(question, config=retrieval_config)
                evidence = trace["evidence_payload"]
                self._set_status(run_id, "running", current_stage="answer", question_index=index)
                answer = service.answer_question(question_payload, evidence)
                results.append({
                    "sample_id": raw_question.get("sample_id"),
                    "question": question,
                    "trace": trace,
                    "answer": answer,
                })
                self._emit(run_id, "question_done", f"完成第 {index}/{len(questions)} 个问题", {"question_index": index, "evidence_count": len(evidence)})

            result = {
                "schema_version": "isolated_rag_query_v1",
                "run_id": run_id,
                "ingestion_run_id": ingestion_run_id,
                "index_version": index_version,
                "input_identity": self._load(run_id).get("input_identity", {}),
                "question_count": len(results),
                "results": results,
            }
            result_path = Path(self._load(run_id)["result_path"])
            _write_json(result_path, result)
            self._set_status(run_id, "succeeded", finished_at=_timestamp(), current_stage="done", result_path=str(result_path))
            self._emit(run_id, "run_done", "staged index RAG 测试完成", {"status": "succeeded", "result_available": True, "question_count": len(results)})
        except InterruptedError as exc:
            self._set_status(run_id, "cancelled", finished_at=_timestamp(), current_stage="cancelled", error=str(exc))
            self._emit(run_id, "run_cancelled", "staged index RAG 测试已取消", {"error": str(exc)})
        except Exception as exc:
            self._set_status(run_id, "failed", finished_at=_timestamp(), current_stage="failed", error=str(exc))
            self._emit(run_id, "run_error", f"staged index RAG 测试失败: {type(exc).__name__}", {"error": str(exc)})

    def run_evaluation_sync(self, run_id: str) -> None:
        """由持久 worker 同步执行一个已入队的隔离评测。"""
        state = self._load(run_id)
        ingestion_run_id = str(state.get("ingestion_run_id") or "")
        index_version = str(state.get("index_version") or "")
        self._set_status(
            run_id,
            "running",
            started_at=state.get("started_at") or _timestamp(),
            current_stage="evaluation",
        )
        try:
            from app.rag_eval.isolated_evaluation import execute_isolated_evaluation

            state = self._load(run_id)
            result = execute_isolated_evaluation(
                run_id=run_id,
                ingestion_run_id=ingestion_run_id,
                index_version=index_version,
                dataset_path=Path(state["dataset_path"]),
                output_dir=_run_dir(run_id),
                service=self._create_isolated_rag_service(ingestion_run_id, index_version),
                retrieval_options=state.get("retrieval_options"),
                ragas_options=state.get("ragas_options"),
                strategy_profile=state.get("strategy_profile"),
                steps=state.get("steps"),
                event_callback=lambda event_type, message, data: self._emit(run_id, event_type, message, data),
                cancel_checker=lambda: bool(self._load(run_id).get("cancel_requested")),
            )
            summary = result.get("summary", {})
            if summary.get("status") == "failed":
                error = str(summary.get("error") or summary.get("status_reason") or "隔离评测失败")
                self._set_status(
                    run_id,
                    "failed",
                    finished_at=_timestamp(),
                    current_stage="failed",
                    error=error,
                    status_reason=str(summary.get("status_reason") or "step_failed"),
                    result_path=str(_run_dir(run_id) / "result.json"),
                    summary=summary,
                )
                self._emit(run_id, "run_error", f"隔离评测失败: {error}", {
                    "status": "failed",
                    "status_reason": summary.get("status_reason", ""),
                    "error": error,
                    "result_available": True,
                })
                return
            self._set_status(
                run_id,
                "succeeded",
                finished_at=_timestamp(),
                current_stage="done",
                result_path=str(_run_dir(run_id) / "result.json"),
                summary=summary,
            )
            self._emit(run_id, "run_done", "隔离评测流程完成", {
                "status": summary.get("status", "unknown"),
                "result_available": True,
            })
        except InterruptedError as exc:
            self._set_status(run_id, "cancelled", finished_at=_timestamp(), current_stage="cancelled", error=str(exc))
            self._emit(run_id, "run_cancelled", "隔离评测已取消", {"error": str(exc)})
        except Exception as exc:
            self._set_status(run_id, "failed", finished_at=_timestamp(), current_stage="failed", error=str(exc))
            self._emit(run_id, "run_error", f"隔离评测失败: {type(exc).__name__}", {"error": str(exc)})

    def _run_evaluation(self, run_id: str, ingestion_run_id: str, index_version: str) -> None:
        """兼容旧调用点；新的评测入口由 persistent worker 调用同步执行。"""
        del ingestion_run_id, index_version
        self.run_evaluation_sync(run_id)

    def _create_isolated_rag_service(self, ingestion_run_id: str, index_version: str) -> RagService:
        """以显式 staged index identity 创建 RAG Service，不读取 active pointer。"""
        ingestion = self._load(ingestion_run_id)
        index_dir = _run_dir(ingestion_run_id) / "indexes" / index_version
        self._validate_staged_index(index_dir, ingestion)
        manifest = _read_json(index_dir / "manifest.json")
        embedding_config = embedding_config_from_manifest(manifest)
        frozen_state_embedding = ingestion.get("embedding_config")
        if isinstance(frozen_state_embedding, dict):
            state_config = EmbeddingConfiguration.from_mapping(frozen_state_embedding)
            if state_config.to_manifest() != EmbeddingConfiguration.from_mapping(embedding_config).to_manifest():
                raise ValueError("isolated embedding configuration does not match staged manifest")
            embedding_config = state_config.to_runtime_dict()
        runtime_config = RagRuntimeConfig(
            vector_db_dir=str(index_dir / "chroma"),
            collection_name=str(ingestion["collection_name"]),
            production_config_path=PRODUCTION_RAG_CONFIG_PATH,
            embedding_config=embedding_config,
            release_id=str(manifest.get("release_id") or index_version),
            embedding_scope="evaluation",
        )
        from langchain_openai import ChatOpenAI

        answer_llm = ChatOpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL, model=settings.MODEL)
        return RagService(create_rag_runtime(runtime_config, answer_llm))

    def resolve_staged_index(self, ingestion_run_id: str, index_version: str) -> IndexIdentity:
        """返回可供只读调用方复用的已验证 staged index identity。"""
        index_dir = _run_dir(ingestion_run_id) / "indexes" / index_version
        return IndexBindingGate(
            self._load,
            _run_dir,
            lambda: self._load(ingestion_run_id).get("embedding_config") or _staged_embedding_config(index_dir),
        ).resolve_staged_index(
            ingestion_run_id, index_version
        )

    @staticmethod
    def _validate_staged_index(index_dir: Path, ingestion: Dict[str, Any]) -> IndexIdentity:
        """兼容旧调用点；全部 staged index 校验委托统一绑定门禁。"""
        run_id = str(ingestion.get("run_id") or "")
        run_dir = Path(index_dir).resolve().parents[1]
        return IndexBindingGate(
            lambda _: ingestion,
            lambda _: run_dir,
            lambda: ingestion.get("embedding_config") or _staged_embedding_config(index_dir),
        ).resolve_staged_index(
            run_id, str(Path(index_dir).name)
        )

    def _load(self, run_id: str) -> Dict[str, Any]:
        """读取内存状态或隔离目录中的持久化运行状态。"""
        safe_id = _safe_run_id(run_id)
        path = _run_dir(safe_id) / "run.json"
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
        else:
            with self._lock:
                state = self._runs.get(safe_id)
            if state is None:
                raise KeyError(f"isolated run not found: {safe_id}")
        with self._lock:
            self._runs[safe_id] = state
        return state

    def _set_status(self, run_id: str, status: str, **fields: Any) -> None:
        """更新运行状态并同步到 run.json；跨进程锁串行化读-改-写。"""
        with self._lock, _run_file_lock(run_id):
            state = self._load(run_id)
            state.update({"status": status, "last_activity_at": _timestamp(), **fields})
            _write_json(_run_dir(run_id) / "run.json", state)

    def _emit(self, run_id: str, event_type: str, message: str, data: Dict[str, Any]) -> None:
        """记录事件并推送到 SSE；事件推送失败不能改变任务结果。"""
        with self._lock, _run_file_lock(run_id):
            state = self._load(run_id)
            event_seq = int(state.get("event_seq") or 0) + 1
            event = {
                "event_id": event_seq,
                "type": event_type,
                "message": message,
                "timestamp": _timestamp(),
                "data": data,
            }
            state.setdefault("events", []).append(event)
            state["events"] = state["events"][-_MAX_EVENTS:]
            state["event_seq"] = event_seq
            state["last_activity_at"] = event["timestamp"]
            _write_json(_run_dir(run_id) / "run.json", state)
            event_queue = self._queues.get(run_id)
        if event_queue is not None:
            try:
                event_queue.put(event, timeout=1)
            except queue.Full:
                pass

    @staticmethod
    def _public_state(state: Dict[str, Any]) -> Dict[str, Any]:
        """复制可返回前端的状态，隐藏内部路径与运行控制对象。"""
        public = dict(state)
        public.pop("root", None)
        public.pop("result_path", None)
        public.pop("dataset_path", None)
        public.pop("questions", None)
        public["events"] = list(state.get("events", []))
        result_path = Path(str(state.get("result_path") or ""))
        public["result_available"] = bool(
            state.get("kind") in {"rag_query", "evaluation", "candidate_generation", "dataset_governance", "tuning_dataset_governance"}
            and (
                state.get("status") == "succeeded"
                or (
                    state.get("kind") == "evaluation"
                    and state.get("status") == "failed"
                    and result_path.is_file()
                )
            )
        )
        if state.get("kind") == "evaluation":
            public["stale"] = _is_stale_evaluation(state)
        return public


isolated_run_manager = IsolatedRunManager()
