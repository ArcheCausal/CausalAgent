#!/usr/bin/env bash
# 校验发布清单、实际 Compose 镜像、沙盒配置与统一密钥文件；不启动服务。
set -euo pipefail

release_dir="${1:?usage: verify.sh <release-dir>}"
config_file="${CAUSALAGENT_CONFIG_FILE:-/etc/causalagent/config/production.env}"
compose_file="${CAUSALAGENT_COMPOSE_FILE:-docker-compose.prod.yml}"

# 此脚本在服务器宿主运行；应用镜像的 Python 版本不能满足宿主依赖。
python3 -c 'import sys; sys.version_info >= (3, 11) or sys.exit("verify.sh requires Python 3.11 or newer on the deployment host; found %s" % sys.version.split()[0])'

python3 - "${release_dir}" "${config_file}" "${compose_file}" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib


release_dir = Path(sys.argv[1]).resolve()
config_file = Path(sys.argv[2]).resolve()
compose_name = sys.argv[3]
if compose_name not in {"docker-compose.prod.yml", "docker-compose.staging.yml"}:
    raise SystemExit("unsupported release Compose file")
manifest = json.loads((release_dir / "release.manifest.json").read_text(encoding="utf-8"))
images = manifest["images"]
mapping = {
    "app": ["APP_IMAGE", "STAGING_APP_IMAGE"],
    "mcp": ["CAUSAL_MCP_IMAGE", "STAGING_MCP_IMAGE"],
    "mysql_primary": ["MYSQL_IMAGE", "STAGING_MYSQL_PRIMARY_IMAGE"],
    "mysql_replica": ["MYSQL_REPLICA_IMAGE", "STAGING_MYSQL_REPLICA_IMAGE"],
    "postgres": ["POSTGRES_IMAGE"], "nginx": ["NGINX_IMAGE"],
    "searxng": ["SEARXNG_IMAGE"], "valkey": ["VALKEY_IMAGE"],
    "loki": ["LOKI_IMAGE"], "alloy": ["ALLOY_IMAGE"], "grafana": ["GRAFANA_IMAGE"],
    "sandbox_processing": ["CAUSALAGENT_SANDBOX_IMAGE"],
    "opensandbox_server": ["OPENSANDBOX_SERVER_IMAGE"],
    "opensandbox_execd": ["OPENSANDBOX_EXECD_IMAGE"],
    "opensandbox_egress": ["OPENSANDBOX_EGRESS_IMAGE"],
    "opensandbox_interpreter_base": ["OPENSANDBOX_INTERPRETER_BASE_IMAGE"],
}
if set(images) != set(mapping):
    raise SystemExit("release image inventory is incomplete or contains unknown images")
for name, reference in images.items():
    if not isinstance(reference, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", reference) is None:
        raise SystemExit(f"image {name} is not digest-pinned")
if json.loads((release_dir / "images.lock.json").read_text(encoding="utf-8")) != images:
    raise SystemExit("image lock differs from release manifest")

for section, required in (
    ("compose_files_sha256", {"docker-compose.prod.yml", "docker-compose.staging.yml"}),
    ("sandbox_files_sha256", {
        "deploy/opensandbox/release-lock.json", "deploy/opensandbox/services.yml",
        "deploy/opensandbox/server_entrypoint.py", "deploy/opensandbox/config.toml",
        "config/secret_file.py",
    }),
):
    expected = manifest["deployment"][section]
    if set(expected) != required:
        raise SystemExit(f"missing required deployment files in {section}")
    for name, digest in expected.items():
        path = release_dir / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"deployment file is missing or changed: {name}")

lock = json.loads((release_dir / "deploy/opensandbox/release-lock.json").read_text(encoding="utf-8"))
for key in ("server", "execd", "egress", "interpreter_base"):
    if lock["images"][key] != images["opensandbox_" + key]:
        raise SystemExit(f"OpenSandbox release lock differs for {key}")
if images["sandbox_processing"] in lock["images"].values():
    raise SystemExit("processing image must be the published application image, not an upstream base")


def read_secrets(path):
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o640 or info.st_uid != 0 or info.st_gid != 3000:
        raise SystemExit(f"secret file must be mode 0640 and owned by root:GID 3000: {path}")
    try:
        return load_secrets(path=path, required_keys=REQUIRED_SECRET_KEYS)
    except SecretFileError as exc:
        raise SystemExit(str(exc)) from None


# 共享加载器已经通过上方发布文件摘要检查，不依赖应用或模型运行时。
sys.path.insert(0, str(release_dir))
from config.secret_file import REQUIRED_SECRET_KEYS, SECRET_KEYS, SECRET_SCHEMA_VERSION, SecretFileError, load_secrets

if manifest["secrets"] != {"schema_version": SECRET_SCHEMA_VERSION,
                           "required_keys": list(REQUIRED_SECRET_KEYS), "allowed_keys": list(SECRET_KEYS)}:
    raise SystemExit("release secret schema differs from the shared loader")
# Compose 自己解析 env_file、extends、变量优先级和路径；不在脚本中复制其插值语义。
command = ["docker", "compose", "--project-directory", str(release_dir),
           "--env-file", str(config_file), "--env-file", str(release_dir / "images.env"),
           "-f", str(release_dir / compose_name), "config", "--format", "json"]
parsed = subprocess.run(command, cwd=release_dir, capture_output=True, text=True, timeout=60)
if parsed.returncode:
    raise SystemExit("release Compose configuration could not be resolved")
compose = json.loads(parsed.stdout)
secret_file = Path(compose["secrets"]["causalagent_env"]["file"]).resolve()
read_secrets(secret_file)
services = compose["services"]
for name in ("sandbox-controller", "opensandbox", "worker"):
    if name not in services:
        raise SystemExit(f"required sandbox service is missing: {name}")
service_images = {
    "app": "app", "worker": "app", "sandbox-controller": "app",
    "db-bootstrap": "app", "monitor": "app", "rag-eval-worker": "app",
    "agent-persistence-cleanup": "app", "causal-mcp": "mcp", "cdfm-weights-seed": "mcp",
    "mysql": "mysql_primary", "mysql-primary": "mysql_primary", "mysql-replica": "mysql_replica",
    "postgres-checkpoint": "postgres", "gateway": "nginx", "staging-gateway": "nginx",
    "nginx": "nginx", "searxng": "searxng", "searxng-init": "searxng",
    "valkey": "valkey", "loki": "loki", "alloy": "alloy", "grafana": "grafana",
    "opensandbox": "opensandbox_server",
}
for name, service in services.items():
    key = service_images.get(name)
    if key is None or service.get("image") != images[key]:
        raise SystemExit(f"Compose image differs from release manifest: {name}")
if services["sandbox-controller"]["environment"].get("CAUSALAGENT_SANDBOX_IMAGE") != images["sandbox_processing"]:
    raise SystemExit("Compose processing image differs from release manifest")

if set(compose["secrets"]) != {"causalagent_env"}:
    raise SystemExit("deployment must use one shared secret file")
for name in ("worker", "sandbox-controller", "opensandbox"):
    service = services[name]
    if service.get("secrets") != [{"source": "causalagent_env", "target": "causalagent.env"}]:
        raise SystemExit(f"shared secret mount mismatch: {name}")
    if service["environment"].get("CAUSALAGENT_SECRET_FILE") != "/run/secrets/causalagent.env":
        raise SystemExit(f"shared secret path mismatch: {name}")

config_path = Path(compose["configs"]["opensandbox_config"]["file"])
with config_path.open("rb") as handle:
    config = tomllib.load(handle)
if config["runtime"]["execd_image"] != images["opensandbox_execd"] or config["egress"]["image"] != images["opensandbox_egress"]:
    raise SystemExit("OpenSandbox runtime images differ from release manifest")
if config["runtime"].get("type") != "docker" or config["docker"].get("network_mode") != "bridge":
    raise SystemExit("OpenSandbox configuration must use the single-host Docker bridge runtime")
if (config["storage"].get("allowed_host_paths") != [] or config["docker"].get("sandbox_binds") != []
        or config["egress"].get("disable_ipv6") is not True):
    raise SystemExit("OpenSandbox configuration does not enforce the required isolation")
if config["renew_intent"].get("enabled") is not False:
    raise SystemExit("OpenSandbox renewal must be disabled")
print("release images, sandbox configuration and shared secret file verified")
PY

echo "deployment verified"
