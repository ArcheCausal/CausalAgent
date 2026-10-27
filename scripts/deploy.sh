#!/usr/bin/env bash
# 从已解压的部署包部署一个发布：校验配置、拉取镜像、启动 Compose、更新 current。
#
# 用法：deploy.sh <部署包目录> <服务器发布目录>
# 服务器不执行 git pull，也不需要完整源码；只使用服务器上的配置与密钥。
set -euo pipefail

bundle_dir="${1:?usage: deploy.sh <bundle-dir> <releases-dir>}"
releases_dir="${2:?usage: deploy.sh <bundle-dir> <releases-dir>}"
config_file="${CAUSALAGENT_CONFIG_FILE:-/etc/causalagent/config/production.env}"
compose_file="${CAUSALAGENT_COMPOSE_FILE:-docker-compose.prod.yml}"
bundle_dir="$(cd "${bundle_dir}" && pwd -P)"
config_file="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${config_file}")"
mkdir -p "${releases_dir}"
releases_dir="$(cd "${releases_dir}" && pwd -P)"

release_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release_id"])' "${bundle_dir}/release.manifest.json")"
if [[ ! "$release_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ ]]; then
  echo "invalid release_id in manifest" >&2
  exit 1
fi
target="${releases_dir}/${release_id}"
mkdir -p "${target}"
cp -a "${bundle_dir}/." "${target}/"

# 把发布清单里的镜像 digest 映射为 Compose 读取的环境变量；部署包本身不含任何密钥。
python3 - "${bundle_dir}/release.manifest.json" "${target}/images.env" <<'PY'
import json, re, sys

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
    "sandbox_processing": "CAUSALAGENT_SANDBOX_IMAGE",
    "opensandbox_server": "OPENSANDBOX_SERVER_IMAGE",
    "opensandbox_execd": "OPENSANDBOX_EXECD_IMAGE",
    "opensandbox_egress": "OPENSANDBOX_EGRESS_IMAGE",
    "opensandbox_interpreter_base": "OPENSANDBOX_INTERPRETER_BASE_IMAGE",
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
    if not isinstance(reference, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", reference) is None:
        raise SystemExit(f"manifest image {key} is missing or not digest-pinned")
    lines.append(f"{env_name}={reference}")
for key, env_name in alias_map.items():
    lines.append(f"{env_name}={images[key]}")
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
PY

# 在切换 current 或启动服务前检查实际 Compose、独立密钥与沙盒配置。
bash "${target}/scripts/verify.sh" "${target}"

cd "${target}"
docker compose --env-file "${config_file}" --env-file "${target}/images.env" -f "${compose_file}" pull
# 临时容器和 sidecar 不属于 Compose 服务，需要预先拉取清单中的这些镜像。
while IFS= read -r reference; do
  docker image pull "${reference}"
done < <(python3 -c 'import json,sys; images=json.load(open(sys.argv[1]))["images"]; [print(images[key]) for key in ("sandbox_processing", "opensandbox_execd", "opensandbox_egress", "opensandbox_interpreter_base")]' release.manifest.json)
docker compose --env-file "${config_file}" --env-file "${target}/images.env" -f "${compose_file}" up -d
ln -sfn "${target}" "${releases_dir}/current"
echo "deployed release ${release_id} to ${target}"
