"""RAG 评测运行产物的统一路径边界。"""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _env_path(name: str, default: str | Path) -> Path:
    value = os.getenv(name, "").strip()
    return _resolve_path(value) if value else _resolve_path(default)


RAG_EVAL_ROOT = _env_path("RAG_EVAL_ROOT", PROJECT_ROOT / "tmp" / "rag_eval")

# 旧的细分环境变量继续覆盖统一默认值，便于已有部署逐步迁移。
RAG_EVAL_SOURCE_ROOT = _env_path("RAG_EVAL_SOURCE_ROOT", RAG_EVAL_ROOT / "sources")
RAG_EVAL_ISOLATED_RUN_ROOT = _env_path("RAG_EVAL_ISOLATED_RUN_ROOT", RAG_EVAL_ROOT / "runs")
RAG_EVAL_DATASET_ROOT = _env_path(
    "RAG_EVAL_DATASET_ROOT",
    RAG_EVAL_ROOT / "datasets" / "registered",
)
RAG_EVAL_TUNING_DATASET_ROOT = _env_path(
    "RAG_EVAL_TUNING_DATASET_ROOT",
    RAG_EVAL_ROOT / "datasets" / "tuning",
)
RAG_EVAL_CANDIDATE_DATASET_ROOT = _env_path(
    "RAG_EVAL_CANDIDATE_DATASET_ROOT",
    RAG_EVAL_ROOT / "datasets" / "candidates",
)
RAG_EVAL_BASELINE_ROOT = _env_path(
    "RAG_EVAL_BASELINE_ROOT",
    RAG_EVAL_ROOT / "datasets" / "baselines",
)
RAG_EVAL_ARTIFACT_ROOT = _env_path("RAG_EVAL_ARTIFACT_ROOT", RAG_EVAL_ROOT / "artifacts")
RAG_EVAL_OUTPUT_ROOT = _env_path("RAG_EVAL_OUTPUT_ROOT", RAG_EVAL_ROOT / "reports")
RAG_EVAL_MACHINE_OUTPUT_DIR = RAG_EVAL_OUTPUT_ROOT / "machine"
RAG_EVAL_REPORT_OUTPUT_DIR = RAG_EVAL_OUTPUT_ROOT / "human"
RAG_EVAL_RUN_OUTPUT_DIR = RAG_EVAL_OUTPUT_ROOT / "runs"
RAG_EVAL_LEGACY_ROOT = RAG_EVAL_ROOT / "legacy"
