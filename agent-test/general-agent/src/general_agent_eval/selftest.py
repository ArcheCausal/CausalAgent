"""离线自检：不联网、不调 Docker。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import metrics, parse
from .selector import Spec, select


def _check_parse() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ok.csv"
        path.write_text(
            ",X0,X1,X2\nX0,0,1,0\nX1,0,0,1\nX2,0,0,0\n", encoding="utf-8"
        )
        matrix, error = parse.parse_adjacency(path, 3)
        assert error is None, error
        assert matrix.tolist() == [[0, 1, 0], [0, 0, 1], [0, 0, 0]]

        bad = Path(tmp) / "bad.csv"
        bad.write_text("X0,0,0,0\nX1,0,0,0\n", encoding="utf-8")
        matrix, error = parse.parse_adjacency(bad, 3)
        assert matrix is None and error == "shape"

        diag = Path(tmp) / "diag.csv"
        diag.write_text("0,0,0\n0,1,0\n0,0,0\n", encoding="utf-8")
        matrix, error = parse.parse_adjacency(diag, 3)
        assert matrix is None and error == "diagonal"


def _check_metrics() -> None:
    gold = {(0, 1), (1, 2)}
    edges = [(0, 1, "directed"), (1, 2, "directed")]
    result = metrics.score(edges, gold)
    assert (result["precision"], result["recall"]) == (1.0, 1.0)
    assert metrics.shd(edges, gold) == 0

    partial = [(0, 1, "directed")]
    result = metrics.score(partial, gold)
    assert result["recall"] == 0.5
    assert result["skeleton_recall"] == 0.5
    assert metrics.shd(partial, gold) == 1


def _check_selection() -> None:
    specs = [
        Spec(f"M/D010_N500/rep00", Path("a.npz"), "M", 10, 500, 0),
        Spec(f"M/D010_N500/rep01", Path("b.npz"), "M", 10, 500, 1),
    ]
    assert len(select(specs, ["M"], [10], [500], 1, 1)) == 1
    try:
        select(specs, [], [10], [500], 1, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("空选择必须报错")


def main() -> int:
    _check_parse()
    _check_metrics()
    _check_selection()
    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
