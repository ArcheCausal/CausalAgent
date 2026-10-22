"""压测客户端清单：版本、运行时与网络位置，供报告记录。"""

from __future__ import annotations

import platform
import socket
import sys


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
    }


def _cpu_count() -> int | None:
    try:
        import os

        return os.cpu_count()
    except Exception:
        return None


def _total_memory_gib() -> float | None:
    try:
        import ctypes

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


def _local_ip() -> str | None:
    """通过 UDP 套接字获取本机出口地址，不产生实际流量。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("203.195.194.205", 8088))
            return probe.getsockname()[0]
    except OSError:
        return None
