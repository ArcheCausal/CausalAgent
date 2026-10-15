"""解析官方镜像的不可变 digest，生成 images.lock.json 的输入文件。

文档职责：集中定义官方镜像的参考 tag，并说明 digest 只能由 CI 用 docker buildx 解析。

适用范围：CI 生成部署包前使用；本地没有 Docker 时不得猜测或手写 digest。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 与 docker-compose.*.yml 中的官方镜像保持一致；升级时两处一起改。
OFFICIAL_IMAGE_REFERENCES = {
    "postgres": "postgres:16-alpine",
    "nginx": "nginx:1.27-alpine",
    "searxng": "searxng/searxng:2026.8.21-bbb3c7d82",
    "valkey": "valkey/valkey:9-alpine",
    "loki": "grafana/loki:3.7.4",
    "alloy": "grafana/alloy:v1.18.0",
    "grafana": "grafana/grafana:13.1.1",
}


def resolve(reference: str) -> str:
    """用 buildx 解析一个参考的 manifest digest。"""
    proc = subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{.Manifest.Digest}}",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"无法解析 {reference} 的 digest: {proc.stderr.strip()}"
        )
    return f"{reference}@{proc.stdout.strip()}"


def main() -> int:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "official-images.json")
    resolved = {
        name: resolve(reference)
        for name, reference in OFFICIAL_IMAGE_REFERENCES.items()
    }
    output.write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
