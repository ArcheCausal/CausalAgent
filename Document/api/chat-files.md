# 会话与文件 API

文档职责：记录普通用户会话、聊天消息和文件库接口的当前路径、授权边界与持久化语义。

适用范围：修改 `app/chat/routes.py`、`app/files/routes.py`、普通用户前端调用或文件库迁移时使用；Job 创建时的文件快照见 [`../architecture/job-file-lifecycle.md`](../architecture/job-file-lifecycle.md)。

## 会话接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/api/new_chat` | 生成 UUID 并立即在主库创建 Session |
| `GET` | `/api/sessions` | 读取当前用户未归档会话列表 |
| `GET` | `/api/load_session?session=<id>` | 读取当前用户会话消息与展示附件 |
| `POST` | `/api/change_session` | 修改当前用户会话标题 |
| `POST` | `/api/delete_session` | 删除会话业务数据并登记 checkpoint 清理 |

所有会话接口按当前用户过滤。未知或不属于当前用户的 Session 不会被自动重建。新建会话使用主库写入；列表可以使用允许回退的 eventual read；加载、修改、删除和 Job 相关的实时路径使用 strong read 或主库事务。

`GET /api/load_session` 在一个主库只读事务中批量读取消息、展示附件、Session 下的 Job、输入账本和事件。既有 `sender`、`text` 字段保持不变；带 Job 关联的用户消息还可以返回 `analysis_job_id`、`analysis_job_input_id` 和 `thinking_after`：

```json
{
  "sender": "user",
  "text": "目标变量是 Y",
  "analysis_job_id": "job-id",
  "analysis_job_input_id": 42,
  "file_attachment": {
    "filename": "patients.csv"
  },
  "thinking_after": {
    "phase_sequence": 1,
    "status": "completed",
    "elapsed_seconds": 8.4,
    "last_event_id": 126,
    "events": []
  }
}
```

`thinking_after` 不是数据库实体，而是按 `analysis_job_inputs.sequence`、用户消息的 `analysis_job_input_id`、assistant 消息的 `source_event_id` 和边界事件组装的展示 DTO。阶段只包含 `node_start`、`progress`、`decision`、`tool_call_start`、`tool_call_result`、`node_retry`、`node_end`；`text_delta` 不进入历史，`interrupt`、`final_result`、`error`、`canceled` 只负责切分和计算状态。关联链不能唯一成立时，服务端记录告警并跳过该事件区间，不按时间猜测。

assistant 消息的 `text` 在报告场景下是结构化报告载荷，与 SSE 终态使用同一份数据：

```json
{
  "sender": "ai",
  "text": {
    "type": "report",
    "layout": "report",
    "render_mode": "structured",
    "document": {
      "schema_version": 1,
      "report_id": "report_...",
      "title": "因果分析报告",
      "blocks": [],
      "assets": {},
      "sources": [],
      "evidence_refs": []
    }
  }
}
```

报告文档保存在 `chat_attachments` 的 `report_document` 附件里，`chat_messages.content` 只保存报告标题作为会话预览。历史读取会重新通过后端报告 schema 校验附件 JSON，再包装成上述载荷；附件损坏或 schema 非法时记录 `chat.attachment.degraded`，只返回消息正文中的报告预览，不把无效 JSON 发给前端。旧版 `visualization`、`causal_graph` 和 `web_search_references` 附件继续按原格式读取和投影。

删除会话前，服务端锁定该会话的 Job 和 Session；如果存在 `queued`、`running` 或 `waiting_input` Job，返回 `409`。否则在同一个 MySQL 事务中登记所有 Job 的父子图 checkpoint 清理 outbox、删除附件、删除聊天消息和删除 Session，然后返回后台清理状态。MySQL 业务删除成功不代表 PostgreSQL checkpoint 已同步完成。删除会话只清理 Job checkpoint，不删除该用户长期记忆。

当用户消息精确关联到初始 Job 输入，且该 Job 有冻结文件名时，历史响应会附带可选的 file_attachment.filename。服务端通过 analysis_job_input_id、输入账本的 chat_message_id 和 job_id 校验归属；响应只返回文件名，不返回文件内容、文件 ID、对象 ID 或哈希。没有文件的消息、恢复输入和 assistant 消息不包含该字段。

## 文件接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/files` | 读取当前用户的文件库逻辑记录 |
| `POST` | `/api/upload_file` | 上传到 `file_objects`/`user_files` 文件库 |
| `POST` | `/api/delete_file` | 删除用户文件逻辑记录，必要时删除未引用 BLOB |

上传只进入文件库，不创建 Session 关联或 Job。服务端按 SHA-256 在同一用户范围内复用不可变 `file_objects`，文件名通过 `user_files` 逻辑记录保存。响应只返回文件元数据和 `user_file_id`，不把 BLOB 内容作为列表结果返回。

删除文件时，仍被活动 Job 的 `input_user_file_id` 使用会返回 `409`；否则先删除 `user_files`，只有对象没有其他逻辑引用时才删除 `file_objects`。删除没有回收站。文件预览、下载和 Agent 真实读取更新访问次数与最近访问时间，重复上传命中已有对象不计为访问。

## 数据边界

普通用户只能访问自己的 Session、消息、文件和 Job。聊天附件可以承载因果图或分析结果等展示数据，但不得把内部 prompt、ToolMessage、完整工具结果或隐藏推理作为普通用户 API 的隐式扩展。文件内容读取必须经过有界大小、类型和权限校验。

管理员查看业务数据使用独立 `/api/admin/business/*` 契约，不应为了复用前端而放宽普通用户接口；管理员契约见 [`../admin/api.md`](../admin/api.md)。
