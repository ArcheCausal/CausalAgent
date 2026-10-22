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

**维护规则**：新增测试必须先放对层级，再更新 `.github/filters/paths.yaml`。跨模块测试要由它读取的生产文件触发，例如部署契约检查 Dockerfile，就必须在 Dockerfile 改变时运行，不能只在契约测试自身改变时运行。`loadtest/runner/**` 变化触发 backend 执行组，验证日志运行时与压测解析器之间的数据传递；共享 Shell 密钥读取器的行分隔符检查由部署契约组显式执行。共享 CI、路径规则和公共测试配置变化时，宁可触发全部常规组。`tests/contracts/deployment/test_staging_compose.py` 和 `test_searxng_compose.py` 需要 Docker CLI，缺少时只跳过并保留未执行结论。

`main` 与 `develop` 的分支规则集 `mian and dev` 要求 `Python syntax`、`Pull request policy` 和 `CI result` 三个检查。删除或重命名工作流任务时，必须同时修改规则集的必需检查，否则 PR 会一直等待一个不会出现的检查。`CI result` 只在 Pull Request 上运行，push 与手动触发只做缓存预热。

Docker unit 镜像按 `tests/README.md` 中的固定 lock 构建，由 `docker/bake-action@v6` 执行 Bake，以获得 GitHub Actions 提供的缓存运行时令牌和服务地址；无需把令牌写入工作流或服务器配置。两个构建步骤使用 GHA v2 层缓存和同一 `scope=unit-test`：Pull Request 只通过 `cache-from` 恢复并将镜像加载到 Docker，`push` 或手动启动的预热任务通过 `cache-to` 写入，使用 `mode=min` 和 `type=cacheonly`，不加载或推送镜像。构建记录附件上传关闭，构建日志与摘要保留。运行测试仍使用 `docker compose -f docker-compose.test.yml run --rm unit-test`，`docker-compose.test.yml` 保持本地与 CI 共用、不含仅 CI 可用的缓存参数，隔离契约（`network_mode: none`、只读挂载、`env_file`）不变。缓存上限与保留期由 GitHub 侧管理，公开仓库使用标准托管 runner 的用量不计费。

验证缓存时，应先在目标基础分支成功运行一次预热任务，确认日志出现 GHA 缓存导出记录，再查看后续 Pull Request 日志的缓存导入和依赖层 `CACHED` 记录。第一次预热或依赖锁、Dockerfile、基础镜像发生变化时，依赖层可能重新构建；任务成功或配置中存在 `cache-from/cache-to` 不代表缓存已实际写入或命中。

Compose 校验只展开配置，不启动服务。静态 Dockerfile 按构建相关路径触发，不会生成或推送镜像；实际 app、MCP、MySQL 主从镜像只由人工触发的发布工作流构建。

发布工作流的静态契约及镜像解析器参数失败路径由 `tests/contracts/deployment/test_release_workflow.py` 检查，包括四个构建全部成功后才打包、同一源码提交传递、可选手动输入、无效输入不写清单，以及拒绝无法验证 revision 的元数据。该文件不模拟 GHCR 成功响应；通过静态检查不代表仓库鉴权、真实镜像查询或 GitHub Actions 执行成功。真实发布需在 Actions 中检查四个 digest、完整源码提交、制品上传及服务器验收。

本地复现预发静态检查：

~~~bash
docker compose --env-file .env.staging.example -f docker-compose.staging.yml config --quiet
python -m pytest -p no:cacheprovider tests/contracts/deployment/test_staging_compose.py
~~~

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

## causal-mcp 纵向验证

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

空库升级和 migration graph 检查必须确认唯一 head 为 `e1f2a3b4c5d6`：

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
