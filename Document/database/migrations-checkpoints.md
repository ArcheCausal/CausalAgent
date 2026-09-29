# 迁移与 Checkpoint

文档职责：记录 MySQL/Alembic/bootstrap、PostgreSQL checkpoint 与 Store setup、两类 cleanup outbox 以及具有破坏性的迁移事实。

适用范围：修改 `Database/bootstrap.py`、`Database/migrations/versions/`、checkpoint 配置、cleanup worker 或部署初始化顺序时使用；数据库危险操作的执行约束见 [`../../Database/AGENTS.md`](../../Database/AGENTS.md)。

## 初始化顺序

`Database/database_init.py` 只加载环境变量、确保 MySQL 数据库存在并执行连接检查。完整入口 `python -m Database.bootstrap` 按顺序执行：

1. MySQL 建库/连接准备。
2. 必要的旧库 schema-aware preflight。
3. `alembic upgrade head` 维护 MySQL 业务 schema。
4. 官方 LangGraph PostgreSQL checkpointer schema setup。

Docker Compose 中 `db-bootstrap` 是一次性服务，`app`、worker、monitor 和 cleanup 依赖其成功退出后再启动。全新空库不需要先运行旧库 preflight；`Database/audit_before_db_upgrade.py` 只服务于已存在、尚未建立目标外键且即将执行相关迁移的旧库。

## 当前迁移链

当前唯一 head 是 `d0e1f2a3b4c5`。develop 的 migration 作为主体链路，RAG 评测 DDL 使用独立且全仓唯一的 revision ID，最终由无 DDL 合流点统一；最终链路和 down revision 以文件内容为准：

| Revision | 当前作用 |
| --- | --- |
| `1a2b3c4d5e6f` | 核心用户、Session、消息、附件、旧文件和归档表 |
| `bae097eab4b3` | 早期 MySQL checkpoint 表 |
| `9359bc171e66` / `d876b980dc9a` | 附件可视化字段和内容容量调整 |
| `f6b8c9d0e1a2` | 移除聊天分区、重建主键/索引并补充业务外键 |
| `e7a9b2c3d4f5` | `analysis_jobs` 与 Job Event |
| `a8b9c0d1e2f3` / `b1c2d3e4f5a6` / `c2d3e4f5a6b7` | 用户角色、共享 monitor 快照、在线配置和管理员审计 |
| `d3e4f5a6b7c8` / `e4f5a6b7c8d9` | 管理员读取索引、受控写入、操作账本和 `auth_version` |
| `f8b9c0d1e2f3` | MySQL checkpoint -> PostgreSQL，建立 cleanup outbox |
| `f9a0b1c2d3e4` | Job 请求幂等键和请求指纹 |
| `a1b2c3d4e5f6` / `b2c3d4e5f6a7` / `c3d4e5f6a7b8` | develop 权威的文件库与 Job 恢复、执行释放、请求 ID |
| `1c2d3e4f5a6b` / `2d3e4f5a6b7c` / `a0b1c2d3e4f5` | develop 联网搜索字段、引用附件类型及其与 request ID 的合流 |
| `r1a2b3c4d5e6f` / `r2b3c4d5e6f7` | RAG 评测 profile 与持久 Job 队列 |
| `r3c4d5e6f7a8` / `g7c8d9e0f1a2` / `h8d9e0f1a2b3` / `i9e0f1a2b3c4` | RAG 评测与 PostgreSQL 合流、任务类型、数据集注册和队列优先级 |
| `s4d5e6f7a8b9` | develop `a0b1c2d3e4f5` 与 RAG 最终 head 的统一无 DDL head |
| `t5e6f7a8b9c0` | 用户长期记忆清理 outbox，不处理或激活历史数据 |
| `u7a8b9c0d1e2` | 把 database_monitor_snapshots.snapshot_key 扩展到 64 字符 |
| `v8b9c0d1e2f3` | 为 chat_attachments.attachment_type 增加结构化报告附件类型 |
| `w9c0d1e2f3a4` | 建立 `roles`、`permissions`、`user_roles`、`role_permissions`，初始化两个角色与第一阶段权限并回填用户关系 |
| `c9d0e1f2a3b4` | 建立跨 Job 的 `analysis_contexts`，并给 Session、Job 和输入账本增加上下文绑定字段 |
| `d0e1f2a3b4c5` | 补齐新注册用户缺失的默认 `user` 角色关系，并为兼容字段仍为 `admin` 的用户补齐管理员关系 |

`f8b9c0d1e2f3` 的 `down_revision` 声明为 `e4f5a6b7c8d9` 与 `e7a9b2c3d4f5`；develop 主链随后经 `f9a0b1c2d3e4`、`a1b2c3d4e5f6`、`b2c3d4e5f6a7`、`c3d4e5f6a7b8` 和 `a0b1c2d3e4f5` 继续，RAG 分支经 `r1...`、`r2...`、`r3...`、`g7...`、`h8...`、`i9...` 继续，最终由 `s4d5e6f7a8b9` 合流，再由 `t5e6f7a8b9c0` 线性追加记忆清理 outbox、由 `u7a8b9c0d1e2` 扩展快照键长度、由 `v8b9c0d1e2f3` 增加结构化报告附件类型、由 `w9c0d1e2f3a4` 建立角色与权限关系表、由 `c9d0e1f2a3b4` 建立分析上下文结构，最后由 `d0e1f2a3b4c5` 补齐注册缺陷遗留的角色关系。回退这类合并迁移必须指定明确目标 revision，不能用 `alembic downgrade -1` 代替。

## 破坏性事实

- `f8b9c0d1e2f3` 建立 `checkpoint_cleanup_outbox` 后直接删除 MySQL `checkpoint_writes` 和 `checkpoints` 表及其数据；PostgreSQL 才是运行时 checkpoint 真相。downgrade 只重建空的兼容表结构，不恢复数据。
- `t5e6f7a8b9c0` 只创建 `user_memory_cleanup_outbox`，不读取、不回填也不激活任何历史记忆数据。downgrade 只删除这张新表，不触碰 PostgreSQL Store 数据。
- `u7a8b9c0d1e2` 只放宽 `database_monitor_snapshots.snapshot_key` 的字符上限，不改写现有快照内容。downgrade 恢复 32 字符上限；若此时仍存在超长快照键，MySQL 会拒绝执行而不是静默截断。
- `v8b9c0d1e2f3` 只在 `chat_attachments.attachment_type` 枚举尾部追加 `report_document`，不删除或改写既有枚举值和历史附件。downgrade 先删除 `report_document` 附件行，再收缩枚举；被删除的结构化报告不能从已有消息正文恢复。
- `w9c0d1e2f3a4` 新建四张 RBAC 表并按现有 `users.role` 回填 `user_roles`，不改写 `users.role` 与任何业务数据。downgrade 只删除这四张表；再次 upgrade 会重新初始化角色权限并重新回填关系，期间的授权变更记录不在迁移范围内。
- `a1b2c3d4e5f6` 直接 `DROP TABLE IF EXISTS uploaded_files`，创建 `file_objects`、`user_files` 和 Job 输入结构；不回填旧数据、不提供旧数据 fallback，也不增加旧数据拒绝迁移逻辑。downgrade 只恢复空的旧 `uploaded_files` 表结构。
- `c9d0e1f2a3b4` 新建 `analysis_contexts` 并向 `sessions`、`analysis_jobs`、`analysis_job_inputs` 增加可空列；不回填历史 Session 或历史 Job 的上下文，旧数据在下一次分析或追问时按需创建。downgrade 只删除这些新结构与对应外键，不删除会话、Job、输入或报告数据。
- `d0e1f2a3b4c5` 只向缺少对应关系的用户补写 `user_roles`：所有用户至少获得 `user` 角色，兼容字段仍为 `admin` 的用户同时保留管理员角色；不修改 `users`、角色种子或权限种子。由于新增关系无法与迁移前已存在的合法授权区分，downgrade 不删除任何角色关系。
- 迁移脚本属于高风险历史事实，不应为了让本地旧库“看起来能升级”而静默删除、回填或修改历史 migration。

## PostgreSQL checkpoint

`Database/checkpoint_setup.py` 调用官方 `AsyncPostgresSaver.setup()` 创建 checkpoint schema。worker 以 `analysis_jobs.job_id` 作为 `thread_id`，根 `checkpoint_ns` 为空；`config.metadata` 同时保存 `job_id` 和业务 `session_id`，供管理员安全摘要精确关联。

checkpoint 与长期记忆共用同一个 `postgres-checkpoint` 实例但属于不同 schema：checkpoint schema 由 `db-bootstrap` 通过 `Database/checkpoint_setup.py` 初始化，官方 Store schema 由 Agent worker 启动时调用 `AsyncPostgresStore.setup()` 初始化。清理 worker 只校验 checkpoint migration 版本和 Store migration 版本，不创建任何表；Store schema 尚未就绪时按有界等待重试，超时后以启动失败退出。

管理员和 monitor 的 quick integrity 只读检查连接、官方表集合和 setup migration 版本；deep audit 额外检查字段/主键、估算统计和最多 20 个跨库 `thread_id -> analysis_jobs.job_id` 关系样本。checkpoint API 不读取或返回状态正文、blob 或 pending writes，缺少 `metadata.job_id` 的历史记录不按时间猜测归属。

## Cleanup outbox

MySQL 删除 Session 或用户时，在同一个业务事务中为相关 Job 写入 `(thread_id)` 唯一的 `checkpoint_cleanup_outbox` 记录；物理删除用户时还会写入一条 `(user_id)` 唯一的 `user_memory_cleanup_outbox` 记录，并保持与 `admin_operations` 的 `operation_id` 关联。两张表都不关联 `users` 外键，用户行删除后任务仍然保留。

同一个 worker 使用 `FOR UPDATE SKIP LOCKED` 在两张表之间轮转领取，写入租约后删除 PostgreSQL 数据：checkpoint 任务删除父图 `job_id` thread 和 `deep-agent:<uuid5(job_id)>` 子图 thread，记忆任务按可信 `user_id` namespace 通过官方 Store API 逐条删除并复核为空。租约过期可再次领取，最多执行有限次数，失败按退避重试。管理员用户删除操作通过 `operation_id` 聚合两类 outbox 状态为 `running`、`succeeded` 或 `failed`。

worker 按 `AGENT_PERSISTENCE_CLEANUP_HEARTBEAT_INTERVAL_SECONDS` 发布脱敏心跳，默认 10 秒；运行快照只保存逻辑 worker 状态、当前任务类型、计数、时间和安全错误结论，不保存 host、账号或原始 `last_error`。

## 相关入口

- [`../../alembic.ini`](../../alembic.ini)：Alembic script location。
- [`../../Database/bootstrap.py`](../../Database/bootstrap.py)：统一初始化编排。
- [`../../Database/checkpoint_setup.py`](../../Database/checkpoint_setup.py)：PostgreSQL setup。
- [`../../Database/agent_persistence_cleanup_worker.py`](../../Database/agent_persistence_cleanup_worker.py)：父子图 checkpoint 与用户长期记忆清理 worker。
- [`../../Database/audit_before_db_upgrade.py`](../../Database/audit_before_db_upgrade.py)：旧库升级前 preflight。
