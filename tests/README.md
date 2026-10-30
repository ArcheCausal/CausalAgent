# 后端测试说明

文档职责：记录测试目录、测试层级和跨模块验证入口。

适用范围：新增或修改测试、CI 测试门禁和部署合同检查时使用；具体模块的专项验证说明以对应的 Document 页面为准。

测试目录先按测试层级划分，再在层级内按业务模块划分：

```text
tests/
├── unit/               # 单个模块、纯函数或最小 Flask 应用，外部依赖使用 fake
│   ├── admin/
│   ├── agent/          # Agent、Deep Agent、知识库 RAG 与多模态契约
│   ├── analytics/
│   ├── auth/
│   ├── chat/
│   ├── database/
│   ├── deployment/     # 发布包生成、预发环境保护等纯函数
│   └── rag_eval/       # RAG 评测台本体：路由、队列、生命周期与生产验收
├── integration/        # 多个实际组件之间的数据传递、生命周期和协作行为
│   ├── admin/
│   ├── agent/
│   ├── config/         # 完整运行时配置与服务模块导入
│   ├── database/
│   └── deployment/     # Flask 页面入口、静态资源与权限行为
├── contracts/          # 跨文件配置一致性、部署约束、迁移图与静态政策
│   ├── database/
│   ├── deployment/
│   └── observability/
├── e2e/database/       # 真实 MySQL 事务、数据绑定、父图恢复和清理验证
├── acceptance/         # 分层 RAG contract/integration/production runner
├── e2e/admin/          # 隔离主从 + PostgreSQL checkpoint E2E 模块
├── run_admin_31_e2e.ps1
├── run_admin_32_e2e.ps1
└── run_searxng_docker_validation.ps1
```

`unit`、`integration` 和 `contracts` 表示测试层级，`admin`、`auth`、`database` 等目录表示业务归属。测试层级说明这项测试证明什么，CI 执行组说明它在什么条件下运行，两者无需一一对应。新增测试时先判断依赖范围，再选择业务目录。仓库测试同时包含 pytest 风格函数与 `unittest.TestCase`，统一由 pytest 负责发现和执行。

RAG 评测台本体（`test_rag_eval_*.py`、`test_rag_candidate_*.py`、`test_isolated_*.py`、`test_rag_dataset_governance.py`、`test_rag_eval_production_acceptance.py`）位于 `tests/unit/rag_eval/`；多模态指数与检索（`test_multimodal_*.py`）与知识库 RAG 契约（`test_rag_release_portable_embedding.py`、`test_rag_runtime_lifecycle.py`、`test_rag_service_and_tool.py`、`test_ragas_generate_with_chunks_engine.py`、`test_rag_candidate_generation_and_sweep.py`）位于 `tests/unit/agent/`。部署、迁移和日志政策检查位于 `tests/contracts/`，Flask 页面入口行为位于 `tests/integration/deployment/`，迁移版本修复的纯函数用例位于 `tests/unit/database/`。默认 pytest 通过 `pytest.ini` 的 `testpaths` 收集 `unit`、`integration` 和 `contracts`，并排除 `smoke` 与 `db_integration`。跨越测试与压测的边界约束见 `tests/AGENTS.md`。

对 staging 入口执行真实 HTTP/SSE 负载的压测客户端不在 `tests/` 之下，位于仓库根 `loadtest/`，不进入默认 pytest 与 CI 执行范围。压测的运行产物、凭据和报告正文都不提交，唯一入库的压测索引是 `loadtest/evidence/index.md`。

`contracts/deployment/` 只做不启动容器的 Compose、Dockerfile、发布 workflow 和镜像边界检查；`integration/deployment/` 只验证 Flask 页面与静态资源的路由行为。SearXNG 的 `SEARXNG_SECRET` 注入和 `/healthz` 真实容器验证通过 `tests/run_searxng_docker_validation.ps1` 手工执行，不属于默认 pytest 范围。需要真实 MySQL 的 `tests/e2e/database/test_load_session_event_query.py` 同样不进入默认范围。

`unit/config/test_secret_file.py` 保持只依赖轻量测试环境；需要导入完整控制服务的配置检查位于 `integration/config/test_controller_configuration.py`，由 backend 组在运行时依赖齐全的镜像中执行。父图读取已绑定数据集、子图恢复和会话删除回收分别在 `e2e/database/test_deep_agent_parent_mysql.py` 与 `test_chat_dataset_cleanup_mysql.py` 使用真实 MySQL，显式隔离环境与续跑要求见 [测试与验证](../Document/development/testing.md)。

## 前端与页面入口

仓库现在有四个互相独立的 Vue 3 + TypeScript 工程，都不加入根级 npm workspace：官网 `website-frontend/`、普通用户应用 `chat-frontend/`、管理员系统 `admin-frontend/`、RAG 评测台 `app/rag_eval/frontend/`。进入目录后执行 `npm ci` 安装锁定依赖。各端的 `npm run check` 覆盖范围按实际 package scripts 区分：官网运行类型检查、单元测试和生产构建；普通用户应用还运行 lint、组件测试和 Mock Playwright E2E；管理员系统运行类型检查、单元测试和生产构建，Mock E2E 需另行运行 `npm run test:e2e:mock`（命令见 `Document/admin/testing.md`）；RAG 评测台使用 `npm run typecheck`、`npm test` 和 `npm run build`。Mock E2E 只使用模拟 API，不等价于真实 Flask、数据库、worker、模型或浏览器验收。

页面入口与构建产物的 Flask 契约由本地或发布前手工检查：

```powershell
python -m pytest -p no:cacheprovider tests/integration/deployment/test_frontend_entries.py tests/contracts/deployment/test_frontend_entrypoints.py
docker compose -f docker-compose.yml config --quiet
docker compose --env-file .env.staging.example -f docker-compose.staging.yml config --quiet
docker compose -f docker-compose.prod.yml config --quiet
```

`/`、`/product`、`/about`、`/docs`、`/changelog` 以及 `/auth/sign-in`、`/auth/sign-up` 由官网产物提供；`/dashboard` 由普通用户应用提供，未登录访问跳转 `/auth/sign-in`；`/rag-eval` 由 RAG 评测台提供并要求 `rag_eval.access`；`/admin` 继续由管理员前端提供。项目已移除 `/chat-next` 和 `/rag_eval` 兼容别名，也不再读取或向 Compose 传递 `CHAT_FRONTEND_ENTRY`。未执行的真实验收、Docker 镜像构建或运行时检查必须在报告中明确保留。

## Docker 单元测试环境（推荐）

统一研究参数的完整提交、集合声明与测量列映射分别由 `unit/agent/test_research_parameter_revisions.py`、`test_parameter_extraction_feedback.py`、`test_measurement_column_mapping.py` 检查；`integration/agent/test_processing_checkpoint_version.py` 使用真实图与 saver 检查旧版本拒绝，缺少冻结输入时不访问数据库或模型。

研究参数的真实模型及数据库恢复入口、思考模式工具续接和连接池重建说明见 [数据处理验收](../Document/development/testing.md)。独立脚本 `acceptance/real_model_parameter_extraction.py` 比较完整参数语义，`acceptance/real_model_reasoning_recovery.py` 检查真实思考历史、参数工具、因果 Agent 文件工具及 PostgreSQL 重连；二者均不属于默认 pytest 范围。

`acceptance/deepseek_responses_roundtrip.py` 使用真实 DeepSeek 和专用 PostgreSQL，检查父图状态消息隔离、非空明文推理回传、同步与异步调用、文件工具续接及重连后的历史一致性。工具或执行模型预算耗尽后，只能使用独立的三次模型收尾额度提交结构化决策；请求不再暴露执行工具，生产 Gate 修正输入和重连不得重开预算，回执保持唯一。额度用尽时也不得再调用模型。`unit/agent/test_analysis_budget.py` 检查阶段转换与真实未执行状态；`unit/agent/test_tool_call_limit.py` 检查已完成调用、混合批次、正常计数和超限停止规则。报告和算法组件检查见 `unit/agent/test_report_document.py`、`integration/agent/test_ate_tool_checks.py`。验收脚本不运行业务 Job 或领域算法，专用容器及报告要求见同一[数据处理验收说明](../Document/development/testing.md)。

数据处理方案的确定性规则位于 `unit/agent/test_data_processing_lifecycle.py`；方案工具、脚本工具、父子状态投影和 checkpoint 状态传递位于 `integration/agent/test_data_processing_plan_state.py`，使用真实 ToolNode、StateGraph 与 InMemorySaver。真实数据库入口 `acceptance/data_processing_recovery.py` 覆盖保存候选后补写执行说明；完整模型与 OpenSandbox 入口为 `acceptance/sandbox_upgrade_job.py --scenario execution-report`。后两者必须在专用隔离服务上显式执行，离线测试不代替这些证据。

`docker-compose.test.yml` 提供独立的 `unit-test` 服务。Dockerfile 的 `test` 目标先从 `tests/smoke/requirements-deep-agent-py311-linux.lock` 安装 Python 3.11 Linux hash-pinned 项目依赖，再安装 `requirements-test.txt`，不会把 `pytest` 临时安装到正在运行的应用容器。

该服务有以下边界：

- 不依赖 `app`、worker、monitor 或 MySQL 服务。
- 禁用容器网络，防止单元测试意外调用外部服务。
- 将当前仓库只读挂载到 `/app`，代码修改立即生效，测试不能改写工作区。
- 使用 `--rm` 时只删除本次测试容器；已经构建的测试镜像会继续复用。



```bash
docker compose -f docker-compose.test.yml build unit-test
```

运行默认范围（全部单元测试与离线组件 integration）：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test
```

运行指定目录或文件时，在服务名后覆盖默认命令：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/admin
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent/test_agent_state_routing.py
```

临时进入相同测试环境排查问题：

```bash
docker compose -f docker-compose.test.yml run --rm unit-test sh
```

退出 Shell 后容器自动删除。修改 Python 源码或测试文件后不需要重建镜像；只有依赖变化才需要重新构建。

## RAG 多模态与隔离评测验收

多模态来源、staged index、release 和隔离评测的测试位于 `tests/unit/agent/test_multimodal_*.py`（`contracts` 为核心与检索身份、`parsing`、`vision`、`indexing` 按职责拆分，共享 fixture 在 `multimodal_fixtures.py`）与 `tests/unit/rag_eval/test_isolated_rag_eval_routes.py`、`tests/unit/rag_eval/test_rag_eval_*.py`；持久队列由默认开发 Compose 的 `rag-eval-worker` 执行。声明式矩阵在 `Document/rag_eval_production_acceptance_matrix.json`，安全 runner 在 `tests/acceptance/run_rag_eval_production_acceptance.py`：

```bash
# 只列出 contract 检查；不执行检查或写入报告
python tests/acceptance/run_rag_eval_production_acceptance.py --layer contract --list

# contract / integration 执行对应白名单检查
python tests/acceptance/run_rag_eval_production_acceptance.py --layer contract
python tests/acceptance/run_rag_eval_production_acceptance.py --layer integration

# production 仅在显式确认后执行只读 readiness
python tests/acceptance/run_rag_eval_production_acceptance.py --layer production --confirm-production-readiness
```

runner 不会代替真实资料摄取、外部模型/VLM、完整 Ragas 评测、题集冻结、release publish 或 active pointer 切换；这些命令或静态结果不得表述为真实生产验收。当前测试入口只保留新的 acceptance runner，不应重新添加已移除的旧入口。

## 本地 Python 回退方式

未使用 Docker 时，需要先跟用户说明目前本地docker环境未启动/出错，需要在当前 Python 环境安装项目依赖和 `pytest`，用户同一之后然后运行：

```bash
python -m pytest
```

不带路径参数时，`pytest.ini` 的 `testpaths` 把收集范围限定为 `tests/unit`、`tests/integration` 和 `tests/contracts`，并排除 `smoke` 与 `db_integration`。`tests/e2e/`、`tests/acceptance/` 和 `tests/smoke/` 使用各自的显式命令。

这条路径只作为环境受限时的回退，不应由 Codex 或开发者反复临时安装依赖来充当团队测试环境。



只有准备好隔离 Docker 主从、PostgreSQL checkpoint、管理员前端生产构建和 Playwright 凭据后，才运行：

```powershell
powershell -ExecutionPolicy Bypass -File tests/run_admin_32_e2e.ps1
```

两个 PowerShell 文件是稳定的人工入口。E2E Python 辅助模块位于 `tests/e2e/admin/`，不应作为普通 pytest 测试自动执行，也不属于 `unit-test` 服务的执行范围。
