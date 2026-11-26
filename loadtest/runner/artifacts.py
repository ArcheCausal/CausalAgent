"""把旧版平铺的 local/runs 与 local/evidence 产物按运行日期重新分组。

旧版把每次运行直接放在 local/runs/<run_id>/，报告直接放在 local/evidence/。新版
使用 local/runs/<date>/<run_id>/ 与 local/evidence/<date>/。本模块只移动文件，
默认只输出计划；需要显式确认后才执行。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from runner import config


def _run_day(run_dir: Path) -> str | None:
    """取一次运行所属的日期：优先 run_id 前缀，其次摘要开始时刻。"""
    day = config.date_from_run_id(run_dir.name)
    if day:
        return day
    summary = run_dir / "summary.json"
    if not summary.exists():
        return None
    try:
        payload = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    started = payload.get("started_at")
    return config.date_key(started) if isinstance(started, (int, float)) else None


def plan_migration() -> list[tuple[Path, Path]]:
    """列出需要移动的运行目录与目标位置；不修改任何文件。"""
    moves: list[tuple[Path, Path]] = []
    if not config.RUNS_DIR.is_dir():
        return moves
    for path in sorted(config.RUNS_DIR.iterdir()):
        if not path.is_dir() or path.name.isdigit():
            continue
        day = _run_day(path)
        if day is None:
            continue
        target = config.RUNS_DIR / day / path.name
        if target.exists():
            continue
        moves.append((path, target))
    return moves


def apply_migration(moves: list[tuple[Path, Path]]) -> list[str]:
    """执行移动，返回逐条结果；目标已存在时跳过，避免覆盖。"""
    results: list[str] = []
    for source, target in moves:
        if target.exists():
            results.append(f"跳过（目标已存在）：{source.name} -> {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        results.append(f"已移动：{source.name} -> {target}")
    return results
