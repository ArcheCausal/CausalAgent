# 测试与验证

文档职责：定义后端、管理员前端、集成迁移和隔离主从/PostgreSQL 验收的验证矩阵与稳定命令。

适用范围：修改代码、迁移、管理员页面或文档中命令时使用；管理员专项测试补充说明见 [`../admin/testing.md`](../admin/testing.md)。

## 测试层级

| 层级 | 位置/入口 | 主要证明 |
| --- | --- | --- |
| 后端 unit | `tests/unit/` | 单模块状态机、权限、Job fencing、结构化输出和 monitor 逻辑 |
| 后端 integration | `tests/integration/` | 跨模块静态契约、日志政策、部署边界和 migration 链 |
| RAG/多模态与隔离评测 | `tests/test_multimodal_*.py`、`tests/test_rag_eval_*.py`、`tests/acceptance/` | 来源/索引/release 契约、隔离队列与评测矩阵的分层检查 |
| 管理员 Vue unit | `admin-frontend/tests/*.spec.ts` | API DTO、组件、看板/设置语义和 SQL digest 展示 |
| 管理员 Mock E2E | `admin-frontend` `test:e2e:mock` | 无真实数据库的页面导航、鉴权和交互 |
| 普通用户应用 Vue unit/contract | `chat-frontend/tests/unit`、`tests/contract` | SSE parser、游标、Job reducer、公开决策与明细补绘、展示推进纯函数、地址路由、幂等和 Flask JSON schema |
| 普通用户应用 Vue component/Mock E2E | `chat-frontend/tests/components`、`tests/e2e-mock` | Composer、消息时间线、假流式草稿与公开决策展示，以及未登录跳转、已登录创建 Job/SSE 终态、会话地址、设置页和退出登录 |
| 官网 Vue unit | `website-frontend/tests/unit` | 路由解析、登录回跳白名单、认证客户端和登录表单交互 |
| RAG 评测台前端 | `app/rag_eval/frontend/test` | 审核工作流纯函数（`npm test`）、类型检查与生产构建 |
| 共享设计系统组件 unit | `packages/design-system/tests` | 组件变体与状态类名、标签页键盘行为、输入控件的无障碍关联、空态/加载态/错误态的角色与进度声明 |
| 前端页面入口 Flask 契约 | `tests/integration/deployment/test_frontend_entrypoints.py` | 四套入口与资源前缀、未登录跳转、权限拒绝、dist 缺失 fail-closed、缓存头、Docker builder/runtime、Compose 变量和忽略规则 |
| Windows 桌面逻辑 | `windows-client/tests/test_config.py`、`test_navigation_policy.py`、`test_runtime.py`、`test_launcher.py` | 配置优先级、URL/origin 白名单、运行时错误和 Edge 事件策略，不创建真实窗口 |
| Windows 壳层 smoke | `windows-client/tests/run_windows_smoke.py`、`test_windows_smoke.py` | 隔离 HTTP stub、真实 WebView2 Edge Chromium 页面加载和窗口退出；只在 Windows 桌面会话执行 |
| 隔离 E2E | `tests/run_admin_31_e2e.ps1` / `run_admin_32_e2e.ps1` | 空库升级、migration 往返、主从、PostgreSQL checkpoint、受控写入/删除和普通用户回归 |

`tests/README.md` 是后端测试目录和 Docker 单元测试环境的补充入口；新增测试时先判断是否需要真实跨模块依赖，再选择 unit 或 integration。

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

前端页面入口和 Docker 静态契约由本地/发布前手工执行，不接入现有 CI：

```powershell
python -m pytest -p no:cacheprovider tests/integration/deployment/test_frontend_entrypoints.py
python -m py_compile app/main/routes.py app/chat/page_routes.py app/rag_eval/page_routes.py config/settings.py tests/integration/deployment/test_frontend_entrypoints.py
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.staging.yml config --quiet
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

终态引用契约的定向覆盖还必须包含：把 RAG/Web 证据引用写入 `result_assessments` 时 Gate 以 `assessment_ref_unknown_result` 拒绝，只修该处后继续以 `proposal_evidence_ref_unknown` 拒绝，两处都修正后才通过；失败规则被翻译成脱敏修正指令并进入重试输入；身份/账本类失败不带规则码且保持通用指令；Gate 拒绝、降级与第二次 Deep Agent 修正各自发布稳定 `event_key` 的 `progress` 阶段说明，并由事件适配器绑定到对应阶段的活跃实例；未登记节点名或空文本不被外带。这几项由 `tests/unit/agent/test_final_analysis_decision.py`、`tests/unit/agent/test_deep_agent_parent_graph.py` 和 `tests/unit/agent/test_stream_events.py` 覆盖，结构化字段描述变更必须同步 `tests/unit/agent/snapshots/` 两个快照。

全量 `tests/unit` 与 `tests/integration` 应在交付前重新执行，并以本次命令输出报告
passed/skipped/failed；历史通过数量不能代替当前工作树证据。日志序列化兜底事件本身也不能
作为生产观测链路已验收的证据。

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

多模态来源、staged index、release publish/rollback、便携 embedding 配置、隔离来源与持久评测队列的 focused tests 位于 `tests/test_multimodal_*.py`、`tests/test_rag_release_portable_embedding.py`、`tests/test_isolated_rag_eval_routes.py` 和 `tests/test_rag_eval_*.py`；RAG 子图 State 隔离与不可用降级由 `tests/unit/agent/test_rag_subgraph_state.py` 覆盖。它们使用 fixture/fake 或静态契约，不代表真实模型、远程 VLM、向量库、SearXNG 或生产数据已执行。

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

production 层不会摄取资料、调用外部 VLM/模型、运行完整评测、冻结题集、发布索引或切换 active pointer；contract、integration、静态 Compose、迁移 head 或 readiness 结果都不能写成真实生产验收通过。RAG 评测使用独立 `rag-eval-worker`、队列任务和产物目录，不等同于独立数据库或安全域。当前测试入口只保留新的 acceptance runner，不应重新添加已移除的旧入口。

## 迁移链验证

空库升级和 migration graph 检查必须确认唯一 head 为 `c9d0e1f2a3b4`：

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

SearXNG 初始化脚本的纯脚本行为由 `tests/unit/deployment/test_searxng_init_settings.py` 覆盖，包含生成、幂等、缺失 example 和生成中断不发布半成品。Compose 部署契约由 `tests/integration/deployment/test_searxng_compose.py` 静态检查，确认镜像不是 `latest`、存在 `/healthz` healthcheck，且默认 `app`/`worker` 不依赖 SearXNG。

需要 Docker Engine 才能执行真实容器验证：

```bash
docker compose -f docker-compose.yml config
docker compose -f docker-compose.yml up -d searxng
docker compose -f docker-compose.yml ps searxng searxng-init valkey
```

容器验证应另外确认 `/healthz` 返回成功、JSON 搜索格式可用，以及停止 SearXNG 后开启 `web_search_enabled` 的 Job 仍按运行期重试和统一降级协议收敛。该真实网络证据不能由 unit 或静态 Compose 测试替代。

可使用隔离临时配置目录执行 init、healthcheck 和幂等性验证；脚本结束时只清理本次生成的 Compose project 和临时目录，不触碰开发环境现有配置：

```powershell
powershell -ExecutionPolicy Bypass -File tests/run_searxng_docker_validation.ps1
```

## 日志与可观测性验证

日志第二阶段的重点回归位于 `tests/unit/test_event_catalog.py`、`tests/unit/test_request_logging.py`、`tests/unit/agent/`、`tests/unit/analytics/test_public_analytics_events.py` 和 `tests/integration/test_logging_policy.py`。它们覆盖事件目录和固定消息、请求/线程/异步任务/worker slot 上下文隔离、Job 终态、node 最终降级、RAG 计数日志、MCP 可信参数及 stdout/stderr、数据库/monitor/cleanup 转移、匿名访问事件的请求边界与访客标识脱敏，以及运行路径普通 logging 调用和敏感详情键的 AST 政策。

MCP 日志、60 秒慢调用、Job/invocation 关联、控制面取消、目标进程终止和 sibling 隔离集中由 `tests/unit/agent/test_mcp_v2_contract.py`、`tests/unit/agent/test_execution_guard.py`、`tests/unit/test_event_catalog.py` 与 `tests/integration/test_observability_compose.py` 覆盖。可先运行定向回归：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_mcp_v2_contract.py tests/unit/agent/test_execution_guard.py tests/unit/test_event_catalog.py tests/integration/test_observability_compose.py
```

其中 ProcessPool 测试只证明合成 runner 的 invocation 级终止和并行隔离，不代表 OLC 的生产数据性能；完整 Job 取消仍需真实 worker、MySQL heartbeat、HTTP MCP 和算法容器联调。

日志改造必须运行完整 `unit-test` 服务，不能只运行新增文件。测试通过只证明代码级合同，不证明 Docker 日志驱动、Alloy、Loki、Grafana、positions、查询标签或真实模型/MCP 链路。

环境受限时才使用本地回退：

```bash
python -m pytest tests/unit
```

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

MCP execute/control lane 改造的定向回归还应覆盖 `tests/unit/agent/test_mcp_v2_contract.py`、`tests/unit/agent/test_worker_runtime.py`、`tests/unit/test_event_catalog.py`、`tests/integration/deployment/test_mcp_compose.py` 和 `tests/integration/test_observability_compose.py`。真实 HTTP 验收使用 `tests/acceptance/p2_mcp/run_acceptance.py`，其中普通 execute 槽饱和时取消必须从独立 control lane 发出，并包含两个取消同时到达、同一 control 成员 `max_in_flight=2` 的场景。控制容量、响应、传输和协议异常分别记录为 `control_capacity_timeout`、`response_timeout`、`transport_error` 和 `invalid_response`；`unknown` 不能被解释为远端一定未取消。

所有文档变更还必须检查相对链接、顶部职责声明、失效路径和旧 acceptance 入口引用，以及 `git diff --check`；如果任务涉及根 README 的入口或部署说明，还必须核对 README 中的命令、链接和目录导航。只有用户明确要求时才修改根 README；完整验收通过前不追加完成态 CHANGELOG。
