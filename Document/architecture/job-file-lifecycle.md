# Job 与文件生命周期

文档职责：记录 Session、analysis Job、输入账本、文件库、LangGraph checkpoint 以及删除清理之间的当前数据关系和状态转换。

适用范围：修改 `app/agent/job_service.py`、会话/文件路由、相关 Alembic migration、worker fencing 或跨库删除流程时使用；普通用户 API 入口见 [`../api/agent-jobs.md`](../api/agent-jobs.md) 与 [`../api/chat-files.md`](../api/chat-files.md)。

## 核心实体

| 实体 | 存储 | 作用 |
| --- | --- | --- |
| Session | MySQL `sessions` | 用户可见的会话容器，`id` 是 UUID 字符串；业务访问仍按 `session_id` 授权 |
| Job | MySQL `analysis_jobs` | 长任务队列、状态、lease、重试、冻结文件快照、终态摘要和创建请求关联 |
| 分析上下文 | MySQL `analysis_contexts` | 跨 Job 的业务事实：文件快照、分析参数、最新算法摘要、证据摘要和最新报告引用；一个 Session 可以有多个 |
| Job 输入 | MySQL `analysis_job_inputs` | initial/resume 输入账本、顺序、问题 ID、幂等键和对应聊天消息 |
| Job 事件 | MySQL `analysis_job_events` | SSE 时间线、生命周期事件和内部执行摘要 |
| 文件对象 | MySQL `file_objects` | 按用户和 SHA-256 去重的不可变 BLOB |
| 用户文件 | MySQL `user_files` | 用户可见文件名、访问统计和对象引用 |
| 数据处理候选 | MySQL `analysis_job_datasets` | 每个 Job 的候选 CSV/脚本对象引用、revision、来源 hash 和独立校验状态 |
| 沙盒生命周期 | MySQL `analysis_sandbox_sessions` | 记录创建、执行、心跳和清理事实，以 Job/worker/attempt/lease epoch fencing 执行 |
| 父图 checkpoint | PostgreSQL 官方 LangGraph 表 | Job 的外层恢复状态；`thread_id` 是 `analysis_jobs.job_id` |
| Deep Agent checkpoint | PostgreSQL 官方 LangGraph 表 | 内层消息、工具状态和 raw 文件；使用稳定的 `deep-agent:<uuid5(job_id)>` thread，当前独立调用实际保存在根 namespace |
| 数据处理 checkpoint | PostgreSQL 官方 LangGraph 表 | 以 Job thread 和 `data_processing_v1:<run_hash>` namespace 保存处理子图恢复状态 |
| 长期记忆 Store | PostgreSQL 官方 `AsyncPostgresStore` 表 | 按可信 `user_id` namespace 保存 Deep Agent 两份 memory 文件；不属于 Job checkpoint |
| checkpoint cleanup outbox | MySQL `checkpoint_cleanup_outbox` | Job 父子图 checkpoint 删除请求的可靠账本，按 `thread_id` 唯一 |
| 用户记忆 cleanup outbox | MySQL `user_memory_cleanup_outbox` | 用户长期记忆 Store 删除请求的可靠账本，按 `user_id` 唯一，不关联 `users` 外键 |

新建会话时，`POST /api/new_chat` 先在 MySQL 主库插入 `sessions` 记录再返回 ID。创建 Job、保存聊天、修改标题和上传文件都要求会话或用户文件真实存在且属于当前用户，不会根据未知 ID 自动重建对象。Job 创建时保存入口确定的原始 `X-Request-ID` 到 `analysis_jobs.request_id`；历史行可为 `NULL`，幂等重放不覆盖首次值。

## 文件库与冻结输入

文件上传进入文件库，不自动关联 Session 或 Job。相同用户相同 SHA-256 的内容复用 `file_objects`，不同文件名仍可以形成不同的 `user_files` 逻辑记录；浏览器中尚未提交的选择只是 composer 状态，不构成 Job 输入。

创建 Job 时，服务端在同一个 MySQL 事务中锁定并快照 `input_user_file_id`、对象 ID、文件 hash 和文件名，同时写入 initial `analysis_job_inputs` 和用户聊天消息。之后用户替换或清除浏览器草稿不会改变已创建 Job 的输入。

文件预览、下载以及 Agent 真正读取文件内容都在主库事务内更新 `last_accessed_at` 和 `access_count`；命中已有对象的重复上传不计为访问。CSV 预览最多读取 256 KiB、100 行、50 列，单元格最多 1000 字符，并且只按文本处理。

## 数据处理产物与分析输入

容量策略来自控制服务启动配置。所有尚未确认关闭的资源均占用容量；心跳超时、TTL 经过或销毁请求失败不会释放占用。生命周期记录不关联业务级联外键，Job、Session 或用户删除后仍由控制服务核查并清理远端资源。具体状态机见 [沙盒控制服务](sandbox-controller.md)。

新 Job 先冻结用户原始文件，再由数据处理 Agent 在独立 OpenSandbox 服务中生成候选 CSV 与可重放脚本。Worker 通过独立控制服务的受限 API 使用短期沙盒，不持有上游密钥，也不连接 Docker daemon。原始文件在沙盒内只读；每次创建请求默认拒绝所有出站规则，服务端网络和宿主防火墙还必须阻断沙盒 IPv4/IPv6 外连。候选文件、脚本和语义说明由 Worker 校验路径、大小与 Job/lease 身份后保存至 MySQL；模型不能直接访问数据库。

每个候选都由 `analysis_job_datasets` 记录，文件内容存放在 `file_objects`，不创建 `user_files`，因此不会出现在用户文件列表。独立校验节点从 MySQL 强读候选对象；只有对象归属、hash、CSV 结构、处理语义和算法输入条件都通过后，服务端才把候选标为通过。绑定节点再锁定 Job 和候选，复核当前租约、来源及确认版本，在同一事务内更新 `analysis_jobs.prepared_dataset_id`。后续画像、摘要、图表和全部算法调用都使用该数据对象，并保留原始输入 hash 作为来源身份。

数据处理 Agent 通过统一参数工具提交研究参数，完成后由现有父节点使用最终参数确定分析上下文。切换文件或目标、处理变量导致上下文变化时，旧绑定会清除；同一上下文的重复绑定保留已处理数据。同一 Job 重启后从 MySQL 恢复数据集身份，重复保存相同候选返回同一个 ID 和 revision。已校验候选仍作为该 Job 的审计和恢复记录保留。文件对象回收时必须同时检查 `user_files` 与 `analysis_job_datasets` 引用，不能因用户删除原始文件而删除仍被 Job 使用的处理结果或脚本。

执行前，服务端向用户展示方案说明和版本。方案版本绑定 Job、原始文件 hash、研究语义和方案说明。resume API 只在当前交互版本匹配时接受明确确认，并把确认写入输入账本；修改或拒绝的自由文本只作为 Agent 输入，不具有执行授权。新的语义答复或方案说明变化会清除确认；已确认语义范围内的脚本修正可以复用确认，脚本与候选文件仍分别校验内容 hash。沙盒执行前、候选 MySQL 事务、独立校验和最终绑定都检查当前 Job 租约及最新确认版本，防止旧方案在恢复或修订后继续执行。

等待交互的截止时间由 MySQL 计算；日期格式串作为绑定参数传入，公开事件和 Job 的等待记录使用数据库时间。正常暂停保持活动会话约束并释放当前 worker 执行占用，确认后为同一 Job 追加恢复输入并重新排队。

处理方案草稿、服务端确认快照和执行说明分别存放在子图 State，并随现有 PostgreSQL checkpoint 恢复；MySQL 继续保存候选方案快照、数据及脚本引用、hash 和确认输入，不新增列。补写执行说明不取消确认；修改脚本清空当前候选引用与说明并要求重新执行，修改方案或研究语义同时清空确认快照与版本。清空引用不会删除历史候选，重新执行的候选也不继承旧说明。结束检查复用原有确认工具、`waiting_input` 和 `Command(resume=...)`，不增加人工中断层，不改变 Job thread 或数据处理 namespace；恢复已有子图时不覆盖保存状态，旧 checkpoint 不回填新字段。执行说明仅经显式父子图投影传递，绑定候选 ID、修订号和方案版本，不写入候选 metadata。

通过校验的报告显式保存输出字段及其 `semantic_kinds` 快照。恢复绑定、Adapter 检查和 MCP 强读都要求报告有效，不能从缺失快照推定连续测量语义。普通报告追问只使用报告及分析事实；新的 Job 不自动复用旧 Job 的处理产物。

方案 metadata 的 `role_columns` 保存研究角色到处理后列名的映射，并参与现有方案版本计算；映射变化按方案变化清除确认与当前候选引用。研究参数快照仍保留用户角色名称，独立校验报告分别保存原始角色与实际执行列。恢复后，数据摘要、算法输入与最终决策校验从已绑定候选的同一映射生成执行参数副本，不改写研究参数修订链。

研究参数通过 `set_research_spec` 完整提交，修订记录关联当前 Job 的真实 initial 或 resume 输入；同一来源允许后续解释纠正。输入 ID 只能从程序交付的来源中选择，持久化边界再次核对 Job 归属。新自由文本先暂停旧方案的执行资格，参数输入回执、方案依赖和确认检查均通过后才允许执行。父图和候选参数绑定使用第 3 版流程，旧 checkpoint 和旧候选格式不迁移；新流程保留自身的 interrupt/resume、租约 fencing 和幂等恢复能力。

## 分析上下文与上下文绑定

`analysis_contexts` 是跨 Job 的业务事实来源，保存创建时的文件快照（`input_user_file_id`、`file_object_id`、`file_hash`、`filename`）、分析参数（`target`、`treatment`、`analysis_question`）、最新结构化算法摘要、RAG/Web 证据摘要和最新报告引用。同一个 Session 可以存在多个上下文，`sessions.active_analysis_context_id` 只表示当前默认上下文；`sessions` 指向上下文的一侧不建外键，避免与 `analysis_contexts` 到 `sessions` 的级联删除形成环，所有上下文写入都按 `user_id + session_id` 校验归属。

创建 Job 时，服务端在同一个 MySQL 事务里按冻结文件复用当前 active 上下文或新建上下文，并把 `analysis_context_id` 同时写入 `analysis_jobs` 和 initial `analysis_job_inputs`；resume 输入记录当时的上下文。数据处理子图保存结构化参数修订，现有父节点接收最终参数和候选后幂等回填上下文；尚未承载结果的上下文可以修订，已有历史结果时只有完整研究身份一致才复用，否则新建并切换 active 指针。target/treatment 为空不代表该上下文尚未确定。上下文切换先于最终数据集绑定。分析成功才在终态事务里写回算法摘要、证据摘要和报告引用，失败或澄清不覆盖原有有效结果。

用户在会话里用自然语言切换分析上下文时，切换事务同时更新 Session 默认指针、当前 Job 和当前输入账本的上下文绑定，并校验 worker、attempt 和 lease epoch。如果目标上下文的文件与 Job 当前冻结文件不同，同一个事务还会重写 `analysis_jobs` 的冻结文件快照：这是服务端主动的重新冻结，等价于把本次执行的文件输入改为目标上下文引用的文件。冻结快照仍然只由服务端写入，浏览器草稿不会改变它；目标上下文引用的文件已经被删除时，切换直接失败并返回澄清，不写入失效指针。

删除 Session 时 `analysis_contexts` 随 `sessions` 级联删除，不需要单独的清理 outbox；上下文本身不产生 PostgreSQL checkpoint。旧 Session 和历史 Job 的 `analysis_context_id` 可以为空，下一次分析或追问时按需创建上下文。

## Job 状态和并发

活动状态为 `queued`、`running`、`waiting_input`，终态为 `succeeded`、`failed`、`canceled`。同一 `user_id + session_id` 同时最多有一个活动 Job；`active_session_key` 是可空普通列，唯一键 `uq_analysis_jobs_active_session` 负责并发兜底。

领取时 worker 写入 `worker_id`、`attempt_count`、`lease_epoch`、锁定时间和心跳，并把 `execution_state` 置为 `leased`。运行中的业务取消会立即提交 `status=canceled`、取消事件和聊天投影，同时保留执行身份并转为 `execution_state=draining`；heartbeat 随后标记 invocation guard 撤销。LLM/RAG 继续合作式取消，MCP 算法调用额外通过签名控制面请求按 invocation 终止独立算法进程；取消失败或回执未知时仍保持 fencing，绝不接受迟到结果。worker cleanup 完成后以 `worker_confirmed` 释放，失联 lease 由 420 秒阈值以 `lease_expired` 回收。终态写入、事件写入和 assistant 消息提交前必须确认 worker、attempt、lease epoch 和 leased 状态仍匹配，旧 worker 不能覆盖新尝试。stale recovery 只在确认 PostgreSQL checkpoint 可读且未恢复的 interrupt 后进行；canceled Job 永不 resume 或 stale recovery，无法可靠读取恢复状态时禁止冒险重放。

等待确认同时保存公开交互 JSON 与数据库截止时间（300 秒）。worker 进程内有独立于执行槽位的过期扫描任务，每 5 秒把已过期的 waiting_input 收敛为 failed/human_input_timeout，写入错误事件、assistant 消息并释放活动会话锁；创建、恢复等入口也会在同一事务内检查期限，不依赖后台扫描。resume 只清理等待字段，不延长既有截止时间；有效期内已接受的确认不会因为之后排队超过五分钟而失效。

## 等待输入、恢复与取消

Agent 产生 interrupt 后，Job 进入 `waiting_input`，保留 `active_session_key` 以阻止同一会话创建第二个 Job，但释放 worker lease并记录 `worker_confirmed`。服务端将问题 ID、公开提示和稳定 interrupt 事件写入 MySQL；恢复请求把输入追加为 `resume` 记录，随后重新排队同一个 Job，继续同一个 checkpoint。`queued`、`running`、`waiting_input` 都可以被即时逻辑取消；取消后的业务终态不可逆，旧 worker 的迟到结果会被 fencing 拒绝。

恢复输入只允许文本或受限 JSON，服务端限制长度、深度、字段/数组项数和 UTF-8 字节数。恢复与取消操作各自要求 UUID v4 `Idempotency-Key`，相同键重放相同请求时返回原结果，不同参数返回冲突。取消支持 `queued/running/waiting_input`，写入稳定 `canceled` 生命周期事件；运行中取消只释放业务活动锁，执行占用在 graph/EventWriter/heartbeat 和可能的 MCP 远端取消完成 cleanup 前保持 `draining`。

取消事务先按取消前的状态设置 `execution_state`、worker 身份、锁定时间和心跳，再写入 `status=canceled`，使运行中取消保留 cleanup 所需的执行身份，排队与等待输入取消则直接释放执行占用。

## 会话历史中的执行阶段

`ExecutionPhase` 是接口和前端展示概念，不新增表或字段。一个 `analysis_job_inputs` 记录对应至多一个阶段：initial 输入形成 sequence 0，后续 resume 依次形成 sequence 1、2 等。公开节点事件从上一个边界之后开始，归入下一个 `interrupt`、`final_result`、`error` 或 `canceled` 边界所关联的输入；活动 Job 最后尚无边界的区间归入最新输入。

用户消息通过 `chat_messages.analysis_job_input_id` 与输入账本精确关联；边界 assistant 消息同时通过 `source_event_id` 指向事件、通过 `analysis_job_input_id` 指向输入。同一输入先 interrupt 后取消时仍只有一个阶段，取消只把该阶段状态更新为 `canceled`。任一关系缺失、多义或与 `analysis_job_inputs.chat_message_id` 不一致时，该区间不进入历史展示。

Deep Agent 的算法公开决策、工具开始/结果和 Gate 后最终决策都作为阶段内公共事件保存，不依赖浏览器内存或 checkpoint 正文。会话刷新按 Job 事件 ID 顺序重建阶段；`step_id` 只负责把明细挂到对应父节点，不作为数据库实体或授权依据。

## Checkpoint 身份和跨库清理

父图 checkpoint 身份是：

```text
thread_id = analysis_jobs.job_id
checkpoint_ns = ""
```

业务 `session_id` 仍然是 MySQL `sessions.id`，不是 checkpoint thread。新 Job 从同一 Session 的 MySQL 聊天历史加载有界初始窗口；同一 Job 的 resume 和 stale recovery 才继续原 checkpoint。旧的 session-thread checkpoint 不迁移、不读取、不清理。管理员 checkpoint 摘要必须使用 `metadata.job_id` 精确关联，缺少该字段的记录不能按时间猜测归属。

Deep Agent 的 checkpoint 与长期记忆共用 PostgreSQL 实例但不是同一类数据：父图以
`thread_id=job_id` 保存外层 Job State；Deep Agent 子图以
`thread_id=deep-agent:<uuid5(job_id)>` 保存内部 messages、工具状态和短期 raw 文件。
数据处理子图也使用 `thread_id=job_id`，以
`data_processing_v1:<run_hash>` namespace 隔离冻结输入；同一 Job 内参数修订不改变处理 namespace；同一 Job 的 checkpoint 清理会删除该 thread 的全部 namespace。
父图传入的 namespace 配置是 `deep_agent_v1`，当前独立调用实际保存在根 namespace（空字符串）；
读取应以实际持久化位置为准，正式中断恢复和故障接管的验证边界见
[`agent-runtime.md`](agent-runtime.md#父图子图与持久化)。`AsyncPostgresStore` 另以
`("causalagent", "memory", str(user_id))` namespace 保存 `/memories/preferences.md`
和 `/memories/research_background.md`。worker 首次使用某个用户 namespace 时只做
create-if-absent 初始化，不覆盖已有内容；运行时对象与 MCP session 不会写入 Store。
子图 checkpoint 只保存由 Job、attempt、lease 和输入身份计算出的不可逆
`execution_scope` 摘要，不保存 execution guard、executor、数据库连接或签名材料。

删除 Session 或用户时，MySQL 事务先锁定并删除业务数据，同时为相关 Job 写入
`checkpoint_cleanup_outbox`；物理删除用户时还会为同一 `user_id` 登记一条
`user_memory_cleanup_outbox`。两类登记与业务删除在同一 MySQL 事务提交，任一登记失败就
回滚整个删除。单独删除 Session 或 Job 只登记 checkpoint 清理，不删除用户长期记忆。

同一个 cleanup worker（`python -m Database.agent_persistence_cleanup_worker`）用租约轮转领取
两张表，避免任何一类任务长期排在后面。checkpoint 任务在一次 attempt 内删除父图和数据处理子图使用的
Job thread，以及因果 Deep Agent 的 `thread_id=deep-agent:<uuid5(job_id)>`；全部删除都成功才标记完成，
部分成功则整项重试并依赖官方删除接口的幂等性。记忆任务按
`("causalagent", "memory", str(user_id))` 构造 namespace，用官方 Store API 逐条删除该
namespace 下的全部条目，并在删除后重新查询确认 namespace 为空；checkpoint 任务不触碰
`store` 或 `store_migrations` 表。租约过期可以恢复，失败按有限次数和退避重试。两个数据库
之间没有伪造的分布式事务，后台清理状态必须可查询。

管理员用户删除操作通过 `operation_id` 同时聚合两张 outbox，只有 checkpoint 清理和用户记忆
清理全部成功才进入 `succeeded`，任一任务最终失败则进入 `failed`；被归类为终态失败或租约
过期的任务由维护入口 `Database/lifecycle_repair.py` 重置后重新领取。

删除逻辑文件时，如果仍有活动 Job 使用该 `user_file_id`，请求必须被阻断；删除 `user_files` 后只有在没有其他逻辑引用时才删除 `file_objects` BLOB，不提供回收站。
