"""数据集扫描、路径解析与分层抽样。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

CELL_PATTERN = re.compile(r"^D(\d+)_N(\d+)$")
REP_PATTERN = re.compile(r"^rep(\d+)$")


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    path: Path
    mechanism: str
    dim_nominal: int | None
    n_nominal: int | None
    rep: int | None


def artifact_name(dataset_id: str) -> str:
    return dataset_id.replace("/", "__")


def scan(root: Path) -> list[DatasetSpec]:
    specs: list[DatasetSpec] = []
    for path in sorted(Path(root).rglob("*.npz")):
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts
        if len(parts) < 3:
            continue
        cell = CELL_PATTERN.match(parts[-2])
        rep = REP_PATTERN.match(parts[-1])
        specs.append(DatasetSpec(
            dataset_id=relative.as_posix(),
            path=path,
            mechanism=parts[-3],
            dim_nominal=int(cell.group(1)) if cell else None,
            n_nominal=int(cell.group(2)) if cell else None,
            rep=int(rep.group(1)) if rep else None,
        ))
    return specs


def _rank(seed: int, spec: DatasetSpec) -> bytes:
    return hashlib.blake2b(f"{seed}|{spec.dataset_id}".encode(), digest_size=8).digest()


def select(
    specs: list[DatasetSpec],
    mechanisms: tuple[str, ...] | None,
    exclude: tuple[str, ...],
    dims: tuple[int, ...],
    ns: tuple[int, ...],
    per_cell: int,
    seed: int,
) -> list[DatasetSpec]:
    wanted = set(mechanisms) if mechanisms else None
    excluded = set(exclude)
    groups: dict[tuple[str, int | None, int | None], list[DatasetSpec]] = {}
    for spec in specs:
        if wanted is not None and spec.mechanism not in wanted:
            continue
        if spec.mechanism in excluded:
            continue
        if dims and spec.dim_nominal not in dims:
            continue
        if ns and spec.n_nominal not in ns:
            continue
        groups.setdefault((spec.mechanism, spec.dim_nominal, spec.n_nominal), []).append(spec)

    chosen: list[DatasetSpec] = []
    for key in sorted(groups, key=lambda item: (item[0], item[1] or -1, item[2] or -1)):
        ranked = sorted(groups[key], key=lambda spec: _rank(seed, spec))
        chosen.extend(ranked[:per_cell])
    return sorted(chosen, key=lambda spec: spec.dataset_id)
