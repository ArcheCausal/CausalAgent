# 分析 Job API

文档职责：记录普通用户创建、查看、订阅、恢复和取消分析 Job 的 HTTP/SSE 契约。

适用范围：修改 `app/agent/routes.py`、`app/agent/job_service.py`、前端 Job 状态恢复或 `analysis_job_events` payload 时使用；内部 worker 执行机制见 [`../architecture/agent-runtime.md`](../architecture/agent-runtime.md)。

## 接口总览

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/api/agent/jobs` | 创建或按幂等键重放一个分析 Job |
| `GET` | `/api/agent/jobs/active` | 读取当前用户活动 Job，可用 `session_id` 过滤 |
| `GET` | `/api/agent/jobs/<job_id>/events` | 订阅该 Job 的 SSE 事件 |
| `POST` | `/api/agent/jobs/<job_id>/resume` | 提交 `waiting_input` Job 的恢复输入 |
| `POST` | `/api/agent/jobs/<job_id>/cancel` | 即时逻辑取消 `queued`、`running` 或 `waiting_input` Job |
| `POST` | `/api/send_stream` | 已废弃，固定返回 `410` 和迁移提示 |

所有路径都要求当前用户登录，并按 `job_id + user_id` 校验资源归属。Web 层不执行 Agent，只校验参数、持久化 Job 并入队。

## 创建 Job

请求 JSON 至少包含：

```json
{
  "message": "请分析这些变量之间的关系",
  "session_id": "session-uuid",
  "input_user_file_id": 123,
  "web_search_enabled": false
}
```

`input_user_file_id` 和 `web_search_enabled` 都可省略；`web_search_enabled` 缺省为 `false`。消息必须是非空文本，长度受服务端限制。请求必须带标准 UUID v4 `Idempotency-Key`。`web_search_enabled` 只接受 JSON 布尔值；字符串 `"true"`、`"false"`、数字、`null` 等非布尔值在接口层返回 `400`，服务层在创建 Job 前再次严格校验。该开关参与请求指纹，因此同一个幂等键不能把搜索开关从开启改为关闭或反之。

服务端在一个 MySQL 事务中检查会话归属、活动 Job、文件归属，冻结文件快照，按该文件复用当前 Session 的默认分析上下文或新建一个，然后写入 Job（含 `analysis_context_id`）、initial input（含 `analysis_context_id`）、用户聊天消息和包含 `web_search_enabled` 的请求指纹。target 与 treatment 由后续 fold 节点确定后回填上下文；响应不返回上下文 ID，前端不需要传入。

请求上下文中的 `X-Request-ID` 会在入口校验或生成，并作为创建 Job 的原始关联 ID 保存到 `analysis_jobs.request_id`；历史 Job 该字段可以为 `NULL`。同一 `Idempotency-Key` 重放时只返回首次创建的 Job，不会用后续请求的 `X-Request-ID` 覆盖首次值；该内部关联字段不进入普通用户响应。

- 新 Job 返回 `202`，`success=true`、`existing=false`、`job_id` 和 `status=queued`。
- 相同用户、相同幂等键和相同请求参数重放原 Job，返回 `200`、`existing=true`。
- 同一 `user_id + session_id` 已有 `queued`、`running` 或 `waiting_input` Job 时返回 `409`，错误码为 `active_job_conflict`。
- 同一个幂等键对应不同请求参数时返回 `409`。
- 缺少或非 UUID v4 幂等键、消息为空、`web_search_enabled` 非布尔值或文件/会话无权访问时返回 `400` 或 `403`。

## 活动 Job

`GET /api/agent/jobs/active` 返回当前用户的活动 Job 摘要，并附带当前公开事件最大 ID `last_event_id`。可传 `session_id` 只恢复指定会话。摘要包含状态、worker/lease 观测、尝试和恢复次数、冻结文件名以及等待输入的问题 ID/公开提示，但不返回内部 checkpoint 状态或文件正文。前端刷新时以会话历史实际回放到的 `rendered_event_id` 作为 SSE 游标，活动摘要中的最新 ID 不能覆盖它。

## SSE 订阅

`GET /api/agent/jobs/<job_id>/events` 返回 `text/event-stream`。客户端断线重连时使用上次收到的事件 ID：

```text
Last-Event-ID: 42
```

也兼容 `last_event_id=42` 查询参数。服务端从 `analysis_job_events.id > 42` 读取事件，返回标准 SSE 的 `id`、`event`、`data` 字段；没有新事件时按配置轮询并发送 heartbeat。事件 payload 按事件类型执行字段白名单清洗，worker 的 `attempt`、未知附加字段和内部对象不会对外可见。

收到 `interrupt` 后，前端应展示公开问题并等待恢复；收到 `final_result`、`error` 或 `canceled` 后连接结束。若 Job 已经进入终态，即使数据库查询时没有新的事件，服务端也会结束连接。

`decision` 事件除既有路由说明外，还可包含 `decision_kind=algorithm|evidence|final`。`algorithm` 表示模型在同一次算法 Tool Call 中显式提交的公开选择依据，`evidence` 表示同一位置的 `rag_evidence_search`/`web_evidence_search` 检索依据，两者都可同时带公开工具名；`final` 只在 FinalizationGate 验证成功后产生，可带 `low|medium|high` 置信度。模型工具参数流中的公开摘要会先以 `decision_delta` 增量事件发送，工具开始/结果仍分别由可信工具边界发布；完整 `decision` 仍作为历史回放和无增量回退。公开说明是展示文本而非隐藏思维链，不包含内部结果引用；缺失或格式无效的工具说明不会阻止工具执行。相关事件都持久化到 `analysis_job_events`，SSE 重连按原 Event ID 续传，历史回放直接展示完整 `decision` 文本。

FinalizationGate 拒绝时额外发布 `progress` 阶段说明，只含 `summary` 文本：第一次失败挂在 `finalization_gate` 阶段，说明未通过校验并给出脱敏后的具体修正要求；若该次修正仍未通过，则在同一阶段说明本次降级为仅基于已验证输入的报告。第二次 Deep Agent 启动修正时，`progress` 说明挂在新创建的 `deep_agent` 阶段，指出该阶段沿用已有工具结果、不重复调用工具。这两类说明与 `decision` 一样按稳定 `event_key` 幂等落库，SSE 重连和会话刷新按原 Event ID 回放。

新 Deep Agent 报告的 `final_result.data` 可包含程序生成的 `finalization_status`：`valid` 表示最终结构化决策已通过当前 Job/attempt/lease、AlgorithmResult 与 Action Ledger 校验；`degraded` 表示一次修正仍未通过，但系统生成了安全报告并将 Job 置为 `succeeded`。`degraded` 结果不展示未经 Gate 验证的主图，也不把内部校验错误、provider ID、raw result 或工具参数返回给用户。该字段是结果质量元数据，不是模型的 `outcome`，旧 Job 没有该字段时按兼容语义处理。

新报告终态是结构化报告文档，`final_result.data` 的形状如下：

```json
{
  "type": "final_result",
  "data": {
    "type": "report",
    "layout": "report",
    "render_mode": "structured",
    "document": {
      "schema_version": 1,
      "report_id": "report_550e8400e29b41d4a716446655440000",
      "title": "因果分析报告",
      "blocks": [
        {
          "id": "section_summary",
          "type": "section",
          "title": "结论摘要",
          "children": [
            {
              "id": "markdown_summary",
              "type": "markdown",
              "content": "## 结论\n\n- 变量 X 与变量 Y 呈正相关",
              "evidence_refs": ["ev_7c9e6679e25b4c3a8f0b123456789abc"]
            }
          ]
        },
        { "id": "chart_age", "type": "chart", "title": "年龄分布", "asset_key": "chart_histogram_age" },
        { "id": "graph_main", "type": "causal_graph", "title": "主要因果关系", "asset_key": "graph_main" }
      ],
      "assets": {
        "chart_histogram_age": {
          "asset_key": "chart_histogram_age",
          "type": "chart",
          "chart_type": "histogram",
          "data": { "bins": [20, 25, 30], "counts": [4, 8] },
          "metadata": { "variable": "age", "sample_count": 1200, "unit": null },
          "options": { "show_tooltip": true }
        },
        "graph_main": {
          "graph_id": "graph_main",
          "schema_version": 1,
          "nodes": [
            { "id": "node_age", "variable": "age", "label": "age", "metadata": {}, "evidence_refs": [] }
          ],
          "edges": [
            {
              "id": "edge_age_income",
              "source": "node_age",
              "target": "node_income",
              "edge_type": "directed",
              "weight": 0.42,
              "metadata": {},
              "evidence_refs": []
            }
          ],
          "metadata": { "algorithm": "causal_pc", "graph_source": "postprocessed" }
        }
      },
      "sources": [
        { "source_id": "src_550e8400e29b41d4a716446655440000", "kind": "file", "title": "data.csv", "file_id": 123, "url": null }
      ],
      "evidence_refs": [
        {
          "evidence_id": "ev_7c9e6679e25b4c3a8f0b123456789abc",
          "source_ids": ["src_550e8400e29b41d4a716446655440000"],
          "locator": { "columns": ["age", "income"], "rows": [1, 1200] },
          "description": "年龄和收入字段的相关性统计结果"
        }
      ]
    },
    "references": [
      {
        "title": "网页标题",
        "url": "https://example.com/page"
      }
    ]
  }
}
```

报告文档只使用四种块类型：`section` 包含子块，`markdown` 保存自然语言文本，`chart` 和 `causal_graph` 通过 `asset_key` 引用 `assets` 中的资源。`markdown.content` 是新报告中唯一允许出现 Markdown 的字段，列表、标题、表格、引用、代码块和链接继续复用普通聊天的 Markdown 解析器；模型不生成 HTML、CSS、Base64 图片、图片标签或图表占位符。图表资源第一阶段只支持 `histogram`、`bar` 和 `heatmap`，数据是经过校验的原始数值，前端使用 SVG 与 CSS 绘制。因果图资源是业务模型，前端通过投影函数转换为 vis-network 载荷，`graph_semantics` 等 Agent 内部字段不出现在公开结果中。

因果图的节点 ID 由变量名生成（例如 `node_age`），边 ID 由端点变量名生成（例如 `edge_age_income`），端点重复时追加序号后缀；节点和边 ID 只用于渲染与选择事件。`sources` 的 `source_id` 和 `evidence_refs` 的 `evidence_id` 使用前缀加随机 UUID，不在其中编码标题、文件名或数组位置。

`sources` 和 `evidence_refs` 由后端根据冻结文件、联网搜索和检索证据生成，报告块只引用 `evidence_id`；如果模型把本次清单中的 `ev_...` ID 写进正文但漏填 `evidence_refs`，后端装配会回填对应 markdown 块的结构化引用。ID 使用前缀加随机 UUID，不在其中编码标题、文件名或数组位置，也不保存原始文件正文。模型返回的块 ID 重复、块类型未知、`asset_key` 不存在或类型不匹配、`evidence_id` 不存在时，报告节点进入受控错误路径并返回降级报告文档，不保存部分报告。

联网搜索成功且存在结果时，报告终态额外包含最多 9 条 `references`。报告/追问使用的搜索结果与公开引用共用 `WEB_SEARCH_MAX_RESULTS=9` 上限。引用只公开网页标题和 URL，不返回网页正文、搜索工具内部字段或完整搜索结果。引用随 assistant 消息独立持久化；重新加载会话时通过 `message.references` 返回相同的 `title + url` 数组。后端只提供该字段契约，不要求前端展示引用。普通问答和报告追问节点的公开正文使用 `text_delta` 增量；报告节点只返回结构化草稿，模型正文恒为空，因此不发送文字增量，预处理和后处理节点同样不发送文字增量。算法选择说明使用独立的 `decision_delta`，不进入聊天正文。

## 追问、上下文切换与报告重新生成

Agent 在每轮开始时判断用户意图，再由图路由执行；用户不需要提供上下文 ID。普通问答、报告追问、重新分析、报告重新生成和上下文切换都可能出现在同一个会话里。

当用户提到另一个文件或另一次历史分析时，服务端按文件名、目标变量、处理变量、报告标题或问题描述匹配同一会话的历史分析：

- 唯一命中：切换 Session 默认分析上下文并继续回答或重新分析。目标上下文的文件与当前 Job 冻结文件不同时，服务端会在同一个事务里重新冻结本次 Job 的文件输入，之后的分析按新文件执行。
- 多个候选：返回澄清问题，要求用户说明目标变量或报告标题；在用户确认前不修改默认上下文。
- 没有命中但点名了文件库里的文件：为该文件新建分析上下文、重新冻结输入，然后重新分析。
- 目标上下文的文件已经被删除：返回澄清问题，提示重新上传文件。

转达澄清问题时，终态载荷与普通文本回答相同（`type=text`），不包含内部分析上下文 ID。按当前上下文重新生成报告时仍然返回结构化报告文档，该模式只使用上下文已确认的文件、参数、算法结果、因果边和检索证据，不修改算法图，也不新增上下文里没有的因果结论。

## Resume 与 Cancel

恢复请求示例：

```json
{
  "question_id": "question-uuid",
  "answer": "补充信息"
}
```

`answer` 也兼容 `message` 字段，可以是文本或受限 JSON。恢复请求必须使用新的、可复用的 UUID v4 `Idempotency-Key`；服务端追加 `analysis_job_inputs.input_type='resume'`，然后重新排队同一个 Job。相同键重放返回原结果，不同问题或答案返回冲突。

取消请求支持 `queued`、`running` 和 `waiting_input`。事务提交后业务状态立即成为不可逆 `canceled`，同时写入稳定取消事件、assistant 投影并释放 Session 活动锁；`running` Job 的执行占用会暂时显示为内部 `draining`，普通用户不会看到该字段。请求同样需要 UUID v4 幂等键：首次取消返回 `202`，相同键重放返回 `200`，已由另一幂等键取消、或已经 `succeeded/failed` 时返回 `409 job_state_conflict`。取消不承诺终止已发出的远端调用；其结果不会进入 parser、router、error handler、重试或后续节点。

浏览器按 `job_id` 分别维护取消请求和 SSE 订阅：同一 Job 的网络重试复用同一个幂等键；收到 `409 job_state_conflict` 且状态已经是 `canceled` 时按成功对账，其他活动状态冲突则保留任务并恢复展示。取消成功或切换页面前会使旧订阅代次失效，迟到的 SSE 事件不得重新修改已取消 Job 的前端状态。

Job 状态、输入冻结、checkpoint identity 和旧 worker fencing 的完整关系见 [`../architecture/job-file-lifecycle.md`](../architecture/job-file-lifecycle.md)。
