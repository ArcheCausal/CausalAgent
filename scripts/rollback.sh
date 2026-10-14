#!/usr/bin/env bash
# 回滚到上一个发布：把 current 指回 releases 下的上一个目录并重启 Compose。
set -euo pipefail

releases_dir="${1:?usage: rollback.sh <releases-dir>}"
releases_dir="$(cd "${releases_dir}" && pwd -P)"
config_file="${CAUSALAGENT_CONFIG_FILE:-/etc/causalagent/config/production.env}"
compose_file="${CAUSALAGENT_COMPOSE_FILE:-docker-compose.prod.yml}"

current="$(readlink -f "${releases_dir}/current")"
previous=""
for candidate in "${releases_dir}"/*/; do
  [[ -d "${candidate}" ]] || continue
  candidate_target="$(readlink -f "${candidate}")"
  [[ "${candidate_target}" == "${current}" ]] && continue
  if [[ -z "${previous}" || "${candidate}" -nt "${previous}" ]]; then
    previous="${candidate}"
  fi
done
if [ -z "${previous}" ]; then
  echo "no previous release to roll back to" >&2
  exit 1
fi

ln -sfn "${previous%/}" "${releases_dir}/current"
cd "${previous}"
docker compose --env-file "${config_file}" --env-file "${previous%/}/images.env" -f "${compose_file}" up -d
echo "rolled back to ${previous}"
