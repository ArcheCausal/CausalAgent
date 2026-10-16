"""从 GHCR 解析同一源码提交的四个自有镜像，并校验 OCI revision。

仅查询镜像元数据，不下载镜像层或构建镜像。任意镜像缺失、digest 无效或源码
提交不匹配时退出；全部通过后才写入镜像清单和 GitHub Actions 输出。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

IMAGE_NAMES = {
    "app": "causalagent-app",
    "mcp": "causalagent-mcp",
    "mysql_primary": "causalagent-mysql-primary",
    "mysql_replica": "causalagent-mysql-replica",
}
REVISION_LABEL = "org.opencontainers.image.revision"


def source_commit(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("source commit must be a full lowercase Git SHA")
    return value


def registry_owner(value: str) -> str:
    owner = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", owner):
        raise argparse.ArgumentTypeError("invalid GHCR owner")
    return owner


def digest_override(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    digest = value if value.startswith("sha256:") else f"sha256:{value}"
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise argparse.ArgumentTypeError("digest must contain 64 lowercase hexadecimal digits")
    return digest


def inspect(reference: str, template: str) -> str:
    proc = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference, "--format", template],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode:
        raise ValueError(f"cannot inspect {reference}; check image existence and registry access")
    return proc.stdout.strip()


def verify_revision(image: object, expected_commit: str) -> None:
    """兼容单平台 config 和 Buildx 返回的多平台 config 映射。"""
    if not isinstance(image, dict) or not image:
        raise ValueError("image configuration is missing")
    if "config" in image:
        configs = [image]
    else:
        # BuildKit 的证明材料不是可运行平台，不能用其标签判定业务镜像版本。
        configs = [config for platform, config in image.items() if platform != "unknown/unknown"]
    if not configs:
        raise ValueError("image has no runnable platform configuration")
    for config in configs:
        if not isinstance(config, dict) or not isinstance(config.get("config"), dict):
            raise ValueError("platform image configuration is missing")
        labels = config["config"].get("Labels")
        if not isinstance(labels, dict) or labels.get(REVISION_LABEL) != expected_commit:
            raise ValueError("image OCI revision does not match the selected source commit")


def resolve(owner: str, commit: str, overrides: dict[str, str]) -> dict[str, str]:
    images: dict[str, str] = {}
    for name, repository in IMAGE_NAMES.items():
        base = f"ghcr.io/{owner}/{repository}"
        reference = f"{base}@{overrides[name]}" if overrides[name] else f"{base}:sha-{commit[:7]}"
        digest = inspect(reference, "{{.Manifest.Digest}}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"invalid registry digest for {name}")
        if overrides[name] and overrides[name] != digest:
            raise ValueError(f"registry digest does not match the requested digest for {name}")
        pinned = f"{base}@{digest}"
        # 在不可变引用上校验标签，避免解析 tag 后 tag 变化导致检查另一份内容。
        try:
            verify_revision(json.loads(inspect(pinned, "{{json .Image}}")), commit)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from exc
        images[name] = pinned
    return images


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, type=registry_owner)
    parser.add_argument("--source-commit", required=True, type=source_commit)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    for name in IMAGE_NAMES:
        parser.add_argument(f"--{name.replace('_', '-')}-sha256", default="", type=digest_override)
    args = parser.parse_args()
    overrides = {name: getattr(args, f"{name}_sha256") for name in IMAGE_NAMES}
    try:
        images = resolve(args.owner, args.source_commit, overrides)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"image resolution failed: {exc}\n")
    args.output.write_text(json.dumps(images, indent=2) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            for name, reference in images.items():
                handle.write(f"{name}={reference}\n")
    print(f"verified all four image revisions against {args.source_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
