"""扫描数据集目录并做确定性分层抽样。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

CELL = re.compile(r"^D(\d+)_N(\d+)$")
REP = re.compile(r"^rep(\d+)$")


@dataclass(frozen=True)
class Spec:
    dataset_id: str
    path: Path
    mechanism: str
    dim: int | None
    n: int | None
    rep: int | None


def scan(root: Path) -> list[Spec]:
    specs: list[Spec] = []
    for path in sorted(Path(root).rglob("*.npz")):
        rel = path.relative_to(root).with_suffix("")
        parts = rel.parts
        if len(parts) < 3:
            continue
        cell = CELL.match(parts[-2])
        rep = REP.match(parts[-1])
        specs.append(Spec(
            dataset_id=rel.as_posix(),
            path=path,
            mechanism=parts[-3],
            dim=int(cell.group(1)) if cell else None,
            n=int(cell.group(2)) if cell else None,
            rep=int(rep.group(1)) if rep else None,
        ))
    return specs


def available(specs: list[Spec]) -> dict:
    return {
        "mechanisms": sorted({s.mechanism for s in specs}),
        "dims": sorted({s.dim for s in specs if s.dim}),
        "ns": sorted({s.n for s in specs if s.n}),
    }


def _rank(seed: int, spec: Spec) -> bytes:
    return hashlib.blake2b(f"{seed}|{spec.dataset_id}".encode(), digest_size=8).digest()


def select(specs, mechanisms, dims, ns, per_cell, seed, exclude=()) -> list[Spec]:
    # 没有默认选择：缺任何一组都拒绝执行，避免"手滑跑了全量"
    if not mechanisms or not dims or not ns:
        raise ValueError("必须显式指定 mechanisms / dims / ns")
    wanted_m = {str(item) for item in mechanisms}
    wanted_d = {int(item) for item in dims}
    wanted_n = {int(item) for item in ns}
    excluded = {str(item) for item in exclude}

    groups: dict[tuple[str, int, int], list[Spec]] = {}
    for spec in specs:
        if spec.mechanism not in wanted_m or spec.mechanism in excluded:
            continue
        if spec.dim not in wanted_d or spec.n not in wanted_n:
            continue
        groups.setdefault((spec.mechanism, spec.dim, spec.n), []).append(spec)

    chosen: list[Spec] = []
    for key in sorted(groups):
        ranked = sorted(groups[key], key=lambda item: _rank(seed, item))
        chosen.extend(ranked[:per_cell])
    return sorted(chosen, key=lambda item: item.dataset_id)
