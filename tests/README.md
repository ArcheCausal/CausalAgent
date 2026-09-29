# 后端测试说明

测试目录先按测试层级划分，再在层级内按业务模块划分：

```text
tests/
├── unit/               # 单个模块或最小 Flask 应用，外部依赖使用 fake/mock
│   ├── admin/
│   ├── agent/
│   ├── auth/
│   ├── chat/
│   └── database/
├── integration/        # 跨模块静态契约、部署边界和 migration 链路
│   ├── admin/
│   ├── deployment/
│   └── migrations/
├── acceptance/         # 分层 RAG contract/integration/production runner
├── e2e/admin/          # 隔离主从 + PostgreSQL checkpoint E2E 模块
├── run_admin_31_e2e.ps1
├── run_admin_32_e2e.ps1
└── run_searxng_docker_validation.ps1
```

`unit` 和 `integration` 表示依赖范围，`admin`、`auth`、`database` 等目录表示业务归属。新增测试时先判断是否需要真实跨模块依赖，再选择业务目录。仓库测试同时包含 pytest 风格函数与 `unittest.TestCase`，统一由 pytest 负责发现和执行。

`integration/deployment/` 只做不启动容器的 Compose 部署契约检查；SearXNG 的 init、healthcheck 和幂等性真实容器验证通过 `tests/run_searxng_docker_validation.ps1` 手工执行，不属于默认 pytest 范围。

## 前端与页面入口

仓库现在有四个互相独立的 Vue 3 + TypeScript 工程，都不加入根级 npm workspace：官网 `website-frontend/`、普通用户应用 `chat-frontend/`、管理员系统 `admin-frontend/`、RAG 评测台 `app/rag_eval/frontend/`。进入目录后执行 `npm ci` 安装锁定依赖。各端的 `npm run check` 覆盖范围按实际 package scripts 区分：官网运行类型检查、单元测试和生产构建；普通用户应用还运行 lint、组件测试和 Mock Playwright E2E；管理员系统运行类型检查、单元测试和生产构建，Mock E2E 需另行运行 `npm run test:e2e:mock`（命令见 `Document/admin/testing.md`）；RAG 评测台使用 `npm run typecheck`、`npm test` 和 `npm run build`。Mock E2E 只使用模拟 API，不等价于真实 Flask、数据库、worker、模型或浏览器验收。

页面入口与构建产物的 Flask 契约由本地或发布前手工检查：

```powershell
python -m pytest -p no:cacheprovider tests/integration/deployment/test_frontend_entrypoints.py
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.staging.yml config --quiet
docker compose -f docker-compose.prod.yml config --quiet
```

`/`、`/product`、`/about`、`/docs`、`/changelog` 以及 `/auth/sign-in`、`/auth/sign-up` 由官网产物提供；`/dashboard` 由普通用户应用提供，未登录访问跳转 `/auth/sign-in`；`/rag-eval` 由 RAG 评测台提供并要求 `rag_eval.access`；`/admin` 继续由管理员前端提供。项目已移除 `/chat-next` 和 `/rag_eval` 兼容别名，也不再读取或向 Compose 传递 `CHAT_FRONTEND_ENTRY`。未执行的真实验收、Docker 镜像构建或运行时检查必须在报告中明确保留。

## Docker 单元测试环境（推荐）

`docker-compose.test.yml` 提供独立的 `unit-test` 服务。Dockerfile 的 `test` 目标先从 `tests/smoke/requirements-deep-agent-py311-linux.lock` 安装 Python 3.11 Linux hash-pinned 项目依赖，再安装 `requirements-test.txt`，不会把 `pytest` 临时安装到正在运行的应用容器。

该服务有以下边界：

- 不依赖 `app`、worker、monitor 或 MySQL 服务。
- 禁用容器网络，防止单元测试意外调用外部服务。
- 将当前仓库只读挂载到 `/app`，代码修改立即生效，测试不能改写工作区。
- 使用 `--rm` 时只删除本次测试容器；已经构建的测试镜像会继续复用。



```bash
docker compose -f docker-compose.test.yml build unit-test
```

运行全部单元测试：

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

多模态来源、staged index、release 和隔离评测的 focused tests 位于仓库根 `tests/test_multimodal_*.py`、`tests/test_isolated_rag_eval_routes.py`、`tests/test_rag_eval_*.py`；持久队列由默认开发 Compose 的 `rag-eval-worker` 执行。声明式矩阵在 `Document/rag_eval_production_acceptance_matrix.json`，安全 runner 在 `tests/acceptance/run_rag_eval_production_acceptance.py`：

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
python -m pytest tests/unit
```

这条路径只作为环境受限时的回退，不应由 Codex 或开发者反复临时安装依赖来充当团队测试环境。



只有准备好隔离 Docker 主从、PostgreSQL checkpoint、管理员前端生产构建和 Playwright 凭据后，才运行：

```powershell
powershell -ExecutionPolicy Bypass -File tests/run_admin_32_e2e.ps1
```

两个 PowerShell 文件是稳定的人工入口。E2E Python 辅助模块位于 `tests/e2e/admin/`，不应作为普通 pytest 测试自动执行，也不属于 `unit-test` 服务的执行范围。
