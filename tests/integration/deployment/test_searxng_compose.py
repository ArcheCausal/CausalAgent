"""SearXNG Compose 部署契约的静态验证。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def _compose_config() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI 不可用，跳过 Compose 配置验证")

    env = os.environ.copy()
    # Compose 校验只解析密钥文件的挂载路径；使用仓库里的测试密钥文件。
    env["CAUSALAGENT_SECRET_FILE"] = str(REPO_ROOT / "tests/unit-test-secrets.env")
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, "docker compose config failed"
    return json.loads(proc.stdout)


def test_searxng_has_pinned_image_and_shallow_healthcheck():
    config = _compose_config()
    searxng = config["services"]["searxng"]

    assert searxng["image"] != "searxng/searxng:latest"
    healthcheck = searxng["healthcheck"]
    assert healthcheck["test"]
    assert "/healthz" in " ".join(healthcheck["test"])
    assert "searxng-init" not in config["services"]
    # 密钥由容器启动适配脚本从密钥文件导出，配置目录只读挂载。
    entrypoint = searxng["entrypoint"]
    assert any("export_secret SEARXNG_SECRET" in str(item) for item in entrypoint)
    assert "SEARXNG_SECRET" not in searxng.get("environment", {})
    assert any(item["source"] == "causalagent_env" for item in searxng.get("secrets", []))
    assert any(
        mount["target"] == "/etc/searxng" and mount.get("read_only") is True
        for mount in searxng["volumes"]
    )


def test_default_app_and_worker_do_not_depend_on_searxng():
    config = _compose_config()

    for service_name in ("app", "worker"):
        depends_on = config["services"][service_name].get("depends_on", {})
        assert "searxng" not in depends_on


def test_default_compose_keeps_agent_and_rag_workers_separate():
    config = _compose_config()
    services = config["services"]

    assert {
        "mysql-primary",
        "mysql-replica",
        "postgres-checkpoint",
        "db-bootstrap",
        "app",
        "worker",
        "monitor",
        "agent-persistence-cleanup",
        "rag-eval-worker",
        "searxng",
        "valkey",
        "loki",
        "alloy",
        "grafana",
    } <= set(services)
    assert services["worker"]["command"] == ["python", "-m", "app.agent.worker"]
    assert services["rag-eval-worker"]["command"] == ["python", "-m", "app.rag_eval.worker"]
    assert "searxng" not in services["rag-eval-worker"].get("depends_on", {})
