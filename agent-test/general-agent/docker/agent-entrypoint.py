#!/usr/bin/env python3
"""容器 PID 1：记录环境证据 → 运行 claude → 转发信号 → 记录后置证据。"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HARNESS = Path("/work/.harness")


def run(cmd, timeout=30):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (proc.stdout + proc.stderr).strip()
    except Exception as exc:
        return f"ERROR: {exc}"


def dump(name, payload):
    HARNESS.mkdir(parents=True, exist_ok=True)
    target = HARNESS / f"{name}.json"
    tmp = HARNESS / f".{name}.tmp"
    data = {
        "time": time.time(),
        "uid": os.getuid(),
        "cwd": os.getcwd(),
        "python": run(["python3", "-V"]),
        "claude": run(["claude", "--version"]),
        "pip_freeze": run(["pip", "freeze"]).splitlines(),
    }
    data.update(payload)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, target)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: agent-entrypoint.py <command> [args...]", file=sys.stderr)
        return 2

    dump("pre", {"command": sys.argv[1:]})
    child = subprocess.Popen(sys.argv[1:])

    def forward(signum, frame):
        # 只转发；退出码仍以子进程为准
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    code = child.wait()
    # 被信号终止时 Popen 返回负值；转成 shell/docker 惯用的 128+signal
    if code < 0:
        code = 128 - code
    dump("post", {"command": sys.argv[1:], "exit_code": code})
    return code


if __name__ == "__main__":
    sys.exit(main())
