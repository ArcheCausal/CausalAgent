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
    # docker compose config 只需通过 Compose 的非空变量校验；不读取或输出仓库密钥。
    env["CHECKPOINT_POSTGRES_PASSWORD"] = "compose-test-only"
    env["GRAFANA_ADMIN_PASSWORD"] = "compose-test-only"
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
    # 密钥由 SEARXNG_SECRET 环境变量注入，配置目录只读挂载。
    assert searxng["environment"]["SEARXNG_SECRET"]
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
