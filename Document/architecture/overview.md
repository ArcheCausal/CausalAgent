# 系统架构总览

文档职责：记录 CausalAgent 当前的进程边界、组件职责、运行拓扑和主要数据流。

适用范围：修改 Flask 应用注册、Web/worker/monitor/清理进程、Compose 服务或跨组件数据流时使用；Job 和文件的细粒度生命周期见 [`job-file-lifecycle.md`](job-file-lifecycle.md)。

## 系统边界

CausalAgent 的 Web 入口是 `CausalAgent.py`，它调用 `app/__init__.py` 的 `create_app()`。应用启动时先执行数据库就绪检查，再注册 `auth`、`chat`、`files`、`agent`、`main`、`admin` 和 `admin_page` 七个 blueprint。Web 进程只负责认证、短请求、Job 入队和 SSE 推送，不在请求线程中执行 Agent、MCP 或 RAG 长任务。

桌面入口 `Run_causal.py` 委托给 `windows-client/causalagent_desktop/`，只创建 Windows WebView2（Edge Chromium）壳并加载已经配置的 CausalAgent origin。开发默认地址是 `http://127.0.0.1:5001/dashboard`，Release 包嵌入正式 HTTPS origin 并默认进入 `/dashboard`；桌面端不包含 Flask、MySQL、worker、模型或第二套前端，服务器仍必须先提供现有 Web 页面、Cookie Session、API、SSE 和文件能力。同源下运行四个独立构建的前端：官网 `website-frontend/` 提供 `/`、`/product`、`/pricing`、`/about`、`/docs`、`/changelog` 与 `/auth/sign-in`、`/auth/sign-up`；普通用户应用 `chat-frontend/` 提供 `/dashboard*`；RAG 评测台 `app/rag_eval/frontend/` 提供 `/rag-eval`；管理员前端 `admin-frontend/` 提供 `/admin*`。页面按权限表授权，各前端的构建产物都由 Flask 在同源路径提供。

## 进程与职责

| 组件 | 当前入口 | 主要职责 | 持久化边界 |
| --- | --- | --- | --- |
| Web | `python CausalAgent.py` 或 Gunicorn `CausalAgent:app` | 认证、会话/文件短请求、Job 入队、普通用户 SSE、管理员 API | MySQL 业务表；读取 PostgreSQL 安全摘要 |
| Agent worker | `python -m app.agent.worker` | 领取 Job、运行父图/Deep Agent/RAG/Web、通过私网 MCP 调用算法、写事件和终态结果 | MySQL Job/Event；PostgreSQL 父子 checkpoint 与长期记忆 Store |
| causal-mcp | `python -m Agent.CausalAgentMCP.app` | 校验签名与 Job lease，以有界独立进程执行因果算法和精确取消 | MySQL primary strong read；不持有业务状态 |
| monitor | `python -m Database.monitor_worker` | 采集 MySQL/PostgreSQL 运行事实并写共享快照 | MySQL monitor 快照和在线配置 |
| Agent 持久化清理 | `python -m Database.agent_persistence_cleanup_worker` | 轮转消费两张 MySQL outbox，删除 PostgreSQL 父子图 checkpoint 和用户长期记忆 Store namespace | MySQL outbox；PostgreSQL checkpoint 与 Store |
| db-bootstrap | `python -m Database.bootstrap` | 建库、Alembic migration、PostgreSQL checkpoint schema setup | 修改初始化目标数据库 |

worker 的 Job 并发单元是 slot。一个 worker 进程可以启动多个 slot；所有 slot 共享进程级
MCP execute/control Client pool、静态 Registry、PostgreSQL Store/checkpointer 和编译后的
父图，每次 Job 只创建独立的可信 runtime context。具体约束见
[`agent-runtime.md`](agent-runtime.md) 与 [`mcp-runtime.md`](mcp-runtime.md)。

## 主要数据流

```mermaid
flowchart LR
    Browser[普通用户或管理员浏览器] --> Web[Flask Web]
    Web -->|strong write/read| MySQL[(MySQL 主库)]
    Web -->|SSE 轮询| Events[(analysis_job_events)]
    Worker[Agent worker slots] -->|领取 Job / 写 Event| MySQL
    Worker -->|父图/Deep Agent checkpoint 与 Store| PostgreSQL[(PostgreSQL)]
    Worker -->|私网 Streamable HTTP| MCP[causal-mcp]
    MCP -->|lease strong read| MySQL
    Web -->|只读安全摘要| PostgreSQL
    MySQL -->|checkpoint_cleanup_outbox / user_memory_cleanup_outbox| Cleanup[Agent 持久化清理 worker]
    Cleanup -->|adelete_thread(父图与子图) / Store namespace purge| PostgreSQL
    Monitor[monitor worker] -->|采集| MySQL
    Monitor -->|quick/deep 只读检查| PostgreSQL
    MySQL --> Snapshots[(database_monitor_snapshots)]
    Snapshots --> Web
```

跨库删除不使用分布式事务。MySQL 业务删除和 cleanup outbox 在同一 MySQL 事务提交，
cleanup worker 之后异步删除 PostgreSQL 父图 checkpoint、Deep Agent 子图 checkpoint 和
被删除用户的长期记忆 namespace，详见 [`job-file-lifecycle.md`](job-file-lifecycle.md)。
用户接口和管理员操作查询接口分别暴露后台清理状态。

## Docker 拓扑

默认开发 Compose `docker-compose.yml` 当前包含 16 个服务：`mysql-primary`、
`mysql-replica`、`postgres-checkpoint`、`db-bootstrap`、`app`、`worker`、
`causal-mcp`、`monitor`、`agent-persistence-cleanup`、`rag-eval-worker`、
`searxng`、`valkey`、`loki`、`alloy` 和 `grafana`。
`db-bootstrap` 成功后，依赖它的运行服务才启动；开发拓扑没有自动故障切换。

当前生产 Compose `docker-compose.prod.yml` 实际包含生产 MySQL、PostgreSQL checkpoint、
`db-bootstrap`、`app`、Agent `worker`、私网 `causal-mcp`、
`agent-persistence-cleanup`、`monitor` 和独立 `rag-eval-worker` 服务；它不提供开发拓扑的
MySQL replica、SearXNG/Valkey、Loki/Alloy/Grafana 或自动故障切换。部署入口见
[`../development/deployment.md`](../development/deployment.md)。

## 组件边界

- `app/` 负责 HTTP、认证、持久化服务编排和 Job worker 外壳，不承载因果算法实现。
- `Agent/` 负责 LangGraph 图、结构化输出、MCP/RAG 工具节点和因果工具。
- `Database/` 负责连接、迁移、bootstrap、monitor、checkpoint setup 和 Agent 持久化清理 worker。
- `website-frontend/` 只负责官网公开页面、登录注册表单和公开内容展示；登录与注册只调用 `/api/login`、`/api/register`，不做授权判定。
- `chat-frontend/` 只负责登录后的普通用户工作区（`/dashboard*`）、API schema、Pinia 领域状态、Job SSE 传输和声明式渲染；其 SSE 传输使用 `fetch()` + `ReadableStream`，不改变 Flask SSE 路径和内容，未登录时把浏览器交给 `/auth/sign-in`。
- `app/rag_eval/frontend/` 只负责 RAG 评测台页面与其审核工作流；页面只通过 `/api/rag_eval` 访问后端。
- `admin-frontend/` 只负责管理员页面与 API 消费，不替代 Flask 后端，也不直接连接数据库。
- `Document/admin/` 只描述管理员如何消费系统能力；数据库内部机制归 `Document/database/`。

修改这些边界时，必须同时核对对应目录的局部 `AGENTS.md` 和本页链接的权威文档。
