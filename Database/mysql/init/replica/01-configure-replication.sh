#!/bin/bash
set -eu

if [ -z "${MYSQL_REPLICATION_PASSWORD:-}" ]; then
  echo "MYSQL_REPLICATION_PASSWORD is required for replica replication setup" >&2
  exit 1
fi

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
  SOURCE_USER='${MYSQL_REPLICATION_USER:-replica}',
  SOURCE_PASSWORD='${MYSQL_REPLICATION_PASSWORD}',
  SOURCE_AUTO_POSITION=1,
  GET_SOURCE_PUBLIC_KEY=1;
START REPLICA;
SET PERSIST read_only = ON;
SET PERSIST super_read_only = ON;
SQL
