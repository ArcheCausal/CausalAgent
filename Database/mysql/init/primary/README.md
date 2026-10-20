# primary 初始化脚本

文档职责：说明主库首次初始化时的账号、密码来源和授权范围。

适用范围：使用项目 MySQL primary 镜像初始化全新数据目录；已有数据卷不会重新执行初始化脚本。

`01-create-replication-user.sh` 会在 MySQL 数据目录第一次初始化时自动执行；账号名来自环境变量，密码由 `/opt/causalagent/secret-env.sh` 从 `/run/secrets/causalagent.env` 读取：

- 写账号名使用 `MYSQL_WRITE_USER`，缺省时使用 `MYSQL_USER`；密码必须来自文件中的 `MYSQL_WRITE_PASSWORD`
- 读账号名使用 `MYSQL_READ_USER`，缺省时使用 `MYSQL_USER`；密码必须来自文件中的 `MYSQL_READ_PASSWORD`
- 专用写、读密码缺失或为空时脚本失败，均不回退到 `MYSQL_PASSWORD`
- 读取 `MYSQL_REPLICA_STATUS_USER` / `MYSQL_REPLICA_STATUS_PASSWORD`
- 读取 `MYSQL_REPLICATION_USER`
- 读取 `MYSQL_REPLICATION_PASSWORD`
- 在 primary 上创建应用写账号、应用读账号、复制状态检查账号和复制通道账号
- 写账号授予业务库上的增删改查、`REFERENCES` 和迁移所需 DDL 权限
- 读账号授予业务库上的 `SELECT`，并仅授予
  `performance_schema.events_statements_summary_by_digest` 的表级 `SELECT`，
  用于管理员看板的高负载 SQL digest 摘要
- 状态账号只授予 `REPLICATION CLIENT`
- 复制通道账号授予 `REPLICATION SLAVE` 和 `REPLICATION CLIENT`

注意：

- 如果 volume 已经存在，`/docker-entrypoint-initdb.d` 不会重跑。
- `MYSQL_REPLICATION_PASSWORD` 不能为空，否则脚本会失败。
- `MYSQL_REPLICA_STATUS_USER` 或 `MYSQL_REPLICA_STATUS_PASSWORD` 缺失时，不创建状态账号；应用的 `eventual` 读会回退主库读连接。
