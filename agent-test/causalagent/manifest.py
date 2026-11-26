"""运行清单：记录实验身份并在续跑前校验。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import evaluate

MANIFEST_VERSION = 1
CLIENT_VERSION = 2


class ManifestError(RuntimeError):
    """运行清单缺失或身份不一致。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_snapshot(config) -> dict:
    return {
        "parallel": config.run.parallel,
        "timeout_minutes": config.run.timeout_minutes,
        "max_interactions": config.run.max_interactions,
        "interaction_seconds": config.run.interaction_seconds,
        "seed": config.run.seed,
        "data": {
            "dir": str(config.data.dir),
            "mechanisms": (
                list(config.data.mechanisms)
                if config.data.mechanisms is not None else None
            ),
            "exclude": list(config.data.exclude),
            "dims": list(config.data.dims),
            "ns": list(config.data.ns),
            "per_cell": config.data.per_cell,
        },
    }


def _datasets(specs) -> list[dict]:
    return [
        {"dataset": spec.dataset_id, "sha256": _sha256(spec.path)}
        for spec in specs
    ]


def build(config, specs, run_id: str, git_commit: str) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit,
        "base_url": config.run.base_url,
        "backend_version": config.run.backend_version,
        "client_version": CLIENT_VERSION,
        "metrics_version": evaluate.METRICS_VERSION,
        "config": config_snapshot(config),
        "datasets": _datasets(specs),
    }


def _append(problems: list[str], name: str, old, new) -> None:
    if old != new:
        problems.append(f"{name}: {old!r} -> {new!r}")


def verify(existing: dict, config, specs, git_commit: str) -> list[str]:
    problems: list[str] = []
    _append(problems, "manifest_version",
            existing.get("manifest_version"), MANIFEST_VERSION)
    _append(problems, "git_commit", existing.get("git_commit"), git_commit)
    _append(problems, "base_url", existing.get("base_url"), config.run.base_url)
    _append(problems, "backend_version",
            existing.get("backend_version"), config.run.backend_version)
    _append(problems, "client_version",
            existing.get("client_version"), CLIENT_VERSION)
    _append(problems, "metrics_version",
            existing.get("metrics_version"), evaluate.METRICS_VERSION)
    if existing.get("config") != config_snapshot(config):
        problems.append("config 快照不一致")

    old = existing.get("datasets") or []
    current = _datasets(specs)
    if len(old) != len(current):
        problems.append(f"datasets 数量: {len(old)} -> {len(current)}")
    else:
        for index, (left, right) in enumerate(zip(old, current)):
            if left.get("dataset") != right["dataset"]:
                problems.append(
                    f"datasets[{index}].dataset: "
                    f"{left.get('dataset')!r} -> {right['dataset']!r}"
                )
            elif left.get("sha256") != right["sha256"]:
                problems.append(
                    f"datasets[{index}].sha256 不一致: {right['dataset']}"
                )
    return problems


def check(existing, config, specs, git_commit: str,
          force: bool = False) -> list[str]:
    problems = verify(existing, config, specs, git_commit)
    if problems and not force:
        raise ManifestError("\n".join(problems))
    return problems


def write(path: Path, data: dict) -> None:
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
