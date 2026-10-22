# 构建与部署

文档职责：记录 CausalAgent Docker 镜像构建、开发/预发/生产 Compose 拓扑、测试预发操作流程、初始化顺序和管理员静态产物的发布边界。

适用范围：测试服务器首次部署和已有 staging 服务更新，以及修改 Dockerfile、Compose 服务、镜像启动命令、环境配置或发布流程时使用；数据库内部迁移和 checkpoint 细节见 [`../database/migrations-checkpoints.md`](../database/migrations-checkpoints.md)。

## 镜像构建

`Agent/CausalAgentMCP/Dockerfile` 使用 MCP 自己的依赖环境，在复制源码后执行 `python -c "import Agent.CausalAgentMCP.app"`，并检查导入完成后的 `sys.modules` 中没有 `langgraph*`、`langchain*` 或 `Agent.deep_agent` 及其子模块。`publish-staging-images.yml` 的 MCP 构建直接使用该 Dockerfile，导入失败或越过依赖边界都会阻止镜像推送。此检查不需要数据库或部署密钥；发布前仍须用真实数据库和配置启动镜像并确认 `/ready` 返回成功，并执行算法 `Command` 终态解析与依赖调度回归。

`Dockerfile` 当前有六个重要阶段：

1. `python-deps` 按 `tests/smoke/requirements-deep-agent-py311-linux.lock` 的 hash 锁定安装全部 Python 依赖（含 CPU PyTorch），不使用 `requirements-base.txt` 与 `requirements.txt` 在线求解。
2. `test` 在共享依赖上安装 `requirements-test.txt`，默认执行 `tests/unit`。
3. `admin-builder` 构建管理员前端，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-admin`。
4. `chat-builder` 构建普通用户应用，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-chat`。
5. `website-builder` 构建官网前端，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-website`。
6. `rag-eval-builder` 构建 RAG 评测台，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-rag-eval`。

四个前端的 Vite 构建会把 Geist Sans 和 Noto Sans SC 的 WOFF2 子集放入各自的资源目录，并把字体对应的 OFL 文件复制到 `assets/font-licenses/`，文件名包含内容摘要。许可文本通过各前端已有的资源路由提供，与同目录带 hash 的字体资源一样可以安全使用 immutable 缓存；发布时字体与许可证必须一起保留。

最终运行镜像不包含 Node、npm，不启动 Vite，不开放 Node 端口；Gunicorn 默认绑定 `0.0.0.0:5001`，由 `WEB_WORKERS`、`WEB_THREADS` 和 `WEB_TIMEOUT` 分别调整进程数、每进程线程数和超时秒数，默认值为 `1`、`12` 和 `120`。

预发 `app` 保留 `python scripts/staging_environment_guard.py && exec gunicorn` 启动顺序，三个参数分别使用 `--workers $${WEB_WORKERS}`、`--threads $${WEB_THREADS}` 和 `--timeout $${WEB_TIMEOUT}`。Compose 通过 `environment` 注入配置值；命令中的 `$$` 阻止 Compose 提前插值，再由容器内的 `sh -c` 展开变量。生产 `app.environment` 同样注入三项 `WEB_*` 配置，变量未设置或为空时使用上述默认值，并沿用 Dockerfile 的默认启动命令。

四个前端各自使用独立的运行时目录，Compose 默认指向镜像内产物，源码卷不能覆盖这些目录：官网 `WEBSITE_FRONTEND_DIST_DIR=/opt/causalagent-website`，普通用户应用 `CHAT_FRONTEND_DIST_DIR=/opt/causalagent-chat`，RAG 评测台 `RAG_EVAL_FRONTEND_DIST_DIR=/opt/causalagent-rag-eval`，管理员系统 `ADMIN_FRONTEND_DIST_DIR=/opt/causalagent-admin`。

四套入口的页面地址与资源前缀是：官网 `/`、`/product`、`/pricing`、`/about`、`/docs`、`/changelog`、`/auth/sign-in`、`/auth/sign-up` 与 `/site-assets/`；普通用户应用 `/dashboard*` 与 `/dashboard-assets/`；RAG 评测台 `/rag-eval` 与 `/rag-eval/assets/`、`/rag-eval/brand/`；管理员系统 `/admin*` 与 `/admin/assets/`、`/admin/brand/`。入口 HTML 不缓存，带 hash 的 `assets/` 资源使用长期 immutable 缓存；任一 dist 缺失时它的页面入口和资源路径统一返回带 request ID 的 503（`website_frontend_missing`、`chat_frontend_missing`、`rag_eval_frontend_missing`），不回退到其他前端。

## 开发部署

默认 `docker-compose.yml` 是包含主系统、RAG 评测、联网搜索和可观测性的 15 服务开发拓扑；服务职责如下：

| 服务 | 作用 |
| --- | --- |
| `mysql-primary` / `mysql-replica` | 主库写入与复制副本，分别使用独立数据卷 |
| `postgres-checkpoint` | LangGraph checkpoint 数据库 |
| `db-bootstrap` | 一次性 MySQL/Alembic/PostgreSQL 初始化 |
| `app` | Flask Web，暴露 5001 |
| `worker` | Agent Job worker |
| `causal-mcp` | 私有 Streamable HTTP 因果算法服务；只加入内部网络，不映射宿主端口 |
| `monitor` | 数据库共享快照采集 |
| `agent-persistence-cleanup` | 跨库删除 Job 父子图 checkpoint 和用户长期记忆 Store |
| `rag-eval-worker` | 独立领取 RAG 摄取、候选、评测和治理队列任务 |
| `searxng` / `valkey` | 固定版本的 SearXNG 联网学术搜索及其缓存/队列依赖 |
| `loki` / `alloy` / `grafana` | 开发环境运行日志采集、存储和查看；只加入独立的 observability network |

`app`、Agent worker、`causal-mcp`、monitor、RAG evaluation worker 和 agent-persistence-cleanup 依赖 `db-bootstrap` 成功退出；`causal-mcp` 另外等待 `mysql-primary` 健康，并以 `/health`/`/ready` 提供进程、MySQL strong read 和执行器就绪边界。`causal-mcp` 不映射宿主端口，算法在有界容量内按 invocation 使用独立子进程，因此控制面取消只终止目标算法，不回收并行 sibling；`CAUSAL_MCP_SLOW_LOG_SECONDS` 在开发、预发和生产 Compose 中默认 60 秒。因果 MCP 镜像先安装 CPU Torch，再安装固定版本 `cdfm-base==0.1.0` 与现有 CDMIR；构建末尾按写死的 revision 与逐文件 SHA-256 把 CDFM 权重下载到镜像内只读目录 `/opt/cdfm-weights-image`，任一项不合格都会让构建失败。开发 Compose 的 `CDFM_MODEL_PATH` 仍默认指向 `DMIRLAB/CDFM`，由开发 smoke 从 Hugging Face 加载；预发由一次性 `cdfm-weights-seed` 服务把镜像内容同步进命名卷 `rag_eval_staging_cdfm_weights`，`causal-mcp` 只读挂载 `/opt/cdfm-weights` 并设置 `HF_HUB_OFFLINE=1`，运行期不再访问网络。服务通过 `config/database_settings.py` 读取 MySQL 配置，通过统一密钥文件读取 Bearer/HMAC 材料。Compose 不向 MCP 直接注入应用或模型密钥环境变量，也不要求模型地址和名称；但挂载的完整文件会经过全局必需键检查，仍需包含应用和模型等必需密钥。当前 worker 新路径还依赖 `causal-mcp` healthy，启动时按“PostgreSQL checkpoint pool/schema → AsyncPostgresStore setup → 静态 Registry → 进程级 MCP Client pool handshake → RAG readiness → Deep Agent/父图编译”的顺序完成 fail-fast 初始化；slot 不再创建 stdio MCP session。`searxng` 依赖 Valkey 健康。联网搜索是 Job 级可选能力，worker readiness 不等待 SearXNG，运行期不可用时新 `web_evidence_search` 返回受控 unavailable/disabled 结果；非搜索 Job 不会因此阻止启动。开发拓扑当前不提供自动故障切换。启动命令见 [`setup.md`](setup.md)。

### 服务器密钥文件

密码、令牌和签名密钥统一保存在服务器上的单个密钥文件里，默认路径为 `/etc/causalagent/secrets/causalagent.env`，可以用环境变量 `CAUSALAGENT_SECRET_FILE` 覆盖。服务器发布必须将文件设为 `0640`、属主设为 `root`、属组设为容器专用 GID `3000`。Compose 会把该组加入 MySQL、PostgreSQL、Grafana 和 SearXNG 容器；发布校验检查服务器权限，验收还需确认各容器的实际读取身份可读。单进程本地运行仍可使用 `0600` 或 `0400`。该文件不进入 Git、部署包或镜像。允许出现的键由 `config/secret_file.py` 的 `SECRET_KEYS` 定义，模板见 [`deploy/secrets/causalagent.env.example`](../../deploy/secrets/causalagent.env.example)；未知键、重复键、空值、缺键和权限不安全都会让进程启动失败。

所有环境的容器通过 Compose secret 把该文件只读挂载到 `/run/secrets/causalagent.env`。应用、worker、MCP 和数据库配置由 `config/secret_file.py` 显式读取；加载器清除继承的密钥环境变量，不会把文件值重新写入进程环境。可选 LangSmith 集成会单独导出其 SDK 所需密钥。所有服务仍由 Compose 启动。Compose 负责选择镜像、提供普通环境变量并挂载密钥文件；镜像内的应用负责解释文件内容。项目 Python 服务直接调用密钥加载器；MySQL、PostgreSQL、SearXNG 和 Grafana 的上游入口不认识项目自定义的多键格式，由 `deploy/scripts/secret-env.sh` 只导出对应入口需要的变量，再执行上游入口。MySQL 主从使用基于官方 MySQL 构建的自有镜像，适配入口已写入镜像；镜像中的 mysql 用户属于 GID 3000，官方入口降权后仍可读取初始化所需密钥。离线开发工具 `Agent/knowledge_base/build_knowledge.py` 与 `Agent/knowledge_base/multimodal/cli.py` 可读取本地 `.env`，但不属于 app 或 worker 的生产启动路径。非敏感配置继续使用环境变量或非密钥配置文件。

开发 Compose 使用仓库根目录的 `causalagent.env`（已加入 `.gitignore`）作为同一个密钥文件入口；首次启动前把它从 [`deploy/secrets/causalagent.env.example`](../../deploy/secrets/causalagent.env.example) 复制到仓库根目录并按需填写。

`loadtest/` 的数据库只读采集器从部署环境文件读取账号和库名，通过数据库容器内的 `secret-env.sh` 读取密码；密码不放入 SSH 或 Docker 命令参数。本地 Grafana 日志核查默认从仓库根目录 `causalagent.env` 读取口令，可用 `CAUSALAGENT_SECRET_FILE` 指定其他受控文件；使用步骤见 [`loadtest/README.md`](../../loadtest/README.md)。
采集器按配置选择配套的 `MYSQL_READ_USER` / `MYSQL_READ_PASSWORD`，未声明 READ 时选择 WRITE，最后兼容 legacy USER/PASSWORD；认证失败保留受控错误码，不切换账号。负载开始前必须通过真实主库查询、队列与连接数预检。
预发网关对白名单只读 API 记录结构化 request ID、固定路由标签、HTTP/上游状态和上游连接、响应、请求耗时；不记录查询参数、资源正文或凭据。日志写入 `/proc/1/fd/1`，由容器日志采集。客户端与应用继续使用同一 `X-Request-ID`；没有应用记录时三段关联保持不完整，不能凭网关记录推定应用错误原因。
Shell 密钥读取器兼容 LF 和 CRLF 换行，只移除行尾属于分隔符的回车，避免 Windows 保存的文件使密码多出一个字符；移除分隔符后为空的值仍然拒绝。本地 Grafana 核查读取统一密钥文件时使用相同规则，保留口令中的引号和空格。

开发 `docker-compose.yml` 与兼容入口 `docker-compose.replica.yml` 为所有读取密钥的服务固定设置已有的 `ENVIRONMENT=development`。Python 密钥加载器和官方镜像的 Shell 适配器在该环境下跳过 POSIX 权限位和属主检查，直接读取主机文件，以兼容 Windows Docker Desktop 呈现的挂载权限；文件仍须存在并可读，各入口原有的内容校验继续执行。不增加权限策略开关、密钥准备服务或密钥卷。预发、生产以及未显式设置 development 的 Linux 进程保留现有权限检查，服务器发布校验仍要求 `0640 root:3000`。

开发 Python 服务通过仓库源码挂载读取当前实现；`causal-mcp` 只读挂载 `config/secret_file.py`，因此本地调整密钥读取逻辑后只需重建容器，不需要为了这项修改重新下载镜像中的 CDFM 权重。预发和生产继续使用镜像内代码。

首次启动或代码更新后运行 `docker compose up -d --build`，后续启动运行 `docker compose up -d`。修改密钥文件后运行 `docker compose up -d --force-recreate`，让容器重新挂载并让各进程重新读取；已有数据库密码和 Grafana 账号不会因为修改文件而自动更新。

## 联网搜索（SearXNG）

开发与预发 Compose 使用同一套密钥注入方式：`searxng/core-config/settings.yml` 与 `deploy/staging/searxng/config/settings.yml` 都是提交在仓库里的非密钥配置，只包含引擎白名单、JSON 输出、Valkey 地址等字段，不写 `secret_key`。`SEARXNG_SECRET` 与其他密钥一致，保存在服务器密钥文件里；容器启动时由 `deploy/scripts/secret-env.sh` 从该文件导出为环境变量，SearXNG 随后读取它并覆盖配置文件中的同名字段，因此密钥不进入 Git 或 Compose 的直接环境配置，但启动后的 SearXNG 进程环境仍含自身所需密钥。

开发与预发 Compose 都固定使用 `searxng/searxng:2026.8.21-bbb3c7d82`。升级镜像时必须重新验证 `settings.yml`、JSON 输出格式、三学术引擎配置和 `/healthz`，并确认 `SEARXNG_SECRET` 仍会覆盖文件中的同名字段；不要直接改回 `latest`。`/healthz` 只检查 SearXNG Web 进程和配置加载后的 HTTP 响应，不检查外部学术引擎、DNS 或出站网络；真实搜索可用性仍由 web_search 的运行期重试和降级处理。

开发 Compose 的可观测组件使用固定版本和独立命名卷：Loki、Alloy 不开放宿主机端口，Grafana 只绑定 `127.0.0.1:3000` 并以 `/grafana/` 为入口子路径。启动 Grafana 前必须在密钥文件里设置非空的 `GRAFANA_ADMIN_PASSWORD`；采集范围由应用容器的 `causalagent_observability` 标签筛选，不包含 MySQL、PostgreSQL、Loki、Alloy 或 Grafana 自身。完整字段、标签和真实验收边界见 [`observability.md`](observability.md)。

## 预发部署

`docker-compose.staging.yml` 是隔离预发拓扑，包含网关、应用、Agent 与 RAG worker、私有 MCP、MySQL 主从、PostgreSQL checkpoint、SearXNG/Valkey 和 Loki/Alloy/Grafana。数据库、搜索、Grafana 和观测数据都使用 staging 专属命名卷；应用服务与观测服务分属两个 Docker 网络。所有 Python 服务先通过 `scripts/staging_environment_guard.py` 校验项目/DSN/数据库/卷名中的 production/prod 标识，`db-bootstrap` 成功后才启动应用服务；worker 还等待 `causal-mcp` healthy，并要求显式的 Deep Agent model/base/API key/context window 与 MCP service token/signing key。SearXNG 使用 `deploy/staging/searxng/config/` 下已提交的非密钥 `settings.yml`。Grafana 数据保存在命名卷 `rag_eval_staging_grafana`，不依赖宿主目录权限。正式 RAG 索引、active pointer 与发布配置使用可写命名卷 `rag_eval_staging_formal`，评测数据使用 `rag_eval_staging_eval`，模型缓存使用 `rag_eval_staging_models`；assets 仍为只读挂载，正式发布配置位于可写正式卷内。

公网只发布 gateway 的 8088 端口。Grafana 不再映射宿主端口，由 gateway 反代到同源 `/grafana/`；SearXNG、Valkey、Loki、Alloy、MySQL、PostgreSQL、app、worker 和 MCP 均不发布宿主端口。该入口是明文 HTTP，仅用于测试服务器，管理员凭据会在链路上以明文传输；云安全组和主机防火墙只允许需要的公网入口端口。

### CI 与人工镜像发布

`.github/workflows/lightweight-ci.yml` 只在面向 `main`/`develop` 的 PR 上运行门禁，并按改动路径选择后端单元测试、五个前端工程自检/构建、Compose 与预发部署合同、Alloy 配置验证和四个 Dockerfile 的 BuildKit 静态检查；面向 `main` 的 PR 无条件运行全部检查。`push` 到 `main`/`develop` 不运行门禁，只重建 Docker unit 测试镜像以导出层缓存。两处流程都不推送镜像。完整镜像构建只由 `.github/workflows/publish-staging-images.yml` 的 `workflow_dispatch` 触发，并拒绝 `develop` 以外的分支；它把 app、MCP、MySQL primary、MySQL replica 分别构建并直接推送 GHCR，不创建镜像 artifact，也不通过 SSH 部署服务器。每个镜像使用 `sha-<commit 前 7 位>` 标签，摘要中同时给出 `sha256` digest；服务器配置必须使用 digest。

仓库默认分支是 `main`，因此首次手动触发前，发布 workflow 文件必须先合并到 `main`；触发时在 Actions 页面选择 `develop`。公开仓库的公开 GHCR Container 镜像按 GitHub Packages 计费规则免费存储和传输（见 [GitHub Packages 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-packages)）。新建 GHCR 包后，维护者需在包设置中将可见性改为 Public，之后测试服务器才可匿名拉取。构建使用普通 `ubuntu-latest` runner，并在构建前后记录磁盘余量；MCP 首次构建是否能在 runner 可用磁盘内完成，以该次 workflow 实际结果为准。镜像直接推送 GHCR，不导出为 Actions artifact。

GitHub 当前的公开 Linux x64 标准 runner 配置为 4 核、16 GB 内存和 14 GB SSD，公开仓库使用标准 runner 不计费；具体规格以 [GitHub-hosted runners 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) 为准。GHCR 第一次发布的新包默认为 Private，维护者需按 [包访问与可见性说明](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility) 手动改成 Public。

服务器不执行 `git pull`，也不需要完整源码；它只解压部署包并使用服务器上的非密钥配置与密钥文件。

首次部署时，在服务器上准备两份文件；模型名、地址等普通参数与密钥的分工见 [`setup.md`](setup.md#配置前提)：

1. 非密钥配置 `/etc/causalagent/config/production.env`：从部署包的 `deploy/config/production.env.example` 复制，填入普通环境配置；
2. 密钥文件 `/etc/causalagent/secrets/causalagent.env`：从 `deploy/secrets/causalagent.env.example` 复制，填入互不重复的真实随机值，并设置 `chown root:3000 /etc/causalagent/secrets/causalagent.env` 与 `chmod 640 /etc/causalagent/secrets/causalagent.env`。

不要在服务器以外复制密钥文件，也不要把它提交到 Git；`.gitignore` 会排除 `causalagent.env` 与 `deploy/secrets/*.env`，`.dockerignore` 也会排除环境文件与本地数据，避免本地构建时把它们放进镜像上下文。

部署前先下载 Actions 制品并解压其中的 `.tar.gz`。`deploy.sh` 接收已经解压的目录，复制到调用者指定的 `<releases_dir>/<release_id>`，从清单生成 `images.env`，更新 `current`，再执行 Compose `pull` 和 `up -d`。测试服务器的完整命令见下方[测试预发操作流程](#测试预发操作流程)；生产使用 `docker-compose.prod.yml` 和相应服务器配置。两种环境必须使用不同的 Compose project、配置和数据卷。

脚本不负责下载和解压制品、创建服务器配置、镜像仓库登录、旧 RAG 数据迁移或最终验收。它在 `pull/up` 前切换 `current`，失败时不会自动恢复链接。部署完成后按 [`release-acceptance.md`](release-acceptance.md) 选择明确的 Compose 文件执行检查。

需要验证 Compose 合并后的部署契约时，使用 `docker compose config`；不要运行 `docker compose down -v`，它会删除预发数据库、checkpoint、搜索、观测和正式 RAG 数据卷。首次启动后还需在云安全组与主机防火墙核实只有 gateway 端口对公网开放。

### 测试预发操作流程

本节用于 Ubuntu 测试服务器首次部署或更新已有 staging 服务。镜像发布与制品生成沿用[部署包与发布清单](#部署包与发布清单)，密钥格式与读取机制沿用[服务器密钥文件](#服务器密钥文件)，详细验收沿用 [`release-acceptance.md`](release-acceptance.md)。以下命令在服务器同一个 root Bash 会话中按顺序执行；普通账号先执行 `sudo -i`，再设置本节变量。每一步成功后再继续，重新登录后需要重新设置变量。

```mermaid
flowchart LR
    A[发布镜像并下载制品] --> B[上传并解压部署包]
    B --> C[确认项目名并准备配置]
    C --> D[核对原有数据与备份]
    D --> E[deploy.sh 拉取并更新]
    E --> F[检查状态并执行验收]
```

#### 1. 确认现有项目

```bash
docker compose ls
```

更新已有服务时，使用 `NAME` 列中的项目名，不能仅根据宿主文件夹名推断。例如现有输出为 `causalagent-staging running(15)`、配置文件为 `/opt/causalagent-staging/docker-compose.staging.yml`，后续继续使用 `causalagent-staging`。首次部署没有对应项目时，才自行确定一个独立的测试项目名。

```bash
export COMPOSE_PROJECT_NAME=causalagent-staging
export RELEASES_DIR=/opt/causalagent-staging/releases
```

更新其他项目时替换上面两项。发布目录和 Compose 项目名是两个独立参数；脚本会创建版本子目录，不需要把部署包手工覆盖到旧目录。当前命名卷没有显式 `name`，实际卷名带项目名前缀；保持项目名还需保持对应卷定义，才能继续使用原卷。

#### 2. 上传并解压制品

在 Actions 中运行 `Publish staging images` 并等待自动打包成功，下载对应制品。外层 ZIP 包含内层 `tar.gz`、`images.lock.json` 与 `release.manifest.json`；镜像层由 Docker 从仓库拉取，不在部署包中。将 ZIP 上传到服务器 `/tmp`，以下发布标识仅作示例，更新时替换为本次制品名中的标识：

```bash
RELEASE_ID=2026.10.16-0b79676-38870125961-1
ARCHIVE_DIR="/tmp/causalagent-artifact-$RELEASE_ID"
export BUNDLE_DIR="/tmp/causalagent-$RELEASE_ID/bundle"
mkdir -p "$ARCHIVE_DIR" "$BUNDLE_DIR"
unzip "/tmp/causalagent-release-$RELEASE_ID.zip" -d "$ARCHIVE_DIR"
tar -xzf "$ARCHIVE_DIR/causalagent-release-$RELEASE_ID.tar.gz" -C "$BUNDLE_DIR"
test -f "$BUNDLE_DIR/release.manifest.json" && echo "manifest OK"
test -f "$BUNDLE_DIR/scripts/deploy.sh" && echo "deploy.sh OK"
test -f "$BUNDLE_DIR/docker-compose.staging.yml" && echo "staging compose OK"
```

服务器需已安装 Docker Engine、Compose 插件、Python 3、tar 和 unzip，并能访问镜像仓库。若本机已解开 ZIP，只需上传内层 `tar.gz` 并执行 `tar`；若部署包已经解压，直接将 `BUNDLE_DIR` 设置为包含上述三个文件的绝对目录，跳过解压。不要把 `verify.sh` 当作此时的预检入口，它依赖部署时才生成的 `images.env`。私有镜像需先在同一个 root 用户下执行 `docker login ghcr.io`，公开可匿名拉取的镜像无需登录。

#### 3. 准备服务器配置

| 文件 | 来源与用途 | 本节使用的服务器路径 |
| --- | --- | --- |
| 普通配置 | `deploy/config/production.env.example`；项目名、模型名、地址、数据库账号名和运行参数 | `/etc/causalagent/config/production.env` |
| 密钥文件 | `deploy/secrets/causalagent.env.example`；密码、API key、令牌和签名密钥 | `/etc/causalagent/secrets/causalagent.env` |

首次准备时从部署包的两个模板创建配置并填写真实值；已有文件时编辑现有文件，不用模板覆盖。普通配置中的 `COMPOSE_PROJECT_NAME` 必须与第 1 步一致，不能直接沿用模板的 `causalagent`。`production.env` 可以作为本次测试配置的文件名，环境由项目名、数据库、数据卷和所选 Compose 文件确定；若同一服务器还有生产服务，必须为两套服务配置不同的配置文件与密钥文件路径。

从旧环境变量机制迁移时，只将密钥键及其现有值转入密钥文件；普通参数留在普通配置。沿用数据库现有密码及账户、数据库名，不能按新装模板重新随机生成；修改文件不会自动修改已初始化数据库的账户密码。新增键按模板补齐，已有应用签名和服务认证材料也应保留，轮换另行安排。

文件管理器通常以 SSH 普通账号上传，具有 sudo 权限不代表上传进程拥有 root 权限。优先在服务器编辑真实密钥；若配置已准备好且已上传到 `/tmp/upload`，先保留现有目标文件的受限备份，再在 root 终端安装：

```bash
mkdir -p /etc/causalagent/config /etc/causalagent/secrets "$RELEASES_DIR"
install -D -o root -g root -m 0640 \
  /tmp/upload/production.env /etc/causalagent/config/production.env
install -D -o root -g 3000 -m 0640 \
  /tmp/upload/causalagent.env /etc/causalagent/secrets/causalagent.env
chmod 0600 /tmp/upload/production.env /tmp/upload/causalagent.env
```

`install` 会覆盖同名目标，因此只在确认本次上传内容和备份后执行；已在目标路径编辑完成则跳过。源路径必须以 `/` 开头，`tmp/upload` 在 root 家目录下会被解释为 `/root/tmp/upload`。上传目录应仅允许上传者访问；部署后由操作者清理临时密钥副本，不要保留可被其他用户读取的副本。

#### 4. 部署前核对

```bash
export CAUSALAGENT_CONFIG_FILE=/etc/causalagent/config/production.env
export CAUSALAGENT_SECRET_FILE=/etc/causalagent/secrets/causalagent.env
export CAUSALAGENT_COMPOSE_FILE=docker-compose.staging.yml
test -r "$CAUSALAGENT_CONFIG_FILE" && echo "config readable"
test -r "$CAUSALAGENT_SECRET_FILE" && echo "secret readable"
grep -Fqx "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME" "$CAUSALAGENT_CONFIG_FILE" \
  && echo "project matches"
stat -c '%a:%u:%g' "$CAUSALAGENT_SECRET_FILE"
```

项目名检查必须通过，密钥权限必须输出 `640:0:3000`。这些 export 传递路径或环境选择，不包含密钥值；应用仍从容器内 `/run/secrets/causalagent.env` 读取密钥。

清除当前 shell 中旧的镜像变量，避免覆盖清单生成的 `images.env`：

```bash
unset APP_IMAGE CAUSAL_MCP_IMAGE MYSQL_IMAGE MYSQL_REPLICA_IMAGE POSTGRES_IMAGE
unset NGINX_IMAGE SEARXNG_IMAGE VALKEY_IMAGE LOKI_IMAGE ALLOY_IMAGE GRAFANA_IMAGE
unset STAGING_APP_IMAGE STAGING_MCP_IMAGE STAGING_MYSQL_PRIMARY_IMAGE STAGING_MYSQL_REPLICA_IMAGE
```

更新已有服务前，保留旧 Compose、旧配置及已验证的镜像引用，确认 MySQL、checkpoint 和正式 RAG 数据备份可用。使用以下只读命令检查现有卷和宿主目录，再与新部署包的挂载声明比较；宿主数据路径和迁移范围见[服务器数据目录](#服务器数据目录)与[多模态索引目录与挂载](#多模态索引目录与挂载)，不要用新建空目录代替原数据迁移。

```bash
for container_id in $(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME"); do
  docker inspect --format '{{.Name}} {{.Config.Image}} {{json .Mounts}}' "$container_id"
done
```

#### 5. 拉取并更新服务

```bash
bash "$BUNDLE_DIR/scripts/deploy.sh" "$BUNDLE_DIR" "$RELEASES_DIR"
```

脚本把版本放入 `$RELEASES_DIR/<release_id>`，生成 `images.env` 并切换 `current`，随后执行 `pull`、`up -d`。拉取阶段旧容器继续运行；更新阶段按需停止并重建镜像或配置有变化的容器，可能短暂中断。项目名与卷定义一致时继续使用原卷，但 bootstrap 可能执行数据库迁移，卷保留不代表数据内容不变，也不保证进行中的任务无中断。项目名不同会尝试创建另一套资源，可能出现端口占用或新建空卷；不要为更新先执行 `down`，更不要执行 `down -v`。

#### 6. 检查结果与失败处理

```bash
export RELEASE_DIR="$(readlink -f "$RELEASES_DIR/current")"
export COMPOSE_FILE="$RELEASE_DIR/$CAUSALAGENT_COMPOSE_FILE"
cd "$RELEASE_DIR"
bash "$RELEASE_DIR/scripts/verify.sh" "$RELEASE_DIR"
staging_compose() {
  docker compose --env-file "$CAUSALAGENT_CONFIG_FILE" \
    --env-file "$RELEASE_DIR/images.env" \
    -f "$COMPOSE_FILE" "$@"
}
staging_compose ps --all
```

确认 `db-bootstrap` 和 `cdfm-weights-seed` 成功退出，长期服务正常运行，有健康检查的服务应达到健康状态。`verify.sh` 成功只代表部分静态条件通过；继续按[发布验收清单](release-acceptance.md)检查实际镜像、密钥读取、数据库、MCP、真实任务、SSE 和 RAG。该清单的通用示例默认生产路径，使用时改为本节的项目名、staging Compose 与 `RELEASE_DIR`，不要直接覆盖为 `/opt/causalagent/releases/current`。

部署失败时保留首次错误和本次版本目录。如果本次发布目录及 `images.env` 已生成，先执行本步的路径设置与函数定义，再使用 `staging_compose ps --all` 及 `staging_compose logs --tail 100 <服务名>` 在服务器本地排查；若尚未生成，则使用第 4 步按 Docker 项目标签查询的命令查看原容器。只分享脱敏后的诊断信息。`current` 在拉取前已经改变，不能用它判断发布成功，也不会自动回到旧版本；如果失败发生在 `up -d` 阶段，可能已有部分服务更新。回退前确认目标版本与当前数据库 schema、配置和密钥兼容；`rollback.sh` 按目录修改时间选择非当前目录，不等于上一成功版本，也不恢复数据。首次从旧目录迁入发布目录时，旧服务不会自动成为脚本可选的历史版本。

### 服务器数据目录

预发与生产 Compose 还会挂载少量宿主数据目录。它们属于服务器数据而不是仓库源码，因此使用绝对路径，不依赖发布目录，也不随部署包分发：

| 环境变量 | 默认宿主路径 | 容器路径 | 用途 |
| --- | --- | --- | --- |
| `CAUSALAGENT_KB_SOURCE_DIR` | `/var/lib/causalagent/kb/source` | `/app/Agent/knowledge_base/source` | 受控知识源目录（只读） |
| `CAUSALAGENT_KB_DB_DIR` | `/var/lib/causalagent/kb/db` | `/app/Agent/knowledge_base/db` | 旧 Chroma 知识库向量库 |
| `CAUSALAGENT_KB_ASSETS_DIR` | `/var/lib/causalagent/kb/assets` | `/app/Agent/knowledge_base/multimodal_assets` | 多模态资产（图片、PDF 等，只读） |
| `CAUSALAGENT_GRAPH_OUTPUT_DIR` | `/var/lib/causalagent/reports/graphs` | `/app/app/static/generated_graphs` | 报告生成的因果图 |

这些路径不随部署包分发，需要在服务器上预先创建、放入所需数据并为对应容器设置访问权限；只读来源目录仍需可读。目录不存在时 Docker 可能新建空目录，不能把目录创建成功视为资料已经迁移。知识源和图输出目录由生产 Compose 挂载；预发仅挂载表中的旧向量库和多模态资产目录。绝对路径使这些数据不随发布目录切换，也不需要服务器保留 Git 仓库。`rag_formal` 与 `rag_eval` 仍然只使用命名卷，不依赖这些宿主目录。

## 生产部署

`docker-compose.prod.yml` 使用生产 MySQL、PostgreSQL checkpoint、统一 bootstrap、Web、Agent worker、独立 `causal-mcp`、monitor、checkpoint cleanup 和独立 `rag-eval-worker`；生产环境不挂载源代码，使用独立卷、网络和日志轮转设置。worker 通过进程级 MCP Client pool 调用私有服务，必须显式提供不可变 Deep Agent 配置和 MCP 鉴权材料；它依赖 `causal-mcp` healthy 后才开始 claim Job。`causal-mcp` 使用独立不可变镜像、单 ASGI worker、默认最多 2 个并行 invocation 和 4 个等待队列，不开放宿主端口；每个已运行 invocation 使用独立单进程 executor 以支持精确取消。镜像内的 CDMIR 与 `cdfm-base==0.1.0` 固定版本，并使用 CPU Torch 依赖闭合 `pip check`；CDFM 权重同样在构建期按固定 revision 下载到 `/opt/cdfm-weights-image` 并逐文件校验。生产 Compose 仍由部署环境通过 `CDFM_MODEL_PATH` 显式指定权重位置；命名卷入卷与 `HF_HUB_OFFLINE` 目前只在预发拓扑生效。RAG evaluation worker 与主系统进程隔离，并通过独立评测卷共享必要的运行产物；它不带开发可观测性标签，不应把评测日志混入主系统观测流。当前生产 Compose 是单独的生产配置，不能假设它自动提供开发 Compose 的 MySQL replica、SearXNG、Loki/Alloy/Grafana 或故障切换能力。
当前生产 Compose 未定义 SearXNG 服务；如果生产环境启用 `web_search_enabled`，必须另外提供可访问的 `SEARXNG_URL` 和对应的搜索服务部署，搜索不可用时仍遵循 worker 运行期降级语义。生产 readiness 和发布证据必须按 [`testing.md`](testing.md) 重新执行并记录，不能用单元或静态 Compose 检查替代。

## 部署包与发布清单

发布后的服务器验收命令见 [`release-acceptance.md`](release-acceptance.md)。

服务器只部署部署包，不执行 `git pull`，也不需要完整源码。CI 用 `scripts/build_release_bundle.py` 在某个源码提交上生成部署包，包含：两个 Compose 文件、`deploy/` 下的网关与配置模板、`observability/` 的采集与看板配置，以及 `scripts/` 下的部署脚本。部署包不包含 Python、Vue、TypeScript 源码，也不包含真实密码、API key、数据库数据或 RAG 正式索引。

镜像范围：应用代码、MCP 代码和 MySQL 自定义初始化逻辑进入自有镜像（`causalagent-app`、`causalagent-mcp`、`causalagent-mysql-primary`、`causalagent-mysql-replica`）；PostgreSQL、Nginx、SearXNG、Valkey、Loki、Alloy 和 Grafana 使用官方镜像。两类镜像都必须在 `images.lock.json` 与 `release.manifest.json` 中固定 digest；`scripts/resolve_official_images.py` 用 `docker buildx imagetools` 解析官方镜像 digest，未固定 digest 时构建直接失败。

正常发布只需人工触发一次 Actions，镜像构建与部署包生成已自动衔接：

1. 在 `develop` 上运行 `Publish staging images`，构建并推送 app、MCP、MySQL primary 和 MySQL replica 到 GHCR。四个构建全部成功后，工作流自动调用 `Build release bundle`，传入本次完整源码提交 SHA；任意构建失败时不生成部署包。这里的发布是推送容器仓库，不要求创建 GitHub Release。
2. 打包工作流用该提交的 `sha-<前7位>` 标签查询四个 GHCR 镜像的 digest，并在不可变引用上核对各平台的 OCI revision。第三方镜像 digest 也由工作流自动查询；随后生成发布清单、镜像锁和压缩包并上传制品。发布标识默认由 UTC 日期、提交短 SHA、运行编号及重试次数组成，摘要显示完整提交和四个镜像引用。查询只读取镜像元数据，不下载完整镜像层。
3. 下载 Actions 制品，把其中的压缩包传到服务器并解压。服务器配置与密钥保持独立；已有数据的存储迁移只在首次采用新卷结构时执行。
4. 运行 `deploy.sh`。脚本把清单的镜像映射为 `APP_IMAGE`、`CAUSAL_MCP_IMAGE`、`MYSQL_IMAGE` 等变量，同时生成预发使用的 `STAGING_*` 别名，并写入发布目录的 `images.env`。
5. 脚本以服务器普通配置和 `images.env` 两个 `--env-file` 启动 Compose，执行 `pull` 和 `up -d`。服务器不再需要逐项手填新发布的镜像 digest。部署 shell 不应保留旧的同名镜像环境变量，以免覆盖清单生成值。
6. 按服务器验收清单确认实际容器镜像、数据库、MCP、SSE、worker 和 RAG；清单生成或容器启动成功不等于验收通过。

```mermaid
flowchart LR
    A[人工运行镜像发布 Action] --> B[构建并推送四个镜像]
    B --> C[查询同一提交的 digest 并校验 revision]
    C --> D[自动生成清单和部署包]
    D --> E[人工下载、上传和解压]
    E --> F[服务器运行 deploy.sh]
    F --> G[Compose 拉取镜像并启动]
```

需要复用已有镜像或修正人工发现的版本错误时，单独运行 `Build release bundle`。输入全部留空时，它通过 Actions API 选择最近一次成功的 `Publish staging images` 所对应的 `develop` 提交，不分别取四个仓库各自的最新镜像。`source_commit` 可填写 7–40 位提交 SHA；工作流 checkout 后记录完整 SHA，部署配置与镜像据此保持同一版本。`release_id` 可手填；四个 `*_sha256` 可分别填写 64 位十六进制 digest，允许 `sha256:` 前缀，未覆盖的项继续自动查询。手动 digest 同样必须属于所选源码提交；切换到另一提交的镜像时需同时修改 `source_commit`。所选提交须包含当前发布工具及对应部署结构，早期不支持部署包的提交不能直接按此方式打包。

没有成功发布记录、任意镜像缺失、仓库访问失败、digest 无效或 OCI revision 不一致时，工作流立即失败，不生成部署包，不自动补建镜像，也不退回旧版本。单独打包不执行镜像构建和推送；新版本仍通过镜像发布 Action 生成。私有 GHCR 镜像需允许本仓库的 `GITHUB_TOKEN` 读取；服务器拉取私有镜像仍需预先执行 `docker login ghcr.io`。

`production.env.example` 只提供服务器普通配置模板；填写后的 `production.env` 与自动生成的 `images.env` 是 Compose 的两个输入文件。参数先经 Compose 的 `environment` 声明进入对应容器，再由 `settings.py`、数据库配置类或 worker 的 `os.getenv` 读取；目前普通参数没有统一的 Python 读取入口。

服务器目录为 `/opt/causalagent/releases/<release_id>/` 加一个 `current` 符号链接；`/etc/causalagent/config/production.env` 保存普通参数，`/etc/causalagent/secrets/causalagent.env` 保存密钥。`verify.sh` 只校验部分静态条件：镜像引用带 digest、密钥权限与必需键名，以及 Compose 配置可解析。它不检查运行中容器、密钥完整格式或部署文件摘要；调用时还需显式用标准 `COMPOSE_FILE` 环境变量选择目标文件，见验收清单。`rollback.sh` 按目录修改时间选择非当前目录并启动其中的 Compose，不记录上一成功发布，也不恢复数据库、密钥或 RAG 数据。

测试环境和生产环境使用同一个 `release.manifest.json`，只替换普通环境配置、密钥文件和数据卷；不替换 app 镜像、MCP 镜像、网关配置包、数据库迁移版本和已验收的第三方镜像 digest。

## 多模态索引目录与挂载


正式 release 的存放位置与运行期读取的向量索引在预发和生产统一由独立命名卷提供。发布接口在 `app` 容器内把通过门禁的 staged index 物化成 `<release_id>/chroma`；运行期各服务按 active pointer 的 `index_path` 打开其中的 `chroma`。预发与生产用同一组环境变量指向卷内路径：

- `MULTIMODAL_INDEX_ROOT`：正式索引根目录，容器内为 `/var/lib/causalagent/rag/formal/indexes`；
- `MULTIMODAL_ACTIVE_INDEX_CONFIG`：active pointer，容器内为 `/var/lib/causalagent/rag/formal/active_index.json`；
- `RAG_PRODUCTION_CONFIG_PATH`：正式发布配置，容器内为 `/var/lib/causalagent/rag/formal/production_rag_config.json`。

这三项都属于可写命名卷 `rag_formal`（预发为 `rag_eval_staging_formal`，生产为 `rag_formal_prod`），由 `app`、`worker` 和 `rag-eval-worker` 共享；索引、指针、发布配置、隔离区和回滚审计都在同一卷内，重建容器不会丢失。评测数据（runs、sources、datasets、artifacts、reports）收敛为评测卷 `rag_eval`（预发 `rag_eval_staging_eval`，生产 `rag_eval_prod`），模型缓存或模型文件使用模型卷 `rag_models`（预发 `rag_eval_staging_models`，生产 `rag_models_prod`）；预发另有 CDFM 权重命名卷 `rag_eval_staging_cdfm_weights`，由一次性 seed 服务从镜像只读副本同步。删除评测数据不会影响正式索引，更新正式索引也不需要重建 app 镜像。

这份索引不能只读挂载。Chroma 的持久层是 SQLite，`PersistentClient` 在构造阶段就要写入跨进程写锁记录（`acquire_write` 表），只读挂载下会抛 `error returned from database: (code: 8) attempt to write a readonly database`，第一次 RAG 查询就降级为 `rag_unavailable`；readiness 检查只读 pointer、manifest、embedding 和目录，不打开向量库，所以这个失败不会在 worker 启动时暴露。release 的字节稳定性由 manifest 校验保证：`_stable_file_chunks` 在计算 `chroma` 目录哈希时已经剔除 `acquire_write` 的建表和插入语句，锁记录不会让 release 校验失败。

开发和兼容副本 Compose 直接挂载仓库内的 `Agent/knowledge_base/multimodal_indexes` 与 `Agent/knowledge_base/multimodal_runtime`，不再使用一次性同步容器，也不再使用 `kb_multimodal_indexes` 命名卷。预发和生产不再挂载宿主仓库目录，也不再从 `./Agent/knowledge_base/multimodal_indexes` 同步。

已有部署采用新卷结构前，需要把现有正式索引和 active 指针转入新位置，否则新卷为空，运行期无法找到原 active release。全新安装没有旧数据时不需要执行迁移；之后切换应用镜像也不需要每次迁移。一次性工具只接收旧宿主目录，把索引复制到卷内 `indexes/`，把 `active_index.json` 复制到卷根目录，并保留旧目录：

```bash
bash scripts/migrate-rag-storage.sh <旧索引目录> <旧 runtime 目录> <命名卷名>
```

该工具不复制 `production_rag_config.json`、旧评测卷或模型目录，也不直接迁移旧索引命名卷。执行前必须定位最新正式数据，停止相关写入并保留备份；目标参数需使用实际 Docker 卷名。正式发布配置、评测数据和模型是否保留须分别处理，复制后还需验证 manifest、active 指针相对路径和真实查询。脚本不检查目标为空、不提供复制后校验或原子切换，重复执行可能覆盖同名文件。

发布新 release 后按响应中的 `requires_worker_restart=true` 重启读取索引的服务。开发发布直接写入宿主仓库目录；使用 `restart` 明确重启已有容器，普通 `up -d` 在配置未变化时不保证重启：

```bash
docker compose -f docker-compose.yml restart app worker rag-eval-worker
```

`docker compose down -v` 会连同 MySQL、PostgreSQL、SearXNG 和正式 RAG 卷一起删除，任何时候都不要使用。正式 RAG 卷需要定期备份；恢复完整的正式卷和发布绑定数据后不需要重新评测，只有正式索引、manifest 或评测绑定丢失时才需要重新构建和评测。

## RAG release 与 worker 生命周期

开发、兼容副本和 staging 的 Agent worker 使用 `JOB_DRAIN_TIMEOUT_SECONDS`（默认 60 秒）进行优雅 drain，并设置至少 75 秒的 Compose `stop_grace_period`。worker 启动只做 active pointer、manifest 冻结来源身份、embedding、版本、collection 和向量目录的轻量 readiness 检查，不读取原始 PDF；失败时继续运行并标记内部 `rag_unavailable`，不加载 RAG 重资源。构建、evaluate、gate-check 和 publish 阶段仍严格要求构建者在受控目录内提供并校验正式 PDF。发布新 release 后必须先完成隔离评测和显式 publish，再通过停止/重启 worker 使 active pointer 生效；不支持热切换、蓝绿切换或零停机。active pointer 发布会保留 previous pointer，candidate/资产/评测产物不自动删除。完整行为契约见 [`../architecture/rag-evaluation.md`](../architecture/rag-evaluation.md)。

生产的模型名、地址、MySQL 账号名和 worker 参数由普通环境配置提供；`SECRET_KEY`、模型 API key、MySQL 和 checkpoint 密码、MCP 鉴权材料只从统一密钥文件读取。不要在文档、镜像层、命令行日志或 API 响应中打印密钥。

## 源码 Release 与 CD 流程

`.github/workflows/lightweight-ci.yml` 只在 PR 上运行，负责代码、前端、Compose、预发部署合同和 Dockerfile 静态检查，并按改动路径选择执行范围；`.github/workflows/publish-staging-images.yml` 只由人工触发构建和推送测试镜像，不部署服务器或执行回滚。`.github/workflows/release-windows.yml` 只负责面向开发者的 Windows Developer Preview 制品，不代表服务端已经完成 CD。
公开说明维护在 [`.github/release-notes`](../../.github/release-notes)，workflow 在对应版本文件存在时优先读取该说明；其他 tag 才使用内置的通用 Draft 文案。

正常发布时，先把 workflow 和目标版本代码合并到 `main`，再创建并推送指向 `main` 历史的严格 SemVer tag。推送 tag 不会自动触发构建；维护者需在 Actions 页面手动运行 `Windows Developer Preview Release`，填写 `target_tag`，并保持 `upload_to_published_release` 关闭。workflow 随后在 GitHub 托管的 `windows-latest` runner 上检出该 tag，创建独立 `.venv-desktop`、运行桌面逻辑测试、构建 onefile、检查冻结通道与桌面环境，并生成 EXE 和 `SHA256SUMS.txt`。只有全部门禁通过后才创建 Draft Pre-release；维护者验收 Draft 后手动发布。构建发生在 GitHub runner，不是在触发者的电脑上。

如果 tag 对应的 Release 已经发布，但附件因 workflow 故障缺失，修复后的 workflow 合并到默认分支后可使用 `workflow_dispatch` 补齐：填写已经存在的 `target_tag`，并显式勾选 `upload_to_published_release`。手动运行使用默认分支上的修复版 workflow，但源码始终重新检出目标 tag，并验证 tag commit 与 `origin/main` 的祖先关系。补齐模式只接受已发布且未锁定的 Release，无论其当前是正式版还是 Pre-release；目标不存在、仍是 Draft、已 immutable 或已有同名附件时均失败，不覆盖现有附件，也不移动或重建 tag。

手动补齐成功后，维护者还应在 Release 页面核对 workflow run、EXE 文件名、`SHA256SUMS.txt`、下载校验结果和 Release Notes。`workflow_dispatch` 只补附件，不自动改写已经发布的说明。Pre-release 不会被 GitHub 选为仓库的 `Latest` 正式 Release；是否转成正式版必须依据产品成熟度单独决策，不能为了侧栏展示而取消预发布标记。



## Windows 桌面壳发布边界

`windows-client/` 是独立的 Windows 桌面依赖和发布边界。它只打包 `Run_causal.py` 与 `causalagent_desktop/` 的 WebView2 壳，不复制 Flask、MySQL、Agent worker、模型、知识库索引、后端 `.env` 或 Docker 依赖；Developer Preview EXE 默认访问本地 loopback Flask 页面，Release EXE 访问构建时固定的 HTTPS 页面和 Flask API。

桌面依赖由 `windows-client/requirements-desktop.txt` 单独锁定，当前基线是 CPython 3.12、`pywebview==5.4`、`pyinstaller==6.22.2` 和显式的 Bottle/pythonnet 依赖。后端 `requirements-base.txt` 保持服务器/Docker 边界，不为桌面壳取消注释桌面依赖。

使用 Windows 构建机和桌面虚拟环境执行 Release 构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-client\build.ps1 `
  -DistributionChannel Release `
  -ReleaseOrigin "https://causalagent.example.com" `
  -PackageMode onedir `
  -IconPath "C:\path\to\CausalAgent.ico"
```

构建脚本会把公开的 Release HTTPS origin 和 `release` 通道标记嵌入 PyInstaller 包；冻结后的 EXE 强制 release、关闭 debug/开发者工具，不能以本地 `127.0.0.1` 为默认地址。`-IconPath` 必须是正式 `.ico`；`build/`、`dist/` 和生成的 `.spec` 仅为本地构建产物。目标机必须预先安装 Microsoft Edge WebView2 Runtime，运行时用户数据目录固定为 `%LOCALAPPDATA%\CausalAgent\WebView`，以保留 Cookie/localStorage。

Developer Preview 构建使用以下命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-client\build.ps1 `
  -DistributionChannel DeveloperPreview `
  -PackageMode onefile
```

该通道嵌入 `developer-preview` 标记，冻结包默认访问 `http://127.0.0.1:5001/`，只接受 loopback origin 并强制关闭 debug。`CAUSALAGENT_DESKTOP_TEST_AUTOCLOSE_SECONDS` 只对 source development smoke 生效，冻结的 Developer Preview/Release 包会忽略它。它不包含后端或 Docker 服务；本地真实 WebView2 smoke 只证明本地 stub、Edge Chromium 和窗口生命周期，不证明公网服务、模型、SSE 或生产 RAG。

桌面壳的顶层导航只允许配置 origin；外部 HTTPS 交给系统浏览器，`file://`、`javascript:`、未知协议、外部 HTTP 和其他 origin 会被取消。服务器前端更新不需要重新打包 EXE，重新加载同一 HTTPS 页面即可获取新版页面。桌面发布不新增专用 API、Token 鉴权或 JS-Python 通用桥。

## 管理员产物

本地非 Docker 发布前必须在 `admin-frontend/` 执行 typecheck、unit 和 build。管理员端构建通过 Vite/TypeScript 别名读取 `packages/design-system/src`，Docker `admin-builder` 在安装 npm 依赖前复制共享包源码。未设置 `ADMIN_VITE_DEV_SERVER_URL` 时，Flask 从 `admin-frontend/dist/`（或 `ADMIN_FRONTEND_DIST_DIR` 指定目录）提供 `/admin/`；目录缺少 `index.html` 时返回带 request ID 的 503 和 `admin_frontend_missing`。Docker 运行镜像从 `/opt/causalagent-admin` 提供构建结果。

官网、聊天端和管理员端的 `dist/` 由 `.gitignore` 与 `.dockerignore` 排除。RAG 评测台的 `app/rag_eval/frontend_dist/` 通过 `.gitignore` 的显式例外保留在版本库，供本地 Flask 静态页面和入口契约使用；Docker 构建上下文排除这份副本，并由 `rag-eval-builder` 从当前源码生成部署产物。开发热更新才显式启动 Vite，生产不要把 Vite 端口作为后端依赖。

## 数据库发布顺序

开发/预发空库或数据库环境重建时先启动依赖数据库，再运行 `Database.bootstrap` 完成 Alembic 和 checkpoint setup，确认成功后才启动 app/worker/monitor/agent-persistence-cleanup/rag-eval-worker。当前唯一 Alembic head 是 `e1f2a3b4c5d6`；清理 worker 还要求 Agent worker 至少完成一次启动以初始化官方 Store schema。具有破坏性的 checkpoint/file migration 不会自动回填旧数据，执行 downgrade 必须选择明确 revision，并在隔离环境先验证往返。迁移风险和 preflight 规则见 [`../database/migrations-checkpoints.md`](../database/migrations-checkpoints.md)。
