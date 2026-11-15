"""环境自检。只读，不跑评测。"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from . import container, data


def _writable(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return str(path)


def _disk(path: Path) -> str:
    free = shutil.disk_usage(path).free / (1024 ** 3)
    if free < 10:
        raise RuntimeError(f"剩余空间仅 {free:.1f} GB")
    return f"{free:.1f} GB free"


def _image_entry(config, entrypoint, *args) -> str:
    proc = container.docker(
        ["run", "--rm", "--entrypoint", entrypoint, config["agent"]["image"], *args],
    )
    return proc.stdout.strip()


def doctor(config) -> int:
    checks = []

    def check(name, fn):
        try:
            checks.append((name, True, fn() or ""))
        except Exception as exc:
            checks.append((name, False, str(exc)[:200]))

    def secret():
        name = config["agent"]["auth_env"]
        if not os.environ.get(name):
            raise RuntimeError(f"缺少环境变量 {name}")
        return "已设置（值不打印）"

    def mount():
        workspace = Path(config["workspace_dir"])
        workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(workspace)) as tmp:
            case = Path(tmp) / "probe"
            case.mkdir()
            container.probe_mount(config, case)
        return "ok"

    check("docker", lambda: container.docker(
        ["version", "--format", "{{.Server.Version}}"]
    ).stdout.strip())
    check("image", lambda: container.image_digest(config["agent"]["image"]))
    check("auth env", secret)
    check("output dir", lambda: _writable(Path(config["output_dir"])))
    check("workspace dir", lambda: _writable(Path(config["workspace_dir"])))
    check("data", lambda: str(data.ensure_data(config)))
    check("toolbox", lambda: _image_entry(
        config, "python3", "-c",
        "import sys,numpy,pandas,sklearn,lingam;"
        "from causallearn.utils.DAG2CPDAG import dag2cpdag;"
        "print(sys.version.split()[0])",
    ))
    check("claude", lambda: _image_entry(config, "claude", "--version"))
    check("mount", mount)

    def lock():
        path = data.toolbox_lock_path()
        if not path.is_file():
            raise RuntimeError("toolbox.lock 不存在")
        return data.sha256_file(path)[:12]

    check("lock", lock)
    check("resources", lambda: container.check_resources(config))
    check("disk", lambda: _disk(Path(config["output_dir"])))

    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{'OK  ' if ok else 'FAIL'} {name:<{width}}  {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1
