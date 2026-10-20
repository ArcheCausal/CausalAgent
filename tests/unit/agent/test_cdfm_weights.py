"""CDFM 权重入卷的幂等与校验契约。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from Agent.CausalAgentMCP import cdfm_weights


def _content(name: str) -> bytes:
    """返回与文件名绑定、长度固定的测试内容。"""
    return (name.encode("utf-8") * 8)[:16]


def _pin_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 pin 替换为小体积测试文件，避免在单元测试中下载真实权重。"""
    files: dict[str, dict[str, object]] = {
        name: {"sha256": hashlib.sha256(_content(name)).hexdigest(), "size": len(_content(name))}
        for name in ("config.json", "model.safetensors")
    }
    monkeypatch.setattr(cdfm_weights, "WEIGHTS_FILES", files)


def _write_image(directory: Path, *, revision: str | None = None) -> None:
    """写出镜像内权重目录，可选地伪造一个过期 revision。"""
    directory.mkdir(parents=True, exist_ok=True)
    for name in cdfm_weights.WEIGHTS_FILES:
        (directory / name).write_bytes(_content(name))
    version = cdfm_weights.expected_version()
    if revision is not None:
        version["revision"] = revision
    (directory / cdfm_weights.VERSION_FILENAME).write_text(
        json.dumps(version, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def test_expected_version_is_derived_from_pinned_digests() -> None:
    version = cdfm_weights.expected_version()

    assert version["schema_version"] == cdfm_weights.VERSION_SCHEMA
    assert version["revision"] == cdfm_weights.WEIGHTS_REVISION
    assert version["file_count"] == len(cdfm_weights.WEIGHTS_FILES)
    assert version["total_bytes"] == sum(
        int(spec["size"]) for spec in cdfm_weights.WEIGHTS_FILES.values()
    )
    assert [item["name"] for item in version["files"]] == sorted(cdfm_weights.WEIGHTS_FILES)


def test_seed_rejects_image_weights_with_stale_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像内 version.json 与当前 pin 不一致时必须失败，不能把旧权重写进卷。"""
    _pin_fixture(monkeypatch)
    source = tmp_path / "image"
    _write_image(source, revision="stale")

    with pytest.raises(cdfm_weights.WeightsError):
        cdfm_weights.seed(source, tmp_path / "volume")
    assert not (tmp_path / "volume" / cdfm_weights.VERSION_FILENAME).exists()


def test_seed_rejects_tampered_image_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像内文件内容与摘要不符时必须失败，不能在卷里留下半成品。"""
    _pin_fixture(monkeypatch)
    source = tmp_path / "image"
    _write_image(source)
    (source / "model.safetensors").write_bytes(b"tampered")

    with pytest.raises(cdfm_weights.WeightsError):
        cdfm_weights.seed(source, tmp_path / "volume")
    assert not (tmp_path / "volume" / cdfm_weights.VERSION_FILENAME).exists()


def test_seed_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """第一次同步写入卷，第二次因 version.json 一致而直接退出。"""
    _pin_fixture(monkeypatch)
    source = tmp_path / "image"
    _write_image(source)
    target = tmp_path / "volume"

    assert cdfm_weights.seed(source, target) is True
    assert cdfm_weights.seed(source, target) is False
    assert json.loads((target / cdfm_weights.VERSION_FILENAME).read_text(encoding="utf-8")) == cdfm_weights.expected_version()
    for name in cdfm_weights.WEIGHTS_FILES:
        assert (target / name).read_bytes() == _content(name)
