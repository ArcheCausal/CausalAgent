# 测试与验证

文档职责：定义后端、管理员前端、集成迁移和隔离主从/PostgreSQL 验收的验证矩阵与稳定命令。

适用范围：修改代码、迁移、管理员页面或文档中命令时使用；管理员专项测试补充说明见 [`../admin/testing.md`](../admin/testing.md)。

## 测试层级

| 层级 | 位置/入口 | 主要证明 |
| --- | --- | --- |
| 后端 unit | `tests/unit/` | 单模块状态机、权限、Job fencing、结构化输出和 monitor 逻辑 |
| 后端 integration | `tests/integration/` | 多个实际组件之间的数据传递、生命周期和协作行为；真实数据库用例放在 `tests/e2e/` |
| 静态契约 | `tests/contracts/` | Compose、Dockerfile、发布 workflow、migration 图与日志政策的跨文件一致性 |
| RAG/多模态与隔离评测 | `tests/unit/agent/test_multimodal_*.py`、`tests/unit/rag_eval/`、`tests/acceptance/` | 来源/索引/release 契约、隔离队列与评测矩阵的分层检查 |
| 管理员 Vue unit | `admin-frontend/tests/*.spec.ts` | API DTO、组件、看板/设置语义和 SQL digest 展示 |
| 管理员 Mock E2E | `admin-frontend` `test:e2e:mock` | 无真实数据库的页面导航、鉴权和交互 |
| 普通用户应用 Vue unit/contract | `chat-frontend/tests/unit`、`tests/contract` | SSE parser、游标、Job reducer、公开决策与明细补绘、展示推进纯函数、地址路由、幂等和 Flask JSON schema |
| 普通用户应用 Vue component/Mock E2E | `chat-frontend/tests/components`、`tests/e2e-mock` | Composer、消息时间线、假流式草稿与公开决策展示，以及未登录跳转、已登录创建 Job/SSE 终态、会话地址、设置页和退出登录 |
| 官网 Vue unit | `website-frontend/tests/unit` | 路由解析、登录回跳白名单、认证客户端和登录表单交互 |
| RAG 评测台前端 | `app/rag_eval/frontend/test` | 审核工作流纯函数（`npm test`）、类型检查与生产构建 |
| 共享设计系统组件 unit | `packages/design-system/tests` | 组件变体与状态类名、标签页键盘行为、输入控件的无障碍关联、空态/加载态/错误态的角色与进度声明 |
| 前端页面入口 Flask 行为 | `tests/integration/deployment/test_frontend_entries.py` | 四套入口的页面与资源路由、未登录跳转、权限拒绝、dist 缺失 fail-closed、缓存头和开发服务器接管 |
| 前端构建与部署契约 | `tests/contracts/deployment/test_frontend_entrypoints.py`、`test_admin_deployment.py` | Docker builder/runtime、Compose 变量、产物目录、忽略规则和各工程资源前缀 |
| Windows 桌面逻辑 | `windows-client/tests/test_config.py`、`test_navigation_policy.py`、`test_runtime.py`、`test_launcher.py` | 配置优先级、URL/origin 白名单、运行时错误和 Edge 事件策略，不创建真实窗口 |
| Windows 壳层 smoke | `windows-client/tests/run_windows_smoke.py`、`test_windows_smoke.py` | 隔离 HTTP stub、真实 WebView2 Edge Chromium 页面加载和窗口退出；只在 Windows 桌面会话执行 |
| 隔离 E2E | `tests/run_admin_31_e2e.ps1` / `run_admin_32_e2e.ps1` | 空库升级、migration 往返、主从、PostgreSQL checkpoint、受控写入/删除和普通用户回归 |

`tests/README.md` 是后端测试目录和 Docker 单元测试环境的补充入口。测试层级说明这项测试证明什么，CI 执行组说明它在什么条件下运行，两者不要求一一对应。默认 pytest 范围由 `pytest.ini` 的 `testpaths` 限定为 `tests/unit`、`tests/integration` 和 `tests/contracts`，并排除 `smoke` 与 `db_integration`；`e2e/`、`acceptance/` 和 `smoke/` 使用各自的显式入口。

## GitHub Actions CI

`lightweight-ci.yml` 的验证门禁由面向 `main`、`develop` 的 Pull Request 触发，并按改动路径决定执行范围。`push` 到 `main` 或 `develop` 保留镜像缓存预热，手动触发沿用同一缓存预热语义；预热任务不产生检查结果，合入 Pull Request 后自动写入缓存，供后续 Pull Request 只读恢复。

面向 `develop` 的 Pull Request 执行 Python 语法和 Pull Request 规范检查，并按 `.github/filters/paths.yaml` 选择受影响的后端、前端和契约执行组，多个组可以同时触发。面向 `main` 的 Pull Request 执行全部常规组，并保留只接受 `develop` 来源的分支规则。路径判断失败时汇总检查失败，不能解释为没有相关变更。

| 过滤器 | 覆盖范围 | 触发任务 |
| --- | --- | --- |
| `backend` | CI 与路径规则、`pytest.ini`、公共测试工具、`tests/unit/`、后端组件 integration、`app/**/*.py` 与 `app/*.py`、`Agent/`、`Database/`、`config/`、顶层入口脚本、依赖清单、测试镜像与测试配置 | `backend` 执行组：全部后端 unit 与不依赖真实外部服务的组件 integration |
| `frontends` | `chat-frontend/`、`admin-frontend/`、`website-frontend/`、`app/rag_eval/frontend/`、`app/rag_eval/frontend_dist/`、`packages/design-system/`、`Dockerfile`、`.dockerignore` | 现有五个前端矩阵项 |
| `contracts` | `docker-compose*.yml`、Dockerfile、发布与打包工作流、`tests/contracts/`、`tests/unit/config/`、`tests/unit/deployment/`、迁移目录、可观测与入口路由、环境变量模板 | Compose 与部署契约、前端入口契约、Alloy 配置验证 |
| `build` | `Dockerfile`、`Agent/CausalAgentMCP/Dockerfile`、`Database/mysql/Dockerfile.*`、Compose、`.dockerignore`、依赖清单和依赖锁 | Dockerfile 静态检查 |

只改普通说明文档时，仅基础检查运行，其余组按路径结果跳过。结构化输出、流式配置和 schema 快照等用例统一由 `backend` 执行，没有独立的基础测试任务。`app/rag_eval/frontend/**` 只触发 `frontends` 和需要的契约检查，不因为位于 `app/` 下就启动后端组；`tests/contracts/**` 只触发 `contracts`。`ci-result` 汇总任务核对路径判断的预期结果和实际任务结果：应执行的失败、取消或意外跳过，以及路径判断失败都会使它失败。面向 `main` 的 Pull Request 要求常规组全部完成。

**维护规则**：新增测试必须先放对层级，再更新 `.github/filters/paths.yaml`。跨模块测试要由它读取的生产文件触发，例如部署契约检查 Dockerfile，就必须在 Dockerfile 改变时运行，不能只在契约测试自身改变时运行。`sandbox_controller/**` 变化触发 backend 与 contracts 执行组，Python 语法检查也覆盖该目录；处理镜像的 Dockerfile 与锁定依赖触发 BuildKit 检查。`loadtest/runner/**` 变化触发 backend 执行组，验证日志运行时与压测解析器之间的数据传递；共享 Shell 密钥读取器的行分隔符检查由部署契约组显式执行。共享 CI、路径规则和公共测试配置变化时，宁可触发全部常规组。`tests/contracts/deployment/test_staging_compose.py` 和 `test_searxng_compose.py` 需要 Docker CLI，缺少时只跳过并保留未执行结论。

`main` 与 `develop` 的分支规则集 `mian and dev` 要求 `Python syntax`、`Pull request policy` 和 `CI result` 三个检查。删除或重命名工作流任务时，必须同时修改规则集的必需检查，否则 PR 会一直等待一个不会出现的检查。`CI result` 只在 Pull Request 上运行，push 与手动触发只做缓存预热。

Docker unit 镜像按 `tests/README.md` 中的固定 lock 构建，由 `docker/bake-action@v6` 执行 Bake，以获得 GitHub Actions 提供的缓存运行时令牌和服务地址；无需把令牌写入工作流或服务器配置。两个构建步骤使用 GHA v2 层缓存和同一 `scope=unit-test`：Pull Request 只通过 `cache-from` 恢复并将镜像加载到 Docker，`push` 或手动启动的预热任务通过 `cache-to` 写入，使用 `mode=min` 和 `type=cacheonly`，不加载或推送镜像。构建记录附件上传关闭，构建日志与摘要保留。运行测试仍使用 `docker compose -f docker-compose.test.yml run --rm unit-test`，`docker-compose.test.yml` 保持本地与 CI 共用、不含仅 CI 可用的缓存参数，隔离契约（`network_mode: none`、只读挂载、`env_file`）不变。缓存上限与保留期由 GitHub 侧管理，公开仓库使用标准托管 runner 的用量不计费。

验证缓存时，应先在目标基础分支成功运行一次预热任务，确认日志出现 GHA 缓存导出记录，再查看后续 Pull Request 日志的缓存导入和依赖层 `CACHED` 记录。第一次预热或依赖锁、Dockerfile、基础镜像发生变化时，依赖层可能重新构建；任务成功或配置中存在 `cache-from/cache-to` 不代表缓存已实际写入或命中。

Compose 校验只展开配置，不启动服务。静态 Dockerfile 按构建相关路径触发，不会生成或推送镜像；实际 app、MCP、MySQL 主从和数据处理镜像只由人工触发的发布工作流构建。

发布工作流的静态契约及镜像解析器参数失败路径由 `tests/contracts/deployment/test_release_workflow.py` 检查，包括五个构建全部成功后才打包、同一源码提交传递、处理镜像的构建上下文与平台、本次构建 digest 的任务输出传递、独立打包的手动输入、无效输入不写清单，以及拒绝无法验证 revision 的元数据。该文件不模拟 GHCR 成功响应；通过静态检查不代表仓库鉴权、真实镜像查询或 GitHub Actions 执行成功。真实发布需在 Actions 中检查五个 digest、完整源码提交、处理镜像引用传递、制品上传及服务器验收。

本地复现预发静态检查：

~~~bash
docker compose --env-file .env.staging.example -f docker-compose.staging.yml config --quiet
python -m pytest -p no:cacheprovider tests/contracts/deployment/test_staging_compose.py
~~~

轻量 contracts 执行组中的 `tests/unit/config/test_secret_file.py` 只验证密钥文件与基础配置，不导入完整服务。控制服务导入及数据库密钥隔离由 [test_controller_configuration.py](../../tests/integration/config/test_controller_configuration.py) 在 backend 镜像中验证；`.github/filters/paths.yaml` 显式覆盖 `tests/integration/config/**`，新增同类测试时保持这一归属。

## 普通用户 Vue 前端

在仓库根目录执行：

```powershell
Push-Location chat-frontend
npm ci
npm run lint
npm run typecheck
npm run test:unit
npm run test:components
npm run test:e2e:mock
npm run build
npm run check
Pop-Location
```

`npm run check` 是普通用户应用的代码级门槛；当前实现覆盖 Lint、类型检查、unit/contract、组件测试、Mock Playwright E2E 和 Vite 生产构建。Mock E2E 覆盖未登录访客跳转 `/auth/sign-in?next=/dashboard`、登录后的工作区与真实 Job/SSE 终态、`/dashboard/settings` 与 `/dashboard/session/<id>` 地址行为，以及退出登录回到 `/`。官网前端使用 `website-frontend` 目录的 `npm run check`；RAG 评测台使用 `app/rag_eval/frontend` 目录的 `npm run typecheck`、`npm test` 和 `npm run build`；管理员端沿用 [`../admin/testing.md`](../admin/testing.md) 的命令。Mock E2E 不证明真实 Flask、Cookie Session、MySQL/PostgreSQL、worker、文件上传、模型、Chrome/Edge 双浏览器或桌面壳。

`playwright.mock.config.ts` 在本地默认复用 5174 端口上已有的开发服务器（`reuseExistingServer`），运行前必须确认该端口服务的是当前工作区；若该端口被其他检出占用，应在确认后改用临时端口的等价配置，或停止占用该端口的进程。

页面入口的 Flask 行为与构建产物契约由 `tests/integration/deployment/` 和 `tests/contracts/deployment/` 共同覆盖；运行镜像 builder/runtime 仍需本地或发布前额外核对，CI 的 Dockerfile 检查仅验证 BuildKit 静态规则：

```powershell
python -m pytest -p no:cacheprovider tests/integration/deployment/test_frontend_entries.py tests/contracts/deployment/test_frontend_entrypoints.py
python -m py_compile app/main/routes.py app/chat/page_routes.py app/rag_eval/page_routes.py config/settings.py tests/integration/deployment/test_frontend_entries.py tests/contracts/deployment/test_frontend_entrypoints.py
docker compose -f docker-compose.yml config --quiet
docker compose --env-file .env.staging.example -f docker-compose.staging.yml config --quiet
docker compose -f docker-compose.prod.yml config --quiet
```

真实验收必须在可用的 Flask、数据库、worker 和合成/授权模型环境中另行完成。至少要记录官网注册登录与回跳、普通用户应用的 Session/文件、普通 Job、Thinking/图/Markdown、断线恢复、active Job、waiting_input/resume、运行态停止对账、错误路径、快速切换、RAG 评测台的管理员与普通用户差异、管理员后台、Chrome/Edge、四视口和桌面壳结果。入口拆分是当前代码事实，但不能由自动化测试推导为真实生产验收通过。

四个前端 dist 缺失需分别验证稳定的 503、对应的 `*_frontend_missing` 错误码和 request ID；`/chat-next`、`/rag_eval` 和 `/static/rag_eval_app/...` 应返回 404，三套 Compose 文件中不得再出现 `CHAT_FRONTEND_ENTRY`。

## Windows 桌面客户端

桌面逻辑测试不启动窗口，也不连接 Flask、MySQL、模型或 WebView2 Runtime：

```powershell
.\.venv-desktop\Scripts\python.exe -m pip install -r .\windows-client\requirements-desktop-test.txt
.\.venv-desktop\Scripts\python.exe -m pytest windows-client/tests/test_config.py windows-client/tests/test_navigation_policy.py windows-client/tests/test_runtime.py windows-client/tests/test_launcher.py -q
```

桌面依赖验收使用独立虚拟环境；从仓库根目录执行兼容入口检查，必须确认 Python 3.12、Windows、`pywebview==5.4` 的完整依赖和 WebView2 Runtime：

```powershell
.\.venv-desktop\Scripts\python.exe .\Run_causal.py --check-environment
.\.venv-desktop\Scripts\python.exe -c "import webview; print('ok')"
```

真实壳层 smoke 不放入普通 pytest 默认执行范围。它会启动本地 stub（`/`、`/health`、`/external-link`）、创建真实 Edge Chromium 窗口，并使用测试专用自动关闭钩子验证窗口关闭和进程退出：

```powershell
.\.venv-desktop\Scripts\python.exe .\windows-client\tests\run_windows_smoke.py
```

也可以在已有 pytest 环境中显式运行 `test_windows_smoke.py`，但必须设置 `CAUSALAGENT_DESKTOP_RUN_SMOKE=1`；未设置时的 skip 不是 smoke 通过证据。壳层 smoke 通过只证明桌面壳与 stub 的加载/退出链路，不证明远程生产 origin、服务器 Session、真实 SSE、模型或后端 API 已验收。

## 后端 Docker 单元测试

RAG State 隔离和异常分流的单元测试位于 `tests/unit/agent/test_rag_subgraph_state.py`，覆盖 Planner 预检跳过 ToolNode、查询失败与协议错误标记、`success=False` Parser 路径、父 State 投影和取消/撤销传播。该测试使用 fake LLM、fake RAG tool 和导入桩，不覆盖真实模型、真实 MCP session、真实知识库向量检索或 PostgreSQL checkpoint。

测试镜像基于 Dockerfile 的 `test` target：Python 3.11 Linux 运行依赖从 `tests/smoke/requirements-deep-agent-py311-linux.lock` 以 `--require-hashes` 安装，随后再安装 `requirements-test.txt`。`unit-test` 服务不依赖 app/worker/monitor/MySQL，关闭容器网络，只读挂载仓库，并通过 Compose `env_file` 注入 `tests/unit-test-env`；这些已注入环境变量优先于项目 `.env`：

```bash
docker compose -f docker-compose.test.yml build unit-test
docker compose -f docker-compose.test.yml run --rm unit-test
```

运行指定测试：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/admin
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_job_lifecycle.py
```

## Deep Agent 定向验证

### OpenSandbox 数据处理验证

方案、确认快照与执行说明的纯规则测试在 [test_data_processing_lifecycle.py](../../tests/unit/agent/test_data_processing_lifecycle.py)，真实 ToolNode、StateGraph 和 InMemorySaver 的状态传递检查在 [test_data_processing_plan_state.py](../../tests/integration/agent/test_data_processing_plan_state.py)。它们检查相同方案保留确认、方案变化清空绑定、脚本变化要求重新执行、执行说明事实绑定及结束纠正预算，不使用模型或外部服务替身，也不证明 MySQL、PostgreSQL 或 OpenSandbox 已验证。

[test_role_column_mapping.py](../../tests/integration/agent/test_role_column_mapping.py) 覆盖中文研究角色到英文输出列的映射、缺失列、角色冲突、未指定角色及原研究参数身份保留；串联真实 CSV 校验、摘要校验、Deep Agent 投影、本地 DirectLiNGAM/ATE 和父图最终校验，检查缺少 ATE 检查时仍要求补充。它不连接模型、数据库或 OpenSandbox；可将该文件加入下列 Docker pytest 命令执行。

```bash
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_data_processing_lifecycle.py tests/integration/agent/test_data_processing_plan_state.py
```

真实 MySQL/PostgreSQL 入口 `data_processing_recovery.py` 额外覆盖候选保存后调用正式执行说明工具、确认保持有效、说明投影及后续独立校验与绑定。完整模型与 OpenSandbox 入口 `sandbox_upgrade_job.py --scenario execution-report --timeout-seconds 900` 要求真实模型在保存候选后调用说明工具，从子图 checkpoint 检查实际工具顺序，并检查仅确认一次、最终候选与说明一致以及独立校验成功。真实服务不可用时只能报告未执行，不能以离线结果代替。

数据处理验收必须连接真实 OpenSandbox API、真实沙盒主机和真实数据库，不能使用替代 Provider、模拟 API 响应或伪造外网阻断结果。先从真实沙盒验证 Python 3.11 镜像和固定依赖，再逐项检查创建、原始文件只读、脚本执行、输出回读、超时中断、销毁、MySQL 候选持久化/恢复、PostgreSQL 子图恢复以及真实 IPv4/IPv6 出站被拒绝。公网连接探测的超时本身不能作为防火墙配置证据；验收需同时检查沙盒主机上的 egress sidecar、Docker bridge 和转发规则。

真实沙盒应使用独立 Compose project、网络、数据库和临时密钥文件，按 [沙盒部署说明](../../deploy/opensandbox/README.md) 准备固定镜像与代理路由。控制服务、OpenSandbox 和处理容器属于不同验证边界，必须分别检查实际连接和权限；数据库恢复、健康接口或静态检查不能代替完整沙盒验收。上线前还需在隔离环境执行完整模型、Worker 重启接管、MCP HTTP 和报告链路验收。

统一密钥文件的代码与部署检查包括 `tests/unit/config/test_secret_file.py`、`tests/unit/config/test_sandbox_client.py`、`tests/integration/config/test_controller_configuration.py`、`tests/contracts/deployment/test_sandbox_compose.py` 和 `tests/contracts/deployment/test_release_preflight.py`。发布预检使用真实 Bash、Linux root 文件权限和 Docker Compose CLI，只解析配置，不启动服务。真实启动时，Worker、控制服务和 OpenSandbox 管理服务共享 `causalagent.env`，执行用户脚本的沙盒不得接收文件或密钥；两个认证值分别验证有凭据成功、无凭据及错误凭据拒绝。完整 Job 入口 `tests/acceptance/sandbox_upgrade_job.py` 通过 `FULL_JOB_SANDBOX_SECRET_FILE` 读取控制服务所用统一密钥文件中的两个认证值。

方案确认还要验证确认版本与 Job 输入账本一致、用户修改或拒绝后旧方案不会执行、新语义答复或方案说明变化会使旧确认失效，以及已确认语义范围内的脚本修正可以安全恢复。只有真实 OpenSandbox、MySQL、PostgreSQL 和 Worker 的完整链路才能证明生产级恢复及网络隔离；当前工作区缺少这些验收服务时，必须明确保留未验证结论。

无需外部服务的定向检查如下。生命周期测试直接运行真实 StateGraph、节点包装器和 InMemorySaver，覆盖连续提问、按中断 ID 恢复、重新编译图、校验重试及正常暂停不产生失败事件；语义快照测试使用真实校验器、ToolRuntime、本地 MCP 服务和算法进程，不证明数据库或 HTTP。

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps -T unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_sandbox_preview.py tests/unit/agent/test_processing_plan_public_events.py tests/unit/agent/test_cdfm_input_authority.py tests/unit/agent/test_deep_agent_state.py tests/integration/agent/test_data_processing_child_lifecycle.py tests/integration/agent/test_measurement_semantics_snapshot.py -q
```

参数提取证据与失败终态的回归检查如下。它使用真实 Pydantic、LangChain 工具调用解析器、提示词、确定性参数规则、StateGraph、worker 终态构造和公共事件白名单，检查缺失或空证据、说明性证据、完整确认、部分确认、修改部分提议、重复字段、非法测量类型、冲突声明、澄清问题缺失、结构错误与业务错误共享一次纠错重试，以及处理失败不产生成功终态；不把模型输出文字匹配作为校验条件，也不使用模型或数据库替身证明真实模型纠错、数据库事务或浏览器链路。

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps -T unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_parameter_extraction_feedback.py tests/unit/agent/test_research_parameter_revisions.py tests/integration/agent/test_data_processing_failure_terminal.py tests/integration/agent/test_data_processing_child_lifecycle.py -q
```

研究参数与控制服务的定向检查如下。修订测试直接调用真实参数规则；恢复测试使用实际 StateGraph 和 InMemorySaver。协议和客户端配置测试不创建远端沙盒；Compose 与 schema 项只证明静态契约。

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps -T unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_research_parameter_revisions.py tests/integration/agent/test_research_parameter_recovery.py tests/unit/agent/test_sandbox_controller_protocol.py tests/unit/config/test_sandbox_client.py tests/unit/config/test_secret_file.py tests/contracts/database/test_sandbox_lifecycle_schema.py tests/contracts/deployment/test_sandbox_compose.py -q
```

真实 MySQL 生命周期检查位于 [test_sandbox_sessions_mysql.py](../../tests/e2e/database/test_sandbox_sessions_mysql.py)。它只接受显式启用的隔离数据库，按文件顶部说明设置 `SANDBOX_DATABASE_ACCEPTANCE=1`、专用主机名、数据库名与容器内密钥路径，再执行该文件。首次要求空库；已有验收数据库仅在明确设置 `SANDBOX_DATABASE_REUSE=1` 时续跑。该入口检查真实事务、竞争、fencing、操作幂等和迁移，不创建远端沙盒。资源记录不依赖业务级联删除，核查须同时覆盖业务删除后遗留记录仍可清理。

[父图数据库检查](../../tests/e2e/database/test_deep_agent_parent_mysql.py) 与 [会话数据集清理检查](../../tests/e2e/database/test_chat_dataset_cleanup_mysql.py) 复用上述隔离边界。先在持久测试容器中初始化空库，再在同一容器设置 `SANDBOX_DATABASE_REUSE=1` 执行两个模块，保留容器内临时密钥文件：

```bash
python -m pytest -p no:cacheprovider tests/e2e/database/test_sandbox_sessions_mysql.py -k empty_upgrade_explicit_downgrade_and_schema_inspection -q
SANDBOX_DATABASE_REUSE=1 python -m pytest -p no:cacheprovider tests/e2e/database/test_deep_agent_parent_mysql.py tests/e2e/database/test_chat_dataset_cleanup_mysql.py -q
```

这些检查使用真实 MySQL 候选保存、独立校验与绑定、父图强读及 Flask 删除事务，覆盖数据集身份、原生子图恢复、重试事件、checkpoint 清理入队和文件引用保护；子图 checkpoint 使用内存存储，不证明 PostgreSQL、外部模型、MCP HTTP 或 OpenSandbox。默认 CI 的离线 backend 组不运行这些数据库用例。

控制 API 故障验收入口为 [sandbox_controller_http.py](../../tests/acceptance/sandbox_controller_http.py)：在同一隔离 MySQL 边界下显式设置 `SANDBOX_CONTROLLER_HTTP_ACCEPTANCE=1`，执行 `python tests/acceptance/sandbox_controller_http.py`。它使用真实 TCP HTTP、MySQL 和独立控制进程，并连接不可达的 `127.0.0.1:9` 注入上游连接失败，检查认证、请求限制、容量保留、租约撤销及命名锁丢失。报告保存在容器内 `/tmp/sandbox-controller-http/`；失败创建的记录保留且不自动释放。该入口不运行沙盒脚本，不证明真实 OpenSandbox 成功路径。

真实上游入口为 [sandbox_controller_remote.py](../../tests/acceptance/sandbox_controller_remote.py)。它要求同一隔离 MySQL、已就绪的真实 OpenSandbox、容器内独立上游 key 文件与实际处理镜像 digest，同时显式设置 `SANDBOX_CONTROLLER_HTTP_ACCEPTANCE=1`、`SANDBOX_CONTROLLER_REMOTE_ACCEPTANCE=1` 和 `SANDBOX_CONTROLLER_ACCEPTANCE_IMAGE`。`python tests/acceptance/sandbox_controller_remote.py --scenario chain` 检查正式 Worker 客户端、断连创建、初始化、执行、回执、撤销和清理；`--scenario resilience` 检查真实 SIGKILL 后的启动清理及最后一个容量名额的并发。加上 `--upstream-fault` 时，在脚本输出 `stop_upstream` 或 `restore_upstream` 后，操作该次隔离 OpenSandbox 容器，并在对应报告目录创建同名 `.done` 文件确认已经实际停止或恢复；不能只写确认文件代替故障。报告与控制进程日志保留在隔离容器内；故障测试仅作用于该次创建的 Job、操作和远端沙盒。该入口不运行完整 Worker 调度、模型、RAG、MCP 或生成报告的 Job。

真实数据库入口是 [data_processing_recovery.py](../../tests/acceptance/data_processing_recovery.py)，必须在专用内部 Docker 网络中连接全新空库，设置 `TERMINAL_ACCEPTANCE_MYSQL_HOST=recovery-mysql`、`TERMINAL_ACCEPTANCE_POSTGRES_HOST=recovery-postgres` 和唯一的 `DATA_RECOVERY_RUN_ID`，按脚本顶部说明运行。它执行正式 bootstrap、真实 Flask 注册/登录及文件和 Job 服务、方案输入账本、候选重复保存、校验绑定、上下文切换、旧租约写入拒绝和 PostgreSQL 重建连接池恢复；MCP 使用进程内服务和真实 MySQL 强读，检查二值语义拒绝 DirectLiNGAM。额外的原生子图检查在三次新建 PostgreSQL 连接池之间恢复两次提问。报告保存在验收容器 `/tmp/data-processing-recovery/`；同一容器续跑必须使用 `--resume`，另起一轮必须使用新的空库，数据库和原始报告保留。该入口不访问业务数据库，也不执行模型、沙盒脚本、Worker 故障接管或浏览器/HTTP 完整链路。

2026-10-23 的隔离 MySQL/PostgreSQL 执行中，上述 13 个检查项全部通过，包含候选元组/JSON 数组重放返回相同 ID 和 revision、同上下文保留与跨上下文清除数据指针、旧租约拒绝保存/校验/绑定，以及从数据库恢复二值语义后拒绝 DirectLiNGAM。原生子图使用实际编译的 StateGraph，在三次新建 PostgreSQL 连接池之间完成两轮 interrupt/resume。数据库最初以空库创建，修复后在同一隔离环境续跑；这些结果不证明真实模型、OpenSandbox、HTTP、Worker 崩溃接管或生产网络隔离。

新增参数恢复入口为 [research_parameter_recovery.py](../../tests/acceptance/research_parameter_recovery.py)，需先运行原恢复验收，并复用同一次专用环境。它使用明确构造的图输入和真实 MySQL/PostgreSQL，覆盖连续修订、重建连接池、最终上下文重放、候选参数身份、历史上下文保护与旧 checkpoint 拒绝。配套 [research_parameter_tool_sources.py](../../tests/acceptance/research_parameter_tool_sources.py) 通过真实 ToolNode、graph runner、事件写入和 resume 读取答复来源；默认检查真实答复仅进入待处理状态、没有参数写入或隐藏模型调用，并通过统一参数工具检查真实初始账本来源、完整列集合、同源纠正、重复提交、过期草稿拒绝及 PostgreSQL 重连；显式提供真实模型时，经同一参数工具完成提交。

[real_model_parameter_extraction.py](../../tests/acceptance/real_model_parameter_extraction.py) 在上述专用环境中验证统一研究参数契约。模型配置只从容器私有 JSON 读取，字段为 `model/base_url/api_key/use_responses_api/output_version/reasoning`；通过 `--model-config` 指定，默认路径 `/tmp/research-parameter-model.json`，可用 `--initial-model-config` 指定初始场景模型。初始场景复用生产系统提示词和参数工具 schema，由真实模型生成工具参数并执行确定性解析；答复场景由真实模型发起原提问或确认工具调用，再使用真实 MySQL/PostgreSQL、统一参数工具、graph runner 和事件写入。思考模式的历史推理必须来自原模型响应，不补造推理字段。执行 `python tests/acceptance/real_model_parameter_extraction.py --repetitions 2 --max-model-requests 144`。包含全部 11 列的集合声明、角色及范围、取消和完整或部分确认共 13 组场景，分别经过初始、提问答复、方案修改三个入口；两次重复共 78 项。可用可重复指定的 `--scenario` 选择场景。开关思考对照均使用 `auto` 工具选择、相同输出上限和超时；默认 `--max-output-tokens 8192`、`--request-timeout 90`，输出预算包含推理与可见输出。测试中的问题、期望调用和答复是明确输入，不证明自主规划、沙盒执行或完整 Job；未实际运行时不能声明模型理解或数据库恢复通过。入口拒绝业务数据库，不加载根 `.env`，报告不包含凭据、端点和答复正文，仅记录思考强度及预算。

[real_model_reasoning_recovery.py](../../tests/acceptance/real_model_reasoning_recovery.py) 在同一专用环境中使用私有 `--model-config`，默认 `/tmp/research-parameter-model-high.json`。参数部分执行真实模型→统一参数工具→原生 interrupt→关闭 PostgreSQL 连接池→重连并 resume→模型续接；因果部分运行正式 Deep Agent 的文件工具及结构化收尾，重连后再次调用模型。请求钩子只统计思考配置、历史推理项、非空明文推理字符数和工具返回项，继续真实 HTTP 传输；只有实际回传明文推理及工具返回才能通过续接检查。该入口不提供分析数据或领域算法工具，不证明因果算法、MCP HTTP、搜索、沙盒或完整生产分析流程。

[deepseek_responses_roundtrip.py](../../tests/acceptance/deepseek_responses_roundtrip.py) 是独立的 Responses 协议验收入口。运行容器与全新的 PostgreSQL 位于专用 Docker 网络，数据库别名固定为 `reasoning-postgres`，用户及数据库名均为 `reasoning_acceptance`，不发布数据库端口。模型从只读密钥文件读取 `DEEP_AGENT_API_KEY`，非敏感配置使用 `DEEP_AGENT_MODEL`、`DEEP_AGENT_BASE_URL`；Windows 开发挂载沿用 `ENVIRONMENT=development`。运行 `python tests/acceptance/deepseek_responses_roundtrip.py --report /tmp/唯一报告名.json`。该入口验证同步流式、非流式、关闭思考、`store=False` 明文保留、父图消息投影、真实文件工具续接，以及关闭连接后从 PostgreSQL 恢复的推理文本 hash 和再次模型调用。报告只保存状态、字符数及请求计数；保留专用数据库和报告。它不访问业务 MySQL，也不证明 MCP、RAG、沙盒或完整 Job 验收。

[test_parameter_validation_feedback.py](../../tests/integration/agent/test_parameter_validation_feedback.py) 直接组合真实完整研究草稿提交规则、CSV 校验器和重试消息组装，检查完整草稿保留既有角色、显式 null 清空，以及错误、实际角色值、列名和算法兼容性从原报告完整进入内部重试消息；它不调用模型，不证明语义提取正确。

[sandbox_upgrade_job.py](../../tests/acceptance/sandbox_upgrade_job.py) 用于完整隔离 Job：上传合成 CSV，经正式 Worker 和模型生成澄清、脚本与方案，提交显式测试答复及带版本的确认，然后执行真实沙盒、独立校验、数据绑定、MCP HTTP 算法和结构化报告。它在两次研究参数答复已保存后结束原 slot，启动新的正式 Worker 进程，再提交方案确认并恢复同一 Job。入口复用专用 MySQL/PostgreSQL；模型客户端必须单独允许外联，不能修改原内部数据库验收客户端的网络限制。控制服务另行连接该专用主库与 OpenSandbox，Worker 仅挂控制 token。

显式设置 `FULL_JOB_SANDBOX_ACCEPTANCE=1`、`FULL_JOB_SANDBOX_CONTROLLER_URL` 和指向控制服务统一密钥文件的 `FULL_JOB_SANDBOX_SECRET_FILE`，并在客户端准备仅含模型配置的 `/tmp/full-job-models.json`，再执行 `python tests/acceptance/sandbox_upgrade_job.py --timeout-seconds 900`。模型配置包含 `outer/deep/context_window_tokens`，前两项各含 `model/base_url/api_key`；入口将真实模型密钥与隔离数据库凭据写入本次私有文件，不修改根 `.env` 或原验收密钥文件。MCP 使用客户端回环地址的 8080 端口，控制服务使用独立容器；报告只保留状态、计数和源码 hash，原始诊断留在容器 `/tmp/sandbox-upgrade-job/`。本场景关闭 Web，RAG 使用独立空目录并报告不可用，不包含浏览器、CDFM 推理、生产网络或生产部署验收。

同一入口的 `--scenario role-columns --timeout-seconds 900` 使用中文研究角色及英文 CSV 列名，要求真实模型在方案中保存映射。验收依次检查原始研究角色未被改写、候选映射与独立校验使用的列一致、真实 MCP 算法与 ATE 检查使用对应变量对、最终校验通过及报告存在。该场景直接确认已明确的方案，不插入额外研究修订或 Worker 重启；恢复场景继续由原入口单独验证。

同一入口增加 `--scenario all-nodes`，默认仍运行原研究参数修订场景。新场景上传列名为 `0/1/2/3/4` 的合成连续数值 CSV，由真实模型提出字段范围问题，测试答复为“全部作为节点参与因果发现”，随后提交带版本的方案确认。验收要求该研究选择只问一次、最终角色均为空、实际列名保留，并通过正式 Worker、真实沙盒、独立校验、CSV 绑定、MCP PC 算法和结构化报告；该场景不要求参数修订次数或 Worker 重启。运行前须确认隔离控制服务及上游已就绪，并与提取验收顺序执行，避免两个 runner 竞争同一隔离 Job 队列。入口存在或提取矩阵通过均不能视为此完整 Job 验收通过。

### 工具可见性与 Web 开关

新增验证只使用真实规则函数、领域对象和框架组件，不生成模型、算法或搜索的替代返回：

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps unit-test python -B -m pytest -p no:cacheprovider tests/unit/agent/test_tool_visibility.py tests/unit/agent/test_deep_agent_state.py tests/integration/agent/test_tool_visibility_runtime.py -q
```

纯函数用例验证只有明确的 `True` 保留 Web 工具，`False`、`None` 和其他值隐藏，其他工具及原列表不变、顺序不变、不同 Context 独立，已移除工具不被重新加入。真实框架用例通过生产 `build_deep_agent()`、Registry、算法/RAG/Web/文件工具和 Responses API 序列化生成 HTTP 请求，在发送前由请求钩子显式中止，不返回模拟模型响应；分别核对并发开关、工具返回后下一轮、Gate 修正和 `FinalAnalysisDecision` schema 仍在载荷中。这只证明框架与载荷，不证明真实模型成功调用或完成结构化输出。

生产 ToolNode 用例执行关闭搜索的真实入口，核对 `disabled`、`WEB_SEARCH_DISABLED` 和 terminal Ledger；算法空数据前置拒绝也来自真实工具检查，不运行 MCP 算法。使用这些实际拒绝记录验证 Gate 复用同 attempt 的完整记录、拒绝未完成/终态不一致记录，以及旧 attempt 的 Web 记录不能满足当前检查。该用例保持 Gate 现有终态规则，不能解释成搜索或算法已经执行，也不能代替真实 Worker 接管和 PostgreSQL 恢复。

真实模型和搜索的显式入口见 [`tool_visibility_model_smoke.py`](../../tests/smoke/tool_visibility_model_smoke.py)，在已经配置生产格式非敏感环境变量及密钥文件的进程中运行：

```powershell
python -B tests/smoke/tool_visibility_model_smoke.py
```

该入口并发运行启用和关闭搜索的全新内存图调用，记录实际发出的工具名，并校验真实模型的 `FinalAnalysisDecision` 与 Web ToolMessage/终态 Ledger。仅 `available` 或 `no_results` 证明检索调用成功；`unavailable`、`protocol_error` 如实报告，不能算 SearXNG 成功。它不创建 MySQL Job，不连接 PostgreSQL；完整 Job、同 attempt Gate 修正、Worker 新 attempt 和 PostgreSQL 恢复仍需在更新后新建 Job 上逐项验收。模型、搜索、Job、恢复的证据必须分别报告，环境阻塞或未执行不可算通过。

### 分析事实的一致性验收

方法定义、混合图、画像、报告投影和保存后的恢复使用真实纯函数及领域模型检查，不替代算法或模型执行结果：

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps unit-test python -B -m pytest -p no:cacheprovider `
  tests/unit/agent/test_algorithm_specs.py `
  tests/unit/agent/test_analysis_facts_contracts.py `
  tests/unit/agent/test_graph_semantics_contracts.py `
  tests/unit/agent/test_cdfm_runner.py `
  tests/unit/agent/test_data_profile_semantics.py `
  tests/unit/agent/test_data_profile_projection.py `
  tests/unit/agent/test_profile_chart_contracts.py `
  tests/unit/agent/test_nonlinearity.py `
  tests/unit/agent/test_report_facts_persistence.py `
  tests/unit/agent/test_analysis_context_projection.py `
  tests/unit/agent/test_report_document.py `
  tests/unit/agent/test_shared_analysis_principles.py `
  tests/unit/agent/test_deep_agent_schema_snapshots.py `
  tests/unit/agent/test_shared_contract_imports.py `
  tests/unit/agent/test_acceptance_json_comparison.py
```

四种 CDFM 变量对编码、真实有向环拒绝、无向闭合路径、双箭头图资产与文字语义、未知测量语义、输入视图差异，以及方向相反与单方缺边的区分，由这些确定性检查覆盖。它们不证明模型效果，也不说明真实运行一定产生了对应边型或冲突。schema 有计划地变化后使用 `tests/unit/agent/regenerate_schema_snapshots.py` 更新两份快照；生成时仅给 snapshots 子目录写权限，正式测试继续保持仓库只读、网络关闭。

真实完整分析复用 [profile_dispatch_job_acceptance.py](../../tests/acceptance/p2_mcp/profile_dispatch_job_acceptance.py)。该人工入口从现有 worker 读取非敏感配置和密钥文件，只读挂载源码、输入文件与本地权重；创建独立网络、数据库及验收容器，不连接原业务数据库，不自动批准数据处理。先执行只读预检，再启动分析：

```powershell
python -B tests/acceptance/p2_mcp/profile_dispatch_job_acceptance.py launch --preflight-only --dataset <实际CSV路径> --cdfm-weights-volume <已有权重卷>
python -B tests/acceptance/p2_mcp/profile_dispatch_job_acceptance.py launch --dataset <实际CSV路径> --data-mode generic --cdfm-weights-volume <已有权重卷> --port 5187
```

Alarm 窗口数据使用 `--data-mode alarm-windowed`，额外核对 11 个真实数值列及有效 PC 结果；其他数据使用 generic，不套用 Alarm 专项断言。`--prompt` 可指定真实分析问题，`--expect-capability` 可重复指定期望调用的能力；最终主结果仍由真实 DeepAgent 决策和门禁决定。预检只证明配置、文件和镜像可用，不能证明卷内权重或模型已经加载；实际运行另查权重摘要、raw artifact 与标准图对应关系。

首次分析成功后，在本次仍运行的 worker 容器执行以下命令，以同一分析上下文重新生成报告，不重启已经退出的 run 容器：

```powershell
docker exec <本次前缀>-worker python -B tests/acceptance/p2_mcp/profile_dispatch_job_acceptance.py regenerate
docker exec <本次前缀>-worker python -B tests/acceptance/p2_mcp/profile_dispatch_job_acceptance.py verify-regeneration
```

再生入口先保存幂等请求身份，复用现有 Job API；核对保存的方法快照、逐结果取舍、选择依据、输入视图、主图和报告资源，并检查再生没有新增算法调用。跨 MySQL JSON 与 checkpoint 的比较只允许 `1e-14` 的浮点序列化舍入差异，键、顺序、身份和其他字段保持严格比较，不修改生产事实以迁就验证。验收保留本次容器和证据，未自动删除数据库或卷。

自动一致性检查不代替报告正文复核。还需逐项检查主辅身份、选择理由、逐边方向与类型、`edge_counts`、测量语义来源、假设和未检验事项；标题也不得将未知测量语义写成已确认业务量。记录时分别列出确定性检查、真实算法调用、完整模型/数据库/MCP/SSE 链路及新报告再生的实际结果与耗时；未执行或被环境中断的项目不能写为通过。前端改动另外执行相应静态、构建和真实页面检查。

调度前拒绝的诊断路径另由真实 ToolNode 回归覆盖：

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps unit-test python -B -m pytest -p no:cacheprovider tests/integration/agent/test_algorithm_terminal_state.py::test_real_toolnode_rejection_preserves_method_snapshot
```

该检查使用生产工具、Registry 和调度器，覆盖行数不足及数值依赖未满足，核对结果、ToolMessage 和账本保留方法快照；不使用执行器替身，也不代表已运行被拒绝的算法。

Deep Agent 新路径的定向测试覆盖静态 Algorithm Registry 与 `ToolNode` 依赖调度、父图
`deep_agent → finalization_gate → report` 拓扑、父子 State 白名单投影、三类
Finalization outcome/degraded 合同、RAG evidence 延迟初始化、可信 Web 开关、worker
进程级 runtime 复用和公共事件/SSE 字段脱敏：

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps unit-test python -m pytest -p no:cacheprovider `
  tests/unit/agent/test_dependency_dispatch.py `
  tests/unit/agent/test_deep_agent_parent_graph.py `
  tests/unit/agent/test_final_analysis_decision.py `
  tests/unit/agent/test_runtime_tool_state_updates.py `
  tests/unit/agent/test_stream_events.py `
  tests/unit/agent/test_worker_runtime.py `
  tests/integration/agent/test_deep_agent_graph_runtime.py
```

真实 Deep Agents 依赖下的 integration 证明 `CompiledStateGraph` 构造及当前默认
PC/DirectLiNGAM tool 接线，不调用真实 DeepSeek；dependency middleware 的调度测试使用真实
LangChain `ToolNode` 和 fake executor，不能替代真实 MCP HTTP、PostgreSQL Store、
RAG/SearXNG 或完整 MySQL Job 验收。通过数量由实际 pytest 输出记录，不在长期文档中固化。

公开决策和刷新恢复的定向覆盖还包括：Tool envelope 剥离 `public_decision` 后执行器只接收科学参数；RAG/Web 检索器入参不含该字段且公开工具名进入事件；无效公开说明不阻断工具；Gate 后内部结果引用映射为公开算法名；`decision/tool_call` 事件经会话历史白名单回放；前端对先于父阶段到达的明细执行一次性暂存和补绘，并对实时决策做渐进展示、对同阶段并行决策串行展示、对历史回放和 `prefers-reduced-motion` 直接展示完整文本。并行 RAG/Web ToolNode 回归还必须覆盖同一查询内排名 reference、跨查询重叠来源和 ToolMessage/State reference 一致性。浏览器缓存通过聊天页脚本与样式版本参数失效。

算法调用登记、统一终态和生命周期事件可独立运行无替代依赖的定向回归：

```powershell
docker compose -f docker-compose.test.yml run --rm --no-deps unit-test python -m pytest -p no:cacheprovider `
  tests/integration/agent/test_algorithm_terminal_state.py `
  tests/integration/agent/test_tool_lifecycle_events.py `
  tests/unit/agent/test_deep_agent_models.py `
  tests/unit/agent/test_action_ledger_reducers.py `
  tests/unit/agent/test_deep_agent_schema_snapshots.py
```

这些检查使用真实 LangGraph、ToolNode、内存 checkpoint、生产本地 PC runner 和纯领域契约，覆盖执行前 queued/running 登记、revision 单调恢复、失败 Command 的状态保留、deadline 清理及依赖阻断、Gate 完整性、报告超时限制和 sink/custom/聚合路径的稳定键。纯 asyncio deadline 工作负载只证明调度控制流，不等价于 CDFM、远端取消、真实模型、PostgreSQL/MySQL 或浏览器 SSE 验收。

真实算法终态另有两个[人工验收入口](../../tests/acceptance/p2_mcp/algorithm_terminal_acceptance.py)，不加入默认 pytest。`algorithm_terminal_acceptance.py` 的 server/client 分别运行在本次新建的容器中，仓库只读挂载 `/app`；服务端关闭外网，客户端使用 `--network container:<本次服务端容器>`。镜像需要同时具备测试环境的 LangGraph/应用依赖和 MCP 镜像的真实 CDFM 包；CDFM 权重按 `Agent/CausalAgentMCP/cdfm_weights.py` 的固定 revision 与摘要准备，只读挂载 `/weights`，设置 `CDFM_MODEL_PATH=/weights`、`HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1`。容器内命令如下：

```bash
python tests/acceptance/p2_mcp/algorithm_terminal_acceptance.py server
python tests/acceptance/p2_mcp/algorithm_terminal_acceptance.py client --deadline 0.75 --report /tmp/algorithm-terminal-report.json
```

服务端显式生成并冻结合成 CSV，保留默认真实进程池和 runner；它没有连接部署数据库。验收先做真实 CDFM 离线推理预检，再检查两次 PC、CDFM deadline、外部取消、Command/结果/账本/公共事件和进程清理；同一可信 Job 身份下的真实 PC 成功与 CDFM 超时结果还会一起进入 Gate，验证 PC 可被采用，空的显式报告草稿经程序装配后会自动含有 CDFM 分析限制。这不包含模型生成决策或报告。deadline 包括进程初始化与模型加载，不能解释为模型已经推理指定时长。缺失权重时 CDFM 项标为 `BLOCKED`，PC 的工程替补结果不算 CDFM 证据。退出码分别为 PASS=0、FAIL=1、BLOCKED=2。

[真实持久化入口](../../tests/acceptance/p2_mcp/terminal_persistence_acceptance.py) 使用独立内部网络中的全新 MySQL/PostgreSQL 容器，不公开数据库端口。MySQL 仅在初始化时允许空 root 密码并创建空的 `terminal_acceptance` 库；PostgreSQL 创建同名库和用户，临时内部服务使用 trust 认证。客户端设置 `TERMINAL_ACCEPTANCE_MYSQL_HOST` 和 `TERMINAL_ACCEPTANCE_POSTGRES_HOST` 为本次容器名后运行：

```bash
python tests/acceptance/p2_mcp/terminal_persistence_acceptance.py
```

该脚本拒绝非空 MySQL 库，在容器内生成一次性凭据并修改初始密码，然后执行正式 bootstrap migration、真实注册/登录和 Session/Job 服务；覆盖重新建立连接池后的 PostgreSQL checkpoint 恢复、revision 单调性、两次本地真实 PC、持久化事件幂等、Flask SSE 的 Last-Event-ID/刷新和取消后的迟到写入拒绝。报告保存在 `/tmp/terminal-persistence-report.json`。重跑必须使用新空库，结束后保留数据库与报告；这不证明 MCP/CDFM 与数据库的完整组合、worker 崩溃接管、真实浏览器或模型报告生成。

终态引用契约的定向覆盖还必须包含：把 RAG/Web 证据引用写入 `result_assessments` 时 Gate 以 `assessment_ref_unknown_result` 拒绝，只修该处后继续以 `proposal_evidence_ref_unknown` 拒绝，两处都修正后才通过；失败规则被翻译成脱敏修正指令并进入重试输入；身份/账本类失败不带规则码且保持通用指令；Gate 拒绝、降级与第二次 Deep Agent 修正各自发布稳定 `event_key` 的 `progress` 阶段说明，并由事件适配器绑定到对应阶段的活跃实例；未登记节点名或空文本不被外带。这几项由 `tests/unit/agent/test_final_analysis_decision.py`、`tests/unit/agent/test_deep_agent_parent_graph.py` 和 `tests/unit/agent/test_stream_events.py` 覆盖，结构化字段描述变更必须同步 `tests/unit/agent/snapshots/` 两个快照。

全量 `tests/unit` 与 `tests/integration` 应在交付前重新执行，并以本次命令输出报告
passed/skipped/failed；历史通过数量不能代替当前工作树证据。`tests/contracts` 由
`contracts` 执行组在具备 Python、Git 和 Docker CLI 的 runner 上运行，Compose 相关
检查缺少 Docker CLI 时跳过并保留未执行结论。日志序列化兜底事件本身也不能作为生产观测
链路已验收的证据。

## 因果效应估计与函数形式检查

`tests/unit/agent/test_ate_applicability.py` 使用真实纯检查和内核验证完整调整集、参与列准入、数值编码分类语义拒绝、结构原因码与检查记录契约；`test_g_formula_kernel.py` 验证调整后直接标准化的线性一致性、非线性差异、bootstrap 可复现性与拒绝路径。`tests/integration/agent/test_ate_tool_checks.py` 使用固定输入图、正式 ToolRuntime/Adapter/MCP 服务与真实进程 runner，验证连续调整变量估计、非参与分类列、工具消息与账本一致、错误引用拒绝、当前查询的诊断与估计匹配、函数形式检查的对比区间与结局模型要求、报告快照重新生成和预算身份收尾。`tests/integration/agent/test_ate_service_checks.py` 直接验证 MCP 使用实际 CSV 及保存语义复核，并保留内核共线等具体不适用理由。固定图是被测入口的已知输入，不代表因果发现准确性。

`tests/unit/agent/test_effect_diagnose_tool.py` 校验诊断工具对图引用、身份、变量与结构条件的判定，以及 `verdict`、`recommended_capability_id` 和 `recommended_tool_name` 的输出契约；`tests/unit/agent/test_functional_form.py` 与 `test_functional_form_prompt.py` 校验判定纯函数和提示词中的工具名。`tests/integration/agent/test_role_column_mapping.py` 从研究角色出发，经过方案映射得到执行列，再实际调用发现工具、诊断工具和后门 OLS，确认成功估计成为主结果。

调度 deadline 用例通过真实进程池初始化阶段的信号量屏障阻止 runner 开始，确定触发截止时间，并检查已完成的本地适用性记录只归属于对应调用。该用例不依赖 OLS 运行速度，不替换算法或 MCP 服务；它验证进程启动阶段超时，不表示回归已经开始执行。既有 FakeAlgorithmExecutor 用例仅检查传输无关的身份契约，默认结果和模板重绑定都必须从可信上下文复制来源身份及分析数据集 ID，不能作为真实算法链路证据。

~~~powershell
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_ate_applicability.py tests/unit/agent/test_ate_kernel.py tests/unit/agent/test_ate_worker_gate.py tests/unit/agent/test_g_formula_kernel.py tests/unit/agent/test_g_formula_mcp_execution.py tests/unit/agent/test_effect_diagnose_tool.py tests/unit/agent/test_functional_form.py tests/integration/agent/test_ate_tool_checks.py tests/integration/agent/test_ate_service_checks.py tests/integration/agent/test_role_column_mapping.py
~~~

上述进程内组件测试不访问 MySQL，不运行 HTTP MCP、PostgreSQL checkpoint 或完整模型 Job，不能替代真实环境验收。真实环境需另验证权威数据集绑定、HTTP 鉴权和传输、checkpoint 恢复后的记录、真实预算跳过及最终报告。缺少效应请求或检查记录的旧结果应可读但不能满足新门禁；检查后超时可保留 `ready`，仍禁止成功图和 raw artifact；检查前失败及预算收尾不可生成记录。

算法在独立进程中执行，进程池默认使用 `forkserver`。父进程先使用过线程化的数值库时，直接 `fork` 的子进程运行梯度提升树会崩溃，因此函数形式检查的 `outcome_model=hgb` 用例必须在真实进程池中执行；单独运行这些用例通过不代表已在父进程预热后的顺序下验证，合并执行 `tests/unit/agent` 与 `tests/integration/agent` 才能覆盖该交互。

## causal-mcp 纵向验证

算法工具和 Gate 的执行回归分别位于 `tests/unit/agent/test_runtime_tool_state_updates.py` 与 `tests/unit/agent/test_final_analysis_decision.py`。其中算法路径使用 `tests/support/local_algorithm_execution.py`，通过正式 MCP 服务、真实进程池和 causal-learn PC 执行进程内冻结 CSV；`ToolRuntime.state` 必须携带从该 CSV 转换的 `data_profile`。成功、两行样本导致的真实执行失败、处理方案绑定、工具结果及终态 Ledger 和 Gate 校验分别断言。这些文件中其他既有用例仍按其各自依赖边界解释，不能把整个文件的通过数量当作真实外部服务验收。

`tests/unit/agent/test_direct_lingam_analysis.py::test_direct_lingam_returns_structured_success_result` 使用真实 DirectLiNGAM，校验方法快照、安装版本以及邻接矩阵与公开边的方向、权重。CDFM 诊断测试只调用实际诊断和失败封装函数，使用真实文件读取异常与字典访问异常检查来源字段及消息截断；不证明 CDFM 模块加载或模型推理。`tests/integration/agent/test_cdfm_load_failure.py` 在真实 CDFM 依赖存在时，用空本地权重目录和离线设置验证加载失败的公开封装；缺少依赖时明确跳过，也不证明成功推理。MCP 缺省 runner 版本由 `tests/unit/agent/test_runner_method_metadata.py` 检查元数据与来源模型之间的契约，必须保持未知，不能通过填写虚构版本满足校验。MCP 的成功结果与慢调用日志用例运行真实 PC 进程，日志用例通过正式 JSON formatter 读取事件和关联字段；慢调用包含排队耗时，因此不要求告警晚于取得执行槽的 accepted 事件。上述本地组件证据均不包含 HTTP、MySQL 权威读取、PostgreSQL 恢复、真实模型或完整 Job。

```bash
python -m pytest -p no:cacheprovider tests/unit/agent/test_final_analysis_decision.py tests/unit/agent/test_runtime_tool_state_updates.py tests/unit/agent/test_cdfm_failure_diagnostics.py tests/unit/agent/test_direct_lingam_analysis.py tests/unit/agent/test_agent_state_routing.py tests/unit/agent/test_job_recovery.py tests/unit/agent/test_mcp_v2_contract.py tests/unit/agent/test_runner_method_metadata.py tests/integration/agent/test_cdfm_load_failure.py
```

`causal-mcp` 的代码级验证覆盖固定 capability/spec digest、Bearer/HMAC 时窗与命令绑定、结果规范化、进程池容量/迟到结果、N×K 客户端池、owner task 清理和 `AlgorithmExecutor` 结构化结果。使用仓库测试镜像执行：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_mcp_v2_contract.py
docker compose -f docker-compose.test.yml run --rm unit-test python tests/spike/p2_mcp_v2.py
docker pull mysql:8.0
docker run --rm --cpus=2 --memory=2g -v "${PWD}/tests:/tests:ro" causalagent-demopaper-causal-mcp:latest python /tests/acceptance/p2_mcp/run_acceptance.py
docker run --rm --cpus=2 --memory=2g -v "${PWD}/tests:/tests:ro" causalagent-demopaper-causal-mcp:latest python /tests/acceptance/p2_mcp/cdfm_acceptance.py
.\tests\acceptance\p2_mcp\scan_container_logs.ps1 -ContainerName causal-mcp
```

第二条 smoke 使用真实 MCP 2.2 Streamable HTTP/HTTP/1.1、真实 client pool 和 fake authority reader，证明协议/结构化 envelope/生命周期；第三条在实际 `causal-mcp` 镜像和 `2 CPU/2 GiB` 容器约束下运行 PC、OLC、DirectLiNGAM fixture、容量窗口、deadline、RSS/CPU、A/B pool，以及使用合成慢 runner 的真实 HTTP `cancel_algorithm`、目标进程终止和重复取消幂等。CDFM 使用独立的 `cdfm_acceptance.py` 入口，显式验证真实 CPU 模型、`directed_graph`、方向转换、私有 raw artifact、公共结果脱敏和控制面取消；它使用进程内冻结 CSV，不等价于 MySQL strong-read 或准确率验收，也不加入默认资源窗口。镜像固定 CDMIR commit、`cdfm-base==0.1.0` 与 CPU Torch，构建后必须通过 `pip check`。MySQL authority 另用一次性 `mysql:8.0` 容器与 `tests/acceptance/p2_mcp/mysql_strong_read.py` 验证有效 lease/旧 lease拒绝；真实容器算法调用后的 Docker logs 使用合成值做零命中扫描。生产数据规模、完整 migration Compose 和正式资源基准仍需单独验收。

fake executor 的 State/checkpoint 前置验证：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_deep_agent_fake_executor.py
```

该测试只证明官方 `DeepAgentState` 扩展、runtime context 不进入 State、显式 parent/deep projection、fake executor 结果/ledger 和 checkpointer 单步恢复；不证明真实 Deep Agent、Memory、RAG/Web、Finalization 或 worker 集成。

## RAG、多模态与隔离评测

多模态来源、staged index、release publish/rollback、便携 embedding 配置、隔离来源与持久评测队列的测试位于 `tests/unit/agent/` 的 `test_multimodal_*.py`、`test_rag_release_portable_embedding.py` 与 `tests/unit/rag_eval/` 的 `test_isolated_rag_eval_routes.py`、`test_rag_eval_*.py`；RAG 子图 State 隔离与不可用降级由 `tests/unit/agent/test_rag_subgraph_state.py` 覆盖。它们使用 fixture/fake 或静态契约，不代表真实模型、远程 VLM、向量库、SearXNG 或生产数据已执行。

正式多模态 embedding 默认使用 `EMBEDDING_API_KEY` 与 `EMBEDDING_BASE_URL`；缺少配置时相关运行只应报告 embedding 不可用。需要测试本地 embedding 时必须在隔离测试中显式传入配置，生产 defaults 的 `local_embedding.enabled` 当前为 `false`。

声明式验收矩阵位于 `Document/rag_eval_production_acceptance_matrix.json`，安全 runner 位于 `tests/acceptance/run_rag_eval_production_acceptance.py`。从仓库根目录运行：

```bash
# 只列出 contract 检查，不执行检查或写入报告
python tests/acceptance/run_rag_eval_production_acceptance.py --layer contract --list

# 执行非变更 contract 检查
python tests/acceptance/run_rag_eval_production_acceptance.py --layer contract

# integration 层按矩阵执行静态/隔离检查
python tests/acceptance/run_rag_eval_production_acceptance.py --layer integration

# production 层必须显式确认，且仅做只读 readiness
python tests/acceptance/run_rag_eval_production_acceptance.py --layer production --confirm-production-readiness
```

runner 通过 pytest 执行白名单内的测试文件，从而继承 `tests/conftest.py` 安装的离线密钥文件；不要改回 `unittest` 直接加载模块，否则会绕过收集期配置。生产层不会摄取资料、调用外部 VLM/模型、运行完整评测、冻结题集、发布索引或切换 active pointer；contract、integration、静态 Compose、迁移 head 或 readiness 结果都不能写成真实生产验收通过。RAG 评测使用独立 `rag-eval-worker`、队列任务和产物目录，不等同于独立数据库或安全域。当前测试入口只保留新的 acceptance runner，不应重新添加已移除的旧入口。

## 迁移链验证

空库升级和 migration graph 检查必须确认唯一 head 为 `x1y2z3a4b5c6`：

```bash
python -m alembic heads
```

迁移 downgrade/upgrade 仅在隔离数据库执行，并指定明确 revision；不能用 `alembic downgrade -1` 代替合并迁移的回退验证。

生产/预发 Compose 的 `:?` 必需变量应在无凭据占位的临时环境中验证“缺失即 fail closed”，
再使用合成占位值检查静态展开；不要把占位配置当成可部署或真实服务通过。`AsyncPostgresStore.setup()`
属于 PostgreSQL 官方 Store schema 初始化，必须与 checkpointer schema/readiness 分开验证；
checkpoint 清理不得触碰 `store`/`store_migrations` 表，用户长期记忆只由
`user_memory_cleanup_outbox` 通过官方 Store API 删除。

## SearXNG 部署验证

SearXNG 不再有初始化脚本：开发与预发 Compose 都直接使用已提交的非密钥 `settings.yml`，密钥由 `deploy/scripts/secret-env.sh` 从服务器密钥文件导出。Compose 部署契约由 `tests/contracts/deployment/test_searxng_compose.py` 静态检查，确认镜像不是 `latest`、存在 `/healthz` healthcheck、启动适配脚本导出 `SEARXNG_SECRET` 且配置目录只读挂载，同时默认 `app`/`worker` 不依赖 SearXNG。

需要 Docker Engine 才能执行真实容器验证：

```bash
docker compose -f docker-compose.yml config
docker compose -f docker-compose.yml up -d searxng
docker compose -f docker-compose.yml ps searxng valkey
```

容器验证应另外确认 `/healthz` 返回成功、JSON 搜索格式可用，以及停止 SearXNG 后开启 `web_search_enabled` 的 Job 仍按运行期重试和统一降级协议收敛。该真实网络证据不能由 unit 或静态 Compose 测试替代。

可使用隔离临时配置目录验证 `SEARXNG_SECRET` 注入与 `/healthz`；脚本结束时只清理本次生成的 Compose project 和临时目录，不触碰开发环境现有配置：

```powershell
powershell -ExecutionPolicy Bypass -File tests/run_searxng_docker_validation.ps1
```

## 日志与可观测性验证

日志第二阶段的重点回归位于 `tests/unit/test_event_catalog.py`、`tests/unit/test_request_logging.py`、`tests/unit/agent/`、`tests/unit/analytics/test_public_analytics_events.py` 和 `tests/contracts/observability/test_logging_policy.py`。它们覆盖事件目录和固定消息、请求/线程/异步任务/worker slot 上下文隔离、Job 终态、node 最终降级、RAG 计数日志、MCP 可信参数及 stdout/stderr、数据库/monitor/cleanup 转移、匿名访问事件的请求边界与访客标识脱敏，以及运行路径普通 logging 调用和敏感详情键的 AST 政策。

MCP 日志、60 秒慢调用、Job/invocation 关联、控制面取消、目标进程终止和 sibling 隔离集中由 `tests/unit/agent/test_mcp_v2_contract.py`、`tests/unit/agent/test_execution_guard.py`、`tests/unit/test_event_catalog.py` 与 `tests/contracts/observability/test_observability_compose.py` 覆盖。可先运行定向回归：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_mcp_v2_contract.py tests/unit/agent/test_execution_guard.py tests/unit/test_event_catalog.py tests/contracts/observability/test_observability_compose.py
```

其中 ProcessPool 测试只证明合成 runner 的 invocation 级终止和并行隔离，不代表 OLC 的生产数据性能；完整 Job 取消仍需真实 worker、MySQL heartbeat、HTTP MCP 和算法容器联调。

日志改造必须运行完整 `unit-test` 服务，不能只运行新增文件。测试通过只证明代码级合同，不证明 Docker 日志驱动、Alloy、Loki、Grafana、positions、查询标签或真实模型/MCP 链路。

环境受限时才使用本地回退，并且必须先取得用户同意再安装项目依赖：

```bash
python -m pytest
```

不带路径参数时按 `pytest.ini` 的 `testpaths` 收集 `tests/unit`、`tests/integration` 和 `tests/contracts`，并排除 `smoke` 与 `db_integration`。真实环境入口不会被这条命令收集。

## 前端共享设计系统

共享包的组件契约在 `packages/design-system/` 内自检，与任何前端工程相互独立：

```powershell
Push-Location packages/design-system
npm ci
npm run typecheck
npm run test:unit
npm run check
Pop-Location
```

`npm run check` 覆盖类型检查和单元测试。测试断言的是组件的行为契约：变体与尺寸的类名、禁用和加载状态不接受点击、加载时保留可读的标签、标签页的方向键和首尾跳转与 `aria-controls` 关联、输入控件的标签绑定与错误描述、空态和错误态的定位方式，以及加载态的角色与进度声明。

自检不证明视觉结果：颜色、字体、间距、圆角、投影和减少动态的表现必须在该前端接入之后，用真实页面在桌面端和移动端核对。组件设计 token 的核对以 `Document/design-system/tokens.md` 为准。

## 管理员前端

```bash
cd admin-frontend
npm ci
npm run typecheck
npm run test:unit
npm run build
```

`npm run build` 会先执行 typecheck，再生成 `/admin/` base 的 Vite 产物。管理员设计系统统一本轮只检查 loading、empty、error、401/403 和敏感内容代码契约；浏览器与真实数据库验收不纳入本轮页面样式交付。

## 隔离管理员 E2E

完成管理员生产构建后，从仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File tests/run_admin_32_e2e.ps1
```

脚本通过 `docker-compose.admin-e2e.yml` 叠加固定的隔离容器名、端口和数据卷，不触碰开发库；覆盖空库 `alembic upgrade head`、3.2 migration downgrade/upgrade、PostgreSQL checkpoint 读取/清理、受控用户/文件写入、逐目标审计、主从追平和普通用户回归。需要 `PLAYWRIGHT_BASE_URL`、管理员/普通用户凭据；只有 Edge 时可设置 `PLAYWRIGHT_CHANNEL=msedge`。

脚本结束后会保留隔离容器和卷，不自动清理。物理删除用例会修改种子数据，不能用 `KeepSeededData` 重放。

## 日志链路与文档验证

第一阶段真实环境补验必须在隔离 Compose project、独立卷和合成凭据中执行：

```powershell
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml pull loki alloy grafana
docker compose -f docker-compose.yml run --rm --no-deps alloy validate /etc/alloy/config.alloy
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml ps
```

随后验证五类 service 唯一事件、Alloy/Loki 重启和 positions 续读、暂停 Loki 时业务日志不阻塞、高基数字段不成为标签，以及 30 分钟代表性负载的行数、字节数、stream 数和事件排行。第二阶段还要逐项执行 Web 500、Job/node/RAG/MCP/monitor/副本/cleanup 故障矩阵，通过 request ID 和 job ID 检索完整关联链，检查并发无串值、正常流零 `WARNING/ERROR`，并用合成秘密、连接 URL、提示词、LLM 输出、CSV、SQL 参数和异常敏感文本做 stderr、Docker log 与 Loki 零命中抽样。

真实模型或知识库凭据不可用时，必须明确写为“未取得真实模型证据”，不能用 fake unit 测试替代。Docker daemon、Alloy validate、positions、上下文隔离、MCP stdout/可信参数或隐私检查任一失败时，不得标记第二阶段完成。

MCP execute/control lane 改造的定向回归还应覆盖 `tests/unit/agent/test_mcp_v2_contract.py`、`tests/unit/agent/test_worker_runtime.py`、`tests/unit/test_event_catalog.py`、`tests/contracts/deployment/test_mcp_compose.py` 和 `tests/contracts/observability/test_observability_compose.py`。真实 HTTP 验收使用 `tests/acceptance/p2_mcp/run_acceptance.py`，其中普通 execute 槽饱和时取消必须从独立 control lane 发出，并包含两个取消同时到达、同一 control 成员 `max_in_flight=2` 的场景。控制容量、响应、传输和协议异常分别记录为 `control_capacity_timeout`、`response_timeout`、`transport_error` 和 `invalid_response`；`unknown` 不能被解释为远端一定未取消。

所有文档变更还必须检查相对链接、顶部职责声明、失效路径和旧 acceptance 入口引用，以及 `git diff --check`；如果任务涉及根 README 的入口或部署说明，还必须核对 README 中的命令、链接和目录导航。只有用户明确要求时才修改根 README；完整验收通过前不追加完成态 CHANGELOG。

统一研究需求重构的离线组件检查包括 `tests/unit/agent/test_research_parameter_revisions.py`、`test_parameter_extraction_feedback.py`、`test_measurement_column_mapping.py` 与 `tests/integration/agent/test_processing_checkpoint_version.py`。它们分别检查实际差异与同源纠正、完整列集合解析、显式输出映射、真实 StateGraph/InMemorySaver 的旧版本拒绝及缺少冻结输入的失败路径，不替代真实模型、数据库、OpenSandbox 或浏览器证据。
