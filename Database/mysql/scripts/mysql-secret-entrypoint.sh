#!/bin/sh
# MySQL 官方入口的密钥适配层。
#
# 官方 mysql 镜像要求通过环境变量提供 root 密码；本项目把密码集中保存在服务器上的
# 单个密钥文件里，因此这里在入口脚本之前读取密钥文件并导出官方变量，容器本身仍然
# 只认识官方的环境变量名。
set -eu

. /opt/causalagent/secret-env.sh

require_secret_file_permissions
export_secret MYSQL_ROOT_PASSWORD MYSQL_ROOT_PASSWORD

exec docker-entrypoint.sh "$@"
