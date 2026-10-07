# 数据库一致性与主从

文档职责：记录 MySQL 写库、业务读库、复制状态观测、strong/eventual read 和连接池的当前实现。

适用范围：修改 `app/db.py`、`config/database_settings.py`、账号权限、读写路由、复制回退或连接容量时使用；管理员看板只描述消费结果，内部采集机制见 [`monitoring.md`](monitoring.md)。

`config/database_settings.py` 是 MySQL 连接层的独立配置边界。`app.db` 只依赖其中的 `DatabaseConfig`/`database_settings`，因此被 `causal-mcp` 导入时不会初始化 `config.settings`，也不会要求 `API_KEY`、`BASE_URL` 或 `MODEL`。`config.settings.AppConfig` 仍保留同名 `MYSQL_*` 字段供 App、Admin、Worker 和 Monitor 的兼容调用方使用，并继续对应用和模型配置 fail-fast。

## 连接职责

| 连接/账号 | 允许的职责 |
| --- | --- |
| `MYSQL_WRITE_USER` | 主库写入、migration、启动就绪检查 |
| `MYSQL_READ_USER` | 主库/从库业务读；额外读取允许的 Performance Schema digest |
| `MYSQL_REPLICA_STATUS_USER` | 仅执行 `SHOW REPLICA STATUS` |
| `MYSQL_REPLICATION_USER` | 仅供 MySQL 从库复制通道拉取 binlog |
| `MYSQL_USER`/`MYSQL_PASSWORD` | 历史兼容兜底，不替代职责账号 |

PostgreSQL checkpoint 使用 `CHECKPOINT_POSTGRES_*` 配置，管理员和 monitor 的 checkpoint 检查使用只读连接；账号、host 和连接串不能进入 API、快照或审计。

## Read 选择

`get_write_connection()` 固定连接 MySQL 主库。`get_read_connection(consistency="strong")` 固定读取主库；`consistency="eventual"` 才允许尝试副本。副本必须有专用状态账号、IO/SQL 复制线程均为 `Yes`，并且延迟不超过 `MYSQL_REPLICA_MAX_LAG_SECONDS`；否则回退主库，不自动切换写主。

复制状态有短时缓存，默认 `MYSQL_REPLICA_STATUS_CACHE_SECONDS=2`；默认可接受延迟为 2 秒。连接失败、状态缺失或延迟超限都只触发主库回退。来源返回值只使用逻辑别名 `primary`、`replica-1` 等，不暴露真实主机名。

## 读写矩阵

| 路径 | 一致性要求 | 说明 |
| --- | --- | --- |
| Job 创建/领取/心跳/状态、事件和输入写入 | 主库事务 | 这是队列、fencing 和 SSE 事件的权威状态 |
| 用户登录、会话恢复、启用状态、`auth_version` 和 `user_roles`/`role_permissions` 权限 | strong 主库 | 不能用副本或 Session 缓存完成授权 |
| Session 列表等允许短暂延迟的普通读取 | eventual，可回退 | 只读且不影响资源归属判断 |
| Session/Job/File 所有权校验、删除、文件访问计数 | strong/主库事务 | 防止副本延迟造成越权或错误删除 |
| 管理员列表和在线配置 | strong 主库或共享快照 | 管理员页面不依赖弱一致授权 |
| monitor 容量估算 | 按采集器策略 | 必须返回 source、observed_at 和 estimate 语义 |

不能把“所有 SELECT 都去副本”当作读写分离规则。每个新读取路径必须先说明它是否可以接受副本延迟，以及回退主库后的来源标签如何展示。

## 容量和进程边界

连接池按 OS 进程计算，容量估算为：

```text
write_pool + read_pool * (1 + replica_count)
```

worker slot 共享所在 worker 进程的连接池；slot 数增加会增加实际执行并发和数据库事务压力，但不会为每个 slot 自动复制一套池。默认建连超时 5 秒、获取池连接超时 3 秒、获取失败重试间隔 50 毫秒；池大小上限由配置校验限制。

当前开发 Compose 固定使用 `mysql-primary`、`mysql-replica` 和 `postgres-checkpoint` 服务名与独立数据卷，没有自动故障切换。切换 worktree 或 Compose project 时必须核对固定容器名、端口和数据卷，不能用 `down -v` 代替数据保留操作。
