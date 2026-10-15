"""生成 CausalAgent 部署包与发布清单。

文档职责：定义部署包的目录结构、镜像 digest 固定方式，以及 release.manifest.json 与
images.lock.json 的生成规则。

适用范围：CI 在某个源码提交上生成部署包时使用；服务器只用部署包部署，不执行 git pull。

用法（在仓库根目录执行）：

    python scripts/build_release_bundle.py \
        --output-dir dist/release \
        --release-id 2026.10.11-abc1234 \
        --source-commit abc1234... \
        --app-image ghcr.io/example/causalagent-app@sha256:... \
        --mcp-image ghcr.io/example/causalagent-mcp@sha256:... \
        --mysql-primary-image ghcr.io/example/causalagent-mysql-primary@sha256:... \
        --mysql-replica-image ghcr.io/example/causalagent-mysql-replica@sha256:...

官方镜像 digest 由 --images-file 提供（JSON: {"postgres": "postgres:16-alpine@sha256:..."}），
或用 --resolve-official-images 调用 docker buildx imagetools 解析。脚本不写入任何真实密钥。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from config.secret_file import (  # noqa: E402
    REQUIRED_SECRET_KEYS,
    SECRET_SCHEMA_VERSION,
)

# 部署包只包含 Compose、网关与观测配置、运维脚本和非密钥配置模板；
# 不包含 Python/Vue/TypeScript 源码、真实密钥、数据库数据和 RAG 正式索引。
BUNDLE_INCLUDE = (
    "docker-compose.staging.yml",
    "docker-compose.prod.yml",
    "deploy",
    # 只打包观测的配置文件；observability/*.py 属于应用源码，已经在镜像里。
    "observability/alloy",
    "observability/loki",
    "observability/grafana",
    "scripts/deploy.sh",
    "scripts/verify.sh",
    "scripts/rollback.sh",
    "scripts/migrate-rag-storage.sh",
)
BUNDLE_EXCLUDE_NAMES = {".env", "causalagent.env", "secrets.json"}
OFFICIAL_IMAGES = (
    "postgres",
    "nginx",
    "searxng",
    "valkey",
    "loki",
    "alloy",
    "grafana",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_paths(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _alembic_head() -> str:
    versions = sorted((REPO_ROOT / "Database/migrations/versions").glob("*.py"))
    heads: dict[str, set[str]] = {}
    revisions: dict[str, str] = {}
    for path in versions:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        metadata: dict[str, object] = {}
        for node in module.body:
            if isinstance(node, ast.Assign):
                targets = [target for target in node.targets if isinstance(target, ast.Name)]
                for name in ("revision", "down_revision"):
                    if any(target.id == name for target in targets):
                        metadata[name] = ast.literal_eval(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in {"revision", "down_revision"} and node.value is not None:
                    metadata[node.target.id] = ast.literal_eval(node.value)
        if "revision" not in metadata or "down_revision" not in metadata:
            raise SystemExit(f"migration metadata missing in {path.name}")
        revision = str(metadata["revision"])
        down = metadata["down_revision"]
        revisions[revision] = path.name
        heads.setdefault(revision, set())
        parents = down if isinstance(down, (tuple, list)) else (down,)
        for parent in parents:
            if parent:
                heads.setdefault(str(parent), set()).add(revision)
    head_revisions = sorted(name for name in revisions if not heads.get(name))
    if len(head_revisions) != 1:
        raise SystemExit(f"expected exactly one alembic head, found: {head_revisions}")
    return head_revisions[0]


def _resolve_official_images(images: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for name in OFFICIAL_IMAGES:
        reference = images.get(name)
        if not reference:
            raise SystemExit(f"missing official image reference for {name}")
        if "@sha256:" in reference:
            resolved[name] = reference
            continue
        proc = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{.Manifest.Digest}}"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"could not resolve digest for {name}: {proc.stderr.strip()}")
        digest = proc.stdout.strip()
        resolved[name] = f"{reference}@{digest}"
    return resolved


def _collect_bundle_files() -> list[Path]:
    files: list[Path] = []
    for entry in BUNDLE_INCLUDE:
        target = REPO_ROOT / entry
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            for path in target.rglob("*"):
                if not path.is_file():
                    continue
                if path.name in BUNDLE_EXCLUDE_NAMES:
                    continue
                if "__pycache__" in path.parts:
                    continue
                files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--mcp-image", required=True)
    parser.add_argument("--mysql-primary-image", required=True)
    parser.add_argument("--mysql-replica-image", required=True)
    parser.add_argument("--images-file")
    parser.add_argument("--resolve-official-images", action="store_true")
    args = parser.parse_args()

    if args.images_file:
        official_references = json.loads(Path(args.images_file).read_text(encoding="utf-8"))
    else:
        official_references = {name: f"{name}:latest" for name in OFFICIAL_IMAGES}

    images = {
        "app": args.app_image,
        "mcp": args.mcp_image,
        "mysql_primary": args.mysql_primary_image,
        "mysql_replica": args.mysql_replica_image,
    }
    if args.resolve_official_images:
        images.update(_resolve_official_images(official_references))
    else:
        images.update(official_references)

    for name, reference in images.items():
        if "@sha256:" not in reference:
            raise SystemExit(f"image {name} is not digest-pinned: {reference}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    bundle_files = _collect_bundle_files()
    bundle_sha = _sha256_paths(bundle_files)
    compose_sha = _sha256_file(REPO_ROOT / "docker-compose.staging.yml")
    gateway_files = sorted((REPO_ROOT / "deploy/staging").rglob("*"))
    gateway_sha = _sha256_paths([path for path in gateway_files if path.is_file()])
    observability_files = sorted((REPO_ROOT / "observability").rglob("*"))
    observability_sha = _sha256_paths(
        [path for path in observability_files if path.is_file() and "__pycache__" not in path.parts]
    )

    manifest = {
        "release_id": args.release_id,
        "source_commit": args.source_commit,
        "deployment_bundle_sha256": bundle_sha,
        "images": images,
        "deployment": {
            "compose_sha256": compose_sha,
            "gateway_config_sha256": gateway_sha,
            "observability_config_sha256": observability_sha,
        },
        "database": {
            "migration_head": _alembic_head(),
            "checkpoint_schema": "langgraph-checkpoint-postgres",
        },
        "rag": {
            "runtime_schema": "multimodal-release-v1",
            "required_index_schema": "multimodal-manifest-v1",
            "active_release": "external-volume",
        },
        "secrets": {
            "schema_version": SECRET_SCHEMA_VERSION,
            "required_keys": list(REQUIRED_SECRET_KEYS),
        },
    }

    (output_dir / "images.lock.json").write_text(
        json.dumps(images, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "release.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    archive = output_dir / f"causalagent-release-{args.release_id}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in bundle_files:
            tar.add(path, arcname=path.relative_to(REPO_ROOT).as_posix())
        tar.add(
            output_dir / "release.manifest.json",
            arcname="release.manifest.json",
        )
        tar.add(output_dir / "images.lock.json", arcname="images.lock.json")

    print(f"release_id={args.release_id}")
    print(f"bundle_sha256={bundle_sha}")
    print(f"archive={archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
