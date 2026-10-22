"""压测客户端清单：版本、运行时与网络位置，供报告记录。"""

from __future__ import annotations

import platform
import ctypes
import hashlib
import importlib.metadata
import os
import socket
import subprocess
import sys
from pathlib import Path


def collect_client_manifest() -> dict:
    """采集压测客户端的规格与网络位置。"""
    return {
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": _cpu_count(),
        "total_memory_gib": _total_memory_gib(),
        "hostname": socket.gethostname(),
        "local_ip": _local_ip(),
        "source": collect_source_manifest(),
        "dependencies": {name: importlib.metadata.version(name) for name in ("httpx",)},
    }


def _cpu_count() -> int | None:
    return os.cpu_count()


def _total_memory_gib() -> float | None:
    try:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.ullTotalPhys / (1024 ** 3), 2)
    except Exception:
        return None


def collect_source_manifest() -> dict:
    """逐文件摘要包含未提交的客户端与采集器改动，不读取本地产物或凭据。"""
    root = Path(__file__).resolve().parents[1]
    paths = sorted(path for directory in ("runner", "ops")
                   for path in (root / directory).rglob("*.py")
                   if "__pycache__" not in path.parts)
    paths += sorted(root.glob("*.sh")) + [root / "requirements.txt", root / "verify_report.py"]
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in paths}
    aggregate = hashlib.sha256("\n".join(f"{name}:{digest}" for name, digest in sorted(files.items())).encode()).hexdigest()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    return {"git_revision": revision, "source_sha256": aggregate, "files": files}


def _local_ip() -> str | None:
    """通过 UDP 套接字获取本机出口地址，不产生实际流量。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("203.195.194.205", 8088))
            return probe.getsockname()[0]
    except OSError:
        return None
