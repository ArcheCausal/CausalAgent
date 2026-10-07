# Deep Agent 集成技术实现方案

> **文档职责**：把《Deep Agent 集成改造计划》中已经冻结的产品与架构决策，落实为可编码、可迁移、可测试和可回退的技术方案；同时记录版本适配、状态契约、MCP 传输与并发、幂等恢复、可观测性及验收门槛。
>
> **适用范围**：适用于 `Agent/`、`app/agent/worker/`、`Database/`、RAG/Web 工具适配层和 Docker Compose 中的 Deep Agent 改造。本文不改变产品语义，不代表功能已经实现，也不替代现有运行时事实文档。

**状态**：技术设计草案，等待按阶段实施

**事实核对日期**：2026-09-14

**产品决策来源**：[deep-agent-integration-plan.md](./deep-agent-integration-plan.md)
**目标架构图**：[deep-agent-integration-technical-design.drawio](./deep-agent-integration-technical-design.drawio)

---

## 1. 文档边界与结论

产品文档回答“为什么改、用户最终得到什么、哪些行为已经冻结”；本文回答“代码放在哪里、状态如何流转、失败如何恢复、依赖如何安装、并发如何受控、怎样证明实现正确”。为避免两个文档重新耦合，本文只引用实现必须遵守的产品决策，随后给出技术解释；若摘要措辞与产品文档第 19.1 节冲突，以产品文档原文为准。

本方案给出以下核心技术结论：

1. 新版本不保留 `langchain-mcp-adapters`，主程序和独立 `causal-mcp` 镜像都使用 `mcp==2.2.0`。主程序通过官方 MCP client 调用服务，模型只使用本地 function tools；这组依赖切片已通过隔离 `pip --dry-run`。
2. 旧版本继续把 `langchain-mcp-adapters` 与 MCP v1 保存在自己的锁文件和镜像中，不与新版本共环境。官方 v2 服务端虽能兼容早期协议，但本项目新链路没有必要主动保留 v1 客户端。
3. 新 Deep Agent 会直接看到由 `AlgorithmSpec` 生成的本地 LangChain function tools；“不暴露动态工具清单”特指不在运行时调用远端 MCP `list_tools()` 后，把服务端当时返回的任意工具自动注册给模型。MCP tools 是内部服务契约，由 `AlgorithmExecutor` 调用，不是第二份模型工具清单。
4. 当前仓库依赖直接加入 `deepagents==0.7.13` 无法解析；必须整体升级 LangChain/LangGraph 兼容簇。本文给出已通过独立 `pip --dry-run` 的候选簇，但完整项目依赖和运行回归仍是阶段 0 的强制门禁。
5. 本方案存在两类不同的池：worker 内的长期 MCP Client pool 负责连接复用与传输故障隔离，`causal-mcp` 内的有界 CPU 进程池负责算法并发。MCP session 不承载 Job 状态。首版默认单副本、2 个进程、队列 4、每 Job 同时最多 2 个工具调用；这些值是保守启动值，不是性能承诺，必须用真实数据压测后调整。
6. 外层 LangGraph 继续承担 Job 生命周期、checkpoint、`fold`、fencing 和最终确定性门禁；内层单 Deep Agent 只承担工具选择、迭代分析与结构化决策。两层状态通过显式投影连接，禁止共享无边界字典。
7. 旧实现和新实现保存在不同分支、构建为不同镜像并分别部署，不在同一个运行系统中保留双路径。本次设计不实施架构对比实验，只冻结未来比较所需的版本材料；未来实验允许工具调用漂移，主要评价报告质量。
8. MVP 不新增 `analysis_tool_invocations` 表。算法工具保持只读、允许崩溃恢复时重复计算；checkpoint 保存 Tool call、结果和 Action Ledger，现有 Job lease/fencing 阻止旧 worker 的迟到结果进入报告。只有未来出现有外部副作用的工具或严格去重要求时，才新增执行记录。
9. 长期记忆采用 Deep Agents 官方 filesystem-backed memory：只配置偏好和用户主动要求保存的研究背景，模型通过内置 `edit_file` 写入两份固定 memory 文件，`StoreBackend` 把虚拟文件持久化到现有 PostgreSQL 的 `AsyncPostgresStore`；`FilesystemPermission` 精确允许这两个路径并以 `/**` 拒绝其他模型写入。不新增自定义记忆 Tool、记忆 UI 或独立审计表；长期记忆的查看、删除、保留期和并发写入仍暂缓。
10. MCP 2.2 首版使用 Streamable HTTP over HTTP/1.1。每个 worker 操作系统进程维护一个可配置的长期 `mcp.Client`/transport 成员池，并复用同一个进程级 `httpx2.AsyncClient` 连接池；协议未建立有状态 session 时，成员的 `mcp_session_id` 合法为空。`CAUSAL_MCP_CLIENT_POOL_SIZE=N`、`CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT=K` 同时覆盖 A（`K=1`，成员级隔离）和 B（`K>1`，成员内并发复用）两种运行方式；首版默认 `2×1`，以后只通过部署配置和 worker drain/restart 切换，不维护两套实现。

---

## 2. 产品决策原文与技术落点

以下内容摘要自产品文档“19.1 已冻结的产品/架构决策”，用于建立可追踪性；完整产品语义以原章节为准。

> - 外层 LangGraph + 内层单 Deep Agent，不采用 Supervisor + 多 Specialist；
> - `AlgorithmSpec` 是唯一事实源，Tool name/description/schema、公开名和 Adapter binding 从 Spec 生成；Runtime Registry 只负责只读索引和绑定，不维护第二套 description；
> - 保留 Deep Agents 内置 `read_file` 和 `edit_file`：前者读取虚拟文件，后者供长期记忆写入；关闭 `write_file` 及其他不需要的 filesystem、execute、task/subagent 工具；
> - 算法以 function Tool 暴露，Adapter 经传输无关 `AlgorithmExecutor` 调用应用内私有 `causal-mcp`；不把 DeepSeek 不支持的内置 MCP Tool 类型作为模型入口；
> - `causal-mcp` 独立容器部署但仍属于当前应用，只在私有网络接受 worker 调用，不向外部暴露；RAG/Web 不并入该服务；
> - `causal-mcp` 业务无状态，MCP session 不是 Job/session/checkpoint 真相源；调用以签名 `McpInvocationContext`、稳定 invocation identity 和 lease fencing 隔离；
> - 因果算法的“池”是有界进程执行池或服务副本，不是按 Job 长期保活的 MCP session 池；
> - Spec description/prompt 说明选择与顺序，硬依赖由 `requires`/`produces` 和普通 ToolDependencyPlanner 直接拓扑排序；无依赖调用并行，有依赖调用按序；
> - Action Ledger 始终存在，可为空；真实 Tool 调用、结果、失败和重跑由运行时自动记录，不让模型在最后复述执行事实；
> - Deep Agent 必须通过显式 `ToolStrategy(FinalAnalysisDecision)` 在 `structured_response` 中提交可选主结果、逐结果取舍、冲突、修订建议、选择依据、定性置信度和 `confidence_basis`；不再实现并行存在的普通 `finalize_analysis` Tool；
> - 外层只保留纯确定性的 `FinalizationGate`，不调用 LLM、不修边；它校验 finalization、Ledger、结果和主图引用后，三种正常结果均进入 report；
> - RAG 直接迁移为 evidence-only：移除内部再提问和答案模型，保留真实 active release/readiness、dense/sparse/BM25/MMR、merge/rerank、阈值/top_k、证据压缩、parser、引用与降级状态；
> - 内层 Deep Agent 使用 DeepSeek Responses API；父图其他模型节点不在本轮被隐式迁移，不设计跨供应商流式降级，也不公开原始 reasoning/思维链；
> - Human-in-the-loop 只沿用外层 `fold` 数据补充；当前不在 Deep Agent 内增加 interrupt；

技术实现中的对应关系如下：

| 产品概念 | 技术载体 | 强制边界 |
|---|---|---|
| 外层安全壳 | 现有 `Agent/causal_agent/graph.py` 与 worker runtime | 持有 Job 权威状态、checkpoint、fold、finalization gate、report |
| 内层 Deep Agent | 静态编译的 Deep Agent graph，作为父图中的 `deep_agent` 节点运行 | 不是 Deep Agents `subagent`，不启用 `task` 委派，不持有 Job 真相 |
| 领域工具 | `AlgorithmSpec` 生成的本地 function tools | 模型参数与可信运行参数严格分离 |
| 算法执行 | `AlgorithmExecutor` 接口及 MCP 实现 | Deep Agent 不接触数据库 ID、签名、lease_epoch |
| 执行事实 | checkpoint 中的 ToolMessage、AlgorithmResult 与 Action Ledger reducer | 由运行时记录，模型不能声明调用成功；MVP 接受故障后的重复只读计算 |
| 最终决策 | `ToolStrategy(FinalAnalysisDecision)` | LLM 只提交候选决策；外层 Gate 做动态一致性校验 |
| MCP 隔离 | 私有 Streamable HTTP 服务 + 有界进程池 | session 无业务身份；不对宿主机/公网发布端口 |

---

## 3. 当前实现基线与迁移原则

### 3.1 当前事实

当前 Docker 基线是 Python 3.11。父图主链路为：

```text
agent -> fold -> preprocess -> mcp -> rag -> [web_search] -> agent -> postprocess -> report
```

当前实现还有四个与本次改造直接相关的约束：

- 每个 worker slot 启动一个长期存在的 stdio `ClientSession`，加载 MCP tools 后编译自己的图；slot 内 Job 串行执行，`JOB_WORKERS` 默认 2。
- `Agent/tool_node/mcp_tool_call_adapter.py` 只处理 `tool_calls[0]`，状态只容纳一个 `causal_analysis_result`，因此不能表达 DeepSeek 并行返回的多个 tool calls。
- 当前 MCP server 的 async handler 直接调用同步 CPU 算法，会阻塞服务事件循环；把传输从 stdio 改为 HTTP 并不会自动解决 CPU 并发。
- 外层 PostgreSQL checkpointer 已用 `thread_id=job_id`，MySQL Job 表已有 `worker_id`、`attempt_count`、`lease_epoch`、events 和 inputs 账本，应复用这些权威身份而不是另造 Agent session。

### 3.2 版本保留、迁移与未来对比前置条件

本项目不在同一套运行系统中实现新旧双路径。旧版本固定 Git commit/tag、依赖锁、镜像 digest 和声明式配置；新版本在独立分支开发并构建独立镜像。发生问题时停止新版本分配 Job，再回退到旧镜像，而不是在进程内切换 graph。

本次技术设计不包含离线 replay、Shadow、流量实验 A/B、Canary 的执行流程或评测脚本；这里不包括第 9.3 节 MCP Client pool 的 A/B 并发配置。为了让后续独立计划仍能公平比较两个不可变部署单元，现在只要求每个版本保留以下前置材料：

- Git commit/tag、完整依赖锁文件、应用镜像和 `causal-mcp` 镜像 digest；
- migration head、声明式配置版本，以及 prompt、模型可见 Tool schema、`FinalAnalysisDecision` schema 和 `spec_digest`；
- RAG active release、manifest、embedding fingerprint 和模型/API 配置；
- 带版本号的冻结输入与问题集。

未来实验主要比较报告质量，允许模型选择、Tool 调用顺序和调用次数发生自然漂移，因此不要求逐调用完整复现。若届时需要比较延迟、成本或失败率，应在新的实验计划中单独冻结采集口径；若两版数据库 schema 不兼容，则使用独立数据库快照或先实施向后兼容 migration。Git 分支本身不能回退数据库和在途 Job。

---

## 4. 版本适配方案

### 4.1 当前锁定版本与已确认冲突

| 组件 | 当前仓库 | 目标/候选 | 判定 |
|---|---:|---:|---|
| Python | 3.11 | 3.11 | 可保留；Deep Agents 要求 Python `>=3.11,<4` |
| deepagents | 未安装 | 0.7.13 | 新增 |
| langchain | 1.3.1 | 1.4.0 | 必须升级；Deep Agents 0.7.13 要求 `>=1.3.18,<2` |
| langchain-core | 1.4.0 | 1.6.3 | 随兼容簇升级 |
| langchain-openai | 1.2.2 | 1.6.2 | 建议升级以使用当前 Responses 路径；需做 DeepSeek 契约测试 |
| langgraph | 1.2.1 | 1.2.11 | 随 LangChain 1.4.0 升级 |
| langgraph-checkpoint | 4.1.1 | 4.2.0 | 随兼容簇升级 |
| langgraph-checkpoint-postgres | 3.1.0 | 3.1.2 | 随兼容簇升级 |
| langgraph-prebuilt | 1.1.0 | 1.1.0 | 可保留 |
| pydantic | 2.11.4 | 2.13.5 候选 | 独立解析选择 2.13.5；须全仓回归 |
| langchain-mcp-adapters | 0.2.2 | 新版本移除 | 只存在于冻结的旧版本镜像 |
| 主程序 mcp | `>=1.9.2,<2` | 2.2.0 | 直接使用官方 MCP client，不经过 LangChain adapter |
| causal-mcp 镜像 mcp | 与主程序共环境 | 2.2.0 | 独立镜像固定 v2 |

实证结果：

- 在当前 `langchain==1.3.1` 上直接加入 `deepagents==0.7.13`，`pip --dry-run` 返回 `ResolutionImpossible`。
- 上表候选兼容簇在 2026-09-13 的当前宿主 Python 3.12.3 中，以独立依赖切片执行 `pip install --dry-run --ignore-installed` 成功解析；其中包含 `mcp==2.2.0`、不包含 `langchain-mcp-adapters`，解析器选择 `pydantic==2.13.5`。
- 这不是当前完整 `requirements.txt` 的安装和测试结果。代码实施前必须在新虚拟环境对全量 requirements 生成锁文件、安装、导入并跑定向测试。
- 项目镜像使用 Python 3.11，因此仍须在真实 Docker 3.11 构建中重新解析和安装；宿主 Python 3.12 的 dry-run 不能替代该证据。

### 4.2 为什么新版本可以直接使用 MCP 2.2.0

旧结论中的限制来自 LangChain adapter，而不是 Deep Agents 或 MCP 协议：

```text
langchain-mcp-adapters==0.3.2
└── mcp >=1.24.0,<2
```

新方案不在一个镜像中保留旧 adapter，所以约束消失：

```text
new app/worker image: deepagents + mcp 2.2.0
causal-mcp image: mcp 2.2.0 + causal algorithm dependencies
legacy image: langchain-mcp-adapters + mcp 1.x（仅用于旧版本对比/回退）
```

主程序只需要官方 MCP client，不需要用 adapter 将远端 MCP tools 动态转换为模型工具。模型看到的是本地 AlgorithmSpec tools；`McpAlgorithmExecutor` 内部调用 MCP 2.2 client。

LangChain 1.4 还提供 beta `langchain.mcp.MCPAdapter`，其 `langchain[mcp]` extra 使用独立 `fastmcp>=4.0.1,<5`。本轮不采用，原因是其 API 仍为 beta，且远端工具动态发现会弱化 `AlgorithmSpec` 的唯一事实源、可信参数隐藏和 Adapter 硬校验边界。后续稳定后可做技术替换，但不能改变领域工具契约。

### 4.3 旧版本与 v2 服务端的兼容边界

“兼容”必须分三层理解：

| 层次 | 结论 | 约束 |
|---|---|---|
| Python 包 ABI/API | 不兼容共存 | 新旧镜像分别安装；禁止跨环境 import |
| MCP 线协议 | 服务端向后兼容 | v2 server 同时支持最新及更早协议；v1 client 走旧修订 |
| 本项目领域契约 | 必须自行验证 | 对协议发现/协商（v2 现代路径 `server/discover`，legacy fallback 才 `initialize`）、`list_tools`、`call_tool`、错误映射、超时、取消和无 session 调用做集成测试 |

新系统主程序和服务端都走 v2；上述 v1→v2 兼容只用于将来需要让冻结旧镜像连接新 MCP 服务时的可选测试，不是新架构的日常链路。服务端使用 v2 无状态 HTTP。首版可设置 `json_response=True`，因为本项目不依赖 server-to-client sampling、elicitation 或 MCP progress backchannel；用户可见进度统一来自 Job 事件与 Action Ledger。若以后引入这些能力，必须重新评估 JSON-only 响应。

### 4.4 升级门禁

阶段 0 必须产出独立锁文件并通过：

1. 全量 requirements 解析和 clean install；
2. `langchain_openai.ChatOpenAI` 对 DeepSeek Responses API 的最小 function-call 与多 function-call 测试；
3. Deep Agent `ToolStrategy` 的成功、schema 失败重试和 `structured_response` 读取测试；
4. 父图 PostgreSQL checkpoint 恢复测试；
5. MCP 2.2 client 到 MCP 2.2 server 的真实 HTTP 契约测试；
6. 当前 RAG、Web、Job、SSE 和管理员关键回归。

任一项失败时不得替换旧版本镜像或进入真实 Job 验收；应在新分支和隔离环境内先解决兼容簇。

---

## 5. 目标运行时分层

### 5.1 外层 LangGraph

外层图保留现有 `agent` 路由节点与 `fold`。进入因果分析分支后，目标链路是：

```text
agent
  -> fold/admission
  -> deep_agent
  -> finalization_gate
  -> report
```

`create_deep_agent()` 返回一个 `CompiledStateGraph`。在本项目中它作为父图的 `deep_agent` 节点/嵌套 graph 运行，但不称为 Deep Agents `subagent`：不配置 `subagents=`，也不向模型提供 `task` 工具。该 graph 在 worker slot bootstrap 时编译一次，调用时只接收当前 Job 的 state projection 和 runtime context。

新路径不再保留当前父图中的显式 `mcp_tool_node`。内层 Deep Agent 自己的标准模型/工具循环负责执行模型可见的本地 function tools；算法 Tool 内部依次经过 Adapter、`AlgorithmExecutor` 和私有 MCP Client。也就是说，“MCP”从父图节点名降为领域工具的内部传输实现。旧 `mcp_tool_node` 只属于冻结的旧版本，不应复制到新图；测试和事件也不得同时把 Deep Agent 内部 tools node 与旧 `mcp_tool_node` 当成两个算法执行阶段。

内层 graph 不自行创建 checkpoint 数据库；配置 `checkpointer=None`，继承父图的 PostgreSQL checkpointer，并使用稳定 namespace，例如 `deep_agent_v1`。namespace 必须包含实现代际，不能包含随机值。

父图进入和离开子图时使用显式转换函数：

```python
def to_deep_agent_input(state: CausalAgentState) -> DeepAgentState: ...
def from_deep_agent_output(state: DeepAgentState) -> ParentStateUpdate: ...
```

禁止将整个父状态原样传入，也禁止 Deep Agent 返回任意 key 覆盖 Job 状态。

### 5.2 Parent State、DeepAgentState 与 runtime context

三者的差别是生命周期和可变性，不是字段长得是否相似：

| 对象 | 是否可变 | 是否 checkpoint | 用途 |
|---|---|---|---|
| `CausalAgentState` | 节点可更新 | 是 | 父图完整业务状态、路由、文件摘要、报告和 Job 执行进度 |
| `DeepAgentState` | 内层 Agent/Tool 可更新 | 是，由父图继承 | `messages`、算法结果、Action Ledger、证据和 `structured_response` |
| runtime context | 单次 graph run 内只读 | 否 | execution guard、MCP/RAG/Web executor、认证身份和运行依赖 |

官方定义中，State 是运行中变化的短期状态；runtime context 是 dependency injection。执行对象和连接不能放入可序列化 State。建议扩展当前 `AgentRunContext`：

```python
@dataclass(frozen=True)
class AgentRunContext:
    execution_guard: ExecutionGuard
    trusted_identity: JobExecutionIdentity
    algorithm_executor: AlgorithmExecutor
    rag_executor: EvidenceRetriever
    web_executor: WebEvidenceSearcher
```

`trusted_identity` 可以包含与 State 同名的 ID，但来源和权威性不同：State 中的 ID 用于恢复和展示关联，context 中的身份由 worker claim 产生，只供权限/fencing 检查。两者不一致时拒绝执行。context 只存在于当前运行进程，不能被模型序列化或写入 checkpoint。

自定义内层 State 必须继承官方 `deepagents.graph.DeepAgentState`，保留其 `messages` reducer，再增加本项目字段；不能用一个无关的 TypedDict 替换官方基础 State。

### 5.3 Deep Agents profile 与工具裁剪

Deep Agents 默认注入文件工具，并默认增加 general-purpose subagent。当前版本没有一个 `disable_default_tools=True` 的 `create_deep_agent()` 参数；`tools=` 又只会追加领域工具。对固定的 `deepagents==0.7.13`，更直接的裁剪方式是向 `create_deep_agent()` 传入自定义 `FilesystemMiddleware(tools=["read_file", "edit_file"])`：框架按 middleware name 替换默认 filesystem middleware，工具 allowlist 从来源上不创建 `write_file`、目录检索、delete 或 execute。`HarnessProfile` 仍只负责关闭默认 general-purpose subagent：

```python
profile = HarnessProfile(
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
)
register_harness_profile("openai:<deepseek-model-id>", profile)

deepseek_model = ChatOpenAI(
    model=settings.DEEP_AGENT_MODEL,
    base_url=settings.DEEP_AGENT_BASE_URL,
    use_responses_api=True,
    profile={
        "max_input_tokens": settings.DEEP_AGENT_CONTEXT_WINDOW_TOKENS,
    },
)

backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/memories/": StoreBackend(
            namespace=trusted_memory_namespace,
        ),
    },
)

filesystem_middleware = FilesystemMiddleware(
    backend=backend,
    tools=["read_file", "edit_file"],
)

filesystem_permissions = [
    FilesystemPermission(
        operations=["write"],
        paths=[
            "/memories/preferences.md",
            "/memories/research_background.md",
        ],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/**"],
        mode="deny",
    ),
]

deep_agent = create_deep_agent(
    model=deepseek_model,
    tools=build_domain_tools(),
    subagents=[],
    memory=[
        "/memories/preferences.md",
        "/memories/research_background.md",
    ],
    backend=backend,
    store=postgres_store,
    middleware=[filesystem_middleware],
    permissions=filesystem_permissions,
)
```

Profile 应按实际由 `ChatOpenAI` 解析出的 provider/model key 注册，并做启动测试，避免因 DeepSeek 使用 OpenAI-compatible client 而未命中。产品决策已确认保留 Deep Agents 内置 `read_file` 与 `edit_file`：`read_file` 用于读取当前 Job 的大 Tool 结果、summary 淘汰历史和原始算法输出，也可读取 memory 文件；`edit_file` 用于更新 `/memories/` 下由可信 bootstrap 预先创建的两份 memory 文件。`FilesystemMiddleware.tools` 是允许列表，不是提示词约束；其快照必须证明 `write_file`、`delete`、目录检索和 execute 根本没有注册。默认 general-purpose subagent 被 Profile 关闭后不出现 `task`；`tools=` 只传算法与 RAG/Web evidence 工具，不再额外增加记忆工具。

底层只组合官方 `StateBackend` 与 `StoreBackend`，不使用宿主 `FilesystemBackend`。`/memories/` 由 `StoreBackend` 路由并按可信用户身份隔离；其余 Deep Agent 虚拟文件落在当前运行 State，并通过父图 checkpoint 持久化。Deep Agents 0.7.13 已弃用把 runtime factory 直接传给 `backend=` 的旧写法，因此使用预构造 backend 对象。`trusted_memory_namespace(runtime)` 必须从 worker 在 graph invocation 时绑定的可信身份提取 canonical `user_id`，而不是读取模型 Tool 参数；0.7.13 官方示例使用 `runtime.server_info.user.identity`，本项目是否改用自定义 `context_schema` 字段必须在阶段 0 以实际 LangGraph Runtime 对象验证后写入 helper，本文不提前硬编码一个尚未证明存在的属性。Harness Profile 和 model profile 都是 beta API，因此锁定 0.7.13，并用模型可见 tool-name 快照、backend 路由测试和 model profile 启动断言防止升级漂移；工具快照应恰好允许领域工具、evidence 工具、`read_file`、`edit_file` 和结构化输出工具。

`edit_file` 的路径权限已经冻结为上面的两条 first-match 规则：先精确允许 `/memories/preferences.md` 与 `/memories/research_background.md`，再用 `/**` 拒绝模型写入其他虚拟路径。0.7.13 对未命中规则的操作默认允许，因此兜底 deny 不可省略，顺序也不能颠倒。该权限只约束内置 filesystem Tool；可信 Adapter 直接调用 backend API 不经过模型 Tool permission，因此仍能写 raw 文件。raw 文件继续以哈希和不可变 `AlgorithmResult` 为权威，模型不能通过 `edit_file` 修改它们。除这组 permission 外，本轮不新增其他长期记忆防御措施；查看、删除、保留期和并发编辑仍为暂缓项。

### 5.4 Backend、自动 offload 与 summarization

这里的 backend 是 Deep Agents 的虚拟文件存储接口，不等于固定的本地目录或固定数据库。本方案的 `CompositeBackend` 按路径分流：

| 虚拟路径 | backend | 实际持久化位置与生命周期 |
|---|---|---|
| `/memories/**` | `StoreBackend` | 通过 LangGraph Store 写入 `AsyncPostgresStore`，即现有 PostgreSQL 中的 Store 表，跨 thread/Job 保留 |
| `/large_tool_results/**` | 默认 `StateBackend` | 写入内层 graph State，并随 Job 的 PostgreSQL checkpoint 保存；不进入长期记忆 Store |
| `/conversation_history/**` | 默认 `StateBackend` | summarization 淘汰的完整消息历史随 checkpoint 保存；不跨 Job 作为用户记忆复用 |
| `/raw_algorithm_results/**` | 默认 `StateBackend` | Adapter 写入的原始算法结果随当前 Job checkpoint 保存；只在 checkpoint 生命周期内可复核，不进入长期记忆 Store |

因此，大 Tool 结果自动 offload 后不是直接写入业务 MySQL，也不是写入 `/memories/`；它首先成为当前 Job State 中的虚拟文件，再由外层 checkpointer 持久化。模型可以通过 `read_file` 按路径和分页参数读取 offload 内容，但领域 Adapter 仍须先生成有界 `AlgorithmResult` 和 diagnostics，不能强迫模型扫描完整 raw payload 才能判断成功与否。原始大 payload 不进入 ToolMessage，而是由可信 Adapter 通过 backend API 程序化写入 `/raw_algorithm_results/{invocation_id}/{result_index}.json`；模型不需要 `write_file` 才能获得该文件。

`StateBackend` 的“虚拟文件”仍是 graph State 的一部分，最终会进入 PostgreSQL checkpoint，因此这种转存主要解决 schema 解耦、上下文膨胀和分段读取，不会神奇地减少 checkpoint 总字节数。raw 文件必须使用 canonical JSON、显式序列化版本、大小上限和 SHA-256，禁止 pickle。0.7.13 的 `StateBackend.upload_files()/aupload_files()` 会经 Pregel 内部 `files` channel 排队更新，并不是 Adapter 自己构造的同一个 `Command` 字段；因此本文只冻结一致性目标，不宣称具体 API 已经原子。Adapter 必须先检查 upload response，再回读并校验大小/hash，随后才允许发布 `AlgorithmResult` 引用和 Ledger terminal update。阶段 0 集成测试需要证明这些更新在一次 Tool graph step 的 checkpoint 中一致可见；若框架无法保证单 checkpoint 原子性，则容忍“只有未引用的孤儿 raw 文件”，但绝不允许“已发布结果指向缺失或 hash 不符的 raw 文件”，恢复时用相同 invocation 重算并覆盖该路径。checkpoint cleanup 会一起清除它，所以这不是跨保留期审计存储。

Deep Agents 0.7.13 默认启用 `SummarizationMiddleware`，并使用传给 `create_deep_agent()` 的同一个 `deepseek_model` 生成摘要；MVP 不另配第二个摘要模型。需要配置的“模型大小”实际是模型 profile 的 `max_input_tokens`，否则 OpenAI-compatible DeepSeek 模型可能没有准确 profile，框架会回退到固定 token/message 默认值。启动时必须断言：

```text
deepseek_model.profile.max_input_tokens == DEEP_AGENT_CONTEXT_WINDOW_TOKENS
summary trigger = 85% of max_input_tokens
summary keep = 10% of max_input_tokens
summary trim_tokens_to_summarize = 4000
```

这些比例沿用 0.7.13 的 model-aware 默认值，阶段 0 仍需用真实 DeepSeek Responses API 验证 token 统计、触发时机和摘要后 Tool call 关联不被破坏。若以后要使用独立、较小的摘要模型，应替换默认 `SummarizationMiddleware` 并单独验证，不在本轮隐式加入。summary 淘汰的原始历史保存于 `/conversation_history/`，Agent 在摘要不足时可通过 `read_file` 定向回读；这项能力必须验证分页、token 上限和 checkpoint 恢复后的路径稳定性。

### 5.5 结构化终态

Deep Agent 使用：

```python
response_format=ToolStrategy(FinalAnalysisDecision)
```

`state["structured_response"]` 是 `deep_agent.ainvoke()/astream()` 完成后返回的内层 graph 最终 State 字段，不是 runtime context。LangChain 根据 `ToolStrategy` 捕获并校验模型的结构化输出后写入该字段；外层 `deep_agent` 节点再把它投影到父 State。普通消息中的 JSON、markdown 或自然语言“最终结论”均不作为结构化终态。Deep Agent 不再拥有 `finalize_analysis` 普通工具。

### 5.6 数据准入、预处理与外层 fold

数据处理必须保留“两层准入”，不能把所有失败都交给模型解释：

| 层次 | 处理内容 | 失败去向 |
|---|---|---|
| 外层 admission | 文件存在/归属/冻结身份、可解析性、空行列、基础 schema、安全和资源硬上限 | 用户可补充的数据缺口进入现有 `fold`；权限、损坏和内部错误直接失败 |
| Algorithm Adapter | 该算法特有的类型、编码、尺度、缺失、样本量和统计假设 | 返回 `invalid_input` 或 `not_ready`，Deep Agent 可选择其他能力 |

首版自动预处理只允许可逆、确定性、不删除样本且不改变研究问题的操作，例如确定性类型规范化、类别编码和算法明确要求的标准化。删除缺失行、插补、异常值删除、目标变量变更或 estimand 变换不得默认执行。

每次 Adapter 记录 `preprocessing_recipe`、输入摘要、算法版本和输出 provenance。派生数据只在调用内存中存在，不提供下载、不写 checkpoint、不成为跨 Job 权威数据；恢复时依据冻结输入和 recipe 幂等重算。

哪些缺口允许 interrupt 应实现为外层稳定错误码白名单，而不是通过错误消息文本判断：

```text
USER_INPUT_FILE_MISSING
USER_INPUT_SCHEMA_AMBIGUOUS
USER_INPUT_REQUIRED_FIELD_MISSING
```

权限错误、输入归属冲突、哈希不一致、超限和系统错误不在白名单中。

### 5.7 多租户、长期偏好与用户产物

Job 隔离仍以 `user_id + session_id + job_id + attempt_count + lease_epoch` 为权威组合。MCP context、数据库查询、checkpoint namespace 和 Store namespace 必须从可信上下文取得，不能接受模型覆盖。

MVP 的跨会话记忆只保存两份虚拟 Markdown 文件：稳定偏好 `/memories/preferences.md`，以及用户明确要求后续复用的研究背景 `/memories/research_background.md`。`memory=[...]` 使官方 memory middleware 在模型调用前把文件内容加载到系统上下文；模型在对话热路径中通过内置 `edit_file` 更新文件，因此不新增 `remember_user_context`、`forget_user_context` 等自定义工具。模型写权限由第 5.3 节的 `FilesystemPermission` 精确限制到这两个文件，其他虚拟路径写入一律拒绝。由于 `edit_file` 只能修改已存在文件，可信 bootstrap 必须在首次使用前执行“仅缺失时创建”的初始化，绝不能在每次 Job 启动时用空模板覆盖已有记忆；如果 `StoreBackend`/Store 组合不能提供安全的 create-if-absent，阶段 0 必须选择带事务/CAS 的初始化位置。并发编辑冲突仍属于本轮明确延后的数据生命周期边界。系统提示必须要求：只有明确、可长期复用的信息才写入；研究背景必须由用户主动要求保存；不得保存文件正文、数据画像、算法结果、因果图、Tool 输出或模型推测。

官方 backend 组合已在第 5.3 节给出。`StoreBackend.namespace` 调用 `trusted_memory_namespace(runtime)`，helper 从 worker claim 后绑定到实际 LangGraph Runtime 的可信用户身份读取 canonical `user_id`，不是模型参数；具体是官方 `server_info.user.identity` 还是本项目 `context_schema` 字段，必须由阶段 0 Runtime Spike 决定并做启动断言。路径分流和实际存储结构见第 5.4 节。

```python
backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/memories/": StoreBackend(
            namespace=trusted_memory_namespace,
        ),
    },
)
```

`StoreBackend` 是虚拟文件到 LangGraph Store 的适配层，Store 本身是持久化接口而不是固定数据库。生产实现使用 `AsyncPostgresStore`，复用现有 `postgres-checkpoint` 服务和 `CHECKPOINT_POSTGRES_*` 连接配置；开发/生产默认数据库仍为 `causalagent_checkpoints`，各环境继续使用自己的现有数据卷（开发为 `postgres_checkpoint_data`，生产和 staging 保持各自隔离名称）。该服务名是历史命名，实施本方案时不改名，以免无收益地联动 Compose、配置、健康检查和运维文档；未来独立基础设施重构若确有必要，可再统一改为 `postgres-langgraph`。

同一 worker 进程可以让 checkpointer 与 `AsyncPostgresStore` 复用同一进程级 PostgreSQL 连接池，但二者使用不同逻辑对象和表：Store 的 `store`、`store_migrations` 不属于 checkpoint 清理范围。`Database.bootstrap` 在现有 checkpoint setup 之后调用 `AsyncPostgresStore.setup()`，readiness 同时验证 checkpointer 和 Store；checkpoint cleanup 只能清理 Job checkpoint，绝不能清理用户记忆。MVP 不启用向量检索/pgvector，不新增记忆 UI、查看接口或独立审计记录。

首版 UI 不新增内部计划或 Tool 时间线。用户可见产物固定为最终报告、至多一张由 `primary_result_ref` 直接引用的标准化主图，以及现有预处理图表。没有有效算法结果时不显示主图，但仍可生成证据型/失败说明型正常报告；内部 Ledger 只向报告提供事实，不直接扩张页面结构。

---

## 6. 状态与领域 schema

以下为实现级骨架；字段名在 migration 和 API 实施前应以同名 Pydantic model 固定，并生成 JSON Schema 快照测试。

### 6.1 AlgorithmSpec

```python
class AlgorithmSpec(BaseModel):
    capability_id: str                 # 稳定机器标识，如 causal.pc
    version: str                       # 领域契约版本，不等同包版本
    tool_name: str                     # 模型可见名称
    public_name: str                   # 报告显示名称
    description: str
    model_input_schema: type[BaseModel]
    requires: frozenset[str]
    produces: frozenset[str]
    assumptions: tuple[str, ...]
    result_contract: type[BaseModel]
    default_timeout_seconds: int
    concurrency_key: str
```

`AlgorithmSpec` 只由主程序维护和发布。worker bootstrap 从受版本控制的 Python 定义编译模型可见的本地 LangChain function tools，并与 Adapter 实例一起注册：`registry.register(spec=PC_SPEC, adapter=PcAdapter())`。不再使用 `adapter_key` 字符串再查一层白名单；Registry entry 本身就是经代码审查的静态绑定。启动时计算 canonical JSON 的 SHA-256 `spec_digest`，写入日志、Ledger 和结果 provenance。禁止从数据库、MCP `list_tools()` 或用户输入加载任意 import path 或动态模型工具。

`causal-mcp` 不复制完整 Spec，也不负责 tool description、模型参数 schema 或报告公开名；它只暴露固定的内部执行工具并维护 runner registry，例如 `execute_algorithm(capability_id, ...)` 与 `capability_id -> runner`。主程序发给 MCP 的内部命令至少包含 `capability_id`、`capability_version` 和 `spec_digest`，服务端对支持的 runner/契约版本做硬校验，不支持则返回稳定的 `MCP_CAPABILITY_VERSION_UNSUPPORTED`。`/ready` 或启动握手可以返回服务能力/版本摘要用于 fail-fast，但该摘要只做兼容性检查，绝不能据此动态注册模型工具。这样避免双份 Spec 漂移，同时仍允许 MCP 镜像拒绝错误的主程序发布组合。

首版条目为：

| capability_id | tool_name | requires | produces | 默认 Tool 并发 | 超时 |
|---|---|---|---|---:|---:|
| `causal.pc` | `causal_pc` | `tabular_dataset` | `standardized_graph`, `diagnostics` | 2 | 300s |
| `causal.olc` | `causal_olc` | `tabular_dataset` | `standardized_graph`, `diagnostics` | 1 | 600s |
| `causal.direct_lingam` | `causal_direct_lingam` | `continuous_tabular_dataset` | `standardized_graph`, `diagnostics` | 1 | 300s |

各算法的精确参数、缺失值、变量类型、样本量和统计假设必须从现有 runner 逐项提取后写进 Spec 测试；本表不替代算法契约核对。

### 6.2 模型输入与可信输入分离

模型可见的本地 LangChain tool 参数只声明科学选择，例如 alpha、目标变量或算法超参数。Job 身份、冻结文件身份、lease、连接和服务凭据本来就不属于该函数的模型参数，因此不在 schema 中额外声明“隐藏字段”；Adapter 通过 `ToolRuntime`/runtime context 和已冻结的 `analysis_job_inputs` 取得它们并形成 `McpInvocationContext`。

内部 MCP tool 可以把 `trusted_context` 与 `signature` 定义为服务间参数，因为该远端 schema 不会注册到 Deep Agent；`AlgorithmExecutor` 负责填充，模型没有构造或覆盖它们的入口。

### 6.3 结果与 Ledger

```python
AlgorithmResultStatus = Literal[
    "valid",
    "invalid_input",
    "not_applicable",
    "not_ready",
    "execution_failed",
    "timed_out",
]

class AlgorithmResult(BaseModel):
    result_ref: str
    invocation_id: str
    provider_call_id: str
    capability_id: str
    capability_version: str
    status: AlgorithmResultStatus
    standardized_graph: StandardizedGraph | None
    diagnostics: Diagnostics
    warnings: list[SafeWarning]
    raw_result_ref: str | None
    raw_result_sha256: str | None
    raw_result_size_bytes: int | None
    raw_result_serialization_version: str | None
    provenance: AlgorithmResultProvenance

class ActionAttempt(BaseModel):
    retry_ordinal: int
    revision: int
    status: Literal[
        "queued", "running", "succeeded", "failed", "timed_out",
        "not_ready", "canceled", "discarded"
    ]
    started_at: datetime | None
    finished_at: datetime | None
    safe_error_code: str | None

class InvocationRecord(BaseModel):
    invocation_id: str
    response_identity: str
    response_identity_source: Literal[
        "provider_response_id", "message_execution_id"
    ]
    provider_response_id: str | None
    provider_item_id: str | None
    provider_call_id: str
    tool_name: str
    final_status: Literal[
        "pending", "succeeded", "failed", "timed_out", "not_ready",
        "canceled", "discarded"
    ]
    result_ref: str | None
    attempts: dict[int, ActionAttempt]

class ProjectDeepAgentState(DeepAgentState):
    data_profile: DataProfile
    algorithm_results: Annotated[dict[str, AlgorithmResult], merge_algorithm_results]
    action_ledger: Annotated[dict[str, InvocationRecord], merge_action_ledger]
    rag_evidence: Annotated[dict[str, EvidenceResult], merge_evidence_results]
    web_evidence: Annotated[dict[str, WebEvidenceResult], merge_evidence_results]
    structured_response: FinalAnalysisDecision | None
```

`AlgorithmResultStatus` 的边界必须稳定：`invalid_input` 只表示数据或参数不满足已声明的输入契约；`not_applicable` 表示输入合法但算法的科学适用条件不成立；`not_ready` 表示依赖 artifact 尚未可用；算法进程、协议或 Adapter 失败，以及远端 payload 缺字段、维度错误、引用未知节点等**输出契约破坏**统一归入 `execution_failed` 并携带安全错误码；`timed_out` 只表示超过本次算法 deadline。不能把坏输出命名为 `invalid_input`，否则会把服务端缺陷错误归因给用户数据。

不再使用含义不明的通用 `merge_by_id`。三个 reducer 的 key 和更新规则分别冻结：

- `merge_algorithm_results` 以 `result_ref` 为 key；结果一旦形成即不可变，完全相同的重放可忽略，同 key 内容不同则抛出一致性错误；
- `merge_action_ledger` 先以 `invocation_id` 定位逻辑调用，再以 `retry_ordinal` 定位一次实际尝试；每个 attempt 的 `revision` 从 `queued → running → terminal` 单调递增，旧 revision 不能覆盖新 revision，相同 revision 内容不同则报错；
- `merge_evidence_results` 以各自的 `evidence_ref` 为 key，并采用与 AlgorithmResult 相同的不可变重放规则。

一个 Job 只有一个 Action Ledger，但其中可包含多个以 `invocation_id` 为 key 的 `InvocationRecord`；每个逻辑调用又包含一个或多个 attempt。Ledger 必须记录每一次实际尝试，包括最终成功前发生的连接失败、容量拒绝、算法错误、超时和恢复重算。`final_status` 是 invocation 汇总状态，不能覆盖 `attempts` 历史。不能使用 list append 作为执行事实 reducer，否则 checkpoint replay 会重复记录；也不能只在最终失败时记录，否则无法区分“从未开始”“失败后成功”和“响应丢失后重算”。

实现口径补充（2026-09-14）：单次 Tool graph step 只向 checkpoint 提交该 attempt 的 terminal revision，terminal record 同时保留 `started_at/finished_at`，并与 ToolMessage、AlgorithmResult 或 evidence 通过同一个 `Command` 更新。这里的“只提交 terminal”是对 checkpoint 写放大的约束，不是删除失败尝试；后续同一 invocation 的重试仍以更高 `retry_ordinal/revision` 单调合并并保留历史。queued/running 如需用户可见实时进度，由事件适配器发出，不作为额外 checkpoint revision。可预期算法失败必须转换为 `AlgorithmResult.status=execution_failed`，同时写入 failed Ledger 后返回模型继续分析；租约失效、取消和运行资格撤销仍属于控制流异常，不得伪装成普通算法失败。

### 6.4 FinalAnalysisDecision

```python
class ResultAssessment(BaseModel):
    result_ref: str
    disposition: Literal["primary", "supporting", "discarded"]
    rationale: str

class FinalAnalysisDecision(BaseModel):
    outcome: Literal[
        "evidence_only", "algorithm_supported", "no_valid_algorithm"
    ]
    primary_result_ref: str | None
    result_assessments: list[ResultAssessment]
    conflict_status: Literal["none", "resolved", "unresolved"]
    conflicts: list[ScientificConflict]
    revision_proposals: list[RevisionProposal]
    selection_rationale: str
    confidence: Literal["low", "medium", "high"]
    confidence_basis: list[str]

FinalizationStatus = Literal["valid", "degraded"]
```

`revision_proposals` 只能用于报告，schema 中不得含可替换 `standardized_graph` 的字段。

上述状态分为四层，禁止再复用 `analysis_failed` 表达不同含义：MySQL Job 生命周期继续使用 `queued/running/waiting_input/succeeded/failed/canceled`；`FinalAnalysisDecision.outcome` 只表达分析内容；`AlgorithmResult.status` 只表达单个算法结果；`FinalizationStatus` 只表达最终一致性校验质量。`finalization_status=valid|degraded` 是程序生成的报告元数据，不属于模型提交的 outcome。单个算法或 attempt 超时使用 `timed_out`，不新增同名 Job 终态。`finalization_degraded` 最终仍生成报告并把 Job 标记为 `succeeded`，因为 Job 成功语义是“受控流程产出可交付报告”，但报告必须明确它没有通过完整 finalization，一律不展示未经 Gate 验证的主图。

MVP 不建立独立结果表、结果查询 API 或跨 Job 结果引用。`result_ref` 是当前 Job State 内的稳定引用，格式为 `"{invocation_id}:{result_index}"`；它不包含裸 `job_id` 或供应商 response id，模型只能引用上下文中已有的 `result_ref`。外层 Gate 再校验引用存在且属于当前 Job/attempt/lease。内部跨服务日志使用 `invocation_id` 对账，不把内部 Job 身份暴露为模型结果引用。

---

## 7. AlgorithmSpec、Registry 与 Adapter

Deep Agent 编排与其工具分成两个独立目录：

```text
Agent/deep_agent/
  graph.py
  state.py
  context.py
  profile.py
  finalization.py
  prompts.py
  memory.py
  postgres_store.py
Agent/deep_agent_tools/
  models.py
  algorithm_specs.py
  registry.py
  dependency_planner.py
  algorithm_tools.py
  algorithm_executor.py
  mcp_algorithm_executor.py
  rag_evidence_tool.py
  web_evidence_tool.py
```

`Agent/deep_agent/` 只负责 Deep Agent graph、状态、prompt、Profile、官方 memory backend/Store 装配和 finalization；`Agent/deep_agent_tools/` 独立负责所有领域模型工具、领域 Spec、Adapter 和执行接口，不包含自定义记忆工具。独立 MCP 服务仍在 `Agent/CausalAgentMCP/`，不归入 Deep Agent 目录。

Registry fail-fast 发生在 worker slot bootstrap：依赖和配置载入后、graph 编译前、worker readiness 变为 ready 和 claim Job 之前。MCP server 对自己的 runner registry 在其容器启动时另做一次服务端校验，并在 worker readiness 阶段与主程序声明的 capability/version 摘要核对。模块 import 本身不执行数据库或网络副作用。

worker bootstrap 校验：

1. `capability_id`、`tool_name` 唯一；
2. 每个 Spec 都与且只与一个 Adapter 实例静态绑定；
3. `requires`/`produces` 使用登记的 artifact type；
4. result contract 能生成稳定 JSON Schema；
5. 模型 tool schema 与 description 能正常生成；
6. timeout、并发 key 和版本均显式声明；
7. spec digest 在同一构建中稳定。

Adapter 的固定执行顺序为：

```text
解析模型参数
-> 检查 Job authority/fencing
-> 从 inputs 账本读取冻结数据身份
-> 算法专属预处理和硬假设校验
-> AlgorithmExecutor.execute()
-> 标准化结果
-> result_contract 硬校验
-> 更新 Action Ledger 和 AlgorithmResult
```

任何一步失败都返回结构化、安全的 ToolMessage；原始堆栈、SQL、文件正文、token 和内部路径只进入脱敏后的服务日志，不能进入模型上下文或 SSE。

---

## 8. 多 Tool call、依赖与并行

### 8.1 保留全部调用

DeepSeek Responses API 可能在一个模型 turn 返回多个 function calls，而且 `parallel_tool_calls` 不能用于关闭并行。新实现必须按 `response_identity + provider_call_id` 保留全部调用，其中 `response_identity` 优先使用真实 `provider_response_id`，仅在适配层丢失该字段时使用已持久化的本地 fallback；不能复用当前只取 `[0]` 的 adapter。LangChain `ToolMessage.tool_call_id` 只有在阶段 0 证明映射关系后，才可视为 `provider_call_id` 在消息协议中的对应字段。

每轮按以下步骤调度：

1. 将全部 function call 解析为 `PlannedToolCall`；
2. 依据 Spec 的 `requires`/`produces` 构建直接依赖 DAG；
3. 检测重复 call id、未知工具、环和同批不可满足依赖；
4. 对入度为 0 的调用按 Job semaphore 和 Tool semaphore 分批并行；
5. 每批结束后发布产物，再推进下一层；
6. 每个调用产生一条与原 call id 对应的 ToolMessage，包括 `not_ready` 和失败；
7. 部分失败不取消无依赖兄弟调用；依赖失败的后继返回 `not_ready`，不得猜测输入。

“同批 A 产生 B 所需 artifact”的情况必须建立 A→B 边，B 不在同一并发批执行。缺少任何生产者时直接 `not_ready(missing_artifacts=...)`。检测到环时整轮调度失败，但 Job 可把安全错误反馈给 Deep Agent 重新规划一次。

### 8.2 稳定 invocation identity

invocation identity 的作用是把模型的一次逻辑 function call、MCP 日志、ToolMessage、Ledger 和结果串在一起，不等于必须建立 invocation 数据表。不能只使用 `job_id:provider_call_id`：供应商只保证 call id 用于当前 response 内关联，不能把其跨 turn 唯一性当作本项目契约；同时也不应把裸 Job ID 编入将来可能进入模型或事件的结果引用。

MVP 使用确定性 UUIDv5。DeepSeek Responses API 的 response 顶层 `id` 是一次模型响应身份，function-call item 同时带 item `id` 和用于把 function output 配回调用的 `call_id`。本项目把 response 顶层 `id` 命名为 `provider_response_id`，把配对用的 `call_id` 命名为 `provider_call_id`；item 自身 `id` 只作为可选追踪字段 `provider_item_id`，不参与逻辑调用键。经 `ChatOpenAI` Responses 适配后，阶段 0 必须验证这三个协议字段分别落到哪些 `AIMessage`、`response_metadata`、`additional_kwargs` 或标准化 tool-call 字段，不能在验证前把某个 LangChain 字段名直接当作 DeepSeek 协议保证：

```text
provider_response_id = mapped DeepSeek response.id  # nullable if adapter drops it
message_execution_id = locally persisted fallback  # only when provider id is absent
response_identity = provider_response_id or message_execution_id
provider_call_id = mapped DeepSeek function_call.call_id
provider_item_id = mapped DeepSeek function_call.id  # optional trace only
logical_call_key = "{job_id}:{response_identity}:{provider_call_id}"
invocation_id = uuid5(CAUSAL_INVOCATION_NAMESPACE, logical_call_key)
result_ref = "{invocation_id}:{result_index}"
```

`CAUSAL_INVOCATION_NAMESPACE` 是代码中固定的项目 UUID 常量，不是 secret。`tool_call["id"]` 只有在阶段 0 证明它确实映射 DeepSeek `call_id` 后才能赋给 `provider_call_id`；`AIMessage.id` 也可能是 LangChain 本地 message identity 或某个 output item identity，不能未经验证就当作 response 顶层 `id`。当前旧 `normalize_mcp_tool_call_message()` 会重建 `AIMessage` 而不复制 `id/response_metadata`，且仓库里还存在 `planner-{tool}-1`、`rag_enrichment_search_1` 等手工 call id；这些值不满足新路径的 Job 全局调用身份要求，所以新 Deep Agent 链路不得复用旧 normalizer 或静态 id。若真实 DeepSeek/ChatOpenAI 适配未保留稳定 response id，规范化层必须在模型节点输出提交 checkpoint 前生成并持久化一个本地 `message_execution_id`，将其作为 `response_identity` fallback，不能伪装成 `provider_response_id`，也不能按消息下标临时计算。Ledger 通过 `response_identity_source` 明确来源。相同 checkpoint 中的同一逻辑调用在恢复后得到相同 `invocation_id`；模型在后续 turn 主动再次调用同一算法时，因为 response/message execution id 不同而得到新的 invocation。传输重试或未提交结果的恢复重算复用同一 `invocation_id`，只递增 `retry_ordinal`，所有 attempt 都保留在 Ledger。

若 worker 在 MCP 已算完但 Tool 节点 checkpoint 尚未提交时崩溃，恢复后允许重新执行这次业务结果只读的算法。这是明确接受的 at-least-once 计算语义：可能多算，但不会重复写算法业务结果。当前 `require_frozen_file_for_job()` 会更新文件 `last_accessed_at/access_count`，所以重算可能增加访问计数；该计数按“实际发生过的访问”解释，不作为 Job 次数、幂等判断或算法成功依据。现有 execution guard 和 lease fencing 在调用前后校验，旧 worker 的迟到结果不能合并进 State。

未来若加入写型 Tool、付费且不可重复的外部调用，或者必须精确区分“已执行但响应丢失”，再把这一 identity 提升为数据库幂等记录；MVP 不提前承担该复杂度。

---

## 9. MCP 服务实现

### 9.1 进程与网络拓扑

新增独立 `causal-mcp` 镜像与 Compose service：

```text
worker --private Streamable HTTP--> causal-mcp
causal-mcp --strong read----------> MySQL primary
causal-mcp --CPU submit-----------> bounded process pool
```

服务只加入应用内部网络，不设置宿主机 `ports:`。worker 通过内部 DNS 访问，例如 `http://causal-mcp:8080/mcp`。生产环境使用单 ASGI worker/容器，水平扩展由 Compose/Kubernetes 副本承担；不要在一个容器中启动多个各自拥有进程池的 ASGI worker，否则实际 CPU 进程数会乘倍。

MCP app 使用 SDK 暴露的 `streamable_http_app()` 挂载到受控 ASGI app。`/health` 只返回进程存活，`/ready` 检查配置、MySQL 强读和执行池可提交性；两者不得泄露依赖版本、队列内容或数据库详情。

### 9.2 鉴权与签名上下文

私有网络不是身份验证。首版采用两层校验：

- HTTP `Authorization: Bearer <service-token>`，只识别 worker 服务；
- worker 从可信 runtime 构造 `McpInvocationContext`，使用共享密钥做 HMAC-SHA256 签名；`causal-mcp` 验签后仍要强读数据库复核业务身份与有效 lease。

签名 canonical payload：

```python
class McpInvocationContext(BaseModel):
    invocation_id: str
    job_id: str
    session_id: str
    user_id: int
    attempt_count: int
    lease_epoch: int
    worker_id: str
    input_snapshot_digest: str
    issued_at: datetime
    expires_at: datetime
    key_id: str
```

`job_id` 和 `session_id` 使用字符串没有问题：当前 MySQL 列都是 `VARCHAR(36)`，应用也以带连字符的 UUID 字符串传递它们。这里的 `str` 不是“接受任意字符串”；Pydantic validator 必须用 `UUID(value)` 校验并重新序列化为小写、带连字符的 canonical UUID，拒绝空白、非 UUID、超长值和不同文本形式。这样既与现有数据库/API 类型一致，也能保证 HMAC canonical payload 在 worker 与 MCP 服务端完全相同。`user_id` 仍为数据库整数。

Bearer token 放在 transport header。逐调用的 `trusted_context` 与 `signature` 作为内部 MCP `call_tool` 参数传输，而不是依赖动态逐请求 header；它们只存在于服务间 MCP schema，不进入模型可见的本地 function tool schema。服务端检查时间窗口、key id、恒定时间签名比较、Job 当前 owner 与 lease_epoch、input 归属和冻结状态。密钥从 secret 注入，支持 current/previous 两把 key 的滚动窗口；日志只记录 key id。MVP 的只读计算允许相同签名请求在有效期内重放，不额外建立 nonce 数据库。

### 9.3 HTTP 版本、Client 分层与生命周期

MCP SDK 版本、协商出的 MCP 协议修订和底层 HTTP 版本是三件不同的事。`mcp==2.2.0` 的生产 transport 选择 Streamable HTTP；首版底层明确使用 HTTP/1.1，不安装或开启 `httpx2[http2]`。`httpx2` 虽支持 HTTP/2，但默认未启用；当前 SDK 的默认 ASGI 服务组合也不要求 HTTP/2。HTTP/1.1 下并发长请求由连接池使用多条 keep-alive 连接承载，因此连接上限必须不少于允许的算法并发，不能指望在一条 HTTP/1.1 连接上复用多个并发响应。未来只有真实压测证明连接数量成为瓶颈，并且 ASGI server/proxy/client 全链路支持 HTTP/2 时，才单独升级。

这里的两个 Client 处在不同层：

| 对象 | 职责 | 不负责 |
|---|---|---|
| `mcp.Client` | 进入 async context 时连接并协商协议，维护 MCP request id，执行 `call_tool()`，解析 `structured_content`/`is_error`；一个池成员最多承载配置的 `K` 个在途调用 | 不承载 Job 权威身份，不决定算法 CPU 并发，不替代 checkpoint |
| `httpx2.AsyncClient` | HTTP/1.1 连接池、keep-alive、Bearer header、连接/读写/pool timeout | 不理解 MCP tool、ToolMessage、Action Ledger 或结果 schema |

每个 worker **操作系统进程**在 bootstrap 中创建一个进程级 `httpx2.AsyncClient` 和一个长期 `McpClientPool`。池有 `N=CAUSAL_MCP_CLIENT_POOL_SIZE` 个编号成员；每个成员拥有独立的长期 `mcp.Client`/transport context，并有 `K=CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT` 个逻辑调用容量。这里必须把“Client 成员”与“MCP session”分开：协商出的协议或服务端部署若不建立有状态 session，`mcp_session_id` 合法为空；池化仍然复用 Client 生命周期、连接池和请求处理状态，但不能为了追踪而伪造 session id。成员进入 readiness 前分别完成连接、协议协商和 capability/version 校验。所有 Tool call 都由池自动选择健康、非 draining、`inflight < K` 且 `inflight / K` 最小的成员，同负载时 round-robin；选择与占用必须在同一个 `asyncio.Lock/Condition` 临界区完成，不能让调用方指定固定成员或把 Job 粘到某个 session。

同一套实现通过参数支持两种部署方式，无需另设 mode flag：

| 方式 | 配置示例 | 优点 | 代价 |
|---|---|---|---|
| A：成员级隔离 | `N=4, K=1` | 每个成员同时只有一个调用，transport 故障影响面最小，定位清楚 | 长期 Client/transport context 更多；有 session 时 session 也更多 |
| B：成员内复用 | `N=2, K=2` | 用较少长期 Client 承载相同客户端并发，资源利用更高 | 同一成员 transport 损坏时，最多影响该成员的 `K` 个兄弟调用 |

首版默认 `N=2, K=1`，与单 Job 并行 2 和服务端进程数 2 对齐；这是偏向隔离的起点，不代表 B 不受支持。切换到 B 只改部署配置，并在 worker drain 后重启生效，不做运行时热 resize。不同 MCP Client 共享底层 HTTP 连接池，但不共享 MCP request 状态；协议建立 session 时也不共享 session 状态。不同 worker 进程各有一套池，不跨进程共享 Python 对象。

实现时由进程级生命周期持有共享 HTTP Client，每个编号成员再持有可独立关闭和重建的 Client context；不能只用一个不可拆分的全局 context 栈，否则无法隔离重建单个成员。概念结构如下：

```python
class McpClientMember:
    member_id: str
    generation: int
    mcp_session_id: str | None
    max_in_flight: int
    inflight: int
    healthy: bool
    draining: bool
    client: Client
    client_context: AsyncExitStack

# acquire() 在 Condition 内按 normalized load + round-robin 选择并 inflight += 1；
# release() 在同一 Condition 内 inflight -= 1 并唤醒等待者。
```

#### 并发与锁职责

当前 `McpClientPool` 采用“调用方租用资源”而不是“每个 member 自带请求消费任务”的模型。并发协程来自 Deep Agent 并行 Tool call、同一 worker 进程中的多个 Job slot，以及 readiness、超时和故障处理；调用方通过 `pool.acquire()` 取得 member lease 后，直接执行该成员的 `client.call_tool()`，完成后再调用 `pool.release()`。member 只是持有 Client 生命周期和状态的资源对象，不拥有内部请求队列或长期 runner task。

池级 `asyncio.Condition` 由整个 `McpClientPool` 共享，只保护成员调度状态。`acquire()` 必须在同一个临界区内完成健康状态检查、normalized load + round-robin 选择和 `inflight += 1`，避免多个协程同时占用已经达到 `K` 上限的成员；`release()` 在相同 Condition 内执行 `inflight -= 1` 并 `notify_all()`，唤醒因容量不足而等待的调用方。更新 `healthy`、`draining` 等会改变可分配性的状态时也使用该 Condition。池级锁不得覆盖 `client.call_tool()`、等待算法结果、关闭 context 或建立网络连接，否则一个最长 600 秒的调用会阻塞整个池的分配与释放。

每个 member 另外持有独立的 `reconnect_lock`，它不参与正常分配，只串行化该成员的生命周期重建。调用方发现 transport/protocol 故障后，只能调用 pool/member 封装的 `ensure_reconnected(member_id, observed_generation)`，不能直接关闭或替换 `member.client`。第一个取得该锁的调用方协程把成员置为 unhealthy + draining，等待或终止其在途调用，关闭旧 context，创建并校验新 Client，再递增 generation 并恢复分配；其他等待重建的协程取得锁后必须二次检查 generation，若成员已由前一个协程修复则直接返回。重建单个成员不关闭其他健康成员，也不重建共享 HTTP connection pool；只有共享 `httpx2.AsyncClient` 自身不可恢复损坏或 worker 整体 drain 时才执行全池关闭/重建。

锁的固定获取规则是：不得在持有池级 Condition 时等待 `reconnect_lock` 或执行任何网络 I/O；重建协程只在短暂发布 draining 状态和最终替换成员状态时进入 Condition。这样避免池级锁与成员锁形成循环等待。默认 `N=2, K=1` 时，一个成员通常只有一个业务调用方，但仍保留 `reconnect_lock` 以协调健康检查、超时/取消处理和未来 `K>1` 配置。

官方 v2 Client 的生命周期边界必须遵守：构造对象不连接，进入 `async with` 才连接和协商；一旦退出 context，同一个 Client 对象不能再次复用。普通业务 Tool 失败以 `is_error=True` 返回，不销毁 Client。只有 transport/协议连接损坏时才把当前成员标记为 unhealthy + draining，停止新分配；已有调用能够自然结束时等待 `inflight=0`，否则受影响调用以稳定 transport 错误结束并按只读重试预算处理。随后由该成员自己的 reconnect lock 退出旧 context、创建并进入新 Client、递增 generation，再做 capability/version/readiness 校验后恢复分配。A 模式通常只影响一个调用；B 模式可能同时影响该成员内最多 `K` 个调用，但其他成员不被主动取消。共享 HTTP Client 只有自身不可恢复损坏或 worker drain 时才全局重建/关闭。

重连本身不能自动重试所有在途调用。只有明确尚未开始、503/busy 或满足只读 at-least-once 条件的调用，才能在预算内用相同 `invocation_id` 和更高 `retry_ordinal` 重试。若实测证明 SDK 的一个 Client/session 不能安全承载 `K>1`，配置退回 A 模式即可；若连 `K=1` 的长期成员也无法做到故障隔离，阶段 2 才回退为“每次调用独立 MCP Client context、仍共享 HTTP 连接池”。

服务端业务保持无状态：每次 `call_tool` 都带完整签名上下文，任一副本都能验证和执行；长期 Client 只是减少重复握手并复用传输资源，绝不是 Job/session/checkpoint 的真相源。legacy v1 客户端若做兼容测试，仍需通过服务端无状态配置避免粘性业务 session，但这不改变新链路的 v2 Client 设计。

客户端 HTTP/1.1 连接池默认：

```text
max_connections >= N * K + readiness_reserve
max_keepalive_connections >= N * K
keepalive_expiry=30s
connect_timeout=5s
read_timeout=660s
pool_acquire_timeout=CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS
```

`read_timeout` 必须大于最长算法 deadline（首版统一服务端上限为 600 秒），让服务端业务超时先返回结构化错误，而不是由 transport 提前切断。父图/Deep Agent 工具执行 timeout 固定为 720 秒，并要求 Job lease heartbeat 在排队和执行期间持续；HMAC `expires_at` 至少覆盖入队上限、600 秒算法 deadline、响应传输和时钟偏差，服务端在接受调用时校验有效期，已经接受的调用不因执行中跨过 `expires_at` 被误判。超时顺序固定为 `enqueue 5s < algorithm 600s < HTTP read 660s < tool node 720s < Job 总执行预算`。

默认 `N=2, K=1` 时可以继续取 `max_connections=8`、`max_keepalive_connections=4`，为 readiness/控制请求留出余量；切换配置后必须继续满足上述公式。连接数本身不是算法并发上限，真正上限由 Job semaphore、Client pool、Tool semaphore、服务队列和进程池共同决定。

每个 attempt 的内部 trace 至少记录 `worker_instance_id`、`client_member_id`、`client_generation`、`mcp_session_id`、`invocation_id` 和 `retry_ordinal`。`causal-mcp` 在已接受请求的结构化响应中再返回 `service_instance_id` 与 `executor_slot_id`，用于定位具体服务副本和算法执行槽；容器 ID/pod UID 只能作为内部 `service_instance_id` 的来源，不进入模型或公共事件。若 transport 在服务端响应前断开，客户端可能不知道实际服务实例，此时只能用 invocation/request identity 与服务日志反查，文档不能承诺每种失败都能直接回传容器 ID。

Client 池的故障注入必须同时覆盖 A 与 B：A 模式两个成员并发时，单个 500/断流/畸形帧不得终止另一成员；B 模式同一成员的两个调用要验证 SDK 是否能正确关联响应，并验证 transport 损坏时成员整体 drain、受影响 attempts 可对账且其他成员继续工作；还要验证 generation 递增、取消后无残留 request、服务重启后按需恢复、acquire timeout 和 worker drain 不遗留 receive task。

### 9.4 CPU 执行

旧版 stdio 实现中，每个 worker slot 持有一个长期 `ClientSession`，所以传输生命周期是长的；但 MCP tool 虽声明为 `async def`，内部直接执行同步 `runner(csv_data)`，没有切换线程/进程。旧父图只在 `mcp_tool_node` 外设置 360 秒节点 timeout，MCP server 内没有算法级 hard timeout；外层取消也不能可靠停止已经阻塞的同步计算。新路径删除这个显式父图节点，由 Deep Agent 内部工具执行层使用第 9.3 节的 720 秒上限，不能把旧 360 秒配置原样继承过来。

目标 HTTP 实现中的 async 表示服务可以在等待数据库、队列和子进程结果时释放事件循环，不表示算法自身变成异步，也不表示自动具备超时。需要四层显式容错：连接超时、MCP read/call timeout、服务端算法 deadline、父图 Job/节点 timeout；其中最短的业务 deadline 应先触发并返回稳定错误码。

MCP async handler 只做验证、强读、排队和结果封装。算法 runner 必须在进程池中执行，不能直接占用事件循环：

```python
result = await loop.run_in_executor(process_pool, run_algorithm, command)
```

子进程接收经过大小限制和 schema 校验的纯数据命令，不继承数据库连接、MCP session、HTTP client 或 secret。首版允许父进程从冻结 BLOB 强读并把规范化 bytes/table 传给子进程；必须设置输入字节数、行数、列数上限。派生数据只存在于调用内存，不写长期文件。

`Future.cancel()` 不能可靠终止已经运行的 CPU 任务。超时后调用方停止等待并把本次 attempt 标记为 `timed_out`，迟到结果在合并 State 前因 execution guard/lease fencing 被丢弃；若算法库无法合作取消，执行池需设置 `max_tasks_per_child` 并在连续超时后回收进程。需要硬杀单次任务时，应升级为“一调用一受控子进程”的 supervisor，而不是假装 ProcessPool 能强制取消。

---

## 10. 默认并发、队列与预算

### 10.1 首版默认值

以下是单个 worker/单个 `causal-mcp` 副本的保守起始值：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `DEEP_AGENT_MODEL_CALL_LIMIT` | 12 | 每 Job 总模型调用上限，含 finalization 修正 |
| `DEEP_AGENT_TOOL_CALL_LIMIT` | 8 | 每 Job 总 Tool 调用上限，含失败与重跑 |
| `DEEP_AGENT_RECURSION_LIMIT` | 32 | LangGraph 外层最终保险，不替代上述预算 |
| `DEEP_AGENT_TOOL_NODE_TIMEOUT_SECONDS` | 720 | 内层工具执行总等待上限，必须大于 MCP read timeout |
| `DEEP_AGENT_FINALIZATION_RETRY_LIMIT` | 1 | Gate 动态校验失败后只回送 Deep Agent 一次 |
| `DEEP_AGENT_MAX_PARALLEL_TOOLS_PER_JOB` | 2 | 单 Job 并行工具数 |
| `CAUSAL_MCP_CLIENT_POOL_SIZE` | 2 | 每 worker 进程的长期 MCP Client 成员数 `N` |
| `CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT` | 1 | 每成员并发容量 `K`；`1` 为 A 模式，`>1` 为 B 模式 |
| `CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS` | 5 | Client pool 无容量时的最大等待时间 |
| `CAUSAL_MCP_PROCESS_WORKERS` | 2 | 单副本 CPU 子进程数 |
| `CAUSAL_MCP_QUEUE_CAPACITY` | 4 | 等待队列，不含正在运行的 2 个 |
| `CAUSAL_MCP_ENQUEUE_TIMEOUT_SECONDS` | 5 | 入队等待，超时返回 busy |
| `CAUSAL_MCP_ASGI_WORKERS` | 1 | 单容器 ASGI worker 数 |
| `CAUSAL_PC_CONCURRENCY` | 2 | PC 跨 Job并发 |
| `CAUSAL_OLC_CONCURRENCY` | 1 | OLC 跨 Job并发 |
| `CAUSAL_DIRECT_LINGAM_CONCURRENCY` | 1 | DirectLiNGAM 跨 Job并发 |
| `OMP_NUM_THREADS` | 1 | 每个算法进程内部线程 |
| `OPENBLAS_NUM_THREADS` | 1 | 防止进程数×BLAS 线程过量 |
| `MKL_NUM_THREADS` | 1 | 同上 |

队列满或 5 秒内无法入队时返回可重试的 `MCP_CAPACITY_EXHAUSTED`，并带有受控 `retry_after_seconds`；不能无限等待占住 worker lease。客户端只对连接失败、503/busy 和明确尚未开始执行的失败做指数退避，默认最多 2 次；响应丢失时允许 checkpoint 恢复后重复执行只读算法。

### 10.2 上限如何组合

一次调用只有同时拿到以下许可才运行：

```text
Job tool semaphore
∩ MCP global process slot
∩ capability-specific semaphore
∩ still-current Job lease/fence
```

单副本的算法硬并发为：

```text
min(process_workers, sum(tool_limits), CPU_limit, memory_limit)
```

其中：

```text
CPU_limit    = floor(allocated_vCPU / threads_per_algorithm)
memory_limit = floor((container_memory - reserve_memory) / measured_peak_tool_memory)
```

首版 `process_workers=2` 只是缺少基准数据时的默认值。上线前要分别测 PC、OLC、DirectLiNGAM 在小/中/允许上限数据集上的峰值 RSS、CPU 时间和 p95；如果测得内存只允许 1 个 OLC，则以测量值下调。

多副本时总进程容量是 `replicas × process_workers`，而进程内 semaphore 不能形成跨副本全局上限。因此首版固定 1 副本。扩容到 2 个及以上副本前，必须增加 Redis/数据库 lease 型分布式 semaphore 或由外部工作负载调度器控制每 Tool 总量，否则文档中的“全局上限”不成立。

---

## 11. Checkpoint 重放与 MVP 执行语义

MVP 不新增 invocation 执行记录表。LangGraph checkpoint 保存模型产生的 function call、`provider_response_id`、`provider_call_id`、ToolMessage、AlgorithmResult、Action Ledger、raw 虚拟文件和 finalization state；恢复时从最后一个已提交 graph step 继续。

AlgorithmResult 在 checkpoint 中保存有界标准化图、diagnostics、warnings、provenance，以及指向 `/raw_algorithm_results/{invocation_id}/{result_index}.json` 的 `raw_result_ref`、SHA-256、大小和序列化版本。该路径由可信 Adapter 通过 Deep Agents backend API 写入默认 `StateBackend`，模型可用 `read_file(raw_result_ref)` 分段读取；不开放 `write_file`，也不把 raw payload 注入 ToolMessage 或 `/memories/`。

这里的 `raw_result_ref` 不是对象存储或独立数据库记录：虚拟文件本身仍进入父图 PostgreSQL checkpoint，并随 Job checkpoint cleanup 一起删除。它保证运行中和 checkpoint 保留期内可以复核 Adapter 前的原始算法结果，也支持恢复后继续读取；它不提供 checkpoint 清理后的长期审计。若未来需要跨保留期复核，再新增带权限、大小、保留期和清理协议的独立 raw artifact，而不是把当前虚拟路径误称为永久存储。

必须明确 checkpoint 的边界：它只能证明某个 graph step 已经提交，不能原子证明远端 MCP 算法“已经执行但响应尚未写入 checkpoint”。如果 worker 在这一窗口崩溃，恢复时会重放 Tool 节点并重复计算。首版接受这一点，因为三个因果算法是只读计算，不写外部业务状态。

```text
checkpoint 中已有成功 ToolMessage
-> 恢复后不再调用 MCP

checkpoint 只有 AI tool call，没有成功 ToolMessage
-> 恢复后重新调用 MCP
-> 复用相同 invocation_id/result_ref，retry_ordinal 递增
```

现有 MySQL `analysis_jobs` 继续持有 worker ownership、attempt 和 lease fencing；Tool 执行前后都调用 execution guard。`analysis_job_events` 只保存供 SSE 恢复和运维观察的安全生命周期事件，不承担算法结果真相源，也不保存完整结果。

只有出现以下任一条件时才增加数据库 invocation 表：Tool 产生不可重复外部副作用、单次调用费用高到必须严格去重、需要在 MCP 响应丢失后查询既有结果，或合规要求独立执行审计。届时应把它作为新需求设计，而不是 MVP 默认成本。

---

## 12. RAG 与 Web 工具

### 12.1 RAG evidence-only

新 `rag_evidence_search` 是本地 function tool，不进入 `causal-mcp`。需要从当前 RAG pipeline 中拆出“检索证据”能力，保留：

- `active_index.json` 和 release/readiness 校验；
- dense/sparse/BM25、MMR、merge/rerank；
- threshold、top_k、证据压缩和 parser；
- citation、release id、降级状态和空结果语义。

Deep Agent 路径不调用当前 RAG 内部的问题重写模型和答案模型，避免 Agent→RAG Agent 的双层自主循环。返回结构包含 evidence id、片段、来源、页码/定位、score、release id 和 degradation flags；引用必须能由 report renderer 确定性映射。

### 12.2 Web evidence

`web_evidence_search` 同样是本地原子工具，封装现有受控 Web Search/SearXNG 路径。模型可传查询、语言和结果数，但不能传任意 backend URL、header 或认证。返回规范化 source title、URL、摘要、抓取时间和 provider status。

RAG/Web 的调用也进入 Action Ledger，但不会进入 MCP 进程池；它们分别使用独立 async semaphore，避免网络检索占满 CPU 算法容量。

---

## 13. FinalizationGate 与报告

`ToolStrategy` 先保证静态 schema；外层 `FinalizationGate` 再校验运行时事实：

1. 先以当前可信 `job_id + attempt_count + lease_epoch` 过滤结果；只有 provenance 完全匹配且 `status=valid` 的结果才构成 eligible valid candidate set，旧 attempt、其他 Job 或旧 lease 的结果即使仍在 checkpoint 中也不能进入候选集；
2. `result_assessments` 不能引用不存在或 provenance 不匹配的 `result_ref`，每个 eligible valid candidate 必须恰好有一个 assessment；非 valid 结果若被列入 assessment，只能是 `discarded`，不能是 `primary/supporting`；
3. outcome 为 `algorithm_supported` 时 eligible set 必须非空、必须恰好有一个 `primary` disposition，且 `primary_result_ref` 必须引用同一结果；
4. outcome 为 `evidence_only` 时 `primary_result_ref` 和 `primary` disposition 都必须为空；若此前产生 eligible valid results，它们必须全部明确标为 `discarded`，表示报告不采用算法结论；
5. outcome 为 `no_valid_algorithm` 时 eligible set 必须为空、至少存在一次真实算法调用记录，并且 `primary_result_ref` 与 `primary/supporting` disposition 都必须为空；未调用算法而只使用证据时必须选择 `evidence_only`；
6. decision 声明的调用、结果、失败、重跑和取舍必须能与 Action Ledger 的当前 invocation/attempt 事实对账。

第 4 条是已经冻结的产品规则：technically valid 但与当前问题无关的算法结果可以全部 discarded 后进入 `evidence_only`。实现与测试必须同时证明这些结果没有 `primary/supporting` disposition、没有 `primary_result_ref`、不生成主因果图，并且报告不采用其算法结论；技术有效性不能自动推导为业务采用。

Gate 不重复验证已经由 Pydantic、Adapter 或 execution guard 保证的字段，不检查 RAG/Web 来源元数据完整性。它不调用模型、不修改 graph、不替模型补结论。失败策略只允许一次回环：

```text
第一次动态不一致
-> 生成结构化 correction message
-> 回到同一 Deep Agent
-> 只重新提交 FinalAnalysisDecision

第二次仍不一致
-> 不再回到 Deep Agent
-> 程序构造 finalization_status=degraded 的 ReportContext
-> 进入 report，仍然生成报告
-> report 成功持久化后，analysis_jobs.status=succeeded
```

降级 `ReportContext` 向 report 模型提供全部业务安全且经程序核验的上下文：用户问题、数据画像、预处理摘要、属于当前 Job/attempt/lease 的 AlgorithmResult、完整 Action Ledger、RAG/Web evidence、两次 FinalAnalysisDecision 的脱敏错误摘要、Gate 错误码和失败说明。非法 decision 中未经 Gate 验证的引用、assessment、冲突和建议不能原样进入报告上下文。这里的“全部”不包括原始 reasoning、凭据、内部堆栈、数据库连接和未脱敏文件正文。

程序而不是 report 模型决定降级状态和主图引用：只有经过 Gate 验证的 `primary_result_ref` 才能展示主图；没有通过验证时主图为空。report 模型可以根据所有成功、失败和冲突组织说明，但不能新增 Tool 执行事实、图边或证据。这样既保证报告一定生成，也不会让无效 finalization 变成伪成功。

ToolStrategy 自身的 schema 错误反馈计入模型调用预算；外层 Gate 回送计入 `FINALIZATION_RETRY_LIMIT=1`。合法终态和 `finalization_degraded` 均进入 report。只有 report 自身未能生成/持久化，或发生系统、安全、取消错误时，Job 才进入 `failed/canceled`；`finalization_degraded` 是成功 Job 的结果质量元数据，不新增数据库 Job 状态。

---

## 14. DeepSeek Responses API 适配

模型工厂继续使用 `ChatOpenAI` 兼容入口，但内层 Deep Agent 专用实例必须显式设置 `use_responses_api=True`，并在阶段 0 用真实 DeepSeek endpoint 验证，不能仅用 OpenAI mock 证明。父图路由、fold、report 或其他既有模型节点不在本轮被隐式迁移；它们是否改用 Responses API 应按各自兼容性另行验证。配置必须固定实际 DeepSeek model id 和 `DEEP_AGENT_CONTEXT_WINDOW_TOKENS`，不能用会随服务端变化的“最新模型”作为可复现版本身份。

已知边界：

- function tools 可用；DeepSeek 内置 `mcp`、web/file/code 等 tool 类型不作为本项目入口；
- `parallel_tool_calls` 被忽略，服务端始终可能并行返回，所以必须实现第 8 节的完整关联；
- `max_tool_calls` 被忽略，所以工具与模型调用预算必须由本地 middleware/graph guard 执行；
- `previous_response_id`、`store`、conversation 等模型 API 能力不能作为 Job 恢复机制；恢复依赖 LangGraph checkpoint 和现有 Job lease/fencing；
- reasoning 内容不得进入公共 SSE、Job event、report、日志正文或前端；只允许记录 token/时延等不含内容的指标。

流式适配器必须以 response item/call id 聚合 function arguments，在完成事件到达后才调度；不得把半截 JSON 当作调用。Tool result 回写必须携带原 call id，确保多个并行调用不会串配。

---

## 15. 事件、日志与隐私

内部事件先表达完整执行状态，再由公共事件适配器映射到现有公共协议。点号命名只用于内部事件，不新建一套公共 SSE v2：

| 内部事件 | 现有公共事件 | 投影规则 |
|---|---|---|
| `analysis.planning` | `progress` / `decision` | 只输出安全摘要 |
| `tool.queued` | `progress` 或不公开 | 仅在排队对用户有可见等待价值时输出 |
| `tool.started` | `tool_call_start` | 保留公开 tool name、argument keys 和现有 opaque `step_id` |
| `tool.succeeded/failed/not_ready/timed_out` | `tool_call_result` | 增加受控 `status`、`safe_error_code`，不输出参数值或原始结果 |
| `analysis.finalizing` | `progress` | 表达正在校验与生成报告 |
| `analysis.finalization_degraded` | `progress` | 只说明将生成降级报告；最终质量状态进入 `final_result.data.finalization_status` |
| `analysis.completed` | `final_result` | `finalization_status=valid|degraded`；两者均使 Job succeeded |
| `analysis.failed` | `error` | 仅用于阻止报告交付的系统/安全错误 |

继续保留现有 `node_start/node_end/node_retry/text_delta/interrupt/canceled/heartbeat`，避免破坏 SSE replay、Last-Event-ID 和当前前端阶段聚合。`event_adapter.py` 是唯一内部→公共映射入口；`public_events.py` 的白名单只为 `tool_call_result` 增加 `status`、`safe_error_code`，并在 `final_result.data` 中承载最终结果，不把内部事件名直接持久化为新公共协议。

公共 payload 只允许现有公共 job id、opaque `step_id`、公开 tool 名、阶段、进度、受控 status/safe error code、时间和最终报告数据。`invocation_id`、`result_ref`、AIMessage id、供应商 call id 仅用于内部 State/日志/Gate 对账，首版不进入公共事件。禁止输出：模型原始 reasoning、tool 原始 arguments、CSV/文件正文、可信 ID 集合、数据库信息、堆栈、token、签名和标准图全量。

独立 `causal-mcp` 镜像复用当前 `observability.logging_runtime.log_event()`、`log_context()`、环境字段、JSON schema 和 Alloy/Loki 采集拓扑，只补充 MCP 事件，不另建日志框架。MCP stdout/stderr 必须按传输要求处理，协议输出和应用日志不能混流。

内部日志至少包含 request id、job id、invocation id、attempt、lease epoch、capability/version、spec digest、duration、queue wait 和 exit status，以便跨 worker/MCP 关联。建议补充 `mcp.request.accepted`、`mcp.capacity.rejected`、`mcp.tool.started`、`mcp.tool.finished`、`mcp.tool.failed`、`mcp.process.recycled` 和 `mcp.client.reconnected`。高基数字段不能直接作为 Prometheus label。

---

## 16. 配置与健康检查

建议新增配置组：

```text
DEEP_AGENT_PROFILE_VERSION
DEEP_AGENT_MODEL
DEEP_AGENT_BASE_URL
DEEP_AGENT_CONTEXT_WINDOW_TOKENS
DEEP_AGENT_TOOL_RESULT_MAX_TOKENS
DEEP_AGENT_RAW_RESULT_MAX_BYTES
DEEP_AGENT_MODEL_CALL_LIMIT
DEEP_AGENT_TOOL_CALL_LIMIT
DEEP_AGENT_RECURSION_LIMIT
DEEP_AGENT_TOOL_NODE_TIMEOUT_SECONDS
DEEP_AGENT_FINALIZATION_RETRY_LIMIT
DEEP_AGENT_MAX_PARALLEL_TOOLS_PER_JOB
DEEP_AGENT_MEMORY_ENABLED
DEEP_AGENT_MEMORY_MAX_CHARS

CAUSAL_MCP_URL
CAUSAL_MCP_CLIENT_POOL_SIZE
CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT
CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS
CAUSAL_MCP_HTTP_MAX_CONNECTIONS
CAUSAL_MCP_HTTP_MAX_KEEPALIVE_CONNECTIONS
CAUSAL_MCP_SERVICE_TOKEN
CAUSAL_MCP_SIGNING_KEY_CURRENT
CAUSAL_MCP_SIGNING_KEY_PREVIOUS
CAUSAL_MCP_SIGNING_KEY_ID
CAUSAL_MCP_PROCESS_WORKERS
CAUSAL_MCP_QUEUE_CAPACITY
CAUSAL_MCP_ENQUEUE_TIMEOUT_SECONDS
CAUSAL_MCP_INPUT_MAX_BYTES
CAUSAL_MCP_RESULT_MAX_BYTES
```

secret 只从部署 secret/env 注入，不写入仓库、checkpoint 或配置 dump。readiness 必须区分：

- worker ready：依赖锁加载、registry 校验、父图编译、PostgreSQL checkpointer、`AsyncPostgresStore`、MySQL primary、MCP `/ready` 及 capability/version 摘要；
- MCP ready：SDK app 启动、配置有效、MySQL strong read 可用、进程池至少可接收一个探针；
- RAG ready：仍按 active release 与 embedding/index fingerprint 判定，不能被 MCP ready 替代。

---

## 17. 代码影响面

以下是实施时的预期位置，不代表本轮已经修改：

| 位置 | 变更 |
|---|---|
| `requirements.txt` | 升级 LangChain/LangGraph 兼容簇，新增 Deep Agents，移除旧 adapter，固定 `mcp==2.2.0`；`requirements-base.txt` 不承载当前 LangChain/MCP 依赖 |
| 新的 MCP requirements | 独立固定 `mcp==2.2.0` 与算法依赖 |
| `Dockerfile` / 新 MCP Dockerfile | 分离主程序和 causal-mcp 镜像 |
| Compose 文件 | 增加 private `causal-mcp` service、health/readiness、资源和线程限制 |
| `Agent/causal_agent/graph.py` | 用新的 `deep_agent` 节点替换旧因果分析工具链，保留外层 agent/fold/report |
| `Agent/deep_agent/` | 独立承载 graph、State、runtime context、Profile、prompt、官方 memory backend/Store 装配和 FinalizationGate |
| `Agent/deep_agent_tools/` | 独立承载 Spec、registry、schema、依赖 planner、algorithm/RAG/Web tools 和 executor |
| `Agent/CausalAgentMCP/` | v2 Streamable HTTP、鉴权、队列、进程池、结果规范化 |
| `app/agent/worker/bootstrap.py` | 编译目标图、初始化进程级长期 MCP Client pool/共享 HTTP pool/executors、fail-fast |
| `app/agent/worker/graph_runner.py` | 预算、checkpoint namespace、恢复投影 |
| `app/agent/worker/event_adapter.py` | 新内部事件到公共事件的白名单转换 |
| `Database.bootstrap` / PostgreSQL | 复用 `postgres-checkpoint`，初始化并验证 checkpointer 与 `AsyncPostgresStore`；Store 保存按 user_id 隔离的两份 memory 文件 |
| RAG 实现 | 拆分 evidence-only 检索入口，保留 release/readiness |
| tests | schema、planner、fencing、MCP v1→v2、DeepSeek、多调用、恢复和真实 Job 验收 |

旧 `mcp_tool_call_adapter.py`、stdio session 和单结果字段保留在旧版本分支/镜像，不复制到新运行链路。新旧方案的比较不在本次设计中实施，只按第 3.2 节保留未来实验前置材料。

---

## 18. 实施阶段与完成条件

### 阶段 0：依赖与协议 spike

输出独立锁文件、最小 Deep Agent、DeepSeek Responses 多 tool call、MCP 2.2 client→2.2 server HTTP 契约测试。完成条件是 clean install 和上述真实集成全部通过；否则停止后续改造。

### 阶段 1：领域契约、State 与记忆 Store

实现 `Agent/deep_agent/`、`Agent/deep_agent_tools/`、Pydantic schemas、主程序维护的 AlgorithmSpec/Registry、State projection、Action Ledger，以及官方 memory middleware + `StoreBackend` + `AsyncPostgresStore`。完成条件是 schema snapshot、Filesystem allowlist/Profile 工具快照、两份固定 memory 文件 allow write + 其他 `/**` deny write 的权限快照、模型 `max_input_tokens`/summarization 启动断言、两份 memory 文件 create-if-absent 初始化、自动加载与 `edit_file` 写入、其他虚拟路径写入被拒绝、`read_file` 对 State/Store 虚拟路径的分页读取、PostgreSQL 用户 namespace 隔离和 checkpoint replay 测试通过；本阶段不实现记忆查看/删除 UI、保留期、并发冲突处理或审计表，也不新增其他长期记忆防御措施。

### 阶段 2：独立 causal-mcp

实现 v2 Streamable HTTP over HTTP/1.1、参数化 `N×K` 的进程级长期 MCP Client pool/共享 HTTP pool、bearer/HMAC、强读、进程池、队列、600 秒服务端最长 deadline、结果契约和单副本部署。完成条件是真实 PC/OLC/DirectLiNGAM 调用、A（`K=1`）与 B（`K>1`）两种配置的并发/故障注入、自动选择成员、acquire timeout、generation 重建、内部实例/执行槽追踪、事件循环不阻塞、过载返回、迟到结果丢弃和内存基准通过；B 不安全时退回 A，长期 A 仍不安全时才切换为每调用独立 Client context。

### 阶段 3：多工具 Adapter 与调度

实现本地 AlgorithmSpec tools、依赖 DAG、批并发、部分失败、not_ready、按 invocation/attempt/revision 合并的 Ledger reducer 和 recovery replay。完成条件是每种依赖/失败组合都有确定性测试，所有 call id 正确配对，成功前的全部失败、超时与恢复重算记录均不丢失。

### 阶段 4：RAG/Web evidence tools

拆分 evidence-only RAG，接入 Web 原子工具并统一引用。完成条件是真实 active release、dense/sparse/BM25/MMR/rerank 路径和降级/空结果通过，且没有内部答案模型。

### 阶段 5：Deep Agent 与 Finalization

接入单 Deep Agent graph、Filesystem allowlist/Profile 裁剪、预算、ToolStrategy、FinalizationGate 和 report。完成条件是三种正常 outcome、一次修正上限、`finalization_degraded` 仍生成报告并把 Job 标记为 succeeded、结果 Job/attempt/lease 归属校验、所有有效候选 assessment 完整、reasoning 脱敏、checkpoint 恢复和外层 fold 均通过。

### 阶段 6：版本切换与对比材料留存

本阶段不运行架构对比实验。按第 3.2 节固化新旧版本的 commit/tag、锁文件、镜像 digest、schema/spec/prompt、RAG release/fingerprint、模型配置和版本化问题集，供后续独立实验计划使用。当前版本自身验收通过后，在目标环境停止旧 worker、处理在途 Job，再部署新镜像；不在同一进程或同一镜像中保留双路径。

---

## 19. 测试与验收矩阵

| 风险 | 最低测试 | 不能替代的证据 |
|---|---|---|
| 依赖解析 | clean venv 全量 install、import smoke | 不能只看 PyPI 声明 |
| DeepSeek 多调用 | 真实 Responses API 两个 function calls | OpenAI mock 不等价 |
| 模型 profile 与摘要 | 显式 `max_input_tokens`、85% trigger、10% keep、摘要后 Tool call 关联；确认使用同一 Deep Agent 模型 | 只成功初始化不等价 |
| MCP 版本互通 | v2.2 client→v2.2 server 的协议发现/协商（现代 `server/discover`）、list/call/error；可选验证旧镜像 v1→v2 的 initialize fallback | 单元 mock 不等价 |
| CPU 隔离 | 并发算法时 health/event loop 延迟 | HTTP 200 不证明不阻塞 |
| MCP Client A/B 故障隔离 | `K=1` 时单成员故障不终止其他成员；`K>1` 时验证同成员响应关联、故障影响面、drain/rebuild 和其他成员存活；取消后无残留 task | 普通断线重连测试不等价 |
| 队列与上限 | 2 running + 4 queued + 第 7 个 busy | semaphore 单测不证明容器容量 |
| Job fencing | 旧 lease 迟到结果进入 discarded | 普通重试测试不等价 |
| checkpoint | 已提交结果不重算；未提交远端结果允许按相同 invocation identity 重算；全部 retry attempts 保留 | 重新跑成功不等价 |
| raw 虚拟结果 | canonical JSON、大小/hash/version、`read_file` 分页读取、同 graph step 一致性、恢复可读和 checkpoint cleanup 同步删除 | 仅验证 `raw_result_ref` 字符串不等价 |
| RAG | 真实 active release 和索引检索 | fixture/mock 不等价 |
| DB | MySQL primary、migration、索引和并发事务 | SQLite 不等价 |
| checkpointer | PostgreSQL 持久化恢复 | 内存 checkpointer 不等价 |
| 公共事件 | 内部点号事件映射到现有 snake_case 协议；SSE replay/Last-Event-ID、degraded final_result 和脱敏快照 | 日志输出不等价 |
| Profile 裁剪 | 模型可见 tools 快照保留官方 `read_file`、`edit_file`，无 write/list/search/execute/task，且默认 subagent 已关闭 | 只检查传入 `tools=` 不等价 |
| 长期记忆 | 两份 memory 文件、精确路径允许写与 `/**` 兜底拒绝写、其他虚拟路径写入失败、自动加载/写入、用户 namespace 隔离、PostgreSQL 重启后持久化、checkpoint cleanup 不影响 Store | InMemoryStore 不等价于生产持久化 |
| 最终报告 | 三种 outcome、一次修正、degraded 报告对应 succeeded Job、Job/attempt/lease 归属、有效候选 assessment、唯一主图 | 只验证 schema 不等价 |

压测至少记录：每算法数据规模、峰值 RSS、CPU 时间、queue wait、p50/p95/p99、超时率、失败码分布和 Job 总耗时。调高进程数必须以这些数据和容器资源为依据。

---

## 20. 回退策略

回退以部署单元为边界，不改写 Job 历史：

1. 停止新版本 worker 继续 claim Job；
2. 让已持有 lease 的 Job 完成，或按正常 lease 过期后由同版本处理；不要让旧版本直接读取不兼容的新 checkpoint；
3. 停止新 worker，部署保存的旧镜像 digest 和旧声明式配置；
4. 新建 Job 才交给旧版本；新版本未完成 Job 若 checkpoint schema 不兼容，应明确失败/重新创建，不能静默跨版本恢复；
5. 保留事件、checkpoint 和 Store 数据，不在线降包，也不通过 Git checkout 修改正在运行的容器；
6. 回退后核对 report、SSE replay、checkpoint cleanup outbox 和 active Job 唯一约束。

本轮不实现离线 replay、Shadow、流量实验 A/B、Canary 或按比例分流；只保留第 3.2 节列出的未来对比前置材料。MCP Client pool 的 A/B 配置仍按第 9.3 节实测。

---

## 21. 尚未冻结但不阻塞编码的参数

以下项目已有安全默认值，可以在不改变产品语义的前提下通过压测调整：

- `CAUSAL_MCP_PROCESS_WORKERS=2`、queue=4 和各 Tool 并发；
- PC/OLC/DirectLiNGAM 精确 timeout；
- Job 模型/工具调用预算；
- MCP 输入/结果字节上限；
- 单副本达到什么资源阈值后引入分布式全局 semaphore。

以下变化会改变边界，必须重新讨论而不能在实现中顺手加入：多 Agent、Deep Agent 内 HITL、Sandbox/自由代码执行、长期保存派生数据、模型修改因果图、多副本但没有全局容量控制、把 MCP session 当作 Job 身份。

---

## 22. 官方事实来源

版本和行为会随发布变化，实施阶段 0 应以锁定日期重新核对：

- [Deep Agents PyPI](https://pypi.org/project/deepagents/)
- [Deep Agents customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [Deep Agents harness profiles](https://docs.langchain.com/oss/python/deepagents/profiles)
- [Deep Agents subagents](https://docs.langchain.com/oss/python/deepagents/subagents)
- [Deep Agents memory](https://docs.langchain.com/oss/python/deepagents/memory)
- [Deep Agents backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents context engineering and summarization](https://docs.langchain.com/oss/python/deepagents/context-engineering)
- [Deep Agents permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [`create_deep_agent` reference](https://reference.langchain.com/python/deepagents/graph/create_deep_agent)
- [Deep Agents 0.7.13 summarization reference](https://reference.langchain.com/python/deepagents/middleware/summarization/create_summarization_middleware)
- [LangChain PyPI](https://pypi.org/project/langchain/)
- [LangGraph PyPI](https://pypi.org/project/langgraph/)
- [LangGraph subgraphs and checkpoint inheritance](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- [LangGraph context](https://docs.langchain.com/oss/python/concepts/context)
- [LangGraph persistence and Store](https://docs.langchain.com/oss/python/langgraph/persistence)
- [`AsyncPostgresStore` reference](https://reference.langchain.com/python/langgraph.store.postgres/aio/AsyncPostgresStore)
- [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output)
- [LangChain MCP integration](https://docs.langchain.com/oss/python/langchain/mcp)
- [LangChain MCP tool reference](https://reference.langchain.com/python/langchain/mcp/tools)
- [langchain-mcp-adapters PyPI](https://pypi.org/project/langchain-mcp-adapters/)
- [MCP Python SDK PyPI](https://pypi.org/project/mcp/)
- [MCP Python SDK v2: What’s new](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md)
- [MCP Python SDK v2: running servers](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md)
- [MCP Python SDK v2: ASGI integration](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md)
- [MCP Python SDK v2: deployment](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/deploy.md)
- [MCP Python SDK v2: legacy clients](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/legacy-clients.md)
- [MCP Python SDK 2.2.0: Client lifecycle](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/docs/client/index.md)
- [MCP Python SDK 2.2.0: client transports](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/docs/client/transports.md)
- [HTTPX2 HTTP/2 support](https://pydantic.dev/docs/httpx2/guides/http2/)
- [Uvicorn](https://www.uvicorn.org/)
- [DeepSeek Responses API reference](https://api-docs.deepseek.com/api/create-response/)

---

## 23. 本文的完成边界

本文已经冻结实现方向、依赖隔离方式、首版默认并发、状态/幂等/MCP/Finalization 协议和分阶段验收标准。它没有修改 requirements、数据库、Compose 或运行代码，也没有声称 Deep Agent 已可运行。进入实施时，应从阶段 0 开始，按真实依赖解析和真实 DeepSeek/MCP 契约证据推进。
