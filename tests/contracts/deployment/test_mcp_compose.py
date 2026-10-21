"""P2-M causal-mcp Compose boundary checks."""

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_mcp_image_checks_real_entrypoint_after_copying_source() -> None:
    dockerfile = (PROJECT_ROOT / "Agent/CausalAgentMCP/Dockerfile").read_text(
        encoding="utf-8"
    )
    check = next(
        line for line in dockerfile.splitlines()
        if line.startswith('RUN python -c "import Agent.CausalAgentMCP.app;')
    )
    for module in ("langgraph", "langchain", "Agent.deep_agent.", "Agent.deep_agent"):
        assert repr(module) in check
    assert "sys.modules" in check
    assert "assert not forbidden" in check
    for source in ("Agent", "app", "config", "Database", "observability"):
        assert dockerfile.index(f"COPY {source} ./{source}") < dockerfile.index(check)


def test_mcp_publish_build_uses_checked_dockerfile() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/publish-staging-images.yml").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"image: causalagent-mcp\s+context: \.\s+file: Agent/CausalAgentMCP/Dockerfile",
        workflow,
    )
    assert "uses: docker/build-push-action@v6" in workflow
    assert "file: ${{ matrix.file }}" in workflow
    assert "push: true" in workflow


def _service_block(text: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n.*?(?=^  [A-Za-z0-9_-]+:|\Z)",
        text,
    )
    assert match is not None, f"missing service: {service}"
    return match.group(0)


def _assert_mcp_does_not_receive_app_or_model_config(block: str) -> None:
    """MCP 只接收数据库和自身鉴权/算法配置，不接收应用或模型密钥。"""
    for name in ("API_KEY", "BASE_URL", "MODEL", "SECRET_KEY"):
        assert not re.search(rf"(?m)^\s*-?\s*{re.escape(name)}(?:=|:)", block)


def _assert_worker_control_lane_config(block: str) -> None:
    for name in (
        "CAUSAL_MCP_CONTROL_POOL_SIZE",
        "CAUSAL_MCP_CONTROL_MAX_IN_FLIGHT_PER_CLIENT",
        "CAUSAL_MCP_CONTROL_ACQUIRE_TIMEOUT_SECONDS",
        "CAUSAL_MCP_CANCEL_TIMEOUT_SECONDS",
    ):
        assert name in block


def test_development_mcp_is_private_and_worker_waits_for_health() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    mcp = _service_block(compose, "causal-mcp")
    worker = _service_block(compose, "worker")

    assert "dockerfile: Agent/CausalAgentMCP/Dockerfile" in mcp
    assert "./config/secret_file.py:/app/config/secret_file.py:ro" in mcp
    assert "ports:" not in mcp
    assert "healthcheck:" in mcp
    assert "http://127.0.0.1:8080/ready" in mcp
    assert 'causalagent_service: "mcp"' in mcp
    _assert_mcp_does_not_receive_app_or_model_config(mcp)
    assert "causal-mcp:" in worker
    assert "condition: service_healthy" in worker
    _assert_worker_control_lane_config(worker)


def test_staging_and_production_mcp_keep_private_image_boundary() -> None:
    staging = (PROJECT_ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    production = (PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    staging_mcp = _service_block(staging, "causal-mcp")
    production_mcp = _service_block(production, "causal-mcp")
    staging_worker = _service_block(staging, "worker")
    production_worker = _service_block(production, "worker")

    assert "STAGING_MCP_IMAGE" in staging_mcp
    assert "CAUSAL_MCP_IMAGE" in production_mcp
    for block in (staging_mcp, production_mcp):
        assert "./config/secret_file.py:" not in block
        assert "ports:" not in block
        assert "healthcheck:" in block
        assert "http://127.0.0.1:8080/ready" in block
        # MCP 鉴权材料只从密钥文件读取；Compose 只挂载密钥文件并保留签名键 ID。
        assert "source: causalagent_env" in block
        assert "CAUSAL_MCP_SIGNING_KEY_ID" in block
        assert "CAUSAL_MCP_SERVICE_TOKEN=" not in block
        assert "CAUSAL_MCP_SIGNING_KEY_CURRENT=" not in block
        _assert_mcp_does_not_receive_app_or_model_config(block)
    _assert_worker_control_lane_config(staging_worker)
    _assert_worker_control_lane_config(production_worker)
