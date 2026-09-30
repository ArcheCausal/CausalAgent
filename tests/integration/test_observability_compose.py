"""第一阶段 1.3 开发可观测拓扑的静态契约。"""

from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = PROJECT_ROOT / "docker-compose.yml"


def _service_block(compose_text: str, service_name: str) -> str:
    pattern = rf"(?ms)^  {re.escape(service_name)}:\n.*?(?=^  [A-Za-z0-9_-]+:|\Z)"
    match = re.search(pattern, compose_text)
    assert match is not None, f"missing compose service: {service_name}"
    return match.group(0)


def test_default_compose_pins_observability_images_and_resource_caps():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "image: grafana/grafana:13.1.1" in _service_block(compose, "grafana")
    assert "image: grafana/loki:3.7.4" in _service_block(compose, "loki")
    assert "image: grafana/alloy:v1.18.0" in _service_block(compose, "alloy")
    assert 'cpus: "1.0"' in _service_block(compose, "loki")
    assert "mem_limit: 1g" in _service_block(compose, "loki")
    assert 'cpus: "0.5"' in _service_block(compose, "grafana")
    assert "mem_limit: 512m" in _service_block(compose, "grafana")
    assert 'cpus: "0.5"' in _service_block(compose, "alloy")
    assert "mem_limit: 256m" in _service_block(compose, "alloy")


def test_observability_network_ports_and_password_boundary():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    grafana = _service_block(compose, "grafana")
    loki = _service_block(compose, "loki")
    alloy = _service_block(compose, "alloy")

    assert "observability_network" in compose
    assert '"127.0.0.1:3000:3000"' in grafana
    assert "GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD must be set" in grafana
    assert "GF_USERS_DEFAULT_LANGUAGE: zh-Hans" in grafana
    # Grafana 在开发环境同样以 /grafana/ 为子路径，浏览器访问 /grafana/ 入口。
    assert "GF_SERVER_ROOT_URL: /grafana/" in grafana
    assert 'GF_SERVER_SERVE_FROM_SUB_PATH: "true"' in grafana
    assert "ports:" not in loki
    assert "ports:" not in alloy
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in alloy
    assert "alloy_positions:/var/lib/alloy/data" in alloy
    assert "healthcheck:" not in loki
    assert "condition: service_started" in alloy
    assert "healthcheck:" not in alloy
    assert "condition: service_started" in grafana
    assert "healthcheck:" not in grafana


def test_only_intended_development_containers_are_labelled_for_collection():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    intended = {
        "app",
        "worker",
        "causal-mcp",
        "db-bootstrap",
        "agent-persistence-cleanup",
        "monitor",
    }

    for service_name in intended:
        block = _service_block(compose, service_name)
        assert "causalagent_observability: \"true\"" in block
        assert "causalagent_service:" in block
        assert "causalagent_environment: \"development\"" in block

    for service_name in {"mysql-primary", "mysql-replica", "postgres-checkpoint"}:
        assert "causalagent_observability:" not in _service_block(compose, service_name)

    assert compose.count("causalagent_observability: \"true\"") == len(intended)


def test_production_compose_is_not_changed_to_observability_topology():
    production = (PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "grafana/grafana:" not in production
    assert "grafana/loki:" not in production
    assert "grafana/alloy:" not in production
    assert "observability_network" not in production


def test_alloy_pipeline_parses_docker_and_retains_legacy_lines_without_dynamic_labels():
    alloy = (PROJECT_ROOT / "observability/alloy/config.alloy").read_text(encoding="utf-8")

    assert 'host             = "unix:///var/run/docker.sock"' in alloy
    assert 'action        = "keep"' in alloy
    assert "stage.docker {}" in alloy
    assert "drop_malformed = false" in alloy
    assert 'values = ["service_name", "environment", "level", "category"]' in alloy
    assert "loki.write.causalagent.receiver" in alloy


def test_loki_and_grafana_provisioning_match_first_phase_contract():
    loki = (PROJECT_ROOT / "observability/loki/loki-config.yml").read_text(encoding="utf-8")
    datasource = (
        PROJECT_ROOT / "observability/grafana/provisioning/datasources/loki.yml"
    ).read_text(encoding="utf-8")
    dashboards = (
        PROJECT_ROOT / "observability/grafana/provisioning/dashboards/dashboards.yml"
    ).read_text(encoding="utf-8")
    dashboard_path = PROJECT_ROOT / "observability/grafana/dashboards/causalagent-logs.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    for expected in (
        "store: tsdb",
        "object_store: filesystem",
        "retention_period: 72h",
        "retention_enabled: true",
        "ingestion_rate_mb: 4",
        "ingestion_burst_size_mb: 8",
        "max_entries_limit_per_query: 5000",
        "query_timeout: 30s",
        "pattern_ingester:",
        "allow_structured_metadata: true",
        "volume_enabled: true",
        "discover_log_levels: true",
        "discover_service_name:",
    ):
        assert expected in loki

    assert "uid: causalagent-loki" in datasource
    assert "url: http://loki:3100" in datasource
    assert "/var/lib/grafana/dashboards" in dashboards
    assert dashboard["uid"] == "causalagent-logs"
    assert dashboard["title"] == "CausalAgent 异常日志"
    assert {panel["title"] for panel in dashboard["panels"]} == {
        "异常总量",
        "异常级别趋势",
        "按服务分布",
        "按分类分布",
        "Top 10 事件码",
        "最近异常日志",
    }


def test_grafana_dashboard_only_surfaces_warning_and_above():
    dashboard_path = PROJECT_ROOT / "observability/grafana/dashboards/causalagent-logs.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "causalagent-logs"
    assert dashboard["refresh"] == "30s"
    assert dashboard["time"] == {"from": "now-1h", "to": "now"}

    variables = {variable["name"]: variable for variable in dashboard["templating"]["list"]}
    assert set(variables) == {"environment", "service_name", "category", "level"}
    assert variables["level"]["type"] == "custom"
    assert variables["level"]["multi"] is True
    assert variables["level"]["includeAll"] is True
    assert variables["level"]["allValue"] == "warning|error|critical"
    assert set(variables["level"]["query"].split(",")) == {
        "warning",
        "error",
        "critical",
    }
    for variable_name in ("environment", "service_name", "category"):
        assert variables[variable_name]["datasource"] == {
            "type": "loki",
            "uid": "causalagent-loki",
        }
        assert variables[variable_name]["allValue"] == ".+"

    targets = [target for panel in dashboard["panels"] for target in panel["targets"]]
    expressions = [target["expr"] for target in targets]
    assert expressions
    assert all('level=~"${level:regex}"' in expression for expression in expressions)
    assert not any(re.search(r"\b(?:debug|info)\b", expression) for expression in expressions)

    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    event_code_expression = panels["Top 10 事件码"]["targets"][0]["expr"]
    assert "| json" in event_code_expression
    assert "sum by (event_code)" in event_code_expression
    assert panels["最近异常日志"]["targets"][0]["maxLines"] == 200

    forbidden_variables = {
        "event_code",
        "request_id",
        "user_id",
        "session_id",
        "job_id",
        "invocation_id",
        "worker_slot",
        "node",
        "tool",
        "instance",
    }
    assert forbidden_variables.isdisjoint(variables)


def test_mcp_job_timeline_uses_json_fields_without_high_cardinality_labels():
    dashboard_path = (
        PROJECT_ROOT
        / "observability/grafana/dashboards/causalagent-mcp-jobs.json"
    )
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "causalagent-mcp-jobs"
    assert dashboard["title"] == "CausalAgent MCP Job 时间线"
    assert dashboard["refresh"] == "10s"
    assert dashboard["time"] == {"from": "now-1h", "to": "now"}
    assert {panel["title"] for panel in dashboard["panels"]} == {
        "慢调用数量",
        "失败与取消",
        "MCP 生命周期趋势",
        "MCP Job 时间线",
    }

    variables = {item["name"]: item for item in dashboard["templating"]["list"]}
    assert set(variables) == {"environment", "job_id", "tool"}
    assert variables["environment"]["type"] == "query"
    assert variables["job_id"]["type"] == "textbox"
    assert variables["tool"]["type"] == "textbox"

    targets = [target for panel in dashboard["panels"] for target in panel["targets"]]
    expressions = [target["expr"] for target in targets]
    assert all('{service_name=~"worker|mcp"' in expression for expression in expressions)
    assert all("| json" in expression for expression in expressions)
    assert all('job_id=~"${job_id:regex}"' in expression for expression in expressions)
    assert all('tool=~"${tool:regex}"' in expression for expression in expressions)
    assert not any("job_id=" in expression.split("}", 1)[0] for expression in expressions)
    assert not any("tool=" in expression.split("}", 1)[0] for expression in expressions)

    timeline = next(
        panel for panel in dashboard["panels"] if panel["title"] == "MCP Job 时间线"
    )
    assert timeline["options"]["sortOrder"] == "Ascending"
    assert timeline["targets"][0]["maxLines"] == 500
