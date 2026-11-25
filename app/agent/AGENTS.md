# app/agent/AGENTS.md

生效目录：`app/agent/` 及其子目录。

负责约束的修改类型：analysis Job API、Job service、输入/文件快照、幂等、SSE、事件日志、checkpoint recovery、worker lease/fencing 和 worker 运行时。

## 修改前必须阅读

- 必须阅读 [`Document/architecture/job-file-lifecycle.md`](../../Document/architecture/job-file-lifecycle.md)、[`Document/architecture/agent-runtime.md`](../../Document/architecture/agent-runtime.md)、[`Document/api/agent-jobs.md`](../../Document/api/agent-jobs.md) 和 [`Document/api/conventions.md`](../../Document/api/conventions.md)。
- 必须检查调用入口、`app/agent/routes.py`、`job_service.py`、worker runtime/bootstrap、相关 migration、普通用户前端调用和对应 unit 测试。
- 修改 checkpoint identity、Job 字段或事件 payload 时必须同时核对 PostgreSQL inspection、管理员 Job API 和 SSE 恢复逻辑。

## Job 不变量

- 创建、resume、cancel 操作必须使用标准 UUID v4 `Idempotency-Key`；相同键重试必须复用原请求，不同请求参数必须返回冲突。
- 同一 `user_id + session_id` 同时最多允许一个 `queued/running/waiting_input` Job；不能通过绕过服务层或重建 Session 破坏这个约束。
- Job 初始输入必须在 MySQL 事务中写入 `analysis_job_inputs`，并冻结 `input_user_file_id`、对象 ID、hash 和文件名快照；浏览器未发送的文件选择不是持久化输入。
- `waiting_input` 必须保留活动会话锁但释放 worker lease；resume 追加 input 后重新排队同一个 Job；cancel 支持 `queued/running/waiting_input`。running 取消先转为 `canceled/draining`，只有 worker cleanup 或失联 lease 回收后才释放执行占用。
- 所有 worker 状态、事件和终态更新必须校验 worker、attempt 和 `lease_epoch`；失去 lease 的旧 worker 禁止覆盖新尝试。

## 公共事件与安全

- SSE 只能从持久化 `analysis_job_events` 按 Event ID 续传；必须支持 `Last-Event-ID`，不能依赖 worker 内存状态。
- 普通用户事件必须经过脱敏，只允许公开文字和稳定状态；禁止输出原始 prompt、ToolMessage、完整工具结果、文件正文、图状态、内部 attempt 或隐藏推理。
- terminal/interrupt 事件的写入、assistant 消息和 Job 状态必须保持事务语义；稳定生命周期事件必须有可重放的 event key。
- checkpoint 读取失败时禁止盲目 stale recovery；必须阻止可能重复执行的恢复动作并返回稳定错误。
- 修改消息编辑或重发时，必须在同一主库事务里复核会话版本与输入阶段，并对当前历史、模型输入和公开 SSE 应用相同的替代边界。历史追问分叉需要复核来源 Job、根 namespace、问题 ID 与 PostgreSQL 边界。缺少可信边界时由用户明确确认重新分析。

## 修改后验证

- Job/API 变更至少覆盖正常、重复请求、参数冲突、活动 Job 冲突、状态冲突、越权、断线续传和终态收敛。
- worker/事件变更至少运行 `tests/unit/agent/` 中对应测试，并检查普通用户 payload 的脱敏结果。
- 修改文件、Session 删除或 outbox 时必须联测 `app/chat/`、`app/files/`、`Database/` 和对应 migration；不能只跑 Agent 图测试。
