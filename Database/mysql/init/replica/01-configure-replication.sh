#!/bin/bash
set -eu

# 复制账号密码只来自服务器上的单个密钥文件。
. /opt/causalagent/secret-env.sh

REPLICATION_USER="${MYSQL_REPLICATION_USER:-replica}"
REPLICATION_PASSWORD="$(secret_value MYSQL_REPLICATION_PASSWORD)"

until mysqladmin ping -h mysql-primary -uroot -p"${MYSQL_ROOT_PASSWORD}" --silent; do
  echo "waiting for mysql-primary to become available..."
  sleep 2
done

mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
SET GLOBAL super_read_only = OFF;
SET GLOBAL read_only = OFF;
STOP REPLICA;
CHANGE REPLICATION SOURCE TO
  SOURCE_HOST='mysql-primary',
  SOURCE_USER='${REPLICATION_USER}',
  SOURCE_PASSWORD='${REPLICATION_PASSWORD}',
  SOURCE_AUTO_POSITION=1,
  GET_SOURCE_PUBLIC_KEY=1;
START REPLICA;
SET PERSIST read_only = ON;
SET PERSIST super_read_only = ON;
SQL
