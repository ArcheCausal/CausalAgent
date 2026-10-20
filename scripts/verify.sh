#!/usr/bin/env bash
# 静态校验发布清单、镜像 digest 引用、密钥权限与必需键名，以及 Compose 配置。
set -euo pipefail

release_dir="${1:?usage: verify.sh <release-dir>}"
config_file="${CAUSALAGENT_CONFIG_FILE:-/etc/causalagent/config/production.env}"
secret_file="${CAUSALAGENT_SECRET_FILE:-/etc/causalagent/secrets/causalagent.env}"

test -f "${release_dir}/release.manifest.json"
python3 - "${release_dir}/release.manifest.json" "${secret_file}" <<'PY'
import json, os, stat, sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
secret_file = sys.argv[2]

for name, reference in manifest["images"].items():
    if "@sha256:" not in reference:
        raise SystemExit(f"image {name} is not digest-pinned: {reference}")

secret_stat = os.stat(secret_file)
mode = stat.S_IMODE(secret_stat.st_mode)
if mode != 0o640 or secret_stat.st_uid != 0 or secret_stat.st_gid != 3000:
    raise SystemExit("secret file must be mode 0640 and owned by root:GID 3000")

required = manifest["secrets"]["required_keys"]
present = set()
for line in open(secret_file, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    present.add(line.split("=", 1)[0])
missing = [key for key in required if key not in present]
if missing:
    raise SystemExit(f"secret file missing keys: {missing}")
print("release manifest and secret file verified")
PY

docker compose --env-file "${config_file}" --env-file "${release_dir}/images.env" config --quiet
echo "deployment verified"
