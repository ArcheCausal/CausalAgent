"""Static deployment contracts; secret ignore verification requires the Git CLI."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/opensandbox"
COMPOSES = ("docker-compose.yml", "docker-compose.replica.yml", "docker-compose.staging.yml", "docker-compose.prod.yml")
CLIENT_KEYS = {
    "CAUSALAGENT_SANDBOX_CONTROLLER_URL",
    "CAUSALAGENT_SANDBOX_CONTROLLER_TIMEOUT_SECONDS",
    "CAUSALAGENT_SANDBOX_MAX_CONCURRENT_PER_WORKER",
    "CAUSALAGENT_SANDBOX_GLOBAL_CAPACITY_WAIT_SECONDS",
}


def _read(filename):
    return yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))


def _environment(service):
    value = service.get("environment", {})
    return dict(item.split("=", 1) for item in value) if isinstance(value, list) else value


@pytest.mark.parametrize("filename", COMPOSES)
def test_worker_uses_shared_secrets_and_controller_client_policy(filename):
    compose = _read(filename)
    worker = compose["services"]["worker"]
    environment = _environment(worker)
    assert {key for key in environment if key.startswith("CAUSALAGENT_SANDBOX_")} == CLIENT_KEYS
    assert environment["CAUSALAGENT_SECRET_FILE"] == "/run/secrets/causalagent.env"
    sources = {secret["source"] for secret in worker["secrets"]}
    assert sources == {"causalagent_env"}
    assert worker["depends_on"]["sandbox-controller"]["condition"] == "service_healthy"
    assert "sandbox_upstream" not in worker["networks"]
    assert all("docker.sock" not in str(mount) for mount in worker.get("volumes", []))


@pytest.mark.parametrize("filename", COMPOSES)
def test_controller_is_single_process_and_waits_for_primary_bootstrap_and_upstream(filename):
    compose = _read(filename)
    controller = compose["services"]["sandbox-controller"]
    shared = _read("deploy/opensandbox/services.yml")["services"]["sandbox-controller"]
    assert controller["extends"] == {"file": "./deploy/opensandbox/services.yml", "service": "sandbox-controller"}
    assert shared["scale"] == 1
    assert shared["command"] == ["python", "-m", "sandbox_controller"]
    assert "8081/healthz" in str(shared["healthcheck"])
    assert shared["environment"]["MYSQL_POOL_SIZE_WRITE"] == "4"
    primary = "mysql" if filename == "docker-compose.prod.yml" else "mysql-primary"
    assert controller["environment"]["MYSQL_WRITE_HOST"] == primary
    assert shared["environment"]["MYSQL_READ_HOSTS"] == ""
    assert shared["environment"]["JOB_STALE_AFTER_SECONDS"] == "${JOB_STALE_AFTER_SECONDS:-120}"
    assert controller["depends_on"][primary]["condition"] == "service_healthy"
    assert controller["depends_on"]["db-bootstrap"]["condition"] == "service_completed_successfully"
    assert controller["depends_on"]["opensandbox"]["condition"] == "service_healthy"
    assert not shared.get("ports") and not controller.get("ports")
    assert all("docker.sock" not in str(mount) for mount in controller.get("volumes", []))
    assert shared["environment"]["CAUSALAGENT_SANDBOX_GLOBAL_CAPACITY"] == "${CAUSALAGENT_SANDBOX_GLOBAL_CAPACITY:-4}"


@pytest.mark.parametrize("filename", COMPOSES)
def test_management_services_share_one_file_and_keep_network_boundaries(filename):
    compose = _read(filename)
    shared = _read("deploy/opensandbox/services.yml")["services"]
    assert set(compose["secrets"]) == {"causalagent_env"}
    for name in ("worker", "sandbox-controller", "opensandbox"):
        definition = shared[name] if name in shared else compose["services"][name]
        assert definition["secrets"] == [{"source": "causalagent_env", "target": "causalagent.env"}]
        assert _environment(definition)["CAUSALAGENT_SECRET_FILE"] == "/run/secrets/causalagent.env"
    assert compose["services"]["opensandbox"]["networks"] == ["sandbox_upstream"]
    assert "sandbox_upstream" in compose["services"]["sandbox-controller"]["networks"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in shared["opensandbox"]["volumes"]
    assert not shared["opensandbox"].get("ports")
    assert shared["opensandbox"]["configs"] == [{"source": "opensandbox_config", "target": "/etc/opensandbox/config.toml"}]
    assert compose["configs"]["opensandbox_config"]["file"] == "${CAUSALAGENT_OPENSANDBOX_CONFIG_SOURCE:-./deploy/opensandbox/config.toml}"
    # The relative mount is resolved from the extends file's directory.
    for mount in shared["opensandbox"]["volumes"]:
        if mount.startswith("."):
            assert mount.endswith(":ro")
            assert (DEPLOY / mount.split(":", 1)[0]).is_file()


def test_release_references_match_verified_bom_and_interpreter_base():
    lock = json.loads((DEPLOY / "release-lock.json").read_text(encoding="utf-8"))
    config = tomllib.loads((DEPLOY / "config.toml").read_text(encoding="utf-8"))
    shared = _read("deploy/opensandbox/services.yml")["services"]
    assert lock["release"] == "release-1.1.1"
    assert shared["opensandbox"]["image"] == lock["images"]["server"]
    assert config["runtime"]["execd_image"] == lock["images"]["execd"]
    assert config["egress"]["image"] == lock["images"]["egress"]
    assert lock["images"]["interpreter_base"] in (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert all(re.fullmatch(r"docker.io/opensandbox/[^@]+@sha256:[0-9a-f]{64}", ref) for ref in lock["images"].values())
    assert (DEPLOY / "requirements.lock").is_file()


def test_compose_proxy_routing_and_sandbox_limits_are_explicit():
    config = tomllib.loads((DEPLOY / "config.toml").read_text(encoding="utf-8"))
    upstream = _read("deploy/opensandbox/services.yml")["services"]["opensandbox"]
    assert config["proxy"]["resolve_internal"] is False
    assert config["docker"]["network_mode"] == "bridge"
    assert config["docker"]["host_ip"] == "host.docker.internal"
    assert config["docker"]["publish_host"] == "172.17.0.1"
    assert "host.docker.internal:host-gateway" in upstream["extra_hosts"]
    assert config["egress"]["mode"] == "dns+nft"
    assert config["egress"]["disable_ipv6"] is True
    assert config["storage"]["allowed_host_paths"] == []
    assert config["docker"]["sandbox_binds"] == []
    assert config["renew_intent"]["enabled"] is False
    assert "api_key" not in config["server"]
    assert set(upstream["environment"]) == {"SANDBOX_CONFIG_PATH", "CAUSALAGENT_SECRET_FILE"}


def test_bare_secrets_are_excluded_from_git_and_docker_build_context():
    if shutil.which("git") is None:
        pytest.skip("Git CLI is required to verify real secret ignore rules")
    paths = ["sandbox-controller.token", "private/auth.token", "private/opensandbox-api-key", "private/sandbox-controller.env", "deploy/secrets/another-secret"]
    result = subprocess.run(["git", "check-ignore", "--no-index", *paths], cwd=ROOT, text=True, capture_output=True, check=True)
    assert set(result.stdout.splitlines()) == set(paths)
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {"*.token", "**/*.token", "**/opensandbox-api-key", "**/sandbox-controller.env", "deploy/secrets/"} <= set(dockerignore)
    for filename in (".env.example", ".env.staging.example", "deploy/config/production.env.example"):
        lines = (ROOT / filename).read_text(encoding="utf-8").splitlines()
        assert "CAUSALAGENT_SANDBOX_IMAGE=" in lines
        assert not any(line.startswith("CAUSALAGENT_SANDBOX_API_KEY=") for line in lines)


def test_single_secret_template_contains_both_sandbox_authentication_keys():
    text = (ROOT / "deploy/secrets/causalagent.env.example").read_text(encoding="utf-8")
    names = {line.split("=", 1)[0] for line in text.splitlines() if line and not line.startswith("#")}
    assert {"MYSQL_PASSWORD", "CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN", "CAUSALAGENT_SANDBOX_API_KEY"} <= names


@pytest.mark.parametrize("filename", COMPOSES)
def test_resolved_compose_mounts_shared_loader_and_secret_file(filename):
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is required to resolve extends paths")
    template = ".env.staging.example" if filename == "docker-compose.staging.yml" else "deploy/config/production.env.example"
    environment = {
        **os.environ,
        "CAUSALAGENT_SECRET_FILE": str(ROOT / "tests/unit-test-secrets.env"),
        **{key: "example/offline-compose@sha256:" + "a" * 64 for key in ("APP_IMAGE", "CAUSAL_MCP_IMAGE", "MYSQL_IMAGE")},
    }
    parsed = subprocess.run(
        ["docker", "compose", "--env-file", str(ROOT / template), "-f", str(ROOT / filename), "config", "--format", "json"],
        cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30,
    )
    assert parsed.returncode == 0, "Docker Compose could not resolve shared secret mounts"
    compose = json.loads(parsed.stdout)
    for name in ("worker", "sandbox-controller", "opensandbox"):
        service = compose["services"][name]
        assert service["secrets"] == [{"source": "causalagent_env", "target": "causalagent.env"}]
        assert service["environment"]["CAUSALAGENT_SECRET_FILE"] == "/run/secrets/causalagent.env"
    upstream = compose["services"]["opensandbox"]
    for target, source in {
        "/opt/causalagent/server_entrypoint.py": ROOT / "deploy/opensandbox/server_entrypoint.py",
        "/opt/causalagent/config/secret_file.py": ROOT / "config/secret_file.py",
    }.items():
        mounts = [mount for mount in upstream["volumes"] if mount["target"] == target]
        assert len(mounts) == 1
        assert mounts[0]["read_only"] is True
        assert Path(mounts[0]["source"]) == source.resolve()
