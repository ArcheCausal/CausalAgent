# Deep Agent 混合架构接入规划

文档职责：记录在现有 CausalAgent LangGraph 运行时中接入 Deep Agents 的产品语义、架构决策、能力边界、迁移阶段与验收门槛；本文是评审用规划，不代表当前代码已经实现。

适用范围：面向 CausalAgent 的产品、Agent、MCP、RAG、Web Search、Job worker、checkpoint、Human-in-the-loop、公开事件与长期记忆改造；当前运行事实仍以 [`agent-runtime.md`](agent-runtime.md) 和代码为准。

> 状态：**规划中，尚未实施**。本文冻结截至 2026-09-14 已讨论并确认的方向。文中的“当前”来自仓库核对，“目标”是已接受的设计，“待验证”必须通过隔离 Spike 或真实集成测试后才能成为实现事实。

可编辑架构图：[`deep-agent-integration-plan.drawio`](deep-agent-integration-plan.drawio)。

## 1. 结论摘要

首版不把系统改造成 Supervisor + 多个 Specialist 的多 Agent 平台，而采用“**外层确定性 LangGraph 安全壳 + 内层单个 Deep Agent 决策循环 + Spec 生成的共享工具面**”的混合架构。

现有 `agent` 节点继续作为统一入口，并通过提示词与结构化 `route_decision` 判断普通问答、报告追问和因果分析路径，不在它之前新增一层确定性意图分类器。外层 LangGraph 继续负责文件与身份边界、Job/lease/fencing/cancel、checkpoint、确定性数据准入、现有 `fold` Human-in-the-loop 和最终 `FinalizationGate`；Deep Agent 负责在需要时选择一个或多个因果算法或证据 Tool、观察标准化结果、重跑或切换算法、处理冲突、提出可审计但不改图的科学建议，并通过 `response_format=ToolStrategy(FinalAnalysisDecision)` 产生终态 `structured_response`。领域 Tool 调用是可选能力，不是 Deep Agent 的完成前置条件。

这项改造的目的不是“使用更多 Agent”，而是解除当前 `MCP → RAG → Web Search` 固定拓扑造成的业务僵化，同时保留因果分析所需的严谨性。首版的扩展性主要来自 **AlgorithmSpec 单一事实源、稳定 Tool/Adapter 契约、直接依赖排序、结构化最终提交和事件协议**，不是来自预先创建多个子 Agent 或动态 Tool Subgraph。

关于 MCP，建议采用“**独立部署，但不独立持有业务会话**”的方案：把因果算法执行面拆成应用内私有 `causal-mcp` 容器，以 Streamable HTTP 接受 worker 调用并使用有界进程池执行算法；Job、用户、文件、lease、checkpoint 和 Action Ledger 仍由现有应用控制面持有。这样既能隔离算法依赖和 CPU 资源，也不会把 MCP session 演变为第二套状态机。所谓统一 MCP 池应理解为统一算法执行资源池，而不是每个 Job 必须绑定一个长期 session 的 session 池。

| 议题 | 已确认方向 |
| --- | --- |
| 产品边界 | 仍是因果分析助手，不扩张为通用数据 Agent |
| 入口路由 | 保留现有 `agent` 节点负责判断；以提示词和结构化路由结果驱动，不新增前置意图分类节点 |
| 分析完成条件 | Deep Agent 产生通过结构化输出校验的 `FinalAnalysisDecision`，终态决策、Action Ledger 与有效结果通过 `FinalizationGate` 一致性校验，并据此正常生成诚实报告；不要求因果算法必定成功或必定被调用 |
| 编排方式 | 外层 LangGraph 保证控制与安全；内层单 Deep Agent 可自由选择一个或多个 Tool、重跑、切换、比较和停止 |
| 算法扩展 | `AlgorithmSpec` 是唯一事实源；Tool schema、description、公开名和 Adapter binding 均从 Spec 生成 |
| 多 Agent | 不采用；只有出现真实的上下文隔离或复杂复合能力证据后再晋升为 SubAgent |
| 多工具调用 | 不设置产品级并行限制；运行时按 Spec 的 `requires`/`produces` 直接排序，有依赖者串行、无依赖者并行 |
| MCP 部署 | 将因果算法执行拆为应用内私有 `causal-mcp` 服务；worker 使用可配置的长期 MCP Client pool，`N×1` 与 `N×K` 共用一套实现；服务保持业务无状态，不持有 Job 状态或 checkpoint，通过 Streamable HTTP、可信调用上下文和有界进程执行池提供能力 |
| Tool Subgraph | MVP 不为本轮 Tool calls 动态建图；简单 Tool 排序由普通调度器执行 |
| RAG | Deep Agent 直接生成检索问题；保留 active release/readiness、检索、排序、证据压缩、引用与 parser 契约，移除内部再次提问和答案生成模型 |
| Web Search | 目标形态为原子 Tool；Agent 生成检索问题，确定性服务执行搜索、过滤和归并 |
| 自动切换算法 | 是否接受、重跑、切换、比较或修订由 Deep Agent 根据全部标准化结果判断；真实动作由系统账本记录 |
| 数据处理 | 准入检查留在 Agent 之前；算法特定预处理在对应 Adapter 内确定性执行 |
| 算法结果 | Adapter 同时完成算法特定准备、MCP 执行、格式统一和硬契约校验，不再另设通用 normalization 节点 |
| 用户指定算法 | 只作为提示词中的软约束；Deep Agent 可以结合适用性与结果质量调整选择，并在报告中解释实际使用情况 |
| 最终提交 | Deep Agent 必须通过显式 `ToolStrategy(FinalAnalysisDecision)` 产生 `structured_response`；模型提交取舍、定性置信度和依据，程序始终合并真实 Action Ledger |
| 最终检查 | 无论是否调用 Tool、算法成功或全部失败，均进入确定性的 `FinalizationGate`；Gate 不调用 LLM、不修改边，只校验事实一致性 |
| 多算法结果 | Deep Agent 从有效结果中指定一个 `primary_result_ref`；界面最多展示这一张主图，其他结果、冲突和取舍写入报告 |
| LLM 图修订 | LLM 只能提交有引用的 `revision_proposals` 并在报告中说明；MVP 不生成或采用 `interpreted_graph`，不覆盖算法图 |
| 派生数据 | 不作为可下载或长期持久化产物；只保留处理配方、哈希和 provenance |
| Sandbox/文件系统 | MVP 不让模型生成或执行代码，也不开放宿主文件系统；保留虚拟 `read_file/edit_file`，不开放 `write_file`，无需引入执行 Sandbox |
| Checkpoint | 继续由父 LangGraph 的 PostgreSQL checkpointer 统一管理，同一 Job 使用同一 `thread_id` |
| Human-in-the-loop | 仅沿用外层 `fold` 的数据补充 interrupt；MVP 不在 Deep Agent 内新增 interrupt，因为当前没有必须交给用户审批的决策点 |
| 长期记忆 | MVP 保存稳定偏好与用户明确要求长期复用的研究背景；模型只允许写 `/memories/preferences.md` 与 `/memories/research_background.md`，其余虚拟路径全部拒绝模型写入；不保存用户数据内容、数据统计、因果边或算法结论，查看/删除、保留期和并发编辑仍暂缓 |
| 用户可见产物 | 报告、最多一张主因果图、预处理图表；算法失败、取舍、冲突、修订建议和置信度均由报告自然语言说明 |
| 灰度与对比 | 本轮不实现 Shadow、流量实验 A/B 或 Canary 计划；只记录现有代码与声明式环境的基线身份，发布对比另立后续阶段；这与 MCP Client pool 的 A/B 配置方式无关 |

## 2. 产品语义与成功标准

### 2.1 产品仍然是因果分析助手

系统可以回答一般性概念问题，也可以对用户文件执行因果分析。所有请求仍先进入现有 `agent` 节点，由提示词和结构化输出形成显式路由字段；本次不新增一个位于 `agent` 之前的分类节点。

- 普通问答或报告追问可以走现有普通 Agent 路径，不要求执行因果算法。
- 当用户提交数据并提出因果分析需求时，系统进入数据型路径；Deep Agent 可以根据任务和数据决定是否调用因果算法，但报告不得把未发生的 Tool 调用或不存在的算法结果写成事实。
- RAG 与 Web Search 是补充证据能力，不是因果算法的替代品。
- 用户在自然语言中指定算法时，只形成软偏好；不得用硬路由或固定 `tool_choice` 强迫调用不适用的算法。
- 未来可以加入局部因果边验证等新能力，但不在首版实现范围内。

### 2.2 最小真实性与输出不变量

对进入 Deep Agent 的分析，父图的最终 `FinalizationGate` 至少应验证：

1. Action Ledger 始终存在，并如实记录本 Job 的全部 Tool 尝试、结果、失败和重跑；没有调用 Tool 时允许为空账本；
2. Deep Agent 已产生经 `ToolStrategy(FinalAnalysisDecision)` 校验的终态 `structured_response`，其中的结果引用、置信度依据、冲突和建议字段内部一致；
3. 若声明采用算法结果或输出主因果图，`primary_result_ref` 必须指向属于本 Job、通过 Adapter 契约和 provenance 校验的真实有效结果；
4. 若没有有效算法结果，主因果图必须为空，但仍可基于用户问题、数据画像、失败事实和已有证据生成自然语言报告；
5. 提供给报告模型的执行事实必须全部来自 Action Ledger；报告契约禁止模型补写不存在的 Tool 调用、成功结果或证据；
6. 多个算法有效时只指定一个主结果，Deep Agent 不拼接、投票或自由改边生成新的主图；其他结果作为支持、舍弃或冲突材料进入报告。

可将产品完成条件理解为：

```text
analysis_completed
= FinalAnalysisDecision 结构化终态有效
+ FinalAnalysis 与 Action Ledger/结果事实一致
+ 报告陈述诚实
+ 可选主图引用真实有效结果
```

这里的 `analysis_completed` 表示本次分析流程与报告生成正常完成，不等同于“因果算法成功”。算法未调用或全部失败时，报告必须自然说明证据边界，不能输出伪造的因果结论，也不使用固定模板句替代正常报告。

### 2.3 非目标

首版明确不做以下事情：

- 不把 CausalAgent 改造成无领域边界的通用 Agent；
- 不把用户指定算法实现为硬路由或强制 `tool_choice`；
- 不为每个算法创建独立 SubAgent；
- 不引入第二套 Job 状态机、checkpoint、事件总线或文件真相源；
- 不开放模型自由写文件、执行 Python 或 Shell；
- 不保存可下载的派生数据集；
- 不实现未来的局部因果检验工具；
- 不将原始思维链、系统提示词或内部工具结果展示给用户。
- 不在本轮实现 Shadow、流量实验 A/B、Canary 或灰度发布方案；MCP Client pool 的 A/B 是两种并发配置，不是流量实验。

## 3. 当前架构事实与问题

当前父图由 [`../../Agent/causal_agent/graph.py`](../../Agent/causal_agent/graph.py) 定义，MCP、RAG 和 Web Search 的工具调用细节由 [`../../Agent/causal_agent/tool_subgraphs.py`](../../Agent/causal_agent/tool_subgraphs.py) 封装。主要数据分析路径为：

```text
agent → fold → preprocess → mcp → rag → [web_search] → agent → postprocess → report
```

当前架构的优点是路径清晰、状态显式、失败转移可控，且已经与 Job worker、checkpoint、interrupt 和公开事件适配器集成。问题在于：

- MCP、RAG、Web Search 在父图上仍接近固定流水线，模型不能自然地按证据缺口跳过、重复或改变顺序；
- MCP planner 当前强制选择一次工具，难以表达“观察结果后再调用另一个算法”；
- 算法能力说明分散在工具描述和实现中，缺少可扩展的统一能力目录；
- RAG 与 Web Search 子图含有面向旧固定流水线的 LLM planner，接入 Deep Agent 后会产生重复规划；
- 原有事件适配器按固定节点名映射，无法直接表达能力选择、切换理由和多次工具调用。

当前代码中 `agent_node` 除结构化 LLM 路由外，还存在针对显式因果分析请求的关键词快捷判断。目标产品语义已经冻结为“由现有 `agent` 节点持有路由职责，以提示词和结构化结果完成判断，不新增前置分类节点”；该快捷判断是否保留或收敛属于实现阶段的技术核对项，不能在本文中误写成一套新的产品路由层。

### 3.1 当前 RAG 的真实边界

当前 RAG 并不是“纯粹的图”。外层子图负责问题规划、ToolNode、解析和降级；真正的 enhanced retrieval 已在查询实现内部完成，包括 dense/sparse 检索、阈值、MMR、BM25、合并、rerank 和证据压缩。当前原子查询内部仍包含回答模型，因此“把 RAG 变成原子 Tool”并不自动意味着不再调用模型。

目标改造需要拆分两个概念：

- **Agent 面向的原子性**：Deep Agent 只看到一次稳定的证据检索调用；
- **内部是否使用模型**：这是 RAG 服务内部实现选择，与 Tool/Graph 形态不是同一问题。

### 3.2 当前 Web Search 的真实边界

当前 Web Search 子图先用模型生成研究问题和中英检索词，再由确定性搜索节点调用 SearXNG 的 arXiv、Crossref、OpenAlex 来源，最后投影 snippet。接入 Deep Agent 后，前两次模型规划与 Deep Agent 自身判断重叠，目标形态应让 Deep Agent提供结构化检索问题，由原子搜索 Tool 只负责检索、过滤、归并与来源元数据。

### 3.3 当前运行时必须保留的资产

[`agent-runtime.md`](agent-runtime.md) 与 [`job-file-lifecycle.md`](job-file-lifecycle.md) 记录的下列能力不是本次重写对象：

- MySQL Job 控制面、输入账本和公开事件；
- worker slot、MCP session 生命周期和运行依赖；
- lease、fencing、heartbeat、cancel 与 stale recovery；
- PostgreSQL checkpoint 与 `thread_id = job_id`；
- SSE Last-Event-ID、终态事务和历史重放；
- `JobExecutionGuard` 的取消与执行资格检查；
- 公开 payload 白名单和敏感信息脱敏。

### 3.4 当前多 Tool 与 MCP session 的真实限制

当前实现不是“每个 Job 创建并独占一个 MCP session”。[`bootstrap.py`](../../app/agent/worker/bootstrap.py) 中的每个 worker slot 在启动时创建一次 runtime，[`runtime.py`](../../app/agent/worker/runtime.py) 再建立一个长生命周期 stdio `ClientSession`，随后该 slot 在 claim/run 循环中串行处理多个 Job。因此现状可以依靠 worker slot 避免同一 session 内的 Job 并发，却不能直接推出“session 已经按 Job 隔离”，也不能支持 Deep Agent 同一轮返回多个独立 Tool calls 后的安全并发。

当前调用链还有三处单结果假设：[`mcp_tool_call_adapter.py`](../../Agent/tool_node/mcp_tool_call_adapter.py) 的消息规范化只保留 `tool_calls[0]`，[`tool_message_adapter.py`](../../Agent/tool_node/tool_message_adapter.py) 的结果解析只消费一个匹配结果，[`state.py`](../../Agent/causal_agent/state.py) 也只有单个 `causal_analysis_result`。DeepSeek Responses API 的并行 function calling 会让同一轮出现多个 call；如果不先扩展为按 provider response/call id 关联的 invocation/result 集合，其余调用会丢失或错配。即使把传输从 stdio 改成 HTTP，这个协议和状态问题也不会自动消失。

此外，当前 [`mcp_server.py`](../../Agent/CausalAgentMCP/mcp_server.py) 的异步入口会直接调用同步算法 `runner(csv_data)`。CPU 密集算法会阻塞该服务的事件循环；“独立部署”只有同时配合有界进程执行池或多副本资源隔离，才真正提供并发能力。普通线程池不应作为 CPU 密集算法的主要隔离手段，并需限制 BLAS/OpenMP 等底层线程数，避免进程内过度并行。

## 4. 目标混合架构

```text
用户请求 / 冻结文件
        ↓
Job + Worker + Lease/Fencing/Cancel
        ↓
外层 LangGraph 安全壳
  agent 节点：提示词 + 结构化 route_decision
        ├─ 普通问答 / 报告追问 → 现有对应路径
        └─ 数据型因果分析 → fold / 确定性准入 / 基础 DataProfile
        ↓
单个 Deep Agent 决策循环
  读取由 AlgorithmSpec 生成的共享工具面
  → 可选择零个、一个或多个 Algorithm/RAG/Web Tool
  → ToolDependencyPlanner 按 requires/produces 排序
  → 无依赖 Tool 并行执行，有依赖 Tool 按序执行
  → Algorithm Adapter 通过私有 causal-mcp 服务执行算法
  → Adapter 返回统一 AlgorithmResult
  → 观察结果并自主接受、重跑、切换、比较或补证据
  → 可提交只用于报告的 revision_proposals
  → 以 ToolStrategy(FinalAnalysisDecision) 产生 structured_response
        ↓
外层 FinalizationGate 节点：核对 decision / Ledger / 结果 / 主图
        ↓
report节点：无 Tool、成功或全部失败三种正常结果均生成
        ↓
用户产物：报告 + 至多一张主因果图 + 预处理图表

统一持久化：父 LangGraph PostgreSQL checkpoint
执行事实：Action Ledger 始终存在，可为空
运行期虚拟文件：large tool results / conversation history / raw algorithm results → StateBackend → checkpoint
长期记忆：稳定偏好 + 用户明确要求保存的研究背景 → StoreBackend → PostgreSQL Store
派生数据：Adapter 调用内临时存在，不作为长期真相源

应用内私有算法执行面：
worker → 带签名 McpInvocationContext → Streamable HTTP causal-mcp
       → 鉴权/冻结输入/幂等与 fencing 校验 → 有界进程执行池
```

Deep Agent 可以作为父 LangGraph 中可静态发现的编译图/节点接入。这里的“首版不使用 Subgraph”特指：不为模型本轮选择的 Tool calls 动态生成算法编排子图，也不把简单 Adapter 图化；Deep Agent 自身由框架构建为 `CompiledStateGraph` 并嵌入父图，仍需通过 Spike 验证状态投影、interrupt、取消和恢复语义。

## 5. 各层职责

### 5.1 外层 LangGraph：确定性安全壳

外层图负责模型不应拥有最终决定权的事项：

- 区分普通问答、报告追问与数据型因果分析；
- 保留现有 `agent` 节点作为统一路由入口，不在其前增加新的分类器；
- 校验 Session/Job/用户/文件归属和冻结输入身份；
- 完成文件格式、可解析性、空数据等硬性准入检查；
- 产生小而稳定的基础 `DataProfile`，而不是提前做所有算法预处理；
- 管理 interrupt、resume、checkpoint、取消和 fencing；
- 在 Deep Agent 退出后执行纯确定性的 `FinalizationGate`；
- 校验 `FinalAnalysisDecision` 结构化终态已生成，Action Ledger、结果引用、置信度依据、修订建议和报告输入字段一致；
- 仅当 `primary_result_ref` 指向真实有效结果时发布一张主因果图；没有有效结果时允许无图进入报告；
- 将稳定最终提交投影到 report，不再运行通用 LLM 边修复后处理；
- 只通过公共事件适配器向用户发布可见状态。

### 5.2 单个 Deep Agent：动态能力编排

Deep Agent 负责：

- 根据用户目标、基础数据画像和 AlgorithmSpec 生成的 Tool 描述选择候选能力；
- 自主判断是否需要 Tool；需要时调用一个或多个因果算法、RAG 或 Web Tool 并观察结果；
- 根据全部标准化结果自主接受、重跑、切换、并行比较或继续补证据；
- 判断是否需要私有知识库证据或外部学术证据；
- 判断不同算法是否存在实质冲突，并决定一个主结果以及其他结果的 supporting/discarded 状态；
- 必要时提出带结果/证据引用的结构化科学修订建议，但只在报告中说明，不生成新的图产物；
- 通过显式 `ToolStrategy(FinalAnalysisDecision)` 输出可选主结果、逐结果取舍、冲突、修订建议、选择依据、定性置信度及其字段依据，并从 Deep Agent 的 `structured_response` 读取该终态对象。

Deep Agent 不负责：

- 直接读取宿主文件路径或数据库凭据；
- 绕过 Adapter 调用 raw MCP；
- 决定 Job 是否具备最终成功资格；
- 将模型自述当作工具成功证据；
- 把无效算法结果通过自由改边伪装成算法成功；
- 把多个算法结果拼接、投票或改写为一张新的主因果图；
- 自由执行代码、Shell 或任意业务/宿主文件写入；保留的 `read_file` 只读虚拟文件，`edit_file` 的产品用途仅为长期记忆，`write_file` 不开放；
- 修改 AlgorithmSpec 或 Runtime Registry。

### 5.3 Tool Adapter：可信执行边界

Algorithm Tool 是 Agent 可见的 function tool；Adapter 是 worker 内的可信编排边界。DeepSeek Responses API 当前忽略内置 `mcp` Tool 类型，因此模型仍调用 function tool；Adapter 再通过传输无关的 `AlgorithmExecutor` 调用应用内私有 `causal-mcp` 服务。首版一个算法调用使用普通函数链，不为它创建 Subgraph：

```text
Algorithm Tool
  → Spec 能力身份 / Runtime Registry 实现绑定 / 权限与输入身份校验
  → 算法前置条件检查
  → 可逆、确定性的临时数据准备
  → 生成不可由模型填写的 McpInvocationContext
  → AlgorithmExecutor 通过 Streamable HTTP 调用 causal-mcp
  → 服务端鉴权、冻结输入复核、幂等/fencing 和有界算法执行
  → 算法专用结果解析与格式统一
  → 算法硬契约校验
  → AlgorithmResult + diagnostics + provenance
```

Adapter 不是另一个让模型自由调用的工具。前置检查、编码、格式统一、MCP 参数注入和结果解析不应拆成一组可被模型任意重排的细粒度工具。所谓 normalization 仍作为 Adapter 内部操作存在，但不再建立独立 `GraphNormalizer` 节点或万能 `GraphPostprocessPipeline`。

### 5.4 应用内私有 causal-mcp 服务

`causal-mcp` 作为当前应用的一部分独立容器部署，只暴露在 Compose 私有网络中，不映射宿主端口，也不向外部客户端提供公共发现或访问入口。worker 是首版唯一允许的调用方。RAG 与 Web Search 不进入该服务：它们的 I/O、缓存、release 和网络资源模型与 CPU 密集的因果算法不同，强行放入同一个池会放大故障域。

服务边界必须遵守“业务无状态，传输状态可替换”：MCP connection/session 可以复用、按请求创建或因故障重建，但 Job 恢复不得依赖 session 内存，服务也不持有父图 State、Job 终态或 PostgreSQL checkpoint。Job/lease/Action Ledger 的最终执行事实仍由 worker、MySQL 和父图管理；MVP 不新增持久 invocation execution record，允许业务结果只读算法在 checkpoint 提交窗口发生重复计算，`causal-mcp` 不能决定 Job 成功。

worker 进程维护一个自动调度的长期 `McpClientPool`。`CAUSAL_MCP_CLIENT_POOL_SIZE=N` 表示编号的 `mcp.Client`/transport 成员数，`CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT=K` 表示每成员并发容量，`CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS` 限制无可用容量时的等待：A 方式为 `K=1`，提供最强成员级隔离；B 方式为 `K>1`，用更少 Client 成员承载相同并发。协议或服务端未建立有状态 session 时，`mcp_session_id` 可以为空，但 `client_member_id + generation` 始终可追踪。两种方式共用健康检查、按 `inflight/K` 最小负载选择、round-robin 决胜、drain/rebuild 和 generation 追踪；首版默认 `N=2,K=1`，只允许通过部署配置并在 worker drain/restart 后切换，不做热变更，也不把 Job 固定绑定到某个成员。

建议将服务拆为六个内部职责：MCP 协议适配、调用鉴权与上下文校验、冻结输入校验、at-least-once identity/fencing 控制、全局/每 Job/每 Tool 限流，以及有界算法进程执行池。这里同时存在 worker 的 MCP Client pool 和服务端的算法进程池，二者职责不同；任何 MCP session 都不是按 Job 长期保活的业务状态容器。

同轮多 Tool 的目标执行链为：

```text
DeepSeek function calls[]
  → 按 response_identity/provider_call_id 全量规范化
  → ToolDependencyPlanner 生成可执行批次
  → 每个调用分配稳定 invocation_id
  → worker 并发调用 AlgorithmExecutor
  → McpClientPool 自动选择健康且归一化负载最低的成员
  → causal-mcp 校验上下文并申请资源令牌
  → 独立算法进程执行
  → invocation_id/provider_call_id 关联 AlgorithmResult
  → Action Ledger 原子记录并回送 Deep Agent
```

并发许可应取 worker 本 Job 上限、服务全局上限、每 Job 上限、每 Tool 上限和可用进程令牌的交集。HTTP 请求并发不等于算法无限并发；服务在无许可时应有界排队或返回稳定的 `resource_exhausted`，不能无限创建任务。只有未来某个算法协议确实需要跨调用的服务端状态时，才新增带 TTL、显式资源 ID 和清理协议的 capability-specific handle；仍不能借用 MCP session 隐式承载该状态。

## 6. AlgorithmSpec 与 Runtime Registry

### 6.1 单一事实源

`AlgorithmSpec` 是算法能力的唯一事实源。模型实际看到的 Tool name、description 和 args schema，前端使用的公开名称，以及服务端 Adapter binding 都必须从同一个 Spec 生成，禁止另外维护一份 Registry description 或 MCP docstring 副本。

Runtime Registry 仍可作为启动后的只读索引和实现绑定容器，但它不是第二份事实源。它只回答“某个 Spec 对应哪个受控 Tool 和 Adapter 实例”，不重新描述工具用途。若 MCP Tool 的 docstring 仍然存在，它是实现注释或兼容元数据，不能反向覆盖 Spec。

```text
AlgorithmSpec
  ├─ render_tool_name()
  ├─ render_tool_description()
  ├─ build_args_schema()
  ├─ public_name
  └─ adapter_binding
            ↓
      Runtime Registry
  capability_id → Tool / Adapter
```

所有 Deep Agent 实例共享同一份版本化工具目录。实例初始化时直接绑定全部启用的领域 Tool。

### 6.2 AlgorithmSpec 最小语义

首版不在规划阶段冻结具体数据库表或 Python schema，但每个能力至少需要表达：

| 字段语义 | 作用 |
| --- | --- |
| `capability_id` / `version` | 稳定标识、Schema 版本与结果 provenance |
| `public_name` | UI 和报告使用的产品化名称，不展示内部 MCP 函数名 |
| `kind` | 因果发现、因果估计、证据检索等能力类别 |
| `analysis_goal` | 能回答什么问题；帮助模型比较同目标候选能力 |
| `description_source` | 生成模型可见 Tool description 的结构化语义，不另存第二份描述 |
| `data_requirements` | 数值/离散、样本量、缺失、DAG/时序等输入约束 |
| `assumptions` | 无潜在混杂、线性、非高斯等科学假设 |
| `requires` / `produces` | 硬依赖的上游/输出产物；用于本轮 Tool calls 的直接拓扑排序 |
| `result_contract` | 输出图语义、矩阵方向、权重含义和算法硬校验契约 |
| `adapter_binding` | 服务端受控执行入口，不暴露任意导入路径给模型 |
| `enabled` / `availability` | 部署能力和运行可用性 |
| `cost_hint` | 为未来成本与时延优化留出扩展空间，MVP 不用于限制模型并行选择 |

description 应明确用途、输入要求、假设、何时不适用以及硬前置产物。提示词和 description 用于提高模型选择质量，但不是程序控制面；真正的硬依赖仍由 `requires` / `produces` 执行。

### 6.3 共享工具面与裁剪

当前算法较少时，每个算法作为独立 function Tool 绑定给 Deep Agent。不同实例共享相同的工具定义和 Spec 版本，但运行上下文、输入身份、Job 状态与结果必须按 Job 隔离。隔离依据是 worker 从可信 runtime 构造并签名、`causal-mcp` 验证和再次查库复核的调用上下文，以及稳定 invocation identity；它不是 MCP session identity。session 若存在也只承担传输生命周期，不能充当租户或 Job 边界。

Deep Agents 自动注入而本产品不需要的 `write_file`、目录检索、execute、task/subagent 等工具必须从最终模型工具面移除；保留 `read_file` 读取虚拟文件，并保留 `edit_file` 支持官方长期记忆。这里的“受控工具面”只表示模型只能调用显式授予的领域 Tool 和上述两个文件工具，不表示限制它在领域 Tool 中选择一个还是多个。`edit_file` 的写权限通过 `FilesystemPermission` 强制限制为 `/memories/preferences.md` 与 `/memories/research_background.md` 两个精确路径，并以 `/**` deny 规则拒绝模型写入其他虚拟路径；不能仅凭提示词约束写入范围。

Runtime Registry 不应变成第二套编排器。具体“本次调用哪些工具、是否重跑、是否切换或并行比较”仍由 Deep Agent 决定；运行时只执行真实依赖排序、可信参数注入、身份校验和资源级并发保护。

## 7. 算法选择、调用与自动切换

### 7.1 模型可以选择零个、一个或多个 Tool

首版的核心循环是：

```text
判断是否需要 Tool
├─ 不需要：直接形成结构化 FinalAnalysisDecision
└─ 需要：选择一个或多个 Algorithm/RAG/Web Tool
   → 运行时按真实数据依赖排序
   → 无依赖 Tool 并行、有依赖 Tool 串行
   → Adapter 返回结构化结果
   → Agent 观察结果
   → 接受 / 重跑 / 切换 / 比较 / 补证据
   → 需要时提出仅供报告使用的 revision_proposals
→ 由 ToolStrategy(FinalAnalysisDecision) 产生终态 structured_response
```

Deep Agent 不设置“必须调用因果分析工具”的提示词或 `tool_choice` 硬约束。DeepSeek Responses API 当前会忽略 `parallel_tool_calls` 参数并保持并行 Tool calling 可用，因此首版也不增加产品级 `exclusive_group` 或“默认只能运行一个算法”的限制。模型可以不调用 Tool，也可以为了鲁棒性比较多个算法，或先观察一个结果后再决定是否追加调用。资源池、连接数和 worker slot 的技术背压仍由运行时负责，但不能被描述成产品算法选择限制。

用户明确提到某个算法时，将该偏好作为 prompt 上下文提供给 Deep Agent。模型应优先考虑它，但可因数据不适用、执行失败或存在更合适能力而调整；实际调用和调整原因最终由 Action Ledger 与报告共同反映，不建立确定性算法路由。

### 7.2 提示词说明约束，程序只保证硬依赖

Tool description 应清楚说明算法何时适用、何时不适用以及是否需要上游产物，但 description 是概率性提示，不保证模型绝不产生不合法顺序。硬依赖由 Spec 的 `requires` / `produces` 表达，普通 `ToolDependencyPlanner` 只对模型本轮已经选择的 Tool calls 做拓扑排序：

```text
本 Job 已有 required artifact → 直接执行
同一批 Tool calls 有生产者 → 生产者完成后再执行消费者
既无现存 artifact 也无生产者 → 返回 not_ready，由 Agent 下一轮自行处理
没有依赖关系 → 并行执行
```

首版不为这张临时依赖关系主动创建或编译 Subgraph。只有未来某个 Tool 自身形成稳定、长时、需要独立 checkpoint/interrupt/retry 的多阶段工作流时，才单独评估预编译内部 Graph；不能为了补偿一次可能违规的模型调用而动态建图。

### 7.3 Adapter 返回统一 AlgorithmResult

Adapter 完成算法特定准备、真实 MCP 调用、结果格式统一和算法硬契约校验。各算法不必具有同一种原始图结构，但必须返回统一外壳：

```text
AlgorithmResult
  result_ref
  invocation_id / provider_call_id
  capability_id / version
  status: valid | invalid_input | not_applicable | not_ready | execution_failed | timed_out
  graph_semantics: dag | cpdag | latent_mixed_graph | ...
  graph
  diagnostics
  summary
  raw_result_ref / raw_result_sha256 / raw_result_size_bytes / serialization_version
  input_identity / provenance
```

Adapter 可以确定性处理不会改变科学语义的表示问题，例如矩阵方向转换、节点映射、数值序列化和重复无向边合并。状态必须区分责任边界：`invalid_input` 只表示数据或参数违反已声明输入契约；输入合法但算法科学适用条件不成立时是 `not_applicable`；依赖 artifact 缺失时是 `not_ready`；远端结果缺字段、维度不一致、引用不存在节点或违反硬输出契约时属于 `execution_failed`，并附稳定安全错误码。不得调用 LLM 改边后冒充算法成功，也不能把坏输出误报成用户输入错误。

不存在额外的通用 normalization 节点、中央 `GraphInspector` 路由器或万能边修复器。Deep Agent 接收全部标准化结果和 diagnostics；底层 MCP 协议噪声、内部路径和大体积重复 raw payload 不直接注入 ToolMessage。可信 Adapter 把 canonical JSON 原始结果程序化写入默认 `StateBackend` 的 `/raw_algorithm_results/{invocation_id}/{result_index}.json`，并在 AlgorithmResult 中保存引用、hash、大小和序列化版本；模型可用 `read_file(raw_result_ref)` 分段复核，不需要开放 `write_file`。

raw 虚拟文件仍属于 graph State，并随父图 PostgreSQL checkpoint 持久化和清理；这样做解决上下文/schema 解耦和分段读取，但不减少 checkpoint 的总存储量，也不提供 checkpoint 保留期之外的独立审计。目标是在同一 Tool graph step 的 checkpoint 中一致提交 raw 文件、AlgorithmResult 和 Ledger terminal update，但 `StateBackend` 通过内部 `files` channel 更新，不能在 Spike 前把它写成“同一个 Command 已保证原子”。Adapter 必须先确认 raw upload 成功并回读校验 hash，再发布结果引用；若最终无法做到单 checkpoint 原子，只允许出现没有被结果引用的孤儿 raw 文件，禁止提交指向缺失或 hash 不符文件的结果。raw 禁止 pickle，并设置明确大小上限；未来若需要跨 checkpoint 保留期复核，再设计独立 raw artifact 存储。

一个 Job 只有一个 Action Ledger，其中包含多个按 `invocation_id` 索引的 `InvocationRecord`；一个 record 又保存全部 `retry_ordinal/revision` attempts。并行 Tool 不需要修改 Deep Agents 原有 `messages` reducer，但本项目新增集合必须使用各自 reducer：AlgorithmResult 按不可变 `result_ref` 合并，Ledger 按 `invocation_id → retry_ordinal → revision` 单调合并，evidence 按不可变 `evidence_ref` 合并。不能使用含义不明的通用 `merge_by_id`，也不能用简单 list append，否则 checkpoint replay 会重复或覆盖执行事实。

invocation identity 使用 UUIDv5：协议层的 `provider_response_id` 取 DeepSeek response 顶层 `id`，`provider_call_id` 取 function-call item 的 `call_id`；function-call item 自身的 `id` 只是可选追踪字段 `provider_item_id`，不能与用于 Tool output 配对的 `call_id` 混为一谈。UUIDv5 名称使用 `job_id:response_identity:provider_call_id`，其中 `response_identity` 优先取 `provider_response_id`；若适配层没有保留它，就在模型节点提交 checkpoint 前生成并持久化本地 `message_execution_id`，同时记录 `response_identity_source`，不能把本地值伪装成供应商 ID。阶段 0 必须用真实 DeepSeek Responses API 验证三个协议字段经 `ChatOpenAI` 后分别落到哪些 `AIMessage`/tool-call 字段，不能在验证前把 `response_metadata["id"]`、`AIMessage.id` 或 `tool_call["id"]` 任一候选直接写成协议事实。当前旧 adapter 重建 `AIMessage` 时会丢失 `id/response_metadata`，且仓库里存在静态 call id，因此新路径不得复用该 normalizer。相同逻辑调用的传输重试和恢复重算复用 invocation identity，只增加 attempt；模型后续主动再调用则形成新 invocation。

2026-09-14 实施一致性说明：P2-U 已把 Algorithm、RAG 与 Web 三类本地 Tool 统一接入 `ToolRuntime → Command → State reducer`。每次模型响应后的 middleware 会在 Tool 执行前持久化新的 `message_execution_id` fallback；Tool 不再接受模型参数或静态字符串构造调用身份。每次 Tool step 在一个 `Command` 中提交匹配 `tool_call_id` 的 `ToolMessage`、领域结果/evidence 和 terminal `InvocationRecord`。checkpoint 只保存单调 terminal revision，不额外追加 queued/running 快照；queued/running 的实时进度仍由后续公共事件适配层负责。这不改变 AlgorithmResult、Action Ledger 或 Job 的产品状态语义。真实 DeepSeek `response.id`、function-call `call_id` 到 LangChain 字段的映射仍必须通过阶段 0/P3 真实协议验证，当前 ToolNode 单元测试不能替代该门禁。

### 7.4 重跑、切换、主结果和科学修订建议

结果是否足以支持当前任务、是否需要重跑、切换或并行比较，以及多个有效结果是否构成实质冲突，由 Deep Agent 结合用户目标、算法假设、diagnostics、图结构和证据判断。它可以直接再次调用同一 Tool 表示重跑，或调用其他 Tool 表示切换/比较，不需要额外的“重跑决策节点”。

存在多个有效算法结果时，Deep Agent 必须从中指定一个 `primary_result_ref`，其标准化图成为唯一可展示的主因果图。其他结果只能标为 supporting 或 discarded，并在报告中说明支持关系、差异、冲突与取舍。Deep Agent 不得合并边、投票选边或基于语言模型判断生成一张新的“综合主图”。

科学修订只面向结构有效但存在方向不确定、跨算法冲突、时间顺序或领域证据冲突的结果。LLM 可以提出保留、定向、反转或删除边的 `revision_proposals`，但每条建议必须引用已有 `result_ref` 和适用证据。MVP 只把这些内容作为报告建议，不生成或采用 `interpreted_graph`，也不覆盖 Adapter 产生的标准化算法图。

### 7.5 Action Ledger 与 FinalAnalysisDecision

Action Ledger 在 Deep Agent 开始时创建，真实 Tool 开始、结果、失败和重跑次数由运行时自动写入；未调用 Tool 时它是合法的空账本。是否发生重跑是程序事实，不要求模型在最终提交中回忆调用历史。Deep Agent 认为当前任务已经足以形成报告后，必须通过 `response_format=ToolStrategy(FinalAnalysisDecision)` 产生终态 `structured_response`，其中至少包含：

- `outcome`：只使用 `evidence_only | algorithm_supported | no_valid_algorithm` 表达分析内容，不与 Job 失败状态复用名称；
- `primary_result_ref`：可为空；非空时是唯一用于主图的真实有效结果；
- `result_assessments`：每个有效候选结果是 primary、supporting 还是 discarded，以及理由；
- `conflict_status` 与 `conflicts`：无冲突、已解决或未解决，冲突对象和处置依据；
- `revision_proposals`：可选、只进入报告的科学修订建议及其结果/证据引用；
- `selection_rationale`：选择主结果或不选择主结果的依据；
- `confidence` 与 `confidence_basis`：LLM 根据 diagnostics、有效性、跨算法一致性、假设满足程度、数据质量和证据字段给出的定性强弱及依据，不解释为统计概率。

状态语义分四层：Job 生命周期保持 `queued | running | waiting_input | succeeded | failed | canceled`；分析内容使用 `evidence_only | algorithm_supported | no_valid_algorithm`；单个 AlgorithmResult 使用 `valid | invalid_input | not_applicable | not_ready | execution_failed | timed_out`；最终一致性质量使用 `finalization_status=valid | degraded`。`timed_out` 是算法结果/attempt 状态，不新增同名 Job 终态；`degraded` 也不是 Job 状态。

这不会削弱分析步骤的真实作用。`ToolStrategy` 只是要求模型在结束循环时以工具调用式结构化协议提交最终决策，并不替代它此前对 Tool 结果的观察、比较、重跑和补证据。LangChain 负责对 Pydantic/schema 做第一层格式与类型校验，并可把校验错误反馈给模型重试；外层 `FinalizationGate` 再做第二层动态事实校验。Gate 先以当前 Job/attempt/lease 过滤出 `status=valid` 的 eligible candidate set，再要求每个候选恰好有一项 assessment，并核对主结果与 Action Ledger。`algorithm_supported` 必须恰好有一个 primary；`evidence_only` 不得有 primary，已经产生的有效算法结果只能明确 discarded；`no_valid_algorithm` 必须确实调用过算法且没有 eligible valid result。非 valid 结果若进入 assessment 也只能 discarded。前一层保证“形状正确”，后一层保证“事实真实”。

`FinalAnalysisDecision` 是唯一终止协议，不再同时实现一个普通领域 Tool `finalize_analysis`。内部使用 `analysis.finalizing`、`analysis.finalization_degraded` 和 `analysis.completed` 表达过程，不能由模型自述或普通 Tool 成功代替，也不新增公共 `analysis_finalized` 事件。校验失败时，在系统级有界预算内将稳定、脱敏的结构化错误送回 Deep Agent 重试；预算耗尽或动态事实持续不一致时，程序构造 `finalization_status=degraded` 的受控报告上下文并继续生成报告。报告成功持久化后 Job 仍标记为 `succeeded`，但不能展示未经 Gate 验证的主图，报告必须显式说明 finalization 降级。

最终持久化对象由程序合并，而不是完全由模型生成：

```text
FinalAnalysis
  execution = Action Ledger 自动汇总
  decision = 已校验的 FinalAnalysisDecision
  artifacts = 程序解析 primary_result_ref / preprocessing charts
```

`FinalizationGate` 对三种正常结果使用同一后处理入口：

| 正常结果 | Action Ledger | 主因果图 | 报告 |
| --- | --- | --- | --- |
| 未调用 Tool | 空 | 无 | 正常生成，并说明分析依据与边界 |
| 至少一个算法结果有效 | 记录真实调用 | 仅 `primary_result_ref` 对应的一张图 | 正常生成，说明算法、取舍、冲突和置信度 |
| Tool 已调用但算法全部失败/不适用/无效 | 记录全部失败事实 | 无 | 正常生成，以自然语言说明失败及仍可得出的有限分析 |

### 7.6 用户可见语义

用户最终只能看到报告、至多一张主因果图和预处理图表。报告应自然表达：

- 真实调用、成功、失败和重跑来自 Action Ledger；
- 实际采用和作为支持/舍弃的算法，以及模型给出的高层理由；
- 多算法存在冲突时，报告冲突内容、是否解决、最终选择和依据；
- 科学修订建议必须与算法原始结果分开标记，且不渲染成另一张图；
- 失败算法可以作为简洁的“运行失败”“不适用”或“结果无效”记录，但不展示内部栈、路径或敏感参数；
- 置信度是模型基于结构化字段给出的定性结果强弱，并附带主要依据；
- 不需要新增用于展示每个前置检查、重试、Registry 查询或内部 Tool 时间线的用户界面。

## 8. 数据准入与预处理

预处理应拆成两层，而不是全部放在 Agent 前或全部交给 Agent。

### 8.1 Agent 前的确定性准入

以下失败会直接影响任何因果分析，应在外层图处理：

- 文件不存在、归属错误、冻结身份不一致；
- 文件不可解析、没有数据行/列、schema 根本无效；
- 明确需要用户补充的数据缺失；
- 安全与资源硬限制不满足。

用户可以通过补充文件或信息修复时进入 interrupt；系统损坏、权限不变量或内部错误直接失败，不伪装成用户问题。

### 8.2 Adapter 内的算法特定准备

算法选定后，Adapter 才执行该算法实际需要的准备。首版允许自动执行的操作应同时满足：可逆、确定性、不删除样本、不改变研究问题。例如类型规范化、确定性类别编码，以及算法明确要求且不会改变样本集合的标准化。

以下操作不应默认自动执行：

- 删除包含缺失值的行；
- 缺失值插补；
- 异常值删除；
- 改变目标变量、样本集合或 estimand 的转换；
- 任何会显著改变因果问题语义的处理。

此类情况在首版应优先返回“不适用/需要补充数据”，由 Agent 尝试同目标替代能力；没有合法替代且用户可修复时再 interrupt。

### 8.3 派生数据

算法准备产生的派生数据只在 Tool/Adapter 调用内短暂存在，不作为可下载产物，也不成为跨 Job 的权威数据源。checkpoint 和业务数据库只保留必要的处理配方、输入哈希、算法版本、输出摘要与 provenance。进程重启后可以依据冻结输入和配方幂等重算，而不是依赖本地临时文件恢复。

## 9. RAG 与 Web Search 的目标形态

### 9.1 RAG：证据检索 Tool

用户已接受“证据导向 RAG”作为目标方案。Deep Agent 已经承担问题理解与查询生成，因此旧 RAG 子图中的内部再提问/planner 必须移除；当前 `RagService.get_response()` 中“检索后再由回答模型生成答案”的职责也要拆开。目标调用链只有一次模型规划：

```text
Deep Agent 生成结构化 query
  → RAG evidence Tool
  → active release/readiness
  → dense + sparse/BM25 + MMR + merge/rerank + threshold/top_k
  → evidence compression
  → parser/contract validation
  → EvidenceResult
  → Deep Agent 综合判断
```

这里所说的“拆分”不是删除现有 RAG 基础能力，而是把当前同时承担检索与答案生成的接口拆成面向 Agent 的 `get_evidence()` 和旧路径所需的兼容接口。Deep Agent 路径不得再经过 `answer_question`；现有 `_build_evidence_payloads()`、证据压缩、检索 trace 和降级诊断应优先复用。parser 继续存在，但作为 Tool Adapter 内的确定性验证/归一化步骤，不再作为需要模型参与的独立图节点。

RAG Tool 具体负责：

- readiness 与 active release 校验；
- dense/sparse/BM25/MMR/rerank 等内部检索；
- 证据压缩、引用、相关度和充分性输出；
- 明确区分 `available`、`no_relevant_evidence`、`unavailable`、`protocol_error`。

`active release` 指 RAG 运行时实际加载的已发布索引版本及其身份，不是“目录里存在某个索引文件”。每次结果都要携带真实 `release_id`，readiness 必须校验 active pointer、manifest、索引与 embedding 身份能被运行时一致加载；否则返回明确的 `unavailable`/诊断，不能静默查询旧索引。引用契约则保证 Deep Agent 和最终报告引用的是可核验的证据项，而不是不可追溯的自由文本。

目标 `EvidenceResult` 至少包含：

```text
EvidenceResult
  status
  query
  release_id
  evidence[]
    evidence_id
    source_locator / title / modality
    snippet
    dense_score / sparse_score / rerank_score（按可用性提供）
  sufficiency
  diagnostics
```

`evidence_id`、来源定位和 snippet 构成最小引用闭环；评分字段只表达检索系统的相关性信号，不提升为事实置信度。目标迁移采用一次 evidence-only 切换，不保留 Deep Agent 路径先走旧回答模型、再走新证据接口的双阶段兼容分支；旧接口是否暂时保留给其他调用方由实现期调用点核对决定。

### 9.2 Web Search：学术证据搜索 Tool

Deep Agent 直接提供研究问题和结构化 query；Web Tool 复用现有 SearXNG、arXiv、Crossref、OpenAlex 搜索、过滤、轮转归并和 snippet-only 输出。一次调用是原子的，但 Agent 可以根据证据缺口多次调用。

Tool 返回的是检索片段和来源元数据，不宣称已经阅读论文全文，也不把 snippet 自动提升为论文结论。`web_search_enabled` 继续由可信运行上下文控制，模型不能绕过。

### 9.3 何时保留内部 Graph

“原子 Tool”和“内部 Graph”并不冲突。只有满足下列至少一项时，才值得把 Tool 内部实现保留为 Graph：

- 存在需要独立持久化和恢复的多个阶段；
- 中间阶段需要 interrupt；
- 不同阶段具有不同重试、超时或补偿策略；
- 需要对内部阶段做明确、稳定的可观测性与状态检查。

如果只是一次服务调用前后的格式化、解析和降级，普通确定性函数更简单。首版所有 Algorithm Adapter 和本轮 Tool 依赖排序均采用普通函数/调度器，不创建动态 Subgraph。原子 Tool 仍可通过 custom events 发布少量真实进度，但调用中断后的默认恢复粒度是整次 Tool 重跑。

## 10. Tool、Graph、Middleware 与 SubAgent 的选择

| 机制 | 适合解决的问题 | 本项目首版用途 | 不应承担的职责 |
| --- | --- | --- | --- |
| 父 LangGraph 节点/边 | 硬控制流、持久化、interrupt、最终闸门 | 数据准入、Deep Agent 接入、`FinalizationGate`、报告路由 | 算法选择、图修订或动态写死每次都走的 RAG/Web |
| Agent 可见 Tool | 一个可描述、可调用、可观察结果的领域能力 | 各因果算法、证据 RAG、学术 Web Search | 暴露内部身份注入、编码、解析步骤 |
| ToolDependencyPlanner | 解释当前批次 Tool 的 `requires`/`produces` | 直接拓扑排序；独立 Tool 并行、依赖 Tool 按序 | 替模型新增/删除算法或判断科学取舍 |
| 内部 Adapter | 安全参数注入、前置校验、确定性处理、格式统一、硬契约校验 | 每个 Algorithm Tool 的真实执行边界 | 科学解释、跨算法取舍或覆盖原始结果 |
| `response_format` / `ToolStrategy` | 约束 Agent 终态结构 | 以 `FinalAnalysisDecision` 提交可选主结果、冲突、报告型修订建议、依据和定性置信度 | 代替 Action Ledger、动态事实校验或算法分析过程 |
| 私有 `causal-mcp` 服务 | 隔离因果算法的协议、计算资源和故障域 | Streamable HTTP、调用鉴权、冻结输入复核、幂等/fencing、有界进程执行 | 保存 Job/checkpoint、承担 RAG/Web 或把 session 当业务状态 |
| 内部 Graph | Tool 内确实需要持久阶段和独立恢复 | MVP 不使用；未来有充分证据时再评估预编译 Graph | 本轮 Tool 排序或为了“看起来统一”把简单函数图化 |
| Middleware | 横切策略 | 工具面裁剪、`ModelCallLimitMiddleware`、`ToolCallLimitMiddleware`、必要审计与技术背压 | 替代算法 Adapter、模型科学判断或 provenance 校验 |
| SubAgent | 上下文隔离、专门提示词、不同模型或复杂委派 | MVP 不使用 | 单纯为了算法数量多而一算法一 Agent |

未来只有当某个能力自身包含长期多步骤探索、需要独立 checkpoint/interrupt/retry、独立上下文窗口、不同模型/工具权限或复杂结果压缩时，才评估将它晋升为预编译 Graph 或 SubAgent。首版不把“模型可能选错调用顺序”当作引入 Subgraph 的理由。

### 10.1 Deep Agent 系统级有界预算

Deep Agent 循环必须由系统强制限制，不能只靠提示词要求模型“适时停止”。LangChain 官方已经提供 `ModelCallLimitMiddleware` 与 `ToolCallLimitMiddleware`，两者都支持单次运行的 `run_limit`，也支持在 checkpointer 下跨运行累计的 `thread_limit`。本项目首版把“每个 Job 的模型调用上限 + Tool 总调用上限”作为主预算机制；是否再对 Web、RAG 或高成本算法设置单 Tool 上限属于技术配置。

LangGraph `recursion_limit` 不作为第三层产品预算，也不参与“分析是否完成”的语义。若框架嵌套或异常循环仍需要它，可以保留一个明显高于中间件预算的兜底值；正常路径应先由模型/Tool 调用限制触发。具体限额、`exit_behavior` 和预算耗尽时是否仍允许产生一次受控的 `FinalAnalysisDecision`，在依赖 Spike 中冻结；不能伪造一个已通过分析的终态。

## 11. 错误处理与 Human-in-the-loop

Deep Agent 内“诚实地继续”与“立即停止”必须由错误类型决定，而不是统一吞错或统一 interrupt。

| 错误类型 | 示例 | 处理方式 | 用户语义 |
| --- | --- | --- | --- |
| 可重试的瞬态错误 | 网络抖动、短暂限流、超时 | 有界重试；耗尽后返回结构化失败 | 可展示“服务暂时不可用” |
| 算法不适用且有等价替代 | 数据类型或假设不满足 | Tool 返回不适用；Agent 可自动切换 | 展示实际切换及高层原因 |
| 非必要证据工具失败 | RAG unavailable、Web 网络失败 | 记录失败后继续；不得伪造证据 | 报告注明该证据来源未获得 |
| 缺少用户可补充的数据 | 必需列/含义/文件缺失且无合法替代 | `interrupt`，Job 进入等待输入 | 明确请求用户补充什么 |
| 所有因果算法失败 | 候选能力均不适用、执行失败、超时或结果无效 | 记录全部 attempts，`FinalAnalysisDecision` 使用空 `primary_result_ref`，经 Gate 后继续生成报告 | 在完整报告中自然说明失败事实、有限分析与限制，不输出主因果图 |
| 安全或运行不变量破坏 | 权限、文件身份、checkpoint、fencing | 异常向外传播并终止 | 稳定错误，不暴露内部细节 |
| 取消/执行资格失效 | 用户 cancel、lease 被新 worker 接管 | 控制流异常继续向 worker 传播 | canceled/revoked，不得降级为成功 |
| 未预期程序错误 | bug、schema 不变量破坏 | fail closed，记录内部诊断 | 稳定失败信息 |

Deep Agents 官方容错指导同样区分瞬态重试、可由模型恢复的 ToolMessage、需要用户修复的 interrupt 和应向外冒泡的未预期异常。本项目要把这些类别投影到已有 Job 语义，而不是新增另一套状态机。

MVP 不在 Deep Agent 循环内部增加 Human-in-the-loop。当前唯一明确需要用户参与的场景是数据/字段/文件可补充，继续由外层 `fold` 节点 interrupt；算法选择、重跑、主结果选择和报告型修订建议均由 Deep Agent 自主完成。未来若产品允许修改主图、执行有外部副作用的动作或要求用户批准科学口径，再单独设计内部审批点。

## 12. Checkpoint、文件、Sandbox 与恢复

### 12.1 Checkpoint 统一

目标是只在最外层配置持久 PostgreSQL checkpointer，Deep Agent 的编译图作为父图节点嵌入并继承同一 Job 的执行身份。LangGraph 官方文档说明：嵌套编译图默认每次调用使用独立内部状态，并可继承父 checkpointer 支持单次调用内的 durable execution 和 interrupt；需要跨调用积累内部状态时才使用 per-thread 配置。

本项目首版倾向 Deep Agent 的 per-invocation 内部状态，因为它是一次 Job 内的分析循环，长期用户偏好另走 Store。这不表示为 Algorithm Tool 或本轮 ToolDependencyPlanner 创建 Subgraph。实施前必须验证：

- 父图和 Deep Agent 的 checkpoint namespace；
- Tool 调用中断时重启后的重放粒度；
- interrupt 前后的 state 投影；
- stale recovery 是否会重复真实算法调用；
- `JobExecutionGuard` 是否传播到所有内部 Tool；
- cancel 与 fencing 是否能中止 Deep Agent 内部循环和在途调用。

### 12.2 Deep Agents 文件后端

Deep Agents 默认可使用基于 graph state 的虚拟文件系统，也支持跨线程 Store、宿主文件系统和 Sandbox。它们是可选能力，不是接入 Deep Agents 的强制要求。

CausalAgent 不使用宿主 `FilesystemBackend`。`CompositeBackend` 把 `/memories/**` 路由到 `StoreBackend → AsyncPostgresStore`，跨 Job 按可信 `user_id` 保留；`/large_tool_results/**`、`/conversation_history/**` 和 `/raw_algorithm_results/**` 使用默认 `StateBackend`，随当前 Job 的 PostgreSQL checkpoint 持久化和清理。模型保留 `read_file`，可在运行中或恢复后分段读取这些虚拟文件；可信 Adapter 通过 backend API 写 raw 文件，因此不需要向模型开放 `write_file`。`edit_file` 只允许写入两份固定 memory 文件，其他虚拟路径由兜底 deny 规则拒绝模型写入；该 Tool permission 不影响可信 Adapter 直接调用 backend。

这里有一个不能忽略的官方 API 事实：`create_deep_agent(tools=...)` 的 `tools` 参数是**追加**工具，不会自动移除内置文件工具。固定版本 0.7.13 的 `FilesystemMiddleware` 支持 `tools` allowlist，所以 MVP 应传入 `FilesystemMiddleware(tools=["read_file", "edit_file"])` 替换默认 filesystem middleware，从来源上不注册 `write_file`、delete、目录检索和 execute；Harness Profile 只负责关闭默认 general-purpose subagent，从而不出现 `task`。启动测试仍须读取实际模型工具列表，证明只保留领域/evidence Tool、`read_file`、`edit_file` 与结构化输出工具。

`edit_file` 的路径级强制权限采用 0.7.13 的 `FilesystemPermission`。规则按 first-match 计算且未命中默认允许，因此必须先允许写入 `/memories/preferences.md` 与 `/memories/research_background.md`，再用 `/**` deny 兜底拒绝其余模型写入；顺序不能颠倒，也不能只声明 allow。该权限只约束模型调用的内置 filesystem Tool，可信 Adapter 直接调用 backend 写 raw 文件不受影响。

Deep Agents 默认 SummarizationMiddleware 使用同一个 Deep Agent 模型，不必首版再配一套摘要模型；但 OpenAI-compatible DeepSeek 可能缺少准确模型 profile，因此必须显式配置并启动断言 `DEEP_AGENT_CONTEXT_WINDOW_TOKENS`。首版采用模型感知的 85% trigger、保留 10% 和 `trim_tokens_to_summarize=4000`，再用真实 Responses API 验证触发、token 统计和 tool-call 关联。摘要淘汰的历史保存在 `/conversation_history/`，模型可用 `read_file` 定向回读。

### 12.3 为什么 MVP 不需要 Sandbox

当前 MCP 数据处理由受控 Python 实现执行，模型只选择已注册能力，不生成代码，也不访问宿主文件。模型对受控虚拟文件的 `read_file/edit_file` 不等于拥有系统文件访问能力。Sandbox 解决的是不可信代码/命令执行隔离；在没有此类执行面的前提下，引入 Sandbox 会增加部署和 I/O 复杂度但不增加核心安全性。

这不代表“无需安全边界”。Deep Agents 官方安全说明强调，模型能做什么取决于被授予的工具，边界必须在 Tool/Sandbox 层实施，不能期待模型自我约束。因此首版安全重点是移除不需要的自动工具、只绑定 Spec 生成的共享领域 Tool、可信参数注入和 Adapter 校验。

### 12.4 causal-mcp 的鉴权、幂等与恢复边界

worker 调用 `causal-mcp` 时使用两层身份：第一层是仅授予 worker 的服务凭据，用于确认调用方属于当前应用；第二层是 worker 从可信 runtime 构造并用共享密钥签名、由 `causal-mcp` 验证的短时 `McpInvocationContext`，用于确认这次算法调用声称属于哪个 Job 和冻结输入。服务端还必须强读 Job/lease/文件归属，不能把签名本身当作数据库事实。该上下文不能出现在模型 Tool schema 中，也不能接受模型覆盖。建议至少包含：

```text
invocation_id
job_id / user_id / session_id
input_user_file_id / input_object_id / input_sha256
worker_id / worker_slot
attempt / lease_epoch
capability_id / capability_version
retry_ordinal
issued_at / expires_at / signature
```

`causal-mcp` 先验证服务身份、签名和有效期，再调用现有冻结文件归属校验（如 `require_frozen_file_for_job()`）复核输入。`attempt` 与 `lease_epoch` 用于隔离 Job 重试和 worker 接管；执行前、返回结果前均需检查 fencing/cancel，旧执行即使计算完成也不能被新一轮采用。

`job_id: str` 与 `session_id: str` 沿用现有数据库/API 的 UUID 字符串形态，但不是接受任意字符串：worker 与服务端必须用 UUID parser 校验，并统一序列化为小写、带连字符的 canonical UUID 后再签名；`user_id` 保持整数。这样既避免跨层类型不一致，也保证 HMAC canonical payload 稳定。

每次调用使用稳定 `invocation_id`，但 MVP 明确采用只读算法的 at-least-once 语义，不新增持久 invocation execution record。checkpoint 已提交 Tool 结果时恢复不重算；若 MCP 已执行但 Tool graph step 尚未提交，恢复可以用相同 invocation identity 和更高 `retry_ordinal` 重算，Action Ledger 保存所有失败、超时和恢复 attempts，旧 lease 的迟到结果被 fencing 丢弃。`require_frozen_file_for_job()` 因重算而增加 `access_count` 是真实访问次数，不是 Job 次数、去重依据或算法成功次数。只有未来出现有副作用 Tool、严格去重或跨 checkpoint 审计要求时，才另行设计执行记录表。

取消采用合作式边界：worker 失去执行资格后停止等待并拒绝落盘；服务在入队前、进程启动前和结果提交前检查取消/fence。对于无法安全强停的底层算法，允许计算进程最终结束，但结果必须因旧 `lease_epoch` 被丢弃。超时、进程异常和资源拒绝均返回稳定结构化错误，不把内部路径或堆栈暴露给模型。

内部追踪必须把 `worker_instance_id`、`client_member_id`、`client_generation`、`mcp_session_id`、`invocation_id`、`retry_ordinal`、`service_instance_id` 和 `executor_slot_id` 串联起来。服务已经接受调用时，在结构化响应里返回后两个字段；若响应前 transport 断开，客户端可能拿不到服务实例身份，只能凭 invocation/request identity 与服务日志反查。容器 ID/pod UID 可以生成内部 `service_instance_id`，但不进入模型或公共事件。

## 13. 公共事件与用户呈现

### 13.1 MVP 用户可见产物

MVP 不为 Deep Agent 新增计划时间线、完整 Tool 历史或内部决策面板。用户可见产物固定为：

1. 最终报告；
2. 至多一张由 `primary_result_ref` 直接引用的标准化主因果图；
3. 现有预处理图表。

没有有效算法结果时不展示主因果图，但预处理图表仍可存在。算法选择、未采用结果、失败、冲突、科学修订建议和定性置信度全部由报告以自然语言说明，不额外扩张首版页面结构。

### 13.2 报告事实来源

报告生成始终位于 `FinalizationGate` 之后，并读取由程序合并的 `FinalAnalysis`。其中 Tool 执行事实来自 Action Ledger，主图来自通过 Adapter 校验的 `primary_result_ref`，置信度和语义取舍来自已校验的 `FinalAnalysisDecision`。报告模型可以组织语言和解释限制，但不能新增 Tool 调用、成功结果、图边或证据引用。

算法失败不是报告失败。报告应把“尝试了哪些能力、为什么没有形成有效算法结果、仍能从用户问题/数据画像/证据中说明什么、结论受到哪些限制”组织成正常叙述，不使用固定的“分析已结束但未获得结果”占位句，也不把流程完成状态描述为算法成功。

### 13.3 公共事件边界

现有 SSE、MySQL `analysis_job_events` 和 [`../../app/agent/worker/event_adapter.py`](../../app/agent/worker/event_adapter.py) 继续承担进度、恢复与终态一致性。公共协议保持现有 snake_case 事件；内部点号事件只能经唯一公共适配器投影，不能直通 Deep Agent/DeepSeek 流：

| 内部语义 | 现有公共事件 | 约束 |
| --- | --- | --- |
| `tool.started` | `tool_call_start` | 公开 tool name、argument keys 和 opaque `step_id`，不得由模型自述代替 |
| `tool.succeeded/failed/not_ready/timed_out` | `tool_call_result` | 只增加受控 `status/safe_error_code`，不含参数值和 raw result |
| `analysis.finalizing/finalization_degraded` | `progress` | degraded 只说明将生成降级报告，不新增公共终态事件名 |
| `analysis.completed` | `final_result` | `data.finalization_status=valid|degraded`；报告持久化成功后两者均使 Job `succeeded` |
| `analysis.failed` | `error` | 仅用于阻止报告交付的系统、安全或不可恢复错误；取消继续使用现有 canceled 语义 |
| 外层 `fold` interrupt | `interrupt` | 只用于用户可补充的数据问题 |
| 报告生成流 | `text_delta`，结束后 `final_result` | 按现有 stream id/sequence 与脱敏边界处理 |

公共 payload 使用现有公开 Job id 和 opaque `step_id`。`invocation_id`、`result_ref`、provider response/call id、Client/member/session/容器追踪字段只进入内部 State 与日志，不进入 SSE。DeepSeek 原始 reasoning、Deep Agents 内部 SummarizationMiddleware 内容、系统提示词、完整 Tool 参数/结果、内部路径、凭据和堆栈不得进入公共事件或 UI。

## 14. 多租户与长期记忆

首版按用户粒度隔离长期记忆，只维护 `/memories/preferences.md` 和 `/memories/research_background.md`。前者保存明确、低风险且跨任务稳定的偏好，例如语言、报告详略、是否默认展示方法假设；后者只保存用户主动要求后续复用的研究背景。Deep Agent 通过 `read_file` 读取、通过 `edit_file` 更新这两份由可信 bootstrap 预创建的虚拟文件，`StoreBackend` 将其持久化到现有 PostgreSQL `AsyncPostgresStore`。`FilesystemPermission` 只允许模型写这两个精确路径，并用 `/**` deny 规则拒绝其余模型写入。因为不开放 `write_file` 且 `edit_file` 不能创建文件，bootstrap 必须采用“仅缺失时创建”语义，不能在 Job 启动时覆盖已有内容；安全的 create-if-absent/CAS 落点需在阶段 0 随 Store API 一起验证，并发编辑冲突仍按本轮决定暂缓。

不得写入长期记忆：

- 用户上传文件正文或派生数据；
- 数据集统计特征；
- 某次算法选择、因果图、局部边或结论；
- 模型对用户研究目标的猜测；
- 原始 Tool 结果和分析 scratchpad。

LangGraph 官方将 checkpointer 定位为 thread 范围的短期状态，将 Store 定位为跨 thread 的应用数据。项目应继续用 checkpoint 保存 Job 执行状态，用按 `user_id` 命名空间隔离的 Store 保存最小偏好；两者不能混用。

长期记忆的查看/删除 UI、保留期和并发编辑冲突本轮暂不冻结，也不新增其他防御机制；这些能力不能因为底层使用 StoreBackend 就视为已经解决。`edit_file` 的路径级强制权限已经冻结为“两份固定 memory 文件 allow write，其他 `/**` deny write”，属于本轮实现和测试范围。


## 15. 分阶段迁移计划

### 阶段 0：冻结当前基线

本轮只建立**基线身份快照**，不执行 Shadow/流量实验 A-B、回放评测、基线测试、镜像构建、tag 或灰度设计。Git commit 本身不可变，因此以后可直接从该提交创建独立 worktree/镜像重建旧框架，不需要为保留旧实现复制一套长期分叉代码。

截至 2026-09-11，本轮只读核对得到：

| 基线项 | 冻结身份 |
| --- | --- |
| 旧框架源码锚点 | commit `4bea85fdea7292e85577d2b9b3e4c1c763b50829`（`release: tag 0.1.0 发版`） |
| Python 依赖声明 | `requirements.txt` SHA-256 `4b59efde48c8fd0cb9e01af8bbf428226ef32de9a1e87825afb6f2a29181e020` |
| 核心声明版本 | `langchain==1.3.1`、`langchain-core==1.4.0`、`langgraph==1.2.1`；尚未声明 `deepagents` |
| 主镜像定义 | `Dockerfile` SHA-256 `d413cf9f7548041385917c6ad55e5d0bf1a3ff4114af6fa6cef12723009bbd0b` |
| 默认 Compose | `docker-compose.yml` SHA-256 `f877b016cdf856a2c3f35b3e0080a4757534811d34cb8404fcc733b857e16d33` |
| staging Compose | `docker-compose.staging.yml` SHA-256 `b974346d6b39b82f66e47d5d0e2d1d1a3403e158d1e52c161d01cc5b3e4bc712` |
| production Compose | `docker-compose.prod.yml` SHA-256 `bce5925966da88f198d8f564b8d6429064cbc780aaa02e39d41368508b7141e2` |

该快照冻结的是源码和声明式环境身份，不等于已经生成可运行的旧镜像，也不证明旧路径行为已重新验收。当前工作树另有用户自己的 `AGENTS.md` 修改和本规划文档，不把“工作树整体干净”作为基线声明。后续若启动对比测试，应以以上 commit 与哈希重建旧环境，并在独立任务中再冻结数据集、模型版本、随机种子和运行配置。

### 阶段 1：依赖与最小嵌套 Spike

基线依赖声明固定 `langchain==1.3.1`、`langchain-core==1.4.0`、`langgraph==1.2.1`；截至文档核对时，PyPI 的 `deepagents==0.7.13` 要求 Python `>=3.11,<4.0`、`langchain>=1.3.18,<2.0.0`、`langchain-core>=1.6.1,<2.0.0`。因此接入不是“新增一个包”这么简单，必须在隔离环境验证依赖升级影响，不能在功能改造中隐式升级。

Spike 只证明：可初始化、可作为父图节点/子图、Tool 调用、v2 streaming、父 checkpoint、interrupt、取消传播、结构化结果和工具面裁剪。未通过时不进入生产依赖变更。

Spike 还应设置一个反向退出条件：如果在只保留 `read_file/edit_file` 并关闭其余 filesystem、execute、task/subagent 等不需要的默认能力后，Deep Agents 相比 `create_agent + LangGraph` 没有留下足以抵偿依赖和运行复杂度的能力收益，就应回到产品目标重新评估，而不是为了“已经决定接 Deep Agents”而保留空壳依赖。

### 阶段 2：定义 AlgorithmSpec、AlgorithmResult 与 Action Ledger

先定义 Spec 单一事实源和统一状态语义，再迁移算法。验证 Tool name/description/args schema/public name/Adapter binding 均从 Spec 生成；AlgorithmResult 固定表达 `valid | invalid_input | not_applicable | not_ready | execution_failed | timed_out`，并携带 graph semantics、diagnostics、公开摘要、raw result ref 与内部 provenance。Action Ledger 以 InvocationRecord/attempt/revision 记录全部真实调用、失败、超时、恢复重算和最终结果，不依赖模型复述。

### 阶段 3：扩展多 Tool 协议并封装 Adapter

先把父 State、消息规范化、结果解析和 Action Ledger 从单个 `causal_analysis_result` 扩为按 `response_identity/provider_call_id/invocation_id` 关联的集合，并保留可空的真实 `provider_response_id` 与可选 `provider_item_id` 作为 provenance，确保同轮多个 function calls 不丢失、不串结果。保留 Deep Agents 原有 `messages` reducer，为 AlgorithmResult、Action Ledger 和 evidence 分别实现不可变/单调 reducer。随后引入传输无关的 `AlgorithmExecutor`，每个 Algorithm Adapter 完成安全参数注入、算法特定准备、raw 虚拟文件写入、格式统一和硬契约校验。实现普通依赖调度器，对模型同轮 Tool calls 按 Spec `requires`/`produces` 排序；不建立动态 Subgraph。

在该契约稳定后，新增应用内私有 `causal-mcp` 容器、服务鉴权、`McpInvocationContext`、可配置 `N×K` 长期 Client pool、at-least-once identity/fencing 和有界进程执行池。这里池化的是编号的 `mcp.Client`/transport context；协议或服务端未建立有状态 session 时，成员的 `mcp_session_id` 可以为空，不能为了追踪伪造 session。分别验证 A（`K=1`）与 B（`K>1`）的连接复用、自动选池、故障影响面、重建、超时、取消和旧 lease 结果丢弃。旧 stdio 与新 Streamable HTTP 保存在不同版本/镜像中，不在同一个运行系统使用 feature flag 保留双路径，也不在 MVP 新增持久 invocation 表。

同一阶段将 RAG 改为直接 evidence-only 契约：Deep Agent 生成 query，保留 readiness/active release、检索、排序、压缩和 parser，移除 Deep Agent 路径的内部再提问与 answer model。Web Search 则移除重复 planner，保留确定性检索和来源归并。

### 阶段 4：接入单 Deep Agent

在新版本中接入一条新路径：

```text
agent → [fold/admission/data_profile] → deep_agent(structured_response)
      → finalization_gate → report
```

现有 `agent` 节点继续拥有路由职责；方括号部分只在数据路径按现有逻辑进入。Deep Agent 只绑定 Spec 生成的共享领域工具，保留 `read_file/edit_file`，关闭 `write_file`、目录检索、shell、自由代码和 subagent 工具面，但不要求必须调用任何领域工具。内层专用模型显式设置 DeepSeek `use_responses_api=True`、固定实际 model id 和 context window，不隐式迁移父图其他模型节点。配置 `response_format=ToolStrategy(FinalAnalysisDecision)`，模型可自主接受、重跑、切换、比较，或提交仅用于报告的科学修订建议；程序从 `structured_response` 读取终态并验证可选 `primary_result_ref`、冲突字段、证据引用、置信度依据和建议范围。

### 阶段 5：统一 Finalization、报告与公共事件

让未调用 Tool、算法成功、算法全部失败三种正常结果统一经过 `structured_response → FinalizationGate → report`。Action Ledger 始终存在；只有真实有效的 `primary_result_ref` 才生成主图。Gate 还校验所有声明采用/支持/舍弃的结果属于当前 Job/attempt/lease，且每个有效候选恰有一个 assessment、最多一个 primary 并与引用一致。一次修正仍失败时生成 `finalization_status=degraded` 报告；报告成功持久化后 Job 仍为 `succeeded`。用户界面保持报告、至多一张主图和预处理图表三类产物，必要事件经公共适配器映射到现有协议。

### 后续独立阶段：发布对比与灰度

Shadow、流量实验 A/B、Canary、启用阈值和灰度回退方案不在本轮计划中，也不作为本轮 Deep Agent 实现的交付项。完成 Deep Agent 功能与单路径验收后，再基于阶段 0 的基线身份另立独立方案；本文不提前冻结其流量、指标或阈值。MCP Client pool 的 A（`K=1`）/B（`K>1`）只是同一实现的部署配置，仍属于本轮技术验证。

## 16. 实施前验证矩阵

| 验证项 | 必须证明什么 | 证据级别 |
| --- | --- | --- |
| 依赖解析 | Deep Agents 与升级后的 LangChain/LangGraph/MCP/RAG 依赖无冲突 | 隔离环境真实安装 + 全量相关测试 |
| Deep Agent 嵌入 | Deep Agent 编译图能作为父 LangGraph 节点运行且状态投影稳定 | 集成测试 |
| Checkpoint | worker 中断后能按预期恢复，不重复提交错误结果 | 真实 PostgreSQL |
| Interrupt/resume | 数据缺失 interrupt 能由同一 Job 正确恢复 | 真实 checkpoint + Job 流程 |
| Cancel/fencing | 在 Agent、Tool、RAG/Web 阶段均能阻止旧执行落盘 | MySQL + worker 集成 |
| 多 Tool 调用 | DeepSeek 同轮多个 function calls 能完整保留并正确关联 call ID、invocation ID 和结果；独立调用并行、依赖调用按序、部分失败不污染其他调用 | 真实模型 + fake/真实 MCP 分层 |
| causal-mcp 服务 | 私有网络不暴露宿主端口；服务身份与调用签名有效；A/B 两种 Client pool 配置可自动选成员、隔离/drain/rebuild 并对账故障影响；CPU 算法有界并发且不阻塞协议循环 | Compose 集成 + 并发/故障注入 + 真实算法 |
| MCP at-least-once/fencing | 重试、服务重启和 worker 接管时，同一 invocation 的全部 attempts 可对账且旧 lease 结果不能被采用；允许未提交结果重算 | MySQL + worker + causal-mcp 恢复集成 |
| Adapter 契约 | PC、OLC、DirectLiNGAM 均完成格式统一、硬校验、diagnostics 和 provenance | 算法契约单测 + 真实 MCP |
| Action Ledger | 真实调用、失败和重跑次数不依赖模型复述，恢复后不重记/漏记 | 单元 + checkpoint 恢复集成 |
| 结构化最终提交 | 零 Tool、一个/多个有效结果、全部失败均可产生合法 `FinalAnalysisDecision`；`structured_response` 可读取，每个有效结果有取舍，置信度和依据完整 | Schema 单测 + 真实模型 |
| FinalizationGate | 三种正常结果都进入报告；只有有效 `primary_result_ref` 生成一张主图；结果归属与有效候选 assessment 完整；degraded 报告仍对应 succeeded Job | 失败注入集成测试 |
| 报告失败语义 | 算法全部失败仍生成自然报告，不使用固定占位句、不伪造因果结论 | 报告契约单测 + 真实模型 |
| RAG | Deep Agent 路径不再触发内部再提问/答案模型；evidence-only 结果、引用、release identity、readiness 和 unavailable 语义正确 | 真实 active release + 模型调用计数 |
| Web | 真实 SearXNG 来源、snippet 边界、关闭开关和网络失败正确 | 真实 SearXNG |
| 事件适配 | 原始消息、参数值、工具结果、路径、隐藏推理不进入 SSE | 协议单测 + 历史重放集成 |
| 输出界面 | 只产生报告、至多一张主图和预处理图表；其他细节进入报告 | 前端契约 + 集成测试 |
| 多租户 | AlgorithmSpec/Runtime Registry、Tool、checkpoint、偏好 Store 不跨用户泄露 | 权限与并发测试 |
| 虚拟文件与摘要 | `read_file` 可读取 offload/history/raw，raw hash/大小/version 与同 step 提交一致，checkpoint 恢复可读；模型 profile 和 summarization 阈值准确 | 真实 PostgreSQL checkpoint + 真实 DeepSeek Responses |

Fake LLM 只能证明状态和协议，不能证明 Agent 实际选择质量；单元测试不能替代真实 MCP、RAG、SearXNG、PostgreSQL 和模型验收。

测试执行入口与证据边界遵循 [`../development/testing.md`](../development/testing.md)。

## 17. 技术设计阶段的影响面

进入技术实现前，应逐项核对而不是一次性重构：

- `Agent/causal_agent/graph.py`：保留 `agent` 路由职责、接入新路径、`FinalizationGate` 与 report 路由；
- `Agent/causal_agent/state.py`：AlgorithmResult、Action Ledger、finalization 与图产物引用；
- `Agent/causal_agent/context.py`：可信运行上下文；
- `Agent/tool_node/` 与 MCP adapter：AlgorithmSpec/Runtime Registry、ToolDependencyPlanner、Adapter 和 AlgorithmResult；
- `FinalAnalysisDecision` 与显式 `ToolStrategy`：可选主结果、冲突、报告型修订建议、定性置信度、结构化错误反馈与外层动态事实校验；
- `Agent/knowledge_base/`：新增 `get_evidence()`，复用真实 active release/readiness、检索、排序、压缩和 parser，移除 Deep Agent 路径的内部再提问与答案生成；
- 新的应用内 `causal-mcp` 服务与 `AlgorithmExecutor`：Streamable HTTP、服务鉴权、调用上下文、幂等/fencing、进程执行池和资源限额；
- Web Search 实现：移除重复 planner 后的 Tool 契约；
- `app/agent/worker/runtime.py` / bootstrap：依赖初始化和 slot 资源；
- `app/agent/worker/graph_runner.py`：v2 多流、同轮多 Tool call、interrupt；
- `app/agent/worker/event_adapter.py` 与 public events：Tool/Action Ledger、finalization、报告终态事件和脱敏；
- PostgreSQL checkpoint 与 MySQL Job/events：恢复、终态事务和清理；
- 前端产物：报告、至多一张主因果图和预处理图表；
- requirements/镜像/Compose：依赖升级与 worker 启动；
- 对应单元、集成、真实依赖和恢复测试。

这份清单是影响面，不是对所有文件进行修改的授权。

## 18. 风险与权衡

| 风险 | 影响 | 规划中的控制 |
| --- | --- | --- |
| 模型选择不稳定 | 不同运行选择不同算法 | Spec 精确描述、Adapter diagnostics、结构化最终提交、评测集 |
| description 被误当硬控制 | 模型同轮调用了有依赖关系的 Tool | Spec `requires`/`produces` + 直接拓扑排序 + `not_ready` |
| 同轮多 Tool 结果错配 | call ID、结果或部分失败归属错误 | 每次 invocation/result 稳定 ID、Action Ledger、并发集成测试 |
| LLM 建议覆盖算法事实 | 报告建议被误当算法原始结论 | `revision_proposals` 只进入报告，不生成图，不覆盖 raw/standardized graph |
| Tool 重放产生重复执行 | checkpoint 恢复时重复算法/MCP 调用 | 明确只读 at-least-once、稳定 invocation/attempt、输入哈希、执行 guard、恢复测试 |
| Deep Agents 默认工具面过大 | 文件/代码执行扩大攻击面 | 只保留虚拟 `read_file/edit_file`，禁用 write/目录检索/Shell/SubAgent；只允许模型写两份固定 memory 文件并拒绝其余虚拟路径写入 |
| 报告虚构执行事实 | 把模型叙述误当 Tool 调用或成功 | 报告读取程序合并的 FinalAnalysis，执行陈述与 Action Ledger 对账 |
| RAG 双重生成 | 重复成本与观点漂移 | Deep Agent 路径直接切换到 evidence-only，调用计数验证不再进入内部 planner/answer model |
| 把 MCP session 当 Job 状态 | 服务扩缩容或连接重建后无法恢复，形成第二真相源 | 业务无状态服务、稳定 invocation identity、Job/checkpoint 仍由 worker 和数据库持有 |
| B 模式扩大 Client 成员故障域 | 一个 Client/transport 损坏影响同成员多个调用 | `N×K` 配置、自动负载选择、成员 drain/generation 重建、A/B 故障注入；必要时退回 `K=1` |
| 独立服务但算法仍阻塞事件循环 | HTTP 可接收多个请求但 CPU 算法实际串行、超时扩散 | 有界进程执行池或多副本、队列背压、BLAS/OpenMP 线程上限 |
| 内部服务被越权调用 | 跨 Job/用户读取冻结文件或伪造算法身份 | 私有网络、worker 服务凭据、短期签名调用上下文、服务端再次校验文件归属 |
| worker 接管后旧结果落盘 | stale 执行污染新 attempt | `attempt`/`lease_epoch` 前后校验、稳定 invocation 记录、旧结果拒绝采用 |
| 并行事件乱序 | SSE 重复、错配或错误归属 | sequence/call/result ID、公共适配器统一折叠、禁止 raw stream 直通 |
| 依赖升级影响现有系统 | MCP/RAG/checkpoint 回归 | 隔离 Spike，独立依赖决策，旧路径回退 |
| 过早多 Agent | 成本、延迟、协议和恢复复杂度上升 | MVP 单 Agent，以真实观测触发晋升 |

## 19. 已冻结与待后续确认

### 19.1 已冻结的产品/架构决策

- 因果分析助手的产品边界；
- 所有请求继续由现有 `agent` 节点持有路由职责，以提示词和结构化 `route_decision` 判断，不增加前置意图分类节点；
- Deep Agent 不强制调用因果 Tool；未调用 Tool、至少一个算法有效、算法全部失败均可产生结构化终态并生成报告；
- technically valid 的算法结果如果与当前问题无关，可以全部明确标记为 `discarded`，最终 outcome 使用 `evidence_only`；此时不得设置 `primary_result_ref`、不得产生主因果图，报告也不得采用这些算法结论；
- 外层 LangGraph + 内层单 Deep Agent，不采用 Supervisor + 多 Specialist；
- 所有 Deep Agent 实例共享同一份启用领域工具定义，运行上下文与结果按 Job 隔离；
- `AlgorithmSpec` 是唯一事实源，Tool name/description/schema、公开名和 Adapter binding 从 Spec 生成；Runtime Registry 只负责只读索引和绑定，不维护第二套 description；
- 当前工具数量不做渐进式披露，后续工具规模真正造成上下文问题时再优化；
- 保留 Deep Agents 虚拟 `read_file` 与 `edit_file`，关闭 `write_file`、目录检索、execute、task/subagent 工具；不使用宿主 `FilesystemBackend`；
- 算法以 function Tool 暴露，Adapter 经传输无关 `AlgorithmExecutor` 调用应用内私有 `causal-mcp`；不把 DeepSeek 不支持的内置 MCP Tool 类型作为模型入口；
- `causal-mcp` 独立容器部署但仍属于当前应用，只在私有网络接受 worker 调用，不向外部暴露；RAG/Web 不并入该服务；
- `causal-mcp` 业务无状态，MCP session 不是 Job/session/checkpoint 真相源；调用以签名 `McpInvocationContext`、稳定 invocation identity 和 lease fencing 隔离；
- 每个 worker 进程使用一套参数化的长期 MCP Client pool；`CAUSAL_MCP_CLIENT_POOL_SIZE=N`、`CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT=K` 同时支持 A（`K=1`）与 B（`K>1`），`CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS` 限制容量等待，自动选择成员，默认 `2×1`，通过 drain/restart 切换配置；
- 因果算法的“池”是有界进程执行池或服务副本，不是按 Job 长期保活的 MCP session 池；
- 算法前置处理、格式统一和算法硬契约校验都在对应 Adapter 内完成，不另设通用 normalization 节点；
- Deep Agent 可自由选择零个、一个或多个 Tool，不设置产品级 `exclusive_group`、默认单算法限制或必须调用因果 Tool 的硬约束；
- Spec description/prompt 说明选择与顺序，硬依赖由 `requires`/`produces` 和普通 ToolDependencyPlanner 直接拓扑排序；无依赖调用并行，有依赖调用按序；
- MVP 不为本轮 Tool calls 建动态 Subgraph，也不为简单 Adapter 建内部 Graph；
- 全部标准化 AlgorithmResult 与 diagnostics 交给 Deep Agent，由模型决定接受、重跑、切换、比较、补证据或提出报告型科学修订建议；
- Action Ledger 始终存在，可为空；每个 Job 的 Ledger 包含多个 InvocationRecord，每个 record 保留全部失败、超时、恢复重算与最终 attempts，不让模型在最后复述执行事实；
- Deep Agent 必须通过显式 `ToolStrategy(FinalAnalysisDecision)` 在 `structured_response` 中提交可选主结果、逐结果取舍、冲突、修订建议、选择依据、定性置信度和 `confidence_basis`；不再实现并行存在的普通 `finalize_analysis` Tool；
- 多个算法有效时只选一个 `primary_result_ref` 作为唯一主图来源；其他结果、冲突及最终选择依据必须进入报告；
- LLM 不得修改 raw/standardized graph，不生成或采用 `interpreted_graph`；`revision_proposals` 只作为报告建议；
- 外层只保留纯确定性的 `FinalizationGate`，不调用 LLM、不修边；它校验 finalization、Ledger、结果和主图引用后，三种正常结果均进入 report；
- `FinalizationGate` 还校验采用/支持/舍弃结果的当前 Job/attempt/lease 归属和全部有效候选 assessment；`finalization_status=degraded` 仍生成报告，报告持久化成功后 Job 仍为 `succeeded`；
- RAG 与 Web 由模型按需调用，目标均为 Agent 面向的原子 Tool；
- RAG 直接迁移为 evidence-only：移除内部再提问和答案模型，保留真实 active release/readiness、dense/sparse/BM25/MMR、merge/rerank、阈值/top_k、证据压缩、parser、引用与降级状态；
- 派生数据不提供下载、不作为长期产物；
- raw 算法输出由 Adapter 写入 StateBackend 虚拟路径并随 checkpoint 保存，模型可用 `read_file` 读取；不进入长期记忆，也不提供跨 checkpoint 保留期审计；
- MVP 无 Sandbox、无自由代码/文件执行；
- 长期记忆保存稳定偏好和用户主动要求保存的研究背景，通过 `StoreBackend → AsyncPostgresStore` 按用户持久化；`FilesystemPermission` 只允许模型写两份固定 memory 文件并拒绝其余虚拟路径写入；查看/删除、保留期和并发编辑仍暂缓；
- 用户自然语言指定算法只作为提示词软约束，不使用固定 `tool_choice`；实际选择和偏离原因在报告中解释；
- 仅内层 Deep Agent 显式使用 DeepSeek Responses API，并固定实际 model id/context window；父图其他模型不隐式迁移，不设计跨供应商流式降级，不公开原始 reasoning/思维链；
- MVP 用户可见产物仅为报告、至多一张主因果图和预处理图表，其他执行与取舍信息在报告中自然语言说明；
- Human-in-the-loop 只沿用外层 `fold` 数据补充；当前不在 Deep Agent 内增加 interrupt；
- 本轮只冻结旧框架 commit 与声明式环境文件哈希，不设计或实现 Shadow、流量实验 A/B、Canary、流量阈值和灰度回退；
- 首版优先保证正确可运行，成本和延迟留有优化空间。

### 19.2 已明确暂缓的产品边界

长期记忆的查看、删除、保留期和并发编辑冲突由用户明确暂缓；除已经冻结的精确路径 write allow/deny 外，本轮不新增其他防御措施，也不能宣称这些数据生命周期能力已经完成。Gate 真值表已经确认：如果算法返回一个或多个 technically valid 结果，但 Deep Agent 判断它们与当前问题无关并全部标记为 `discarded`，最终 outcome 允许为 `evidence_only`；该规则已进入第 19.1 节冻结清单，不再是开放问题。后续若要引入图编辑、Deep Agent 内审批、更多用户可见过程面板或灰度对比，也应作为新的产品范围重新讨论，不能在技术实现中默认加入。

### 19.3 进入实现前再冻结的技术细节

- Deep Agents/LangChain/LangGraph 的候选升级组合仍需 clean install、全量相关回归后才能写入正式锁文件；
- 真实 DeepSeek model id、context window、Responses `response.id/call_id` 到 LangChain 消息字段的映射，以及摘要阈值仍需真实 API Spike 证明；
- Deep Agent 在父图中的最终 state schema、checkpoint namespace，以及 StateBackend `files` channel 更新与结果/Ledger 在同一 Tool graph step checkpoint 中的一致性；
- 每个现有算法的精确 requirements、assumptions、result_contract、description 模板和 raw JSON 大小上限；
- A/B 两种 MCP Client pool 的真实 SDK 并发安全与故障影响面；测试结果决定实际环境采用 `N×1` 还是 `N×K`，但配置能力和默认 `2×1` 已冻结；
- `service_instance_id/executor_slot_id` 的具体生成方式，以及公共 `tool_call_result.status/safe_error_code` 的最终 schema 兼容测试；
- Deep Agent 系统级有界预算的最终数值，以及哪些数据缺口允许外层 `fold` interrupt；
- 长期记忆文件安全的 create-if-absent 初始化方式，以及查看/删除、保留期和并发写入；`edit_file` 路径级强制权限已经冻结，不再属于开放项。

这些细节会显著影响代码，但不改变本文已经确认的产品方向。

## 20. 事实来源

以下来源用于确认框架能力；项目设计结论仍由本文负责，不把团队方案或模型推断当作官方事实：

- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)：Deep Agents 的定位、默认能力和基于 LangGraph 的关系。
- [`create_deep_agent` API reference](https://reference.langchain.com/python/deepagents/graph/create_deep_agent)：返回 `CompiledStateGraph`、内置工具、工具追加语义、Harness Profile、checkpointer、Store 和 SubAgent 配置。
- [Deep Agents customization - structured output](https://docs.langchain.com/oss/python/deepagents/customization#structured-output)：`response_format`、结构化终态和 `structured_response` 的官方行为。
- [Deep Agents subagents](https://docs.langchain.com/oss/python/deepagents/subagents)：SubAgent 的用途、上下文隔离和自定义编译图接入方式。
- [Deep Agents backends](https://docs.langchain.com/oss/python/deepagents/backends)：StateBackend、StoreBackend、FilesystemBackend 与 Sandbox 的差异。
- [Deep Agents permissions](https://docs.langchain.com/oss/python/deepagents/permissions)：filesystem 权限规则、first-match 与默认允许语义。
- [Deep Agents fault tolerance](https://docs.langchain.com/oss/python/deepagents/fault-tolerance)：重试、ToolMessage、interrupt 与异常冒泡的边界。
- [Deep Agents going to production](https://docs.langchain.com/oss/python/deepagents/going-to-production)：生产 Agent 同时设置模型调用与 Tool 调用 `run_limit` 的官方建议。
- [`ModelCallLimitMiddleware` API reference](https://reference.langchain.com/python/langchain/agents/middleware/model_call_limit/ModelCallLimitMiddleware)：模型调用的 run/thread 上限和耗尽行为。
- [`ToolCallLimitMiddleware` API reference](https://reference.langchain.com/python/langchain/agents/middleware/tool_call_limit/ToolCallLimitMiddleware)：全局或单 Tool 的 run/thread 上限和耗尽行为。
- [Deep Agents streaming](https://docs.langchain.com/oss/python/deepagents/streaming)：v2 `updates/messages/custom` 与子 Agent namespace 的流式接口。
- [LangGraph subgraph persistence](https://docs.langchain.com/oss/python/langgraph/use-subgraphs#subgraph-persistence)：父 checkpointer、per-invocation/per-thread/stateless 子图语义。
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)：条件分支、并行 superstep、`Send` 和 `Command` 的控制流语义。
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：checkpointer 与 Store 的职责区分。
- [LangChain custom workflow](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow)：确定性逻辑与 Agent 行为混合的自定义工作流。
- [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output)：`ProviderStrategy`、`ToolStrategy`、Pydantic/JSON Schema 和校验失败反馈。
- [LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp)：`MultiServerMCPClient` 默认无状态调用、显式持久 session 与 Streamable HTTP 连接配置。
- [MCP Transports specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)：stdio 与 Streamable HTTP 的协议生命周期、安全和 session 约束；本文的 Job、签名上下文、幂等和 fencing 是 CausalAgent 自己的应用层设计，不是 MCP 协议自动提供的能力。
- [DeepSeek Responses API](https://api-docs.deepseek.com/api/create-response/)：response/output item/call identity、function Tool、tool choice、流式事件和 reasoning 输出契约。
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)：function description/schema、thinking Tool use 和 strict JSON Schema 模式边界。
- [deepagents 0.7.13 PyPI metadata](https://pypi.org/pypi/deepagents/0.7.13/json)：截至文档日期的 Python 与 LangChain 依赖要求。
- 团队内部方案：[Deep Agents Harness 驱动的 Supervisor + SubAgent Loop](https://www.notion.so/Deep-Agents-Harness-Supervisor-SubAgent-Loop-3d09953b3cce80fb8290fd429743be72)。该页面是设计输入，不是 LangChain 官方事实来源。
