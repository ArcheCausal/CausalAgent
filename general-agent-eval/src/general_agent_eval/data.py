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
