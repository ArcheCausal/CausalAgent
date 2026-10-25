"""密钥文件与非 root 官方镜像之间的 Compose 权限合同。"""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]


def _service_environment_names(service: dict) -> set[str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return set(environment)
    return {str(item).split("=", 1)[0] for item in environment}


def test_replica_compat_compose_reads_database_passwords_from_the_secret_file() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.replica.yml").read_text(encoding="utf-8"))

    for service_name in ("mysql-primary", "mysql-replica"):
        service = compose["services"][service_name]
        healthcheck = " ".join(service["healthcheck"]["test"])
        assert "secret_value MYSQL_ROOT_PASSWORD" in healthcheck
        assert "MYSQL_ROOT_PASSWORD" not in _service_environment_names(service)

    postgres = compose["services"]["postgres-checkpoint"]
    assert "POSTGRES_PASSWORD" not in _service_environment_names(postgres)
    assert "export_secret CHECKPOINT_POSTGRES_PASSWORD POSTGRES_PASSWORD" in str(postgres["command"])


def test_non_root_secret_readers_join_the_dedicated_secret_group() -> None:
    for filename in ("docker-compose.yml", "docker-compose.staging.yml"):
        compose = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
        for service_name in ("searxng", "grafana"):
            service = compose["services"][service_name]
            assert "3000" in {str(group) for group in service.get("group_add", [])}
            assert "require_secret_file_permissions" in " ".join(service["entrypoint"])


def test_database_secret_readers_join_the_dedicated_secret_group() -> None:
    database_services = {
        "docker-compose.yml": ("mysql-primary", "mysql-replica", "postgres-checkpoint"),
        "docker-compose.staging.yml": ("mysql-primary", "mysql-replica", "postgres-checkpoint"),
        "docker-compose.prod.yml": ("mysql", "postgres-checkpoint"),
        "docker-compose.replica.yml": ("mysql-primary", "mysql-replica", "postgres-checkpoint"),
    }

    for filename, service_names in database_services.items():
        compose = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
        for service_name in service_names:
            service = compose["services"][service_name]
            assert "3000" in {str(group) for group in service.get("group_add", [])}


def test_only_development_compose_relaxes_secret_permissions() -> None:
    for filename in ("docker-compose.yml", "docker-compose.replica.yml"):
        compose = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
        assert "dev-secrets-init" not in compose["services"]
        assert "dev_secrets" not in compose["volumes"]
        for service_name, service in compose["services"].items():
            if not service.get("secrets"):
                continue
            environment = service["environment"]
            if isinstance(environment, list):
                environment = dict(item.split("=", 1) for item in environment)
            assert environment["ENVIRONMENT"] == "development", service_name
            expected = [
                {"source": "causalagent_env", "target": "causalagent.env"}
            ]
            if service_name == "worker":
                expected.insert(0, {"source": "sandbox_controller_token", "target": "sandbox-controller.token"})
            assert service["secrets"] == expected
    for filename in ("docker-compose.staging.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
        for service_name, service in compose["services"].items():
            environment = service.get("environment", {})
            if isinstance(environment, list):
                environment = dict(item.split("=", 1) for item in environment)
            assert environment.get("ENVIRONMENT") in (None, "staging", "production"), service_name


def test_production_compose_is_parsed_with_manifest_generated_images() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    deploy_script = (REPO_ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")

    assert compose["services"]
    assert all(isinstance(service, dict) for service in compose["services"].values())
    assert "release.manifest.json" in deploy_script
    assert "images.env" in deploy_script
    assert "--env-file" in deploy_script
