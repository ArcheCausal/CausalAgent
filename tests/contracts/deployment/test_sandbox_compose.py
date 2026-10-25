"""Static deployment contracts; secret ignore verification requires the Git CLI."""

import json
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
    "CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN_FILE",
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
def test_worker_receives_only_controller_credentials_and_client_policy(filename):
    compose = _read(filename)
    worker = compose["services"]["worker"]
    environment = _environment(worker)
    assert {key for key in environment if key.startswith("CAUSALAGENT_SANDBOX_")} == CLIENT_KEYS
    assert environment["CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN_FILE"] == "/run/secrets/sandbox-controller.token"
    sources = {secret["source"] for secret in worker["secrets"]}
    assert sources == {"causalagent_env", "sandbox_controller_token"}
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
def test_upstream_token_and_docker_access_stay_out_of_worker(filename):
    compose = _read(filename)
    shared = _read("deploy/opensandbox/services.yml")["services"]
    consumers = {"opensandbox_api_key": set(), "sandbox_controller_env": set(), "sandbox_controller_token": set()}
    for name, service in compose["services"].items():
        definition = shared[name] if name in shared else service
        for secret in definition.get("secrets", []):
            if secret["source"] in consumers:
                consumers[secret["source"]].add(name)
    assert consumers == {
        "opensandbox_api_key": {"sandbox-controller", "opensandbox"},
        "sandbox_controller_env": {"sandbox-controller"},
        "sandbox_controller_token": {"worker", "sandbox-controller"},
    }
    assert compose["services"]["opensandbox"]["networks"] == ["sandbox_upstream"]
    assert "sandbox_upstream" in compose["services"]["sandbox-controller"]["networks"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in shared["opensandbox"]["volumes"]
    assert not shared["opensandbox"].get("ports")
    assert shared["opensandbox"]["configs"] == [{"source": "opensandbox_config", "target": "/etc/opensandbox/config.toml"}]
    assert compose["configs"]["opensandbox_config"]["file"] == "${CAUSALAGENT_OPENSANDBOX_CONFIG_SOURCE:-./deploy/opensandbox/config.toml}"
    # The relative mount is resolved from the extends file's directory.
    entrypoint = shared["opensandbox"]["volumes"][-1].split(":", 1)[0]
    assert (DEPLOY / entrypoint).is_file()


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
    assert set(upstream["environment"]) == {"SANDBOX_CONFIG_PATH", "CAUSALAGENT_SANDBOX_API_KEY_FILE"}


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


def test_controller_database_secret_template_contains_no_application_secrets():
    names = {line.split("=", 1)[0] for line in (DEPLOY / "controller.env.example").read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}
    assert names == {"MYSQL_PASSWORD", "MYSQL_WRITE_PASSWORD", "MYSQL_READ_PASSWORD"}
