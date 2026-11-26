"""真实发布包、POSIX 密钥文件及 Docker Compose 解析器的部署预检。

需要 Linux root、Bash 和 Docker Compose CLI；不满足时跳过并保留未执行结论。
只解析 Compose，不连接 daemon，不拉取镜像、不启动服务；不模拟外部命令。
"""

from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = [
    pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0, reason="requires POSIX root for root:GID 3000 files"),
    pytest.mark.skipif(not shutil.which("docker") or not shutil.which("bash"), reason="requires real Docker Compose and Bash CLIs"),
]


def write_secret(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o640)
    os.chown(path, 0, 3000)


@pytest.fixture
def release(tmp_path):
    # 摘要仅作为离线打包参数；本测试不声称这些引用对应已发布镜像。
    official = tmp_path / "official.json"
    official.write_text(json.dumps({name: f"example/{name}@sha256:" + "a" * 64
                                    for name in ("postgres", "nginx", "searxng", "valkey", "loki", "alloy", "grafana")}), encoding="utf-8")
    output = tmp_path / "package"
    command = [sys.executable, str(ROOT / "scripts/build_release_bundle.py"),
               "--output-dir", str(output), "--release-id", "preflight",
               "--source-commit", "a" * 40, "--images-file", str(official)]
    for name in ("app", "mcp", "mysql-primary", "mysql-replica", "sandbox"):
        command.extend([f"--{name}-image", f"example/{name}@sha256:" + "b" * 64])
    built = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stderr
    bundle = tmp_path / "bundle"
    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        archive.extractall(bundle, filter="data")
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    config = tmp_path / "production.env"
    config.write_text((ROOT / "deploy/config/production.env.example").read_text(encoding="utf-8").replace(
        "/etc/causalagent/secrets/", str(secret_dir) + "/"), encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "ENVIRONMENT": "test",
           "CAUSALAGENT_CONFIG_FILE": str(config),
           "DOCKER_HOST": f"unix://{tmp_path}/no-daemon.sock",
           "DOCKER_CONFIG": str(tmp_path / "docker-config")}
    # 真正运行 deploy 的复制和 images.env 生成；缺少应用密钥应在 pull/up 前拒绝。
    releases = tmp_path / "releases"
    deployed = subprocess.run(["bash", str(bundle / "scripts/deploy.sh"), str(bundle), str(releases)],
                              env=env, capture_output=True, text=True, timeout=30)
    assert deployed.returncode != 0
    assert "causalagent.env" in deployed.stderr, deployed.stderr
    assert not (releases / "current").exists()
    target = releases / "preflight"
    assert (target / "images.env").is_file()
    manifest = json.loads((target / "release.manifest.json").read_text(encoding="utf-8"))
    write_secret(secret_dir / "causalagent.env", "".join(
        f"{key}={secrets.token_hex(32)}\n" for key in manifest["secrets"]["required_keys"]))
    return target, secret_dir, env


def verify(release, **overrides):
    target, _, env = release
    return subprocess.run(["bash", str(target / "scripts/verify.sh"), str(target)],
                          env={**env, **overrides}, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("compose", ["docker-compose.prod.yml", "docker-compose.staging.yml"])
def test_real_compose_and_shared_secret_pass_without_a_daemon(release, compose):
    result = verify(release, CAUSALAGENT_COMPOSE_FILE=compose)
    assert result.returncode == 0, result.stderr
    assert "deployment verified" in result.stdout


def test_shared_secret_requires_restricted_real_file_permissions(release):
    (release[1] / "causalagent.env").chmod(0o644)
    result = verify(release)
    assert result.returncode != 0
    assert "root:GID 3000" in result.stderr


def test_shared_file_rejects_unknown_keys(release):
    path = release[1] / "causalagent.env"
    write_secret(path, path.read_text(encoding="utf-8") + "UNKNOWN_SECRET=unexpected\n")
    result = verify(release)
    assert result.returncode != 0
    assert "UNKNOWN_SECRET" in result.stderr


def test_shared_file_rejects_duplicate_upstream_key(release):
    path = release[1] / "causalagent.env"
    write_secret(path, path.read_text(encoding="utf-8") + "CAUSALAGENT_SANDBOX_API_KEY=unexpected\n")
    result = verify(release)
    assert result.returncode != 0
    assert "CAUSALAGENT_SANDBOX_API_KEY" in result.stderr
    assert "重复定义" in result.stderr


def test_controller_and_upstream_tokens_must_differ(release):
    path = release[1] / "causalagent.env"
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    values["CAUSALAGENT_SANDBOX_API_KEY"] = values["CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN"]
    write_secret(path, "".join(f"{key}={value}\n" for key, value in values.items()))
    result = verify(release)
    assert result.returncode != 0
    assert "不同的值" in result.stderr


def test_base64_padding_is_valid_in_both_shared_authentication_fields(release):
    path = release[1] / "causalagent.env"
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    for key in ("CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN", "CAUSALAGENT_SANDBOX_API_KEY"):
        values[key] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    write_secret(path, "".join(f"{key}={value}\n" for key, value in values.items()))
    result = verify(release)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key", ["CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN", "CAUSALAGENT_SANDBOX_API_KEY"])
@pytest.mark.parametrize("value", [None, "short", "x" * 4097, "x" * 32 + " " + "y" * 32])
def test_shared_authentication_fields_are_required_and_validated(release, key, value):
    path = release[1] / "causalagent.env"
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    if value is None:
        del values[key]
    else:
        values[key] = value
    write_secret(path, "".join(f"{name}={secret}\n" for name, secret in values.items()))
    result = verify(release)
    assert result.returncode != 0
    assert key in result.stderr


def test_shell_cannot_override_the_processing_image(release):
    result = verify(release, CAUSALAGENT_SANDBOX_IMAGE="example/other@sha256:" + "c" * 64)
    assert result.returncode != 0
    assert "processing image differs from release manifest" in result.stderr


def test_deployment_file_changes_are_rejected(release):
    path = release[0] / "deploy/opensandbox/server_entrypoint.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    result = verify(release)
    assert result.returncode != 0
    assert "deployment file is missing or changed" in result.stderr


def test_custom_toml_must_preserve_runtime_image_lock(release, tmp_path):
    path = tmp_path / "custom.toml"
    config = (release[0] / "deploy/opensandbox/config.toml").read_text(encoding="utf-8")
    path.write_text(config.replace("docker.io/opensandbox/egress@sha256:", "example/other@sha256:"), encoding="utf-8")
    result = verify(release, CAUSALAGENT_OPENSANDBOX_CONFIG_SOURCE=str(path))
    assert result.returncode != 0
    assert "runtime images differ from release manifest" in result.stderr
