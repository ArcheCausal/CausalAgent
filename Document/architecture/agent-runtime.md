# Agent 运行时

文档职责：记录 Agent worker、Deep Agent、LangGraph、MCP、RAG、联网搜索、结构化输出和公共执行事件的当前协作方式。

适用范围：修改 `Agent/`、`app/agent/worker/`、`Agent/CausalAgentMCP/`、RAG 初始化、联网搜索或用户可见事件协议时使用；Job、checkpoint 与文件生命周期见 [`job-file-lifecycle.md`](job-file-lifecycle.md)，执行约束见 [`../../Agent/AGENTS.md`](../../Agent/AGENTS.md)。

## Worker 启动与运行时边界

模型按职责分为两套配置。外层图使用 `MODEL`、`BASE_URL` 和密钥文件中的 `API_KEY`，明确关闭思考，统一结构化输出入口继续关闭思考。数据处理 Agent 与因果分析 Deep Agent 共用 `DEEP_AGENT_MODEL`、`DEEP_AGENT_BASE_URL` 和独立的 `DEEP_AGENT_API_KEY`，使用 Responses API；`DEEP_AGENT_REASONING_EFFORT` 默认 `high`，允许 `none`、`low`、`high`、`max`，非法值在 worker 初始化时直接失败。这两套模型分别执行各自节点，不增加研究参数审查模型。开启思考后的工具调用历史仍由 Agent 消息与 checkpoint 保存，公共事件不得输出推理内容。

DeepSeek 思考模式不接受强制指定工具或 `required`。模型适配器将思考模式下的强制工具选择改为 `auto`，包括 LangChain ToolStrategy 产生的 `any`；关闭思考时保留原选择。工具 schema、结构化结果校验、参数待处理门禁和最终决策门禁继续生效，自然语言回复不能代替有效提交。接口约束见 [DeepSeek 工具选择说明](https://api-docs.deepseek.com/api/create-chat-completion/)。

`python -m app.agent.worker` 进入 worker bootstrap。生产路径在领取 Job 前按以下顺序初始化：

```text
MySQL readiness
  → PostgreSQL checkpoint pool/schema
  → AsyncPostgresStore setup
  → MCP Client pool connect/handshake
  → 静态 Algorithm Registry 与领域工具
  → RAG active release readiness
  → 沙盒控制服务客户端配置校验与 Provider 构造
  → 数据处理 Agent、因果 Deep Agent 与父图编译
  → worker ready
  → slots claim Job
```

`ProcessRuntime` 持有进程级 MCP Client pool、`McpAlgorithmExecutor`、静态 Registry、PostgreSQL Store/checkpointer、RAG readiness、领域工具、沙盒控制服务 Provider、两个 Agent 和编译后的父图。启动时校验控制服务连接和独立凭据，创建沙盒时调用受限控制 API；上游 OpenSandbox 密钥不进入 Worker。`SlotRuntime` 只引用这些共享对象，不为每个 slot 重建 graph 或创建 stdio MCP session。每次 Job invocation 再创建 `AgentRunContext`，绑定可信 Job、attempt、lease、输入身份、execution guard、executor、filesystem backend、RAG 状态、联网搜索开关、沙盒 Provider、数据处理 Agent 和事件 sink；这些运行时对象不进入 State 或 checkpoint。

进程级 MCP Client pool 与 worker slot 数量相互独立。默认 execute lane 为 `2 × 1`，control lane 为 `1 × 2`；控制 lane 只负责取消，不增加服务端算法并发。启动任一步失败都会在 `worker.startup.ready` 前终止进程，不领取 Job。

`runtime.py` 仍保留 `ProcessRuntime.graph is None` 时的旧 stdio 构造分支，供兼容测试使用。`main_async()` 的生产初始化会装配父图，因此不会进入该分支。

## 父图、子图与持久化

数据分析主路径为：

```text
agent → data_processing → validate_dataset → bind_dataset → preprocess → deep_agent → finalization_gate → report
```

父图直接表达的其余路径为 `agent → report`（按当前分析上下文重新生成报告）、`agent → context_switch` 与 `context_switch → data_processing / report / inquiry_answer / normal_chat`（解析并切换分析上下文后再继续）；普通聊天和报告追问仍由 `normal_chat`、`inquiry_answer` 路径结束。新分析先把冻结的原始 CSV 和研究目标交给数据处理子图。数据处理 Agent 可通过现有 interrupt/resume 契约澄清观察单位和转换语义；它只能调用受控领域工具，不能使用内置 Shell、文件写入工具或通用子 Agent。脚本由独立控制服务经 OpenSandbox API 在 Python 3.11 处理镜像中运行，原始输入只读，沙盒创建请求默认拒绝所有外连。候选 CSV、脚本和说明先写入 MySQL；独立校验节点读取实际文件，通过后才绑定 prepared_dataset_id 并重建分析画像。因果分析阶段的所有算法调用使用同一份已校验数据，不在 MCP 或算法适配器中改写输入。

候选数据集和提交后的脚本正文保存在 MySQL file_objects，analysis_job_datasets 记录候选 revision、来源 hash、对象引用和验证报告，Job 保存最终选中产物 ID。独立校验器先流式检查 CSV 行列结构，并限制最多 1,000,000 个数据单元格，再将文件载入 DataFrame 计算画像。PostgreSQL checkpoint 保存当前阶段、运行标识、产物引用、方案说明及尚待执行的脚本草稿，不保存 CSV 正文或 DataFrame。每次脚本执行都创建短期沙盒；用户等待答复时不会保留正在运行的命令。恢复时按原始文件与已保存脚本重新建立工作区；候选幂等身份包含文件、脚本及附带确认版本的规范化元数据，重复提交返回原 revision。数据处理子图使用以 Job 为 thread、以处理运行标识分隔的 namespace，因此现有 Job 清理 outbox 可一次清理该 Job 的全部处理 checkpoint。

研究参数由数据处理 Agent 通过唯一的 `set_research_spec` 工具提交。父图入口只判断会话意图，不提取研究参数；子图从当前 Job 的真实初始输入及必要的已有研究上下文开始，先用 `inspect_frozen_dataset` 获取完整原始列结构和受限预览，再提交完整参数草稿。`ResearchSpec` 同时表达角色、研究问题、观察单位、时间范围、聚合范围、测量声明和唯一可选的 `effect_request`；程序检查结构与列引用，解析成功后保存有效参数。确定性错误直接返回同一 Agent 纠正，只有真实歧义才通过现有提问工具暂停。解析失败不能以空参数继续分析。

`effect_request` 使用 [共享严格 schema](../../Agent/effect_request.py)：`effect` 只接受 `average_total_effect`，`intervention` 按 `type` 区分带 `from/to/unit` 的 `fixed_contrast` 和带 `delta/unit` 的 `additive_shift`，数值必须有限，额外字段被拒绝。`target_population` 仅用于额外限定观测子群；使用已确认分析数据的全部观测时省略或为 null，不把参与结构探索的列范围写成目标总体。没有估计需求时，完整研究草稿必须显式提交 `effect_request=null`；只有处理和结果角色不会自动触发估计。存在估计意图但干预、数值或单位不明确时，Agent 通过现有 `ask_data_processing_question` 提出具体自然语言方案，用户接受后才写入参数，不补默认干预，也不以 null 绕过澄清。

测量声明区分 `columns` 与 `all_input_columns`。后者的列列表必须为空，由程序依据冻结 CSV 的完整列集合展开；“全部11列”不能保存为列名。完整草稿整体替换有效测量声明，纠正错误条目时不会保留旧条目。`target`、`treatment` 保存研究角色名称，可以与原始或输出列名不同；参与范围、算法和观察单位不属于角色。用户未修改的字段应保留在完整草稿中，明确取消使用 null；程序不使用自然语言关键词代替模型理解。

明确的新研究意图可以同时更新 `analysis_question` 和 `aggregation_scope`；未改变的角色、观察单位及时间范围保持当前值。草稿方案中的提议只有在用户实际接受后才能成为参数；只接受 A 时不能同时写入未接受的 B。处理完成及已绑定产物恢复时，父图的研究问题同步为最终参数中的问题，后续分析读取同一份含义。

工具接收期望修订号、真实输入引用和变更原因（首次提交、用户修改、解释纠正），不接受输入正文或其他 State 写入。输入引用必须已经由程序交给 Agent，并由 MySQL 账本再次核验 Job 归属；澄清答复仍通过持久化 interrupt 关联外层中断与工具调用。程序计算实际差异，只有变化才追加修订，允许同一真实输入支持后续纠正。当前版本的相同提交不增加记录；紧邻提交的相同快照可重放，过期或冲突草稿拒绝。修订链与候选参数绑定使用 `PROCESSING_STATE_VERSION=4`；完整效应定义参与参数指纹、修订和恢复校验，画像不参与研究参数身份。

存在待处理输入时，参数提交必须引用该输入；引用较早来源会被拒绝且不修改参数、修订链或待处理状态。处理完最新输入后，才可引用已交付的原始输入进行解释纠正，避免把新答复的变更错误记在旧来源上。

提问和方案确认工具只接收真实答复，不再调用独立的参数提取模型。自由文本进入 checkpoint 后立即成为待处理输入，使旧确认和候选失效；同一 Agent 通过参数工具处理该输入后，方案工具才能保存绑定当前参数身份的方案。无参数变化也必须记录输入已处理。参数变化、方案变化或未处理输入均不能复用旧执行资格；按钮对有效版本的确认不重新解释研究参数。来源、修订链、文件身份和租约冲突直接失败；解释纠正与工具循环继续受现有调用预算及完成纠正上限限制。

处理运行身份只依赖 Job 和冻结输入；研究参数变化不切换 `data_processing_v1:<run_hash>` checkpoint namespace。初始子图保存空研究草稿（含显式 `effect_request=null`）与可信待处理输入，恢复及校验重试优先使用已有子 State。父包装节点显式接收最终参数、修订链和候选身份，再幂等完成上下文绑定；已有历史结果的研究不能被改写含义，必要时新建上下文。候选 metadata 保存最终参数与方案身份，独立校验和绑定逐次核对；已绑定产物恢复时同时恢复对应参数。缺少 `effect_request` 表示未记录，不等于明确未请求；不受支持版本的处理 checkpoint 和候选不能恢复执行，也不回填新字段。历史上下文身份比较与执行恢复的区别见 [Job 与文件生命周期](job-file-lifecycle.md#分析上下文与上下文绑定)。沙盒 API 与容量事实见 [沙盒控制服务](sandbox-controller.md)。

处理阶段的开始进度事件另行包含当前 attempt 和父任务 ID。恢复或校验重试产生新的阶段实例时使用不同事件键，同次任务重放仍可去重；公开阶段事件的身份不改变子图 checkpoint 或沙盒申请身份。

父图 checkpoint 使用 `thread_id=analysis_jobs.job_id` 和空 namespace。Deep Agent 子图使用同一个 PostgreSQL saver 和稳定的 `thread_id=deep-agent:<uuid5(job_id)>`，独立调用的读写均使用该 thread 的根 namespace（空字符串），不携带父图的 checkpoint ID 或位置映射。父节点读取同一位置的已有状态后继续未完成子图，或在 Gate 修正时追加受控指令。子图 State 保存的分析身份包含 Job、当前冻结输入、算法 Spec 摘要和完整参数生成的 `research_identity`；worker、attempt 和 lease 只代表本次执行权限。输入身份读取的是 invocation 级可刷新引用，服务端重新冻结输入、切换分析上下文或改变效应定义后，不能复用身份不符的执行产物。

首次进入因果子图时，父图只传递用户消息，再显式加入分析问题、画像、研究参数、预处理摘要和执行说明等结构化上下文。程序生成的路由、进度和其他父图消息不作为子图的模型历史。子图已经保存的模型响应、工具调用和返回仍由其自身 checkpoint 完整恢复，Gate 修正只追加新的受控指令。

两个 Agent 共用的 DeepSeek Responses 适配器在流式 `response.output_item.done` 事件中读取真实 `reasoning.content`，与响应其他内容一同写入消息和 checkpoint；不重复拼接推理 delta，也不生成替代推理。同步和异步流式调用均保留这些文本，非流式继续使用框架的完整响应解析。DeepSeek 使用无状态 Responses 协议，每次回传完整历史；即使设置 `store=False`，明文推理也保留，不使用 `previous_response_id` 省略历史。推理内容不进入公共文字事件。

提交数据处理语义问题时，子图在内层 `interrupt` 暂停；父图保留原生中断 ID 和公开载荷，让 LangGraph 直接向外传播暂停，不把它当作执行失败，也不再增加一层人工中断。用户答复通过父图的 `Command(resume=...)` 回到子图原中断位置，连续提问分别消费自己的答复。子图配置保留父任务的恢复索引，移除父 checkpoint ID、位置映射和重放状态，并显式指定共享 saver；读取处理 namespace 时不会误用父图位置。等待期间结束本轮执行并释放 OpenSandbox、MCP 请求、算法子进程和 worker 执行槽位。

从可信 checkpoint 恢复时，系统校验同一 Job、冻结输入、Spec 摘要和数据处理运行身份；已完成结果以原始 provenance 进入 Gate 认可的恢复结果集合，不因新 attempt/lease 被重复执行。旧 worker 的迟到写入继续被 fencing 拒绝；checkpoint 不可可靠读取时阻止恢复。人工恢复只增加 `resume_count`，不消耗故障恢复次数。

长期记忆由 `AsyncPostgresStore` 单独保存，不属于 Job checkpoint。因果 Deep Agent 只注册 `read_file` 和 `edit_file`，并且只允许写 `/memories/preferences.md` 与 `/memories/research_background.md`；其他写入由兜底 deny 拒绝。数据处理 Deep Agent 的默认文件系统工具被限制为只读，脚本只能经专用工具保存并在 OpenSandbox 运行。两者使用同一 DeepAgents Harness profile：关闭通用子 Agent，并从可见工具中移除 Shell、默认写文件、删除和目录搜索工具。namespace 从可信 `AgentRunContext` 的 `user_id` 生成，首次使用时仅做 create-if-absent 初始化。运行时对象不写入 State 或 checkpoint。

cleanup outbox 在同一次清理中删除父图 Job thread 和独立的 Deep Agent child thread；两者均成功才确认完成，具体生命周期见 [`job-file-lifecycle.md`](job-file-lifecycle.md)。

执行前，数据处理 Agent 必须通过公开交互展示方案并等待用户确认。服务端按 `causalagent-data-processing-plan-v2` 计算 SHA-256 方案版本，绑定 Job、冻结文件 hash、完整研究语义和方案 metadata；其中包含效应定义、角色映射及 `effect_execution_context` 的列、单位与转换说明。确认卡中的 `effect_request` 由程序从当前研究参数投影，方案工具不另行接收一份定义。确认版本保存在 Job 的当前交互和恢复输入账本中，候选元数据也保存该身份。用户以自由文本修改或拒绝时不会形成确认；新的语义答复、效应定义、映射或转换说明变化会使 State 中的确认失效。已确认语义范围内的脚本修正可以复用该确认；脚本与候选文件仍单独记录内容 hash。脚本执行入口和 MySQL 候选写入事务都核对当前租约及最新确认版本，独立校验和最终绑定继续核对同一版本。

数据处理子图把方案草稿保存在 `data_processing_metadata`，只允许 `set_data_processing_plan` 修改；用户确认且服务端核对 MySQL 当前确认凭据后，复制到 `data_processing_confirmed_plan`，与 `data_processing_confirmed_plan_version` 一同写入 PostgreSQL checkpoint。执行后的解释经 `save_data_processing_execution_report` 写入独立的 `data_processing_execution_report`；候选 ID、修订号、方案版本、来源和文件 hash、行数及列名由程序读取实际产物生成，解释不能代替独立校验。这份说明通过父子 State 白名单传给后续因果 Agent，不写回候选 metadata。

重复保存相同方案保留确认及候选；方案正文或研究语义变化清空有效确认快照、版本、当前候选引用及执行说明。脚本变化保留方案确认，但清空候选引用和说明，必须重新执行；执行失败同样清空当前候选，成功保存后只为本次候选建立新说明。历史候选不删除。工具按轮顺序执行，同一模型响应中的多个工具调用会收到未执行反馈，避免它们同时读取旧 State。

子图结束 middleware 重新核对当前方案、确认快照与版本、MySQL 确认凭据、候选归属及研究参数、语义身份、脚本 hash 和说明绑定。缺少确认时调用原有确认工具并使用原生 `interrupt`；脚本未执行时要求模型先执行，再申请结束；未解决歧义继续调用原有澄清工具。纠正最多三次，计数随 checkpoint 保存，不重置模型、工具预算或父图独立校验重试。来源、hash、租约和持久化记录冲突直接失败；父节点的一致性检查继续保留。不新增数据库列、迁移、人工中断层、Job thread 或数据处理 checkpoint namespace，旧 checkpoint 不迁移；新父图恢复前检查版本，不支持时提示重新发起任务，历史记录和产物保留。

研究参数的 `target`、`treatment` 保存用户表达的角色名称，可以与 CSV 列名不同。数据处理 Agent 在方案的 `role_columns` 中明确记录这些角色对应的输出列；映射随方案展示、确认并保存到候选 metadata，纳入已有 `plan_version`。未指定的角色保持为空；角色已经是实际列名时可以直接使用。独立校验先解析映射，再检查实际列的存在性、角色冲突和字段语义。`preprocess`、Deep Agent 输入和最终决策校验共用 `execution_parameters()` 生成执行参数副本，算法与 ATE 使用同一组列名；原始研究参数及修订身份保持不变，报告的数据处理事实保留映射。该流程不增加模型调用、数据库字段或确认阶段。

`execution_parameters()` 同时从已确认 metadata 投影 `effect_execution_context`，包含 `treatment_column`、`outcome_column`、原始业务 `treatment_unit`、`treatment_transform` 和 `outcome_transform`。处理转换明确记录源列、输出列及两侧单位；`affine` 按 `output = scale × source + offset` 定义，scale 必须有限且非零。保留原始值时必须明确声明 `identity`，不能根据同名列或自由文字推断；未知转换保留 null，不从参数中的陈旧 context 回退。context 的实际列必须与角色映射一致，业务单位必须与请求一致，防止把业务尺度的干预值直接施加到标准化列。

方案的 `measurement_columns` 显式映射原始列到保留或重命名后的输出列；null 表示删除或不再继承，缺席默认同名保留。聚合或计算派生列不自动继承原始测量类型。参数工具先依据真实输入列解析研究声明，方案工具核对映射来源，独立校验对映射后的输出声明检查列存在性和冲突。通过的 `semantic_kinds` 保存为权威输出快照；画像重建读取该快照，不再要求展开后的每个列名逐字出现在证据文本中。映射经公共事件白名单和前端卡片展示，并纳入方案版本。

独立校验失败时，父图从已有报告读取错误代码及说明、`research_roles`、`role_columns`、解析后的 `target` 和 `treatment`、候选 CSV 实际 `columns` 及算法兼容性结果，交回数据处理 Agent，最多进行三轮修正。这些内容只进入内部模型上下文，不进入公开 SSE 或普通日志；正常交互仍返回用户答复，不追加完整参数快照。角色名称与实际列名不同或映射错误时，通过现有方案工具修正 `role_columns`，按原流程确认并重新执行；不改写研究角色，也不要求用户重复选择。脚本遗漏已确认方案要求的字段时，在原确认范围内修复；用户信息有多个合理含义时提出具体澄清问题。已有明确答复与保存参数矛盾时，使用 `set_research_spec` 纠正，再更新方案并重新确认；不要求用户重复回答明确的问题，不得忽略校验或默认清空角色，仍失败时按现有上限如实结束。超出已确认语义范围的处理需要再次提问和确认；校验通过后直接绑定。`plan_version` 继续绑定确认范围，`prepared_dataset_id` 继续指定实际分析 CSV。因果分析 Agent 不会反向启动数据重处理。公开事件按字段和大小限制保留方案版本及摘要，不公开脚本或内部身份。沙盒预览只读取有限前缀，在 UTF-8 字符边界截断并标注；完整输出超过限制时仍拒绝保存。

## 分析上下文与追问路由

`Session`、`AnalysisContext`、`Job` 和 checkpoint 各管一段事实：Session 是用户可见的会话容器；AnalysisContext 是 MySQL 中的跨 Job 业务事实，保存创建时的文件快照、分析参数、最新结构化算法摘要、RAG/Web 证据摘要和最新报告引用；Job 是一次具体执行并绑定一个上下文；checkpoint 只负责当前 Job 的执行恢复、中间 State 和虚拟文件。一个 Session 可以拥有多个 AnalysisContext，`sessions.active_analysis_context_id` 只表示当前默认上下文。

数据处理 Agent 统一维护参数，处理完成后由现有父包装节点按最终参数绑定上下文，再进入独立校验与数据绑定。`preprocess` 只强读已绑定的数据集并重建画像，不切换上下文。切到不同上下文时清除旧数据绑定，同一上下文的幂等重放保留绑定；恢复已绑定产物时同时从数据库回填当前 Job 的上下文和最终参数。历史研究参数快照取自已有分析产物，普通聊天或报告追问不会遮住该快照。报告后的普通追问读取报告与分析事实，不重新读取 CSV；新 Job 不自动继承旧 Job 的处理数据。

`agent_node` 用一次结构化调用判断意图（`normal_chat`、`start_analysis`、`answer_report`、`revise_report`、`rerun_analysis`、`switch_analysis_context`、`clarify`），模型只输出意图、`context_hint` 和澄清问题；`route_decision` 由后端按固定映射生成，模型不能输出分析上下文 ID 或用户文件 ID，也不能直接选择图节点。节点输入是当前消息、最近有限聊天历史、当前 AnalysisContext 摘要和同一 Session 的历史上下文索引；索引只含文件名、目标变量、处理变量、报告标题、更新时间和简短摘要，提示词里不出现内部 ID。没有可引用报告时 `answer_report` 与 `revise_report` 回退到 `normal_chat`，结构化输出失败同样回退到 `normal_chat`。

新增的 `context_switch` 节点把“回到某个文件或某次历史分析”解析成真实上下文：按用户表述匹配历史索引，唯一命中才切换；歧义、文件缺失或无匹配都返回澄清问题并保持 active 指针不变；用户点名的是文件库里其他文件时，节点在同一事务里为该文件新建上下文。切换事务同时更新 Session 默认指针、当前 Job 和当前输入账本的上下文绑定，并校验 worker、attempt 和 lease epoch，重复执行得到同一结果。

切换会改变本次 Job 的冻结输入：服务端在同一事务里重写 `analysis_jobs` 的冻结文件快照，并通过 invocation 级引用让 MCP 输入摘要、Adapter 校验、`FinalizationGate` 的 provenance 校验和 Deep Agent 子图 execution scope 一起跟随。旧上下文的算法结果、证据和因果图不会进入新的父图 State。

`report_node` 有两种模式：`normal_generation` 按当前分析结果首次生成报告；`full_regeneration_from_context` 只使用当前 AnalysisContext 已确认的事实重新生成完整报告，不修改算法图，也不新增上下文中没有的因果结论。`inquiry_answer_node` 只回答当前报告或上下文事实相关的问题，或原样转达后端给出的澄清问题，既不决定是否重新执行算法，也不修改报告。

## 算法工具与 causal-mcp

`Agent.deep_agent_tools` 包入口只导出错误码、数据模型、算法规格、执行协议、身份函数和 Registry 等轻量契约。MCP 与 Agent 运行时共用这些契约；依赖调度器、算法工具、RAG/Web 工具和测试执行器由调用者从对应子模块显式导入。MCP 导入契约不会加载 LangGraph、LangChain 或 `Agent.deep_agent`；`dependency_planner.py` 仍直接依赖 LangGraph 的 `Command` 并检查算法调用终态。

模型看到的是由本地 `AlgorithmSpec` 生成的 LangChain function tools。当前默认 allowlist 包含 PC、DirectLiNGAM、CDFM 和后门 OLS 估计 `causal.ate`；OLC 的 Spec、Adapter、runner 和算法代码仍保留，但不进入 worker Registry、MCP runner registry 或旧兼容 MCP 工具面。运行时不会调用远端 `list_tools()` 来动态扩展模型工具。CDFM 工具只公开可选 `threshold`，模型路径、CPU device、标准化和缺失值处理策略由 causal-mcp 固定。

绑定经过独立校验的数据集后，分析准备阶段在 `analysis_parameters.data_profile` 保存该数据集的完整画像。`to_deep_agent_input()` 优先校验该字段，只有字段不存在时才读取父 State 顶层 `data_profile`；选中的画像存在但非法时明确失败，不使用其他来源掩盖错误。转换复制全部行列、列顺序、数值列、分类列、缺失、非有限值、空列、常数列和质量问题，不按 `column_profiles.inferred_type` 重新判断存储类型。两处完整画像都不存在时，历史 `column_profiles`、分析参数和 `file_summary` 只恢复可确认的行列事实，数值与分类类型保持未知。数值参与条件来自实际 dtype 和有限值分布，不再由旧 inferred_type 标签决定。文件摘要继续记录文件身份与基础事实。

提供给模型的画像、子图 State、依赖调度器及 AlgorithmTool/Adapter 输入使用同一份转换结果；完整画像的缺失值事实同时决定 `missing_values_present`。完整画像采用顶层兼容来源时，投影后的分析参数也保存同值副本，父图原输入不被修改。工具和调度器不再从文件摘要分别重建画像；父图完成回写保存边界选中的完整画像副本。`continuous_tabular_dataset` 要求画像确认全部真实列名均为数值列，历史输入的未知类型不能满足这一前提。此转换只作用于新进入子图的输入，不修改旧任务已经保存的 checkpoint 或终态。

`AlgorithmDependencyDispatchMiddleware` 在 ToolNode 边界收集同一模型响应中的算法调用，按 `requires/produces` 分层：无依赖调用可并行，有依赖调用按层执行，并受单 Job 并发和工具总预算约束。`DependencyPlan.missing_artifacts_by_call` 以原始 call ID 保存没有现存输入且没有本轮生产者的 artifact；该字段只属于运行时计划，不进入数据库 schema 或模型工具参数。被阻塞调用仍保留在拓扑批次内，执行前分别检查直接缺失、上游终态和实际发布的产物。直接缺失或上游未成功发布时只为该调用返回 `not_ready`；独立调用继续进入真实 handler，并按其 `AlgorithmSpec` 检查已经绑定的数据集是否满足输入要求。输入不兼容的算法调用结束为安全的 `not_applicable` 或 `invalid_input` 结果，不会修改数据，也不会阻止其他独立调用。未执行结果使用固定安全说明，无因果图或 raw artifact；每个调用仍通过自己的 `Command` 保存 ToolMessage、AlgorithmResult、终态 Ledger 和公共结果事件。重复 call ID、工具与 capability 身份冲突和非法依赖图仍属于受控契约错误，不作为普通缺失输入处理；取消和 lease 撤销继续传播控制流。

Adapter 负责模型参数和数据集输入契约检查、标准结果、raw result 完整性和 Ledger 更新；它不根据模型参数删除行、插补、编码或筛选字段。算法必需的数组转换、矩阵运算和算法原生缺失掩码仍由执行实现负责。`McpAlgorithmExecutor` 只负责签名传输、远端执行结果校验和安全错误映射。CDFM 的完整 logits/probabilities 矩阵通过仅供 Executor 与 Adapter 使用的私有 envelope 保存到 raw artifact，不进入 `AlgorithmResult`、ToolMessage、报告输入、前端图资源、SSE 或公共日志。runner 只从已输出边的矩阵位置提取 `prediction.forward_probability` 和 `prediction.reverse_probability`，经标准结果、工具回执和报告事实传递；`weight` 继续为空。可信 Job、文件、lease、HMAC 与数据库身份不会进入模型参数。

`ToolCallProtocolMiddleware` 在完整模型响应之后检查最新 `AIMessage.invalid_tool_calls`。参数 JSON 解析失败时，它使用原调用 ID 追加 `status=error` 的 `ToolMessage`，说明工具未执行，并且不修改原参数、不把原始参数或底层异常写入返回内容；仅在本地 JSON 解析器能够确认时附带行列位置。当前响应任一调用缺少有效 ID，或调用 ID 重复时，运行以受控协议错误终止，不生成替代 ID。若仍有合法工具调用待执行，继续原工具路由；否则通过 `jump_to=model` 返回现有 `before_model` 链，继续接受模型预算检查。存在无效调用时，本轮 `structured_response` 会清空，父图继续使用既有 FinalizationGate 拒绝、一次修正和降级规则。该钩子只检查当前响应，不修复旧 checkpoint。

因果分析使用 `AnalysisBudgetMiddleware` 管理执行和收尾两个阶段。执行阶段沿用 `PendingToolCallLimitMiddleware` 的计数规则，只统计当前响应中尚无回执的调用；ToolStrategy 已完成的结构化提交不占工具预算，也不追加重复回执。整个未执行批次超过剩余工具预算、工具次数达到上限或执行模型次数达到上限时，进入收尾阶段：隐藏全部算法、检索和文件工具，最多再调用模型三次，仅允许提交或修正 `FinalAnalysisDecision`。即使模型请求已隐藏的工具，执行边界也只返回未执行回执。取消、租约撤销与不可恢复的外部错误继续传播，不转换为预算收尾。

`analysis_model_call_count`、`closeout_model_call_count`、`analysis_budget_exhausted` 和 `analysis_budget_reason` 随子图 checkpoint 保存；工具上限按同一子图的 `thread_tool_call_count` 检查。Gate 修正和重连不会重置预算；收尾仅返回普通文字时，在剩余额度内追加明确的结构化提交指令后续接，不直接发送以普通 assistant 消息结尾的下一次请求。收尾次数用尽仍无有效决策时，沿用既有受控降级。被预算拦截的算法记录为 `skipped` 和 `ALGORITHM_BUDGET_EXHAUSTED`，不生成图、效应查询或适用性检查，也不解释为算法执行失败。数据处理子图继续使用原来的模型及工具限制器，不采用因果分析的收尾契约。

每次模型响应后，identity middleware 先把算法调用以 `pending/queued`、revision 0 登记到 Action Ledger；dispatch 的独立 `after_model` 节点再推进到 `pending/running`、revision 1。父图使用同步 checkpoint durability 消费子图，工具外部调用必须等这些 State 更新提交后才开始。调度器达到 deadline 时取消内部协程并等待 MCP control lane 完成清理，随后以 `timed_out`、`ALGORITHM_TIMED_OUT` 生成无因果图、无 raw artifact 的结果。工具清理路径不提前发布 `canceled`。普通成功、失败、超时及依赖未就绪均通过 `Command` 一并写回 ToolMessage、算法结果和 revision 2 终态账本；用户取消和 lease 撤销传播控制流并取消整批任务，不转成普通算法失败。

调度器读取 `Command.update.algorithm_results` 中与当前 call/capability 匹配的受控结果，保留失败 Command 的 State 更新。只有明确的有效成功结果才发布依赖 artifact；失败、超时、不适用、跳过或无法识别的返回均不放行下游依赖。

调用账本保留原始事件阶段 `event_step_id`；聚合兜底复用原阶段、执行 attempt、retry 和事件键，恢复后不使用新阶段改写旧事件的 payload。旧账本缺少事件阶段或完整调用身份时不合成完成事件。

`AlgorithmSpec` 声明算法实际接受的数据要求、缺失值处理方式、分类数据能力和最小行列数，并通过 `model_description()` 提供给模型。它描述输入条件，不授予算法工具改写数据的权限。数据处理 Agent 读取这些要求作为准备数据的参考；候选文件写入 MySQL 后，独立校验节点从持久化对象读取实际 CSV，生成画像并对每个可用算法执行适用性检查。画像保留变量类型、缺失列、非有限值、空列、常数列和疑似标识符列。数据处理语义问题使用自由文本 interrupt，不再使用绑定算法的处理选项 schema。

算法调用不携带预处理方案或批准凭证。独立校验报告保存规范化 `semantic_kinds` 快照，拒绝非法类型、缺失字段及冲突声明。Adapter 从可信运行时的数据集引用读取该快照，MCP 则从 MySQL primary strong read 的同一已绑定候选读取报告；两端都把测量语义传给 `AlgorithmSpec.assess_applicability()`，缺失或无效报告不能回退为空语义。二值、分类或有序变量不会仅因 CSV 中存成数字而被当作连续变量。MCP 同时核对 Job、lease、原始输入 hash、`prepared_dataset_id`、候选状态、数据对象归属和内容 hash，排队后和执行完成后再次核对输入及语义；它不按算法参数改写 CSV。效应调用还从候选的 `analysis_parameter_identity` 与 `processing_plan_identity` 恢复已确认定义和执行 context，并核对 `research_identity`；调用附带的定义或 context 不能替代权威快照。命令、Action Ledger 与结果 provenance 传递研究身份，结果同时记录分析数据身份和原始来源身份。

未声明测量语义时，缺席字段、`null` 或空序列生成空快照；显式提供的空对象、布尔值、数字及字符串仍属非法声明，独立校验必须拒绝，不能用真假值判断把它们转换为空序列。

`causal-mcp` 是私网 Streamable HTTP/HTTP/1.1 服务。服务端以 MySQL primary strong read 校验 Job、attempt、lease 和 worker 身份，再使用有界队列及独立算法子进程运行 capability。MCP session 不保存 Job、checkpoint 或 Action Ledger。

CDFM v0.1 在 runner 内保留输入列顺序，按变量对将二值矩阵解码为 `acyclic_directed_mixed_graph`；对称的两个 1 生成一条双箭头边，表示潜在共同原因候选。runner 不做 `remove_cycles()`；违反图约束时由共享结构检查拒绝作为主图，不自动删除边。该接入只证明工程调用链和私有结果保存，不证明因果发现准确率。

当 heartbeat 或取消使 `JobExecutionGuard` 撤销时，worker 中断本地等待，并通过独立 control lane 发送签名 `cancel_algorithm`。服务端按完整 invocation 身份取消排队任务或终止目标算法进程，不影响并行 sibling。重复取消返回稳定状态；客户端无法确认最终状态时使用 `unknown`，该值不能解释为远端一定未取消。取消属于控制流，不生成普通失败的 `AlgorithmResult`，也不进入算法重试或 FinalizationGate。

客户端池、服务端执行池、容量、重连和取消状态机的详细说明见 [`mcp-runtime.md`](mcp-runtime.md)。

## RAG 与 Web evidence

`rag_evidence_search` 调用 `RagService.get_evidence()`，不调用旧 RAG answer model。知识库证据工具由 Agent 根据当前问题自主判断是否调用；未调用不构成终态校验失败，实际调用后仍必须保留真实的 terminal Action Ledger 状态，不能伪造证据或结果。worker 启动只校验 active release，不加载 Chroma、BM25 或 embedding；第一次真实查询时才在进程内初始化 RAG runtime。结果以受控 evidence reference、snippet、来源定位、分数、release 和状态写入子图 State，再投影为 report formatter 使用的引用摘要。知识库证据的来源展示名由 release manifest 的 `document_id → relative_path` 解析，manifest 未覆盖时才回退到检索块元数据；`asset_uri` 是内部资源路径，不充当 URL。RAG 的排名编号和 Web 的相关性分数都属于单次查询，因此写入 State 和 ToolMessage 前会用稳定 invocation id 限定 evidence reference；同一调用重放保持幂等，不同并行查询即使都返回 `E1` 或同一来源也不会占用同一个 reducer key。

`web_evidence_search` 返回 SearXNG 学术结果的 snippet 与来源元数据，不抓取网页正文。`ToolVisibilityMiddleware` 在每次异步模型调用时读取可信 `AgentRunContext.web_search_enabled`：只有明确为 `True` 才保留 Web 工具，`False`、`None` 或缺少上下文时隐藏。开关由 worker 从本次 Job 记录绑定到 invocation 上下文，不读取模型消息或 State 作为授权依据，也不保存在共享 middleware 对象上。

工具可见性规则集中在 [`tool_visibility.py`](../../Agent/deep_agent/tool_visibility.py)。过滤只作用于 `request.tools` 已有工具，以 `request.override(tools=...)` 生成新请求，保持顺序和工具对象，不恢复其他 middleware 已移除的工具，不修改 Context、State 或共享工具列表。项目内会处理工具集合的 middleware 先执行；后续框架 MemoryMiddleware 只调整提示词，ToolStrategy 单独追加 `FinalAnalysisDecision` 输出工具，算法、RAG、文件工具保持原有配置。工具返回后的下一轮及 Gate 修正调用都会重新过滤。后续权限、任务类型或阶段规则在本模块扩展，任一适用限制禁止即隐藏；需要任务状态时显式增加字段，授权依据仍来自可信上下文。

固定系统提示根据本次提供的工具是否包含 `web_evidence_search` 要求提交最终决策前至少调用一次并等待真实返回。同一次执行中已完成的搜索可以复用，不因修正决策强制重复；需要补充证据时仍可再次搜索。父 State 投影不再按搜索开关追加提示，分析路由的算法调用要求继续保留。工具执行入口仍独立读取运行上下文，关闭时返回 `WEB_SEARCH_DISABLED` 且不触网。可见性过滤不代替执行授权检查。

`FinalizationGate` 继续校验当前 Job、attempt、lease、worker 和输入身份下完整的 terminal Action Ledger。同 attempt 的已完成记录可满足后续决策修正，未完成或终态不一致的记录不能满足检查；新 attempt 不能借用旧 attempt 的搜索记录。无结果、不可用等真实终态按原规则保存和校验，不要求必须取得证据。修正阶段公开说明为“复用已有结果，并补齐校验要求的步骤”。发布验收使用更新版本新建的 Job，本次不处理旧版本 Job 的兼容。RAG/Web 的异常只转换为受控状态和安全错误码，不把异常正文、查询参数或 provider 数据带入公共事件。

旧 `build_graph()` 的固定 `mcp → rag → web_search` 子图只作为兼容路径保留，不是 worker 当前生产入口。

## 因果效应估计与最终门禁

效应估计能力 `causal.backdoor_ols` 与 `causal.g_formula` 的 MCP 契约均为 `3.0`，runner 分别为 `backdoor-ols-runner-v4` 与 `g-formula-runner-v3`。模型提交 `graph_result_ref`、实际 `treatment/outcome` 和已确认的完整 `effect_request`；g-formula 另接受 `outcome_model` 与 `bootstrap`。Worker 核对发现结果的 Job、执行权限、输入与研究身份、源算法版本和图契约；MCP 再从已绑定候选恢复同一请求及执行 context。未知、过期、身份不符或结构不合法的引用返回 `invalid_input`，依赖未就绪返回 `not_ready`。拒绝也经 `algorithm_terminal_command()` 原子写入结果、工具消息和终态账本。

只有明确的 `effect_request` 才启动效应诊断和估计要求。Agent 侧 `effect_diagnose` 只检查当前源图、研究身份、变量对和效应定义，不执行估计；它返回 `verdict=proceed|reject|error`、逐项检查、理由及推荐能力。`proceed` 表示结构上可以继续检查与估计，不能证明识别假设全部成立。分析路由中有合格发现图时，已请求的效应需有同定义诊断；诊断 `proceed` 后需真实发起估计。不适用、失败、超时和未执行保留真实终态，其他定义或源图的记录不能代替本次查询。

[共享适用性规则](../../Agent/deep_agent_tools/ate_applicability.py) 由 Worker、MCP 与两个内核使用，先检查图是否支持当前方法，再取处理变量的全部父节点作为调整集。有效 PDAG 仍可能因未定向边、双箭头边或查询结构返回 `not_applicable`。全部执行列还需通过存在性、数值、缺失、空列、常数和非有限值检查；声明为 `category`、`ordinal` 或 `binary` 的参与列不适用，不参与列不受此限制。未声明测量语义时保留未充分确认的边界，不依据整数 dtype 或唯一值数猜测分类含义。OLS 继续检查设计矩阵共线、自由度和数值有限性；算法不自动删行、填值、编码或改变源图。

[干预准备模块](../../Agent/CausalAgentMCP/inference/interventions.py) 使用已确认的原始业务单位解释干预，当前支持处理列的显式 `identity` 或可逆 `affine`，结果列只支持显式 `identity`。缺少 context、未知或不支持的转换、列或单位冲突均拒绝，不能将业务值直接套到标准化列。明确的额外 `target_population` 当前尚不能验证成员资格，返回 `EFFECT_POPULATION_UNSUPPORTED`，不能以全部样本代替指定子群。

支持检查在原始业务尺度上进行：固定对比检查两个端点，平移干预检查所有观测的 `T+delta`。越出观测处理范围返回 `not_applicable`，记录越界数量及比例，不截断、不删行、不外推，也不缩小总体。记录的 `scope=global_observed_range_only` 与 `conditional_overlap=not_assessed` 明确表示仅检查全局观测范围；同定义的范围检查通过，不能推断给定调整变量后的条件重叠、无未观测混杂或其他识别假设成立。

OLS 对固定对比计算 `beta × (to-from)`，对平移干预计算 `beta × delta`；标准误按干预幅度绝对值缩放，反向对比的区间端点保持正确顺序。g-formula 在已确定调整集 `Z` 及识别假设下拟合 `m(t,z)=E[Y|T=t,Z=z]`：固定对比计算平均 `m(to,Z)-m(from,Z)`，平移计算平均 `m(T+delta,Z)-m(T,Z)`。结局模型可选 `linear` 或 `hgb`，使用同一样本的成对 bootstrap 计算效应区间及相对线性基准的差值不确定性。零估计、零标准误或 `[0,0]` 区间本身不决定结果是否有效。

`AlgorithmResult.ate_check` 保存程序生成的源图引用、变量、调整集、执行列、`applicability=ready|not_applicable` 和稳定原因码；调整集未知时相关字段为 null。`causal_effect` 保存完整请求、实际样本身份和行数、调整规则及支持检查。终态账本的 `effect_query` 保存源图、变量、请求和估计器实际配置，用于核对执行，不能由模型事后补写。失败或超时只有完成适用性检查后才可保留真实 `ready` 记录，超时结果不能携带成功图或 raw artifact；预算跳过不生成检查记录。MCP 从原因码白名单生成受控诊断，不公开任意异常正文。

方法比较使用单独的比较身份：输入与分析数据集、`research_identity`、实际样本身份和行数、源图、实际处理与结果列、完整效应定义（含单位和额外总体限制）、排序后的调整集及调整规则必须相同。估计器配置不参与该身份，因此 OLS 与不同结局模型可在同一科学定义下比较；完整 `effect_query` 仍用于各自执行一致性核验。不同源图、样本、定义或调整规则的结果不可作为同组方法差异。比较输出效应差和有证据支持的成对差值标准误、区间；缺少对应基准证据时不补差值不确定性。

普通 `effect_difference` 按 `difference_direction=right_minus_left` 表示右侧减左侧；`paired_effect_difference` 与 `paired_difference_direction=linear_minus_flexible` 及成对差值区间同向，表示线性估计减灵活估计；重新生成报告时，同版 `analysis-facts-v3` 快照缺少成对点值但已有相应可比记录和方向时，仅从快照中已保存的 g-formula 诊断补值，不重新计算估计。

残差和数据非线性指标、方法差值与区间都是诊断事实。程序不按固定差值阈值裁定科学结论，也不强制采用某个估计器。Agent 根据识别条件、支持检查、模型假设与不确定性，独立选择 `primary_graph_result_ref` 和 `primary_effect_result_ref`，可采用 OLS、g-formula，或保留主图但不采用主效应并说明原因。兼容字段 `primary_result_ref` 仅投影已选择的主效应或主图。每个有效结果仍需唯一取舍，主图与主效应各最多一个。

Gate 严格核验引用、来源、研究身份、终态账本及实际执行配置。主效应必须与已确认定义和角色一致，来自所选主图，并有同定义成功诊断、实际样本及调整证据和 `support.status=supported`。无主效应时记录真实的 `effect_status` 与 `effect_reason`：`not_recorded`、`not_requested`、`not_applicable`、`not_identifiable`、`incomplete` 或 `not_adopted`；采用时为 `adopted`。效应未采用不自动否定独立通过门禁的主图。

无主效应时，`not_applicable` 的估计拒绝记录和 `not_identifiable` 的诊断拒绝记录必须匹配本次定义与变量对；已选择主图时，还必须来自该主图。报告出现科学检查缺口或预算耗尽时，按所选主效应算法判断是否固定提示 OLS 线性假设与区间解释范围。

父图收集缺少诊断、估计或外部证据核对等科学检查缺口为 `analysis_limitations`；未传限制收集器的独立 Gate 校验仍严格拒绝相应缺口。可修正的决策或引用错误只允许一次修正，沿用分析身份和累计执行、收尾预算，不重新开放预算。来源、身份、账本和状态冲突直接失败，不能降为成功。只有 Gate 返回的 accepted 决策可供报告选择主图或主效应；修正后仍降级时不从原始响应恢复选择。

算法在独立进程中执行，进程池默认使用 `forkserver`，避免父进程的 OpenMP 线程经 `fork` 继承；Windows 使用平台默认 `spawn`。Adapter 按 invocation 传递已完成的适用性检查，使外层 deadline 超时仍可保留对应事实，该临时通道不进入模型参数或 checkpoint。报告事实版本为 `analysis-facts-v3`，保存实际执行列、原研究角色、请求、逐结果查询、比较、支持检查及 Gate 接受的独立选择；报告与重新生成规则见下文。

## 方法定义、图约束与数据画像

[AlgorithmSpec](../../Agent/deep_agent_tools/algorithm_specs.py) 是方法解释的唯一来源，生成工具描述和执行时的 `MethodDefinition` 快照。当前 PC、DirectLiNGAM 的方法契约版本为 `2.0`，CDFM 为 `2.1`。`AlgorithmResult.method` 保存输入要求、假设、目标图类型、矩阵方向、边含义和边界；`implementation` 分开记录 runner、实际包版本、内嵌实现版本及权重身份，不把三者混作同一个版本。CDFM 本地权重记录实际 SHA256；只有本地 manifest 的文件摘要匹配时才采用其 revision，否则 revision 保持未知。新增可选边字段会改变共享标准图 schema 的 Spec 摘要，Worker 与 causal-mcp 必须使用同一源码一起更新。

MCP 成功结果使用同一次解析得到的方法和实现元数据。runner 没有提供有效版本时，`implementation.runner_version` 和 `provenance.algorithm_runner_version` 均保持 `None`；不推断版本，也不把未知值转为空字符串而触发结果契约错误。

调度器在输入依赖缺失时直接产生的 `not_ready` 等终态也保存已知的 `MethodDefinition`。这些调用没有执行算法，`implementation` 和原始结果保持为空，不能把方法定义误写成实际执行证据。

| 方法 | 图与矩阵 | 解释边界 |
|---|---|---|
| PC | `pdag`，causal-learn 端点编码 | 普通有向边是候选方向，无向边尚未识别方向；要求合适的条件独立检验、马尔可夫性、忠实性和因果充分性。本次使用 Fisher-Z 时须说明偏相关与高斯条件独立关系的限制，不能从算法通用性推导本次检验不受分布条件限制 |
| DirectLiNGAM | `dag_target_to_source`，矩阵行是结果、列是原因 | 线性、非高斯、无潜在混杂等假设；边系数不等于置信度或已识别干预效应 |
| CDFM | `acyclic_directed_mixed_graph`，二值矩阵行指向列，按变量对解码 | 学习得到的结构候选；不能套用 LiNGAM 的线性、非高斯假设，也不能把候选共同原因写成已证实 |

CDFM 对每对变量读取 `(A[i,j], A[j,i])`：`00` 无边、`10` 为 `i→j`、`01` 为 `j→i`、`11` 为一条 `i↔j`。双箭头表示潜在共同原因候选，不能拆成两条普通有向边。raw artifact 继续保存原始邻接矩阵、变量顺序、logits、probabilities 和实际阈值。编码依据是实际安装包的方向实现及 CDFM 方法约定，不能将没有编码字段的 checkpoint 描述为自行声明了编码。

CDFM 提供候选结构及模型预测概率。runner 按输出边端点和原始变量顺序提取两个分数：`forward_probability = probabilities[source_index,target_index]`，`reverse_probability = probabilities[target_index,source_index]`；反向有向边仍以实际 source/target 为准，双箭头分别保存两个编码分数。`diagnostics.metrics.threshold` 保存模型实际使用的阈值，包括自动校准值。标准化、报告事实快照与图资源保留相同精度，报告模型只解释已提供数值，不重新计算或补写。概率不是结构系数、效应量或统计置信度；程序不据此提高整体置信度或强制选择主图。旧报告缺少字段时保持缺失，不从矩阵回填。展示规则见[普通用户前端](../development/chat-frontend.md)。

CDFM 的输入准入检查确认数值存储和可计算性，不将整数列或低基数数值列自动解释为分类语义，也不把数值存储证明为连续测量。内部沿用的 `continuous_tabular_dataset` 能力前提目前只依据数值列画像形成，方法说明单独表达这个边界；训练机制覆盖及当前分布适用性仍是需要证据支持的条件。

[共享结构检查](../../Agent/deep_agent_tools/graph_validation.py) 在标准化与 FinalizationGate 两处执行，检查图类型、边型、自环、重复边、普通有向子图无环性及 CDFM 的 bow-free 约束。无向边和双箭头不进入普通有向环检查；PC 的无向闭合路径不因此失败。违反约束时保留原始图和 raw artifact，将结果记为不可用，禁止成为主结果，不自动删边。`graph_validation.checks_performed` 与 `not_checked` 分别记录结构检查和未检验的统计、识别与边准确性事项。报告资产、前端图模型和上下文文字分别保留 `→`、`—`、`↔`。

`column_profiles` 将三类信息分开保存：

| 字段 | 内容 | 使用边界 |
|---|---|---|
| `storage_type` | 实际 pandas dtype | 是否能作为数值输入 |
| `statistical_features` | 整数性、非负性、基数、基数占比、低频、非有限值 | 统计提示，不决定测量含义 |
| `measurement_semantics` | kind、source、status、候选含义及证据 | CSV 推断保持 unknown/uncertain；研究需求工具提交测量声明，独立校验后记录 validated_research_spec/user_stated，不等同额外确认 |

`profile_thresholds` 明确：低基数为不同值数不超过 10；低频为取值频数不超过 5 或占非缺失值比例不超过 1%；高缺失为缺失比例大于 30%。低基数与低频独立计算，可以同时成立，不合并低频数值，不由整数和高唯一比推断标识符。画像不修改原数据。非线性检测依据实际数值 dtype、有限观测和至少 10 个不同值选择变量，不执行时提供原因；并列数值用平均秩分箱。图表对低基数数值使用按数值顺序排列的频数柱图，对其他数值使用直方图；字符串类别单独展示。

两套运行入口从 [analysis_principles.py](../../Agent/analysis_principles.py) 引用通用原则。专用提示词继续分别规定工具执行、数据处理、检索与报告契约。结构发现不需要处理变量或结果变量；效应量解释仅适用于实际完成的效应识别和估计，变量名称、整数性与偏态不能自行证明混杂或忠实性违反。

## 结构化终态与报告

报告节点接收完整父 State，再由 [build_report_analysis_context()](../../Agent/causal_agent/analysis_context.py) 唯一投影为 `analysis-facts-v3`。只有 `finalization_status=valid` 且存在 `deep_agent_accepted_decision` 时才投影最终选择，不从原始 `structured_response` 或 `deep_agent_decision` 回填。事实包含各结果的标准图、状态、方法与实现、输入视图、必要诊断、逐结果身份，以及接受决策中的 `result_assessments`、`selection_rationale`、`conflicts`、`confidence_basis`、独立主图和主效应。`edge_counts` 从标准图计算总边数及各边型数量，双箭头只计一条边。比较输入视图时同时检查行数、列顺序、处理操作、处理摘要及被删行、插补和编码列；runner 内部标准化和缺失策略另列。原始 CSV、原始大数组、完整 Action Ledger、文件身份和运行控制字段不进入该报告上下文。

主图只来自 accepted 决策的 `primary_graph_result_ref`，主效应单独来自 `primary_effect_result_ref`；不能按兼容 `primary_result_ref`、结果数量或引用排序猜测。后处理修订建议不替换算法原图，无合格主图时说明原因，不从旧上下文填补失败。报告最多一个因果图块，有主图资源时必须展示一次。新分析将完整事实嵌入 `latest_algorithm_summary.analysis_facts` JSON；无主图时也保存空选择和本次事实，避免旧图残留。重新生成只读取对应的 `analysis-facts-v3` 快照及既有证据，不重新挑选结果；无法安全复用的快照要求重新分析，历史记录保留。缺少效应请求的历史事实标记 `not_recorded`，不能解释为未请求。

`graph_comparisons` 由有效标准图逐变量对计算，区分同一边型与方向、方向相反、边型不同、仅左侧存在和仅右侧存在，保留各侧实际端点。`left_algorithm`、`right_algorithm` 固定左右身份，`relation_counts` 直接计算各类关系数量，避免模型重新计数或按主辅身份猜测左右。它只记录本次候选图差异，不合并结果、不裁定真实方向。`decision.conflicts` 与取舍理由保留为模型评估文本；其中的自然语言若与实际边表冲突，报告以计算比较为准，指出不能由边表支持的表述，不能将另一侧缺边写成方向相反。

Deep Agent 使用 `ToolStrategy(FinalAnalysisDecision)` 生成 `structured_response`。`FinalizationGate` 不调用 LLM，只校验：

- 决策引用存在于当前可用结果或 evidence 集合中；
- AlgorithmResult 与 Action Ledger 的 invocation、终态和结果引用一致；
- provenance 属于当前 Job、attempt、lease、worker、冻结输入及研究身份；可信恢复结果按原始 provenance 核验。
- 每个有效算法结果都有唯一取舍，独立主图、主效应及效应状态满足 outcome、来源和定义约束。
- 每个已登记算法调用都已有一致的终态结果；`pending`、`queued/running` 或缺少结果时禁止进入报告。
- 分析运行（父图 `route_decision=data_processing`，含 `context_switch` 之后的数据处理路径）在没有任何算法结果时，不得提交 `evidence_only` 或 `no_valid_algorithm`。
- `web_search_enabled=true` 时检查当前分析是否有 `web_evidence_search` terminal 调用；零调用在报告入口记录为缺少外部证据核对，不因此隐藏已有可信图。真实返回无结果/不可用时保留实际终态。RAG 是否调用由 Agent 自主决策。

进入 Deep Agent 的运行都是分析运行：`agent` 的 `start_analysis`/`rerun_analysis` 直接路由到 `data_processing`，`context_switch` 也会把 `route_decision` 改写成后续节点名。因此父图投影时按运行追加“必须至少调用一个算法工具”这一条系统约束。Web 调用要求写在固定系统提示中，按本次实际提供的工具是否包含 `web_evidence_search` 生效；可见性由可信运行上下文决定。RAG 不追加强制调用约束。

最终决策的两套引用命名空间不混用：`result_assessments`、`primary_graph_result_ref`、`primary_effect_result_ref`、兼容 `primary_result_ref`、`conflicts.result_refs` 与 `revision_proposals.result_ref` 只接受当前分析认可的算法结果引用；RAG/Web 证据引用只能出现在 `revision_proposals.evidence_refs`。引用分工和按可见工具生效的 Web 调用要求写在固定系统提示中，Gate 继续按可信开关和恢复身份检查真实记录。只有已请求的效应才产生诊断与估计要求；父图通过科学限制收集器保存这些检查或联网核对的缺口。RAG 是否检索由 Agent 自主判断。

模型可修正的决策校验第一次失败时，父图把失败映射为稳定的规则码，再生成一条脱敏修正指令（包含被违反的具体引用规则）交回同一个 Deep Agent 子图；第二次仍失败则设置 `finalization_status=degraded` 并生成安全报告。身份、账本或状态一致性问题不带规则码，直接终止分析，不进入模型修正或报告降级。可修正的 Gate 拒绝会发布 `progress` 阶段说明：首次挂在 `finalization_gate` 阶段并给出同一份修正要求，降级时说明本次仅基于已验证输入生成报告；第二次 Deep Agent 启动修正时，新阶段同样收到一条 `progress` 说明，指出该阶段复用已有结果，并补齐校验要求的步骤。`degraded` 不公开未经验证的主图或最终选择，但报告成功时 Job 仍以 `succeeded` 收敛。校验通过时设置 `finalization_status=valid`，并把内部结果引用转换为公开算法名称后发布最终决策说明。已完整记录的算法超时不阻止使用其他有效结果；报告装配根据执行摘要固定追加超时算法的分析限制说明。

报告节点让模型只产出 `ReportDraft`：报告标题、块结构、Markdown 文本和资源/证据引用。图表资源、因果图模型、来源和证据全部由后端注入并校验，块 ID 重复、块类型未知、`asset_key` 不存在或类型不匹配、`evidence_id` 不存在都会进入节点受控错误路径并生成降级报告文档，不保存部分报告。来源类别按证据通道（输入文件、知识库、联网检索）显式给出，不根据是否存在 URL 推断。报告装配还会把正文中出现且属于本次证据清单的 `ev_...` ID 回填到对应 `markdown.evidence_refs`，兼容模型漏填结构化字段；前端来源页脚据此提供“定位正文”。父 State 用 `chart_assets` 保存已绑定分析数据集的分析准备阶段生成的结构化图表资源，用 `report_document` 保存后端装配完成的结构化报告文档；报告追问只使用文档摘要、资源说明、来源说明和证据说明，不把图表数据、因果图模型或完整文档塞进提示词。新报告不再依赖 `visualization_mapping`、Base64 图片、HTML 图片标签和 `[[CHART:...]]` 占位符。

报告入口的 `finalization_status=valid` 表示决策及来源完整性通过，不表示科学假设均已验证。存在 `analysis_limitations` 或预算耗尽时，后端固定在正文开头写入“分析范围与限制”，图标题使用“候选因果结构”；适用时写入 OLS 估计及区间依赖线性模型的条件说明。已有 accepted 主图资源 `graph_main` 但草稿遗漏图块时，后端补入唯一主图块。限制、预算状态、原始数值与图资源进入报告文档和 `analysis-facts-v3` 快照，历史读取、追问与重新生成保留同一事实；没有记录的内容不推测补写。

算法 Tool 的模型可见参数允许附带 `public_decision.summary`。middleware 在科学参数校验和执行前剥离该字段；有效说明写入 `decision_kind=algorithm` 的公共事件，缺失或格式无效不会阻止算法执行。`rag_evidence_search` 与 `web_evidence_search` 使用同一 envelope，并在外部检索前剥离该字段，写入 `decision_kind=evidence` 的公共事件。两类工具说明都不进入 Adapter、MCP、检索器入参。普通 assistant content、ToolMessage、工具参数、完整工具结果、provider ID、result reference 和隐藏推理都不会因此公开。

## 公共事件与恢复

worker 使用 LangGraph v2 的 `updates`、`messages`、`custom` 和 `tasks` 流。父图以 `deep_agent.astream(..., stream_mode=["messages", "values", "custom"], subgraphs=True, version="v2", durability="sync")` 消费内层模型增量并保留完整 State，再通过父图 custom 流交给公共适配器；适配器只提取工具参数中已确认的 `public_decision.summary`，转换为 `decision_delta`，不外带原始参数或隐藏内容。子图 custom 只转发白名单内的工具开始和结果字段。算法、RAG、Web 工具在外部调用前后通过 `OrderedEventWriter` sink 写入 `tool_call_start`/`tool_call_result`；直接 sink、custom 转发和聚合兜底共用由 invocation、执行 attempt、retry ordinal 和事件类型生成的稳定内部事件键。聚合兜底必须从结果与终态账本恢复一致身份，不能按工具名合成终态或去重。算法结果中的 `valid` 在公共事件边界转换为 `succeeded/调用完成`，超时、取消、不适用与跳过保持各自状态。内层 ToolMessage 和内部事件身份不进入普通用户事件。

公共 payload 由 `event_adapter.py` 与 `public_events.py` 的白名单共同约束。`tool_call_result.status` 只允许受控状态；`safe_error_code` 必须符合固定格式。`finalization_status` 只出现在最终报告数据。事件写入由 Job、attempt、lease、worker 和稳定 `event_key` 保护；失去 lease 或收到取消的 worker 不能提交迟到结果。

独立数据集校验的进度事件键包含 Job attempt、候选数据集 ID 和 LangGraph task ID。同一候选在修正循环中再次进入校验节点时，各任务使用独立事件键及阶段 ID；同一 attempt 内的同一任务重放保持原键和载荷。数据库继续拒绝同键不同载荷，内部事件键和 task ID 不进入公开 SSE。

graph 终态失败的内部诊断由 `classify_graph_failure()` 生成：从外向内检查异常类名及基类、白名单 `safe_cause_code`，再沿包装对象的 `error`、显式 `__cause__` 或未被抑制的 `__context__` 查找原因；只遍历真实异常原因，按对象身份去重，最多检查 32 个对象。外层已有明确分类时保留该分类；结构化输出的供应商失败归入 `provider_error`，工具调用、schema、JSON、输出解析和截断失败归入 `protocol_error`；未识别时保留 `internal_error/node_error`。分类不读取异常正文，`exc_info` 始终保留传入异常及其原始 traceback；诊断只用于 worker 日志，不进入公共事件或数据库事件 payload。

SSE 与会话历史都从 MySQL `analysis_job_events` 恢复。页面刷新先重放持久化事件并记录实际处理到的 Event ID，再从该位置续传；前端可以暂存早于父 `node_start` 到达的工具明细，并在相同 `step_id` 出现后补绘。`decision_delta` 与 `text_delta` 都按各自 `stream_id` 和批次序号增量更新；完整 `decision` 作为公开决策的回退/历史投影，实时页面不会再次复制已经完成的决策增量。普通问答和报告追问的正文通过 `text_delta` 实时渲染，历史回放直接使用已持久化的完整正文；结构化报告不走文字增量，而是在 `final_result` 一次性交付完整报告文档，并以 `report_document` 附件与消息在同一事务落库，刷新后由会话历史接口恢复成同一份载荷。展示速度不影响工具执行，也不产生逐字符数据库事件。

节点错误处理器的降级结果由 worker 负责收敛：LangGraph 在处理器提交降级结果之后，仍会把原任务异常抛给 `astream`。worker 因此读取 updates 流中带 `__error_handler__` 前缀的结果，并在确认图状态已经收敛（没有待执行节点、没有 pending interrupt）时按正常终态继续收尾；缺少任一条件时保持原有失败路径。节点级降级日志 `job.node.degraded` 额外记录 `cause_code`，把结构化输出失败按底层异常类名归类为 tool_call_invalid、schema_invalid、json_invalid、output_parser_error、truncated、timeout、connection_error、rate_limited、request_rejected、provider_error 或 unknown，不记录异常正文；其中 tool_call_invalid 表示模型没有返回任何可解析的工具调用，不再被误报成 schema_invalid。统一入口 `Agent/llm_structured_output.py` 只对「模型本次产出的结构化结果不可用」这一类失败重试一次，第二次调用前重新确认 Job 执行资格，已撤销时直接抛出撤销控制流；模型自身异常、超时、限流、连接和供应商错误不在入口内重试。同一事件还记录 `schema_name`、`structured_attempts`、`validation_error_count`、`validation_first_type` 和 `validation_first_loc`，字段路径只保留 schema 字段名，额外字段统一写成 extra，不记录模型取值、提示词或异常正文。报告节点改用非流式模型生成报告草稿：结构化草稿的模型正文恒为空，流式只增加工具参数拼接的失败面；该节点的空闲超时相应放宽到 120 秒，以容纳只在调用开始和结束时刷新计时的非流式长响应。

## 修改与验证边界

- 修改父图或 Deep Agent 时，必须核对显式 State 投影、child execution scope、Gate 修正和 degraded 路径。
- 修改算法工具时，必须同步检查默认 Spec allowlist、Registry、Adapter、MCP runner registry、schema 快照和公共名称映射。
- 修改 worker 初始化时，必须同时检查 PostgreSQL Store/checkpointer、MCP execute/control lane、RAG readiness、Compose 环境和 slot 资源占用。
- 修改事件或结果展示时，必须验证实时 SSE 与历史回放使用同一白名单，且 payload 不含内部标识或原始工具数据。
- 修改分析上下文、意图路由或上下文切换时，必须同时核对 `Database/analysis_contexts.py` 的事务边界与 fencing 校验、MCP 输入摘要与 Deep Agent execution scope 的一致性，以及 `Document/architecture/job-file-lifecycle.md` 的冻结输入语义。
- 单元测试和 graph 构造只证明代码合同；真实 DeepSeek、MCP HTTP、PostgreSQL、RAG/SearXNG 与完整 Job 的验证入口和证据边界见 [`../development/testing.md`](../development/testing.md)。
