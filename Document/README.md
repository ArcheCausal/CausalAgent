# CausalAgent 技术文档

文档职责：作为 `Document/` 当前技术事实库的入口，定义各主题的唯一权威归属和阅读路径。

适用范围：面向开发者和 AI agent 的系统架构、API、数据库、开发运维与管理员模块说明；执行约束以根目录和局部 `AGENTS.md` 为准。

## 使用方式

`Document/` 描述系统当前是什么、如何工作以及为什么这样设计。文档中的命令、路由、表名和配置必须以当前代码、迁移、Compose 和测试入口核对后为准；若实现与文档冲突，应先修正文档事实或指出实现偏移，不应把旧文档当作代码依据。

执行规则见根目录 [`AGENTS.md`](../AGENTS.md) 以及各实现目录的局部规则文件。文档职责与修改边界见 [`documentation.md`](documentation.md)。

## 文档导航

### 架构

- [`architecture/overview.md`](architecture/overview.md)：进程边界、主要数据流、运行拓扑和组件职责。
- [`architecture/agent-runtime.md`](architecture/agent-runtime.md)：Web、Job worker、LangGraph、MCP、RAG、Web Search、结构化输出和用户事件流。
- [`architecture/mcp-runtime.md`](architecture/mcp-runtime.md)：MCP 客户端池、服务端算法执行池、Worker 通信、HTTP/TCP 与精确取消机制。
- [`architecture/job-file-lifecycle.md`](architecture/job-file-lifecycle.md)：Session、Job、输入账本、文件库、checkpoint 与跨库清理生命周期。
- [`architecture/rag-evaluation.md`](architecture/rag-evaluation.md)：隔离评测、来源、staged index、active release、评测 worker 与生产切换边界。

### API

- [`api/conventions.md`](api/conventions.md)：鉴权、CSRF、request ID、错误结构、分页、敏感内容和 SSE 通用约定。
- [`api/agent-jobs.md`](api/agent-jobs.md)：分析 Job 创建、恢复、取消、幂等与 SSE。
- [`api/chat-files.md`](api/chat-files.md)：普通用户会话、消息和文件接口。
- [`api/rag-eval.md`](api/rag-eval.md)：RAG 来源、摄取、评测、治理、release 与统一运行生命周期 API。

### 数据库

- [`database/overview.md`](database/overview.md)：MySQL 业务数据、PostgreSQL checkpoint 和跨库边界。
- [`database/consistency.md`](database/consistency.md)：主从读写、一致性级别、账号职责和连接池。
- [`database/migrations-checkpoints.md`](database/migrations-checkpoints.md)：bootstrap、Alembic、checkpoint setup、cleanup outbox 与迁移风险。
- [`database/monitoring.md`](database/monitoring.md)：monitor worker、共享快照、在线配置、quick/deep audit 和 cleanup 运行状态。

### 开发

- [`development/setup.md`](development/setup.md)：本地、Docker 和四个前端的开发入口。
- [`development/testing.md`](development/testing.md)：后端、前端、迁移、Web Search/Observability、RAG 多模态与隔离评测的验证矩阵。
- [`development/deployment.md`](development/deployment.md)：镜像构建、16 服务开发 Compose、staging gateway/guard 边界、生产现有拓扑和 release 发布边界。
- [`development/website-frontend.md`](development/website-frontend.md)：官网公开页面与登录注册页面的入口、认证回跳、内容组织、构建和验收边界。
- [`development/chat-frontend.md`](development/chat-frontend.md)：普通用户应用 Vue 前端的 `/dashboard` 入口、地址与会话映射、SSE、状态边界、构建、验收和回退。
- [`../windows-client/README.md`](../windows-client/README.md)：Windows WebView2 桌面壳的独立依赖、配置、打包和 smoke 验收入口。
- [`development/observability.md`](development/observability.md)：日志字段、事件目录、上下文关联、降噪、敏感信息边界和验收状态。
- [`development/agent-tool-routing-history.md`](development/agent-tool-routing-history.md)：因果 MCP、RAG 与联网搜索从旧父图到 Deep Agent 的工具选择契约历史与调研结论。
- [`documentation.md`](documentation.md)：文档归属、维护、链接和日志规则。

### 前端设计系统

- [`design-system/README.md`](design-system/README.md)：共享设计系统的文档入口、代码位置和归属原则。
- [`design-system/design-decision.md`](design-system/design-decision.md)：已确认的视觉方向、对比度边界、字体与动效规则，以及各端允许保留的差异。
- [`design-system/tokens.md`](design-system/tokens.md)：token 的命名、数值、与官网原型变量的对应关系和使用规则。
- [`design-system/components.md`](design-system/components.md)：共享组件的属性、变体、状态要求和使用边界。
- [`design-system/current-state.md`](design-system/current-state.md)：四个前端改造前的样式现状和迁移顺序。

### RAG、联网搜索与评测

- RAG 查询、Web Search 子图及其运行期降级的唯一技术说明在 [`architecture/agent-runtime.md`](architecture/agent-runtime.md)；本入口只提供导航，不复制实现细节。
- RAG evaluation、release readiness、worker drain、active pointer 与多模态产物保留规则在 [`architecture/rag-evaluation.md`](architecture/rag-evaluation.md)；HTTP 契约见 [`api/rag-eval.md`](api/rag-eval.md)。
- RAG evaluation worker、隔离来源/产物目录和生产验收 runner 的执行边界在 [`development/deployment.md`](development/deployment.md) 与 [`development/testing.md`](development/testing.md)；RAG 评测策略和 API 仍以代码与对应 API 文档为准。

### 管理员模块

- [`admin/README.md`](admin/README.md)：管理员模块边界与内部索引。
- [`admin/architecture.md`](admin/architecture.md)：Flask 管理员 API、Vue 页面、鉴权和共享能力消费关系。
- [`admin/api.md`](admin/api.md)：管理员页面和 API 的完整契约。
- [`admin/development.md`](admin/development.md)：管理员前端构建、开发入口和发布依赖。
- [`admin/testing.md`](admin/testing.md)：管理员专项代码级测试与部署静态契约。

## 归属原则

系统级 checkpoint、cleanup worker、MySQL 主从、数据库连接和 monitor 内部机制只在 `database/` 维护；管理员页面如何消费这些能力只在 `admin/` 维护。Job、文件冻结和普通用户 SSE 的业务生命周期只在架构/API 对应页面维护，管理员 API 只引用其消费契约。
