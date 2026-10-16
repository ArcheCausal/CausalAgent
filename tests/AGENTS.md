# tests AGENTS.md

生效目录：tests/ 及其未被更近 AGENTS.md 覆盖的子目录。

职责：约束测试代码的分层归属、真实依赖的取证方式、默认执行范围，以及测试与压测的目录边界。

## 分层归属

- 先判断依赖范围，再选择业务目录：只依赖单个模块或最小 Flask 应用、外部依赖用 fake 的测试放 `unit/`；需要跨模块静态契约、部署边界或 migration 链路的放 `integration/`；需要真实 MySQL/PostgreSQL、主从、Docker、true Flask 或真实模型的放 `e2e/`、`acceptance/` 或 `smoke/`。
- 原来堆在 `tests/` 顶层的 focused tests 已按业务归位：RAG 评测台本体进 `unit/rag_eval/`，多模态与知识库 RAG 契约进 `unit/agent/`，部署契约进 `integration/deployment/`，迁移契约进 `integration/migrations/`。新增同类测试时沿用这套归属，不要再放回 `tests/` 顶层；文件下沉到三级目录后，`Path(__file__).resolve().parents[3]` 才是仓库根，`parents[1]` 会指到 `tests/unit/` 一类中间目录。
- `acceptance/`、`smoke/`、`e2e/`、`spike/` 下是顶层脚本或 test 模块，`run_*.ps1` 是人工入口；它们不属于 `unit-test` 容器执行范围，不得写成随 `pytest` 自动执行。
- `dataset/` 只放测试数据，`support/` 只放测试共用工具，二者都不修改被测代码。

## 真实依赖与取证

- 依赖 MySQL、PostgreSQL、主从副本、Docker、管理员前端构建或真实模型的测试：必须真跑并记录结果，不得用 mock、fake 或只让断言通过的方式冒充实跑。
- 改动网络隔离、挂载、迁移或容器编排的测试，运行前按 `docker-compose.test.yml` 与 `tests/README.md` 确认边界；容器网络被刻意禁用的服务不得访问外部服务。
- 每次改动的结论必须分别说明已执行的测试、未执行的测试和原因，不能用 unit 测试冒充集成或生产验收。

