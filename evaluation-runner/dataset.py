"""数据集扫描、路径解析与分层抽样。"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

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


class DatasetInvalid(ValueError):
    """npz 缺键或形状非法。"""


def load(spec: DatasetSpec) -> tuple[np.ndarray, np.ndarray]:
    with np.load(spec.path) as data:
        if "X" not in data or "adjacency" not in data:
            raise DatasetInvalid(f"{spec.dataset_id} 缺少 X 或 adjacency")
        X = np.asarray(data["X"], dtype=float)
        adjacency = np.asarray(data["adjacency"])
    if X.ndim != 2 or adjacency.ndim != 2:
        raise DatasetInvalid(f"{spec.dataset_id} 维度非法")
    if X.shape[1] == 0 or adjacency.shape[0] != adjacency.shape[1]:
        raise DatasetInvalid(f"{spec.dataset_id} 形状非法")
    if X.shape[1] != adjacency.shape[0]:
        raise DatasetInvalid(f"{spec.dataset_id} X 列数与 adjacency 不一致")
    return X, adjacency


def gold_edges(adjacency: np.ndarray) -> set[tuple[int, int]]:
    return {
        (row, col)
        for row in range(adjacency.shape[0])
        for col in range(adjacency.shape[1])
        if int(adjacency[row, col]) != 0
    }


def _format_cell(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.8g}"


def write_csv(X: np.ndarray, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([f"X{index}" for index in range(X.shape[1])])
        for row in X:
            writer.writerow([_format_cell(value) for value in row])


def profile(X: np.ndarray) -> dict:
    columns = X.shape[1]
    uniques = [np.unique(X[:, index]) for index in range(columns)]
    return {
        "n_rows": int(X.shape[0]),
        "n_cols": columns,
        "kinds": ["binary" if len(values) <= 2 else "numeric" for values in uniques],
        "missing": bool(np.isnan(X).any()),
        "ranges": [
            (float(np.nanmin(X[:, index])), float(np.nanmax(X[:, index])))
            for index in range(columns)
        ],
        "uniques": [int(len(values)) for values in uniques],
    }
