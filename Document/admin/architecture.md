# 管理员模块架构

文档职责：记录管理员 Flask 后端、Vue 前端、实时鉴权以及对数据库 monitor、Job、文件和 checkpoint 能力的消费关系。

适用范围：修改 `app/admin/`、`admin-frontend/`、管理员页面入口、管理员鉴权或管理员与系统级服务的交界时使用；数据库/worker 内部实现分别见 [`../database/overview.md`](../database/overview.md) 与 [`../architecture/agent-runtime.md`](../architecture/agent-runtime.md)。

## 模块组成

管理员模块由三部分组成：

- Flask `admin_bp`，前缀为 `/api/admin`，负责授权、DTO、分页、审计和管理员业务服务。
- Flask `admin_page_bp`，前缀为 `/admin`，负责页面鉴权、Vite 开发跳转或生产 `index.html`、构建静态资源及品牌资源托管。
- `admin-frontend/`，使用 Vue 3、严格 TypeScript、Vue Router、共享 `@causalagent/design-system`、Element Plus、Vite、Vitest 和 Playwright；共享包负责品牌基础和基础展示组件，Element Plus 保留复杂控件适配层。它只调用 Flask API，不直接连接数据库。

`app/__init__.py` 注册 `admin` 和 `admin_page` blueprint。`app/admin/routes.py` 在页面和 API 进入业务代码前调用 `admin_required`（等价于要求 `admin.access` 权限）；管理员身份每次从主库确认用户存在、启用状态、`auth_version` 以及从 `user_roles` 与 `role_permissions` 解析出的权限，不信任 Session 中缓存的角色或权限。未登录页面请求跳转 `/auth/sign-in?next=<管理页面>`，缺少权限时返回受控 `403` 页面。

## 页面边界

Vue router 的 base 固定为 `/admin/`，当前页面为：

| 页面 | 作用 |
| --- | --- |
| `/admin/overview` | 业务聚合概览 |
| `/admin/users` | 用户查看和受控用户操作 |
| `/admin/sessions` | 会话、消息和附件元数据 |
| `/admin/jobs` | Job、MySQL 事件和 checkpoint 安全摘要 |
| `/admin/files` | 文件逻辑记录、预览、下载和删除影响 |
| `/admin/database` | 数据库、monitor、Agent 持久化清理 worker 和两类 outbox 看板 |
| `/admin/database/settings` | monitor 在线配置 |
| `/admin/database/audit` | deep audit 结果 |

后台默认落点是 `/admin/database`。管理员仍可通过普通用户入口访问自己的聊天、文件和 Job；管理员后台不会扩大其普通用户资源访问范围。

## 共享能力消费关系

| 管理员功能 | 消费的系统能力 | 管理员侧边界 |
| --- | --- | --- |
| 数据库看板 | `database_monitor_snapshots`、monitor refresh 请求和清理 worker 心跳 | GET 只读最近快照，不在 Web 请求中运行完整采集 |
| Job 详情 | MySQL `analysis_job_events` 与 PostgreSQL checkpoint 安全摘要 | 不返回 checkpoint 状态正文、blob 或 pending writes |
| 用户/文件删除 | MySQL 业务事务、`checkpoint_cleanup_outbox` 和 `user_memory_cleanup_outbox` | 业务删除先提交，跨库清理异步查询 |
| 文件预览/下载 | `user_files`/`file_objects` 主库事务访问记录 | 有界读取并记录审计，不返回文件 hash 到列表 |
| monitor 配置 | `database_monitor_settings` 的版本锁和来源解析 | 只提交覆盖值，`NULL` 表示继承 |

数据库主从、checkpoint 表、outbox 领取租约、monitor 调度和账号职责不是管理员模块内部实现；变更这些能力时先更新 `Document/database/`，再更新本页消费契约。

## 安全边界与非目标

管理员 API 返回 `401`/`403`，写请求需要 Session CSRF；敏感正文读取要求成功审计可写。受控用户/文件写入还需要主库预览、当前密码重新认证、明确确认和幂等键，批量默认 20、硬上限 50。

后台当前不提供任意 SQL、DDL/DML、migration 按钮、数据库账号授权、复制控制、连接池管理、自动修复、任务强制控制或普通用户数据越权浏览。完整路径和响应边界见 [`api.md`](api.md)。
