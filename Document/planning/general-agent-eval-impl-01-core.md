# 实现手册 1：配置、数据、解析、评分、审计

> 本阶段**不碰 Docker**。产出：一套离线可测的核心模块。
> 结束标准：`pytest` 全绿 + `general-agent-eval selftest` 返回 0。

---

## 1.1 这一阶段为什么先做

解析和评分错了，后面跑 210 集全是白跑。而且这两块可以完全离线验证：

- 解析：手工造几个畸形 CSV，看是否按预期报错；
- 评分：和金标准小图对拍，看 F1/SHD 是否符合手算；
- 选集：看是否拒绝空选择。

Docker 的报错噪音大，不适合同时调业务逻辑。

---

## 1.2 `src/general_agent_eval/config.py`

```python
"""配置加载：默认值 + config.yaml 深合并。"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "cache_dir": "~/.general-agent-eval/cache",
    "output_dir": "~/general-agent-eval-runs",
    "workspace_dir": "~/.general-agent-eval/work",
    "data": {
        "name": "cdfm_bench_rep00",
        "local_dir": "",
        "url": "",
        "sha256": "",
        "exclude": ["TimeLag"],
    },
    "selection": {
        "mechanisms": [],
        "dims": [],
        "ns": [],
        "per_cell": 1,
        "seed": 20261030,
    },
    "agent": {
        "image": "general-agent-eval/agent:py311-toolbox-v1",
        "base_url": "https://api.deepseek.com/anthropic",
        "model": "deepseek-chat",
        "auth_env": "DEEPSEEK_API_KEY",
        "timeout_s": 600,
        "grace_s": 30,
        "retries": 1,
        "disallowed_tools": "WebSearch,WebFetch",
        "pip_index_url": "https://mirrors.aliyun.com/pypi/simple/",
    },
    "docker": {
        "cpus": "2",
        "memory": "4g",
        "pids_limit": 512,
        "shm_size": "512m",
        "host_user": True,
        "keep_container_on_failure": True,
        "network": "bridge",
    },
    "judge": {"enabled": False},
    "baseline_results": "",
}


class ConfigError(RuntimeError):
    pass


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | str) -> dict:
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = _merge(DEFAULTS, raw)
    for key in ("cache_dir", "output_dir", "workspace_dir"):
        config[key] = Path(os.path.expanduser(str(config[key]))).resolve()
    local = config["data"].get("local_dir")
    if local:
        config["data"]["local_dir"] = Path(os.path.expanduser(str(local))).resolve()
    baseline = config.get("baseline_results")
    config["baseline_results"] = (
        Path(os.path.expanduser(str(baseline))).resolve() if baseline else None
    )
    return config
```

注意：**配置里不放密钥**。密钥只从环境变量读，见 `cli.py` 的 `_require_secret`。

---

## 1.3 `src/general_agent_eval/selector.py`

```python
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
```

抽样用 `blake2b(seed|dataset_id)` 排序，保证换机器、换目录后**选中的是同一批**——这是和基线可比的前提。

---

## 1.4 `src/general_agent_eval/data.py`

```python
"""数据集：定位、校验、加载、导出。"""
from __future__ import annotations

import hashlib
import shutil
import tarfile
import urllib.request
from pathlib import Path

import numpy as np

from .selector import Spec


class DataError(RuntimeError):
    pass


def artifact(spec: Spec) -> str:
    return spec.dataset_id.replace("/", "__")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(root: Path) -> str:
    """目录指纹：相对路径 + 文件大小，足够判断两次运行是否同一批数据。"""
    digest = hashlib.blake2b(digest_size=16)
    for path in sorted(Path(root).rglob("*.npz")):
        rel = path.relative_to(root).as_posix()
        digest.update(f"{rel}:{path.stat().st_size}\n".encode())
    return digest.hexdigest()


def ensure_data(config) -> Path:
    data = config["data"]
    local = data.get("local_dir")
    if local:
        root = Path(local)
        if not root.exists():
            raise DataError(f"data.local_dir 不存在：{root}")
        return root

    root = Path(config["cache_dir"]) / data["name"]
    marker = root / ".ready"
    if marker.exists():
        return root / "data"

    if not data.get("url"):
        raise DataError("未配置 data.local_dir 或 data.url")
    # 下载必须带 sha256：否则压缩包被替换就等于把任意文件解到缓存目录
    if not data.get("sha256"):
        raise DataError("使用 data.url 时必须配置 data.sha256")

    root.mkdir(parents=True, exist_ok=True)
    archive = root / "data.archive"
    urllib.request.urlretrieve(data["url"], archive)
    if sha256_file(archive) != data["sha256"]:
        archive.unlink(missing_ok=True)
        raise DataError("数据集 sha256 不匹配")
    _unpack(archive, root / "data")
    marker.write_text(data["sha256"], encoding="utf-8")
    return root / "data"


def _unpack(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        shutil.unpack_archive(archive, target)
        return
    with tarfile.open(archive) as tar:
        tar.extractall(target)


def load(spec: Spec) -> tuple[np.ndarray, np.ndarray]:
    with np.load(spec.path) as data:
        if "X" not in data or "adjacency" not in data:
            raise DataError(f"{spec.dataset_id} 缺少 X 或 adjacency")
        X = np.asarray(data["X"], dtype=float)
        adjacency = np.asarray(data["adjacency"])
    if X.ndim != 2 or adjacency.ndim != 2:
        raise DataError(f"{spec.dataset_id} 维度非法")
    if X.shape[1] == 0 or adjacency.shape[0] != adjacency.shape[1]:
        raise DataError(f"{spec.dataset_id} 形状非法")
    if X.shape[1] != adjacency.shape[0]:
        raise DataError(f"{spec.dataset_id} X 列数与 adjacency 不一致")
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
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([f"X{index}" for index in range(X.shape[1])])
        for row in X:
            writer.writerow([_format_cell(value) for value in row])
```

`ensure_data` 的两条硬规则：

1. 走下载就**必须**有 sha256，不匹配直接中止；
2. 缓存命中用 `.ready` 标记，避免半截解压被当成完整数据。

---

## 1.5 `src/general_agent_eval/parse.py`

```python
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
```

这里刻意**不做阈值化**。模型输出 `0.83` 就直接判 `values` 错误，而不是悄悄变成 1——否则格式问题会被伪装成"分数还行"。

已覆盖的真实噪声：CSV 表头、行首 `X0` 标签、`true/false`、空格/制表符/分号、多余列、缺行。

---

## 1.6 `src/general_agent_eval/metrics.py`

口径必须和 `evaluation-runner/evaluate.py` 完全一致。下面是从中抽出的最小实现：

```python
"""严格定向 + 骨架 + CPDAG-SHD 评分，口径与 evaluation-runner 一致。"""
from __future__ import annotations

from functools import lru_cache

from causallearn.graph.Dag import Dag
from causallearn.graph.Edge import Edge
from causallearn.graph.Endpoint import Endpoint
from causallearn.graph.GraphNode import GraphNode
from causallearn.utils.DAG2CPDAG import dag2cpdag

UNDIRECTED_TYPES = {"undirected", "bidirected", "partially_directed", "partially_oriented"}


def _partition(edges):
    directed = {(s, t) for s, t, kind in edges if kind == "directed"}
    undirected = [frozenset((s, t)) for s, t, kind in edges if kind in UNDIRECTED_TYPES]
    directed_pairs = {frozenset(edge) for edge in directed}
    mirrored = {
        pair for pair in directed_pairs
        if len(pair) == 2
        and (min(pair), max(pair)) in directed
        and (max(pair), min(pair)) in directed
    }
    directed -= {
        (a, b) for pair in mirrored
        for a, b in ((min(pair), max(pair)), (max(pair), min(pair)))
    }
    return directed, undirected, mirrored


def score(edges, gold) -> dict:
    directed, undirected_edges, mirrored = _partition(edges)
    undirected = set(undirected_edges)
    n_pred_edges = len(directed)
    true_positive = len(directed & gold)
    recall = true_positive / len(gold) if gold else 0.0
    precision = true_positive / n_pred_edges if n_pred_edges else 0.0

    predicted_pairs = {frozenset(edge) for edge in directed} | undirected | mirrored
    gold_pairs = {frozenset(edge) for edge in gold}
    intersection = len(predicted_pairs & gold_pairs)
    return {
        "n_pred_edges": n_pred_edges,
        "recall": recall,
        "precision": precision,
        "skeleton_recall": intersection / len(gold_pairs) if gold_pairs else 0.0,
        "skeleton_precision": intersection / len(predicted_pairs) if predicted_pairs else 0.0,
    }


def f1_score(precision: float, recall: float) -> float:
    total = precision + recall
    return 2 * precision * recall / total if total else 0.0


def _cpdag_compelled(gold: frozenset[tuple[int, int]]) -> frozenset[tuple[int, int]]:
    if not gold:
        return frozenset()
    size = max(max(edge) for edge in gold) + 1
    nodes = [GraphNode(str(index)) for index in range(size)]
    dag = Dag(nodes)
    for source, target in gold:
        dag.add_edge(Edge(nodes[source], nodes[target], Endpoint.TAIL, Endpoint.ARROW))
    compelled = set()
    for edge in dag2cpdag(dag).get_graph_edges():
        if edge.get_endpoint1() == Endpoint.TAIL and edge.get_endpoint2() == Endpoint.ARROW:
            compelled.add((int(edge.get_node1().get_name()), int(edge.get_node2().get_name())))
    return frozenset(compelled)


@lru_cache(maxsize=512)
def _compelled_cached(gold: tuple[tuple[int, int], ...]):
    return _cpdag_compelled(frozenset(gold))


def compelled_edges(gold):
    return _compelled_cached(tuple(sorted(gold)))


def shd(edges, gold) -> int:
    """缺失/多余各 1；被迫定向边方向不符 1；可逆边方向自由。"""
    directed, undirected_list, mirrored = _partition(edges)
    predicted_pairs: set[frozenset] = set()
    orientation: dict[frozenset, tuple[int, int]] = {}
    ambiguous: set[frozenset] = set()

    for source, target in directed:
        pair = frozenset((source, target))
        predicted_pairs.add(pair)
        if len(pair) == 2:
            orientation[pair] = (source, target)
    for pair in undirected_list:
        predicted_pairs.add(pair)
        ambiguous.add(pair)
    for pair in mirrored:
        predicted_pairs.add(pair)
        ambiguous.add(pair)

    gold_pairs = {frozenset(edge) for edge in gold}
    compelled = {frozenset(edge): edge for edge in compelled_edges(gold)}
    mismatched = 0
    for pair in predicted_pairs & gold_pairs:
        required = compelled.get(pair)
        if required is None:
            continue
        if pair in ambiguous or orientation.get(pair) != required:
            mismatched += 1
    return (
        len(predicted_pairs - gold_pairs)
        + len(gold_pairs - predicted_pairs)
        + mismatched
    )
```

关键点：SHD 不是"方向错就罚 1"。只有金标准 CPDAG 里**被迫定向**的边，方向错了才罚；可逆边（比如三节点链）方向反过来不罚。这是之前跑基线用的口径。

---

## 1.7 `src/general_agent_eval/audit.py`

```python
"""泄漏审计：扫描模型工具调用里出现的敏感路径/关键词。"""
from __future__ import annotations

import json

# .npz 不列入：模型自己保存中间结果会误报
LEAK_TOKENS = ("cdfm_bench", "causalagent3", "rep00", "/home/", "~/")


def scan(events) -> list[dict]:
    hits = []
    for event in events:
        content = (event.get("message") or {}).get("content") or ()
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            text = json.dumps(block.get("input") or {}, ensure_ascii=False).lower()
            for token in LEAK_TOKENS:
                if token in text:
                    hits.append({
                        "tool": block.get("name"),
                        "token": token,
                        "input": text[:300],
                    })
    return hits
```

容器里只有 `/work` 一个挂载，理论上模型读不到金标准；审计是**兜底证据**，不是唯一防线。

---

## 1.8 `src/general_agent_eval/selftest.py`

```python
"""离线自检：不联网、不调 Docker。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

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
```

---

## 1.9 `tests/test_parse.py`

```python
import numpy as np

from general_agent_eval.parse import parse_adjacency


def test_header_and_row_labels(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text(",X0,X1\nX0,0,1\nX1,0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert error is None
    assert np.array_equal(matrix, np.array([[0, 1], [0, 0]]))


def test_boolean_cells(tmp_path):
    path = tmp_path / "b.csv"
    path.write_text("X0,false,true\nX1,false,false\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert error is None
    assert np.array_equal(matrix, np.array([[0, 1], [0, 0]]))


def test_extra_columns_are_truncated(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("0,1,0,9\n0,0,1,9\n0,0,0,9\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 3)
    assert error is None
    assert matrix.shape == (3, 3)


def test_missing_file(tmp_path):
    matrix, error = parse_adjacency(tmp_path / "none.csv", 3)
    assert matrix is None and error == "missing"


def test_wrong_shape(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("0,1\n0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 3)
    assert matrix is None and error == "shape"


def test_diagonal_must_be_zero(tmp_path):
    path = tmp_path / "e.csv"
    path.write_text("1,0\n0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert matrix is None and error == "diagonal"


def test_non_binary_rejected(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("0,0.5\n0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert matrix is None and error == "values"
```

> `test_boolean_cells` 里断言 `is False` 是刻意的：对角线为真时整体应被拒绝。这个用例同时验证"真值转换"和"对角线校验"。

---

## 1.10 `tests/test_metrics.py`

```python
from general_agent_eval import metrics


def test_perfect_prediction():
    gold = {(0, 1), (1, 2)}
    edges = [(0, 1, "directed"), (1, 2, "directed")]
    result = metrics.score(edges, gold)
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert metrics.f1_score(result["precision"], result["recall"]) == 1.0
    assert metrics.shd(edges, gold) == 0


def test_missing_edge():
    gold = {(0, 1), (1, 2)}
    edges = [(0, 1, "directed")]
    result = metrics.score(edges, gold)
    assert result["recall"] == 0.5
    assert result["precision"] == 1.0
    assert metrics.shd(edges, gold) == 1


def test_extra_edge():
    gold = {(0, 1)}
    edges = [(0, 1, "directed"), (1, 2, "directed")]
    result = metrics.score(edges, gold)
    assert result["precision"] == 0.5
    assert result["recall"] == 1.0
    assert metrics.shd(edges, gold) == 1


def test_wrong_direction_on_compelled_edge():
    # 0->2 <-1 是 v-structure，两条边在 CPDAG 中被迫定向
    gold = {(0, 2), (1, 2)}
    edges = [(2, 0, "directed"), (1, 2, "directed")]
    result = metrics.score(edges, gold)
    assert result["recall"] == 0.5
    assert metrics.shd(edges, gold) == 1


def test_reversible_edge_direction_is_free():
    # 三节点链 0->1->2 的边在 CPDAG 中均可逆，方向反了不罚
    gold = {(0, 1), (1, 2)}
    edges = [(1, 0, "directed"), (1, 2, "directed")]
    assert metrics.shd(edges, gold) == 0


def test_skeleton_metrics():
    gold = {(0, 1)}
    edges = [(0, 1, "directed")]
    result = metrics.score(edges, gold)
    assert result["skeleton_precision"] == 1.0
    assert result["skeleton_recall"] == 1.0
```

---

## 1.11 验证

```bash
cd ~/CausalAgent3/general-agent-eval
source .venv/bin/activate
pip install pytest -i https://mirrors.aliyun.com/pypi/simple/
pytest -q
python -m general_agent_eval.selftest
```

预期：

```text
........
8 passed
selftest OK
```

如果 `test_wrong_direction_on_compelled_edge` 失败，先确认 `causal-learn` 版本和基线一致；这一条对 SHD 口径最敏感。

---

## 1.12 常见错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ImportError: causallearn` | 依赖没装 | `pip install causal-learn` |
| `test_reversible...` 实际 SHD=2 | 用了"方向错就罚"的错误口径 | 检查是否真的走了 CPDAG |
| `dag2cpdag` 报节点名不是整数 | 节点名用了中文/前缀 | 用 `str(index)` |
| 选集数量不对 | `per_cell` 或 `seed` 和基线不一致 | 对齐 `config.yaml` 的 `selection` |

---

## 1.13 本阶段检查点

- [ ] `pytest -q` 全绿
- [ ] `python -m general_agent_eval.selftest` 输出 `selftest OK`
- [ ] `config.py` 里没有任何密钥字段
- [ ] `data.py` 在 `url` 非空但 `sha256` 为空时会拒绝下载

下一份：[02-image](general-agent-eval-impl-02-image.md)。
