#!/usr/bin/env bash
# 从已解压的部署包部署一个发布：更新 current、拉取清单镜像、启动 Compose。
#
# 用法：deploy.sh <部署包目录> <服务器发布目录>
# 服务器不执行 git pull，也不需要完整源码；只使用服务器上的配置与密钥。
set -euo pipefail

bundle_dir="${1:?usage: deploy.sh <bundle-dir> <releases-dir>}"
releases_dir="${2:?usage: deploy.sh <bundle-dir> <releases-dir>}"
config_file="${CAUSALAGENT_CONFIG_FILE:-/etc/causalagent/config/production.env}"
compose_file="${CAUSALAGENT_COMPOSE_FILE:-docker-compose.prod.yml}"

release_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release_id"])' "${bundle_dir}/release.manifest.json")"
target="${releases_dir}/${release_id}"
mkdir -p "${target}"
cp -a "${bundle_dir}/." "${target}/"

# 把发布清单里的镜像 digest 映射为 Compose 读取的环境变量；部署包本身不含任何密钥。
python3 - "${bundle_dir}/release.manifest.json" "${target}/images.env" <<'PY'
import json, sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
images = manifest["images"]
mapping = {
    "app": "APP_IMAGE",
    "mcp": "CAUSAL_MCP_IMAGE",
    "mysql_primary": "MYSQL_IMAGE",
    "mysql_replica": "MYSQL_REPLICA_IMAGE",
    "postgres": "POSTGRES_IMAGE",
    "nginx": "NGINX_IMAGE",
    "searxng": "SEARXNG_IMAGE",
    "valkey": "VALKEY_IMAGE",
    "loki": "LOKI_IMAGE",
    "alloy": "ALLOY_IMAGE",
    "grafana": "GRAFANA_IMAGE",
}
# 预发 Compose 使用 STAGING_* 变量名，这里同步导出，保证同一个发布清单两套拓扑都能用。
alias_map = {
    "app": "STAGING_APP_IMAGE",
    "mcp": "STAGING_MCP_IMAGE",
    "mysql_primary": "STAGING_MYSQL_PRIMARY_IMAGE",
    "mysql_replica": "STAGING_MYSQL_REPLICA_IMAGE",
}
lines = []
for key, env_name in mapping.items():
    reference = images.get(key)
    if not reference or "@sha256:" not in reference:
        raise SystemExit(f"manifest image {key} is missing or not digest-pinned")
    lines.append(f"{env_name}={reference}")
for key, env_name in alias_map.items():
    lines.append(f"{env_name}={images[key]}")
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
PY

ln -sfn "${target}" "${releases_dir}/current"

cd "${target}"
docker compose --env-file "${config_file}" --env-file "${target}/images.env" -f "${compose_file}" pull
docker compose --env-file "${config_file}" --env-file "${target}/images.env" -f "${compose_file}" up -d
echo "deployed release ${release_id} to ${target}"
