"""解析模型输出的 0/1 邻接矩阵。"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def parse_adjacency(path: Path, dim: int) -> tuple[np.ndarray | None, str | None]:
    path = Path(path)
    if not path.exists():
        return None, "missing"

    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = [cell for cell in re.split(r"[,\s;]+", line.strip()) if cell]
        if not cells:
            continue
        if re.fullmatch(r"[Xx]\d+", cells[0]):
            cells = cells[1:]
        values = []
        for cell in cells:
            lower = cell.lower()
            if lower in ("true", "false"):
                values.append(1.0 if lower == "true" else 0.0)
                continue
            try:
                values.append(float(cell))
            except ValueError:
                values = None
                break
        if values is not None and len(values) >= dim:
            rows.append(values[:dim])

    if len(rows) != dim:
        return None, "shape"
    matrix = np.asarray(rows, dtype=float)
    if not np.isin(matrix, (0.0, 1.0)).all():
        return None, "values"
    if matrix.diagonal().any():
        return None, "diagonal"
    return matrix.astype(int), None
