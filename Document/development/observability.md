# 日志契约

文档职责：作为运行日志的唯一权威页面，冻结 v1 JSON 字段、第二阶段事件目录、上下文关联、降噪、隐私边界和验收状态。

适用范围：Web、Job worker、monitor、MCP、数据库 bootstrap、Agent 持久化清理 worker 等进程的运行日志；管理员审计日志、MySQL/PostgreSQL 引擎日志和浏览器开发者控制台不归入本契约。

## 1. 当前结论与实施边界

共享 JSON stderr 运行时位于 [`observability/logging_runtime.py`](../../observability/logging_runtime.py)，机器可校验的事件目录位于 [`observability/event_catalog.py`](../../observability/event_catalog.py)，进程内转移/恢复和重复事件限频位于 [`observability/noise_control.py`](../../observability/noise_control.py)。Web、worker、monitor、MCP 和 maintenance 的自有运行日志统一通过 `log_event()` 写入标准 `logging`；调用方不能自由传入级别、分类或消息。

共享日志已经把请求、Job、worker slot、LangGraph node 和 MCP tool 的上下文贯通，并收敛 Web、Agent/RAG、数据库、monitor 和 Agent 持久化清理 worker 的运行日志。Deep Agent 复用同一日志目录和 SSE 适配边界：内层 ToolMessage 不直接落入公共事件，工具生命周期只生成受控 `tool_call_result` 状态；`finalization_status` 只进入最终结果数据。该接入没有新增数据库迁移、HTTP API 或管理员前端页面。

默认开发 Compose 已接入 Alloy、Loki 和 Grafana，生产 Compose 仍不包含这套拓扑。第二阶段代码与静态测试已落地，但真实 Docker、Alloy positions、Loki 检索和受控故障矩阵尚未取得通过证据时，本阶段不得标记完成，也不得把本地静态检查当作端到端验收。

第三阶段提供两个独立 Grafana 看板：异常日志看板只展示 `warning`、`error`、`critical`；MCP Job 时间线看板合并 worker 和 MCP 的 INFO 级生命周期、慢调用、失败与取消事件。两者都不新增 Flask 日志查询 API、Vue 管理员日志页、数据库表或管理员导航；Alloy 和 Loki 仍采集、保存 INFO/DEBUG。当前只记录看板实现与静态合同，真实 Grafana/Loki、浏览器和故障场景验收仍需在开发或隔离环境人工完成。

## 2. v1 JSON 契约

### 2.1 通用字段

目标输出是单行、UTF-8、合法 JSON。所有受管记录都具备下表中的基础字段；第三方 logger 或显式允许的旧离线脚本若进入共享 handler，`event_code` 和 `category` 可以为 `null`，自有应用运行路径不再依赖这种过渡形态。

| 字段 | 类型/约束 | 规则 |
| --- | --- | --- |
| `timestamp` | string | UTC RFC3339，使用 `Z` 表示 UTC；不使用本地时间 |
| `level` | string | 仅允许 `debug`、`info`、`warning`、`error`、`critical` |
| `service` | string | 仅允许 `web`、`worker`、`monitor`、`mcp`、`maintenance` |
| `environment` | string | 当前部署环境名；不得包含密码、Token、连接串或主机凭据 |
| `event_code` | string/null | 受管事件使用小写点分式，例如 `logging.serialization_failed`；旧记录为 `null` |
| `category` | string/null | 受管事件仅允许 `request`、`lifecycle`、`dependency`、`security`；旧记录为 `null` |
| `message` | string | 经过脱敏的短摘要，不记录原始用户输入、提示词或文件正文 |
| `details` | object/null | 由 `event_code` 定义允许字段；禁止把任意请求 JSON 或异常对象原样放入 |
| `exception_type` | string/null | 只记录异常类名，不记录异常值或原始错误文本 |
| `stack` | array/null | 最多 12 个清理后的代码帧；不含异常消息、源码行、局部变量或完整文件系统路径 |
| `truncated` | boolean | 任一字段或整行发生截断时为 `true`，否则为 `false` |
| `logger`、`module`、`function` | string/null | 来自 `LogRecord` 的来源信息；`function` 对应 `funcName`，三者都不作为 Loki 标签 |

`event_code` 必须匹配小写点分式约束：`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`。`category` 的四个值固定如下：

- `request`：HTTP 请求入口、参数拒绝和请求级结果；不携带正文。
- `lifecycle`：进程、Job、worker lease 和维护任务的启动、停止、状态转换。
- `dependency`：MySQL、PostgreSQL checkpoint、LLM、MCP、RAG 等外部依赖的可观测结果。
- `security`：认证、授权、敏感读取和安全策略拒绝；只记录结论和稳定原因码。

### 2.2 关联字段和标签边界

`request_id`、`user_id`、`session_id`、`job_id`、`invocation_id`、`worker_slot`、`node`、`tool`、`instance` 只能作为 JSON 字段。它们不进入 Loki 标签，不在日志采集层建立索引；后续 Alloy 仅把低基数的 `service` 映射为 `service_name`，并按 `environment`、`level`、`category` 选择标签。匿名访问事件的 `visitor_hash` 属于同一类正文关联字段，同样不进入标签。

`details` 必须按事件码定义字段白名单。上下文 ID 不能重复放进 `details`；未知事件码、未知键、错误类型、越界值和非法上下文都降级为固定的 `logging.contract_invalid`，只携带有限 `violation` 枚举，不回显原键和值，也不改变业务控制流。

上下文通过 `bind_log_context()` 返回的不透明 token 绑定，必须按后进先出顺序交给 `reset_log_context()`；`log_context()` 委托这两个接口。`current_log_context()` 返回去敏后的 `dict[str, str]` 副本，供 MCP 父进程读取可信上下文。连续请求、并发请求、异步任务、`asyncio.to_thread()` 和多个 worker slot 必须保持隔离。

Flask 在确认 `X-Request-ID` 后立即绑定 `request_id`，只有主库确认身份和资源归属后才绑定 `user_id/session_id/job_id`，teardown 无条件逆序清理。worker 从 Job 行绑定原始 `request_id/user_id/session_id/job_id` 和进程内 `worker_slot`；node/tool 包装器继续增加 `node/tool`。`instance + worker_slot` 用于定位运行实例，真实 `hostname:slot` lease 标识仍只用于业务数据库。

### 2.3 体积、异常和降级

所有限制按 UTF-8 字节数计算：

| 部分 | 上限 |
| --- | ---: |
| `message` | 2 KiB |
| 序列化后的 `details` | 4 KiB |
| `stack` | 8 KiB，最多 12 帧 |
| 完整 JSON 行 | 16 KiB |

项目代码栈路径转换为仓库相对路径，依赖代码只保留模块或文件基名、函数和行号。超限时必须先截断可截断字段，再保证输出仍是合法 JSON，并设置 `truncated=true`。序列化最终失败或 stderr handler 自身失败走非递归最小写入，事件码固定为 `logging.serialization_failed`，不得包含原对象或原始异常文本，也不得影响业务控制流。

`FailureTransitionTracker` 用于数据库连接、主从回退、lease、monitor 和 cleanup 等高频后台路径：首次失败和原因签名变化立即记录，相同失败最多每 5 分钟提醒一次并携带 `suppressed_count`；存在恢复事件的目录项只记录一次恢复并携带 `downtime_ms/failure_count`，数据库连接成功则静默复位。慢 SQL 使用 `statement_digest` 的 5 分钟 LRU 限频，最多保留 1024 个键。请求或 Job 的最终失败不经过该抑制器。

## 3. 运行入口、输出边界与隐私

| service | 入口与受管边界 | 当前记录内容 |
| --- | --- | --- |
| `web` | `CausalAgent.py`、`app/__init__.py`、Flask 请求上下文 | 启动结果、未处理或受控 5xx、Job 创建结果、真实安全拒绝和匿名访问事件 |
| `worker` | `app/agent/worker/__main__.py`、slot/runtime/execution | slot、Job、lease、node 最终降级和 cleanup 聚合结果 |
| `monitor` | `Database/monitor_worker.py`、`Database/monitoring.py`、`app/db.py` | 快照、配置、锁、主从、连接和慢 SQL 的转移/恢复事件 |
| `maintenance` | bootstrap、database/checkpoint setup、Agent 持久化清理 worker | 启动边界、两类 outbox attempt 结果和循环级转移/恢复 |
| `mcp` | `Agent/CausalAgentMCP/app.py` 私有容器；兼容路径仍为 `mcp_server.py` stdio 子进程 | 新服务记录收到/拒绝/接受、60 秒慢调用、完成/失败/取消和控制面取消结果；协议输出与应用日志分离，兼容路径日志只写 stderr |

### 3.1 隐私与去重边界

- 消息由事件目录固定，禁止记录用户名、文件名、会话标题、用户输入、提示词、LLM 完整输出、RAG 问题/证据正文、ToolMessage、最终报告、因果图、CSV/文件正文、SQL/参数、Cookie、Token、密码、数据库账号、host、端口、库名、连接 URL、`last_error` 或原始异常文本。
- `details` 只允许事件目录中的稳定枚举、计数、耗时、大小、完整 SHA-256 statement digest 和逻辑别名；运行时递归脱敏是最后防线，不能代替调用边界的字段白名单。
- 同一异常只保留根因边界和最终请求/Job 结果边界。MCP 业务工具结果由子进程记录，父进程只记录 transport 重试耗尽；RAG 内部 catch 不重复记录，Job finalize 每个受影响 Job 最多记录一次降级。
- 正常密码错误、会话自然过期、普通用户误入管理员页面、表单校验、普通 4xx、monitor 命名锁竞争、正常轮询、等待用户输入、用户取消和算法正常环路都不产生 `WARNING/ERROR`。
- 管理员审计表仍是业务审计事实，不与运行日志互相替代；运行日志不能包含管理员操作正文或敏感读取内容。

### 3.2 允许的离线输出

应用运行入口可达代码中的自有 `logging.info/warning/error/critical/exception` 必须由 AST 静态测试阻止。当前只允许 [`Database/audit_before_db_upgrade.py`](../../Database/audit_before_db_upgrade.py) 和未接入生产入口的 [`Database/mysql_checkpointer.py`](../../Database/mysql_checkpointer.py) 保留普通终端日志；其输出不得被容器生产流程调用或采集。`print` 同样由静态清单限制在数据库引导/修复、管理员 CLI、知识库构建/评估和独立算法演示文件中；其中 [`Database/job_execution_upgrade_repair.py`](../../Database/job_execution_upgrade_repair.py) 只输出不含 Job ID、worker 标识和连接信息的 dry-run/执行计数。桌面客户端和浏览器 `console` 不属于服务端运行日志合同，但仍不得用来旁路输出秘密或用户正文。

MCP 不创建应用文件 handler，MCP transport stdout 只允许协议消息；`causal-mcp` 容器通过标准 JSON stderr 接入采集，不能把 CSV、SQL、签名、Token 或异常原文写入事件。MySQL `/var/lib/mysql/mysql-slow.log` 是未被本拓扑采集的数据库引擎日志，不属于事件目录。

### 3.3 第 1.3 步开发采集拓扑

预发 Compose 使用独立的 observability network 和 staging 数据存储，复用同一份 Alloy、Loki 和 Grafana 配置。除 app、worker、causal-mcp、monitor、db-bootstrap、agent-persistence-cleanup 外，预发的 rag-eval-worker 也带有 Alloy 标签，并以低基数的 worker service 分类采集。数据库、SearXNG、Valkey 和观测容器自身不带采集标签。预发 Grafana 不映射宿主端口，只通过 gateway 的同源 `/grafana/` 访问；其持久数据、Loki 数据和 Alloy positions 分别使用独立 staging 命名卷 `rag_eval_staging_grafana`、`rag_eval_staging_loki` 和 `rag_eval_staging_alloy_positions`。该配置不会改变生产 Compose 的服务或数据边界。

默认开发 Compose 在 [`docker-compose.yml`](../../docker-compose.yml) 中增加独立的 `observability_network`，并锁定以下镜像：`grafana/loki:3.7.4`、`grafana/alloy:v1.18.0` 和 `grafana/grafana:13.1.1`。Loki、Alloy 不映射宿主机端口；Grafana 仅映射到 `127.0.0.1:3000`，要求 `GRAFANA_ADMIN_PASSWORD` 非空，并通过 `GF_USERS_DEFAULT_LANGUAGE=zh-Hans` 将未设置个人偏好的账号默认显示为简体中文；账号自己的语言偏好仍具有更高优先级。Loki 数据、Grafana 数据和 Alloy positions 分别使用命名卷，生产 Compose 不复用这些服务或卷。

采集范围由 Compose 静态标签控制：`app`、`worker`、`causal-mcp`、`monitor`、`db-bootstrap` 和 `agent-persistence-cleanup` 才带有 `causalagent_observability=true`。数据库容器和可观测组件自身没有该标签，因此 Alloy 不会递归采集它们。`causal-mcp` 是独立私有容器，使用 `service=mcp` 的 JSON stderr；兼容 stdio 子进程仍由 worker stderr 采集，不把两条路径混写成同一服务事实。

Alloy 先用 `stage.docker` 解包 Docker `json-file` 包装层，再用 `stage.json` 提取 `service`、`environment`、`level` 和 `category`。`drop_malformed=false` 保证非法或旧格式行保留原文，未解析字段不被伪造。最终只保留 `service_name`、`environment`、`level`、`category` 四类低基数标签；`request_id`、`job_id`、`invocation_id`、`user_id`、`session_id`、`node`、`tool`、`instance` 等仍只在 JSON 行正文中。positions 位于 Alloy 的 `/var/lib/alloy/data` 命名卷，重启续读由 `loki.source.docker` 管理。

Loki 使用单节点 TSDB + filesystem，启用 compactor 和 72 小时保留，写入速率为 4 MiB/s、突发 8 MiB，单次查询最多返回 5000 条、查询超时 30 秒。Logs Drilldown 所需的 pattern ingestion、structured metadata、volume endpoint 和 log level discovery 已启用；Grafana 13.1.1 自带 Logs Drilldown，Loki datasource 与独立异常日志看板由 [`observability/grafana/provisioning/`](../../observability/grafana/provisioning/) 自动 provision。

本地验证至少执行：

```powershell
docker compose config --quiet
docker compose pull loki alloy grafana
docker compose run --rm --no-deps alloy validate /etc/alloy/config.alloy
docker compose up -d
docker compose ps
```

`alloy validate` 必须在首次启动和每次修改 `config.alloy` 后执行；它会检查 Alloy 语法、组件引用、必填属性和未知属性，返回非零退出码时不得继续启动或把静态字符串测试当作配置有效证据。

上述固定版本镜像采用最小运行时，默认 Compose 不在容器内部调用 `wget` 或 `curl` 伪造健康检查，也不为 Loki 和 Alloy 暴露宿主机端口。实际验收应以 `docker compose ps`、启动日志、Grafana `/api/health`、Loki 数据源连通性和固定面板是否出现新日志为准；需要单独检查 Alloy 的 `/-/ready` 时，应在临时验证覆盖配置中执行，不改变默认开发拓扑。

随后应在 Alloy 运行期间为 `web`、`worker`、`monitor`、`mcp`、`maintenance` 各写入一个唯一测试事件，查询字段/时间/标签，重启 Alloy 和 Loki 核对 positions，暂停 Loki 核对应用 stderr 不阻塞，并检查 Loki `/loki/api/v1/series` 不出现请求、Job、用户、节点、工具或实例字段标签。代表性负载需另外记录 30 分钟的行数、字节数、stream 数和 72 小时预计磁盘占用；这些数据不能由静态测试推导。

### 3.4 第三阶段独立异常日志看板

固定看板 UID 继续使用 `causalagent-logs`，默认时间范围为最近 1 小时并每 30 秒刷新。页面提供环境、服务、分类和级别筛选；级别选项固定为 `warning`、`error`、`critical`，不能通过看板切换到 INFO/DEBUG。环境、服务、级别和分类继续使用既有低基数标签，未修改 Alloy 标签合同。

看板由异常总量、异常级别趋势、服务分布、分类分布、Top 10 事件码和最近 200 条异常日志组成。`event_code` 仅在 LogQL 中通过 `| json` 解析和聚合；`request_id`、`job_id`、`node`、`tool` 等关联字段只在日志正文中保留，展开日志或进入 Explore 后继续筛选，不新增为 Dashboard 变量或 Loki 标签。

Grafana 继续使用独立账号：开发入口为 `http://127.0.0.1:3000/grafana/`，预发入口为网关同源的 `/grafana/`；两者都不复用 CausalAgent 管理员会话，也不写入 `admin_audit_events`。本阶段不提供 Flask/Vue 日志代理、不嵌入管理员页面、不新增告警通知，并保持生产 Compose 不接入可观测拓扑。

### 3.5 MCP Job 时间线看板

固定 UID 为 `causalagent-mcp-jobs`，默认最近 1 小时、每 10 秒刷新。看板包括慢调用数量、失败与取消数量、按事件码聚合的生命周期趋势，以及最多 500 行、按时间正序展示的 MCP Job 时间线。环境仍使用低基数 Loki 标签；Job ID 与工具筛选是 textbox 正则，查询时经 `| json` 解析正文，不创建高基数标签。

时间线串联 `mcp.client.call.started`、`mcp.request.received/accepted/rejected`、`mcp.tool.slow/finished/failed/canceled` 和 `mcp.client.cancel.*`/`mcp.cancel.finished`。`CAUSAL_MCP_SLOW_LOG_SECONDS` 默认且当前部署值为 60 秒；慢事件只在跨过阈值时记录一次，最终仍必须出现完成、失败或取消终态。该面板用于区分“请求尚未到达 MCP、排队等待、算法运行过慢、已失败、已取消”几类状态，不替代 LangSmith trace 或业务 Job 事件。

### 3.6 匿名访问事件

公开页面的交互通过同源接口 `POST /api/analytics/events` 进入同一套运行日志。该接口不要求登录，也不创建用户、Session、消息、文件或分析 Job；它只接受 `visitor_id` 和最多 10 个事件，请求体上限 8 KiB，事件名、字段名、页面标识和示例标识都必须落在事件目录登记的取值内，任何一项不合法都整体拒绝且不产生部分日志。它不接受客户端时间，成功返回 `202` 和已接受数量；统计写入失败不影响公开页面、登录或真实业务。

- `analytics.public_preview.view`、`analytics.public_preview.demo_open`、`analytics.public_preview.send_click` 和 `analytics.auth.panel_open` 由浏览器上报；`analytics.auth.login_success` 由后端认证路由在登录成功后直接记录，不依赖前端上报的成功状态。普通用户应用在入口拆分后不再上报这些事件，公开预览由官网取代，事件名与页面标识仍按事件目录登记。
- 前端在 `localStorage` 保存一个随机 UUID 作为浏览器匿名标识，后端用现有 `SECRET_KEY` 对该标识做 HMAC-SHA256，日志只写 `visitor_hash`。原始标识、消息正文、文件名、文件内容、Cookie 和 Token 都不进入日志，`visitor_hash` 也不配置为 Loki 标签。
- 日志量控制：同一浏览器会话中，同一页面的 `public_preview.view` 和同一 `demo_key` 的 `demo_open` 各只上报一次，登录面板打开事件不因组件重渲染重复上报，发送点击每次真实点击上报一次；前端使用 `sendBeacon` 或 `fetch(..., { keepalive: true })` 发送且不等待响应，统计请求失败不重试、不阻塞用户操作。

## 4. 第二阶段事件目录

下表与 `EVENT_SPECS` 一一对应。事件码、消息、级别和分类均由目录固定，最后一列只表示允许出现的 `details` 键；具体类型、枚举和大小上限由代码目录校验并由测试锁定。

| 事件码 | 级别/分类 | 固定消息 | 允许详情 |
| --- | --- | --- | --- |
| `logging.serialization_failed` | `error/dependency` | 日志记录序列化失败 | 无 |
| `logging.contract_invalid` | `error/dependency` | 日志事件合同无效 | `violation` |
| `web.startup.ready` | `info/lifecycle` | web 进程启动检查完成 | 无 |
| `web.startup.failed` | `critical/dependency` | web 进程启动失败 | `phase`, `dependency`, `reason_code` |
| `worker.startup.ready` | `info/lifecycle` | worker 进程启动检查完成 | 无 |
| `worker.startup.failed` | `critical/dependency` | worker 进程启动失败 | `phase`, `dependency`, `reason_code` |
| `monitor.startup.ready` | `info/lifecycle` | monitor 进程启动检查完成 | 无 |
| `monitor.startup.failed` | `critical/dependency` | monitor 进程启动失败 | `phase`, `dependency`, `reason_code` |
| `mcp.startup.ready` | `info/lifecycle` | mcp 进程启动检查完成 | 无 |
| `mcp.startup.failed` | `critical/dependency` | mcp 进程启动失败 | `phase`, `dependency`, `reason_code` |
| `maintenance.startup.ready` | `info/lifecycle` | maintenance 进程启动检查完成 | 无 |
| `maintenance.startup.failed` | `critical/dependency` | maintenance 进程启动失败 | `phase`, `dependency`, `reason_code` |
| `worker.slot.ready` | `info/lifecycle` | Worker slot 已就绪 | `tool_count` |
| `worker.slot.failed` | `critical/dependency` | Worker slot 初始化或运行失败 | `phase`, `reason_code` |
| `web.request.unhandled` | `error/request` | 请求发生未处理异常 | `method`, `endpoint`, `route` |
| `web.request.failed` | `error/request` | 请求在受控边界失败 | `method`, `endpoint`, `status_code`, `reason_code` |
| `job.create.accepted` | `info/request` | 分析任务已创建 | `status` |
| `job.create.replayed` | `info/request` | 分析任务幂等重放已接受 | `status` |
| `job.create.failed` | `error/request` | 分析任务创建失败 | `reason_code` |
| `admin.audit.write_failed` | `error/dependency` | 管理员审计事件写入失败 | `action`, `reason_code` |
| `security.login.disabled_account` | `warning/security` | 禁用账号尝试登录 | 无 |
| `auth.login.last_login_update_failed` | `warning/dependency` | 登录后的最后登录时间记录失败 | `reason_code` |
| `security.authorization.denied` | `warning/security` | 已验证用户尝试跨归属访问资源 | `resource_type`, `action`, `reason_code` |
| `security.csrf.rejected` | `warning/security` | CSRF 校验拒绝请求 | `method`, `endpoint`, `reason_code` |
| `security.reauthentication.failed` | `warning/security` | 高风险管理员操作重新认证失败 | `action`, `reason_code` |
| `security.session.revoked` | `warning/security` | 失效安全会话已撤销 | `reason_code` |
| `db.connection.failed` | `error/dependency` | 数据库连接失败 | `source_alias`, `operation`, `reason_code`, `suppressed_count` |
| `db.replica.fallback` | `warning/dependency` | 数据库读取已回退主库 | `source_alias`, `reason_code`, `lag_seconds`, `suppressed_count` |
| `db.replica.recovered` | `info/dependency` | 数据库副本读取已恢复 | `source_alias`, `downtime_ms`, `failure_count` |
| `db.query.slow` | `warning/dependency` | 数据库查询超过慢查询阈值 | `operation`, `duration_ms`, `statement_digest`, `suppressed_count` |
| `worker.job.claimed` | `info/lifecycle` | Worker 已领取分析任务 | `claim_kind`, `attempt`, `lease_epoch` |
| `worker.job.finished` | `info/lifecycle` | 分析任务执行完成 | `attempt`, `duration_ms`, `outcome` |
| `worker.job.interrupted` | `info/lifecycle` | 分析任务已暂停并等待输入 | `attempt`, `duration_ms`, `reason_code` |
| `worker.job.revoked` | `info/lifecycle` | 分析任务执行资格已撤销 | `reason_code`, `status`, `execution_state` |
| `worker.job.failed` | `error/lifecycle` | 分析任务执行失败 | `failure_phase`, `reason_code`, `error_category`, `attempt`, `duration_ms` |
| `worker.job.cleanup_failed` | `error/dependency` | 分析任务执行资源清理失败 | `failure_count`, `phases` |
| `worker.lease.refresh_failed` | `warning/dependency` | Worker lease 刷新失败 | `consecutive_failures`, `suppressed_count` |
| `worker.lease.recovered` | `info/dependency` | Worker lease 刷新已恢复 | `failure_count`, `downtime_ms` |
| `job.node.timeout` | `warning/lifecycle` | Agent 节点最终超时并进入降级路径 | `final_attempt`, `timeout_ms`, `fallback` |
| `job.node.degraded` | `warning/lifecycle` | Agent 节点最终失败并进入降级路径 | `failure_kind`, `final_attempt`, `fallback`, `cause_code`, `schema_name`, `structured_attempts`, `validation_error_count`, `validation_first_type`, `validation_first_loc` |
| `job.postprocess.degraded` | `warning/lifecycle` | 因果分析后处理已降级 | `reason_code`, `affected_count` |
| `chat.attachment.degraded` | `warning/dependency` | 聊天附件解析已降级 | `attachment_type`, `reason_code` |
| `rag.runtime.ready` | `info/lifecycle` | RAG Runtime 初始化完成 | `provider`, `model`, `chunk_count`, `elapsed_ms`, `release_id` |
| `rag.sparse.ready` | `info/lifecycle` | RAG sparse 索引构建完成 | `version`, `documents`, `vocabulary`, `backend`, `elapsed_ms` |
| `rag.startup.unavailable` | `warning/dependency` | RAG 知识库启动检查不可用 | `reason_code` |
| `rag.enrichment.degraded` | `warning/dependency` | RAG 增强结果已降级 | `status`, `reason_code`, `question_count`, `evidence_count` |
| `rag.multimodal.parse_failed` | `error/dependency` | 多模态 RAG 解析失败 | `phase`, `reason_code`, `source_alias`, `page_number`, `image_index`, `table_index`, `status_code`, `fallback_attempted`, `circuit_breaker_open` |
| `mcp.tool.finished` | `info/dependency` | MCP 工具调用完成 | `capability`, `retry_ordinal`, `duration_ms`, `input_bytes`, `result_kind` |
| `mcp.tool.failed` | `error/dependency` | MCP 工具调用失败 | `capability`, `retry_ordinal`, `duration_ms`, `input_bytes`, `reason_code` |
| `mcp.tool.canceled` | `info/dependency` | MCP 工具调用已取消 | `capability`, `retry_ordinal`, `duration_ms`, `reason_code` |
| `mcp.tool.slow` | `warning/dependency` | MCP 工具调用持续时间过长 | `capability`, `duration_ms`, `timeout_seconds` |
| `mcp.request.received` | `info/dependency` | MCP 请求已收到 | `capability`, `retry_ordinal` |
| `mcp.request.rejected` | `warning/dependency` | MCP 请求已拒绝 | `capability`, `reason_code` |
| `mcp.request.accepted` | `info/dependency` | MCP 请求已接受 | `capability`, `retry_ordinal`, `queue_wait_ms`, `timeout_seconds` |
| `mcp.cancel.finished` | `info/dependency` | MCP 取消请求已处理 | `capability`, `cancellation_status` |
| `mcp.client.call.started` | `info/dependency` | Worker 已发起 MCP 调用 | `capability`, `retry_ordinal` |
| `mcp.client.cancel.requested` | `info/dependency` | Worker 已请求取消 MCP 调用 | `capability`, `retry_ordinal` |
| `mcp.client.cancel.finished` | `info/dependency` | Worker MCP 取消请求已完成 | `capability`, `cancellation_status` |
| `mcp.client.cancel.failed` | `warning/dependency` | Worker MCP 取消请求失败 | `capability`, `reason_code` |
| `mcp.capacity.rejected` | `warning/dependency` | MCP 请求因容量限制被拒绝 | `reason_code`, `retry_after_seconds` |
| `mcp.process.recycled` | `warning/dependency` | MCP 算法进程池已回收 | `reason_code` |
| `mcp.client.reconnected` | `info/dependency` | MCP 客户端成员已重连 | `generation`, `pool_lane` |
| `mcp.transport.failed` | `warning/dependency` | MCP transport 最终调用失败 | `reason_code`, `final_attempt`, `duration_ms` |
| `monitor.snapshot.failed` | `error/dependency` | 数据库监控快照采集失败 | `snapshot_key`, `reason_code`, `duration_ms`, `suppressed_count` |
| `monitor.snapshot.recovered` | `info/dependency` | 数据库监控快照采集已恢复 | `snapshot_key`, `downtime_ms`, `failure_count` |
| `monitor.config.degraded` | `warning/dependency` | 数据库监控配置已降级 | `reason_code`, `suppressed_count` |
| `monitor.config.recovered` | `info/dependency` | 数据库监控配置已恢复 | `downtime_ms`, `failure_count` |
| `monitor.lock.failed` | `warning/dependency` | 数据库监控命名锁操作失败 | `snapshot_key`, `reason_code`, `suppressed_count` |
| `monitor.lock.recovered` | `info/dependency` | 数据库监控命名锁操作已恢复 | `snapshot_key`, `downtime_ms`, `failure_count` |
| `agent.persistence.cleanup.succeeded` | `info/lifecycle` | Agent 持久化清理已完成 | `task_type`, `outbox_id`, `attempt`, `duration_ms`, `deleted_count` |
| `agent.persistence.cleanup.failed` | `error/dependency` | Agent 持久化清理执行失败 | `task_type`, `outbox_id`, `attempt`, `duration_ms`, `reason_code` |
| `agent.persistence.cleanup.runtime.degraded` | `warning/dependency` | Agent 持久化清理运行循环已降级 | `reason_code`, `suppressed_count` |
| `agent.persistence.cleanup.runtime.recovered` | `info/dependency` | Agent 持久化清理运行循环已恢复 | `downtime_ms`, `failure_count` |
| `analytics.public_preview.view` | `info/request` | 公开预览已展示 | `visitor_hash`, `page`, `demo_key` |
| `analytics.public_preview.demo_open` | `info/request` | 公开预览示例已打开 | `visitor_hash`, `page`, `demo_key` |
| `analytics.public_preview.send_click` | `info/request` | 未登录访客点击发送 | `visitor_hash`, `page`, `demo_key` |
| `analytics.auth.panel_open` | `info/request` | 登录面板已打开 | `visitor_hash`, `page` |
| `analytics.auth.login_success` | `info/request` | 用户登录成功 | 无 |

## 5. 关联链路与模块边界

```text
X-Request-ID
  -> Flask request context + 已验证 user/session
  -> analysis_jobs.request_id
  -> worker claim
  -> request/user/session/job/worker_slot context
  -> LangGraph node/tool runtime context
  -> Deep Agent / 兼容子图工具摘要
  -> MCP 可信参数与 invocation_id（新路径）
  -> MCP 子进程 JSON stderr
```

- graph 终态失败时，`graph_runner` 把 `sanitize_public_error()` 的脱敏文案作为公开 `message`，另以 `_diagnostic` 内部字段携带 `error_category`、真实 `reason_code` 和 `exc_info`；`OrderedEventWriter` 只把 `message` 交给 `fail_job`，诊断仅与 `terminal_type == "error"` 同步挂在 `terminal_diagnostic` 上供运行日志使用，不进入 `analysis_job_events`、SSE 或管理员接口。`error_category` 取值为 `provider_error/protocol_error/checkpoint_error/runtime_contract_error/internal_error`，只按异常类名（含基类）判定，不读取异常文本；未识别的异常保持 `node_error`。该字段与 `Agent/causal_agent/graph_utils.py` 自定义流里的 `error_kind`（原始异常类名）是不同命名空间的两个概念，不要混用。`exception_type` 与 `stack` 是 v1 顶层字段，由 `exc_info` 派生，不进入 `details` 白名单。
- node 包装器统一绑定 `node`；兼容子图 ToolNode 只从已校验的第一个 tool call 绑定 `tool`，新 Deep Agent 则由父节点 update 聚合静态算法结果并只输出稳定工具名/状态/安全错误码。单次重试失败不写运行异常，只有重试耗尽后的 timeout/degraded 才记录；运行日志不写入 `analysis_job_events`。
- 兼容 MCP 路径的父进程先删除模型给出的可信参数和 `csv_data`，再从 State 注入 `user_id/session_id/job_id/input_user_file_id/input_object_id`，从当前日志上下文注入 `request_id/worker_slot`。必填可信参数缺少权威值时在 transport 前失败关闭；子进程固定绑定 `node=mcp_tool_node` 和真实工具名。
- 新 MCP 路径由 worker 在调用前写 `mcp.client.call.started`，MCP 服务只在 HMAC 验证后绑定 `user_id/session_id/job_id/invocation_id/worker_slot/node/tool`；签名、CSV、参数正文和原始结果不进入日志。服务端收到、拒绝、接受、60 秒慢调用、终态及取消回执均使用相同 invocation 上下文，可在 Grafana 时间线中跨 `worker|mcp` 服务关联。
- 数据库只记录 `primary/replica` 逻辑别名和稳定 reason code。慢 SQL 只记录操作类型、耗时、规范化 SQL 的完整 SHA-256 digest 和抑制数；monitor 正常锁竞争、轮询和成功快照静默。
- Agent 持久化清理每个 outbox attempt 最终边界只记录一个成功或失败事件，claim、heartbeat 和快照发布等循环级异常使用 runtime degraded/recovered；业务数据库中的 fencing、幂等和 outbox 状态机保持原样。

## 6. 验收记录和完成边界

- 第一阶段单元与静态验证覆盖 JSON 契约、上下文、截断、脱敏、序列化失败和 MCP stdout/stderr；第二阶段增加事件目录、合同降级、转移限频、请求/slot/task/thread 隔离、终态映射、MCP 可信参数、RAG/数据库/monitor/cleanup 事件及 AST 日志政策测试；第三阶段增加 Dashboard JSON、固定 UID、面板、变量、级别白名单和高基数字段边界的静态合同。
- 匿名访问事件由 `tests/unit/analytics/test_public_analytics_events.py` 覆盖目录登记、请求边界、`visitor_hash` 脱敏、非法批次整体拒绝和登录侧写入。这些测试只证明代码级合同，不证明真实 Loki 检索或生产流量；普通用户应用的前端上报代码已随入口拆分删除。
- Docker unit 基线固定为 `docker compose -f docker-compose.test.yml build unit-test` 和 `docker compose -f docker-compose.test.yml run --rm unit-test`。本地 Python 缺少 pytest 时不得临时安装依赖冒充仓库基线。
- 第一阶段真实验收仍包括 Compose/Alloy/Loki/Grafana、五类测试事件、positions 重启续读、Loki 不可用时不阻塞、高基数标签检查和 30 分钟代表性负载；第二阶段还必须执行受控故障矩阵、关联检索、隐私抽样和正常流噪声检查。
- Python 3.11 Docker unit、相关 integration 和 Compose 静态合同不能替代 observability Docker 采集、Alloy positions、Loki 检索、真实运行速率/行数/字节数/stream 数或完整 Job 证据。每次发布必须按当前环境重新记录这些结果。

相关权威事实见 [`API 通用约定`](../api/conventions.md)、[`Agent 运行时`](../architecture/agent-runtime.md)、[`数据库监控`](../database/monitoring.md)、[`测试与验证`](testing.md)、[`分析 Job API`](../api/agent-jobs.md) 和 [`迁移与 Checkpoint`](../database/migrations-checkpoints.md)。
