# Agent/AGENTS.md

生效目录：`Agent/` 及其子目录。

负责约束的修改类型：LangGraph 图、State/路由、结构化输出、MCP server/tool、RAG、因果分析工具、后处理和报告输出。

## 修改前必须阅读

- 必须阅读 [`Document/architecture/agent-runtime.md`](../Document/architecture/agent-runtime.md)、[`Document/architecture/job-file-lifecycle.md`](../Document/architecture/job-file-lifecycle.md) 和 [`Document/development/testing.md`](../Document/development/testing.md)。
- 必须从当前调用者开始检查 worker runtime、graph runner、对应 State、工具注册、错误路径和测试；不能只修改一个节点后假设运行时会自动适配。
- 修改 MCP、RAG 或 worker 初始化时，必须检查 `app/agent/worker/runtime.py`、`bootstrap.py`、Docker Compose 环境变量和知识库挂载。

## Graph、输出与工具规则

- 条件路由必须读取显式 State 字段，例如 `route_decision`、`dataset_validation_status`；禁止用展示消息文本猜测控制流。
- 涉及到修改agent链路的，需要告诉用户修改后的逻辑和目前的逻辑区别。
- 结构化输出必须使用统一的 `Agent/llm_structured_output.py` 入口和当前 function calling 约定；修改 thinking、tool choice 或 schema 时必须同步检查调用器和测试。
- MCP planner 使用原生 Tool Calls
- 当前工具调用契约是业务只读而非数据库完全只读：读取冻结文件允许更新 `last_accessed_at` 和 `access_count`，取消不会回滚该运行审计副作用。新增业务写工具前必须设计工具幂等键、写入 fencing、补偿/确认语义，并明确取消后的外部副作用边界。
- RAG readiness 与完整加载是两个阶段；worker 启动只轻量校验 active pointer/release，失败时标记 `rag_unavailable` 并继续无 RAG 运行，不得在启动中擅自加载 embedding、Chroma、BM25 或回答模型。
- 因果工具必须记录输入限制、矩阵方向、边权语义和方法假设；修改 DirectLiNGAM 时必须保持连续数值 CSV 和 `target_to_source` 契约。

## 修改后验证

- 必须覆盖成功路径、工具不可用、结构化解析失败、超时/异常、路由字段缺失和用户事件脱敏。
- Agent 测试通过不代表 Job/worker/API 已验证；跨层变更必须追加 `tests/unit/agent/`、`tests/unit/` 或 Docker 验证，并在结果中区分真实模型/MCP 是否运行。
