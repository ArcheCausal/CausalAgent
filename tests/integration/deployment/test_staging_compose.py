"""预发 Compose、镜像和服务暴露边界的静态合同。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
STAGING_ENV = REPO_ROOT / ".env.staging.example"


def _staging_config() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI 不可用，跳过预发 Compose 配置验证")

    proc = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(STAGING_ENV),
            "-f",
            str(STAGING_COMPOSE),
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, "docker compose could not validate the staging configuration"
    return json.loads(proc.stdout)


def test_staging_includes_search_observability_and_digest_pinned_database_images():
    config = _staging_config()
    services = config["services"]

    assert {
        "searxng",
        "valkey",
        "loki",
        "alloy",
        "grafana",
    } <= set(services)

    for service_name in (
        "app",
        "causal-mcp",
        "mysql-primary",
        "mysql-replica",
    ):
        service = services[service_name]
        assert re.fullmatch(r"ghcr[.]io/causalagent-team/[^@]+@sha256:[0-9a-f]{64}", service["image"])

    for service_name in ("mysql-primary", "mysql-replica"):
        assert "build" not in services[service_name]


def test_only_gateway_is_public_and_grafana_is_reached_through_the_gateway():
    services = _staging_config()["services"]
    published = {
        name: service["ports"]
        for name, service in services.items()
        if service.get("ports")
    }

    # 只有 gateway 暴露公网端口；Grafana 不再单独映射宿主端口。
    assert set(published) == {"gateway"}
    gateway_port = published["gateway"][0]
    assert gateway_port["target"] == 8080
    assert gateway_port["published"] == "8088"
    assert gateway_port.get("host_ip") in (None, "0.0.0.0")
    for service_name in (
        "grafana",
        "searxng",
        "valkey",
        "loki",
        "alloy",
        "mysql-primary",
        "mysql-replica",
    ):
        assert not services[service_name].get("ports")
    # Grafana 走子路径，gateway 需要同时加入观测网络才能反代到它。
    grafana = services["grafana"]["environment"]
    assert grafana["GF_SERVER_ROOT_URL"] == "/grafana/"
    assert grafana["GF_SERVER_SERVE_FROM_SUB_PATH"] == "true"
    gateway_networks = services["gateway"]["networks"]
    assert "rag_eval_staging_observability" in gateway_networks


def test_staging_observability_labels_and_private_networks_are_separate():
    config = _staging_config()
    services = config["services"]
    observed_services = (
        "app",
        "worker",
        "causal-mcp",
        "db-bootstrap",
        "monitor",
        "agent-persistence-cleanup",
        "rag-eval-worker",
    )

    for service_name in observed_services:
        labels = services[service_name]["labels"]
        assert labels["causalagent_observability"] == "true"
        assert labels["causalagent_environment"] == "staging"
        assert labels["causalagent_service"] in {"web", "worker", "mcp", "monitor", "maintenance"}

    networks = config["networks"]
    assert networks["rag_eval_staging"]["driver"] == "bridge"
    assert networks["rag_eval_staging_observability"]["internal"] is True
    assert "rag_eval_staging" in services["searxng"]["networks"]
    assert "rag_eval_staging" in services["valkey"]["networks"]
    assert "rag_eval_staging_observability" in services["alloy"]["networks"]
    assert "rag_eval_staging_observability" in services["loki"]["networks"]
    assert "rag_eval_staging_observability" in services["grafana"]["networks"]


def test_search_embedding_storage_and_secret_exclusions_are_configured():
    config = _staging_config()
    services = config["services"]

    for service_name in ("app", "worker"):
        environment = services[service_name]["environment"]
        assert environment["WEB_SEARCH_PROVIDER"] == "searxng"
        assert environment["SEARXNG_URL"] == "http://searxng:8080"
        assert "EMBEDDING_API_KEY" in environment
        assert "EMBEDDING_BASE_URL" in environment
        assert "EMBEDDING_MODEL" in environment

    assert services["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"]
    assert services["searxng"]["environment"]["SEARXNG_SECRET"]
    assert any(
        mount["target"] == "/etc/searxng"
        and Path(mount["source"]) == (REPO_ROOT / "deploy/staging/searxng/config").resolve()
        for mount in services["searxng"]["volumes"]
    )
    assert any(
        mount["target"] == "/var/lib/grafana"
        and mount["type"] == "volume"
        for mount in services["grafana"]["volumes"]
    )
    assert "rag_eval_staging_grafana" in config["volumes"]

    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env.*" in dockerignore

    # 预发 SearXNG 配置不再保存密钥，密钥由 .env.staging 的 SEARXNG_SECRET 注入。
    settings = yaml.safe_load(
        (REPO_ROOT / "deploy/staging/searxng/config/settings.yml").read_text(encoding="utf-8")
    )
    assert "secret_key" not in settings["server"]


def test_staging_matches_development_service_environment_values():
    """预发应用服务的可调参数必须与 .env.example 的取值对应。"""
    staging = _staging_config()["services"]

    expected = {
        "SSE_POLL_INTERVAL_SECONDS": "0.05",
        "JOB_CHAT_HISTORY_LIMIT": "50",
        "JOB_WORKERS": "2",
        "JOB_MAX_ATTEMPTS": "3",
        "MAX_UPLOAD_SIZE_MB": "20",
        "MYSQL_POOL_SIZE_WRITE": "5",
        "MYSQL_POOL_SIZE_READ": "5",
        "MYSQL_QUERY_WARN_MS": "500",
        "CHECKPOINT_POSTGRES_POOL_MAX_SIZE": "5",
        "ADMIN_BATCH_MAX_TARGETS": "20",
        "DB_MONITOR_REALTIME_INTERVAL_SECONDS": "10",
        "RAG_EVAL_EVALUATION_WORKERS": "5",
        "WEB_THREADS": "12",
    }
    for service_name in ("app", "worker"):
        environment = staging[service_name]["environment"]
        for key, value in expected.items():
            assert environment[key] == value, f"{service_name}.{key}"


def test_publish_workflow_is_manual_develop_only_and_does_not_deploy_or_export_images():
    workflow = (REPO_ROOT / ".github/workflows/publish-staging-images.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert 'refs/heads/develop' in workflow
    assert "sha-${{ needs.prepare.outputs.short_sha }}" in workflow
    assert "steps.build.outputs.digest" in workflow
    assert all(
        image in workflow
        for image in (
            "causalagent-app",
            "causalagent-mcp",
            "causalagent-mysql-primary",
            "causalagent-mysql-replica",
        )
    )
    assert "upload-artifact" not in workflow
    assert "docker save" not in workflow
    assert "ssh " not in workflow.lower()
