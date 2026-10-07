# 数据库职责总览

文档职责：记录 MySQL 业务数据库、PostgreSQL checkpoint/Store 数据库、业务表关系和跨库一致性边界。

适用范围：修改数据库连接、迁移、表结构、checkpoint、cleanup outbox 或跨库删除流程时使用；具体主从读策略见 [`consistency.md`](consistency.md)，初始化与迁移顺序见 [`migrations-checkpoints.md`](migrations-checkpoints.md)。

## 两个数据库的职责

| 数据库 | 当前权威数据 | 不负责的内容 |
| --- | --- | --- |
| MySQL 主库 | 用户、角色与权限关系、Session、消息、附件、文件库、Job、Job Event、输入账本、管理员操作/审计、monitor 快照、两类 cleanup outbox | LangGraph checkpoint 正文和用户长期记忆正文 |
| PostgreSQL checkpoint/Store | LangGraph 官方 checkpoint schema、Job 恢复状态和长期记忆 Store | 用户业务数据、管理员业务审计和公开 API 资源归属 |

MySQL 是业务状态和队列的权威来源；PostgreSQL 是 checkpoint 的运行时真相。当前 LangGraph `thread_id` 使用 `analysis_jobs.job_id`，根 `checkpoint_ns` 为空，业务 `session_id` 仍然引用 MySQL `sessions.id`。

## MySQL 业务表分组

- **身份与会话**：`users`、`sessions`、`archived_sessions`、`chat_messages`、`chat_attachments`。
- **角色与权限**：`roles` 与 `permissions` 保存角色键和权限键，`user_roles` 与 `role_permissions` 保存关系；授权判断以两张关系表为准，`users.role` 只作为过渡兼容字段。
- **文件库**：`file_objects` 保存不可变 BLOB，`user_files` 保存用户可见逻辑文件。
- **Job**：`analysis_jobs` 是队列和状态，`analysis_job_inputs` 是 initial/resume 输入账本，`analysis_job_events` 是事件日志。
- **管理员**：`admin_audit_events` 记录管理员动作和结果，`admin_operations`/`admin_operation_items` 记录受控批量操作。
- **监控**：`database_monitor_snapshots` 保存跨 Web 进程共享的最近采集结果，`database_monitor_settings` 保存单例在线覆盖配置。
- **跨库清理**：`checkpoint_cleanup_outbox` 按 `thread_id` 唯一登记 Job 父子图 checkpoint 删除请求；`user_memory_cleanup_outbox` 按 `user_id` 唯一登记用户长期记忆 Store 删除请求，且不关联 `users` 外键，避免用户行删除后任务丢失。

文件对象与逻辑文件分离后，同一用户相同 hash 可以复用 BLOB；不同文件名保留不同逻辑记录。删除逻辑文件只有在没有其他逻辑引用并且没有活动 Job 使用时才删除 BLOB。

## 关系和删除

用户和 Session 的业务外键负责常规级联；聊天附件通过消息关系删除；Job Event/Input 通过 Job 关系删除。用户物理删除还必须显式处理归档 Session、为每个相关 Job 写入 `checkpoint_cleanup_outbox`、写入一条 `user_memory_cleanup_outbox`，并受关联行数量阈值保护。文件物理删除不提供回收站。

跨 MySQL 和 PostgreSQL 的删除不使用分布式事务。MySQL 事务提交业务删除与 outbox 后，独立 cleanup worker 通过 `adelete_thread()` 删除父图 job thread 和 Deep Agent 子图 thread，并通过官方 Store API 清空被删用户的记忆 namespace；两张 outbox 的状态和租约是可恢复的外部工作账本。

## Schema 权威与就绪检查

业务 schema 的唯一维护入口是 `alembic.ini` 指向的 `Database/migrations`；`Database/database_init.py` 只确保 MySQL 数据库存在并检查连接，不创建业务表。`app/db.py` 的 `check_database_readiness()` 会在 Flask 启动前检查关键表、Job 恢复字段、冻结文件字段、用户安全字段、`user`/`admin` 两个角色种子、两张 cleanup outbox 的领取索引和幂等索引。

当前关键表集合包括：`users`、`sessions`、`chat_messages`、`chat_attachments`、`file_objects`、`user_files`、`archived_sessions`、`checkpoint_cleanup_outbox`、`user_memory_cleanup_outbox`、`analysis_jobs`、`analysis_job_events`、`analysis_job_inputs`、`database_monitor_snapshots`、`database_monitor_settings`、`admin_audit_events`、`admin_operations`、`admin_operation_items`、`roles`、`permissions`、`user_roles` 和 `role_permissions`。不存在 MySQL checkpoint 作为运行时数据源的兼容读取路径。

数据库代码入口：[`../../app/db.py`](../../app/db.py)、[`../../Database/database_init.py`](../../Database/database_init.py)、[`../../Database/bootstrap.py`](../../Database/bootstrap.py) 和 [`../../Database/migrations/versions`](../../Database/migrations/versions)。
