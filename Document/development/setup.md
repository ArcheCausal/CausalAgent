# 开发环境

文档职责：记录当前仓库的本地、Docker、四个前端开发服务器、共享设计系统包和数据库初始化入口。

适用范围：首次配置开发环境、切换运行方式或修改启动入口时使用；服务拓扑与镜像发布见 [`deployment.md`](deployment.md)，测试命令见 [`testing.md`](testing.md)。

## 配置前提

配置分为普通运行参数和密钥两类。模型名、服务地址、数据库账号名、连接池和超时等普通参数来自进程环境；密码、API key、令牌和签名密钥由 `config/secret_file.py` 从单个密钥文件读取。容器内默认路径为 `/run/secrets/causalagent.env`；开发 Compose 默认挂载仓库根目录的 `causalagent.env`（已加入 `.gitignore`）。本地 Python 需用 `CAUSALAGENT_SECRET_FILE` 指向该文件，并在启动前将普通参数提供给进程。应用配置不再自动加载根目录 `.env`，密钥也不回退到环境变量；Compose 自己仍可读取 `.env` 用于普通参数替换。密钥模板见 [`deploy/secrets/causalagent.env.example`](../../deploy/secrets/causalagent.env.example)。

| 配置模块 | 职责与依赖 |
| --- | --- |
| `config/secret_file.py` | 定义密钥键集合，读取、校验和缓存密钥文件，不创建数据库或模型连接 |
| `config/database_settings.py` | 读取 MySQL 连接参数、连接池和副本策略，并从密钥加载器取得密码；不导入 `config/settings.py` |
| `config/checkpoint_settings.py` | 读取 PostgreSQL checkpoint 连接参数，并从密钥加载器取得密码 |
| `config/settings.py` | 汇总应用配置，复用数据库和 checkpoint 配置，读取应用及通用模型密钥 |
| `app/agent/worker/runtime.py` | 创建 Deep Agent 模型；模型名和地址仍从环境读取，API key 从密钥文件读取 |

服务器普通配置模板为 [`production.env.example`](../../deploy/config/production.env.example)，复制填写为 `/etc/causalagent/config/production.env` 后由 Compose 的 `--env-file` 读取。Compose 用其替换 YAML 中的 `${...}`，再通过服务的 `environment` 声明把所需参数传入容器；代码最终读取的是进程环境变量。`--env-file` 不会把所有键自动注入所有容器，Python 也不直接打开该服务器文件。发布镜像引用另由清单生成的 `images.env` 提供，无需在普通配置中填写。

当前普通参数没有统一的 Python 读取入口：部分参数经过 `AppConfig`、`DatabaseConfig` 或 checkpoint 配置类，`DEEP_AGENT_MODEL` 等参数仍由 worker 的 `os.getenv` 直接读取。统一的是服务器普通配置的提供方式，以及敏感值的密钥加载器，不能将这两点表述为所有参数都通过 `settings.py` 读取。

当前密钥加载器在每个调用进程中都校验全部 `REQUIRED_SECRET_KEYS`，并非按服务选择必需键。因此，单独启动数据库消费者或 `causal-mcp` 时，也必须提供包含应用、模型和 Grafana 等全局必需键的文件；完整 Compose 部署同样执行此检查。`causal-mcp` 不导入完整应用配置，不消费模型地址和模型名，但挂载的是完整密钥文件，不能据此声称它完全不依赖模型密钥的存在。

应用/Worker 还需要通用模型地址与名称、Deep Agent 的模型名、地址、context window，以及数据库和 MCP 参数。开发 Compose 允许 Deep Agent base URL 回退到通用 `BASE_URL`；`DEEP_AGENT_API_KEY` 必须在密钥文件中单独提供，不能回退到 `API_KEY`。正式多模态 API embedding 需要文件中的 `EMBEDDING_API_KEY` 和环境中的 `EMBEDDING_BASE_URL`；本地 production embedding 开关当前关闭。`CAUSAL_MCP_SLOW_LOG_SECONDS` 默认 60 秒，只控制慢日志阈值，不改变算法 deadline。不要将密钥文件、密码、API key 或数据库连接串提交到 Git、日志或文档。

主从开发使用职责分离账号：写主库、业务读、复制状态观测和复制通道账号各自配置。没有专用复制状态账号时，eventual read 会安全回退主库。

## Docker 开发

Docker 是当前首选开发方式：

```bash
docker compose -f docker-compose.yml up -d
```

默认开发 Compose 会同时启动日志采集拓扑 `loki`、`alloy` 和 `grafana`。由于 Grafana 服务要求管理员密码非空，首次启动前必须准备好仓库根目录的 `causalagent.env` 并在其中设置 `GRAFANA_ADMIN_PASSWORD`；修改 Alloy 配置或首次拉取镜像时，先执行：

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml pull loki alloy grafana
docker compose -f docker-compose.yml run --rm --no-deps alloy validate /etc/alloy/config.alloy
```

日志查看地址为 `http://127.0.0.1:3000/grafana/`（Grafana 以 `/grafana/` 为子路径）。完整的日志启动、停止、生产边界和验收步骤见
[`observability.md`](observability.md) 与 [`deployment.md`](deployment.md)。

首次启动、空卷重建或数据库环境重建时，Compose 会先运行 `db-bootstrap`。需要单独重跑一次性初始化时执行：

```bash
docker compose -f docker-compose.yml run --rm db-bootstrap
```

开发与预发 Compose 都直接使用已提交的非密钥 `settings.yml`（开发为 `searxng/core-config/settings.yml`，预发为 `deploy/staging/searxng/config/settings.yml`），密钥由密钥文件里的 `SEARXNG_SECRET` 经启动适配脚本导出并覆盖配置文件中的同名字段。开发环境需要在该密钥文件中提供 `SEARXNG_SECRET`，预发使用服务器密钥文件；当前生产 Compose 不包含 SearXNG，启用生产搜索需另行部署搜索服务。`searxng` 使用固定版本镜像并提供 `/healthz` 浅层 healthcheck。默认 `app`/`worker` 不依赖 SearXNG 健康，联网搜索不可用时由 Job 运行期重试并降级。

查看搜索服务的启动和健康状态：

```bash
docker compose -f docker-compose.yml ps searxng valkey
```

需要验证 Compose 合并后的部署契约时，使用 `docker compose config`；不要使用 `down -v` 清理共享数据库或搜索数据卷。

开发 Compose 直接挂载仓库内的 `Agent/knowledge_base/multimodal_indexes` 与 `Agent/knowledge_base/multimodal_runtime`，不再使用一次性同步容器，也不再使用 `kb_multimodal_indexes` 命名卷。Chroma 打开索引时会写入 `acquire_write` 写锁记录，因此这条路径必须可写；不要改回只读挂载。预发和生产的正式索引改由独立命名卷承载，见 [`deployment.md`](deployment.md)。

开发 Compose 使用 `mysql-primary`、`mysql-replica`、`postgres-checkpoint`、`app`、`worker`、`causal-mcp`、`monitor`、`agent-persistence-cleanup`、`rag-eval-worker`、`searxng`、`valkey`、`loki`、`alloy` 和 `grafana`；固定端口和数据卷属于共享 Docker daemon 资源，多个 worktree 同时运行时必须采用独立 project/端口策略，不能误用 `down -v`。

## 本地 Python

不使用 Docker 时先进入项目 Python 环境，并准备好密钥文件（`CAUSALAGENT_SECRET_FILE` 指向本地 `causalagent.env`），然后按需启动：

```bash
python -m Database.bootstrap
python CausalAgent.py
python -m Agent.CausalAgentMCP.app
python -m app.agent.worker
python -m Database.monitor_worker
python -m Database.agent_persistence_cleanup_worker
```

`Database/database_init.py` 只确保 MySQL 数据库存在并检查连接；完整业务表和 PostgreSQL checkpoint schema 仍由 `Database.bootstrap` 负责，官方 Store schema 由 Agent worker 启动时 setup。清理 worker 要求这两类 schema 都已就绪，因此本地手工启动顺序是先 `app.agent.worker`（或至少让它完成一次启动）再启动清理 worker。新空库不要先运行旧库 preflight。

## Windows 桌面客户端开发

Windows 桌面客户端是独立的 WebView2 壳，不启动或打包 Flask、MySQL、worker、模型和知识库。先按 [`../../windows-client/README.md`](../../windows-client/README.md) 创建桌面虚拟环境并安装 `windows-client/requirements-desktop.txt`：

```powershell
python -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r .\windows-client\requirements-desktop.txt
.\.venv-desktop\Scripts\python.exe .\Run_causal.py --check-environment
```

启动桌面壳前，先用 Docker 或本地 Python 启动现有 Flask 后端；开发模式的 URL 通过 `--url` 或 `CAUSALAGENT_DESKTOP_URL` 配置，默认是 `http://127.0.0.1:5001/dashboard`，同样允许 `http://localhost:5001/dashboard`：

```powershell
$env:CAUSALAGENT_DESKTOP_URL = "http://127.0.0.1:5001/dashboard"
.\.venv-desktop\Scripts\python.exe .\Run_causal.py
```

配置优先级为命令行 `--url` > `CAUSALAGENT_DESKTOP_URL` > 模式默认值。Release 包使用构建时嵌入的 HTTPS origin，强制关闭 debug 和开发者工具；它不能通过桌面壳切换到任意外部页面。WebView2 的 Cookie/localStorage 数据目录是 `%LOCALAPPDATA%\CausalAgent\WebView`，用于按服务器 Session 策略跨重启保存登录状态。

## 四个前端开发服务器一键启动

四个前端工程互相独立，逐个启动要在四个目录分别执行 `npm ci` 和 `npm run dev`。`scripts/dev_frontends.ps1` 用 `Start-Process` 为选中的前端各开一个窗口运行 Vite；脚本只使用 PowerShell 内置命令和本机 npm，不引入任何 npm 依赖，端口和资源前缀直接从各工程的 `vite.config.ts` 读取，不会与前端配置出现偏差：

```powershell
.\scripts\dev_frontends.ps1                          # 启动全部四个前端
.\scripts\dev_frontends.ps1 -Frontends website,chat  # 只启动官网和普通端
.\scripts\dev_frontends.ps1 -Frontends rag -Install  # 先执行 npm ci 再启动
.\scripts\dev_frontends.ps1 -WhatIf                  # 只打印将要执行的操作
```

`-Frontends` 可用值为 `website`、`chat`、`admin`、`rag`，也可以写工程目录名（如 `chat-frontend`），默认四个全部启动。各前端的端口与开发地址如下：

| 前端 | 工程目录 | 端口 | 开发地址 |
| --- | --- | --- | --- |
| `website` | `website-frontend/` | 5175 | `http://localhost:5175/site-assets/` |
| `chat` | `chat-frontend/` | 5174 | `http://localhost:5174/dashboard-assets/` |
| `admin` | `admin-frontend/` | 5173 | `http://localhost:5173/admin/` |
| `rag` | `app/rag_eval/frontend/` | 5176 | `http://localhost:5176/rag-eval/` |

端口上已经有服务在监听时，该前端会被跳过；工程缺少 `node_modules` 时会提示先安装依赖并跳过；脚本结束时打印已启动的地址与进程号，以及被跳过的前端和原因。脚本不设置 `*_VITE_DEV_SERVER_URL`，Flask 是否把页面交给 Vite 仍由 `.env` 决定。PowerShell 执行策略阻止运行脚本时，改用 `powershell -ExecutionPolicy Bypass -File scripts\dev_frontends.ps1 <参数>`。

## 前端共享设计系统

四个前端共用的品牌基础和基础组件位于 `packages/design-system/`，它以源码形式参与各前端的构建，不单独产出构建产物。视觉决策、token 和组件契约的记录在 `Document/design-system/`，接入方式和改动约束见 `packages/design-system/README.md`。

管理员端和 RAG 评测台已接入本包的共享样式与组件；本包本身不启动独立开发服务器。改动后执行自检：

```powershell
Push-Location packages/design-system
npm ci
npm run typecheck
npm run test:unit
Pop-Location
```

安装依赖必须使用 `npm ci`：本目录的 `.npmrc` 固定 `legacy-peer-deps`，与 `website-frontend/` 的处理方式一致。

## 普通用户应用前端开发

普通用户应用工程位于 `chat-frontend/`，开发服务器默认使用 5174 端口和 `/dashboard-assets/` base：

```powershell
Push-Location chat-frontend
npm ci
npm run dev
Pop-Location
```

Vite 将 `/api` 代理到 `http://127.0.0.1:5001`。如果希望通过 Flask 页面跳转到 Vite，设置 `CHAT_VITE_DEV_SERVER_URL=http://127.0.0.1:5174`；`/dashboard`、`/dashboard/settings` 和 `/dashboard/session/<id>` 都会跳转到 `http://127.0.0.1:5174/dashboard-assets<原路径>`。未设置该变量时，页面从 `chat-frontend/dist/` 或 `CHAT_FRONTEND_DIST_DIR` 指定目录由 Flask 提供。开发时也可以直接访问 `http://127.0.0.1:5174/dashboard-assets/` 获取热重载。

未登录访问 `/dashboard` 会跳转到 `/auth/sign-in?next=/dashboard`，因此本地联调登录流程时需要同时启动官网前端或使用已登录的浏览器 Session。

## 官网前端开发

官网工程位于 `website-frontend/`，开发服务器默认使用 5175 端口和 `/site-assets/` base：

```powershell
Push-Location website-frontend
npm ci
npm run dev
Pop-Location
```

Vite 将 `/api` 代理到 `http://127.0.0.1:5001`。设置 `WEBSITE_VITE_DEV_SERVER_URL=http://127.0.0.1:5175` 后，`/`、`/product`、`/pricing`、`/about`、`/docs`、`/changelog` 与 `/auth/sign-in`、`/auth/sign-up` 会跳转到 `http://127.0.0.1:5175/site-assets<原路径>`；未设置时由 Flask 从 `website-frontend/dist/` 或 `WEBSITE_FRONTEND_DIST_DIR` 提供。该工程使用 `.npmrc` 固定 `legacy-peer-deps`，安装依赖请使用 `npm ci`。

## RAG 评测台前端开发

RAG 评测台源码位于 `app/rag_eval/frontend/`，开发服务器默认使用 5176 端口和 `/rag-eval/` base，构建产物输出到 `app/rag_eval/frontend_dist/`：

```powershell
Push-Location app/rag_eval/frontend
npm ci
npm run dev
Pop-Location
```

页面本身仍由 Flask 在 `/rag-eval` 提供并要求 `rag_eval.access`；开发时直接访问 `http://localhost:5176/rag-eval/` 获取热重载，Vite 把 `/api` 代理到 `http://127.0.0.1:5001`。这个工程的 `vite` 没有指定 `host`，只监听 IPv6 的 `::1`，因此用 `localhost` 而不是 `127.0.0.1` 访问。页面写请求需要 Session 绑定的 CSRF 令牌，前端在挂载时从 `/api/check_auth` 读取并附加 `X-CSRF-Token`。

## 管理员前端开发

管理员 Vue 源码位于 `admin-frontend/`。需要热更新时执行：

```bash
cd admin-frontend
npm ci
npm run dev
```

Vite 固定使用 `/admin/` base，默认端口为 5173，并把 `/api` 代理到 `http://127.0.0.1:5001`。Flask 仍先完成页面鉴权；只有显式设置 `ADMIN_VITE_DEV_SERVER_URL=http://127.0.0.1:5173` 才跳转到 Vite。普通部署保持该变量为空，让 Flask 托管构建产物。

## 初始管理员

完成 migration 并注册一个启用的普通用户后，只通过现有 CLI 提升管理员：

```bash
python -m app.auth.admin_cli promote <username>
```

Docker 中可使用：

```bash
docker compose -f docker-compose.yml run --rm app python -m app.auth.admin_cli promote <username>
```

该命令不创建公开管理员注册接口，也不负责降级管理员。
