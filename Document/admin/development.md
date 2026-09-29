# 管理员前端开发与部署

文档职责：记录管理员 Vue 前端的本地开发、生产构建、初始管理员入口和它依赖的系统级服务。

适用范围：修改 `admin-frontend/`、管理员静态资源托管或管理员开发/发布命令时使用；完整 Docker/数据库部署事实分别见 [`../development/deployment.md`](../development/deployment.md) 与 [`../database/migrations-checkpoints.md`](../database/migrations-checkpoints.md)。

## 生产部署

Dockerfile 使用 Node 24 构建 `admin-frontend/`；构建前复制 `packages/design-system/src/`，由 Vite/TypeScript 别名读取共享源码，再把产物复制到最终 Python 镜像的 `/opt/causalagent-admin`。运行镜像不包含 Node、不启动 Vite，也不开放 Node 端口。

非 Docker 环境需要先生成管理员前端产物：

```bash
cd admin-frontend
npm ci
npm run typecheck
npm run test:unit
npm run build
```

未设置开发服务器地址时，Flask 默认托管仓库中的 `admin-frontend/dist/`（需要先执行 `npm run build`）；目录缺少入口文件时 `/admin/` 返回带 request ID 的 503。Docker 镜像通过 `ADMIN_FRONTEND_DIST_DIR=/opt/causalagent-admin` 指向镜像内产物。

## 创建初始管理员

先完成 Alembic migration，并注册一个处于启用状态的普通用户，然后执行：

```bash
# 本地运行
python -m app.auth.admin_cli promote <username>

# Docker 运行
docker compose -f docker-compose.yml run --rm app python -m app.auth.admin_cli promote <username>
```

该命令只做幂等提升，不创建用户，也不负责降级管理员。管理员登录后进入 `/admin/database`。

## 系统服务依赖

管理员后台依赖数据库 bootstrap、PostgreSQL checkpoint 与 Store、monitor 和 Agent 持久化清理 worker。迁移前请在 `.env` 设置非空的 `CHECKPOINT_POSTGRES_PASSWORD`；完整初始化顺序和破坏性迁移规则见 [`../database/migrations-checkpoints.md`](../database/migrations-checkpoints.md)。常用入口为：

```bash
docker compose -f docker-compose.yml run --rm db-bootstrap

# 本地运行
python -m Database.bootstrap
```

数据库初始化统一由 `db-bootstrap` 完成，`agent-persistence-cleanup` 是持续运行、消费两张 outbox 的跨库清理 worker：

```bash
python -m Database.agent_persistence_cleanup_worker
```

## 启动 monitor

数据库看板读取共享快照。要持续产生新快照，需要独立启动：

```bash
python -m Database.monitor_worker
```

如果 monitor 未运行，页面仍可读取已有快照，但自动刷新和手动刷新请求不会产生新的采集结果。快照、配置优先级和 quick/deep audit 的内部事实见 [`../database/monitoring.md`](../database/monitoring.md)。

## 本地 Vite 开发

只有修改管理员 Vue 源码并需要热更新时，才使用 Vite：

```bash
cd admin-frontend
npm ci
npm run dev
```

同时在 Flask 进程环境中设置：

```text
ADMIN_VITE_DEV_SERVER_URL=http://127.0.0.1:5173
```

Flask 仍先完成管理员页面鉴权，再跳转到 Vite。Vite 只代理 `/api` 到 Flask，不替代 Python 后端。普通部署应保持该配置为空。

管理员侧栏页脚提供“进入聊天”（同源 `/dashboard`）和“RAG 评测台”（同源 `/rag-eval`）两个入口，并在“进入聊天”上方提供“进入 Grafana”入口，浏览器直接跳转到 `http://127.0.0.1:3000/`。Grafana 地址对应默认开发 Compose 仅绑定本机的服务，不经过 Flask，也不共享管理员 Session；生产或远程部署不得使用硬编码的 127.0.0.1:3000。

## 发布产物

`admin-frontend/dist/` 是管理员 Vue 的构建结果，与其他三个前端一致：源码变更后必须重新构建（`npm run build`），构建产物由 `.gitignore` 和 `.dockerignore` 忽略，不进入版本库，镜像从当前源码重新构建并使用 `/opt/causalagent-admin` 中的结果。目录缺少 `index.html` 时 `/admin/` 返回带 request ID 的 `503` 和 `admin_frontend_missing`。系统整体部署顺序见 [`../development/deployment.md`](../development/deployment.md)。
