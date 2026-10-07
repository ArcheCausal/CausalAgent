# Agent 工具路由历史调研

文档职责：记录因果工具、RAG 和学术 Web 检索从旧父图到 Deep Agent 的路由契约变化，并说明“零因果 MCP 也能完成/降级”的引入时间与当前修复边界。

适用范围：排查 `Agent/causal_agent`、`Agent/deep_agent`、MCP 因果工具、RAG/Web evidence tool、FinalizationGate 和 Job 的 `web_search_enabled` 语义；历史事实以 Git 提交和当前源码为准。

## 结论

截至 2026-09-20，可以确认有两个时间点：

1. **契约开始允许零因果工具调用**：2026-09-14 21:06 的 `6cf4702`（`feat: deepagent p2-u ... RAG evidence-only`）。该提交的 Deep Agent 系统提示明确写成“自主选择零个、一个或多个”因果算法、知识库证据或 Web 证据工具；同一提交的 `FinalAnalysisDecision` 将 `evidence_only` 作为合法 outcome，静态 schema 没有要求算法结果存在。
2. **该行为进入实际生产父图**：2026-09-15 17:35 的 `d2d497b`（`feat(agent): integrate Deep Agent worker and finalization path`）首次把 Deep Agent 接到 worker 父图，18:23 的 `a107e8d` 再补齐完整 MCP/worker 运行时。该版本的 `FinalizationGate` 只对 `no_valid_algorithm` 要求真实算法调用，`evidence_only` 可以在没有算法调用时通过；Gate 失败后的既有一次修正预算用尽后才会进入 `finalization_status=degraded`。

因此，问题不是最近某个模型突然“不听话”，而是 **2026-09-14 引入的新 Agent 合同把“工具选择”从强制规划器改成了模型自主选择，并且把零算法的 evidence-only 设计成合法终态；2026-09-15 后该合同随新父图生效**。

## 与旧实现的对照

| 时期 | 证据 | 实际契约 |
| --- | --- | --- |
| 2025-11-26，`76b228f` | `Agent/tool_node/causal_analysis_task.py` 的 `select_causal_tool` | 先列出 MCP 工具，再由选择器返回 `selected_tool`；选择失败时回退到第一个工具。 |
| 2026-07-26，`3d8dbf7` | `Agent/causal_agent/nodes.py` 的 `mcp_planner_node` | 文档字符串写明“强制模型从可用 MCP tools 中选择一个”，并通过 tool choice 进入因果工具调用。旧父图还把 `preprocess -> mcp -> rag` 固定串起来，联网搜索由 `web_search_enabled` 决定是否继续。 |
| 2026-09-14，`6cf4702` | `Agent/deep_agent/prompts.py`、`Agent/deep_agent_tools/models.py`、`Agent/deep_agent/finalization.py` | Deep Agent 允许零、一个或多个工具；`evidence_only` 可在没有算法结果时表达“只返回证据”。 |
| 2026-09-15，`d2d497b` / `a107e8d` | `Agent/causal_agent/graph.py`、`Agent/deep_agent/finalization.py`、worker runtime | worker 当前入口改为 `preprocess -> deep_agent -> finalization_gate -> report`；RAG/Web 不再是父图必经节点，而是 Deep Agent 可调用的领域工具。 |

“以前必须选一个然后给出置信度”需要拆开看：旧选择器强制的是 `selected_tool` 和 `reason`，并不是选择器本身的置信度；`confidence` 是旧 RAG/边评估结果字段，以及新 `FinalAnalysisDecision` 的必填字段。新 schema 仍要求 `confidence`，但在旧 Gate 规则下它并不证明任何算法真的运行过。

## 当前问题的直接原因

- Deep Agent 的工具列表包含 `rag_evidence_search` 和 `web_evidence_search`，但“工具已注册”不等于“模型必须调用”。迁移后父图没有再用旧的固定 `mcp -> rag -> web_search` 边强制执行。
- 原来的 `web_search_enabled` 只负责在 `web_evidence_search` 真正执行时决定是否触网；它没有成为模型提示中的必选调用合同，也没有被 FinalizationGate 用来拒绝零 Web 调用。
- RAG 也曾存在同样缺口：模型可以直接产出结构化终态，`evidence_only` 又没有算法调用门禁，因此“开启能力”可能表现为“从未调用”。

## 当前工作树的工具选择合同

- 分析路由始终注入算法运行级约束；`FinalizationGate` 要求当前 Job attempt 至少有一个算法结果。RAG 不再注入强制调用提示，也不因零次 RAG 调用拒绝终态；如果 Agent 选择调用，仍需保留真实 terminal 状态。
- `web_search_enabled=true` 时额外注入一次 `web_evidence_search` 约束；Gate 按相同的 Job、attempt、lease、worker 和输入身份检查 Web terminal ledger。关闭时不强制联网，工具自身仍保持不触网。
- 约束不写入 worker 级静态 system prompt，而是在父图把可信运行上下文投影给 Deep Agent 时按 Job 注入，避免不同 Job 的联网选项互相污染；RAG 的可选性也按当前工具契约保留。

实现位置：

- [Deep Agent 运行级提示](../../Agent/deep_agent/prompts.py)
- [父子 State 投影](../../Agent/deep_agent/state.py)
- [FinalizationGate](../../Agent/deep_agent/finalization.py)
- [父图运行上下文传递](../../Agent/causal_agent/graph.py)
- [当前运行架构](../architecture/agent-runtime.md)

## 证据边界

上述时间线来自本仓库 Git 历史，不代表真实模型线上调用日志。当前单元测试能证明提示注入、运行上下文传递和 Gate ledger 校验；尚未证明真实 DeepSeek/真实 MCP/SearXNG/RAG provider 在生产环境中每次都成功返回。若要确认线上命中率，还需要按 `tool_call_start`、`tool_call_result` 和 Job attempt 统计真实事件。
