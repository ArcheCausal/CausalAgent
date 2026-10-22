"""保存按源码 hash 命名的不可覆盖客户端副本，供整轮长时间运行。"""
from __future__ import annotations

import json
import hashlib
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from runner import config
from runner.manifest import collect_client_manifest


def freeze() -> pathlib.Path:
    manifest = collect_client_manifest()
    target = config.LOCAL_DIR / "client-snapshots" / manifest["source"]["source_sha256"] / "loadtest"
    target.mkdir(parents=True, exist_ok=False)
    for relative, expected in manifest["source"]["files"].items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config.LOADTEST_DIR / relative, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
            raise RuntimeError("冻结期间源文件变化，请使用新快照，不覆盖当前副本")
    (target / "client-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(freeze())
