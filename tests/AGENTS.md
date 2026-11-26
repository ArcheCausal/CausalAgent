# tests AGENTS.md

生效目录：tests/ 及其未被更近 AGENTS.md 覆盖的子目录。

职责：约束测试代码的分层归属、真实依赖的取证方式、默认执行范围，以及测试与压测的目录边界。

## 分层归属

- 先判断依赖范围，再选择业务目录：只依赖单个模块、纯函数或最小 Flask 应用且外部依赖使用 fake 的测试放 `unit/`；需要多个实际组件之间数据传递、生命周期和协作行为的放 `integration/`；只做跨文件配置一致性、部署约束、迁移图和静态政策的放 `contracts/`；需要真实 MySQL/PostgreSQL、主从、Docker、true Flask 或真实模型的放 `e2e/`、`acceptance/` 或 `smoke/`。
- `contracts/` 按领域分成 `deployment/`、`database/` 和 `observability/`。读取 Dockerfile、Compose、发布 workflow、`.dockerignore`、migration 文件或日志入口的项目必须放在这里，因为 CI 会用单独的执行组运行它们；需要 Git 或 Docker CLI 的契约应显式说明工具需求，不满足时只能跳过并保留未执行结论。
- 大文件按职责拆分，共享 fixture 单独成模块：多模态测试分为核心与检索、解析、远程视觉、索引发布四个文件，`multimodal_fixtures.py` 只放本地 fixture。拆分后必须保持覆盖集合不变，并用 `--collect-only` 比对迁出前后数量。
- `tests/` 顶层的 focused tests 已全部归位，新测试不要再放回顶层。三级目录中的仓库根是 `Path(__file__).resolve().parents[3]`，`parents[1]` 会指到 `tests/unit/` 一类中间目录。
- `acceptance/`、`smoke/`、`e2e/`、`spike/` 下是顶层脚本或 test 模块，`run_*.ps1` 是人工入口；它们不属于 `unit-test` 容器执行范围，不得写成随 `pytest` 自动执行。
- `dataset/` 只放测试数据，`support/` 只放测试共用工具，二者都不修改被测代码。

## 真实依赖与取证

- 依赖 MySQL、PostgreSQL、主从副本、Docker、管理员前端构建或真实模型的测试：必须真跑并记录结果，不得用 mock、fake 或只让断言通过的方式冒充实跑。
- 默认 pytest 范围由 `pytest.ini` 的 `testpaths` 限定为 `tests/unit`、`tests/integration` 和 `tests/contracts`，并排除 `smoke` 与 `db_integration` 标记；需要真实数据库的用例放在 `e2e/`、`acceptance/` 或 `smoke/`，用显式命令运行。
- 改动网络隔离、挂载、迁移或容器编排的测试，运行前按 `docker-compose.test.yml` 与 `tests/README.md` 确认边界；容器网络被刻意禁用的服务不得访问外部服务。
- 每次改动的结论必须分别说明已执行的测试、未执行的测试和原因，不能用 unit 测试冒充集成或生产验收。

