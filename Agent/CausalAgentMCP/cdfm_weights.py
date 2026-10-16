"""CDFM 权重的构建期下载与运行期入卷。

镜像构建阶段把固定 revision 的权重下载到只读目录 ``/opt/cdfm-weights-image``，
并逐文件校验字节数与 SHA-256；运行期由一次性 seed 服务把镜像内容同步进命名卷，
使 ``causal-mcp`` 只读挂载本地目录，不再访问网络。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.request


CDFM_REPO_ID = "DMIRLAB/CDFM"
VERSION_SCHEMA = "cdfm_weights_v1"
VERSION_FILENAME = "version.json"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"

# revision 与逐文件摘要由实现者在构建时解析一次后固定在此处，不接受运行时输入，
# 保证同一镜像内的模型内容不会漂移。
WEIGHTS_REVISION = "fd3ac4f72e617c9b2165082493f2494c9f980d04"
WEIGHTS_FILES: dict[str, dict[str, object]] = {
    "config.json": {
        "sha256": "4787703699634465ddcb286c446c927a78be0502bb1eba3f68d1546258e2befd",
        "size": 717,
    },
    "model.safetensors": {
        "sha256": "937047358da5368af6e38b99beac1d8b3b789afb8c9c4c857767eb67e07eb760",
        "size": 38707144,
    },
}


class WeightsError(RuntimeError):
    """权重下载、校验或入卷失败。"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_version() -> dict[str, object]:
    """返回由固定 revision 和逐文件摘要派生的版本描述。"""
    files = [
        {"name": name, "sha256": spec["sha256"], "size": spec["size"]}
        for name, spec in sorted(WEIGHTS_FILES.items())
    ]
    return {
        "schema_version": VERSION_SCHEMA,
        "repo_id": CDFM_REPO_ID,
        "revision": WEIGHTS_REVISION,
        "file_count": len(files),
        "total_bytes": sum(int(item["size"]) for item in files),
        "files": files,
    }


def _download(url: str, destination: Path, *, timeout_seconds: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "causalagent-build"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            with destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise WeightsError(f"download failed: {url}") from exc


def fetch(destination: Path, *, endpoint: str, timeout_seconds: int = 300) -> dict[str, object]:
    """下载并校验全部权重文件，任何不合格都直接失败。"""
    version = expected_version()
    destination.mkdir(parents=True, exist_ok=True)
    for item in version["files"]:
        name = str(item["name"])
        target = destination / name
        url = f"{endpoint.rstrip('/')}/{CDFM_REPO_ID}/resolve/{WEIGHTS_REVISION}/{name}"
        with tempfile.TemporaryDirectory(dir=str(destination)) as staging:
            temporary = Path(staging) / name
            _download(url, temporary, timeout_seconds=timeout_seconds)
            actual_size = temporary.stat().st_size
            if actual_size != int(item["size"]):
                raise WeightsError(
                    f"size mismatch for {name}: expected {item['size']}, got {actual_size}"
                )
            actual_hash = _sha256_file(temporary)
            if actual_hash != item["sha256"]:
                raise WeightsError(f"sha256 mismatch for {name}")
            shutil.move(str(temporary), str(target))
    total = sum(int(item["size"]) for item in version["files"])
    actual_total = sum((destination / str(item["name"])).stat().st_size for item in version["files"])
    if actual_total != total:
        raise WeightsError(f"total byte mismatch: expected {total}, got {actual_total}")
    (destination / VERSION_FILENAME).write_text(
        json.dumps(version, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return version


def _read_version(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def seed(source: Path, target: Path) -> bool:
    """把镜像内权重幂等同步进命名卷；已一致时返回 False 表示未改动。"""
    expected = expected_version()
    if _read_version(source / VERSION_FILENAME) != expected:
        raise WeightsError(f"image weights are not the pinned revision: {source}")
    if _read_version(target / VERSION_FILENAME) == expected:
        return False
    for item in expected["files"]:
        name = str(item["name"])
        origin = source / name
        if not origin.is_file() or _sha256_file(origin) != item["sha256"]:
            raise WeightsError(f"image weight is missing or corrupt: {name}")
    target.mkdir(parents=True, exist_ok=True)
    for item in expected["files"]:
        name = str(item["name"])
        temporary = target / f".{name}.tmp"
        shutil.copyfile(source / name, temporary)
        os.replace(temporary, target / name)
    temporary_version = target / f".{VERSION_FILENAME}.tmp"
    temporary_version.write_text(
        json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_version, target / VERSION_FILENAME)
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CDFM weights fetch and seed")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch_parser = subparsers.add_parser("fetch", help="download and verify weights at build time")
    fetch_parser.add_argument("--destination", required=True)
    fetch_parser.add_argument("--endpoint", default=os.getenv("HF_ENDPOINT", DEFAULT_HF_ENDPOINT))
    fetch_parser.add_argument("--timeout-seconds", type=int, default=300)
    seed_parser = subparsers.add_parser("seed", help="copy verified weights into the named volume")
    seed_parser.add_argument("--source", required=True)
    seed_parser.add_argument("--target", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "fetch":
            version = fetch(
                Path(args.destination),
                endpoint=args.endpoint,
                timeout_seconds=args.timeout_seconds,
            )
            print(f"cdfm-weights-fetched revision={version['revision']} total_bytes={version['total_bytes']}")
            return 0
        changed = seed(Path(args.source), Path(args.target))
        print("cdfm-weights-seeded changed=" + ("true" if changed else "false"))
        return 0
    except WeightsError as exc:
        print(f"cdfm-weights-error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
