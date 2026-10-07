# 构建与部署

文档职责：记录 CausalAgent Docker 镜像构建、开发/生产 Compose 拓扑、初始化顺序和管理员静态产物的发布边界。

适用范围：修改 Dockerfile、Compose 服务、镜像启动命令、生产环境变量或发布流程时使用；数据库内部迁移和 checkpoint 细节见 [`../database/migrations-checkpoints.md`](../database/migrations-checkpoints.md)。

## 镜像构建

`Dockerfile` 当前有六个重要阶段：

1. `python-deps` 按 `tests/smoke/requirements-deep-agent-py311-linux.lock` 的 hash 锁定安装全部 Python 依赖（含 CPU PyTorch），不使用 `requirements-base.txt` 与 `requirements.txt` 在线求解。
2. `test` 在共享依赖上安装 `requirements-test.txt`，默认执行 `tests/unit`。
3. `admin-builder` 构建管理员前端，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-admin`。
4. `chat-builder` 构建普通用户应用，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-chat`。
5. `website-builder` 构建官网前端，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-website`。
6. `rag-eval-builder` 构建 RAG 评测台，连同共享设计系统字体源码一起构建，产物复制到 `/opt/causalagent-rag-eval`。

四个前端的 Vite 构建会把 Geist Sans 和 Noto Sans SC 的 WOFF2 子集放入各自的资源目录，并把字体对应的 OFL 文件复制到 `assets/font-licenses/`，文件名包含内容摘要。许可文本通过各前端已有的资源路由提供，与同目录带 hash 的字体资源一样可以安全使用 immutable 缓存；发布时字体与许可证必须一起保留。

最终运行镜像不包含 Node、npm，不启动 Vite，不开放 Node 端口；Gunicorn 默认绑定 `0.0.0.0:5001`，由 `WEB_WORKERS`、`WEB_THREADS` 和 `WEB_TIMEOUT` 调整 Web 进程参数。

四个前端各自使用独立的运行时目录，Compose 默认指向镜像内产物，源码卷不能覆盖这些目录：官网 `WEBSITE_FRONTEND_DIST_DIR=/opt/causalagent-website`，普通用户应用 `CHAT_FRONTEND_DIST_DIR=/opt/causalagent-chat`，RAG 评测台 `RAG_EVAL_FRONTEND_DIST_DIR=/opt/causalagent-rag-eval`，管理员系统 `ADMIN_FRONTEND_DIST_DIR=/opt/causalagent-admin`。

四套入口的页面地址与资源前缀是：官网 `/`、`/product`、`/pricing`、`/about`、`/docs`、`/changelog`、`/auth/sign-in`、`/auth/sign-up` 与 `/site-assets/`；普通用户应用 `/dashboard*` 与 `/dashboard-assets/`；RAG 评测台 `/rag-eval` 与 `/rag-eval/assets/`、`/rag-eval/brand/`；管理员系统 `/admin*` 与 `/admin/assets/`、`/admin/brand/`。入口 HTML 不缓存，带 hash 的 `assets/` 资源使用长期 immutable 缓存；任一 dist 缺失时它的页面入口和资源路径统一返回带 request ID 的 503（`website_frontend_missing`、`chat_frontend_missing`、`rag_eval_frontend_missing`），不回退到其他前端。

## 开发部署

默认 `docker-compose.yml` 是包含主系统、RAG 评测、联网搜索和可观测性的 16 服务开发拓扑；服务职责如下：

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
| `kb-indexes-sync` | 一次性同步，把仓库里的多模态 release 复制进命名卷 `kb_multimodal_indexes`；作为 app、worker、rag-eval-worker 的启动依赖先运行 |
| `searxng` / `valkey` | 固定版本的 SearXNG 联网学术搜索及其缓存/队列依赖 |
| `loki` / `alloy` / `grafana` | 开发环境运行日志采集、存储和查看；只加入独立的 observability network |

`app`、Agent worker、`causal-mcp`、monitor、RAG evaluation worker 和 agent-persistence-cleanup 依赖 `db-bootstrap` 成功退出；`causal-mcp` 另外等待 `mysql-primary` 健康，并以 `/health`/`/ready` 提供进程、MySQL strong read 和执行器就绪边界。`causal-mcp` 不映射宿主端口，算法在有界容量内按 invocation 使用独立子进程，因此控制面取消只终止目标算法，不回收并行 sibling；`CAUSAL_MCP_SLOW_LOG_SECONDS` 在开发、预发和生产 Compose 中默认 60 秒。因果 MCP 镜像先安装 CPU Torch，再安装固定版本 `cdfm-base==0.1.0` 与现有 CDMIR；`CDFM_MODEL_PATH` 默认指向 `DMIRLAB/CDFM`，开发 smoke 可从 Hugging Face 加载，生产仍应通过不可变镜像/模型内容治理避免漂移。服务通过 `config/database_settings.py` 读取 MySQL 配置，只注入自身 Bearer/HMAC 密钥和算法参数，不注入应用的 `API_KEY`、`BASE_URL`、`MODEL` 或 `SECRET_KEY`；Bearer/HMAC 密钥只通过环境变量或部署 secret 注入。当前 worker 新路径还依赖 `causal-mcp` healthy，启动时按“PostgreSQL checkpoint pool/schema → AsyncPostgresStore setup → 静态 Registry → 进程级 MCP Client pool handshake → RAG readiness → Deep Agent/父图编译”的顺序完成 fail-fast 初始化；slot 不再创建 stdio MCP session。`searxng` 依赖 Valkey 健康。联网搜索是 Job 级可选能力，worker readiness 不等待 SearXNG，运行期不可用时新 `web_evidence_search` 返回受控 unavailable/disabled 结果；非搜索 Job 不会因此阻止启动。开发拓扑当前不提供自动故障切换。启动命令见 [`setup.md`](setup.md)。

## 联网搜索（SearXNG）

开发与预发 Compose 使用同一套密钥注入方式：`searxng/core-config/settings.yml` 与 `deploy/staging/searxng/config/settings.yml` 都是提交在仓库里的非密钥配置，只包含引擎白名单、JSON 输出、Valkey 地址等字段，不写 `secret_key`。密钥与其他密钥一致，由环境变量注入：开发 Compose 默认 `local-searxng-secret`，预发由 `.env.staging` 的 `SEARXNG_SECRET` 显式提供。SearXNG 直接读取该变量并覆盖配置文件中的同名字段，因此预发密钥只存在于服务器本地环境文件，不进入 Git。

开发与预发 Compose 都固定使用 `searxng/searxng:2026.8.21-bbb3c7d82`。升级镜像时必须重新验证 `settings.yml`、JSON 输出格式、三学术引擎配置和 `/healthz`，并确认 `SEARXNG_SECRET` 仍会覆盖文件中的同名字段；不要直接改回 `latest`。`/healthz` 只检查 SearXNG Web 进程和配置加载后的 HTTP 响应，不检查外部学术引擎、DNS 或出站网络；真实搜索可用性仍由 web_search 的运行期重试和降级处理。

开发 Compose 的可观测组件使用固定版本和独立命名卷：Loki、Alloy 不开放宿主机端口，Grafana 只绑定 `127.0.0.1:3000` 并以 `/grafana/` 为入口子路径。启动 Grafana 前必须设置非空的 `GRAFANA_ADMIN_PASSWORD`；采集范围由应用容器的 `causalagent_observability` 标签筛选，不包含 MySQL、PostgreSQL、Loki、Alloy 或 Grafana 自身。完整字段、标签和真实验收边界见 [`observability.md`](observability.md)。

## 预发部署

`docker-compose.staging.yml` 是隔离预发拓扑，包含网关、应用、Agent 与 RAG worker、私有 MCP、MySQL 主从、PostgreSQL checkpoint、SearXNG/Valkey 和 Loki/Alloy/Grafana。数据库、搜索、Grafana 和观测数据都使用 staging 专属命名卷；应用服务与观测服务分属两个 Docker 网络。所有 Python 服务先通过 `scripts/staging_environment_guard.py` 校验项目/DSN/数据库/卷名中的 production/prod 标识，`db-bootstrap` 成功后才启动应用服务；worker 还等待 `causal-mcp` healthy，并要求显式的 Deep Agent model/base/API key/context window 与 MCP service token/signing key。SearXNG 使用 `deploy/staging/searxng/config/` 下已提交的非密钥 `settings.yml`。Grafana 数据保存在命名卷 `rag_eval_staging_grafana`，不依赖宿主目录权限。多模态 index 使用可写命名卷 `kb_multimodal_indexes_staging`，active/previous runtime、assets 与 retrieval policy 仍为只读挂载。

公网只发布 gateway 的 8088 端口。Grafana 不再映射宿主端口，由 gateway 反代到同源 `/grafana/`；SearXNG、Valkey、Loki、Alloy、MySQL、PostgreSQL、app、worker 和 MCP 均不发布宿主端口。该入口是明文 HTTP，仅用于测试服务器，管理员凭据会在链路上以明文传输；云安全组和主机防火墙只允许需要的公网入口端口。

### CI 与人工镜像发布

`.github/workflows/lightweight-ci.yml` 只在面向 `main`/`develop` 的 PR 上运行门禁，并按改动路径选择后端单元测试、五个前端工程自检/构建、Compose 与预发部署合同、Alloy 配置验证和四个 Dockerfile 的 BuildKit 静态检查；面向 `main` 的 PR 无条件运行全部检查。`push` 到 `main`/`develop` 不运行门禁，只重建 Docker unit 测试镜像以导出层缓存。两处流程都不推送镜像。完整镜像构建只由 `.github/workflows/publish-staging-images.yml` 的 `workflow_dispatch` 触发，并拒绝 `develop` 以外的分支；它把 app、MCP、MySQL primary、MySQL replica 分别构建并直接推送 GHCR，不创建镜像 artifact，也不通过 SSH 部署服务器。每个镜像使用 `sha-<commit 前 7 位>` 标签，摘要中同时给出 `sha256` digest；服务器配置必须使用 digest。

仓库默认分支是 `main`，因此首次手动触发前，发布 workflow 文件必须先合并到 `main`；触发时在 Actions 页面选择 `develop`。公开仓库的公开 GHCR Container 镜像按 GitHub Packages 计费规则免费存储和传输（见 [GitHub Packages 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-packages)）。新建 GHCR 包后，维护者需在包设置中将可见性改为 Public，之后测试服务器才可匿名拉取。构建使用普通 `ubuntu-latest` runner，并在构建前后记录磁盘余量；MCP 首次构建是否能在 runner 可用磁盘内完成，以该次 workflow 实际结果为准。镜像直接推送 GHCR，不导出为 Actions artifact。

GitHub 当前的公开 Linux x64 标准 runner 配置为 4 核、16 GB 内存和 14 GB SSD，公开仓库使用标准 runner 不计费；具体规格以 [GitHub-hosted runners 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) 为准。GHCR 第一次发布的新包默认为 Private，维护者需按 [包访问与可见性说明](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility) 手动改成 Public。

服务器首次部署时，在仓库目录复制模板并只在服务器编辑真实配置：

~~~bash
cp .env.staging.example .env.staging
chmod 600 .env.staging
~~~

把四个 STAGING_*_IMAGE 替换为发布摘要中的完整 GHCR @sha256:... 值；为 MySQL root/app/write/read/replication、PostgreSQL、Flask、模型、视觉、Embedding、MCP、SearXNG 和 Grafana 设置互不重复的真实凭据，并把 SearXNG 的 `SEARXNG_SECRET` 填为独立随机值。不要在服务器以外复制 .env.staging，也不要把它提交到 Git。.gitignore 会排除该文件，.dockerignore 也会排除环境文件与本地数据，避免本地构建时把它们放进镜像上下文。

~~~bash
docker compose --env-file .env.staging -f docker-compose.staging.yml config --quiet
docker compose --env-file .env.staging -f docker-compose.staging.yml pull
docker compose --env-file .env.staging -f docker-compose.staging.yml up -d
~~~

不要运行 docker compose down -v；它会删除预发数据库、checkpoint、搜索和观测数据。首次启动后还需在云安全组与主机防火墙核实只有 gateway 端口对公网开放。

## 生产部署

`docker-compose.prod.yml` 使用生产 MySQL、PostgreSQL checkpoint、统一 bootstrap、Web、Agent worker、独立 `causal-mcp`、monitor、checkpoint cleanup 和独立 `rag-eval-worker`；生产环境不挂载源代码，使用独立卷、网络和日志轮转设置。worker 通过进程级 MCP Client pool 调用私有服务，必须显式提供不可变 Deep Agent 配置和 MCP 鉴权材料；它依赖 `causal-mcp` healthy 后才开始 claim Job。`causal-mcp` 使用独立不可变镜像、单 ASGI worker、默认最多 2 个并行 invocation 和 4 个等待队列，不开放宿主端口；每个已运行 invocation 使用独立单进程 executor 以支持精确取消。镜像内的 CDMIR 与 `cdfm-base==0.1.0` 固定版本，并使用 CPU Torch 依赖闭合 `pip check`；`CDFM_MODEL_PATH` 由部署环境显式传入。RAG evaluation worker 与主系统进程隔离，并通过独立评测卷共享必要的运行产物；它不带开发可观测性标签，不应把评测日志混入主系统观测流。当前生产 Compose 是单独的生产配置，不能假设它自动提供开发 Compose 的 MySQL replica、SearXNG、Loki/Alloy/Grafana 或故障切换能力。
当前生产 Compose 未定义 SearXNG 服务；如果生产环境启用 `web_search_enabled`，必须另外提供可访问的 `SEARXNG_URL` 和对应的搜索服务部署，搜索不可用时仍遵循 worker 运行期降级语义。生产 readiness 和发布证据必须按 [`testing.md`](testing.md) 重新执行并记录，不能用单元或静态 Compose 检查替代。

## 多模态索引目录与挂载

`Agent/knowledge_base/multimodal_indexes/` 同时承担两个角色：它是正式 release 的存放位置，也是运行期读取的向量索引。发布接口在 `app` 容器内把通过门禁的 staged index 物化成 `<release_id>/chroma`；运行期各服务按 `multimodal_runtime/active_index.json` 的 `index_path` 打开其中的 `chroma`。

这份索引不能只读挂载。Chroma 的持久层是 SQLite，`PersistentClient` 在构造阶段就要写入跨进程写锁记录（`acquire_write` 表），只读挂载下会抛 `error returned from database: (code: 8) attempt to write a readonly database`，第一次 RAG 查询就降级为 `rag_unavailable`；readiness 检查只读 pointer、manifest、embedding 和目录，不打开向量库，所以这个失败不会在 worker 启动时暴露。release 的字节稳定性由 manifest 校验保证：`_stable_file_chunks` 在计算 `chroma` 目录哈希时已经剔除 `acquire_write` 的建表和插入语句，锁记录不会让 release 校验失败。

开发、兼容副本、预发和生产都把这条路径挂到可写命名卷：开发和兼容副本 Compose 使用 `kb_multimodal_indexes`（`app`、`worker`、`rag-eval-worker`），预发使用 `kb_multimodal_indexes_staging`（`app`、`worker`），生产使用 `kb_multimodal_indexes_prod`（`app`、`worker`）。四份 Compose 各有一个一次性 `kb-indexes-sync` 服务，把宿主目录里的 release 复制进卷，并作为上述服务的 `service_completed_successfully` 依赖在启动前运行；开发和兼容副本从仓库根目录复制，预发和生产从宿主 `Agent/knowledge_base/multimodal_indexes/` 复制。复制是合并式的，不删除卷内已有的 release。

卷内容与宿主 release 必须指向同一个 release id。指针文件 `multimodal_runtime/active_index.json` 仍从宿主目录读取，不在卷内；两侧不一致时 `_resolve_multimodal_release` 会因找不到目录而抛错、RAG 不可用，如果指针里存在 fallback 项还会触发自动回退，改写指针并把原 active release 目录移入 `quarantine/`。需要在不重启整套服务的前提下重新同步时执行：

```bash
docker compose run --rm kb-indexes-sync
```

发布新 release 后仍按既有约定重启读取索引的服务（发布响应的 `requires_worker_restart=true`）。开发环境在容器内发布的新 release 只写入卷，需要导出到仓库后再提交：

```bash
docker cp causalagent_app:/app/Agent/knowledge_base/multimodal_indexes/<release_id> ./Agent/knowledge_base/multimodal_indexes/
```

卷内容落后于宿主目录时按显式步骤重建，而不是依赖 `down -v`：停止读取索引的服务，删除对应命名卷（开发为 `<项目名>_kb_multimodal_indexes`，预发和生产分别为 `<项目名>_kb_multimodal_indexes_staging`、`<项目名>_kb_multimodal_indexes_prod`），再重新 `up`，卷会先由 `kb-indexes-sync` 从宿主目录填充。`docker compose down -v` 会连同 MySQL、PostgreSQL、SearXNG 数据卷一起删除，任何时候都不要使用。

## RAG release 与 worker 生命周期

开发、兼容副本和 staging 的 Agent worker 使用 `JOB_DRAIN_TIMEOUT_SECONDS`（默认 60 秒）进行优雅 drain，并设置至少 75 秒的 Compose `stop_grace_period`。worker 启动只做 active pointer、manifest 冻结来源身份、embedding、版本、collection 和向量目录的轻量 readiness 检查，不读取原始 PDF；失败时继续运行并标记内部 `rag_unavailable`，不加载 RAG 重资源。构建、evaluate、gate-check 和 publish 阶段仍严格要求构建者在受控目录内提供并校验正式 PDF。发布新 release 后必须先完成隔离评测和显式 publish，再通过停止/重启 worker 使 active pointer 生效；不支持热切换、蓝绿切换或零停机。active pointer 发布会保留 previous pointer，candidate/资产/评测产物不自动删除。完整行为契约见 [`../architecture/rag-evaluation.md`](../architecture/rag-evaluation.md)。

生产必须通过环境变量或安全的 secret 机制提供 `SECRET_KEY`、模型配置、MySQL 职责账号、非空 `CHECKPOINT_POSTGRES_PASSWORD` 和 RAG evaluation worker 所需配置。不要在文档、镜像层、命令行日志或 API 响应中打印密钥。

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

开发/预发空库或数据库环境重建时先启动依赖数据库，再运行 `Database.bootstrap` 完成 Alembic 和 checkpoint setup，确认成功后才启动 app/worker/monitor/agent-persistence-cleanup/rag-eval-worker。当前唯一 Alembic head 是 `d0e1f2a3b4c5`；清理 worker 还要求 Agent worker 至少完成一次启动以初始化官方 Store schema。具有破坏性的 checkpoint/file migration 不会自动回填旧数据，执行 downgrade 必须选择明确 revision，并在隔离环境先验证往返。迁移风险和 preflight 规则见 [`../database/migrations-checkpoints.md`](../database/migrations-checkpoints.md)。
