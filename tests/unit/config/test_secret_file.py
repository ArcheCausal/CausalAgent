"""验证服务器密钥文件的读取、格式校验和权限边界。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from config import secret_file
from config.secret_file import (
    REQUIRED_SECRET_KEYS,
    SECRET_KEYS,
    SecretFileError,
    load_secrets,
    require_secret,
    reset_secret_cache,
)


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _complete_lines(**overrides: str) -> list[str]:
    values = {name: f"value-{index}" for index, name in enumerate(REQUIRED_SECRET_KEYS)}
    values.update(overrides)
    return [f"{name}={value}" for name, value in values.items()]


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_secret_cache()
    yield
    reset_secret_cache()


def test_load_secrets_reads_all_declared_keys(tmp_path):
    path = _write(
        tmp_path / "causalagent.env",
        _complete_lines(CAUSAL_MCP_SIGNING_KEY_PREVIOUS="previous-key"),
    )

    values = load_secrets(path=path)

    assert set(values) == set(REQUIRED_SECRET_KEYS) | {"CAUSAL_MCP_SIGNING_KEY_PREVIOUS"}
    assert values["SECRET_KEY"] == "value-%d" % list(REQUIRED_SECRET_KEYS).index("SECRET_KEY")


def test_load_secrets_does_not_export_values_to_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "stale-from-environment")
    path = _write(tmp_path / "causalagent.env", _complete_lines())

    values = load_secrets(path=path)

    assert "SECRET_KEY" not in os.environ
    assert values["SECRET_KEY"] != "stale-from-environment"


def test_load_secrets_clears_declared_keys_absent_from_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "leftover")
    path = _write(tmp_path / "causalagent.env", _complete_lines())

    load_secrets(path=path)

    assert "LANGCHAIN_API_KEY" not in os.environ


def test_missing_file_raises_without_leaking_values(tmp_path):
    with pytest.raises(SecretFileError) as excinfo:
        load_secrets(path=tmp_path / "absent.env")

    assert "密钥文件不存在" in str(excinfo.value)


def test_missing_required_key_is_rejected(tmp_path):
    lines = [
        line
        for line in _complete_lines()
        if not line.startswith("SECRET_KEY=")
    ]
    path = _write(tmp_path / "causalagent.env", lines)

    with pytest.raises(SecretFileError) as excinfo:
        load_secrets(path=path)

    assert "SECRET_KEY" in str(excinfo.value)


def test_unknown_key_is_rejected(tmp_path):
    path = _write(
        tmp_path / "causalagent.env",
        _complete_lines(NOT_A_DECLARED_SECRET="oops"),
    )

    with pytest.raises(SecretFileError) as excinfo:
        load_secrets(path=path)

    assert "NOT_A_DECLARED_SECRET" in str(excinfo.value)


def test_duplicate_key_is_rejected(tmp_path):
    lines = _complete_lines()
    lines.append("SECRET_KEY=duplicate")
    path = _write(tmp_path / "causalagent.env", lines)

    with pytest.raises(SecretFileError) as excinfo:
        load_secrets(path=path)

    assert "重复定义" in str(excinfo.value)


def test_line_without_equals_is_rejected(tmp_path):
    lines = _complete_lines()
    lines.append("BROKEN-LINE")
    path = _write(tmp_path / "causalagent.env", lines)

    with pytest.raises(SecretFileError):
        load_secrets(path=path)


def test_empty_value_is_rejected(tmp_path):
    lines = [
        "SECRET_KEY=" if line.startswith("SECRET_KEY=") else line
        for line in _complete_lines()
    ]
    path = _write(tmp_path / "causalagent.env", lines)

    with pytest.raises(SecretFileError) as excinfo:
        load_secrets(path=path)

    assert "SECRET_KEY" in str(excinfo.value)


def test_documentation_example_matches_declared_keys():
    """部署模板必须与 SECRET_KEYS 完全一致，避免文档与代码漂移。"""
    example = Path(__file__).resolve().parents[3] / "deploy/secrets/causalagent.env.example"
    declared = set(SECRET_KEYS)
    documented = set()
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# 可选键："):
            optional_keys = line.split("：", 1)[1].split("（", 1)[0]
            documented.update(key.strip() for key in optional_keys.split(","))
            continue
        if line.startswith("#"):
            continue
        documented.add(line.split("=", 1)[0])

    assert documented == declared


@pytest.mark.skipif(os.name == "nt", reason="Windows 无 POSIX 权限位")
def test_insecure_permissions_are_rejected(tmp_path):
    path = _write(tmp_path / "causalagent.env", _complete_lines())
    path.chmod(0o644)

    with pytest.raises(SecretFileError) as excinfo:
        load_secrets(path=path)

    assert "权限不安全" in str(excinfo.value)


@pytest.mark.skipif(os.name == "nt", reason="Windows 无 POSIX 权限位")
def test_owner_only_permissions_are_accepted(tmp_path):
    path = _write(tmp_path / "causalagent.env", _complete_lines())
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert load_secrets(path=path)


@pytest.mark.skipif(os.name == "nt", reason="Windows 无 POSIX 权限位")
def test_dedicated_group_permissions_are_accepted(tmp_path):
    path = _write(tmp_path / "causalagent.env", _complete_lines())
    path.chmod(0o640)

    assert load_secrets(path=path)


def test_require_secret_rejects_undeclared_key(tmp_path):
    load_secrets(path=_write(tmp_path / "causalagent.env", _complete_lines()))

    with pytest.raises(SecretFileError):
        require_secret("NOT_DECLARED")
