# CausalAgent AGENTS.md

生效目录：仓库根目录及所有未被更近的 AGENTS.md 覆盖的子目录。

负责约束的修改类型：全局协作规则、代码/文档变更边界、跨模块核对、敏感信息保护、验证义务和 Git 工作方式。系统当前事实不在本文件完整展开，统一从 Document/ 导航。

## 全局工作规则

- 必须使用中文回复；需要解释时保持完整段落和清晰逻辑，不用零散结论替代推理。
- 必须先读后写：先确认调用入口、相关实现、schema/migration、配置、前端调用、测试和权威文档，再修改文件。
- 使用渐进式阅读文档方法，首先理解这次任务修改范围，然后再阅读涉及到板块的文档。
- 必须以当前实现为准，不能因为旧 README、日志或设计文本写过某行为就假设代码仍然如此；发现冲突时要在修改前说明并修正文档或实现边界，**目前文档系统和agents.MD系统，均使用嵌套系统，需要进入深层目录修改具体内容**
- 必须保持最小必要改动，禁止无关重构、全局风格清理和为未来场景预埋复杂抽象。
- 必须保留用户已有的未提交改动；不能使用 git reset --hard、git checkout -- 或其他强制覆盖命令。
- 禁止删除重要文件、数据库数据、迁移历史、知识库索引或生成产物。确实需要删除时必须先让用户明确处理。
- 不得输出或提交 .env、密码、API key、Cookie、Token、数据库连接串、用户文件正文。
- 默认不主动提交、推送、建 PR 或改分支；完成后提供建议的提交信息、批次和 PR/MR 文案，完整功能实现后，写入日志文件之后，单独输出一份更改的完整日志给用户，
- 解释复杂内容时善用可视化
- 保持简洁直接，区分事实和猜测
- 基于可靠来源工作，必要时调用相关skills/mcp获取官方文档和事实，可用的官方文档和事实有：
  - langchain MCP
  - langchain-reference MCP

- 不偏离用户目标和约束
- 合理使用子Agent，避免无意义并行
- 修改代码保持克制，不做无关重构
- 禁止私自用 Git 回滚任何代码
- 不允许在实现或者测试的时候使用任何 mock、假的、欺骗的、只为了通过测试而 workaround 的方式来欺骗我
- 你在发现任何文档或者代码有错误的时候，你的更新不要保留任何错误痕迹。我们不需要任何错误的记录。

## 文档体系

Document/ 是开发者和 AI 使用的当前技术事实库，回答“系统现在是什么、如何工作、为什么这样设计”。每个新建或重构文档的标题后必须立即写出“文档职责”和“适用范围”。

- 入口：Document/README.md
- 架构：Document/architecture/
- API：Document/api/
- 数据库：Document/database/
- 开发与维护：Document/development/
- 管理员模块：Document/admin/

AGENTS.md 只写“修改时必须怎么做”的执行约束，不复制完整系统说明。当前目录的局部规则如下，修改对应模块时必须阅读：

- Database/AGENTS.md：migration、主从、checkpoint、monitor 和危险数据库操作。
- app/agent/AGENTS.md：Job、幂等、SSE 脱敏、checkpoint 和 worker。
- Agent/AGENTS.md：LangGraph、结构化输出、MCP、RAG 和因果工具。
- admin-frontend/AGENTS.md：TypeScript、管理员 API、构建和浏览器验证。
- observability/AGENTS.md：共享日志运行时、事件目录、降噪、脱敏和采集拓扑。
- packages/design-system/AGENTS.md：设计 token、品牌基础样式、共享组件契约和本包自检。

## 跨模块不可省略的约束

- 修改 Job 或文件流程时，必须核对 Session 真实存在/归属、analysis_job_inputs 输入账本、文件快照、active Job 唯一约束、worker lease/fencing、SSE Last-Event-ID 和 checkpoint cleanup outbox。
- 修改数据库 schema、读写路径或 Compose 时，必须分别核对 migration、check_database_readiness()、strong/eventual read、主从回退、PostgreSQL checkpoint 和 Docker 服务依赖。
- 修改多模态索引目录的挂载、发布写入位置或容器卷时，必须核对 Agent/knowledge_base/multimodal_indexes 的双重角色（运行期读取与发布写入）、命名卷与宿主 release 的同步方向、multimodal_runtime/active_index.json 与卷内内容的一致性、readiness 检查与实际打开向量库的差异，并同步 Document/development/deployment.md 的挂载说明。
- 修改 Agent、MCP、RAG 或 worker 初始化时，必须核对 worker runtime/bootstrap、slot 资源、显式 State 路由、结构化输出配置、工具失败路径和公共事件适配器。
- 修改管理员后端或前端时，必须核对实时主库授权、CSRF、request ID、分页上限、敏感读取审计、受控写入幂等和 401/403/empty/error 状态。
- 修改目录、启动方式、schema 或部署事实时，必须同步检查唯一权威 Document/ 页面和局部 AGENTS.md。
- 测试的时候需要阅读 tests/README.md 相关内容

## 验证义务

- 任何代码改动都必须运行与风险直接相关的测试；依赖 MySQL/PostgreSQL、主从、Docker、浏览器或真实模型的证据必须分别说明，不能用 unit 测试冒充。
- 文档改动至少必须检查相对链接指向现存文件、顶部职责声明、失效路径引用、git diff --check 和日志 rename 纯度；未明确要求修改根 README 时，另须确认 `git diff -- README.md` 为空；明确要求时则检查 README 命令、链接和目录导航。
- 迁移、数据库、管理员 E2E 和 worker 变更的具体命令以 Document/development/testing.md 和局部 AGENTS.md 为准。
- 如果环境限制导致某项验证未执行，最终答复必须明确未执行内容、原因和残余风险。

## 日志与 Git

- CHANGELOG.md 是根目录追加式开发日志；历史正文禁止改写，只允许在文件末尾追加新记录。
- 不需要说明验证结果，只需要说明更改的功能即可
  - 日志系统命名规则：
    ```
    ---
    y.m.d
    - 【父模块：总更改说明】
      - 【子模块：更改说明】：详细更改说明
    ```
    相同日期的更改请合并。
- 分支名遵循 keyword(function)/dec，提交标题遵循 keyword(function):dec。
