# Agent 运行时

文档职责：记录 Agent worker、Deep Agent、LangGraph、MCP、RAG、联网搜索、结构化输出和公共执行事件的当前协作方式。

适用范围：修改 `Agent/`、`app/agent/worker/`、`Agent/CausalAgentMCP/`、RAG 初始化、联网搜索或用户可见事件协议时使用；Job、checkpoint 与文件生命周期见 [`job-file-lifecycle.md`](job-file-lifecycle.md)，执行约束见 [`../../Agent/AGENTS.md`](../../Agent/AGENTS.md)。

## Worker 启动与运行时边界

`python -m app.agent.worker` 进入 worker bootstrap。生产路径在领取 Job 前按以下顺序初始化：

```text
MySQL readiness
  → PostgreSQL checkpoint pool/schema
  → AsyncPostgresStore setup
  → MCP Client pool connect/handshake
  → 静态 Algorithm Registry 与领域工具
  → RAG active release readiness
  → Deep Agent 与父图编译
  → worker ready
  → slots claim Job
```

`ProcessRuntime` 持有进程级 MCP Client pool、`McpAlgorithmExecutor`、静态 Registry、PostgreSQL Store/checkpointer、RAG readiness、领域工具和编译后的父图。`SlotRuntime` 只引用这些共享对象，不为每个 slot 重建 graph 或创建 stdio MCP session。每次 Job invocation 再创建 `AgentRunContext`，绑定可信 Job、attempt、lease、输入身份、execution guard、executor、filesystem backend、RAG 状态、联网搜索开关和事件 sink；这些运行时对象不进入 State 或 checkpoint。

进程级 MCP Client pool 与 worker slot 数量相互独立。默认 execute lane 为 `2 × 1`，control lane 为 `1 × 2`；控制 lane 只负责取消，不增加服务端算法并发。启动任一步失败都会在 `worker.startup.ready` 前终止进程，不领取 Job。

`runtime.py` 仍保留 `ProcessRuntime.graph is None` 时的旧 stdio 构造分支，供兼容测试使用。`main_async()` 的生产初始化会装配父图，因此不会进入该分支。

## 父图、子图与持久化

数据分析主路径为：

```text
agent → fold → preprocess → deep_agent → finalization_gate → report
```

父图直接表达的其余路径为 `agent → report`（按当前分析上下文重新生成报告）、`agent → context_switch` 与 `context_switch → fold / report / inquiry_answer / normal_chat`（解析并切换分析上下文后再继续）；普通聊天和报告追问仍由 `normal_chat`、`inquiry_answer` 路径结束。父图只把问题、数据画像、分析参数和必要输入消息投影到 Deep Agent；子图返回时只接收算法结果、Action Ledger、RAG/Web evidence、结构化决策和 Gate/report 所需摘要。完整消息、Deep Agents 内部字段和中间计划不会回投影到父 State。

父图 checkpoint 使用 `thread_id=analysis_jobs.job_id` 和空 namespace。Deep Agent 子图使用同一个 PostgreSQL saver，但使用稳定的 `thread_id=deep-agent:<uuid5(job_id)>` 与 `checkpoint_ns=deep_agent_v1`。子图 State 另外保存由 Job、attempt、lease 和当前冻结输入身份构成的 execution scope；scope 不匹配时从父图的最小输入重新开始，不复用旧执行产物。输入身份读取的是 invocation 级可刷新引用，因此服务端按用户表述重新冻结输入或切换分析上下文后，scope 随之变化，旧执行产物不会被复用。Gate 要求修正时，从同一子图 checkpoint 追加受控指令。

长期记忆由 `AsyncPostgresStore` 单独保存，不属于 Job checkpoint。模型只注册 `read_file` 和 `edit_file`，并且只允许写 `/memories/preferences.md` 与 `/memories/research_background.md`；其他写入由兜底 deny 拒绝。namespace 从可信 `AgentRunContext` 的 `user_id` 生成，首次使用时仅做 create-if-absent 初始化。Deep Agent 不启用 subagent、`task`、代码执行或宿主文件系统。

当前 cleanup outbox 只按 `job_id` 删除父图 thread，尚未删除独立的 Deep Agent child thread；这一实现缺口及其影响见 [`job-file-lifecycle.md`](job-file-lifecycle.md)。

## 分析上下文与追问路由

`Session`、`AnalysisContext`、`Job` 和 checkpoint 各管一段事实：Session 是用户可见的会话容器；AnalysisContext 是 MySQL 中的跨 Job 业务事实，保存创建时的文件快照、分析参数、最新结构化算法摘要、RAG/Web 证据摘要和最新报告引用；Job 是一次具体执行并绑定一个上下文；checkpoint 只负责当前 Job 的执行恢复、中间 State 和虚拟文件。一个 Session 可以拥有多个 AnalysisContext，`sessions.active_analysis_context_id` 只表示当前默认上下文。

`agent_node` 用一次结构化调用判断意图（`normal_chat`、`start_analysis`、`answer_report`、`revise_report`、`rerun_analysis`、`switch_analysis_context`、`clarify`），模型只输出意图、`context_hint` 和澄清问题；`route_decision` 由后端按固定映射生成，模型不能输出分析上下文 ID 或用户文件 ID，也不能直接选择图节点。节点输入是当前消息、最近有限聊天历史、当前 AnalysisContext 摘要和同一 Session 的历史上下文索引；索引只含文件名、目标变量、处理变量、报告标题、更新时间和简短摘要，提示词里不出现内部 ID。没有可引用报告时 `answer_report` 与 `revise_report` 回退到 `normal_chat`，结构化输出失败同样回退到 `normal_chat`。

新增的 `context_switch` 节点把“回到某个文件或某次历史分析”解析成真实上下文：按用户表述匹配历史索引，唯一命中才切换；歧义、文件缺失或无匹配都返回澄清问题并保持 active 指针不变；用户点名的是文件库里其他文件时，节点在同一事务里为该文件新建上下文。切换事务同时更新 Session 默认指针、当前 Job 和当前输入账本的上下文绑定，并校验 worker、attempt 和 lease epoch，重复执行得到同一结果。

切换会改变本次 Job 的冻结输入：服务端在同一事务里重写 `analysis_jobs` 的冻结文件快照，并通过 invocation 级引用让 MCP 输入摘要、Adapter 校验、`FinalizationGate` 的 provenance 校验和 Deep Agent 子图 execution scope 一起跟随。旧上下文的算法结果、证据和因果图不会进入新的父图 State。

`report_node` 有两种模式：`normal_generation` 按当前分析结果首次生成报告；`full_regeneration_from_context` 只使用当前 AnalysisContext 已确认的事实重新生成完整报告，不修改算法图，也不新增上下文中没有的因果结论。`inquiry_answer_node` 只回答当前报告或上下文事实相关的问题，或原样转达后端给出的澄清问题，既不决定是否重新执行算法，也不修改报告。

## 算法工具与 causal-mcp

模型看到的是由本地 `AlgorithmSpec` 生成的 LangChain function tools。当前默认 allowlist 包含 PC、DirectLiNGAM 和 CDFM；OLC 的 Spec、Adapter、runner 和算法代码仍保留，但不进入 worker Registry、MCP runner registry 或旧兼容 MCP 工具面。运行时不会调用远端 `list_tools()` 来动态扩展模型工具。CDFM 工具只公开可选 `threshold`，模型路径、CPU device、标准化和缺失值处理策略由 causal-mcp 固定。

`AlgorithmDependencyDispatchMiddleware` 在 ToolNode 边界收集同一模型响应中的算法调用，按 `requires/produces` 分层：无依赖调用可并行，有依赖调用按层执行，并受单 Job 并发和工具总预算约束。Adapter 负责模型参数校验、确定性预处理、标准结果、raw result 完整性和 Ledger 更新；`McpAlgorithmExecutor` 只负责签名传输、远端执行结果校验和安全错误映射。CDFM 的 logits/probabilities 通过仅供 Executor 与 Adapter 使用的私有 envelope 保存到 raw artifact，不进入 `AlgorithmResult`、ToolMessage、SSE 或公共日志。可信 Job、文件、lease、HMAC 与数据库身份不会进入模型参数。

`causal-mcp` 是私网 Streamable HTTP/HTTP/1.1 服务。服务端以 MySQL primary strong read 校验 Job、attempt、lease 和 worker 身份，再使用有界队列及独立算法子进程运行 capability。MCP session 不保存 Job、checkpoint 或 Action Ledger。

CDFM v0.1 在 runner 内保持 `directed_graph` 和输入列顺序，按 `adjacency[i,j]` 生成 `column_i → column_j`，不做 `remove_cycles()`；当前生产图也不因此承诺自动环路修复。它的预训练数据包含潜在混杂，对应真值形式是双向边，因此模型给出对称邻接时会被转成两条方向相反的边，这种形状不能直接读成互反因果。该接入只证明工程调用链和私有结果保存，不证明因果发现准确率。

当 heartbeat 或取消使 `JobExecutionGuard` 撤销时，worker 中断本地等待，并通过独立 control lane 发送签名 `cancel_algorithm`。服务端按完整 invocation 身份取消排队任务或终止目标算法进程，不影响并行 sibling。重复取消返回稳定状态；客户端无法确认最终状态时使用 `unknown`，该值不能解释为远端一定未取消。取消属于控制流，不生成普通失败的 `AlgorithmResult`，也不进入算法重试或 FinalizationGate。

客户端池、服务端执行池、容量、重连和取消状态机的详细说明见 [`mcp-runtime.md`](mcp-runtime.md)。

## RAG 与 Web evidence

`rag_evidence_search` 调用 `RagService.get_evidence()`，不调用旧 RAG answer model。知识库证据工具由 Agent 根据当前问题自主判断是否调用；未调用不构成终态校验失败，实际调用后仍必须保留真实的 terminal Action Ledger 状态，不能伪造证据或结果。worker 启动只校验 active release，不加载 Chroma、BM25 或 embedding；第一次真实查询时才在进程内初始化 RAG runtime。结果以受控 evidence reference、snippet、来源定位、分数、release 和状态写入子图 State，再投影为 report formatter 使用的引用摘要。知识库证据的来源展示名由 release manifest 的 `document_id → relative_path` 解析，manifest 未覆盖时才回退到检索块元数据；`asset_uri` 是内部资源路径，不充当 URL。RAG 的排名编号和 Web 的相关性分数都属于单次查询，因此写入 State 和 ToolMessage 前会用稳定 invocation id 限定 evidence reference；同一调用重放保持幂等，不同并行查询即使都返回 `E1` 或同一来源也不会占用同一个 reducer key。

`web_evidence_search` 返回 SearXNG 学术结果的 snippet 与来源元数据，不抓取网页正文。每次调用读取 `AgentRunContext.web_search_enabled`；关闭时返回 `WEB_SEARCH_DISABLED` 且不触网。分析运行在 `web_search_enabled=true` 时按运行注入“至少调用一次 Web evidence”约束，并由 `FinalizationGate` 校验当前 Job attempt 的 terminal Action Ledger；未开启时不强制联网。RAG/Web 的异常只转换为受控状态和安全错误码，不把异常正文、查询参数或 provider 数据带入公共事件。

旧 `build_graph()` 的固定 `mcp → rag → web_search` 子图只作为兼容路径保留，不是 worker 当前生产入口。

## 结构化终态与报告

Deep Agent 使用 `ToolStrategy(FinalAnalysisDecision)` 生成 `structured_response`。`FinalizationGate` 不调用 LLM，只校验：

- 决策引用存在于当前可用结果或 evidence 集合中；
- AlgorithmResult 与 Action Ledger 的 invocation、终态和结果引用一致；
- provenance 属于当前 Job、attempt、lease、worker 与冻结输入；
- 每个有效算法结果都有唯一取舍，主结果满足 outcome 约束。
- 分析运行（父图 `route_decision=fold`，含 `context_switch` 之后的 fold）在没有任何算法结果时，不得提交 `evidence_only` 或 `no_valid_algorithm`。
- `web_search_enabled=true` 时，分析运行必须有一次 `web_evidence_search` terminal 调用；允许真实返回无结果/不可用状态，但不能零调用完成。RAG 是否调用由 Agent 自主决策。

进入 Deep Agent 的运行都是分析运行：`agent` 的 `start_analysis`/`rerun_analysis` 直接路由到 `fold`，`context_switch` 也会把 `route_decision` 改写成后续节点名。因此父图投影时按运行追加“必须至少调用一个算法工具”这一条系统约束；当本 Job 开启联网搜索时再追加“必须至少调用一次 `web_evidence_search`”，RAG 不追加强制调用约束，所有约束都不写进 worker 级系统提示词。

最终决策的两套引用命名空间不混用：`result_assessments`、`primary_result_ref`、`conflicts.result_refs` 与 `revision_proposals.result_ref` 只接受本次运行返回的算法结果引用；RAG/Web 证据引用只能出现在 `revision_proposals.evidence_refs`。该分工以及“开启联网搜索时必须实际完成一次 Web 检索”的要求同时写在按运行提示和 Gate 校验中，RAG 是否检索由 Agent 自主判断，避免模型把“证据不采用”写进算法结果取舍。

第一次校验失败时，父图把失败映射为稳定的规则码，再生成一条脱敏修正指令（包含被违反的具体引用规则）交回同一个 Deep Agent 子图；第二次仍失败则设置 `finalization_status=degraded` 并生成安全报告。身份、账本或状态一致性问题不带规则码，修正指令保持通用措辞，不把内部问题包装成模型可修正的指令。每次 Gate 拒绝都会发布一条 `progress` 阶段说明：可修正时挂在 `finalization_gate` 阶段并给出同一份修正要求，降级时说明本次仅基于已验证输入生成报告；第二次 Deep Agent 启动修正时，新阶段同样收到一条 `progress` 说明，指出该阶段沿用已有工具结果、不重复调用工具。`degraded` 不公开未经验证的主图或最终选择，但报告成功时 Job 仍以 `succeeded` 收敛。校验通过时设置 `finalization_status=valid`，并把内部结果引用转换为公开算法名称后发布最终决策说明。

报告节点让模型只产出 `ReportDraft`：报告标题、块结构、Markdown 文本和资源/证据引用。图表资源、因果图模型、来源和证据全部由后端注入并校验，块 ID 重复、块类型未知、`asset_key` 不存在或类型不匹配、`evidence_id` 不存在都会进入节点受控错误路径并生成降级报告文档，不保存部分报告。来源类别按证据通道（输入文件、知识库、联网检索）显式给出，不根据是否存在 URL 推断。报告装配还会把正文中出现且属于本次证据清单的 `ev_...` ID 回填到对应 `markdown.evidence_refs`，兼容模型漏填结构化字段；前端来源页脚据此提供“定位正文”。父 State 用 `chart_assets` 保存预处理阶段生成的结构化图表资源，用 `report_document` 保存后端装配完成的结构化报告文档；报告追问只使用文档摘要、资源说明、来源说明和证据说明，不把图表数据、因果图模型或完整文档塞进提示词。新报告不再依赖 `visualization_mapping`、Base64 图片、HTML 图片标签和 `[[CHART:...]]` 占位符。

算法 Tool 的模型可见参数允许附带 `public_decision.summary`。middleware 在科学参数校验和执行前剥离该字段；有效说明写入 `decision_kind=algorithm` 的公共事件，缺失或格式无效不会阻止算法执行。`rag_evidence_search` 与 `web_evidence_search` 使用同一 envelope，并在外部检索前剥离该字段，写入 `decision_kind=evidence` 的公共事件。两类工具说明都不进入 Adapter、MCP、检索器入参。普通 assistant content、ToolMessage、工具参数、完整工具结果、provider ID、result reference 和隐藏推理都不会因此公开。

## 公共事件与恢复

worker 使用 LangGraph v2 的 `updates`、`messages`、`custom` 和 `tasks` 流。父图以 `deep_agent.astream(..., stream_mode=["messages", "values"], subgraphs=True, version="v2")` 消费内层模型增量并保留完整 State，再通过父图 custom 流交给公共适配器；适配器只提取工具参数中已确认的 `public_decision.summary`，转换为 `decision_delta`，不外带原始参数或隐藏内容。算法、RAG、Web 工具在外部调用前后通过 `OrderedEventWriter` sink 写入 `tool_call_start`/`tool_call_result`；父 `deep_agent` update 只作为聚合结果的兼容去重兜底。内层 ToolMessage 不进入普通用户事件。

公共 payload 由 `event_adapter.py` 与 `public_events.py` 的白名单共同约束。`tool_call_result.status` 只允许受控状态；`safe_error_code` 必须符合固定格式。`finalization_status` 只出现在最终报告数据。事件写入由 Job、attempt、lease、worker 和稳定 `event_key` 保护；失去 lease 或收到取消的 worker 不能提交迟到结果。

SSE 与会话历史都从 MySQL `analysis_job_events` 恢复。页面刷新先重放持久化事件并记录实际处理到的 Event ID，再从该位置续传；前端可以暂存早于父 `node_start` 到达的工具明细，并在相同 `step_id` 出现后补绘。`decision_delta` 与 `text_delta` 都按各自 `stream_id` 和批次序号增量更新；完整 `decision` 作为公开决策的回退/历史投影，实时页面不会再次复制已经完成的决策增量。普通问答和报告追问的正文通过 `text_delta` 实时渲染，历史回放直接使用已持久化的完整正文；结构化报告不走文字增量，而是在 `final_result` 一次性交付完整报告文档，并以 `report_document` 附件与消息在同一事务落库，刷新后由会话历史接口恢复成同一份载荷。展示速度不影响工具执行，也不产生逐字符数据库事件。

节点错误处理器的降级结果由 worker 负责收敛：LangGraph 在处理器提交降级结果之后，仍会把原任务异常抛给 `astream`。worker 因此读取 updates 流中带 `__error_handler__` 前缀的结果，并在确认图状态已经收敛（没有待执行节点、没有 pending interrupt）时按正常终态继续收尾；缺少任一条件时保持原有失败路径。节点级降级日志 `job.node.degraded` 额外记录 `cause_code`，把结构化输出失败按底层异常类名归类为 tool_call_invalid、schema_invalid、json_invalid、output_parser_error、truncated、timeout、connection_error、rate_limited、request_rejected、provider_error 或 unknown，不记录异常正文；其中 tool_call_invalid 表示模型没有返回任何可解析的工具调用，不再被误报成 schema_invalid。统一入口 `Agent/llm_structured_output.py` 只对「模型本次产出的结构化结果不可用」这一类失败重试一次，第二次调用前重新确认 Job 执行资格，已撤销时直接抛出撤销控制流；模型自身异常、超时、限流、连接和供应商错误不在入口内重试。同一事件还记录 `schema_name`、`structured_attempts`、`validation_error_count`、`validation_first_type` 和 `validation_first_loc`，字段路径只保留 schema 字段名，额外字段统一写成 extra，不记录模型取值、提示词或异常正文。报告节点改用非流式模型生成报告草稿：结构化草稿的模型正文恒为空，流式只增加工具参数拼接的失败面；该节点的空闲超时相应放宽到 120 秒，以容纳只在调用开始和结束时刷新计时的非流式长响应。

## 修改与验证边界

- 修改父图或 Deep Agent 时，必须核对显式 State 投影、child execution scope、Gate 修正和 degraded 路径。
- 修改算法工具时，必须同步检查默认 Spec allowlist、Registry、Adapter、MCP runner registry、schema 快照和公共名称映射。
- 修改 worker 初始化时，必须同时检查 PostgreSQL Store/checkpointer、MCP execute/control lane、RAG readiness、Compose 环境和 slot 资源占用。
- 修改事件或结果展示时，必须验证实时 SSE 与历史回放使用同一白名单，且 payload 不含内部标识或原始工具数据。
- 修改分析上下文、意图路由或上下文切换时，必须同时核对 `Database/analysis_contexts.py` 的事务边界与 fencing 校验、MCP 输入摘要与 Deep Agent execution scope 的一致性，以及 `Document/architecture/job-file-lifecycle.md` 的冻结输入语义。
- 单元测试和 graph 构造只证明代码合同；真实 DeepSeek、MCP HTTP、PostgreSQL、RAG/SearXNG 与完整 Job 的验证入口和证据边界见 [`../development/testing.md`](../development/testing.md)。
