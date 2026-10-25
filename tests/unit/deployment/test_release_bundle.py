"""验证部署包生成、发布清单和镜像 digest 固定的契约。"""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "scripts/build_release_bundle.py"

APP_IMAGE = "ghcr.io/example/causalagent-app@sha256:" + "a" * 64
MCP_IMAGE = "ghcr.io/example/causalagent-mcp@sha256:" + "b" * 64
MYSQL_PRIMARY_IMAGE = "ghcr.io/example/causalagent-mysql-primary@sha256:" + "c" * 64
MYSQL_REPLICA_IMAGE = "ghcr.io/example/causalagent-mysql-replica@sha256:" + "d" * 64
SANDBOX_IMAGE = "ghcr.io/example/causalagent-data-preparation@sha256:" + "e" * 64
OFFICIAL_IMAGES = {
    "postgres": "postgres:16-alpine@sha256:" + "1" * 64,
    "nginx": "nginx:1.27-alpine@sha256:" + "2" * 64,
    "searxng": "searxng/searxng:2026.8.21-bbb3c7d82@sha256:" + "3" * 64,
    "valkey": "valkey/valkey:9-alpine@sha256:" + "4" * 64,
    "loki": "grafana/loki:3.7.4@sha256:" + "5" * 64,
    "alloy": "grafana/alloy:v1.18.0@sha256:" + "6" * 64,
    "grafana": "grafana/grafana:13.1.1@sha256:" + "7" * 64,
}


def _build(tmp_path: Path, images_file: Path) -> Path:
    output_dir = tmp_path / "release"
    proc = subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--output-dir",
            str(output_dir),
            "--release-id",
            "2026.10.11-abc1234",
            "--source-commit",
            "abc1234",
            "--app-image",
            APP_IMAGE,
            "--mcp-image",
            MCP_IMAGE,
            "--mysql-primary-image",
            MYSQL_PRIMARY_IMAGE,
            "--mysql-replica-image",
            MYSQL_REPLICA_IMAGE,
            "--sandbox-image",
            SANDBOX_IMAGE,
            "--images-file",
            str(images_file),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return output_dir


@pytest.fixture
def images_file(tmp_path: Path) -> Path:
    path = tmp_path / "official-images.json"
    path.write_text(json.dumps(OFFICIAL_IMAGES), encoding="utf-8")
    return path


def test_manifest_pins_every_image_and_lists_required_secret_keys(tmp_path, images_file):
    output_dir = _build(tmp_path, images_file)
    manifest = json.loads((output_dir / "release.manifest.json").read_text(encoding="utf-8"))

    assert manifest["release_id"] == "2026.10.11-abc1234"
    assert manifest["source_commit"] == "abc1234"
    for name, reference in manifest["images"].items():
        assert "@sha256:" in reference, name

    assert set(manifest["images"]) == {
        "app", "mcp", "mysql_primary", "mysql_replica",
        "postgres", "nginx", "searxng", "valkey", "loki", "alloy", "grafana",
        "sandbox_processing", "opensandbox_server", "opensandbox_execd",
        "opensandbox_egress", "opensandbox_interpreter_base",
    }
    # 清单只列必需键名，不包含任何密钥值。
    assert "MYSQL_PASSWORD" in manifest["secrets"]["required_keys"]
    assert "API_KEY" in manifest["secrets"]["required_keys"]
    assert manifest["secrets"]["schema_version"] == "v1"
    serialized = json.dumps(manifest)
    assert "unit-test" not in serialized
    assert "REPLACE_WITH" not in serialized
    upstream = json.loads((REPO_ROOT / "deploy/opensandbox/release-lock.json").read_text(encoding="utf-8"))
    for key, reference in upstream["images"].items():
        assert manifest["images"]["opensandbox_" + key] == reference
    assert manifest["images"]["sandbox_processing"] == SANDBOX_IMAGE
    independent = manifest["secrets"]["independent"]
    assert set(independent) == {"sandbox_controller_env", "sandbox_controller_token", "opensandbox_api_key"}
    assert independent["sandbox_controller_env"]["allowed_keys"] == ["MYSQL_PASSWORD", "MYSQL_WRITE_PASSWORD", "MYSQL_READ_PASSWORD"]
    assert "CAUSALAGENT_SANDBOX_API_KEY" not in manifest["secrets"]["allowed_keys"]
    assert "CAUSALAGENT_SANDBOX_API_KEY" not in manifest["secrets"]["required_keys"]


def test_bundle_excludes_source_and_secrets(tmp_path, images_file):
    output_dir = _build(tmp_path, images_file)
    archive = next(output_dir.glob("causalagent-release-*.tar.gz"))
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())

    assert "docker-compose.staging.yml" in names
    assert "docker-compose.prod.yml" in names
    assert "release.manifest.json" in names
    assert "images.lock.json" in names
    # 仅放行实际挂载的官方服务入口，不扩展为应用源码白名单。
    assert {name for name in names if name.endswith(".py")} == {"deploy/opensandbox/server_entrypoint.py"}
    assert {"deploy/opensandbox/services.yml", "deploy/opensandbox/config.toml", "deploy/opensandbox/release-lock.json"} <= names
    assert not any("frontend" in name for name in names)
    assert not any(name.endswith("causalagent.env") for name in names)
    assert not any(name.endswith((".env", ".token", ".key")) or name.endswith("/opensandbox-api-key") for name in names)


def test_images_lock_matches_manifest_images(tmp_path, images_file):
    output_dir = _build(tmp_path, images_file)
    manifest = json.loads((output_dir / "release.manifest.json").read_text(encoding="utf-8"))
    lock = json.loads((output_dir / "images.lock.json").read_text(encoding="utf-8"))

    assert lock == manifest["images"]


def test_non_digest_pinned_image_is_rejected(tmp_path):
    images = dict(OFFICIAL_IMAGES)
    images["grafana"] = "grafana/grafana:13.1.1"
    path = tmp_path / "official-images.json"
    path.write_text(json.dumps(images), encoding="utf-8")

    output_dir = tmp_path / "release"
    proc = subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--output-dir",
            str(output_dir),
            "--release-id",
            "2026.10.11-abc1234",
            "--source-commit",
            "abc1234",
            "--app-image",
            APP_IMAGE,
            "--mcp-image",
            MCP_IMAGE,
            "--mysql-primary-image",
            MYSQL_PRIMARY_IMAGE,
            "--mysql-replica-image",
            MYSQL_REPLICA_IMAGE,
            "--sandbox-image",
            SANDBOX_IMAGE,
            "--images-file",
            str(path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "not digest-pinned" in proc.stderr


def test_sandbox_deployment_files_have_verifiable_manifest_hashes(tmp_path, images_file):
    import hashlib

    output_dir = _build(tmp_path, images_file)
    manifest = json.loads((output_dir / "release.manifest.json").read_text(encoding="utf-8"))
    archive = next(output_dir.glob("causalagent-release-*.tar.gz"))
    with tarfile.open(archive) as tar:
        for section in ("compose_files_sha256", "sandbox_files_sha256"):
            for name, expected in manifest["deployment"][section].items():
                assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == expected


@pytest.mark.parametrize("reference", ["processing:latest", "processing@sha256:short", "processing@sha256:" + "g" * 64])
def test_processing_image_requires_a_complete_digest_before_writing_bundle(tmp_path, images_file, reference):
    output = tmp_path / "release"
    proc = subprocess.run([
        sys.executable, str(BUILD_SCRIPT), "--output-dir", str(output),
        "--release-id", "processing-check", "--source-commit", "abc1234",
        "--app-image", APP_IMAGE, "--mcp-image", MCP_IMAGE,
        "--mysql-primary-image", MYSQL_PRIMARY_IMAGE, "--mysql-replica-image", MYSQL_REPLICA_IMAGE,
        "--sandbox-image", reference, "--images-file", str(images_file),
    ], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)

    assert proc.returncode != 0
    assert "sandbox_processing is not digest-pinned" in proc.stderr
    assert not output.exists()


def test_processing_image_cannot_be_the_official_interpreter_base(tmp_path, images_file):
    lock = json.loads((REPO_ROOT / "deploy/opensandbox/release-lock.json").read_text(encoding="utf-8"))
    output = tmp_path / "release"
    proc = subprocess.run([
        sys.executable, str(BUILD_SCRIPT), "--output-dir", str(output),
        "--release-id", "processing-check", "--source-commit", "abc1234",
        "--app-image", APP_IMAGE, "--mcp-image", MCP_IMAGE,
        "--mysql-primary-image", MYSQL_PRIMARY_IMAGE, "--mysql-replica-image", MYSQL_REPLICA_IMAGE,
        "--sandbox-image", lock["images"]["interpreter_base"], "--images-file", str(images_file),
    ], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
    assert "not an upstream base" in proc.stderr
    assert not output.exists()


@pytest.mark.parametrize("release_id", ["../outside", "/absolute", "a\nb"])
def test_release_id_cannot_escape_the_release_directory(tmp_path, release_id):
    output = tmp_path / "release"
    proc = subprocess.run([
        sys.executable, str(BUILD_SCRIPT), "--output-dir", str(output),
        "--release-id", release_id, "--source-commit", "abc1234",
        "--app-image", APP_IMAGE, "--mcp-image", MCP_IMAGE,
        "--mysql-primary-image", MYSQL_PRIMARY_IMAGE, "--mysql-replica-image", MYSQL_REPLICA_IMAGE,
        "--sandbox-image", SANDBOX_IMAGE,
    ], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert "release_id must be" in proc.stderr
    assert not output.exists()


def test_official_image_references_match_compose_files():
    """官方镜像参考必须与 Compose 中的默认值一致，避免升级时漂移。"""
    from scripts.resolve_official_images import OFFICIAL_IMAGE_REFERENCES

    staging = (REPO_ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    production = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    combined = staging + production
    for reference in OFFICIAL_IMAGE_REFERENCES.values():
        assert reference in combined, reference
