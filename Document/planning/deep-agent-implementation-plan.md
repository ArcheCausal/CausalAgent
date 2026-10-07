# Deep Agent 集成实施计划

> **文档职责**：把 Deep Agent 产品规划与技术设计拆解为可分支协作、可逐阶段验收、可合并和可回退的工程任务；明确主线实施者与 MCP 协作成员的文件所有权、依赖顺序、合并门禁、测试证据和文档更新范围。
>
> **适用范围**：适用于从 `develop@4bea85fdea7292e85577d2b9b3e4c1c763b50829` 创建的新 DeepAgent 功能分支，以及由该分支派生的 MCP 协作分支。本文是实施计划，不代表功能已经实现或验收通过。

**状态**：P1 共享契约与 P2-U 协议/ToolRuntime State 写回代码已完成；P2-U 的 AlgorithmSpec 调度协议骨架已形成，正在按本计划收口且尚未接入真实 Deep Agent dispatch；P0 真实门禁未完成，P2-M 及 P3 以后未完成

**计划日期**：2026-09-14

**产品决策来源**：[deep-agent-integration-plan.md](./deep-agent-integration-plan.md)

**技术设计来源**：[deep-agent-integration-technical-design.md](./deep-agent-integration-technical-design.md)

**当前运行事实**：[agent-runtime.md](../architecture/agent-runtime.md)

---

## 本轮实施结论（2026-09-14）

### 执行范围与停止点

本轮在保留 P1 的基础上完成 P2-U 主线的协议级实现，并补齐 Algorithm、RAG、Web 本地 Tool 的 `ToolRuntime → Command → State reducer` 写回链路。实现范围限定为不依赖真实 MCP 服务、PostgreSQL、SearXNG 或模型凭据即可验证的 State/context、权限、内存 backend、Adapter、依赖调度、evidence Tool 契约和真实 LangGraph ToolNode State 合并；没有修改 `requirements.txt`、Compose、worker/bootstrap、现有 MCP server、父图、Job/SSE、前端或数据库 schema。未执行提交、推送、分支切换、删除文件和覆盖工作树操作。

P0 的真实门禁没有被本轮重新认定为通过。当前宿主实际缺少 `deepagents`/`langgraph` 运行依赖，且没有 DeepSeek、MCP、PostgreSQL、SearXNG 的真实验收凭据；因此不能把真实依赖安装、DeepSeek Responses、多 Tool/ToolStrategy、父子图 checkpoint、MCP HTTP、Store 重启保留或 active RAG/Web 链路写成已完成。P2-U 只以 fake executor、内存 Store 和协议级测试交付；真实 Deep Agent graph 入口和 PostgreSQL Store 入口采用惰性加载，缺依赖时 fail closed。

### P1 已实施内容

| 位置 | 已完成内容 |
|---|---|
| `Agent/deep_agent_tools/models.py` | `AlgorithmResult`、`AlgorithmResultProvenance`、`ActionAttempt`、`InvocationRecord`、RAG/Web evidence、`McpInvocationContext`、安全诊断、`FinalAnalysisDecision`、标准化图、canonical raw JSON 元数据和三个 reducer。 |
| `Agent/deep_agent_tools/algorithm_specs.py` | PC、OLC、DirectLiNGAM 三项首版 `AlgorithmSpec`；模型输入 schema、requires/produces、假设、超时、并发键/默认并发、工具 schema 和 canonical SHA-256 `spec_digest`。可信 Job/文件/lease 字段没有进入模型可见 Tool schema。 |
| `Agent/deep_agent_tools/registry.py` | 静态 capability/tool 索引和一对一 Adapter binding；重复 capability、重复 tool、未知 capability、schema 生成失败在注册期拒绝；不从 MCP `list_tools()` 动态注册。 |
| `Agent/deep_agent_tools/identity.py` | 固定项目 UUID 命名空间、provider response identity 优先、本地 `message_execution_id` fallback、UUIDv5 invocation identity、`result_ref` 和 MCP context canonical JSON payload。item `id` 未参与逻辑调用键。 |
| `Agent/deep_agent_tools/algorithm_executor.py` | 传输无关的异步 `AlgorithmExecutor` Protocol、带稳定安全错误码的 executor error，以及 command/context/result 的 capability、spec、input、Job attempt/lease 对账校验。 |
| `Agent/deep_agent_tools/fake_algorithm_executor.py` | 可观察、确定性、只用于主线测试的 fake executor；支持成功模板、失败注入、调用记录和可信上下文身份检查，不连接 MCP 或数据库。 |
| `Agent/deep_agent_tools/error_codes.py` | 用户输入、算法不适用/未就绪、执行失败、结果契约错误、MCP 能力/容量/鉴权/lease 等稳定安全错误码；已知服务端结果契约错误不能归类为 `invalid_input`。 |
| `tests/unit/agent/` | P1 模型、Spec、Registry、identity、reducer、executor 和 schema 快照测试；`snapshots/deep_agent_full_schema_snapshot.json` 保存完整 Pydantic/Tool schema，另有 manifest 哈希快照用于稳定性审查。 |

### P2-U 已实施内容

| 位置 | 已完成内容 |
|---|---|
| `Agent/deep_agent/state.py`、`context.py` | 实现 `ProjectDeepAgentState(DeepAgentState)` 兼容基类、Action Ledger/算法结果/RAG/Web 独立 reducer 字段、空 Ledger 初始值、父子 State 白名单投影，以及不把 guard/executor/Store/checkpointer 写入 checkpoint 的检查。真实 `deepagents.graph.DeepAgentState` 只在安装后继承，当前环境使用协议测试 fallback。 |
| `Agent/deep_agent/profile.py`、`prompts.py`、`graph.py`、`finalization.py` | 冻结 `read_file`/`edit_file` 工具面、两条 memory write allow 加 `/**` deny 的 first-match 规则、模型/Tool/递归/Finalization retry 预算、85%/10%/4000 summarization 参数、受控系统提示和 `ToolStrategy(FinalAnalysisDecision)`/引用闭包校验。真实 graph 依赖通过惰性加载，缺包不会伪造生产图。 |
| `Agent/deep_agent/memory.py`、`postgres_store.py` | 实现 `CompositeBackend(StateBackend + StoreBackend)` 的协议级路径路由；memory 按可信 `user_id` namespace 隔离；默认内存 backend 的两份 Markdown 文件 create-if-absent；模型不能写 raw/其他虚拟路径，可信 Adapter 可直接写 canonical raw JSON 并回读校验 hash/大小/version；PostgreSQL Store 只做惰性官方 API 装配，checkpoint cleanup helper 明确排除 `store`/`store_migrations`。官方 StoreBackend 初始化、PostgreSQL 重启保留和真实跨 Job 保留列入后续真实验收，不作为当前默认 P2-U 的阻断条件。 |
| `Agent/deep_agent_tools/dependency_planner.py` | 已形成只覆盖 PC、OLC、DirectLiNGAM 的静态 Registry DAG 协议；P2-U 收口时以 `AlgorithmSpec` 作为唯一事实源，统一 `requires/produces`、算法 timeout、`concurrency_key` 和默认并发，并保留同轮 calls、分层执行和失败传播语义。当前尚未接入真实 Deep Agent Tool dispatch；P3 接入，P5 使用真实 DeepSeek 验证。 |
| `Agent/deep_agent_tools/adapters/`、`algorithm_tools.py`、`runtime_updates.py` | 实现 PC、OLC、DirectLiNGAM 应用侧 Adapter；逐项落实参数校验、连续数据/样本量/缺失边界、可逆确定性预处理 recipe digest、timeout/concurrency 所属 Spec、统一 `AlgorithmResult`/provenance、raw result metadata 和单调 Ledger revision；增加旧 runner 节点/边/权重方向的纯函数标准化。三类 LangChain Tool 已使用 runtime identity 并通过 `Command` 同步更新结果、Ledger 与 ToolMessage；未接入 MCP transport。 |
| `Agent/deep_agent_tools/rag_evidence_tool.py`、`web_evidence_tool.py`、`Agent/knowledge_base/rag_service.py` | 增加不调用 answer model 的 `RagService.get_evidence()` 与 `rag_evidence_search`；保留 active release/readiness、脱敏 embedding fingerprint、检索 trace、dense/sparse/MMR/rerank、compression、citation 和 unavailable/no-evidence/protocol 状态。Web Tool 复用当前 SearXNG arXiv/Crossref/OpenAlex、top-3 轮转和最多 9 条 snippet；`web_search_enabled` 关闭时不触网。 |
| `tests/unit/agent/`、`tests/integration/agent/` | 新增 P2-U State、Profile、permission、memory、dependency planner、Adapter、RAG/Web、FinalAnalysisDecision 和 Store/checkpoint 边界测试；只使用 fake/isolated 实现，未把它们写成真实服务验收。 |

Reducer 的具体语义已经冻结：AlgorithmResult 和 evidence 以不可变引用去重，同 key 内容不同抛一致性错误；Action Ledger 以 `invocation_id → retry_ordinal → revision` 合并，旧 revision 不覆盖新 revision，同 revision 内容不同抛一致性错误，并保留所有 retry attempt。`AlgorithmResult` 的状态边界也已落到模型校验中：`invalid_input` 不接受已知服务端输出/传输错误码，`execution_failed` 不接受已知用户输入错误码。

### 验证证据与限制

已执行并通过：

```text
python -m pytest -q tests/unit/agent/test_deep_agent_models.py tests/unit/agent/test_algorithm_specs.py tests/unit/agent/test_algorithm_registry.py tests/unit/agent/test_invocation_identity.py tests/unit/agent/test_action_ledger_reducers.py tests/unit/agent/test_algorithm_executor_contract.py tests/unit/agent/test_deep_agent_schema_snapshots.py
28 passed
```

同时尝试运行 `python -m pytest -q tests/unit/agent`，但本地收集阶段因宿主环境缺少既有项目依赖而中断：`langgraph` 缺失导致图/worker 相关测试无法导入，`mysql` 缺失导致 Job 相关测试无法导入，共 16 个收集错误；没有通过临时安装依赖掩盖该限制。Docker CLI 本身可用，但本轮没有借此声称 P0 clean install 或真实 Docker 验收通过。P1 新增测试本身只依赖当前可用的 Pydantic 环境，不代表现有 Agent/Job 全链路已验证。

### 完整实施结论

P1 共享领域/传输契约与 P2-U 主线协议代码已经形成可供后续 MCP 子分支和主线真实 runtime 复用的基点。P2-U 定向测试证明了 State 投影、空 Ledger、权限拒绝、可信 raw write、用户 namespace 隔离、create-if-absent、依赖分层/失败传播、三项 Adapter 的输入边界、evidence-only 输出和结构化终态校验；这证明的是协议和 fake/isolated 行为，不是生产运行时能力。

当前仍未完成或未验收：P0 真实兼容依赖与 DeepSeek Responses spike、P2-M causal-mcp 纵向切片、P3 worker/MCP pool/checkpoint 接入、P4 父图/FinalizationGate/report/SSE、P5 Docker/MySQL/PostgreSQL/真实 MCP/真实 DeepSeek/真实 active RAG/SearXNG/full Job 验收、P6 当前事实文档与 CHANGELOG 收口。P2-U 也没有宣称真实 PostgreSQL Store 跨重启保留、真实 RAG 不触发 answer model、真实 Web 网络关闭边界或真实模型多 Tool 关联已经通过。

因此当前状态是“P1 + P2-U 协议级主线实现完成，生产 Deep Agent 功能尚不可部署”。进入 P3 前必须先取得并复验 P0 兼容簇，同时等待/合并 P2-M；进入 P4/P5 前必须使用真实依赖和隔离服务补齐对应证据，不能用本轮 unit/mock 结果替代。

---

## 1. 实施结论

本次改造采用一条 DeepAgent 主线和一条 MCP 协作支线：

```text
develop@4bea85f
    │
    └─ DeepAgent 主分支（你）
         ├─ 文档与阶段 0 基线
         ├─ 共享领域/传输契约
         │       │
         │       └─ MCP 子分支（协作成员）
         │            └─ causal-mcp 纵向切片
         │
         ├─ Deep Agent / State / Memory / RAG / Web（并行）
         ├─ 合并 MCP 子分支
         ├─ worker / graph / finalization / report / events 集成
         └─ 分层验收、文档收口与版本身份冻结
```

顺序上的关键约束是：

1. 先验证依赖与真实协议，不在大规模代码迁移中隐式升级 LangChain/LangGraph/MCP。
2. 先冻结 `AlgorithmSpec`、`AlgorithmResult`、`McpInvocationContext`、`AlgorithmExecutor` 和安全错误码，再让协作成员建立 MCP 子分支。
3. 主线使用 fake `AlgorithmExecutor` 开发 Deep Agent，不等待 MCP 服务端完成；MCP 支线按冻结契约完成真实执行链路。
4. 并行期间避免两边同时修改 `runtime.py`、`bootstrap.py`、Compose 和事件目录；这些公共文件按阶段指定单一写入者。
5. MCP 支线先完成独立验收，再以普通 merge 合回 DeepAgent 主分支；合并后由主线完成真实 worker、checkpoint、Store、图路由和用户输出集成。
6. 新分支只保留一条新运行路径，不在同一个镜像内维护旧 stdio 与新 HTTP 双路径。旧版本依靠冻结 commit、声明式环境哈希和后续镜像 digest 回退。
7. 本轮不实施 Shadow、流量实验 A/B、Canary、多 Agent、Deep Agent 内 HITL、Sandbox、自由代码执行或派生数据长期保存。

---

## 2. 开始条件与工作树处理

### 2.1 分支约定

建议分支名遵循仓库 `keyword(function)/dec` 规则：

| 用途 | 建议分支 | 创建者 |
|---|---|---|
| DeepAgent 集成主线 | `feat(agent)/deep-agent-integration` | 你 |
| MCP 协作支线 | `feat(mcp)/causal-mcp-v2` | MCP 协作成员 |

你负责签出 DeepAgent 主分支。当前任务不执行签分支、提交、推送或 worktree 操作。

### 2.2 当前未提交内容

当前工作树已确认包含：

- 用户已有的 `AGENTS.md` 修改；
- 两份 DeepAgent Markdown 文档；
- 两份 DeepAgent draw.io 图；
- `.$deep-agent-integration-plan.drawio.bkp` 备份文件。

执行时应先由你完成文档备份，再创建 DeepAgent 主分支。不得使用 `git reset --hard`、`git checkout --` 或清理命令覆盖现有内容。第一批 DeepAgent 文档提交只暂存明确的产品文档、技术文档、实施计划和正式 draw.io 图；不得顺带暂存 `AGENTS.md` 或 `.bkp`。备份文件是否删除由你自行处理。

建议执行前只读确认：

```powershell
git status --short --branch
git log -1 --oneline --decorate
git diff -- AGENTS.md
```

建议创建主分支：

```powershell
git switch -c "feat(agent)/deep-agent-integration"
```

### 2.3 基线身份

旧框架源码锚点继续使用：

```text
4bea85fdea7292e85577d2b9b3e4c1c763b50829
release: tag 0.1.0 发版
```

产品文档已经记录 `requirements.txt`、Dockerfile 和三份 Compose 的 SHA-256。实施开始时只复核这些哈希，不重新声称旧系统已经完成真实模型、MCP、RAG 或 Docker 验收。

---

## 3. 双方职责与文件所有权

### 3.1 主线实施者（你）

你负责除 MCP 纵向切片之外的全部改造：

- 依赖兼容簇与 Deep Agents/DeepSeek Spike；
- 共享领域 schema、AlgorithmSpec、Registry、Executor Protocol 和 fake executor；
- Deep Agent graph、State、runtime context、Profile、prompt 和预算；
- Action Ledger、evidence reducer、State projection 和 checkpoint 语义；
- 两份长期记忆、`AsyncPostgresStore` 和已冻结的 filesystem permission；
- PC、OLC、DirectLiNGAM 的应用侧 Adapter、预处理、格式统一和硬契约校验；
- RAG evidence-only 与 Web evidence 原子 Tool；
- 外层 LangGraph 路由、FinalizationGate、report 和主图选择；
- PostgreSQL Store/bootstrap/readiness、Job fencing、恢复与取消集成；
- 公共事件、结果展示、SSE 脱敏和必要的前端契约；
- 最终文档、CHANGELOG、版本身份与整体验收。

### 3.2 MCP 协作成员

MCP 协作成员负责完整的 MCP 纵向切片：

- `mcp==2.2.0` Streamable HTTP over HTTP/1.1 server；
- 固定内部 `execute_algorithm` 能力和 runner registry；
- bearer/HMAC 调用验证、可信上下文校验和 MySQL strong read；
- PC、OLC、DirectLiNGAM runner 的服务端注册和 capability/version/spec digest 校验；
- 有界进程池、等待队列、算法 deadline、容量拒绝和迟到结果处置；
- 进程级共享 HTTP pool、长期 `McpClientPool(N×K)`、成员选择、drain、generation 重建和取消清理；
- `McpAlgorithmExecutor` 对共享 `AlgorithmExecutor` 协议的实现；
- 独立 MCP requirements、Dockerfile、Compose service、health/readiness 和资源限制；
- MCP 内部日志事件、错误映射、并发/故障注入、真实算法和容器验收；
- MCP 相关部署、测试和可观测性文档草稿。

RAG、Web Search、Deep Agent prompt、FinalizationGate、报告和公共 SSE 不进入 MCP 支线。

### 3.3 共享契约的所有权

以下文件或等价实现必须先在 DeepAgent 主分支建立并提交，再作为 MCP 子分支的起点：

```text
Agent/deep_agent_tools/models.py
Agent/deep_agent_tools/algorithm_specs.py
Agent/deep_agent_tools/algorithm_executor.py
Agent/deep_agent_tools/error_codes.py
Agent/deep_agent_tools/identity.py
```

共享契约至少冻结：

- `AlgorithmSpec`、`AlgorithmResult`、`Diagnostics`、`AlgorithmResultProvenance`；
- `ActionAttempt`、`InvocationRecord` 和 reducer 的键/单调更新规则；
- `McpInvocationContext` 的 canonical UUID、时间窗口、attempt 和 lease 字段；
- `AlgorithmExecutor.execute()` 输入、返回值、取消和超时语义；
- `capability_id/version/spec_digest` 的兼容检查；
- `invocation_id/retry_ordinal/result_ref` 的生成规则；
- 安全错误码和 `valid/invalid_input/not_applicable/not_ready/execution_failed/timed_out` 边界；
- raw result 的 canonical JSON、SHA-256、大小和 serialization version；
- 默认配置名及超时层级。

协作成员可以修改公共文件，但共享 schema 发生破坏性变化时，必须先在主线形成契约变更提交，再同步 MCP 支线；不得在两条分支分别维护同名但不同义的模型。

### 3.4 并行期间的单一写入者

为减少合并冲突，MCP 支线存续期间按下表控制公共文件：

| 文件/区域 | 并行期写入者 | 说明 |
|---|---|---|
| `requirements.txt` | 主线 | 先完成兼容簇；MCP 支线不重复改主程序版本 |
| MCP 专用 requirements/Dockerfile | MCP 支线 | 独立镜像固定 `mcp==2.2.0` |
| `app/agent/worker/runtime.py`、`bootstrap.py` | MCP 支线 | 只接入 MCP pool；主线暂以独立 graph fixture/fake runtime 开发 |
| 三份运行 Compose | MCP 支线 | 只增加 `causal-mcp`、配置、health、依赖和资源边界 |
| `observability/event_catalog.py` 的 MCP 事件 | MCP 支线 | Deep Agent/公共事件由主线在合并后追加 |
| `Agent/deep_agent/**` | 主线 | MCP 支线不得修改 |
| RAG/Web/Finalization/report/public events | 主线 | MCP 支线不得修改 |
| 当前事实文档 | 合并后由主线统一收口 | 支线只提交 MCP 专项事实草稿或单独小节 |

主线在 MCP 合并前不得提前修改表中由 MCP 支线持有的公共文件。需要紧急变更时，先通过独立契约提交同步两条分支。

---

## 4. 总体实施顺序与门禁

| 阶段 | 内容 | 负责人 | 是否可并行 | 进入下一阶段的门禁 | 本轮状态 |
|---|---|---|---|---|---|
| P0 | 分支、文档基线、依赖与协议 Spike | 你；MCP Spike 由协作者配合 | 部分 | 真实依赖、DeepSeek、ToolStrategy、MCP HTTP 最小链路通过 | 未在本轮复验；当前环境缺少目标包和凭据 |
| P1 | 共享领域/传输契约 | 你 | 否 | schema snapshot、reducer、identity、fake executor 测试通过并提交 | 代码和测试已完成；已提交 |
| P2-M | causal-mcp 纵向切片 | MCP 协作者 | 与 P2-U 并行 | MCP 分支独立完成真实算法、并发和故障验收 | 未开始 |
| P2-U | Deep Agent 基础、Memory、Adapter、RAG/Web | 你 | 与 P2-M 并行 | fake executor 下状态、权限、工具和结构化终态测试通过 | 协议级代码和测试已完成；真实依赖/服务未验收 |
| P3 | 合并 MCP 并接入 worker | 你主导，协作者配合 | 否 | 真实 executor 替换 fake 后集成测试通过 | 未开始 |
| P4 | Finalization/report/events/UI 收口 | 你 | 否 | 三类 outcome、degraded、SSE、主图和 Job 终态通过 | 未开始 |
| P5 | 真实依赖与恢复验收 | 双方按模块负责 | 可分层 | Docker、MySQL、PostgreSQL、MCP、DeepSeek、RAG/Web 证据齐全 | 未开始 |
| P6 | 文档、CHANGELOG、版本身份 | 你 | 否 | 当前事实文档与实现一致，验证结果分层记录 | 未开始 |

任一强制门禁失败时应停在当前阶段解决，不能用后续代码、mock 或文档声明绕过。

---

## 5. P0：分支、文档基线与依赖/协议 Spike

### 5.1 文档基线提交

在 DeepAgent 主分支中提交：

```text
Document/planning/deep-agent-integration-plan.md
Document/planning/deep-agent-integration-plan.drawio
Document/planning/deep-agent-integration-technical-design.md
Document/planning/deep-agent-integration-technical-design.drawio
Document/planning/deep-agent-implementation-plan.md
```

不更新 `Document/README.md` 导航，不提交 `.bkp`，不混入现有 `AGENTS.md` 修改。

建议提交标题：

```text
docs(agent):冻结 Deep Agent 实施边界与协作计划
```

### 5.2 Python 依赖 Spike

以 `requirements.txt` 为主程序依赖落点；`requirements-base.txt` 不承载当前 LangChain/MCP 依赖。候选兼容簇为：

```text
deepagents==0.7.13
langchain==1.4.0
langchain-core==1.6.3
langchain-openai==1.6.2
langgraph==1.2.11
langgraph-checkpoint==4.2.0
langgraph-checkpoint-postgres==3.1.2
langgraph-prebuilt==1.1.0
mcp==2.2.0
pydantic==2.13.5  # 候选，以全量解析结果为准
```

新版本移除 `langchain-mcp-adapters`。不要在原虚拟环境直接覆盖安装；在新的 Python 3.11 环境和真实 Docker build 中执行：

1. 全量 `requirements.txt` 解析；
2. clean install；
3. 关键包 import smoke；
4. 当前 unit/integration 回归；
5. 生成并保存解析后的独立 DeepAgent 锁文件；
6. 确认 RAG、管理员和数据库依赖没有被解析器隐式降级。

候选版本只有在完整解析、安装和回归通过后才能写成生产 pin。失败时先调整兼容簇，不进入 P1/P2。

### 5.3 `deepseek-v4-flash` 真实 Responses API Spike

内层 Deep Agent 固定使用：

```text
DEEP_AGENT_MODEL=deepseek-v4-flash
DEEP_AGENT_CONTEXT_WINDOW_TOKENS=1000000
use_responses_api=True
```

API key 只通过安全环境变量注入，不写入仓库、测试快照、日志或文档。真实 Spike 必须验证：

- 单个 function call；
- 同一 response 返回至少两个 function calls；
- `response.id`、function item `id`、`call_id` 在 `AIMessage`/metadata/tool_calls 中的实际映射；
- function output 使用原 `call_id` 正确回填；
- `ToolStrategy(FinalAnalysisDecision)` 成功、schema 失败反馈和修正；
- `structured_response` 可从最终 State 读取；
- v2 `updates/messages/custom/tasks` 流不暴露 reasoning；
- model profile 的 `max_input_tokens=1_000_000` 实际命中；
- summarization 的 85% trigger、10% keep 和摘要后 Tool call 关联。

如果 provider response ID 未被适配层保留，必须在模型节点输出进入 checkpoint 前生成并持久化 `message_execution_id`；不得使用消息下标或手工静态 call id。

### 5.4 最小 Deep Agent/父图 Spike

Spike 只证明框架能力，不直接改完整业务图：

- `create_deep_agent()` 可初始化；
- 自定义 State 继承 `DeepAgentState`；
- 编译图可作为父 LangGraph 节点调用；
- 父 checkpointer 可恢复内层 messages/files/structured response；
- runtime context 不进入 checkpoint；
- 取消和 `JobExecutionRevoked` 能向外传播；
- 只注册领域 fake Tool、`read_file`、`edit_file` 和结构化输出 Tool；
- 默认 `write_file/ls/glob/grep/delete/execute/task/subagent` 不出现。

### 5.5 MCP 2.2 最小协议 Spike

MCP 协作成员验证 `mcp==2.2.0` client 到 server 的真实 Streamable HTTP：

- 协议发现/协商：现代 v2 路径使用 `server/discover`，只有 legacy fallback 才验证 `initialize`；
- list/call/error；
- JSON response；
- bearer header；
- 无状态 session 时 `mcp_session_id=None`；
- 两个并发请求的响应关联；
- 连接断开、取消和 context 关闭后无残留 receive task。

本阶段只做最小 server/client，不提前实现完整算法进程池。

### 5.6 P0 完成条件

以下证据全部成立才进入共享契约冻结：

- Python 3.11 clean install 和 Docker build 成功；
- 当前关键回归没有未解释的新失败；
- `deepseek-v4-flash` 多 Tool call 和 ToolStrategy 真实通过；
- 父子图 checkpoint Spike 通过；
- MCP 2.2 HTTP 最小契约通过；
- Deep Agents 裁剪后仍有足够能力收益；若只剩普通 agent loop，应回到产品设计重新评估。

---

## 6. P1：共享契约冻结

### 6.1 领域模型

新增 `Agent/deep_agent_tools/` 基础结构，先实现纯模型与 reducer，不连接 MCP：

```text
Agent/deep_agent_tools/
├── __init__.py
├── models.py
├── algorithm_specs.py
├── registry.py
├── algorithm_executor.py
├── error_codes.py
├── identity.py
└── fake_algorithm_executor.py
```

完成以下工作：

1. 固定 `AlgorithmSpec`、三个首版 capability 及 canonical `spec_digest`。
2. 固定 `AlgorithmResult`、`InvocationRecord`、`ActionAttempt`、evidence result 和 provenance。
3. 实现 `merge_algorithm_results` 的不可变重放规则。
4. 实现 `merge_action_ledger` 的 invocation/attempt/revision 单调规则。
5. 实现 `merge_evidence_results` 的不可变重放规则。
6. 实现 UUIDv5 invocation identity，区分 provider response identity 与本地 fallback。
7. 定义传输无关 `AlgorithmExecutor` Protocol 和用于主线测试的 fake executor。
8. 固定安全错误码，不允许把服务端坏输出归类为用户 `invalid_input`。
9. 输出 Pydantic JSON Schema snapshot 和 Tool schema snapshot。

### 6.2 Adapter 与 MCP 的边界

共享契约必须明确：

```text
模型参数
  → 应用侧 Algorithm Adapter
      → authority/fencing
      → 冻结输入身份
      → 算法专属预处理/假设检查
      → AlgorithmExecutor.execute(command, trusted_context)
      → 标准化
      → result_contract 校验
      → raw file + AlgorithmResult + Ledger
```

MCP 服务只执行内部 command，不维护模型 description、公开名称或第二份完整 AlgorithmSpec。`/ready` 返回的 capability/version 摘要只用于兼容检查，不用于动态注册模型 Tool。

### 6.3 P1 测试

建议新增：

```text
tests/unit/agent/test_deep_agent_models.py
tests/unit/agent/test_algorithm_specs.py
tests/unit/agent/test_algorithm_registry.py
tests/unit/agent/test_invocation_identity.py
tests/unit/agent/test_action_ledger_reducers.py
tests/unit/agent/test_algorithm_executor_contract.py
tests/unit/agent/snapshots/
```

至少覆盖：重复 ID、未知 capability、同 key 不同内容、旧 revision 覆盖、新旧 attempt、provider id 缺失、本地 fallback 恢复、schema 稳定性和错误码边界。

### 6.4 MCP 子分支创建点

P1 测试通过并提交后，将该 commit 作为 MCP 子分支唯一基点。建议提交标题：

```text
feat(agent):建立 Deep Agent 与 MCP 共享执行契约
```

协作成员从该提交创建 `feat(mcp)/causal-mcp-v2`。此后共享契约只通过小型、可审查的契约提交同步，不在两条分支各自修改。

### 6.5 本轮 P1 实施状态

6.1 中列出的八个共享代码文件和 6.3 中列出的测试文件已经落地；快照同时提供完整 Pydantic JSON Schema、完整 Tool schema 以及用于审查的标题/required/properties/SHA-256 manifest。P1 使用 fake executor 和纯 Pydantic reducer 完成，未把 MCP transport、数据库连接、Job authority 或 Deep Agent runtime 偷渡进共享模块。

本轮 P1 定向测试共 28 项通过。由于 P0 的目标依赖未安装，本轮没有在现有 Agent/worker 全量测试收集失败后继续扩展实现，也没有把 `tests/unit/agent` 的既有导入错误标记为 P1 失败；后续进入 P2 前必须先恢复 P0 兼容簇并重新执行全量回归。按协作门禁，当前代码可以作为 MCP 子分支的契约候选基点，但由于本轮未提交，不能把它描述为已经建立了可供协作者直接签出的 commit。

---

## 7. P2-M：MCP 协作支线

### 7.1 服务端结构

由 MCP 协作成员在 `Agent/CausalAgentMCP/` 内实现：

```text
Agent/CausalAgentMCP/
├── app.py
├── config.py
├── auth.py
├── models.py
├── runner_registry.py
├── executor_pool.py
├── service.py
├── health.py
├── requirements.txt
└── Dockerfile
```

目录可根据当前实现最小调整，但职责不得重新集中到单个 `mcp_server.py`。

服务端必须做到：

- Streamable HTTP over HTTP/1.1；
- 私有网络部署，不映射宿主业务端口；
- bearer 服务身份 + HMAC 调用签名；
- canonical UUID、key id、签名时窗和 constant-time compare；
- 每次调用用 MySQL primary 强读复核 Job、用户、文件、attempt 和 lease；
- 固定 runner registry，拒绝未知 capability/version/spec digest；
- 只返回结构化、安全错误，不返回堆栈、SQL、路径、文件正文或 secret；
- `/ready` 区分进程可用、MySQL strong read 和进程池可接收探针；
- MCP session 不保存 Job/checkpoint/Action Ledger。

### 7.2 CPU 进程池与背压

实现默认：

```text
CAUSAL_MCP_PROCESS_WORKERS=2
CAUSAL_MCP_QUEUE_CAPACITY=4
CAUSAL_MCP_ENQUEUE_TIMEOUT_SECONDS=5
CAUSAL_MCP_ASGI_WORKERS=1
OMP_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
MKL_NUM_THREADS=1
```

算法 runner 只能在有界进程池运行。async handler 只处理校验、强读、排队和结果封装。必须验证：

- 2 running + 4 queued；第 7 个请求返回 `MCP_CAPACITY_EXHAUSTED`；
- health/readiness 在两个 CPU 任务运行时仍可响应；
- 算法 deadline 先于 HTTP read timeout；
- timeout 后迟到结果不被采用；
- 子进程不继承数据库连接、HTTP client、MCP client 或 secret；
- PC/OLC/DirectLiNGAM 的峰值 RSS、CPU 时间和耗时被记录；
- 连续 timeout 后按设计回收进程，而不是假设 `Future.cancel()` 能硬杀任务。

### 7.3 客户端池与 executor

建议新增：

```text
app/agent/worker/mcp_client_pool.py
Agent/deep_agent_tools/mcp_algorithm_executor.py
```

默认配置：

```text
CAUSAL_MCP_CLIENT_POOL_SIZE=2
CAUSAL_MCP_MAX_IN_FLIGHT_PER_CLIENT=1
CAUSAL_MCP_POOL_ACQUIRE_TIMEOUT_SECONDS=5
CAUSAL_MCP_HTTP_MAX_CONNECTIONS=8
CAUSAL_MCP_HTTP_MAX_KEEPALIVE_CONNECTIONS=4
```

实现要求：

- 每个 worker OS 进程一个共享 HTTP client 和一个长期 Client pool；
- 每个成员有独立 Client context、member id、generation、health、draining 和 inflight；
- acquire 在同一 Condition 临界区按 normalized load + round-robin 选成员并占用容量；
- 普通业务 `is_error=True` 不销毁成员；transport/protocol 损坏才 drain/rebuild；
- 成员重建不主动取消其他健康成员；
- A 模式 `K=1` 与 B 模式 `K>1` 使用同一实现；
- B 不安全时回退配置到 A，不维护第二套代码；
- 重试只覆盖连接失败、503/busy 和明确尚未开始的只读调用；
- 同一逻辑调用重试复用 `invocation_id` 并递增 `retry_ordinal`；
- worker drain 后关闭成员 context，再关闭共享 HTTP client，不遗留后台 task。

### 7.4 Compose、配置与日志

MCP 协作成员负责在开发、staging、production Compose 中增加：

- `causal-mcp` 私有 service；
- healthcheck 和 worker 的 `service_healthy` 依赖；
- MySQL primary、共享只读代码/必要数据边界；
- secret/env 注入，但不提交 secret；
- 单副本、CPU/内存和 BLAS 线程限制；
- 不暴露宿主端口；
- 独立 MCP 镜像，不安装主应用无关的 RAG/前端依赖；
- Alloy 采集标签和 `service=mcp` JSON stderr；
- `mcp.request.accepted`、capacity、tool、process、client reconnect 等目录事件。

### 7.5 MCP 支线验收

MCP 子分支合并前必须提供：

1. v2.2 client/server 的协议发现/协商、call、error 真实 HTTP 证据，并区分现代 `server/discover` 与 legacy `initialize` fallback；
2. PC、OLC、DirectLiNGAM 真实算法调用；
3. HMAC 正常、过期、错误 key、错误签名和越权输入测试；
4. MySQL strong read 与旧 lease 拒绝；
5. A/B Client pool 并发、故障影响、drain、generation 重建和取消清理；
6. 进程池容量、event loop 非阻塞、算法 timeout 和内存基准；
7. Compose `config --quiet`、镜像 build、health/readiness；
8. stdout/stderr、日志字段和敏感内容零泄漏测试；
9. MCP 专项文档变更清单与真实/未执行证据。

建议提交批次：

```text
feat(mcp):实现 MCP v2 私有算法服务
feat(mcp):实现长期客户端池与算法执行器
test(mcp):覆盖并发故障与真实算法契约
docs(mcp):补充部署并发与故障边界
```

---

## 8. P2-U：主线并行实现

### 8.1 Deep Agent State 与 runtime context

在 `Agent/deep_agent/` 中实现：

```text
Agent/deep_agent/
├── __init__.py
├── graph.py
├── state.py
├── context.py
├── profile.py
├── prompts.py
├── finalization.py
├── memory.py
└── postgres_store.py
```

本阶段使用 fake executor，不修改 MCP 支线持有的 worker runtime/bootstrap。完成：

- `ProjectDeepAgentState(DeepAgentState)`；
- 父 State → DeepAgent State → 父 State 的显式投影；
- 执行对象只放 runtime context，不进入 checkpoint；
- Action Ledger 始终存在，可为空；
- algorithms/RAG/Web evidence 独立 reducer；
- `FinalAnalysisDecision` ToolStrategy schema；
- 模型/工具/递归/finalization retry 预算；
- reasoning、系统提示和 raw Tool 数据不进入公共输出。

### 8.2 Filesystem 与长期记忆

实现 `CompositeBackend(StateBackend + StoreBackend)`，并冻结如下模型写权限：

```python
permissions = [
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
```

当前默认 P2-U 必须实现和验证：

- 只注册 `read_file`、`edit_file`；
- 两份固定 memory 文件可修改；
- 其他虚拟路径写入被拒绝；
- trusted Adapter 直接调用 backend 仍可写 raw result；
- 可信 `user_id` namespace 隔离；
- 两份文件 create-if-absent，不覆盖已有内容；
- checkpoint cleanup 不删除 Store；
- 查看/删除 UI、保留期、并发冲突和独立审计表明确不实现。

官方 StoreBackend 的两份 memory 文件初始化、PostgreSQL 重启后记忆保留、跨 Job 保留和真实 Store cleanup，列入后续 PostgreSQL/长期记忆真实验收；当前不以这些真实服务证据阻断默认 P2-U 协议完成。

### 8.3 Algorithm Adapter 与依赖调度

在主线实现：

```text
Agent/deep_agent_tools/dependency_planner.py
Agent/deep_agent_tools/algorithm_tools.py
Agent/deep_agent_tools/adapters/
```

逐算法核对现有 runner，不能从规划表推断精确输入合同。PC、OLC、DirectLiNGAM 分别记录：

- 参数 schema 和默认值；
- 数据类型、缺失、样本量、目标变量和统计假设；
- 可逆且确定性的预处理 recipe；
- graph orientation/weight semantics；
- diagnostics、warnings 和公开摘要；
- raw result canonical JSON/hash/size/version；
- 超时和 concurrency key。

P2-U 收口后，`AlgorithmSpec` 是依赖调度的唯一事实源：工具身份、`requires/produces`、算法级 timeout、`concurrency_key` 和默认并发均从 Spec/Registry 派生，调度器不得再维护第二套算法默认值。`DeepAgentBudget` 只提供当前 Job 的全局并发上限和安全 timeout ceiling。调度器当前只覆盖 PC、OLC、DirectLiNGAM，不建立 Algorithm 与 RAG/Web 之间的跨域 artifact 依赖。

P2-U 只完成可独立测试的协议调度器：必须保留同轮所有 calls，按 `requires/produces` 建 DAG；无依赖并行，有依赖分层串行，兄弟部分失败不互相取消，依赖失败返回 `not_ready`，未知 Tool/重复 call id/环返回安全错误供 Deep Agent 重规划一次。P3 才在真实 Deep Agent Tool dispatch 边界接入该调度器，确保默认 ToolNode/执行器不能绕过它；P5 再用真实 DeepSeek 验证同轮独立调用并行、依赖调用串行和多 Tool identity 关联。

### 8.4 RAG evidence-only

从当前真实 RAG runtime 拆出 `get_evidence()`，保留：

- active release/readiness；
- dense/sparse/BM25/MMR；
- merge/rerank、阈值和 top_k；
- evidence compression 和 parser；
- citation、release identity、fingerprint；
- unavailable/empty/protocol error。

Deep Agent 路径不得进入旧的 RAG question planner 或 answer model。通过模型调用计数证明一次 evidence Tool 不产生内部回答模型调用。旧 RAG 评测、publish 和 active pointer 生命周期不因本次改造改变。

### 8.5 Web evidence Tool

复用当前 SearXNG arXiv/Crossref/OpenAlex 的确定性检索、过滤、top-3 轮转和最多 9 条引用；移除 Deep Agent 路径中的旧 planner。Deep Agent 直接提供结构化 query，Tool 返回 snippet 和来源元数据。保留 `web_search_enabled` 的用户级关闭语义，关闭时 Tool 不可调用或返回稳定禁用结果，不能偷偷访问网络。

### 8.6 主线并行阶段测试

建议新增：

```text
tests/unit/agent/test_deep_agent_state.py
tests/unit/agent/test_deep_agent_profile.py
tests/unit/agent/test_deep_agent_permissions.py
tests/unit/agent/test_deep_agent_memory.py
tests/unit/agent/test_tool_dependency_planner.py
tests/unit/agent/test_algorithm_adapters.py
tests/unit/agent/test_rag_evidence_tool.py
tests/unit/agent/test_web_evidence_tool.py
tests/unit/agent/test_final_analysis_decision.py
tests/integration/agent/test_deep_agent_checkpoint.py
```

并行阶段只声明 fake executor、isolated Store 或协议级测试通过；不能写成真实 MCP、PostgreSQL、RAG、SearXNG 或模型验收完成。

### 8.7 P2-U 实施状态

8.1—8.6 已形成协议实现和单元测试基础，但尚未完成父图/worker 主链接入，不能写成
完整落地。2026-09-14 的实现审计补齐了真实 graph 的 `state_schema`、
`context_schema`、模型 profile、摘要/调用预算 middleware、filesystem permission
和关闭默认 general-purpose subagent 的 Harness Profile；同时修正了 RAG evidence
字段与不可用降级、依赖调度的并发/超时边界、旧 runner payload 标准化、
`arrows="from"` 方向语义和 DirectLiNGAM CSV 硬校验。隔离 Python 3.11 容器使用
第 4.1 节目标依赖簇成功构造 `CompiledStateGraph`，但仓库声明依赖和正式镜像仍未升级。

依赖调度器在本阶段只作为 `AlgorithmSpec` 驱动的协议组件收口；真实 Deep Agent
Tool dispatch 接入属于 P3，真实 DeepSeek 并发和依赖行为验证属于 P5。当前不把
调度器单元测试通过写成已经控制生产 ToolNode 执行。

随后补齐了此前与本计划不一致的 Tool 运行时写回：`build_deep_agent()` 会把项目
Tool materialize 为 LangChain Tool；Algorithm、RAG、Web 均从注入的 `ToolRuntime`
读取 tool-call identity、State 和 `AgentRunContext`，不再使用静态 fallback。每次模型
响应后的 middleware 会生成并持久化新的 `message_execution_id`；Tool 返回的同一个
`Command` 同时包含匹配的 `ToolMessage`、AlgorithmResult 或 evidence map，以及 terminal
`InvocationRecord`。可预期算法失败以 `execution_failed` 返回模型并记录 failed Ledger，
不会中断分析；取消、lease 失效和 execution guard 撤销仍保持控制流异常。按本轮决策，
checkpoint 只提交 terminal revision，queued/running 留给 P4 公共事件适配，不改变既定
单调 reducer 和失败尝试保留语义。

当前证据边界为：

```text
tests/unit/agent + tests/integration/agent：297 passed, 1 skipped（Docker unit-test）
ToolRuntime/Command/ToolNode 定向：6 passed
真实 Deep Agents / LangGraph：目标依赖簇下 graph 构造及 ToolRuntime 定向 7 passed；未调用真实模型
真实 PostgreSQL Store/checkpoint：from_conn_string 生命周期已修正；未连接隔离数据库、未验证重启保留
真实 RAG active release / SearXNG / DeepSeek：未执行，无真实服务/凭据
```

仍未完成的关键项包括：父 State → Deep Agent → 父 State 的真实 worker 调用；P3 的
依赖调度器 Tool dispatch 接入和 artifact publication；MCP 传输重试的 `retry_ordinal`
注入；P0 真实 DeepSeek response/call identity 字段映射；官方 StoreBackend 两份
memory 文件的初始化及 PostgreSQL 重启保留；真实 MCP、RAG、SearXNG、DeepSeek
与 checkpoint 恢复验收。官方 StoreBackend 相关项目不阻断当前默认 P2-U，但必须在
后续真实 PostgreSQL/长期记忆验收中单独报告。
P2-U 不修改 P2-M 持有的 MCP server、worker runtime/bootstrap、Compose 或 requirements；
后续 P3 合并前仍须按 9.1—9.4 的共享契约、普通 merge、真实 MCP 握手和 fencing 门禁执行。
旧 MCP 文件没有物理删除，只在未来新运行链接入时按计划移除不可达引用，并在需要删除
文件时另行取得明确授权。

---

## 9. P3：合并 MCP 支线并接入 worker

### 9.1 合并前检查

MCP 支线必须：

- 工作树干净；
- 合并最新共享契约提交；
- MCP 定向测试通过；
- Compose 静态验证通过；
- 提供已执行、未执行和残余风险清单；
- 不包含真实 secret、连接串、用户数据或本地备份。

主线在合并前完成 P2-U 的 fake executor 测试，并暂停修改 MCP 支线持有的公共文件。

### 9.2 合并方式

使用普通 merge 保留协作历史，不 rebase 已共享分支，不使用全局 ours/theirs，也不 force-push。建议：

```powershell
git fetch origin
git merge --no-ff "origin/feat(mcp)/causal-mcp-v2"
```

冲突必须按语义逐项解决，重点核对：

- `requirements.txt` 只有一套兼容版本；
- `AlgorithmExecutor` 接口与 MCP 实现一致；
- worker runtime 同时持有 MCP pool、PostgreSQL checkpointer/Store 和 compiled graph；
- bootstrap fail-fast 顺序正确；
- Compose 同时保留数据库、RAG、SearXNG、observability 和新增 MCP；
- MCP 与 Deep Agent 日志目录事件不互相覆盖；
- secret/env 名称与技术设计一致；
- cancel/fencing 在 pool acquire、远程返回和 State 合并前均检查。

### 9.3 worker 生命周期接入

合并后由主线完成：

```text
进程 bootstrap
  → MySQL readiness
  → PostgreSQL checkpointer pool/schema
  → AsyncPostgresStore schema/readiness
  → Algorithm Registry fail-fast
  → MCP pool connect/capability handshake
  → RAG readiness
  → Deep Agent/父图编译
  → worker ready
  → slots claim Job
```

MCP pool 是进程级资源，不再按 slot 启动 stdio server。slot 继续拥有执行上下文和 graph invocation，但不能把 Client、HTTP pool、Store 或 execution guard 序列化到 State。

P3 同时把 P2-U 的 AlgorithmSpec 调度器接入真实 Deep Agent Tool dispatch：只处理
PC、OLC、DirectLiNGAM 三类算法 Tool；同一模型响应中的独立调用按 Job 全局上限
并行，有依赖调用按 `requires/produces` 分层串行，默认 ToolNode/执行器不得绕过
该调度边界。调度器的实际接入点必须先由目标 Deep Agents/LangGraph 依赖 Spike
确认，不预设未验证的 middleware 或 ToolNode API。

### 9.4 旧链路处理

从新运行链中移除以下引用和注册：

- `langchain-mcp-adapters`；
- slot 级 stdio `ClientSession`；
- `load_mcp_tools()` 动态模型 Tool 注册；
- 只取 `tool_calls[0]` 的 normalizer；
- 单个 `causal_analysis_result` 作为新路径权威结果；
- 固定 `mcp → rag → web_search` 数据分析流水线。

旧文件是否物理删除属于重要文件删除，必须在实现时另行取得用户明确授权。未授权时只移除新运行链引用并在验收中确认旧代码不可达，不执行删除。

---

## 10. P4：父图、Finalization、报告与公共事件

### 10.1 父图目标路径

修改 `Agent/causal_agent/graph.py`，保留现有 `agent` 路由职责、fold、report 和确定性安全壳：

```text
agent
  ├─ 普通问答/报告追问 → 现有路径
  └─ 数据型因果分析
       → fold/admission/data_profile
       → deep_agent
       → finalization_gate
       → report
```

路由只读显式 `route_decision`/`fold_decision`。当前关键词快捷判断是否保留必须基于实现测试决定，不能新增第二个前置分类器。

### 10.2 FinalizationGate

实现纯确定性 Gate：

- 从当前 `job_id + attempt_count + lease_epoch` 筛选 eligible results；
- 每个 eligible valid result 恰好一个 assessment；
- 非 valid result 只能 discarded；
- `algorithm_supported` 必须唯一 primary；
- `evidence_only` 不得有 primary；已有 valid 结果时必须全部 discarded；
- `no_valid_algorithm` 要求 eligible set 为空且至少有一次真实算法调用；
- decision 的调用、失败、重跑和结果引用与 Ledger 对账；
- Gate 不调用 LLM、不修改 graph、不补结论。

第一次动态不一致回到同一 Deep Agent，仅重提 `FinalAnalysisDecision`；第二次仍失败则构造 `finalization_status=degraded` 的安全 ReportContext，进入 report。degraded 报告成功持久化后 Job 仍为 `succeeded`，但主图为空。

### 10.3 报告与结果展示

报告事实来源固定为：

- 用户问题和数据画像；
- 当前 Job/attempt/lease 的 AlgorithmResult；
- 程序合并的完整 Action Ledger；
- RAG/Web evidence；
- 经 Gate 允许的 decision 字段；
- degraded 时的安全错误摘要。

报告不得虚构 Tool 调用、结果、因果边或证据。用户可见产物保持：报告、至多一张 `primary_result_ref` 主图、现有预处理图表。其他算法结果、冲突、舍弃原因、置信度和失败进入报告，不新增内部时间线页面。

### 10.4 公共事件与 SSE

修改：

```text
app/agent/worker/event_adapter.py
app/agent/public_events.py
app/agent/worker/result_presenter.py
observability/event_catalog.py
```

要求：

- 内部点号事件仍映射到现有公共 snake_case 事件；
- `tool_call_result` 只增加受控 `status`、`safe_error_code`；
- `final_result.data` 增加 `finalization_status`；
- `invocation_id`、`result_ref`、provider IDs、raw args/results 不进入公共事件；
- reasoning、系统提示、路径、签名、数据库信息和文件正文零泄漏；
- Last-Event-ID、历史重放、稳定 event key 和终态事务不变；
- cancel/revoked 不被错误映射为普通降级。

如果普通聊天页面无需结构变化，则不做无关前端重构，只补充现有附件/主图/报告合同测试。

---

## 11. P5：分层测试与真实验收

### 11.1 静态与单元测试

先运行定向测试，再运行完整 unit：

```powershell
docker compose -f docker-compose.test.yml build unit-test
docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider tests/unit/agent
docker compose -f docker-compose.test.yml run --rm unit-test
```

日志和策略变更还需覆盖：

```text
tests/unit/test_event_catalog.py
tests/unit/test_request_context_contract.py
tests/integration/test_logging_policy.py
tests/integration/test_logging_entrypoints.py
```

单元测试只证明 schema、状态、路由、权限和协议，不证明真实 MCP、数据库、RAG、SearXNG 或 DeepSeek。

### 11.2 依赖与 Compose

至少执行：

```powershell
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.staging.yml config --quiet
docker compose -f docker-compose.prod.yml config --quiet
python -m alembic heads
```

确认 Alembic 仍只有当前合法 head；若本轮未新增 MySQL schema，不创建空 migration。`AsyncPostgresStore.setup()` 属于 PostgreSQL 官方 Store schema 初始化，必须与 checkpointer schema/readiness 分开验证，checkpoint cleanup 不得清理 Store 表。

### 11.3 真实 MCP 验收

在隔离 Compose project 中验证：

- `causal-mcp` 不映射宿主端口；
- worker capability handshake；
- 三个真实算法；
- 2 running + 4 queued + 第 7 个 busy；
- A/B Client pool 故障注入；
- 服务重启、成员 generation 重建和 worker drain；
- queue wait、p50/p95/p99、RSS、CPU、timeout 和错误码分布；
- 旧 lease 迟到结果 discarded；
- 正常流无意外 WARNING/ERROR；
- 合成敏感内容在 stderr/Docker logs/Loki 零命中。

### 11.4 PostgreSQL checkpoint 与长期记忆

真实 PostgreSQL 验证：

- 同一 Job `thread_id=job_id` 恢复；
- 已提交 Tool result 不重算；
- 未提交远端结果允许以相同 invocation identity 重算；
- retry attempts 不丢失或重复；
- raw file 与 result/hash/version 同步可读；
- 两份 memory 文件跨 Job、跨 PostgreSQL 重启保留；
- 不同 user namespace 不串读；
- 固定 memory 文件可 edit，其他 State/Store 虚拟路径拒绝模型写；
- checkpoint cleanup 删除 Job checkpoint/raw file，但不删除长期 Store memory。

### 11.5 真实 DeepSeek 验收

使用 `deepseek-v4-flash` 运行：

1. 零领域 Tool，直接产生 `evidence_only`；
2. 一个有效算法，产生唯一 primary；
3. 多个有效算法，保留全部 assessment 和唯一 primary；
4. valid 结果全部 discarded，产生无主图的 `evidence_only`；
5. 算法全部失败，产生 `no_valid_algorithm` 自然报告；
6. 同轮多 Tool calls 正确关联；
7. 依赖 calls 串行、独立 calls 并行；
8. 首次非法 decision 修正成功；
9. 二次仍非法进入 degraded，Job succeeded；
10. 摘要前后 Tool call、Ledger 和 result refs 不错配。

Fake LLM 不能替代这些证据。

### 11.6 RAG 与 Web

RAG 必须在真实 active release、manifest、embedding fingerprint、Chroma/BM25 产物和模型配置存在时验证 evidence-only；记录内部模型调用计数，证明不再执行旧 answer model。没有真实 active release 时，只能报告 unavailable 合同通过，不能标记真实 RAG 验收完成。

Web 使用真实 SearXNG 验证三来源、snippet、上限、关闭开关、网络失败和引用投影。默认 unit-test 网络关闭，因此真实 Web smoke 必须运行在 Compose 网络中。

### 11.7 Job/SSE/取消恢复

真实 MySQL + worker + PostgreSQL 验证：

- initial/resume 使用同一 checkpoint；
- fold interrupt 进入 `waiting_input` 并正确恢复；
- Agent、pool acquire、MCP、RAG、Web、finalization、report 各阶段取消；
- running cancel 的 draining/worker_confirmed/lease_expired；
- stale worker 不得写 State、事件、assistant 消息或终态；
- terminal event、assistant message 与 Job status 事务一致；
- SSE Last-Event-ID 和历史重放不遗漏、不重复；
- degraded final result 对应 succeeded Job。

### 11.8 完整验收判定

结果必须分层报告：

| 层次 | 可以声明 | 不可以替代 |
|---|---|---|
| unit | schema/reducer/路由/权限通过 | 真实协议和服务 |
| integration | 跨模块静态或隔离契约通过 | 真实模型/RAG/数据库容量 |
| Docker | 镜像、服务、health、网络通过 | 模型选择质量 |
| real MCP | 协议、算法、并发、故障通过 | Deep Agent 终态质量 |
| real DeepSeek | 多 Tool、ToolStrategy、报告通过 | RAG release |
| real RAG/Web | 检索来源和降级通过 | checkpoint/Job 恢复 |
| full Job | 端到端恢复、事件、报告通过 | 未执行的生产负载 |

任何未执行项、环境原因和残余风险必须单独列出。

---

## 12. P6：文档、日志与交付收口

### 12.1 实现过程中同步更新

代码阶段完成后按真实实现更新：

| 文档 | 更新内容 |
|---|---|
| `Document/planning/deep-agent-integration-plan.md` | 只在冻结产品决定变化时更新；标记实现进度但不伪造完成 |
| `Document/planning/deep-agent-integration-technical-design.md` | 最终类名、配置、默认值、验证结论和偏差 |
| `Document/architecture/agent-runtime.md` | 新父图、进程级 MCP pool、Deep Agent、Store、事件和恢复当前事实 |
| `Document/architecture/job-file-lifecycle.md` | checkpoint State/raw file 与长期 Store 的不同生命周期 |
| `Document/development/deployment.md` | causal-mcp 镜像、Compose、secret、health、drain/restart 和回退 |
| `Document/development/testing.md` | 新定向测试、真实 MCP/DeepSeek/Store/RAG/Web 证据边界 |
| `Document/development/observability.md` | 独立 causal-mcp 容器、事件目录、采集和隐私边界 |
| `Document/api/agent-jobs.md` | final result、degraded 和用户可见产物语义（如 DTO 改变） |
| `Document/api/conventions.md` | `tool_call_result`/`final_result` 白名单字段（如协议改变） |
| `README.md` / `README_EN.md` | 同步新架构、MCP 私有服务、启动与用户可见结果；中英文一致 |
| 两份 draw.io | 只有最终实现与现图不一致时更新 |

按用户决定，本轮不把实施计划加入 `Document/README.md` 导航。

### 12.2 CHANGELOG

只有完整功能实现并完成相应验收后，才在 `CHANGELOG.md` 文件末尾按实际完成日期追加记录。不得改写历史正文，也不得把未执行的真实模型、Docker、数据库或 RAG 验收写成完成。

建议日志结构：

```text
---
y.m.d
- 【Agent：接入 Deep Agent 混合架构】
  - 【领域契约与状态】：...
  - 【MCP v2 算法服务】：...
  - 【证据工具与最终报告】：...
  - 【恢复、事件与长期记忆】：...
  - 【验证】：已执行 ...；未执行 ...
```

### 12.3 文档验证

至少执行：

- 相对链接目标存在；
- 新建/重构文档标题后有“文档职责”和“适用范围”；
- 旧 stdio、固定 `mcp → rag → web_search` 和单结果事实不再出现在当前运行时文档；
- README/README_EN 命令、服务名、目录和架构一致；
- `git diff --check`；
- `git diff -- README.md README_EN.md` 人工对照；
- `git diff -- CHANGELOG.md` 只包含末尾追加；
- 不提交 `.env`、secret、日志、用户文件正文或 `.bkp`。

---

## 13. 建议提交与合并批次

### 13.1 DeepAgent 主分支

```text
docs(agent):冻结 Deep Agent 实施边界与协作计划
build(agent):验证并锁定 Deep Agent 兼容依赖
feat(agent):建立 Deep Agent 与 MCP 共享执行契约
feat(agent):实现 Deep Agent 状态与长期记忆
feat(agent):实现算法 Adapter 与多工具调度
feat(agent):将 RAG 和 Web 改为证据工具
merge(mcp):合并 causal-mcp v2 执行链路
feat(agent):接入父图与 FinalizationGate
feat(agent):统一报告结果与公共事件
test(agent):补充 Deep Agent 真实依赖与恢复验收
docs(agent):同步 Deep Agent 当前运行事实与验收边界
```

### 13.2 MCP 子分支

```text
feat(mcp):实现 MCP v2 私有算法服务
feat(mcp):实现长期客户端池与算法执行器
feat(mcp):增加私有服务部署与健康检查
test(mcp):覆盖协议并发故障与算法契约
docs(mcp):记录 MCP 部署与验收边界
```

提交应保持可独立 review；不要把依赖升级、共享 schema、MCP 服务、Deep Agent graph、文档和格式化混成一个大提交。

---

## 14. 阻断条件与降级决策

| 情况 | 处理 |
|---|---|
| 完整依赖无法解析或现有关键回归失败 | 停在 P0，调整兼容簇，不进入业务迁移 |
| `deepseek-v4-flash` 多 Tool call ID 无法稳定关联 | 停在 P0，不使用消息下标/静态 ID 绕过 |
| Deep Agent 裁剪后没有实质能力收益 | 回到产品决策评估是否改用 `create_agent + LangGraph` |
| MCP `K>1` 不安全 | 使用同一实现退回 `N×1`，不阻塞 MVP |
| 长期 Client `K=1` 仍无法可靠重建 | 退回每调用独立 Client context、共享 HTTP pool |
| filesystem permission 不能强制精确路径 | 长期记忆子能力不得标记完成；不增加其他防御方案 |
| 真实 RAG active release 不可用 | 继续完成代码和 unavailable 合同，但整体 RAG 验收保持未完成 |
| 算法峰值内存不支持 2 个进程 | 按实测下调进程/Tool 并发，不为了默认值冒险 |
| 需要物理删除旧 MCP 文件 | 停止并取得用户明确授权；未授权只移除运行链引用 |
| checkpoint schema 不兼容旧版本 | 回退只接收新 Job，不让旧 worker静默恢复新 checkpoint |

---

## 15. 完成定义

只有同时满足以下条件，Deep Agent 集成才能标记完成：

1. 新 DeepAgent 分支中只有一条生产运行路径，旧版本可由冻结镜像/commit 回退。
2. `deepseek-v4-flash` 的真实 Responses 多 Tool、ToolStrategy、summarization 和字段映射通过。
3. AlgorithmSpec 是模型工具定义的唯一事实源，三个算法 Adapter 合同和 provenance 通过。
4. MCP v2 私有服务、鉴权、strong read、进程池、队列和 A/B Client pool 验收通过。
5. Action Ledger、invocation identity、checkpoint replay、cancel 和 lease fencing 没有丢失或错配。
6. 两份 memory 文件可按用户隔离持久化，精确路径可写，其他虚拟路径拒绝模型写入。
7. RAG evidence-only 与 Web evidence 的真实来源和降级边界得到相应证据。
8. 三种正常 outcome 和 finalization degraded 都能生成诚实报告；只有真实 primary 生成一张主图。
9. 公共事件、SSE、历史重放和最终结果没有泄露内部 ID、参数值、raw result 或 reasoning。
10. Docker、MySQL、PostgreSQL、MCP、DeepSeek、RAG/Web 和 full Job 证据按层次分别报告。
11. 当前事实文档、中英文 README 和追加式 CHANGELOG 与最终实现一致；实施计划不加入 `Document/README.md` 导航。
12. 未执行项、环境限制和残余风险全部显式列出，没有用 unit/mock 冒充真实验收。

完成后再冻结新版本 commit、依赖锁、镜像 digest、schema/spec/prompt、RAG release/fingerprint、`deepseek-v4-flash` 配置和版本化问题集，供后续独立对比或灰度方案使用。
