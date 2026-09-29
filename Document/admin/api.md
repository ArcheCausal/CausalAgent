# 管理员 API 契约

文档职责：记录 `/api/admin` 管理员 API、`/admin` 页面入口、响应边界和受控业务操作契约。

适用范围：修改 `app/admin/routes.py`、管理员 service、鉴权/CSRF 或 Vue API 调用时使用；数据库 monitor、主从和 checkpoint 内部实现见 [`../database/overview.md`](../database/overview.md) 与 [`../database/monitoring.md`](../database/monitoring.md)。

管理员 API 统一使用 `/api/admin` 前缀。除特别说明外，接口只允许已启用且通过 `user_roles` 与 `role_permissions` 获得 `admin.access` 权限的用户访问；后端每次请求都会通过主库强一致读重新确认用户状态与权限，不把浏览器 Session 中的角色或权限缓存作为授权依据。

## 通用约定

- 未登录 API 请求返回 `401`，普通用户或已失效管理员返回 `403`。
- 所有响应都包含 `X-Request-ID`；格式合法的上游 request ID 会被沿用。
- 登录和 `check_auth` 返回 Session 绑定的 CSRF token。管理员写请求必须通过 `X-CSRF-Token` 回传。
- 受控业务写入还要求当前管理员密码重新认证、`Idempotency-Key`、影响预览和明确确认。
- 列表默认返回 20 条、最多 50 条，并使用不透明游标分页。

## 页面与品牌资源

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/admin/overview` | 业务概览页面 |
| `GET` | `/admin/users` | 用户管理页面 |
| `GET` | `/admin/sessions` | 会话与消息页面 |
| `GET` | `/admin/jobs` | 分析任务页面 |
| `GET` | `/admin/files` | 文件资产页面 |
| `GET` | `/admin/database` | 数据库看板与管理员默认落点 |
| `GET` | `/admin/database/settings` | 采集配置页面 |
| `GET` | `/admin/database/audit` | 数据库审计页面 |
| `GET` | `/admin/assets/<filename>` | 受保护的管理员前端构建资源 |
| `GET` | `/admin/brand/<filename>` | 受保护的管理员品牌图标与应用清单资源 |
| `GET` | `/api/admin/brand/logo` | 受保护的品牌图片 |

未登录访问管理员页面时跳转 `/auth/sign-in?next=<管理页面>`；缺少 `admin.access` 权限返回 `403`（错误码 `admin_required`）。

## 数据库看板

`/admin/database` 使用 `view=database`、`view=cleanup-worker`、`view=outbox` 三段 URL 视图；用户删除结果可附加 `operation_id` 进入 Outbox 并高亮对应记录。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/db/dashboard` | 聚合读取最近共享快照 |
| `GET` | `/api/admin/db/health` | 兼容的数据库健康响应 |
| `GET` | `/api/admin/db/overview` | 兼容的数据库概览响应 |
| `GET` | `/api/admin/db/integrity?mode=quick` | 读取最近 quick 完整性快照 |
| `GET` | `/api/admin/db/slow-queries` | 读取 SQL 性能摘要 |
| `GET` | `/api/admin/jobs/workers` | 读取 Worker/Job 快照 |
| `POST` | `/api/admin/db/refresh` | 登记实时、SQL 和容量刷新请求 |
| `POST` | `/api/admin/db/integrity/run` | 登记完整性审计请求 |
| `GET` | `/api/admin/db/audit` | 读取最近 deep audit 快照 |
| `POST` | `/api/admin/db/audit/run` | 登记 deep audit 请求 |

所有数据库看板 GET 只读取 MySQL 中最近的共享快照，不在 Web 请求中执行完整采集。共享快照还包括 `agent_persistence_cleanup_runtime`（Agent 持久化清理 worker 心跳，含当前任务类型）和 `agent_persistence_cleanup_outbox`（checkpoint 与用户长期记忆两类脱敏队列摘要）；刷新接口只登记请求，实际采集由独立 monitor 完成。Outbox 不返回 `last_error` 原文，只返回安全错误结论；quick/deep 采集会通过只读连接检查 PostgreSQL checkpoint，并把脱敏结论写回共享快照。

Quick 快照的 `checks[]` 每项包含 `description` 和 `warning`：`description` 说明该项实际核对的结构或运行条件，`warning` 仅在本次检查异常或不可用时返回具体原因；历史快照缺少 `description` 时，前端回退展示 `warning`。

SQL 性能摘要按 Performance Schema 的单次平均 `AVG_TIMER_WAIT` 降序选取和展示，平均耗时相同时按累计 `SUM_TIMER_WAIT` 降序次排序，不等同于单次查询超过 `long_query_time`。慢查询告警优先使用采集窗口内 `Slow_queries` 增量。

## 在线采集配置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/db/settings` | 读取当前有效值、覆盖值和版本 |
| `PUT` | `/api/admin/db/settings` | 使用乐观版本锁保存覆盖值 |
| `POST` | `/api/admin/db/settings/reset` | 重置数据库覆盖值 |
| `GET` | `/api/admin/db/settings/history` | 游标读取配置审计历史 |

有效值优先级是“数据库覆盖 > 环境变量 > 代码默认值”，数据库中的 `NULL` 表示继承。每个进程最多缓存 5 秒；数据库读取失败时优先使用最后有效值，再回退环境变量或代码默认值。

## 业务读取

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/business/overview` | 业务聚合概览 |
| `GET` | `/api/admin/business/users` | 用户列表 |
| `GET` | `/api/admin/business/users/<id>` | 用户详情 |
| `GET` | `/api/admin/business/sessions` | 会话列表 |
| `GET` | `/api/admin/business/sessions/<id>` | 会话详情 |
| `GET` | `/api/admin/business/sessions/<id>/messages` | 会话消息列表 |
| `GET` | `/api/admin/business/messages/<id>/attachments` | 消息附件列表 |
| `GET` | `/api/admin/business/messages/<id>/content` | 分块读取消息正文 |
| `GET` | `/api/admin/business/attachments/<id>/content` | 分块读取附件正文 |
| `GET` | `/api/admin/business/jobs` | 分析任务列表 |
| `GET` | `/api/admin/business/jobs/<id>` | 分析任务详情 |
| `GET` | `/api/admin/business/jobs/<id>/events` | 任务事件列表 |
| `GET` | `/api/admin/business/jobs/<id>/checkpoints` | PostgreSQL checkpoint 安全摘要 |
| `GET` | `/api/admin/business/jobs/<id>/content` | 分块读取任务内容 |
| `GET` | `/api/admin/business/files` | 文件列表 |
| `GET` | `/api/admin/business/files/<id>` | 文件详情 |
| `GET` | `/api/admin/business/files/<id>/preview` | 有界 CSV 文本预览 |
| `GET` | `/api/admin/business/files/<id>/download` | 下载文件 |
| `GET` | `/api/admin/operations/<operation_id>` | 查询受控用户删除及 Agent 持久化清理状态 |

密码哈希、Cookie、Token、文件哈希、数据库账号、host 和 grants 不进入列表 DTO。Job DTO 额外返回 `execution_state`、`execution_released_at` 和 `execution_release_reason`；worker 汇总返回 draining 数量、最长 draining 时长以及 `worker_confirmed`/`lease_expired` 计数。任务事件接口只从 MySQL payload 提取节点名、说明和耗时；checkpoint 接口按 `thread_id=analysis_jobs.job_id` 与 `metadata.job_id` 精确归属任务，根 namespace 为空，默认 20、最多 50 条并按不透明 `checkpoint_id` 游标分页，只返回 ID、父 ID、namespace、时间、step、source 和更新通道。它不读取或返回 checkpoint 状态正文、blob 和 pending writes；旧 session-thread checkpoint 不迁移、不读取、不清理。历史记录缺少 `job_id` 时不会按时间猜测归属。PostgreSQL 不可用时返回 `503 checkpoint_unavailable`，任务详情和 MySQL 事件接口仍可用。

消息、附件及任务内容最多按 64 KiB 源字节分块读取；成功的敏感读取要求审计可写。CSV 预览最多读取 256 KiB、100 行、50 列，单元格最多 1000 字符，并且只按文本渲染。

## 受控业务写入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/admin/business/users/operations/preview` | 预览用户批量操作影响 |
| `POST` | `/api/admin/business/users/operations` | 执行用户启停、角色或密码操作 |
| `GET` | `/api/admin/business/users/<id>/delete-impact` | 预览用户删除影响 |
| `DELETE` | `/api/admin/business/users/<id>` | 物理删除用户 |
| `GET` | `/api/admin/business/files/<id>/delete-impact` | 预览文件删除影响 |
| `DELETE` | `/api/admin/business/files/<id>` | 物理删除文件和 BLOB |

操作者不能禁用、降级或删除自己，也不能移除最后一个启用管理员。角色、状态或密码实际变化会通过 `users.auth_version` 使目标用户旧 Session 失效。用户删除先提交 MySQL 业务数据，PostgreSQL 父子图 checkpoint 和用户长期记忆清理由同一个 outbox worker 异步完成；只有两类清理全部成功，操作才进入 `succeeded`；接口可能返回 `202`，可通过操作查询接口读取 `running/succeeded/failed`。物理删除没有回收站，Job/file 生命周期和跨库清理边界见 [`../architecture/job-file-lifecycle.md`](../architecture/job-file-lifecycle.md) 与 [`../database/migrations-checkpoints.md`](../database/migrations-checkpoints.md)。
