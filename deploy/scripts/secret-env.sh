#!/bin/sh
# 服务器密钥文件的只读读取器，供官方镜像的启动适配使用。
#
# 文档职责：为 MySQL、PostgreSQL、Grafana 和 SearXNG 等官方镜像提供从同一个密钥文件
# 读取官方入口所需环境变量的统一方法。
#
# 适用范围：容器启动脚本 source 本文件后调用 secret_value / export_secret；应用容器不
# 使用本文件，而是由 config/secret_file.py 直接读取同一个文件。
set -eu

SECRET_FILE="${CAUSALAGENT_SECRET_FILE:-/run/secrets/causalagent.env}"

secret_value() {
    # 从密钥文件取出一个键的值；文件缺失、键缺失或取值为空时返回非零。
    key="$1"
    if [ ! -f "$SECRET_FILE" ]; then
        echo "secret file not found: $SECRET_FILE" >&2
        return 1
    fi
    value="$(sed -n "s/^${key}=//p" "$SECRET_FILE" | head -n 1)"
    if [ -z "$value" ]; then
        echo "secret key missing or empty: $key" >&2
        return 1
    fi
    printf '%s' "$value"
}

export_secret() {
    # 把一个密钥键导出为官方镜像需要的环境变量名。
    key="$1"
    target="$2"
    value="$(secret_value "$key")" || return 1
    export "$target=$value"
}

require_secret_file_permissions() {
    # 除专用 GID 3000 外，拒绝组或其他用户读取密钥文件。
    if [ ! -f "$SECRET_FILE" ]; then
        echo "secret file not found: $SECRET_FILE" >&2
        return 1
    fi
    metadata="$(stat -c '%a:%u:%g' "$SECRET_FILE" 2>/dev/null || stat -f '%Lp:%u:%g' "$SECRET_FILE")"
    mode="${metadata%%:*}"
    owner_and_group="${metadata#*:}"
    owner_id="${owner_and_group%%:*}"
    group_id="${owner_and_group#*:}"
    case "$mode" in
        600|400)
            if [ -r "$SECRET_FILE" ]; then return 0; fi
            ;;
        640)
            if [ "$owner_id" = "0" ] && [ "$group_id" = "3000" ] && [ -r "$SECRET_FILE" ]; then return 0; fi
            ;;
    esac
    echo "insecure or unreadable secret file: $SECRET_FILE must be readable and 600, 400, or 640 owned by root:3000" >&2
    return 1
}
