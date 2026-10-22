# 开发日志索引

文档职责：按月份提供详细开发日志的入口、主题关键词与已确认发布关联，供 AI 查询历史和团队回溯。

适用范围：本仓库有记录的开发月份；当前技术事实以代码与 [Document/](Document/README.md) 为准，官网发布说明单独维护。

详细记录位于 `changelog/YYYY/YYYY-MM.md`。新记录直接写入所属月份文件，跨月后封存，无需月底搬移正文；具体规则见 [文档维护说明](Document/documentation.md#开发日志) 与 [AGENTS.md](AGENTS.md#日志与-git)。

已知月份时直接读取对应文件；未知月份时先在 `changelog/` 搜索关键词，再读取命中段落：

```powershell
rg -n -i 'FinalizationGate|Action Ledger' changelog
```

以下按月份从新到旧排列，无记录的月份不创建空文件。发布关联表示该月记录提及的已确认发布，`—` 表示尚未登记关联；发布内容须按实际 tag 范围确认，不能直接等同于整个月文件。

## 2026

| 月份 | 主要主题 | 发布关联 |
| --- | --- | --- |
| [2026-10](changelog/2026/2026-10.md) | DeepAgent、算法调度、分析事实、工具可见性、CI、部署密钥、容量测试、日志归档 | — |
| [2026-09](changelog/2026/2026-09.md) | DeepAgent、Action Ledger、FinalizationGate、MCP、设计系统、官网、预发部署 | — |
| [2026-08](changelog/2026/2026-08.md) | PostgreSQL checkpoint、RAG 评测运行台、Gold v2、release readiness、开发者预览 | `v0.1.0` |
| [2026-07](changelog/2026/2026-07.md) | Job/SSE、文件生命周期、多模态索引、Gold、隔离评测、Ragas | — |
| [2026-06](changelog/2026/2026-06.md) | PubMedQA、embedding、benchmark_v2、Ragas、评测校验 | — |
| [2026-05](changelog/2026/2026-05.md) | 数据库迁移、主从读写、Job、worker、SSE、ToolNode | — |
| [2026-03](changelog/2026/2026-03.md) | 混合检索、MMR、证据链、metadata、RAG 评测 | — |

## 2025

| 月份 | 主要主题 | 发布关联 |
| --- | --- | --- |
| [2025-12](changelog/2025/2025-12.md) | CausalAgent 项目命名 | — |
| [2025-11](changelog/2025/2025-11.md) | SSE、思考气泡、图表、报告、Blueprint、MCP 算法 | — |
| [2025-10](changelog/2025/2025-10.md) | checkpoint、MySQL、Alembic、interrupt、Docker | — |
| [2025-09](changelog/2025/2025-09.md) | 数据分析、数据可视化、RAG、causal-learn、人机交互 | — |
| [2025-08](changelog/2025/2025-08.md) | LangGraph 节点、预处理、后处理、报告生成、fold | — |
| [2025-07](changelog/2025/2025-07.md) | LangChain、LangGraph、RAG、LangSmith、延迟会话 | — |
| [2025-06](changelog/2025/2025-06.md) | MCP、PC 因果发现、因果图、文件管理、会话交互 | — |
| [2025-05](changelog/2025/2025-05.md) | LLM chat、MySQL、CSV 上传、Gunicorn、多用户登录 | — |
