"""第一阶段：网络、入口与测试账号可访问性预检。"""

from __future__ import annotations

import json
import socket
import time

import httpx

from runner import config
from runner.manifest import collect_client_manifest


def tcp_probe(host: str, port: int, timeout: float = 10.0) -> dict:
    """对入口端口做一次 TCP 连接探测。"""
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "reachable": True,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
            }
    except OSError as exc:
        return {
            "reachable": False,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "error": type(exc).__name__,
        }


def run_preflight(base_url: str) -> dict:
    """执行入口预检并返回结构化结果。"""
    parsed = httpx.URL(base_url)
    host = parsed.host or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    result: dict = {
        "base_url": base_url,
        "client": collect_client_manifest(),
        "tcp": tcp_probe(host, port),
        "http": {},
    }
    with httpx.Client(
        base_url=base_url, timeout=config.HTTP_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for path in ("/", "/api/check_auth", "/auth/sign-in", "/dashboard"):
            started = time.monotonic()
            try:
                response = client.get(path)
                result["http"][path] = {
                    "status": response.status_code,
                    "location": response.headers.get("location"),
                    "request_id": response.headers.get("X-Request-ID"),
                    "server": response.headers.get("Server"),
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            except httpx.HTTPError as exc:
                result["http"][path] = {"status": 0, "error": type(exc).__name__}
    return result


def render_preflight(result: dict) -> str:
    """把预检结果渲染为可读文本。"""
    lines = [
        f"入口: {result['base_url']}",
        f"TCP: {json.dumps(result['tcp'], ensure_ascii=False)}",
        f"客户端: {json.dumps(result['client'], ensure_ascii=False)}",
    ]
    for path, detail in result["http"].items():
        lines.append(f"{path}: {json.dumps(detail, ensure_ascii=False)}")
    return "\n".join(lines)
