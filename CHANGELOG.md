## 更新日志

---
2025.5.9
- 【内容新增】：完成LLM chat框架的全构建
- 【内容新增】：统一数据库，增强安全性和规范性
- 【内容新增】：增加CSV文件上传功能
- 【内容新增】：增加文件上传的后端校验功能

---
2025.5.10
- 【内容新增】：完成MySQL数据库的构建
- 【内容新增】：将后端服务部署至服务器
- 【性能提升】：引入gunicorn，优化前后端交互模式

---
2025.5.11
- 【性能提升】：成功部署gunicorn，支持多用户并行登录
- 【内容新增】：实现Flask会话加密与多用户登录密钥检测
- 【bug修复】：修复了MySQL数据库的错误实现

---
2025.6.11
- 【内容新增】：实现MCP（Method Call Protocol）的初步演示
- 【性能提升】：构建异步任务逻辑，提升回答函数性能

---
2025.6.12
- 【内容新增】：实现基础的PC因果发现算法库
- 【内容新增】：连通MCP与因果库，允许LLM按需调用
- 【内容新增】：集成vis-network库，实现交互式因果图渲染
- 【内容新增】：增加LLM上下文理解功能（支持20轮对话）
- 【性能提升】：创建后台asyncio事件循环以支持异步任务
- 【内容新增】：实现历史会话中因果图的保存与加载
- 【性能提升】：优化多用户并行登录逻辑
- 【内容新增】：增加前端加载动画效果

---
2025.6.14
- 【性能提升】：分离数据库初始化脚本，提高系统健壮性

---
2025.6.15
- 【bug修复】：修复了AI回复时加载动画不消失的问题
- 【bug修复】：修复了特定场景下AI错误回复"上传成功"的问题
- 【bug修复】：修复了MCP在处理多文件上传时无响应的问题
- 【内容新增】：增加文件检查逻辑，支持同名文件更新
- 【内容新增】：优化了前端界面样式

---
2025.6.15
- 【内容新增】：重构数据库，增加归档和分区功能
- 【数据库更新内容】(Document/Database_NOTES.md)
- 【内容新增】：更新前端样式以适配新版数据库
- 【内容新增】：调整后端逻辑以适配新版数据库

---
2025.6.16
- 【内容新增】：增加会话标题可编辑功能
- 【内容新增】：增加会话标题实时预览功能
- 【内容新增】：在设置中新增操作手册与用户隐私协议

---
2025.6.17 晨
- 【内容新增】：完成后端对会话标题编辑功能的支持
- 【内容新增】：增加会话列表的删除功能，现在可以向左滑动删除会话啦
- 【内容新增】：增加模糊搜索，现在用户不需要指定文件名，也可以调用因果分析功能
- 【BUG修复】：修复了AI回复时加载动画不消失的问题
- 【BUG修复】：修复了创建新会话的时候显示错误的问题

---
2025.6.17 晚
- 【内容新增】：增加文件列表功能，现在可以查看上传的文件列表啦
- 【内容新增】：文件库设计，文件库对齐
- 【内容新增】：增加文件删除功能
- 【BUG修复】：增加文件哈希大小检测，不只是检测文件名
- 【内容新增】：css文件增加注释，方便后续维护
- 【内容新增】：增加文件引用功能，现在点击文件可以在聊天框引用啦
- 【BUG修复】：修复了文件列表滚动后内容显示不正确的问题
- 【BUG修复】：修复了按下文件名之后，清空输入框的问题

---
2025.6.21
- 【内容新增】：设置页面的md格式支持
- 【内容新增】：消息支持复制

---
2025.6.23
- 【BUG修复】：修改会话更新逻辑

---
2025.7.1
- 【BUG修复】：修复ai回复时禁用输入框逻辑

---
2025.7.6
- 【内容新增】：重置与ai交互逻辑，新增agent智能体和langchain架构，对mcp进行重新架构升级，对参数接口进行统一，现在回复是基于agent啦

---
2025.7.7
- 【内容新增】：系统完美集成了rag和mcp功能，生成报告的时候会查询知识库，生成一份更加详细的报告了
- 【内容新增】：集成langsmith，可以在后端查看具体的调用结果
- 【bug修复】：修复用户新建对话时，无论是否发送消息都创建新的会话的问题，增加延迟会话逻辑，会话等待逻辑

---
2025.7.14
- 【内容新增】：加载密匙逻辑全面更改
- 【内容新增】：全面重构agent逻辑，新增langgraph逻辑

---
2025.8.7
- 【内容新增】：全面重构agent，增加langgraph图，节点，边关系构建
- 【内容新增】：增加后处理逻辑，增加报告生成逻辑，增加预处理逻辑

---
2025.8.13
- 【内容新增】： 补充agent中文件加载节点，预处理节点部分功能实现

---
2025.8.16
- 【内容新增】： 拓展fold节点，增加数据分析内容，增加数据内容验证文件

---
2025.9.17
  - 【内容新增】： 补充fold节点，增加数据分析内容，增加数据内容验证文件。
  - 目前对于用户上传文件可以进行初步判断，对于不合理的数据进行人工干预，对于需要更改数据提出建议，后期再进行更改。用户需要补充目标变量和处理变量才可以进行因果分析。

---
2025.9.18
  - 【内容新增】： 补充预处理节点，增加数据分析内容，增加数据内容可视化，增加数据内容总结。
  - 【内容新增】： 补充rag节点，增加知识库查询功能，补充mcp调用causal-learn因果分析算法。

---
2025.9.20
  - 【内容新增】： 补充human节点，增加人机交互过程

---
2025.10.14
  - 【内容新增】： 补充后处理节点，增加后处理功能
  - 【内容新增】： 后处理节点：1. 查看是否有环路，如果存在环路，则使用LLM辅助决策进行修正。2. 查看是否有不合理边，如果存在不合理边，则使用LLM辅助决策进行修正。

---
2025.10.19
  - 【结构重置】：重构代码中人设部分
  - 【结构重置】：重构数据库连接

---
2025.10.22
  - 【bug修复】： 解决agent路由的bug问题，目前可以正常跑通

---
2025.10.23
  - 【bug修复】：修复agent中的用户暂停逻辑
  - 【bug修复】：修复agent当中的文件上传逻辑

---
2025.10.26
  - 【内容新增】：增加langgraph中的checkpoint支持,重构langgraph的节点逻辑
  - 【内容新增】：实现mysql数据库的langgraph checkpoint功能，实现同步/异步方法
  - 【内容新增】：增加inquiry_answer节点，实现对用户追问的回答
  - 【bug修复】： 修复目前节点的reducer机制，修复state中的reducer机制，实现消息记录的补充说明
  - 【bug修复】： 主程序对checkpoint的响应逻辑补充，补充config配置，修复对多次回答的逻辑缺失
  - 【内容新增】：补充Alembic数据库迁移功能

---
2025.10.28
  - 【内容重构】：重构工具执行节点，封装@task工具，支持数据库的task支持
  - 【bug修复】：重构human in loop节点，支持interrupt机制，支持用户输入的传递

---
2025.10.31
  - 【内容新增】：docker部署

---
2025.11.2
  - 【BUG修复】：修复用户注册密码加密问题，使用bcrypt进行加密

---
2025.11.6
  - 【内容新增】：增加思考过程气泡和详情面板，支持思考过程的展示和展开/收起
  - 【内容新增】：支持SSE流式传输节点，支持显示思考进度
  - 【内容新增】：支持ai的流式传输重构

---
2025.11.11
  - 【内容新增】：增加预处理图表支持，完善报告生成
  - 【内容新增】：数据库中支持可代替图表生成

---
2025.11.16
  - 【内容新增】：优化报告样式

---
2025.11.21
  - 【重构】：重构Agent目录关系，增强结构可读性，修改模块内部导入路径，修改目录层级关系,修改引用关系
  - 【重构】：重构flask框架，增加blueprint，增加app目录，修改CausalAgent主文件，适配目前APP文件目录，修改模块内部导入路径
  - 【bug修复】：修复docker由于目录重置导致的问题，修改目录关系

---
2025.11.26
  - 【内容新增】：完善MCP机制，支持动态选择不同算法
  - 【内容新增】：新增olc算法支持

---
2025.12.18
- 【重构】：更名为CausalAgent

---
2026.3.20
- 【内容重构和新增】：
  - 重构了查询主链路， dense 检索 -> MMR -> sparse 检索 -> 候选融合重排 -> 证据块构造 -> 结构化回答 -> 证据链输出。
  - 重构了知识库构建脚本，在 build_knowledge.py 中补齐了文档级和 chunk 级 metadata，包括 doc_id、chunk_id、doc_type、corpus、page 等，为后续检索过滤、证据链保存和评测提供基础。
  - 重构了问题生成模块，在 rag_questions.py 中把 RAG 问题从字符串列表升级为结构化对象，新增 intent、priority、why_needed，让知识库查询更贴近报告增强目标。
  - 重构了任务与状态传递，在 rag_query_task.py 和 state.py 中把 knowledge_base_result 从字符串改为结构化结果，保证 LangGraph 流程中可以传递完整证据链。
  - 适配了下游消费逻辑，在 nodes.py、fix_cycles.py、evaluate_edge_llm.py 中增加了摘要转换逻辑，使报告生成、环路修正和边评估都能消费结构化 RAG 结果，而不是依赖旧的字符串结果。
  - 增加了混合检索能力：dense 检索负责语义召回，sparse 检索负责关键词召回。
  - 增加了 MMR 去重能力，减少相似 chunk 重复进入最终证据集合。
  - 增加了轻量级融合重排逻辑，综合 dense 分数、sparse 分数、语料类型和双路命中情况得到最终 rerank_score。
  - 新增了评测脚本 rag_eval.py，当前支持检索层指标 Recall@k、Precision@k、MRR、Hit Rate，以及轻量级生成层关键点覆盖评测。
  - 新增了 metadata 导出脚本 export_metadata.py，

---
2026.5.17
**重要更新**
- 【内容新增与重构】：
  - 建立了更完整的数据库迁移链：新增核心业务表基线迁移，并补上 checkpoint 迁移依赖关系。
  - 将 `Database/database_init.py` 从“直接创建业务表”改为“数据库引导脚本”，业务表结构正式交给 Alembic 维护。
  - 在 `app/db.py` 中完成数据库访问分层：写连接、业务读连接、复制状态检查连接三条路径分离，并加入连接池、弱一致读回退和慢查询告警。
  - 新增主从开发拓扑 `docker-compose.replica.yml` 及 MySQL primary/replica 初始化脚本，支持 GTID、半同步复制、慢查询日志和应用侧读写分离验证。
  - 新增数据库审计脚本、轻量监控接口和一组覆盖配置解析、连接边界、迁移链、主从初始化、失效会话保护的测试。
  - 修复旧实现中多个容易在生产化阶段暴露的问题，包括：应用误用业务账号执行 `SHOW REPLICA STATUS`、数据库初始化职责和 Alembic 迁移职责重叠、旧 session 在用户数据失效后仍可能继续访问接口、上传文件缺少体积上限等。
- 【修复问题】
  - 修复数据库初始化与迁移职责重叠的问题
   旧版 `Database/database_init.py` 同时负责建库、建表、建索引和部分结构逻辑，容易与 Alembic 演进冲突。现在它只负责确保数据库存在和连接可用，结构统一由迁移脚本维护。
  - 修复主从读写边界不清的问题
   旧代码主要通过单一路径访问 MySQL，主从环境下很难明确“哪些查询必须强一致、哪些查询允许弱一致”。本轮将强一致读、弱一致读和写入路径拆开，并在弱一致读失败时自动回退主库。
  - 修复复制状态检查权限模型不干净的问题
   旧设计容易让应用继续用业务账号执行 `SHOW REPLICA STATUS`。现在新增专用状态账号；未配置该账号时，系统会安全回退主库，而不是继续误用高权限账号。
  - 修复旧 session 残留导致的伪登录状态问题
   当浏览器 session 还在、但数据库中的用户已失效时，原逻辑可能继续把请求当作已登录。现在引入 `app/auth/session_guard.py`，会在鉴权时校验真实用户，不存在则清空 session。
  - 修复上传文件缺少大小上限的问题
   文件上传现在新增 `MAX_UPLOAD_SIZE_MB` / `MAX_UPLOAD_SIZE_BYTES` 限制，避免过大文件直接写入 `uploaded_files.file_content`。
  - 修复迁移链起点不完整的问题
   新增 `1a2b3c4d5e6f_create_core_schema.py` 作为核心 schema 基线，并让 checkpoint 迁移依赖它，避免空库初始化只能依赖历史手工建表。

---
2026.5.18
**重要更新**
- 【内容新增与重构】：
  - 任务创建、任务领取、事件写入和 SSE 推送已经拆分到不同层，Web 不再直接承担长任务执行。
  - 新增 `analysis_jobs` 与 `analysis_job_events` 数据库作为任务队列和事件流的持久化数据库。
  - 后台 worker 以 slot 为单位持有独立 MCP session 和 Agent graph，避免 Web 进程阻塞。
  - 同一 `user_id + session_id` 的并发任务通过唯一约束兜底，防止重复执行。
  - 旧接口 `POST /api/send_stream` 已转为迁移提示，前端改走 `POST /api/agent/jobs` 与 SSE 订阅。

---
2026.5.26
- 【内容新增】
  本次改造是在不改变现有架构的前提下，引入 LangGraph 1.2 的节点级容错能力。已完成依赖升级门禁、LangChain v1 兼容迁移、节点 async 化、MCP/RAG task 异步化，以及基于 `retry_policy / timeout / error_handler` 的集中容错策略。

- 【bug修复】
  - 升级到 langgraph-checkpoint==4.1.1 后，JsonPlusSerializer 不再有 dumps/loads，新版接口是 dumps_typed/loads_typed。
  - 修复前端报告占位符问题

---
2026.5.28
- 【内容新增与重构】：
  - 重构 RAG 测评模块，将数据集处理、检索评测、Ragas 评测、Claim 评测、Trace 导出和报告生成拆分到独立目录。
  - 建立 `validate_datasets -> retrieval_eval -> ragas_eval -> trace_export -> summary` 评测流水线。
  - 移除不应长期保留在仓库中的运行产物，并补充 RAG 测评框架和数据集说明文档。

---
2026.5.29
- 【内容新增】
  - 将原先集中在 `execute_tools_node` 的 MCP 因果分析与 RAG 查询拆分为父图中的 `mcp`、`rag` 两个业务阶段节点，并将工具调用细节封装在各自的 compiled subgraph 内。
  - 支持原生 LangChain ToolNode 调用。

---
2026.5.29
- 【内容新增】
  - 将原先集中在 `execute_tools_node` 的 MCP 因果分析与 RAG 查询拆分为父图中的 `mcp`、`rag` 两个业务阶段节点，并将工具调用细节封装在各自的 compiled subgraph 内。
  - 支持原生 LangChain ToolNode 调用。

- 【BUG修复】：
  - 修复 `export_metadata.py` 重构后遗留的文件冲突和重复实现问题。

---
2026.5.30
- 【BUG修复】
  - 修复外键写入、LangGraph checkpointer 写 MySQL 与 RAG 格式化问题。

---
2026.5.31
- 【BUG修复】
  - 梳理 ToolNode 的标准消息协议。
  - MCP worker 主路径迁移到 `langchain-mcp-adapters`：每个 slot 通过 `MultiServerMCPClient.session("causal")` 维护持久 session，并用 `load_mcp_tools(session)` 加载 ToolNode 可执行工具。

---
2026.5.31
- 【内容新增与重构】：
  - 增加可配置的 RAG 检索参数和检索配置结构。
  - 增加外部医疗数据集处理、评测数据生成和 Ragas 数据集构建能力。
  - 统一医疗语料、测试集、评测输出和检索配置入口。

- 【BUG修复】
  - 梳理 ToolNode 的标准消息协议。
  - MCP worker 主路径迁移到 `langchain-mcp-adapters`：每个 slot 通过 `MultiServerMCPClient.session("causal")` 维护持久 session，并用 `load_mcp_tools(session)` 加载 ToolNode 可执行工具。

---
2026.6.1
- 【内容新增】：医疗知识库构建增加 OpenAI-compatible embedding 支持，并继续复用原持久化目录。
- 【内容新增】：支持通过环境变量临时覆盖向量库目录和 Chroma collection。
- 【内容重构】：RAG benchmark 与医疗数据集解耦，统一使用 `benchmark_v2` schema。
- 【内容重构】：使用 `gold_doc_ids` 记录文档级检索目标，并兼容旧 `gold_chunk_ids`。

---
2026.6.3
- 【内容重构】：RAG active benchmark 切换为 PubMedQA labeled。
- 【内容新增】：增加 PubMedQA 数据处理脚本，生成 1000 条语料和 1000 条评测数据。
- 【内容重构】：医疗知识库统一从 active corpus 构建，默认使用 `pubmedqa_clean` collection。
- 【内容新增】：增加向量库与 benchmark 一致性检查，防止使用错误知识库运行评测。
- 【当前状态】：旧 RAGCare 向量库保留在 `tmp/RAGCare`。

---
2026.6.5
- 【内容新增】：完善 PubMedQA 检索、回答生成和 Ragas 评测流程。
- 【内容新增】：增加 PubMedQA 专用回答提示词、检索配置快照和评测结果校验。
- 【BUG修复】：评测前自动核对样本数、问题顺序、检索配置和向量库摘要，避免不同评测结果混用。

---
2026.7.10
- 【内容修复】
  - 修复 PC、OLC 因果边在后处理中的格式和矩阵方向不一致问题。
  - 环路修订会生成实际的修订图，最终 SSE、前端展示和历史会话统一使用该图；结构异常时安全回退原图。
  - 统一结构化输出配置入口，Compose 曾支持可切换的结构化输出模式，并移除 `LANGSMITH_*` 配置。
- 【验证】
  - 新增后处理、最终图选择和结构化输出配置测试；完整测试集已通过。

---
2026.7.11
- 【内容新增与重构】：
  - 新增 RAG 测评工作台前后端，可通过 `/rag_eval` 或 `/rag-eval` 访问。
  - `pubmedqa_pipeline` 默认评测前 30 条，样本数可由前端动态调整。
  - Ragas generation 默认使用 6 个 context、单个 context 1600 字符、回答 1100 字符和 PubMedQA prompt v6。
  - `claim_eval` 从默认 pipeline 和前端调参入口移除，坏例链路只统计 retrieval 和 Ragas 相关问题。
  - 保留 `trace_export`，在前端和报告中统一显示为 Bad Case Traces / 坏例链路。
  - 移除 CLI 调参备份层；前端不再展示 CLI 等价字段，后端不再提供 `GET /api/rag_eval/cli-params`，也不再接受 `cli_overrides`。
  - 新增正式 RAG 检索配置查看与发布接口：`GET /api/rag_eval/production-config` 和 `POST /api/rag_eval/production-config/publish`。
  - 正式 RAG 查询读取 `Agent/knowledge_base/rag/runtime/production_rag_config.json`；文件不存在或无效时，回退到 `query_rag.py` 的默认 `RagRetrievalConfig()`。
  - 新增 `GET /api/rag_eval/run-state`，支持运行页刷新恢复；取消请求会在当前样本完成后停止，并通过 `step_progress` 展示进度。
  - 新增 `GET /api/rag_eval/analysis/latest` 和 `GET /api/rag_eval/runs/<run_id>/analysis`，只读提供报告、坏例 Trace 和证据链。
  - `RAG_EMBEDDING_PROVIDER` 支持 `auto`、`openai_compatible` 和 `local` 三种模式，并支持通过 `RAG_LOCAL_EMBEDDING_MODEL_PATH` 指定本地模型。
  - `build_knowledge.py` 默认拒绝向非空 collection 追加数据；只有显式传入 `--allow-append` 才允许追加，避免重复 chunk 污染知识库。
  - 删除已停用的旧数据集处理和评测辅助脚本。

---
2026.7.18
- 【技术验证】：
  - 完成多模态知识摄取规划 P01S：冻结七类合法小型 fixture、来源哈希、人工 gold、环境记录和可复现实测脚本。
  - Windows CPU 对比 PyMuPDF 1.24.7、Docling 2.113.0 与 PaddleOCR PP-StructureV3 3.7.0；默认内部解析/OCR 路径选择 PaddleOCR，PyMuPDF 仅作为数字 PDF/原生表格 fallback，并保留许可证发布门禁。
  - Docling 因首次模型获取不可复现而不入选；Docker CPU 和 GPU 安装未通过的结果已如实记录，未用推测吞吐补齐。
  - PaddleOCR 对数字、扫描、混合 PDF 及复杂表格 gold 通过，项目自有中文图 OCR `1-CER` 为 98.25%，项目自有流程图为 100%；候选均不提供结构化因果边字段，质量标为 unsupported，仍需 P08 视觉语义与方向复核。
- 【验证】：
  - P01S fixture manifest 与来源哈希测试在 Python 3.10.20、3.12.13 环境通过；完整 `tests/rag_ingestion` 回归结果见 P01S 报告。

---
2026.7.19
- 【内容新增】：
  - 完成多模态知识摄取规划 P05：数字 PDF 适配器按阅读顺序输出正文与页内表格片段，保留页码和归一化 bbox，并单独发现内嵌图片位置。
  - 完成 P06：图片 OCR descriptor 保留 OCR 文字、置信度、bbox、语言配置和方向校正提示；低置信度或无文字均以非阻断诊断输出。Paddle PP-StructureV3 生产 port 在依赖缺失时显式失败，不伪造 OCR 结果。
- 【验证】：
  - Python 3.10.20 与 3.12.13 环境各通过 117 项 `tests/rag_ingestion` 测试，并通过新增文件的 `py_compile` 与 `git diff --check`。

---
2026.7.20
- 【问题修复】：
  - P06.1 将 PP-StructureV3 改为进程级唯一解析 slot；多个 `PaddleOcrPort` 复用同一 pipeline，不兼容的 device/语言配置显式失败，避免重复常驻约 6.2 GiB 的模型资源。
- 【验证】：
  - Python 3.10.20 与 3.12.13 环境各通过 119 项 `tests/rag_ingestion` 测试；新增 fake-Paddle 回归覆盖多 port 复用与配置冲突，且未运行真实模型。

---
2026.7.20
- 【内容新增】：
  - 新增 `execute_ingest(IngestCommand)`，完成已验证来源到 P06 OCR 片段与结构化问题的最小正式摄取编排；结果保留 `KnowledgeFragment`，不写 Chroma、不调用旧 `build_knowledge.py`。
  - PDF 来源在 PyMuPDF 的 AGPL/商业许可证发布门禁通过前，明确返回阻断问题，不会静默进入 P05 adapter。
- 【验证】：
  - Python 3.10.20 与 3.12.13 环境各通过 122 项 `tests/rag_ingestion` 测试；覆盖图片 OCR 编排、PDF 门禁与空来源失败，未运行真实模型。
  - P01S 隔离 Paddle Docker target 补齐 `libgomp1` 后完成安装与容器导入断言：`paddleocr=3.7.0`、`paddle=3.2.0`、`cuda=False`；未运行 PP-StructureV3 或下载模型权重。
  - 新增 P06 provenance smoke 工具与两项单测，记录输入、adapter、raw/stdout/stderr 和模型缓存哈希；两个本地 Python 环境的 `tests/rag_ingestion` 均通过 124 项。经授权在 `CA-py310` 安装固定 Paddle 依赖后，真实 adapter smoke 使用既有模型缓存输出 4 个 OCR 片段、0 个问题，raw/stdout/stderr/input 哈希均通过复核；未下载模型权重。
  - 删除已停用的旧数据集处理和评测辅助脚本。

---
2026.7.22
- 【内容新增】
  - 完成数据库管理阶段一的最小管理员权限：`users.role` 仅支持 `user` / `admin`，历史用户默认保持普通用户。
  - 管理接口统一校验数据库中的实时角色和启用状态，未登录返回 `401`，普通用户返回 `403`。
  - 新增初始管理员提升命令 `python -m app.auth.admin_cli promote <username>`（本地运行）、`docker-compose -f docker-compose.replica.yml run --rm app python -m app.auth.admin_cli promote <username>`（docker运行），只允许幂等提升已注册且已启用的用户。
  - 登录与会话恢复固定使用主库强一致读，禁用用户无法登录，已有会话也会立即失效。

- 【BUG修复】
  - 修复删除已创建会话时遗留 MySQL checkpoint 的问题：会话、消息、附件和 checkpoint 现在在同一事务内删除，`checkpoint_writes` 由外键级联清理。
  - 删除流程会锁定目标会话并拒绝仍有 queued/running job 的会话，避免并发任务与删除操作交叉写入。

---
2026.7.22
- 【内容新增】
  - 完成数据库管理阶段一第二步管理员只读数据库看板：新增 revision、主从节点、连接、表容量、快速完整性、慢查询和 worker/job 只读检查。
  - 新增受统一管理员校验保护的 `/admin/database` 后台页面；管理员登录后只进入后台，普通用户继续进入聊天界面。
  - 后台使用 Flask 原生 HTML/CSS/JavaScript，建立白色简约左侧导航骨架，按钮仅使用蓝色和绿色，不提供 SQL、修复、迁移或数据库写入能力。
  - 旧管理接口保持 `data` 类型和旧字段兼容，新接口与新增元数据统一标记状态、逻辑来源、采集时间、估算属性和降级警告。
  - 升级前审计复用快速完整性检查，并扩展到 job、event、checkpoint 和 pending write 轻量孤立计数。

---
2026.7.22
- 【内容新增与重构】
  - 新增独立数据库 monitor 进程和 MySQL 共享监控快照，将实时状态、SQL 性能、表容量、完整性审计拆分为不同采集周期，管理 GET 接口只读取最近快照。
  - 新增聚合看板接口、共享手动刷新接口和独立完整性审计接口；自动刷新可以关闭，但仍保留首次读取与手动刷新。
  - 将 digest 区块更名为“SQL 性能摘要/高负载 SQL”，明确其按累计总耗时排序；慢查询告警改为优先使用采集窗口内 `Slow_queries` 增量。
  - 运行期完整性审计移除已有外键保证关系的全表孤立扫描，改为检查关键约束是否存在；升级前审计按 schema 和迁移需要决定是否执行，新库不再先跑 preflight。
  - 三套 Compose 增加 `monitor` 服务，并集中提供自动刷新、分层周期、慢查询告警和完整性审计配置。

---
2026.7.23
- 【BUG修复】
  - 完整恢复 `bbcdf46` 误删的前端初始化、聊天交互、任务事件订阅、会话历史与删除逻辑，修复未登录首页显示空白的问题。

---
2026.7.23
- 【工程规范】
  - 新增 GitHub Actions 轻量 CI，对 `main`、`develop` 执行 Python 语法编译、无外部服务依赖的轻量测试和 Pull Request 策略检查。
  - 统一 Pull Request 标题格式，并明确 `feature -> develop -> main` 的合并路径。

---
2026.7.24
- 【架构重构】：
  - 将生产 RAG 的 embedding、Chroma、BM25 corpus 和回答 LLM 收敛到 worker 进程级 `RagRuntime`，通过 `RagService -> Tool -> Graph` 显式注入；同一 worker 的所有 slot 共享 Runtime/Service。
  - worker 启动时严格验证已有且非空的 Chroma collection；初始化失败时绑定 `UnavailableRagService`，任务继续执行并返回稳定、脱敏的知识库不可用结果，修复后通过重启 worker 恢复。
  - `query_rag.py` 保留评测、CLI 和 Web 兼容入口，统一委托给独立的延迟 compatibility Service；生产检索配置继续逐问题动态读取。
  - 移除生产 RAG Tool 对 `langgraph.func.task` 的依赖，Service/Runtime 不进入 Tool schema 或 LangGraph state。
- 【验证】：
  - 新增 Runtime 生命周期、严格失败阶段、日志脱敏、BM25 parity、候选隔离、并发检索、Service 动态配置、Tool 降级和 worker 多 slot 共享测试。
  - 变更文件通过 `py_compile`；已执行完整测试发现和有限 worker 启动，但当前本机 Python 缺少项目 LangChain/pytest/Flask 依赖且 Docker daemon 未启动，两者均在导入阶段受阻，需在完整项目运行环境中复验。

---
2026.7.24
- 【依赖与检索重构】：
  - 引入固定版本 `bm25s==0.3.10`，以 BM25s NumPy/Lucene 稀疏索引替换生产路径中的手写 BM25 语料统计、逐文档打分和排序。
  - 保留现有中英文 tokenizer、512 token 截断、`SparseRetriever` 协议、候选结构和分数归一化；Chroma 继续作为唯一知识库来源，worker 启动时构建一次进程级只读内存索引，不增加磁盘索引。
  - 适配层恢复 BM25s Lucene 为排序省略的 `(k1 + 1)` 常数，保持旧 `sparse_score` 原始分数尺度；缺少 BM25s 或索引构建失败仍进入 `sparse_corpus` 降级阶段。
  - 移除只暴露旧词频结构且仓库内无调用的 `_get_sparse_corpus()`、`_bm25_score()` 私有兼容入口。
  - 多模态契约测试共 34 项，覆盖解析、审计、索引身份、发布门禁、远程策略、检索防护及 Agent 路由；Python 编译检查与差异格式检查通过。
  - 100 页 OmniDocBench 固定样本已完成官方 end-to-end 评测，文本 Edit Distance 为 0.193744，公式 CDM 为 0.0574299，共评估 26 个表格和 107 个公式。
- 【当前边界】：
  - OmniDocBench 仅作为解析能力 benchmark，不纳入知识库；布局 mAP 尚未完成。
  - 当前尚未按新质量门禁发布生产索引；需在正式知识源确定后重新摄取、评估并发布。
  - 持久化运行摘要、异常退出后的陈旧锁恢复、并发成功幂等实测以及完整父图因果链验收仍待完成。

---
2026.7.25
- 【内容新增与重构】
  - 管理员系统迁移到独立 `admin-frontend/`：使用 Vue 3、严格 TypeScript、Vue Router、Element Plus 和 Vite，保留数据库看板五卡、容量、完整性、SQL 性能、Worker/Job 及全部来源、过期、估算、排队、空态和失败态语义；普通用户聊天前端和 API/SSE 协议未修改。
  - 新增数据库监控在线配置与审计：七项覆盖按“数据库 > 环境变量 > 代码默认值”解析，进程缓存最多 5 秒，数据库读取失败时优先保留最后有效值；配置保存/重置使用乐观锁并记录 success、rejected、failed 审计事件。
  - 管理员写请求新增 Session 绑定 CSRF，所有响应新增 request ID；未登录管理员页面回到统一登录入口，API 继续保持 401/403 JSON 契约。
  - Dockerfile 改为 Node 24 构建阶段与 Python 运行阶段，Vue 产物固定复制到 `/opt/causalchat-admin`；三套 Compose 不增加 Node/Vite 服务或端口。
- 【BUG修复】
  - 修复看板“最后采集”把毫秒时间戳原样显示的问题，统一展示本地可读日期时间。
  - 普通刷新和完整性审计成功提示改为 5 秒后自动消失；警告、超时和错误提示继续保留。
  - 五张核心状态卡的截断来源与采集时间增加原生悬停提示，可查看完整内容。
  - 主库初始化为应用读账号补充 `events_statements_summary_by_digest` 的最小表级 `SELECT`，修复高负载 SQL 摘要因 1142 权限错误持续降级的问题，不授予全局 Performance Schema 读取。

---
2026.7.26
- 【BUG修复】
  - 修复 MCP 正常返回但载荷被包装为嵌套 `result`、二次 JSON 或缺少顶层 `success` 时，父图误判结果并重复执行 `agent -> fold -> preprocess -> mcp` 的问题。
  - MCP 服务端改为返回结构化字典；客户端兼容历史字符串载荷和 `structured_content` 包装，并将协议异常统一转换为 `success=False`。
  - 为 MCP planner、ToolNode 和结果 parser 补充失败出口；ToolNode 异常恢复后不再强行进入 parser，避免二次异常。
  - 修复mcp在返回tollcall时的文件内容错误
- 【json输出格式化】
  - 完成统一结构化调用器及业务调用点迁移，统一走langchain的toolcall方式，实现json格式调用
  - 完成 Agent、Fold 显式 State 路由和恢复路径调整。
  - 完成 RAG、MCP、环路修复和边评估失败降级。
  - 修复 MCP planner did not return tool_calls：补充 tool_choice="required"。


---
2026.7.26
- 【管理员看板体验优化】
  - SQL 性能摘要新增前端业务语义映射，按任务调度、聊天、文件、Agent 状态、数据库监控、事务和连接操作解释 Digest；未知表按 SQL 操作与表名弱推断并明确标注。
  - 继续按真实共享快照逐条反查仓库 SQL：SHOW 状态/变量、Performance Schema 摘要、monitor 命名锁、快照读写、配置读取、Worker/Job 汇总、主库版本、server UUID 和会话身份复核均改为“代码确认”，详情展示对应文件与函数依据。
  - 连接初始化类 SQL 明确区分仓库显式调用和 mysql-connector 自动行为；`SET NAMES`、autocommit、sql_mode 的依据来自连接池配置及运行镜像中的实际驱动实现。
  - 高负载 SQL 列表默认展示业务模块、业务动作和中文说明，保留后端原始排序且不隐藏事务、连接或监控语句。
  - 每条摘要新增右侧详情抽屉，完整展示 Digest、执行次数、累计/平均耗时、扫描行和返回行，并说明归一化模板无法恢复 `?` 对应的真实参数。
  - 详情抽屉桌面端最大约 720px，760px 以下铺满屏幕；关闭后清理当前详情状态。

---
2026.7.26
- 【3.1 只读业务后台】
  - 新增业务概览、用户与权限、会话与内容、分析任务、文件资产及 Schema/deep 审计六个 Vue 页面；保留数据库看板、采集配置和 `/admin/database` 登录落点，不修改普通用户 `chat.html`、`style.css`、`script.js`、聊天 API 或 Job/Event/SSE 契约。
  - 新增 `/api/admin/business/*` 统一只读接口，列表默认 20、最多 50 条并使用不透明游标；DTO 不返回密码哈希、完整正文、文件 BLOB/哈希或认证/数据库秘密。
  - 消息、附件与任务输入/结果/错误改为管理员明确点击后按最多 64 KiB 源字节分块读取；短消息列表只返回长度摘要，避免把短正文伪装成摘要提前泄露。
  - 新增敏感访问审计包装器：记录管理员、动作、目标、结果、错误码和 request ID，不保存正文；成功敏感访问在审计不可写时失败关闭，401/403、404 和服务失败记录拒绝或失败结果。
  - 新增 CSV 纯文本安全预览和附件式下载；预览限制为 256 KiB、100 行、50 列、单元格 1000 字符，成功预览/下载在同一事务中更新 `last_accessed_at`、增加 `access_count` 并写审计。
  - 新增只手动调度的 `deep_audit` 共享快照，覆盖 revision、关键字段/索引/外键、字符集/UTC/隔离级别、账号职责结论、Job/Event、checkpoint/pending writes、归档关系、`active_session_key` 和逐从库状态；不自动修复，不返回账号、host 或 grants。
  - 新增 `d3e4f5a6b7c8` migration，为用户角色/状态、会话活动时间、任务创建时间、文件上传时间和审计目标时间增加只读查询索引；未对当前开发库执行 migration。
  - 左侧导航桌面端支持 248px/约 76px 收缩并保存浏览器偏好，移动端使用可关闭抽屉；展开和折叠均通过受保护接口复用 `README/CausalAgent.png`，未复制或生成新 Logo。

---
2026.7.26
- 【多模态知识库流程】：
  - 新增独立的多模态公共知识库模块，提供资料检查、摄取、质量评估、发布、回滚和自动维护命令；其资产、索引与发布指针均与现有医疗知识库隔离。
  - 支持文本、Markdown、表格、图片及 PDF 的标准化处理。PDF 默认使用 Docling，原始资料、解析产物、标准化单元和资源均通过 URI 与 SHA-256 建立可回读的审计关系。
  - 索引版本绑定 embedding 与视觉配置；发布前执行完整性和质量门禁，查询时再次校验发布指针、manifest 与运行时 embedding 指纹。
  - 远程视觉增强受资料白名单、模型配置、调用预算和审计策略约束；失败状态会持久化，未增强的低价值图片不会进入有效检索结果。
  - 完整开发依赖统一由 `requirements.txt` 引入多模态依赖清单；基础生产依赖保持不变。
- 【Agent 与 worker 接入】：
  - 新增 `multimodal_rag_search` 工具，并通过问题的 `corpus` 字段在医疗知识库与多模态知识库之间显式路由；同一批问题禁止混用语料范围。
  - RAG 子图支持持久化检查点，worker 事件流可记录 RAG 规划、工具调用和解析结果；现有医疗 RAG 行为保持不变。
- 【维护与发布控制】：
  - 自动维护支持版本互斥、暂存版本复用、失败不发布以及阶段边界取消和超时。
  - 旧格式 manifest 缺少构建配置、质量策略或质量观测时不能通过新发布门禁。
- 【验证】：
  - Python 3.10.20 与 3.12.13 环境各通过 105 项 `tests/rag_ingestion` 测试。

---
2026.7.26
- 【内容新增】：新增多模态知识库现状分析与发展规划文档。
- 【内容新增】：建立独立的多模态知识库模块，支持资料检查、摄取、评估、发布、回滚和自动维护。
- 【内容新增】：支持文本、Markdown、表格、图片和 PDF，并保存可追溯的 URI 与 SHA-256。
- 【内容新增】：增加索引完整性、质量、manifest 和 embedding fingerprint 发布门禁。
- 【内容新增】：增加远程视觉资料白名单、模型限制、调用预算和审计策略。
- 【内容重构】：将多模态知识库设为默认且唯一的生产 RAG corpus，不再回退到 PubMedQA。
- 【内容重构】：Runtime 改为从多模态 active pointer 加载索引，并校验版本、manifest 和 embedding fingerprint。
- 【内容重构】：将 RAG ToolNode 子图收缩为普通 `rag` 节点，直接完成问题生成、RagService 调用和结果写回。
- 【验证】：多模态摄取测试在 Python 3.10 和 Python 3.12 环境通过。
- 【验证】：默认 corpus、Runtime、metadata、RAG 节点和 worker Service 共享测试通过。

---
2026.7.27
- 【BUG 修复】
  - 将根 `.gitignore` 中会匹配任意层级的 `dist/` 改为只匹配仓库根目录的 `/dist/`，避免 `admin-frontend/dist/` 再次被误忽略。
  - 管理员 Vue 生产构建产物改为随源码同步纳入 Git；Docker 镜像仍由 Node 24 构建阶段从当前源码重新生成产物，并通过 `ADMIN_FRONTEND_DIST_DIR=/opt/causalchat-admin` 交给 Flask 托管。
  - 按原始 Logo 画布中心重新计算折叠侧栏与移动端裁剪偏移，分别向左校正 5px，展开态品牌图保持不变。

---
2026.7.27
- 【3.2 受控业务写入与数据库治理收口】
  - 新增用户单个/批量启停、角色切换和同密码设置，以及用户/文件物理删除；执行接口统一要求 Session CSRF、当前管理员密码重新认证、幂等键、影响预览、明确确认和主库事务。
  - 新增 `users.auth_version` 会话版本、`password_changed_at`、`admin_operations` 与 `admin_operation_items`；角色、状态或密码实际变化会使目标用户旧 Session 失效，成功写入与逐目标去敏审计同事务提交。
  - 禁止操作者禁用、降级或删除自己，并通过稳定锁顺序保护最后一个启用管理员；用户删除显式清理 archived session 与 session checkpoint，文件删除同时删除行和 BLOB 且不提供回收站。
  - `checkpoint_writes` 由应用写入、migration 回填完整业务键摘要并建立唯一索引，特殊 writes 走 upsert、普通 writes 忽略重复；没有使用与级联外键冲突的 generated column，最新 checkpoint 增加 `checkpoint_id` 稳定次排序。
  - 新增 `Database/lifecycle_repair.py`：默认只输出有限孤立关系清单，只有显式 `--apply` 和精确数据库名确认才执行，migration 不静默删除历史数据。
  - 数据库连接池增加有界获取等待、建连超时和从库状态短缓存；副本状态不可用时安全回退主库，不实现自动故障切换。
  - 管理员 Vue 增加当前值/执行后值预览、重新认证、批量选择、删除影响和操作后强一致刷新；生产构建产物随源码更新。
  - 新增删除/保留矩阵、读写一致性矩阵、连接容量依据、发布恢复说明、隐私说明和阶段三 fencing/event/heartbeat/终态原子性设计交接，阶段三能力未提前启用。

---
2026.7.27
- 【3.2 管理员交互修复】
  - 修复受控写入重新认证失败被误判为 Session 失效的问题：只有 `auth_required` / `admin_required` 才离开当前管理页面，`reauth_failed` 保持原 URL 和原弹窗。
  - 用户操作、用户删除和文件删除失败信息改在当前弹窗内部展示；密码错误时清空密码框并允许原地重试，不再把通知藏到页面遮罩后。
  - 用户/文件物理删除弹窗增加明确的两步说明、可见字段名、动态确认值、占位提示和辅助说明，明确第一个输入框填写完整用户名/文件名，第二个填写当前管理员登录密码。

---
2026.7.27
- 【文档与测试结构】
  - 根 README 的管理员部分收敛为首次部署流程，新增 `Document/admin/` 集中维护 API、开发部署与测试说明。
  - 后端测试改为 `unit`、`integration`、`e2e` 三层，并在层内按 admin、agent、auth、chat、database 和 migrations 业务分类。

---
2026.7.27
- 【内容新增】：固定本地 `bge-small-zh-v1.5`、两本 Pearl PDF 及其 SHA-256，远程视觉默认关闭。
- 【内容新增】：新增 24 条页级检索题，覆盖 15 条文本、6 条图片和 3 条表格问题。
- 【内容新增】：增加 worker 离线构建前检查，验证 embedding、知识源、Docling、RapidOCR 和磁盘空间。
- 【内容重构】：PDF 改为逐物理页运行 Docling 和 RapidOCR，降低大文件解析的内存峰值。
- 【内容新增】：增加页级 checkpoint，任务中断后可跳过已完成页面继续执行。
- 【内容重构】：Chroma 分批写入独立 attempt 目录，成功后再提交为正式索引。
- 【内容重构】：评测命中必须同时匹配文档、页码和预期模态。
- 【内容重构】：`run` 默认只执行摄取和评测，必须显式授权才能发布。
- 【验证】：本地 embedding 连续两次初始化的 fingerprint 一致。
- 【验证】：两本 PDF 的格式和 SHA-256 校验通过，Docling 与 RapidOCR 模型资产完整，单页 PDF smoke 通过。
- 【当前状态】：两本 PDF 共 889 页；当日全量摄取、24 题正式评测和 active pointer 发布尚未完成。

---
2026.7.28
- 【BUG修复】：纠正 5 条评测题的错误页码和模态标注，未通过伪造图表单元或降低评测要求绕过门禁。
- 【验证】：24/24 gold 页覆盖通过，文本、图片和表格题数量保持为 15、6 和 3。
- 【内容重构】：暂存评测改为复用生产 dense、BM25s 和 rerank 检索链路。
- 【内容新增】：明确要求图片或表格的问题会补充对应模态候选，普通问题不启用模态偏置。
- 【内容重构】：正式返回数量与 Hit@5 评测口径统一为 5。
- 【验证】：完成 889 页摄取，生成 5364 个标准化单元和 5364 个 Chroma 向量，数量一致。
- 【验证】：Hit@5 为 `0.75`、MRR 为 `0.675`、首条引用定位准确率为 `0.625`、空结果率为 `0`，全部通过发布门禁。
- 【验证】：候选索引通过离线 Docker Runtime smoke，相关测试 `72/72` 通过。
- 【当前状态】：获得明确确认后，active pointer 已切换至 `mm_74b5aef2f5e7322b5a79`。
- 【验证】：发布后 Runtime 成功加载本地 embedding、manifest、collection 和 5364 个向量。
- 【验证】：Compose worker 的两个 slot 均成功加载新索引，未进入不可用降级，也未回退到 PubMedQA。
- 【BUG修复】：修复 MCP artifact 中字符串结果的解析，并区分成功、失败和需要用户补充信息的路由。
- 【BUG修复】：RAG 完成后直接进入后处理，避免再次进入 Agent 造成重复 MCP 循环。
- 【验证】：RAG 节点测试 `10/10` 通过，Python 编译和差异格式检查通过。
- 【验证】：真实父图成功执行 MCP、RAG、后处理和报告流程，并返回图片证据、页码、资产引用和最终报告。
- 【验证】：使用当前代码在隔离目录重新执行 OmniDocBench 固定子集审计、摄取、完整性评测和 OCR probe，生产 active pointer 保持不变。
- 【BUG修复】：独立图片复用 RapidOCR 生成 `raw_text`，OmniDocBench evaluator 支持识别本地 OCR 图片单元，不再依赖远程视觉模型。
- 【验证】：6 个固定样本全部通过审计；隔离索引包含 6 个单元和 6 个向量，完整性门禁和 OCR probe 均通过。
- 【验证】：多模态契约与生产默认测试共 52 项，其中 50 项通过；新增 OCR 回归、Python 编译和差异格式检查通过。
- 【当前状态】：两项真实 Docling PDF 测试在低内存环境触发 `std::bad_alloc`，单独重试后仍未通过。
- 【验证】：使用真实 active pointer 完成纯文本、表格、图片/OCR、跨页和无法回答五类检索 smoke。
- 【验证】：前四类问题返回 `answered`；无法回答问题返回 `insufficient_evidence`，且引用为空。
- 【验证】：检索结果均包含文档 ID、页码、`content_kind` 和 `modality`；图片证据包含可回读的 `asset_uri`。
- 【当前状态】：Compose worker 已加载新索引的 5364 个 chunks，BM25s 和 RagRuntime 初始化成功，未绑定 `UnavailableRagService`。

---
2026.7.29
- 【管理员普通用户能力修复】
  - 管理员继续默认进入 `/admin/database`，同时增加后台与聊天界面的双向入口；管理员主动进入聊天页后不再被前端强制送回后台。
  - 管理员在普通接口中只按自身 `user_id` 访问会话、文件和任务，管理 API 的实时角色校验、`401/403`、CSRF、审计和重新认证边界保持不变。
  - 未登录管理页面使用白名单路径安全回跳；普通用户直访管理页面先返回真实 `403` 拒绝页，再回普通首页明确提示“无管理员权限”。
  - 新增 `/admin` 与 `/admin/` 受保护入口、登录 `redirect_to` 契约及对应后端、Vue 和隔离 E2E 回归覆盖。

---
2026.7.29
- 【管理员前端显示与会话标题优化】
  - 会话标题保留单行省略显示，并增加悬停完整标题提示。
  - 高负载 SQL 的业务模块标签与表头统一居中；侧栏导航、管理员身份和操作按钮字号同步缩小。
  - “进入聊天”与“退出登录”按钮统一对齐，移除聊天入口链接下划线；配置变更记录为管理员和错误码列保留更宽的单行展示空间。
  - 修复会话创建时把首条消息硬截为 8 个字符并永久写入标题的问题；两个创建入口改为统一保存不超过数据库 500 字符上限的完整单行标题，列表省略仅保留为显示效果。
  - 其他文字表述合理性修复。

---
2026.7.29
- 【路线调整】：统一采用 WCode `qwen/qwen3-vl-flash`（7.30 切换为 `qwen/qwen3-vl-8b-instruct`）一次请求返回 OCR 与视觉语义；不再继续本地 OCR 与 VLM 双候选方案。
- 【外发契约】：只允许冻结且明确批准的来源，图片需解码、RGB 归一化并匹配 source/page/image/context/策略 hash；远程失败不回退 RapidOCR，active pointer 保持不变。
- 【本地运行边界】：Windows Docling `std::bad_alloc` 促使 R2/R3 解析入口转为 Docker worker；R2 只生成本地审阅清单，不联网、不建库、不发布。
- 【验证与暂停】：R1/R2 离线契约测试和 Docker preflight 通过；本地 OCR 候选在 729/889 页暂停，未进入评测或发布。

---
2026.7.30
- 【Docker 单元测试环境】
  - 新增独立 `docker-compose.test.yml` 和 Dockerfile `test` 目标，将项目 Python 依赖与 `pytest` 固化为可复用测试镜像，不再向运行中的应用容器临时安装测试工具。
  - `unit-test` 服务不依赖 MySQL、Web、worker 或 monitor，以非敏感空白文件屏蔽项目 `.env` 并关闭 LangSmith 追踪，禁用容器网络，并只读挂载当前源码。
  - 单元测试通过 `docker compose ... run --rm` 按需创建容器，测试结束或退出 Shell 后删除容器；镜像继续保留，只有依赖变化时才需重建。
  - 当前 MVP 只保证 `tests/unit`，集成测试和隔离主从 E2E 保持原有独立执行边界。

---
2026.7.30
- 【R3 门禁】：新增一次性 maintenance worker；R3a 只做全量本地发现和人工审阅清单，R3b 必须先批准清单并取得独立外发授权，maintenance worker 不替代常驻业务 worker。
- 【全量发现】：两份 Pearl 共完成 889/889 页解析，形成 275 条冻结记录；未批准前不调用 WCode。
- 【R3b 结果】：候选 `mm_587799887fc8efb68409` 生成 5460 units，270/275 图片远程成功；决定不做 generation3 重试，保留失败审计，后续门禁不再把远程失败数量机械地作为唯一阻断条件。
- 【存储调整】：页级 checkpoint 从大量小文件收敛到 `checkpoints.sqlite3`，保留按页恢复和完整 hash 身份。

---
2026.7.31
- 【SQL 性能摘要修复】
  - 高负载 SQL Digest 改为按单次平均耗时降序选取，平均耗时相同时按累计耗时降序次排序。
  - 管理员看板主表新增“平均耗时”列和秒单位，并在前端对旧共享快照执行同口径兼容排序，缺失或非法耗时统一置底。

---
2026.7.31
- 【架构决策】：R4 将“解析/索引可用性”与“固定 gold/评测题集”解耦；Pearl 24 条题只作为 Pearl 公共语料回归集，隔离评测使用知识源无关的 `rag_eval_v1`，题集必须显式传入，检索 gold 通过 `gold_evidence` locator 绑定；缺 gold 时显示未评分而不是 0。
- 【解析与索引】：`why-003` 空表通过 bbox `table_recovery` 和 provider-neutral `TableRecoveryProvider` 修复；隔离评测摄取使用低内存 Docling、页级 checkpoint，支持自定义页范围和容器 Docling 模型目录；隔离结果绑定 ingestion/index/manifest/策略 identity，不修改 active pointer。
- 【隔离评测运行台】：Vue 工作台、评测中心、报告编辑和对比分析完成；默认检索与完整 Ragas 两种执行边界明确；固定 4 页真实摄取、RAG 和 prepare-only 流程验证通过。
- 【可靠性】：Windows `WinError 5` 文件占用通过共享原子替换重试修复；确认 PowerShell 默认编码会破坏中文请求，后续调试不再用默认编码判断 RAG 质量。
- 【重要否决】：本地 OCR 候选在 729/889 页暂停，未评测或发布；从 source-specific 题集默认和远程/本地双候选假设切换为通用题集与统一远程授权边界。

---
2026.8.1
- 【取消延迟创建】
- /api/new_chat 创建 UUID 后立即写入 sessions。
- create_job 增加 session 存在性和归属校验。
- save_chat 拒绝未知 session。
- 文件上传拒绝未知 session。
- 加载、改标题、删除未知 session 统一返回 404。
- 更新普通聊天前端，使会话删除支持 202 响应。

- 【LangGraph PostgreSQL Checkpointer】
  - `/api/new_chat` 立即持久化会话；创建 job、保存聊天、改标题和上传文件均拒绝未知 session，取消延迟建行。
  - Agent worker 改用官方 `AsyncPostgresSaver`，按进程共享 PostgreSQL 连接池、按 slot 创建 Saver；新增一次性 setup 入口和独立 cleanup worker。
  - 新增合并两个 Alembic head 的迁移：创建 `checkpoint_cleanup_outbox` 后删除 MySQL checkpoint 表；downgrade 只恢复空表结构，不恢复已删除数据。
  - 会话/用户删除改为 MySQL 业务事务写 outbox，cleanup 使用 `FOR UPDATE SKIP LOCKED`、租约恢复、最多三次尝试和 10/30 秒退避；管理员操作状态支持 `running/succeeded/failed` 查询。
  - 隔离库验证 `upgrade head` 成功删除旧表并创建 outbox；明确回退到 `e4f5a6b7c8d9` 后恢复空 checkpoint 表。合并 head 场景下 `downgrade -1` 本身会因路径歧义失败。

---
2026.8.1
- 【来源与远程边界】：工作台支持多格式知识源登记和删除；上传只刷新 `tmp/rag_eval_sources/`，不自动摄取。内测默认允许本次运行通过精确 outbound manifest 使用远程 VLM，`VISION_ALLOW_REMOTE_DATA=false` 可关闭。
- 【评测配置】：统一 retrieval/Ragas profile 由 MySQL `rag_eval_profiles` 保存，内置 profile 只读，自定义 profile 支持保存、删除和发布；修复可编辑字段边界、profile 参数串改和题集 placeholder 校验。
- 【前端可用性】：补充大模型等待提示、Ragas 完成弹窗、阶段进度合并、sticky 导航和基线/候选完整指标对比，完成类型检查、构建和浏览器验证。

---
2026.8.2
- 【CausalAgent 命名统一】
  - 品牌显示名统一为 `CausalAgent`，Docker、Compose、checkpoint 默认值、镜像、网络、容器、连接池和监控锁统一使用 `causalagent` 前缀。
  - Flask 入口改为 `CausalAgent.py`，MCP 入口目录改为 `Agent/CausalAgentMCP`，Agent 状态类型改为 `CausalAgentState`。
  - 同步更新 CI、管理员构建目录、隔离 E2E 配置、README、AGENTS 和管理员部署测试；未修改现有 `.env`、数据库数据、知识库和历史日志记录。
  - 将主从开发 + PostgreSQL checkpoint 拓扑统一为 `docker-compose.yml` 默认入口；`docker-compose.replica.yml` 暂保留为旧路径兼容副本。

---
2026.8.2
- 【工程规范】
  - 新增 GitHub Issue Form 模板，统一收集背景、问题描述、预期结果、复现步骤、验收标准和环境信息。
  - 除附件外的 Issue 字段启用 GitHub 原生必填校验，不增加空格或 `...` 等内容限制；普通贡献者不能选择空白 Issue。

---
2026.8.2
- 【数据库初始化入口统一】
  - 新增 `python -m Database.bootstrap`，按 MySQL 建库、Alembic migration、LangGraph PostgreSQL checkpoint setup 的顺序执行一次性初始化。
  - 开发 Compose 将初始化入口收敛为一次性的 `db-bootstrap`，app、worker、monitor 和 cleanup worker 都等待统一 bootstrap 成功后启动。
  - 保留 `Database/checkpoint_setup.py` 作为底层独立命令，`checkpoint-cleanup` 继续承担运行期跨数据库清理职责。

---
2026.8.2
- 【DirectLiNGAM 适配 develop】
  - 将 DirectLiNGAM MCP 工具适配到 `Agent/CausalAgentMCP`、`CausalAgentState` 和 `tests/unit/agent` 新结构，保留 develop 的 PostgreSQL checkpointer、管理员后台、数据库初始化与默认 Compose 拓扑。
  - 显式点名 DirectLiNGAM 时确定性选择 `causal_direct_lingam`；结果统一使用 `B[target, source]` 表示 `source -> target`，并在报告中披露线性、非高斯、误差独立、DAG 和无潜在混杂假设。
  - 修复 DirectLiNGAM 边权在后处理修订图中丢失的问题；保留未反转边的数值 `weight`，反转边会清除不再适用的旧方向系数。

---
2026.8.2
- 【评测可靠性】：隔离评测 Evaluation Run 改为写入 MySQL `rag_eval_jobs` 持久队列，由独立 `rag-eval-worker` 执行；worker 以 heartbeat 保活，异常退出后将超时 `running` 任务收敛为 `failed`，不自动重跑可能产生外部调用的 Ragas。
- 【报告与对比】：报告入口统一为“报告编辑”；删除已结束或确认失活的 evaluation run 时同步清理对应 `tmp` 目录，history 和对比接口不再返回该运行；对比页同时展示指标和两次 run 的配置差异。
- 【执行边界】：关闭完整 Ragas 时只执行题集校验、retrieval 和 summary；开启时才进行回答生成、Ragas prepare/judge。自定义 profile 的保存、切换、刷新恢复问题已修复。
- 【验证】：隔离运行、评测与路由单测 32 项通过；Vue typecheck/build、Python 编译、Alembic head 和 Compose 校验通过。`rag_eval_jobs` 迁移尚未执行。

---
2026.8.2（Docker 启动修复补充）
- 【BUG修复】：修复 merge 后 MySQL Alembic 出现双 head 的问题，新增合并迁移，使评测任务、管理员和 PostgreSQL 检查点迁移可以由统一 bootstrap 顺序执行。
- 【BUG修复】：统一检索回答链的结构化输出调用接口，修复旧容器镜像入口和隔离评测 Compose 网络名不一致导致的启动失败。
- 【验证】：Compose 配置、数据库 bootstrap、Alembic 单 head、`/rag_eval` HTTP 200、业务 worker 和评测 worker 启动均通过；数据库 bootstrap 单测 3 项通过。
- 【当前状态】：当前正式索引目录缺少 `mm_74b5aef2f5e7322b5a79/manifest.json`，业务 worker 按安全策略暂绑定不可用 RAG Service；未自动伪造清单或切换 active pointer，待恢复完整索引产物后重启 worker。

---
2026.8.2
- 【评测依赖修复】：明确安装 PyTorch CPU 对应的 `torchvision==0.22.1+cpu`，避免 Docling 间接依赖从 PyPI 拉取 CUDA wheel，导致 RAG Runtime 导入 `sentence-transformers` 时加载 `libcudart.so.12` 失败。
- 【验证边界】：待重新构建 `rag-eval-worker` 后先用单题评测验证 embedding 初始化，再恢复 24 条正式题集评测。

---
2026.8.2
- 【隔离评测修复】：Ragas `answer_relevancy` 改为使用当前评测 staged Runtime 的 embedding，不再读取生产 active pointer；同一评测始终绑定自己的索引 manifest 与运行配置快照。
- 【兼容性】：旧版代码配置入口未传入隔离 embedding 时仍保留原有全局 Runtime 回退；仅在真正执行 Ragas judge 时要求 staged embedding，prepare-only 流程不额外加载它。
- 【验证】：Ragas 与隔离评测相关测试 13 项通过；在 active manifest 缺失、staged index 完整的场景下，单题 Ragas judge `eval_20260802_135826_6a0bccbfe8` 通过。

---
2026.8.3
- 【管理员 checkpoint 读取迁移至 PostgreSQL】
  - Worker 将 `job_id` 写入 LangGraph checkpoint metadata，管理员按会话与任务精确读取 PostgreSQL 安全摘要；历史记录缺少 `job_id` 时不做时间归属猜测。
  - 任务详情分栏展示 MySQL 节点/任务事件和 PostgreSQL checkpoint 状态，PostgreSQL 故障不影响任务元数据与事件读取。
  - quick/deep 审计新增 PostgreSQL 连通性、官方 schema/setup 版本、估算统计和有界跨库关系样本，同时保留 MySQL cleanup outbox 检查。
  - 默认、生产与管理员隔离 Compose 补齐 PostgreSQL、bootstrap、worker、monitor 和 checkpoint cleanup 配置；MySQL SQL Digest 不再把已迁移表标为现行 Agent 状态表。

---
2026.8.3
- 【管理员任务详情视图切换】
  - 任务详情不再同时排列节点事件与 checkpoint 状态，在敏感内容审计提示下新增单视图选择器，默认展示节点与任务事件。
  - PostgreSQL checkpoint 摘要改为选择后按需加载，两套视图分别保留分页、加载态、空态和失败态。

---
2026.8.3
- 【管理员侧边栏图标统一】
  - 使用按需打包的 Lucide Vue 线性 SVG 图标替换侧边栏、折叠控制、移动端导航以及底部操作区中的 Emoji 和字符图标。
  - 导航图标统一为 18px、1.8px 描边并继承当前文字颜色，保持默认灰色与选中蓝色的一致状态。
  - 增加单元测试与管理员 mock E2E 断言，覆盖导航图标数量、SVG 渲染和统一描边。

---
2026.8.4
- 【管理员数据库清理视图】
  - cleanup worker 约每 10 秒写入 `checkpoint_cleanup_runtime` 共享心跳；monitor 采集最多 100 条脱敏 `checkpoint_cleanup_outbox` 待处理/异常记录，不返回 `last_error` 原文。
  - 数据库看板新增按 URL 保持状态的“数据库运行状态 / Cleanup Worker / Outbox 队列”三段视图，并将 Agent Worker/Job 汇总迁移到分析任务管理页。
  - 用户物理删除后持续显示 MySQL 删除结果、PostgreSQL checkpoint 清理聚合和 Operation ID，可直接跳转到带 Operation ID 高亮的 Outbox 视图。
- 【Quick 完整性检查说明】
  - Quick 审计结果新增逐项检查目的说明，覆盖 MySQL 外键、ENUM、cleanup outbox 索引与失败清理任务，以及 PostgreSQL checkpoint 连接、官方表集合和 setup 版本。
  - 异常或不可用时在检查目的后追加具体原因；旧共享快照继续兼容无说明字段的返回结果。
- 【Quick 完整性状态标签】
  - Quick 完整性状态列改用 Element Plus 标签组件，healthy 显示绿色。
  - warning、error、danger 和 unknown 统一显示红色，保留后端返回的英文状态文字。
- 【用户最后登录时间修复】
  - `POST /api/login` 在密码验证成功后通过主库更新 `users.last_login_at`，管理员用户列表继续按上海时间展示；Session 恢复和管理员进入聊天模式不会重复计为登录。
  - 最后登录时间写入失败时仍允许完成登录，响应增加稳定警告码，登录页显示一次约 3 秒后自动消失的非阻塞提示；管理员在提示结束后继续跳转后台。
- 【Job 终态与 worker 租约 BUG 修复】
  - 成功终态将聊天保存、幂等标记、终态事件和 job=succeeded 收敛到同一个 MySQL 事务，提交前崩溃会整体回滚，重复终态调用不会重复保存聊天。
  - worker 的事件、心跳、成功和失败更新统一校验 `job_id + worker_id + attempt_count`，旧 worker 失去租约后不能写事件、聊天或覆盖新 worker 结果。
  - 领取任务时原子收敛心跳过期且已达到最大尝试次数的 running job，写入 error 事件、释放 `active_session_key` 并标记 failed。

---
2026.8.5
- 【LangGraph 执行时间线与普通回答文字流】
  - worker 改用 LangGraph v2 `updates/messages/custom/tasks` 多流，父图真实 task 生命周期生成可重复阶段实例，MCP/RAG 子图工具调用以脱敏摘要嵌套展示。
  - 普通问答与报告追问使用流式 LLM 副本，文字经单一顺序写协程按 150ms 或 384 字符批量写入 `text_delta`；阶段、重试和终态前强制刷新。
  - SSE 重连继续通过数据库事件 ID 与 `Last-Event-ID` 补发，浏览器按 `step_id + stream_id + sequence` 去重，`final_result` 校正草稿且不重复创建回答。
  - 时间线按阶段实例折叠展示，处理中显示转圈，完成后不显示成功图标；真实重试、脱敏失败与 EventSource 重连状态保留为用户可见详情。
- 【聊天时间线无卡片视觉调整】
  - 普通 AI 回答和思考入口去除背景框，顶部显示客户端总耗时，展开后以“思考过程”文本流展示阶段与工具详情。
  - 父级时间线和子级阶段统一使用右三角/倒三角，阶段详情改用缩进和细线表达层级，保留处理中转圈、失败展开和重试状态。
  - 移除展开详情中的“思考过程”标题，保持展开后直接显示阶段文本。
- 【聊天时间线默认展开与间距调整】
  - 时间线创建后默认展开详情，处理完成后继续保留展开状态。
  - 抵消聊天区域通用间距在顶部入口与详情之间造成的额外空白。
  - 移除点击已处理行展开详情时的自动滚动，展开和收起不再改变用户当前阅读位置。
  - 决策事件仅由所属的 `agent`/`fold` 节点生成，避免后续因果工具阶段重复展示继承的旧路由决策。
- 【聊天页面滚动与工具阶段决策修正】
  - 聊天内容改为页面级自然滚动，取消聊天区域和思考详情的嵌套滚动条，时间线内容直接平铺在页面中。
  - 明确禁止 `mcp`、`rag` 阶段复用父图继承的 `route_decision`/`fold_decision`，避免知识库阶段错误显示“已识别为因果分析请求”。

---
2026.8.5
- 【修复：Agent 创建请求幂等】
  - `/api/agent/jobs` 要求客户端提供 `Idempotency-Key`，服务端将请求指纹和幂等键与 job 创建放在同一个 MySQL 事务中。
  - 网络重试使用同一幂等键时返回原 job；同一幂等键对应不同会话或消息时返回冲突，避免终态 job 释放 active 锁后重复保存聊天记录。

---
2026.8.5
- 【功能】：新增 staged index 候选题集自动扩充入口，从 `units.jsonl` 按模态轮询生成 `generated_candidate`，保存 manifest hash、证据 locator 和版本快照；重复题、短答案和无法解析 evidence 的样本只进入拒绝摘要，不自动升级为 gold。
- 【功能】：retrieval sweep 改为最多 8 个 worker 的有界并行执行，保留每组配置、失败/取消状态、空结果数和样本级退化数，最多推荐 2 组候选供人工确认；不自动执行 Ragas、修改 profile 或发布 active pointer。
- 【验证】：候选题集与并行 sweep 新增测试 3 项，连同题集契约和隔离评测测试共 11 项通过；多模态 RAG 评测测试 10 项通过。

---
2026.8.5（候选题集与 sweep 修复补充）
- 【完整性】：候选生成入口增加 staged complete、manifest/unit/vector/Chroma 计数和 embedding 指纹门禁，并把 units/build-state hash 写入来源快照；全部候选失败或被筛除时拒绝写出无效空题集。
- 【并发可靠性】：串行和并行 sweep 现在都会为全部未开始配置补齐 cancelled 记录，不再因已取消 future 抛出 `CancelledError`；默认候选题集 revision 使用微秒时间和随机后缀，临时文件名也彼此隔离。
- 【交付】：新增候选生成与 sweep 测试已从 `.gitignore` 精确反忽略，避免正常提交和 CI 漏掉核心回归测试。
- 【验证】：候选生成、题集契约、隔离评测和多模态 RAG 评测共 26 项 `unittest` 通过；候选 CLI `--help`、8 个相关 Python 文件编译和 `git diff --check` 通过。

---
2026.8.6
- 【Agent Worker包结构重构】
  - 将单文件 `app/agent/worker.py` 拆分为可通过 `python -m app.agent.worker` 启动的 package，按启动编排、运行时、单任务执行、事件写入、图执行、事件适配和结果展示划分职责。
  - 新增显式 `ProcessRuntime` 与 `SlotRuntime`：LLM 在进程级创建，MCP session、tools 与 graph 在 slot 级创建；任务执行不再读取 `app.agent.core.llm` 等模块全局变量。
  - `job_service.py` 和 `routes.py` 保持在 worker package 外，继续供 Web、monitor、管理员看板与 worker 共用；管理员任务、checkpoint 和 SSE 数据契约不变。
  - 测试改为直接导入新职责模块，并将管理员 checkpoint 约束检查指向实际写入 `job_id` metadata 的 `graph_runner.py`。

---
2026.8.7（候选审核、Gold v2 与并行调优闭环）
- 【正确性】：候选输出拒绝覆盖已有 revision，dataset_id 做安全校验；retrieval sweep 的首个配置失败时标记 `baseline_failed`，不再把候选错误提升为基线；自定义 steps 缺少 `retrieval_eval` 时不再抛索引异常。
- 【生成与审核】：候选生成默认接入 Ragas 0.4.3 `generate_with_chunks()`，记录生成错误和拒绝摘要；新增隔离候选 run、SSE 进度/取消、逐题编辑、三态审核和 reviewed revision。
- 【版本门禁】：新增 Pearl 24 + 审核候选 48 的 Gold v2 冻结契约，以及只读绑定 active pointer、index manifest 和 `active_current` 的 Baseline v2 契约；不自动切换 active pointer 或发布 profile。
- 【前端】：候选题审核页面与 retrieval sweep 配置已接入 `/rag_eval`；前端可提交 sweep 配置矩阵和 `retrieval_sweep` 步骤。
- 【验证】：候选/Gold/Baseline/隔离任务及既有评测回归共 26 项 `unittest` 通过，Vue typecheck/build、Python 编译和 `git diff --check` 通过；真实 Ragas/Chroma 端到端仍待 Docker 可用且修复当前主机 Ragas 导入依赖后复验。

---
2026.8.7（候选接口与 Ragas SDK 受控复验）
- 【接口验证】：新增 Flask `test_client` 覆盖候选生成、状态、结果、产物下载、审核、Gold v2 冻结和 Baseline v2 失败关闭；非法布尔整数字段返回 400。
- 【Ragas 验证】：在关闭 LangSmith 追踪且不联网的受控模型下，实际调用已安装 Ragas 0.4.3 的 `TestsetGenerator.generate_with_chunks()` 并得到候选行；该结果只证明 SDK/适配链路，不代表真实模型生成质量。
- 【环境边界】：Docker 当前不可用，未执行 app/worker HTTP 端到端，也未调用外部 DeepSeek 或修改 active pointer。

---
2026.8.7（Docker 真实隔离评测复验）
- 【运行面】：Docker Desktop 恢复后，MySQL 主从、PostgreSQL、app、worker、monitor、checkpoint-cleanup 和 `rag-eval-worker` 均成功启动；`/rag_eval` 返回 200，页面显示后端已连接。
- 【真实链路】：通过 HTTP 创建 `eval_20260807_031531_00972a87bd`，经 SQL 队列和持久 worker 完成 `validate_datasets`、`retrieval_eval`、`retrieval_sweep`，2 组检索配置均为 `pass`，结果产物可由接口读取。
- 【结果边界】：该一题 smoke 的 retrieval 指标为 0，不能作为质量提升证据；候选题真实外部模型生成仍未执行，当前 active index 仍缺少 manifest，Baseline v2 保持 409 失败关闭。

---
2026.8.7（候选生成中断恢复）
- 【可靠性】：真实候选 smoke 在 Ragas `SummaryExtractor` 长时间无进展后通过取消接口收敛；补充 app 进程重启恢复逻辑，将不可续跑的 `created/running/cancelling` 候选 run 标记为 `failed`，避免永久悬挂。
- 【验证】：重启 app 后原 run `candidate_20260807_031820_095cbf2b56` 已收敛为 `failed/process_restart`；候选生成未产生有效题集，也未升级 Gold 或修改 active pointer。

---
2026.8.7（Ragas 真实候选 smoke）
- 【可靠性】：候选生成调用期间关闭 LangSmith tracing，并为 Ragas 0.4.3 设置有界超时、重试和等待参数；避免外部 telemetry 403 干扰生成，也避免默认重试窗口无限拉长取消收敛时间。
- 【真实链路】：HTTP 创建 `candidate_20260807_034548_b382b6a00e`，1 个 staged unit 经 `generate_with_chunks()` 完成 Summary、Embedding、Themes、NER、personas、scenario 和 sample 阶段，最终 `succeeded`，生成 1 条、通过筛选 1 条，生成错误/拒绝均为 0；随后真实 API 审核写入 reviewed revision。
- 【边界】：该 smoke 只有 1 条候选，Gold v2 冻结因未满足 Pearl 24 + 候选 48 返回 409；Baseline v2 因正式 Gold v2 不存在保持 409 失败关闭，未修改 active pointer 或正式 profile。

---
2026.8.7（Candidate audit 伴随产物）
- Candidate revision 现在始终生成同 revision 的 `candidate.json.audit.json`，保存生成错误、筛选拒绝和计数摘要；API 对成功和失败状态均保留固定 artifact 名称，便于追溯。
- 增加 audit 产物存在性和版本覆盖回归测试。

---
2026.8.7（前端真实候选 smoke）
- 通过 `/rag_eval` 页面真实启动 1 单元候选生成，收到 SSE `run_created`、`candidate_progress`、`screening` 和 `run_done`，随后在页面完成逐题审核并写入 reviewed revision；Gold v2 因只有 1 题按门禁返回 409。
- 修复前端把 HTTP 业务错误误显示为“后端接口不可用”的状态误报；服务器已响应但业务拒绝时保留“后端接口已连接”。

---
2026.8.7（候选 coverage 报告）
- Candidate audit 和题集 screening 增加 `rag_candidate_coverage_v1`，记录选中/覆盖 staged unit、evidence locator、多 evidence 样本及模态/内容类型分布；前端在逐题审核区展示该摘要，并明确它不等同于审核通过或 Gold 质量结论。
- 【验证】：候选生成专项 13 项、相关完整回归 30 项 unittest，Vue typecheck/build 均通过。

---
2026.8.8
- 【文件库与可恢复 Job】
  - 将文件存储拆为 `file_objects` 不可变 BLOB 和 `user_files` 逻辑文件记录，上传、选择草稿和创建 Job 的职责边界分离；Job 创建时冻结文件对象、hash 和文件名。
  - 新增 `analysis_job_inputs` 输入账本，支持同一 Job 的 interrupt、`waiting_input`、resume、取消和稳定生命周期事件；stale worker 通过 lease epoch fencing，旧 worker 不能覆盖新 worker 的结果。
  - Agent、MCP 和管理员预览/下载按主库事务读取冻结文件并累计访问次数；完整 CSV 不进入 State、ToolMessage、checkpoint、事件、日志或 SSE。
  - 新增 `a1b2c3d4e5f6_add_file_library_and_job_recovery`。该 migration 只面向开发/测试库，直接删除旧 `uploaded_files` 表，不回填旧数据、不提供 fallback、不拒绝旧数据迁移；downgrade 只恢复空旧表结构。
  - 补充迁移静态测试、Job 恢复定向测试和管理员文件访问事务测试；完整 Docker 后端、管理员前端和隔离数据库往返验证在本次执行中单独记录。
- 【聊天文件附件预览与等待任务提示】
  - 修复 `hidden` 属性被 CSS `display` 覆盖导致的空白文件栏和空白任务栏；无文件、无等待输入时不再占据输入区空间。
  - 文件上传或从文件列表选择后，在输入区内部显示 CSV 文件卡片，包含文件名、类型、大小和清除按钮；输入区随附件卡片自然增高，并覆盖桌面端与 390px 移动端布局。
  - 将按钮文案明确为“取消等待任务”；当前接口只取消 `waiting_input` Job，不中断 `queued`/`running` Job 或正在执行的 LLM/MCP 调用。
  - 完成真实 Flask 页面 DOM 检查、桌面/移动端视觉检查、JavaScript 语法检查和 `git diff --check`。
- 【修复：Job checkpoint identity 与跨 Job 聊天历史】
  - LangGraph 根图统一使用 `analysis_jobs.job_id` 作为 `thread_id`，根 `checkpoint_ns` 保持为空；业务 `session_id` 继续作为会话外键和 State 上下文，不再使用 `job:<job_id>` namespace 或 session-thread identity。
  - 新 Job 按创建事务记录的 `chat_message_id` 从 MySQL 读取同一业务会话的最近消息窗口；同一 Job 的 interrupt/resume 和 stale recovery 继续使用原 checkpoint，当前用户消息不会重复注入。
  - 输入账本按 `initial`/`resume` 区分运行时解析，结构化 resume 值仅作为 `Command(resume=...)` 的原值传入，并在图消息中统一转换为 JSON 文本；PostgreSQL checkpoint 不可用时阻止 stale Job 盲目重放。
  - 会话和管理员用户删除按全部 `analysis_jobs.job_id` 写入 cleanup outbox；管理员 checkpoint/deep audit 按 Job ID 归属，并识别 cleanup outbox 过渡状态。
- 【聊天页面滚动与文件日期显示修复】
  - 将页面和主聊天容器固定在视口内，长报告、时间线和历史消息只在 `chat-area` 内滚动，输入区保持固定可见。
  - 自动滚动目标改为聊天区域，避免新增报告内容继续推动整个页面滚动。
  - 文件列表时间仅显示 `YYYY-MM-DD`，并补充报告图片、表格和代码块的宽度约束，避免内容超出聊天区域。

---
2026.8.9（知识源显示名与候选审核布局）
- 新增来源显示名元数据，用户可在来源目录为知识源命名；名称保存到 `tmp/rag_eval_sources/source_metadata.json`，摄取和候选 run 会快照该名称，source_id、内容 hash 和历史产物不变。
- 候选审核页统一显示知识源名称和短索引别名，并将审核/冻结门禁移动到生成区下方；审核员仍只保存到本地隔离候选 revision 与 review manifest，不写入 MySQL。
- 【验证】：新增来源显示名 API/本地元数据回归覆盖；相关后端回归 32 项通过，前端 typecheck/build、静态页 HTTP 200 和 `git diff --check` 通过。

---
2026.8.10
- 【管理员会话正文审计提示前置】
  - 将“读取正文将会记录管理员、目标、结果和 request ID。”从会话正文弹窗移至会话详情的消息摘要标题下方，使管理员在读取正文前即可看到提示。
  - 保留分析任务等其他敏感正文弹窗的默认审计提示，并补充组件与 Mock E2E 覆盖。

---
2026.8.11
- 【会话历史恢复 Agent 执行阶段】
  - `/api/load_session` 在单个主库只读事务中批量读取消息、附件、Job、输入账本和事件，通过既有外键与事件 ID 为用户输入组装 `thinking_after`，不新增数据库 schema。
  - 历史时间线只返回脱敏节点事件，排除 `text_delta` 与边界事件；关联缺失或多义时跳过对应区间并记录服务端告警，不使用时间猜测。
  - 前端复用实时节点处理器回放多次 interrupt/resume 阶段，interrupt 后保留已完成时间线；活动 Job 从实际回放游标继续 SSE，避免刷新期间重复或跳过事件。
  - 普通用户历史与 SSE 统一使用按事件类型的字段白名单，移除 `attempt` 和未知内部字段。

---
2026.8.13
- 【任务执行边界】将隔离摄取和候选题生成接入既有 `rag_eval_jobs` 持久队列，与 evaluation 共享 `rag-eval-worker`、SQL heartbeat、SSE run.json 事件和 fail-closed 超时收敛；Web 进程不再为这两类长任务创建后台线程。`rag_query` 暂保留兼容线程链路，未纳入本轮迁移。
- 【迁移门禁】新增 `job_kind` 队列字段，支持 `ingestion`、`candidate_generation`、`evaluation` 三类任务；不新建第二套队列，不改变 active pointer、staged index、Candidate/Gold/Baseline 门禁。

---
2026.8.13（三类任务持久队列收口，修正 8.13 早先日志的提前结论）
- 【问题修正】：此前 8.13 日志中「三类任务已统一接入持久队列」的结论早于实际完成状态，本轮补齐并修复阻断验收的问题。
- 【worker 终态】：修复摄取成功终态 `staged` 被 worker 误判为失败的问题——worker 现将 `ingestion + staged` 视为成功并同步 SQL 为 `succeeded`，其它三类任务的 `succeeded/cancelled/failed` 收敛保持不变。
- 【数据库迁移】：执行 Alembic 迁移，`rag_eval_jobs` 新增 `job_kind` 列与 `idx_rag_eval_jobs_kind_queue` 索引，数据库升级到 `g7c8d9e0f1a2`。
- 【启动门禁】：`check_database_readiness()` 新增对 `rag_eval_jobs.job_kind` 列与 `idx_rag_eval_jobs_kind_queue` 索引的检查，避免缺列时 worker 误启动、直到提交任务才报错。
- 【租约 fencing】：worker 心跳失联后触发 fencing，阻止原 worker 继续把成功结果写回 run.json/SQL；`complete_job` 返回是否真正发生 running→succeeded 迁移，失败时把 run.json 收敛为 failed，消除 SQL=failed、run.json=succeeded 的不一致。
- 【SSE 游标】：事件改用单调递增 `event_id` 作为游标，事件数超过 500 上限后不再永久漏读后续事件（含最终完成事件）。
- 【生产 Compose】：`docker-compose.prod.yml` 补齐 `rag-eval-worker` 服务，并为 app 与 worker 共享 `rag_eval_isolated_runs` / `rag_eval_sources` 运行目录。
- 【摄取页范围】：修复无 `page_ranges` 时 worker 二次归一化把空列表误判为非法的问题（`_normalize_page_ranges` 对空列表与 None 一视同仁），否则「HTTP 创建摄取 → 领取」会在 worker 侧直接失败。
- 【测试对齐】：更新因持久队列重构而过时的测试（evaluation 入队断言、候选生成同步执行、候选 run 进程重启恢复），新增「摄取 staged 视为成功」worker 测试。
- 【验证】：迁移后 readiness 通过；相关回归 50 项 unittest 通过；两份 Compose 配置解析通过；真实链路「创建摄取 → SQL 领取 → staged → SQL succeeded」实测通过（run.json=staged，SQL=succeeded，job_kind=ingestion）。

---
2026.8.13（第二轮：跨进程一致性、取消终态收敛与生产部署门禁）
- 【跨进程锁】：app 与 rag-eval-worker 分属不同进程却共用卷内 run.json，`threading.RLock` 只在单进程内有效。新增 `_run_file_lock`（Linux `fcntl.flock` / Windows `msvcrt.locking`），`_set_status` 与 `_emit` 的「读-改-写」现在串行化，避免并发丢失 `cancel_requested`、`event_seq` 与终态。
- 【终态收敛】：`complete_job` 返回 false（SQL 已终态）时不再一律 `mark_worker_fenced`，改为 `_reconcile_terminal_state` 按 SQL 实际终态收敛——`cancelled`→run.json cancelled、`failed`→run.json failed、`succeeded`→幂等保持，消除「SQL=cancelled、run.json=failed」分裂。
- 【生产模型与来源】：生产 Compose 修正 legacy 的 `./knowledge_base/db` 错误路径，并为 app 与 rag-eval-worker 挂载 `Agent/knowledge_base/models`（只读）、`Agent/knowledge_base/source`（只读）与 `Agent/knowledge_base/db`，补充 `MULTIMODAL_DOCLING_ARTIFACTS_DIR`，使生产摄取具备本地 embedding 模型、Docling 资源与冻结来源。
- 【生产 bootstrap 门禁】：生产 Compose 新增一次性 `db-bootstrap` 服务（`python -m Database.bootstrap`），app/worker/monitor 依赖其 `service_completed_successfully`；`Database.bootstrap` 在未配置 `CHECKPOINT_POSTGRES_PASSWORD` 时优雅跳过 PostgreSQL schema setup，避免隔离评测专用拓扑因缺 PostgreSQL 而整体失败。
- 【页范围校验】：`max_pages + page_ranges=[]` 不再被误判为「不能组合」，空列表与 None 一视同仁。
- 【回归测试】：新增跨进程文件锁（多进程并发读改写 run.json 不丢失更新）、`complete_job(false)` 时 SQL 分别为 cancelled/failed 的终态收敛、`max_pages + page_ranges=[]` 三项回归。
- 【验证】：相关回归 54 项 unittest 通过（含 fork 多进程锁测试）；两份 Compose 配置解析通过；bootstrap 无 PostgreSQL 时优雅跳过；真实链路「创建摄取 → staged → SQL succeeded → 终态一致」实测通过。

---
2026.8.13（候选题生成说明与布局收紧）
- 【前端】：候选题生成卡片在参数前增加紧凑说明框，明确“保存逐题审核”“冻结 Gold v2”“绑定 Baseline v2”的产物与边界；其中 Baseline v2 仅绑定已冻结 Gold v2 与只读 `active_current` retrieval，不修改 active pointer 或正式配置。
- 【布局】：候选题生成卡片不再继承工作台的大面积最小高度，说明框后依次展示目标单元数、每单元题数和并行 worker，减少空白区域。
- 【验证】：Vue `typecheck` 与生产 `build` 通过。

---
2026.8.13（候选题生成参数实时提示）
- 【前端】：候选题生成页直接展示当前 staged index 单元总数、本次实际最多选中单元数与请求生成题数；当 staged 单元不足 48 时提示无法按 Gold v2 推荐配置覆盖 48 个单元。
- 【参数边界】：目标单元数、每单元题数和并行 worker 分别补充就地说明，明确 worker 只影响速度；同时说明请求量不保证等于最终候选题数，筛选仍会拒绝重复题、短答案和无法回溯 evidence 的题。
- 【验证】：Vue `typecheck` 与生产 `build` 通过。

---
2026.8.13（RAG 评测运行台视觉系统升级）
- 【前端】：基于 UI/UX Pro Max 的研究运维型 SaaS 设计系统，统一 RAG 评测运行台的青蓝主色、绿色状态语义、表单焦点、流程状态、表格、报告和移动端样式；保留既有工作台、候选题审核、评测、对比和报告接口与业务流程。
- 【可用性】：补充稳定悬停、键盘焦点与 `prefers-reduced-motion` 降低动画支持，避免纯色传达状态。
- 【验证】：Vue `typecheck`、Vite 生产 `build` 与变更范围 `git diff --check` 通过。

---
2026.8.14（候选审核恢复与本地 Gold 闭环）
- 【前端】恢复源码中的“候选题审核”导航和逐题翻页审核界面；通过、待修改、拒绝会立即反映在实时摘要中，页面展示通过 48 题的冻结门槛、生成覆盖摘要和固定操作栏。
- 【Gold】已有 Gold 时改为就地确认：保留当前基准，或确认后先归档旧 Gold 再替换冻结；主界面统一使用“冻结 Gold 基准集”和“绑定评测基准”名称，内部版本号不再作为主操作文案。
- 【评测】评测配置改为读取服务端本地已冻结 Gold 的安全摘要，不再要求浏览器粘贴 JSON；未冻结时禁止启动评测，运行请求只提交 `dataset_source=gold_v2`。
- 【验证】Vue `typecheck`、Vite 生产 `build` 以及候选/Gold/隔离评测相关 Python 单元测试均已通过；未执行 Docker 重建或部署。

---
2026.8.14（候选生成取消可抢占）
- 【修复】Ragas `generate_with_chunks()` 本身不支持进程内抢占；候选生成改由 `rag-eval-worker` 的独立 Linux 子进程执行。取消请求到达后，父 worker 会终止该子进程、收敛 run 状态为 cancelled 并立即释放唯一 slot，避免后续评测长期排队。
- 【前端】SSE 收到候选/摄取/阶段进度后会同步刷新 run 状态，避免真实已运行仍持续显示“排队中”。
- 【验证】新增 worker 取消回归测试；候选、Gold 和隔离评测相关单元测试共 15 项通过，Vue typecheck/build 与变更检查通过。

---
2026.8.15
- 【即时取消与 Worker补强】
  - 增 execution_state、释放时间/原因字段、索引及数据库约束。
  - 实现 queued/running/waiting_input 取消、幂等重放和 fencing。
  - running 取消后进入 canceled/draining，支持 worker cleanup 或 420 秒失联回收。
  - 修复 EventWriter abort、迟到结果、终态 fencing 和 worker cleanup。
  - 更新 Session、用户、文件删除阻断规则。
  - 更新管理员 DTO、worker 看板、普通用户取消按钮、文档和测试。
  - `fail_job()` 在 Job 行锁内区分 `APPLIED`、`CANCELED_FENCED` 和 `OTHER_FENCED`；取消获胜时不再补写普通失败事件或失败聊天投影。
  - `OrderedEventWriter.abort()` 改为幂等且可观察：丢弃文字 buffer、结束排队 Future，并向 worker 传递未预期 consumer/数据库异常；只有被接受的终态写入才设置 `terminal_seen`。
  - `JobExecutionGuard` 覆盖节点、ToolNode、parser、router、retry backoff、error handler、终态生成和事件持久化边界；cleanup 未完成时不确认 `worker_confirmed` 释放。
  - 普通用户取消改为按 Job 隔离幂等键和 SSE subscription generation，支持 `409 canceled` 对账、真实终态展示，并丢弃取消后的迟到事件。
- 【聊天输入区与取消时间线修复】
  - 移除任务排队状态框；Job 进入 queued/running 后，发送按钮显示旋转外环与中心停止方块，输入框保持可编辑。
  - 创建或恢复 Job 后立即释放输入区，不再等待整个 SSE 订阅生命周期；waiting_input 继续保留恢复发送和紧凑取消入口。
  - 取消 Job 时统一收尾所有仍处于进行中的时间线阶段、文字草稿和动画，避免子阶段继续显示“进行中”。

---
2026.8.15（Gold locator 门禁与候选题重绑复审）
- 【冻结门禁】Gold v2 冻结前强制校验候选题 `bound_index_version` 与当前 staged index 一致，并逐条确认 `unit_id/document_id/page_number/modality/content_kind` 在该索引的 `units.jsonl` 中真实存在；绑定审计字段不再混入运行时 locator 匹配。
- 【历史候选】将 48 道候选题重绑到 `mm_f956e532ed6d49ae1f0e`，生成独立待复审候选产物；48/48 题均重置为 `needs_revision`，86 条 locator 校验通过。另生成待复审 manifest，不覆盖原 Gold 或原候选文件。
- 【质量提示】自动标记拼写/非正式措辞、歧义引用、过短、过度复合和跨文档题目；当前 48 题中 20 题触发至少一项提示，全部仍需人工重新审核。
- 【运行门禁】评测与 Baseline 绑定遇到未绑定当前索引的旧 Gold 时直接拒绝，避免继续产生伪低分结果。
- 【验证】相关 Python 回归 17 项、Vue typecheck/build 均通过。

---
2026.8.16
- 【RAG 子图 State 隔离与父图统一输出】
  - 新增 `RagSubgraphState`，将 RAG route、问题列表、ToolMessage、Parser 中间结果和最终输出限制在子图内部。
  - 增加父子 State 适配节点，显式透传 LangGraph `config` 与执行上下文，父图只接收 `knowledge_base_result`，并保持 `mcp -> rag -> agent` 路径。
  - 将 RAG 子图统一为 Planner、ToolNode、Parser、Finalize 条件路由；保留 Planner、RAG task 和 ToolNode 的既有重试次数及普通异常降级边界。
  - 统一 RAG 降级结构，保留 `success`、单问题 `insufficient_evidence` 语义，并继续向 worker 传播 `JobExecutionRevoked` 与 `CancelledError`。
- 【RAG 不可用预检与协议错误分流】
  - 将已有 worker 进程级 `rag_available` 和空工具列表传入 RAG Planner；知识库未初始化或工具未注册时在 Planner 预检阶段跳过 ToolNode，经 Finalize 继续回到 Agent。
  - 为 RAG 查询失败和非法 ToolMessage JSON 增加明确错误标记，分别保持 `unavailable` 与 `protocol_error` 语义，不改变取消/撤销异常传播和既有重试边界。
  - 补充 RAG 预检、查询错误标记、协议错误分流和权威验证边界文档。

---
2026.8.16（隔离评测显式 staged 索引重绑）
- 【重绑目标】：候选题重绑接口改为必须接收页面明确选择的 `ingestion_run_id + index_version`，并在服务端确认该目标为已就绪的 staged 摄取运行；重绑成功后同步更新候选题运行的索引归属，冻结不会再回落到候选题生成时的旧索引。
- 【前端边界】：审核区展示实际重绑目标的日期、知识源和状态；“绑定正式生产基准”只在冻结 Gold 与已发布 active 索引一致时可用，避免把 staged 评测索引误当作生产基准绑定目标。
- 【验证】：候选题路由回归 3 项、Vue `typecheck` 和 Vite 生产 `build` 通过。

---
2026.8.16（隔离评测工作台全局索引上下文）
- 【交互边界】：已摄取索引只允许在隔离知识源工作台切换；候选审核、Gold 冻结与评测中心只展示并使用当前工作索引，不能在子页面另行切换。
- 【恢复规则】：刷新后优先恢复浏览器上次明确选择的摄取运行，仅在该运行不存在时才回退到运行中或最新 staged 索引。
- 【验证】：Vue `typecheck` 与 Vite 生产 `build` 通过。

---
2026.8.16（隔离评测无法重绑时重新生成候选集）
- 【审核出口】：当历史候选题 locator 无法迁移到当前工作索引时，候选审核页提供“重新生成当前索引候选集”；新任务使用当前全局 staged 索引，旧审核记录只保留为历史运行，不会删除。
- 【验证】：Vue `typecheck` 与 Vite 生产 `build` 通过。

---
2026.8.16（隔离评测候选题生成确认配置）
- 【确认步骤】：生成与重新生成候选题均先进入配置页，不再点击即入队；页面展示当前全局索引、预计请求题数和冻结目标，并允许在服务端边界内调整选中单元数、每单元题数和并行 worker。
- 【验证】：Vue `typecheck` 与 Vite 生产 `build` 通过。

---
2026.8.16（隔离评测候选任务恢复与事件隔离）
- 【恢复】：页面刷新不再自动导入历史重绑候选包覆盖当前候选任务；与当前工作索引不一致的已完成候选题会被忽略并提示用户生成当前索引的新候选集。
- 【事件】：候选摄取/生成事件不再写入评测流程事件面板；候选任务失败时在候选页展示服务端错误摘要。
- 【验证】：Vue `typecheck` 与 Vite 生产 `build` 通过。

---
2026.8.16（隔离评测候选题 locator 索引绑定修复）
- 【修复】：候选题生成现在会把 staged `source_snapshot` 同步写入样本级 `source.index_binding`，并为每个 `gold_evidence` locator 写入 `bound_index_version`；避免审核完成后在 Gold 冻结阶段才因 locator 未绑定当前索引而失败。
- 【一致性】：候选题重绑产物同样保留样本级索引绑定，且仍强制生成待复审清单，不自动迁移历史审核通过结论。
- 【验证】：候选/Gold/路由回归 20 项在宿主和 `rag-eval-worker` 运行镜像均通过；真实后台重绑 63 条候选后，样本与 locator 均绑定到 `mm_285e8738562b11e8adb1`。未复审候选冻结仍按预期拒绝。

---
2026.8.17（隔离评测运行对比题集版本提示）
- 【提示】：运行 A/B 下拉框新增 Gold dataset revision；当后端因两次运行的完整题集 identity 不一致而拒绝严格比较时，前端改为说明基线与候选所用 revision、同名题集不等于同内容，以及应选择同一 revision 或仅作趋势参考。
- 【可访问性】：比较错误提示新增 `role="alert"`，使辅助技术可及时获知不可比较原因。
- 【验证】：Vue `typecheck` 与 Vite 生产构建通过，生产静态产物已同步。

---
2026.8.17（隔离评测完整策略并行实验）
- 【后端】：新增 `POST /api/rag_eval/isolated/evaluation-batches`，一次校验并创建 2–4 个不同策略 profile 的独立 evaluation run；run 状态保存 `batch_id/batch_position/batch_size`，仍复用现有持久队列、租约、取消和报告链路。
- 【执行】：`RAG_EVAL_EVALUATION_WORKERS` 默认改为 2，并限制在 1–4；开发、兼容副本和生产 Compose 同步该默认值。每个 slot 仍独立领取一个 SQL 任务，不在 Web 进程执行长任务。
- 【前端】：评测中心支持多选 2–4 个 profile 并行启动，统一轮询批次状态、切换查看逐 run 事件、批量取消，并在全部结束后进入严格 A/B 对比。
- 【并发修复】：真实双 slot 冒烟首次暴露本地 HuggingFace embedding 同时构造的 PyTorch `meta tensor` 竞态；Runtime 现在只串行化进程内本地模型初始化，初始化完成后的两个评测仍并行执行。
- 【实验方案】：新增 `Document/rag_experiment_2_plan.md`，依据实验一的阶段召回损失和固定题/候选题分组指标，提出“混合召回增强”主实验及三个并行对照组；相同 Gold revision 下只改变检索参数。
- 【验证】：并行批次 HTTP 契约、批次快照、worker 多 slot 启动、本地模型并发初始化回归及既有队列回归通过；Vue typecheck/build 和三份 Compose 解析通过。Docker 真实冒烟中两个 retrieval-only run 于同一秒分别由 slot 1/2 启动，并在同一秒成功结束。

---
2026.8.17（Ragas 外部模型失败关闭）
- 【根因】：最新完整评测确实提交了 `run=true` 并进入 `ragas_eval`，但回答生成 72/72 收到模型服务 `HTTP 402 Insufficient Balance`；旧实现仍继续调用/聚合 Ragas，并把全 NaN 结果标记为 `needs_review/ragas_no_valid_scores`，事件因此误导为“Ragas 已完成”。
- 【修复】：隔离 Ragas 准备阶段在首个明确的回答生成失败时立即停止，不调用 judge；step 改发 `step_error`，run 与 SQL job 收敛为 `failed/answer_generation_failed`。judge 无有效数值分数时同样失败关闭为 `ragas_judge_no_valid_scores`。
- 【可诊断性】：失败 evaluation 仍暴露 `result.json` 和报告；Ragas Markdown 新增失败原因、失败回答数和错误摘要，前端可加载失败运行结果。
- 【验证】：新增回答生成失败与 run 终态传播回归；真实单题 Ragas 冒烟 `eval_20260817_162511_a65c4e1dfd` 在首题收到 402 后快速失败，事件包含 `step_error/run_error`、无 judge 事件，结果与报告均可读取并展示 402 原因。

---
2026.8.18
- 【日志管理第一阶段 1.1：日志契约与 Job 请求关联】
  - 新增可观测性日志 v1 契约、现状盘点、敏感信息边界和第二阶段布点清单，并接入 `Document/` 导航。
  - 新增 `c3d4e5f6a7b8_add_analysis_job_request_id` migration，保存创建 Job 的原始 `X-Request-ID`；历史行保持 `NULL`，不新增索引。
  - 将请求 ID 从创建路由传入 Job service，服务层执行格式校验；幂等重放保留首次请求 ID，不被后续请求覆盖。
  - 同步 readiness、deep audit、迁移链测试、Job 创建测试、迁移 head 文档和 E2E head 预期。
- 【日志管理第一阶段 1.2：共享 JSON 运行时与进程接入】
  - 新增共享 `observability` 日志运行时，统一 UTC、单行 JSON stderr、标准 `logging.extra` 事件字段、contextvars 上下文、递归脱敏、体积限制、异常栈清理和序列化失败降级。
  - 接入 Web、worker、monitor、db-bootstrap、checkpoint-cleanup 及维护脚本的启动日志，新增稳定启动事件；旧业务日志保持合法 JSON，无法可靠分类时不伪造 `event_code/category`。
  - 移除 MCP server 本地 `FileHandler` 和数据库初始化文件日志；MCP stdout 保留协议，应用日志经 worker stderr 进入容器日志。
  - 补充日志运行时、并发上下文和 MCP stdio 边界单元测试；未执行真实 Docker、数据库、PostgreSQL checkpoint 和模型/MCP 端到端验收。
- 【日志管理第一阶段 1.3：默认开发可观测拓扑】
  - 在默认开发 Compose 中锁定 Grafana 13.1.1、Loki 3.7.4 和 Alloy v1.18.0，新增独立 observability network、Grafana/Loki/Alloy 持久卷和资源上限；Grafana 仅绑定 127.0.0.1:3000，并要求非空 GRAFANA_ADMIN_PASSWORD。
  - 通过 Compose 静态标签限定 Alloy 只采集 Web、worker、monitor、MCP 转发和维护容器，排除数据库与可观测组件自身；MCP 继续复用 worker stderr。
  - 新增 Loki 单节点 TSDB/filesystem、72 小时保留、compactor、写入/查询限制、Logs Drilldown 能力配置，以及 Alloy Docker 解包、应用 JSON 提取、低基数标签和持久 positions 配置。
  - 新增 Grafana Loki datasource、最小错误仪表盘和可观测拓扑静态契约测试；本次未执行真实 Docker 镜像拉取、全栈启动、positions 重启、Loki 暂停恢复和 30 分钟负载验收。

---
2026.8.18（正式 RAG P0–P2 上下文链路）
- 【P0】：新增并冻结 `Agent/knowledge_base/rag/runtime/production_rag_config.json`，正式 retrieval 基线采用 sparse16、final_top_k6、MMR 0.6，并记录来源 run 与 Gold revision。
- 【P1】：`RagRetrievalConfig` 新增 `answer_max_contexts`；正式 `RagService` 在回答前应用该预算，不再把 Ragas judge 的 `max_contexts` 误认为正式回答上下文限制。
- 【P2】：新增 `answer_context_compression=page_dedupe`，按文档/物理页/内容类型去重；隔离评测的回答模型与 Ragas judge 复用同一 evidence，并记录压缩审计字段。
- 【前端】：评测配置增加正式回答 evidence 压缩选择，检索 profile 快照会包含回答上下文配置。
- 【验证】：新增正式服务上下文预算、page dedupe、隔离回答/judge evidence 一致性回归；`query_rag` 读取 P0 production config 为 `published_config`。
- 【前端修正】：将 `answer_max_contexts` 从通用 retrieval 参数网格中独立成“正式回答上下文”区域，和 evidence 压缩并列展示；旧 profile 缺少该字段时默认显示 P0 的 6 条上下文。Vue typecheck/build 通过，静态页面已同步。
- 【前端调整】：按使用反馈取消独立上下文卡片，将 `answer_max_contexts` 与 `answer_context_compression` 并回原 retrieval 参数列表；前者为普通数值字段，后者为普通下拉字段，不再制造特殊配置区。
- 【修复】：E4 两个并行 run 因 `official_only_when_available` 被误判为不支持的 retrieval override 而在启动阶段失败。原因是新增 answer context 字段时白名单错误收窄到 `RagRetrievalConfig`，已改为允许 profile 字段与 dataclass 字段的并集；adapter-only 字段仍由 `build_retrieval_config` 安全忽略。
- 【验证】：新增隔离评测 override 回归，相关容器测试 27 项通过；失败 run `eval_20260818_074121_84964d4825`、`eval_20260818_074121_56e064a519` 是旧代码生成的历史失败记录，不会被修改，修复后需重新创建 E4 批次。

---
2026.8.18（Gold v2 无人值守题目健康治理）
- 【治理规则】：新增 `dataset_governance` 持久队列任务。只处理带 `source.generator` 的生成题；`human_reviewed`、无 generator 和其他非生成来源永久保护。单次或单指标低 Ragas 分只作为诊断，不能独立退休题目。
- 【审核门禁】：生成题需存在 intrinsic 风险且独立结构化 reviewer 返回 `replace`/confidence >= 0.8；替换候选复用现有 hard screen，并需 `accept`/confidence >= 0.8。审核异常、超时、解析失败统一保留原题。
- 【版本与恢复】：题数、Gold schema、index identity、locator 或候选数量不满足时治理失败，旧 Gold 不变；有替换时在跨进程锁内复核源 evaluation 的数据集 SHA，拒绝 stale run 覆盖新 revision，再归档旧 Gold 并原子写入；零替换只返回 `no_change`，Gold 文件与 revision 不变。不切换 production active index/profile。`/api/rag_eval/gold-v2/governance` 与治理 run API/SSE 提供一次确认、阶段、计数、逐题原因和失败恢复信息。
- 【验证】：治理规则、reviewer fail-closed、stale publish、no-change、index binding 与治理路由回归共 8 项 Docker pytest 通过；5 个相关 Python 文件完成不写 `.pyc` 的内存语法编译，Vue `typecheck` 和 Vite `build` 通过。未调用真实外部 AI。

---
2026.8.19（Gold v2 治理候选分批恢复）
- 【修复】：治理专用候选生成按 8 个 staged 单元分批，每批最多 3 次尝试并按 3/6 秒退避；连接中断只重试当前批次，已通过 hard screen 的批次产物及审计会保留。
- 【失败边界】：累计候选不足时写入批次汇总审计并 fail closed，旧 Gold 不变；达到所需候选数后提前停止，避免无效额外模型调用。
- 【验证】：治理分批重试、候选生成和路由相关 Docker pytest 共 24 项通过；未调用真实外部 AI。

---
2026.8.20（索引绑定调参集自动治理闭环）
- 【边界】：新增独立 `tuning_dataset_governance` 任务，仅消费当前 staged index 绑定的 Gold 自动题；人工题永久保留，不进入正式评测 history、报告或对比分析。
- 【循环】：每轮只评测当前自动题，按四项 Ragas 单题门槛和 retrieval recall/MRR 门槛淘汰低分题，再生成候选、执行证据审核并补齐题数；审核或分数缺失均 fail-closed。
- 【登记】：通过后将题集写入本次隔离运行的 `registered_dataset.json`，携带 index version 与 tuning run 身份；不修改 Gold 文件、不发布 active pointer。
- 【前端】：工作台新增索引绑定调参集入口，说明目标题数、门槛和“不会进入正式报告”的边界，运行状态通过隔离 SSE 展示。
- 【验证】：新增闭环核心单测；Docker 相关契约与治理测试 31 项通过；RAG 评测前端 typecheck/build 通过。

---
2026.8.20（隔离任务状态刷新兜底）
- 【修复】：治理进度事件现在会立即触发状态拉取；运行期间增加 5 秒轮询兜底，SSE 丢事件或断线时仍能收敛到后端终态。
- 【恢复】：浏览器标签页重新激活时主动刷新当前运行；调参集状态明确区分“本轮评测题数”和“待补题数”，避免把当前批次数误认为剩余题数。
- 【验证】：RAG 评测前端 typecheck/build 通过。

---
2026.8.20（RAG 测评后端队列与题集绑定审计）
- 【统一执行】：staged index RAG 试跑从 Web daemon thread 迁入 `rag_eval_jobs`，与摄取、候选生成、完整评测和题集治理共享持久 worker；开发、兼容副本和生产 Compose 的 `RAG_EVAL_EVALUATION_WORKERS` 默认统一为 5。
- 【题集门禁】：所有内联 `rag_eval_v1` 题集在入队前执行 `dataset_kind` 语义校验；`generated_candidate` 还必须通过当前 staged index 的 source snapshot、unit locator 和 index version 校验，跨索引题集直接拒绝。
- 【接口一致性】：Gold 状态接口缺少成对的 `ingestion_run_id + index_version` 时返回 JSON 400，不再抛出 HTML 500；未删除任何接口，仓库无前端调用的接口单独列入人工审核清单。
- 【验证】：宿主与现有 `rag-eval-worker` 容器内相关回归均为 83 项通过；三份 Compose 配置展开、10 个相关 Python 文件不落盘语法编译和本次范围 `git diff --check` 通过。未重启正在运行的 worker，5 slot 默认在下次重建或重建容器后生效。

---
2026.8.20（候选题审核启动/收尾页与 Ragas 进度展示）
- 【审核流程】：候选题审核新增启动、逐题审核和完成三态；最后一题做出结论后进入完成页，不再跳回第一题。
- 【审核统计】：前端独立记录已操作题目，使“待修改”作为有效审核结论统计；完成页提供题号直达、保存审核、冻结 Gold 和返回评测工作台。
- 【进度展示】：复用现有 `step_progress` 事件展示五个评测阶段；Ragas 准备题集与 Judge 轮次分开显示，`1/1` 明确标注为 Judge 轮次。
- 【验证】：新增纯函数状态/进度回归测试 3 项；RAG 评测前端 `npm run typecheck`、`npm run build` 和 Node 测试通过。

---
2026.8.21（RAG 测评生产工程化 Tasks 1–8：接口审计与工程事实同步）
- 【Tasks 1–8 收口】：补齐通用 canonical run lifecycle、不可变 `rag_eval_datasets`/`dataset_ref`、staged index 完整身份门禁、持久队列优先级与分类型并发、容量只读快照、验收矩阵及安全 runner 的工程事实；接口审计清单写入 `Document/rag_eval_api_inventory.md`。具体 run 生命周期兼容接口继续返回弃用 header，本轮未删除任何接口；待移除项仅供用户后续审核。
- 【验证记录】：前序 Tasks 1–7 的交付已记录 focused tests、Python 编译、Alembic head、Compose config 和 contract list 验证；本 Task 8 重新执行路由/引用扫描、配置键扫描和 `git diff --check`。根验收已用 `D:\Anaconda\envs\CA-py310\python.exe scripts/run_rag_eval_production_acceptance.py --layer contract --list` 成功重跑 contract list（exit 0）；该命令只列出 checks、不执行 checks，且上述检查均不等同于真实生产验收通过。

---
2026.8.21（索引绑定调参集候选补题连接失败恢复）
- 【修复】：候选补题保留每批三次连接重试，但不再因连续两批短暂连接失败提前放弃其余已选知识单元；只有已选候选预算全部耗尽且数量仍不足时，才保持 fail-closed 失败。
- 【回归】：新增“连续两批连接失败后继续第三批并补齐候选”用例；原提前停止策略下用例稳定失败，移除提前停止后通过。

---
2026.8.22
- 【日志管理修复：补齐登录时间写入失败事件】
  - 保留 `mysql.connector.Error` 的数据库失败事件路径，为超时、连接异常和未知异常增加 `auth.login.last_login_update_failed` 事件及稳定原因码。
  - 更新认证服务测试和事件目录测试，验证登录时间写入失败不会阻断登录且不回显异常原文。

---
2026.8.23（RAG 子图编排与评测配置命名对齐）
- 【合并决策】：父图中的 RAG 阶段恢复为 Planner、ToolNode、Parser、Finalize 子图，并通过适配节点向父图投影最终检索结果；worker 同步采用模块化运行时入口。
- 【配置】：RAG 评测的配置键与临时目录统一使用 `RAG_EVAL`/`rag_eval` 命名，保留既有评测队列、题集与测试能力。

---
2026.8.24
- 【日志管理第三阶段：独立 Grafana 异常日志看板实现】
  - 保持 `causalagent-logs` Dashboard UID 和既有 Loki 数据源不变，将展示范围固定为 `warning`、`error`、`critical`，并继续在 Loki 中保留 INFO/DEBUG 供 Explore 排障。
  - 增加环境、服务、分类和异常级别筛选，以及异常总量、级别趋势、服务/分类分布、Top 10 事件码和最近 200 条异常日志面板。
  - `event_code` 仅在查询阶段解析；request、Job、用户、节点、工具和实例字段不新增为 Dashboard 变量或 Loki 标签。
  - 本次不新增 Flask/Vue 日志入口，不修改生产 Compose；只执行基础静态检查，真实 Grafana/Loki、浏览器和故障场景验收仍待人工完成。
- 【修复：旧 Job 执行占用阻塞数据库升级】
  - 为 `b2c3d4e5f6a7` 前的旧库增加只读迁移 preflight 和显式 dry-run/apply 修复工具，不改写历史 migration，也不自动清理业务数据。
  - 修复命令要求数据库、revision 和候选条数三重确认，拒绝运行中 Job、部分迁移 schema 和状态漂移；事务内只清除非运行 Job 的旧 `worker_id` / `locked_at`。
  - 程序化 bootstrap 保留共享 JSON stderr 日志配置，避免 Alembic `fileConfig` 覆盖最终失败事件，并补充数据库修复与启动编排测试。
- 【Grafana 默认简体中文】
  - 开发 Compose 将 Grafana 服务器默认语言设置为 `zh-Hans`，新账号和未保存个人语言偏好的账号默认使用简体中文。
  - 保留 Grafana 个人偏好优先级，不修改生产 Compose、数据卷、Loki 或异常日志看板查询。
- 【管理员侧栏新增 Grafana 切换入口】
  - 在管理员侧栏页脚的“进入聊天”上方新增“进入 Grafana”按钮，直接跳转到默认开发环境的 `http://127.0.0.1:3000/`。
  - 为桌面展开、折叠和移动端布局补齐交换图标、按钮间距及 Mock E2E/组件测试覆盖，不改变 Flask 管理员鉴权或 Grafana 登录边界。

---
2026.8.24
- 【联网搜索引用接口边界纠正】
  - 保留报告终态 `final_result.data.references` 与历史消息 `message.references` 的 `title + url` 引用接口，以及独立附件持久化能力。
  - 撤回 `preprocess`、`postprocess` 和 `report` 节点与引用功能无关的流式 LLM 配置；公开文字流仍仅用于普通问答和报告追问。
  - 增加父图 LLM 流式使用范围的回归测试，并补充普通用户 SSE 引用字段契约。

---
2026.8.24
- 【BUG修复】：兼容 mysql-connector 返回的大写元数据列名，修复统一数据库 bootstrap 启动失败。
- 【数据库修复】：完成现有数据库至 `j9e0f1a2b3c4`，清理已结束 Job 的历史运行时租约字段。

---
2026.8.24（RAG 评测产物路径统一与历史迁移）
- 【路径统一】：新增 `RAG_EVAL_ROOT`，默认将运行、来源、登记题集、调参集、基线、制品和报告归档到 `tmp/rag_eval/` 下；旧的细分环境变量仍可覆盖默认路径。
- 【迁移】：将旧 `tmp/rag_eval_*`、`Agent/knowledge_base/rag/output/` 以及 Document 下历史 Gold 治理产物迁移到统一目录，并生成迁移 SHA-256 清单；压缩索引制品同步修正归档内外的恢复路径。
- 【脚本与部署】：候选生成、基线、离线评测、验收 runner、制品打包和 Docker Compose 均改用统一路径；人工维护文档仍保留在 `Document/`。

---
2026.8.24（RAG 评测运行产物删除与来源竞态修复）
- 【工作索引删除】：工作台下拉框只展示 staged 摄取运行；新增摄取运行删除接口，删除前检查候选题、RAG 试跑、评测、治理、Gold 和生产 active pointer 引用。
- 【运行产物删除】：候选题、调参集治理和 Gold 治理的终态运行支持删除；运行中任务和仍有下游引用的运行统一拒绝删除。
- 【来源生命周期】：来源删除同时阻断 queued 摄取，避免排队任务在来源已删除后才开始执行；工作台补充来源显示名改名入口并清理已删除来源的元数据。
- 【验证】：新增生命周期回归测试，容器内定向测试 26 项全部通过；。

---
2026.8.25
- 【SearXNG 部署初始化与可降级边界】
  - `searxng-init` 改为在配置目录内生成临时 `settings.yml`，完成 secret 注入和占位符校验后原子发布，生成失败不留下半初始化目标文件。
  - 固定开发 Compose 的 SearXNG 镜像版本，并增加 `/healthz` 浅层 healthcheck；默认 app/worker 保持与 SearXNG 解耦。
  - 保留联网搜索运行期重试和统一降级语义，补充 init 脚本、Compose 部署契约测试和隔离 Docker 验证入口，并同步部署、启动和测试文档。
- 【联网搜索容错、开关校验与结果上限】
  - 为联网搜索新增 `WebSearchStatus`、`build_web_search_degradation_result()`、`degrade_web_search_parser_failure()` 和 `degrade_web_search_adapter_result()`；result_parser 与父图 web_search 节点分别注册协议错误和子图整体失败兜底，统一降级后回到 `agent`。
  - 降级结果统一包含 `success`、`status`、`query`、`results`、`content` 和错误字段；异常对象经 `sanitize_error()` 归类，`CancelledError` 与 `JobExecutionRevoked` 保持原有传播语义，不转成普通搜索失败。
  - `web_search_enabled` 在路由层和 Job service 层均只接受 JSON 布尔值，非布尔请求返回 `400` 或在服务层拒绝，并继续参与请求指纹和幂等冲突判断。
  - 统一使用 `WEB_SEARCH_MAX_RESULTS=9` 控制搜索结果合并、报告/追问注入和最终引用投影，避免报告依据超出公开引用范围。

---
2026.8.25
- 【合并 develop：联网搜索与结构化日志能力整合】
  - 同时保留联网搜索上下文路由和 develop 的节点结构化错误日志，补齐联网搜索父图与子图 error handler 的节点、超时元数据。
  - Job 创建链路同时持久化 `web_search_enabled` 与原始 `request_id`，保持搜索开关参与幂等指纹且重放不覆盖首次请求 ID。
  - 正常报告与降级报告继续公开受限搜索引用，同时遵守统一 JSON stderr 日志合同。
  - Web Search Planner 内部结构化输出降级改用受管事件，移除 query、命中数和路由选择的普通日志；历史引用附件解析失败使用不含消息 ID 和正文的注册事件。
  - 默认开发 Compose 同时保留 SearXNG/Valkey 搜索服务和 Loki/Alloy/Grafana 可观测拓扑，并同步部署与测试文档。

---
2026.8.25（RAG release readiness 与 Agent worker drain）
- 【Runtime readiness】：worker 启动复用 active release resolver 做轻量 pointer、manifest、正式来源、embedding、版本、collection 和向量目录检查；失败时继续运行并标记内部 `rag_unavailable`，保留对外 `status=unavailable`，不加载 RAG 重资源。
- 【Worker 生命周期】：新增 `JOB_DRAIN_TIMEOUT_SECONDS`（默认 60 秒）；SIGTERM/SIGINT 停止领取新 Job，超时只取消本地任务并交由既有 stale lease recovery 接管，worker Compose stop grace 至少 75 秒。
- 【Release 保留】：active pointer 发布时保留 previous pointer，维护 status 只读展示 active/previous/candidate 与候选超额提示，不自动删除目录；retrieval policy 继续独立回退与发布。
- 【文档/验证】：同步运行时、部署、架构、README、Compose 契约和定向 worker/index 测试；本轮未扩展当前生产 Compose 中缺失的 Agent worker/PostgreSQL checkpoint 拓扑。

---
2026.8.25（多模态正式来源 canonical 契约收紧）
- 【来源契约】：正式来源改用 canonical `source_id`、稳定 `document_id` 和精确 SHA-256；只在配置声明的受控目录树内按哈希唯一解析，并校验扩展名与文件签名。配置文件名变化不改写既有 Gold `document_id`，目录外同 hash 和零/多命中均 fail-closed。
- 【发布门禁】：legacy manifest 仅保留只读历史识别，不能满足正式发布；发布前重新校验 manifest、来源、策略、评测和索引完整性。VLM 默认关闭，只接受来源级显式授权，不引入页级授权。
- 【验证边界】：改名、唯一 hash、签名/扩展名、目录外同 hash、未授权 VLM 和非正式 manifest 使用 tempfile 受控目录 fixture；隔离 worktree 不含 `.gitignore` 忽略的正式 PDF，默认本地文件检查记录为 0 命中，未复制文件或放宽目录边界。

---
2026.8.25（多模态 release API 与发布控制台）
- 【VLM 授权】：隔离摄取远程 VLM 默认关闭；前端按来源提交显式授权，run 状态和 manifest 保存授权集合。
- 【正式发布】：新增 release status、gate-check、publish、rollback API；隔离 staged 索引先晋级正式候选目录，再由用户确认切换 active pointer，保留 previous 且不自动删除旧产物。
- 【前端】：新增正式发布控制台、门禁清单、active/previous 摘要和发布确认弹窗；检索策略配置发布按钮改名，避免与 active pointer 发布混淆。

---
2026.8.25（历史来源兼容与隔离索引级联删除）
- 【来源兼容】：正式固定来源 ID 改为内容 SHA-256 派生 ID；历史文件名绑定 ID 仅作为只读别名解析，重新摄取不再从历史索引覆盖当前来源选择。
- 【级联删除】：工作台删除摄取运行时可级联清理同一隔离运行树下的终态候选、评测、检索和治理产物；生产 active pointer、Gold、运行中任务以及非隔离共享注册数据仍阻断删除。
- 【验证】：后端相关回归 48 项通过（跳过 1 项条件测试），前端 typecheck、单测 3 项、生产构建、Python 编译和 diff 检查通过；真实历史运行仅做阻断验证，未删除现有产物。

---
2026.8.26（RAG 文档职责收敛）
- 【架构文档】：新增 `Document/architecture/rag-evaluation.md`，统一记录隔离评测、来源、staged index、release、评测 worker 与生产切换边界。
- 【API 契约】：新增 `Document/api/rag-eval.md`，覆盖当前 `/api/rag_eval` 的 77 个 method-path 操作、请求/响应、SSE 和兼容路径。
- 【清理】：移除 `Document/` 下旧的独立 RAG 说明、runbook、实验计划和 release worker spec；历史 `CHANGELOG.md` 条目保持不变。
- 【验证】：完成现行引用、Markdown 头部/相对链接、路由集合与 `git diff --check` 检查。

---
2026.8.26
- 【RAG 白名单导入与 develop 融合】
  - 从 `feature/rag_enhancement` 仅导入隔离评测、多模态摄取/OCR/索引、active release publish/rollback、Agent worker readiness/drain、RAG worker/卷/配置、迁移、前端、测试和验收 runner；保留 develop 的 Job、WebSearch、日志与文档体系。
  - RAG 评测任务使用 MySQL 持久队列和独立 `rag-eval-worker`；生产 Agent worker 启动时轻量检查 active release，缺失时保持无 RAG 降级，SIGTERM/SIGINT 按 `JOB_DRAIN_TIMEOUT_SECONDS` 停止领取并等待在途任务。
  - 多模态链路保留受控来源、OCR/可选远程视觉、staged index、评测与显式 release gate；publish/rollback 保留 active/previous 指针且不自动删除索引或运行产物。
- 新增 RAG API/架构文档、隔离评测前端、数据库迁移和生产验收入口；不导入 MySQL queue drill Compose、runner 与测试，并移除已被多模态运行时替代的旧 RAG 原型和样例导出文件。

---
2026.8.27
- 【查询修复】：优化会话事件查询索引，补充 SQL 与 MySQL 验证。
- 【Windows 桌面端】：完成 WebView2 MVP，增加独立运行时、启动器和安全导航边界。
- 【聊天界面】：重构新建会话、空状态、消息布局和首次发送过渡，补充前端与 Docker 验证。
- 【RAG 运行时】：补齐 readiness、portable release 和隔离评测链路，统一运行期 embedding 配置。
- 【验证】：定向测试和 integration acceptance 通过；完整 contract runner 因 Docling 子进程无输出未纳入通过证据。

---
2026.8.28
- 【embedding 配置】：生产 embedding 统一使用 API key 和 `EMBEDDING_*` 环境变量，本地 embedding 保持关闭。
- 【release 发布】：完善 manifest、Chroma、维度和 endpoint 完整性校验，接通 generation CAS 与评测绑定。
- 【RAG 运行时】：正式 runtime 从 active manifest 读取 embedding；API 故障降级为 RAG unavailable，不切换指针。
- 【正式索引】：formal index root 仅保留 API embedding active release，移除旧本地 fallback；Compose 同步 embedding 配置。
- 【解析策略】：远程图片、表格增强失败降为非阻断告警，其余来源、计数和质量门禁保持阻断。
- 【范围收敛】：checkpoint resume 不纳入本次提交。
- 【验证】：Docker focused、RAG 回归和主机定向测试通过；未执行真实 API 发布及 worker drain/restart。

---
2026.8.27
- [普通聊天双状态居中布局]
  - 【布局状态】：普通聊天页新增显式 new-chat / conversation 两种布局状态；新建会话、空历史会话、已有历史会话、首次发送和失败恢复分别按状态处理。
  - 【空状态】：欢迎词改为独立空状态元素，中文和英文文案同步更新；输入区与欢迎区在侧边栏之外的剩余区域居中组合。
  - 【消息布局】：消息、思考入口、思考详情、流式草稿、因果报告和因果图统一约束在最大 880px 的居中内容列，保留用户右对齐和 AI 原有左距。
  - 【交互过渡】：首次 Job 请求成功后使用 300ms FLIP 将输入卡片移动到底部，保留输入控件/文件草稿状态并支持 prefers-reduced-motion；请求失败时保留欢迎态和输入草稿。
  - 【验证】：新增前端布局状态 Node 单测；前端语法与 16 项 Node 状态测试通过，Docker unit-test 中聊天定向测试 21 项通过；浏览器完成未登录 DOM 隔离下的几何、窄窗口、侧栏、焦点和动画检查。
- [indows Developer Preview CD]
  - 【发行通道】：新增显式 `DeveloperPreview` PyInstaller 通道；onefile 冻结包默认访问 `http://127.0.0.1:5001/`，只接受 loopback origin 并强制关闭 debug，缺少通道标记的旧冻结包继续按安全的 Release 模式运行。
  - 【GitHub Release】：新增 `v*` tag 驱动的 Windows workflow，在 `windows-latest` 上运行桌面逻辑测试、构建 onefile、生成 SHA-256，并仅创建/更新 Draft Pre-release；GitHub 自动提供 tag 源码归档，本版本不推送 Docker 镜像。

---
2026.8.29
- 【README：用户、管理员与开发者入口同步】
  - 【用户入口】：补充用户聊天、RAG 运行台、管理后台和 Grafana 地址，区分普通聊天 RAG 与隔离摄取、评测、release 工作台。
  - 【能力与架构】：按当前 LangGraph 父图、MCP/RAG/Web Search 子图、Job worker、SSE/checkpoint 边界更新简化架构图，并补充 OLC、SearXNG、active release 与安全降级说明。
  - 【配置与组件】：补充最小环境配置、典型技术组件和结构化日志链路，链接到现有 Document 权威页面。
  - 【文档结构】：根 README 前半部分面向用户，后半部分承接管理员与开发者；收缩易漂移的项目目录树，并同步英文 README 的事实、图片路径、贡献规则和文档导航。
- 【v0.1.0 Developer Preview：首次版本发布摘要】
  - 【核心链路】：汇总 `develop` 相对 `main` 的首版交付，包含 LangGraph 因果分析、持久化 Job/SSE、幂等与恢复、MCP 工具、PostgreSQL checkpoint 和文件生命周期。
  - 【RAG 与搜索】：交付多模态来源摄取、staged index、混合检索、隔离评测、active release publish/rollback 和 SearXNG 学术搜索及安全降级。
  - 【管理与运维】：交付管理员前后端、MySQL 主从与监控、结构化日志、Alloy/Loki/Grafana、开发/测试/staging/production Compose 及分层技术文档。
  - 【发布制品】：发布 GitHub 自动源码归档与 Windows WebView2 Developer Preview onefile；桌面壳只连接本机服务，本版本不发布官方 Docker 镜像、不部署公网服务、不提供代码签名或自动更新。
- 【v0.1.0 Windows Release 附件补齐】
  - 【构建环境】：修复 GitHub Windows runner 未创建 `.venv-desktop`、而打包脚本只接受该虚拟环境所导致的 onefile 构建失败；依赖安装、逻辑测试、PyInstaller 构建和冻结标记检查统一使用桌面虚拟环境。
  - 【恢复入口】：为 workflow 增加显式手动补齐模式，从原 tag 重新检出和构建，只允许向已发布且未锁定的 Release 上传缺失附件；不改变正式版或 Pre-release 属性，并拒绝移动 tag、修改说明或覆盖同名附件。
  - 【文档与测试】：增加 workflow 静态安全契约测试，并同步桌面发布、CD 恢复流程和 v0.1.0 Release Notes 的验收边界。

---
2026.9.15
- 【MCP/Deep Agent：完成 P2-M 代码切片与 P2-U fake executor 前置】
  - 【P2-M 服务】：新增独立 `causal-mcp` 服务，提供 MCP 2.2 Streamable HTTP、Bearer/HMAC 鉴权、MySQL primary strong read 与旧 lease/worker fencing、固定 PC/OLC/DirectLiNGAM runner、bounded ProcessPool 和 N×K client pool。
  - 【P2-U 前置】：新增基于官方 `DeepAgentState` 的隔离扩展、runtime-only run context、显式 parent/deep state projection，以及带 checkpointer 的 fake executor graph；当前 worker 仍保留旧 stdio 路径，真实接入留待 P3。
  - 【部署与依赖】：补齐 `causal-mcp` 私网 Compose 服务、健康检查和资源边界；独立镜像固定 CDMIR 版本并使用 CPU-only Torch，镜像 `pip check` 通过。
  - 【验收证据】：Docker unit `454 passed`，相关 integration `20 passed`；隔离 MySQL strong-read/旧 lease、HMAC/Compose 配置、真实算法 fixture runner、并发排队/超时回收、A/B pool、故障 generation/cancel 和容器日志敏感字段扫描均按脚本记录。
  - 【边界与风险】：MySQL 使用最小隔离 schema；算法调用、`2 running + 4 queued`、RSS/CPU 为受控 fixture/容器基线，不构成生产容量或性能承诺；真实容器调用已验证健康、鉴权和结构化响应，但极小输入仍返回 `execution_failed`，完整 P3 worker HTTP 接入、迁移链路和生产规模验收尚未完成。
  - 【主线合并收口】：保留 DeepAgent 主线 State/Context/Graph 权威实现，补齐三阶段 lease fencing、异步 strong read、`/ready` 健康门禁、PC 参数透传、容量信号量释放、旧进程代际终止后回收和成功/失败目录事件；修复全量测试中的 RAG module stub 污染。
  - 【合并验证】：重建 Python 3.11 测试镜像后全量 unit `515 passed`；Agent/部署/migration/日志定向 integration `44 passed, 3 skipped`，开发 Compose 静态展开通过；既有 admin deployment 两项失败仍独立保留，不计入 MCP/DeepAgent 完成证据。
- 【P3 worker 与父图接入】：在普通 merge 合入 MCP 协作代码后，接入进程级 MCP client pool、真实 executor、官方 PostgreSQL Store 装配、静态 Algorithm Registry、Deep Agent 子图及外层 `agent → fold/preprocess → deep_agent → finalization_gate → report` 路径；生产路径不按 slot 启动 stdio session。
- 【P4 结果与事件收口】：加入 AlgorithmSpec 驱动的同响应 ToolNode dispatch、RAG evidence 惰性初始化、FinalizationGate 一次修正与二次 degraded 报告、公共 Tool result 安全字段和 `finalization_status`；未删除旧兼容文件或重要产物。


- 【数据库配置隔离】：新增独立 `DatabaseConfig`/`database_settings`，让 `app.db` 不再导入完整 `config.settings`；App/Worker 仍保留 `API_KEY`、`BASE_URL`、`MODEL` 的 fail-fast 校验和兼容的 `MYSQL_*` 字段。
- 【MCP 启动边界】：开发、预发和生产的 `causal-mcp` 配置不注入应用/模型密钥；开发 Compose 同时移除不必要的 `SECRET_KEY`，并补充无模型环境导入与 Compose 契约回归测试。

- 【Deep Agent 恢复边界】：生产装配让父图与 Deep Agent child 共用 worker 创建的 PostgreSQL saver，但使用独立稳定的 child thread/`deep_agent_v1` namespace；child scope 绑定 Job、attempt、lease 和冻结输入 hash，父图只投影 child 引用/status、结果、Ledger、证据和 Gate 所需事实，完整 messages/内部计划留在 child checkpoint。真实 PostgreSQL 重启恢复仍未作为 P5 通过项。
- 【Ledger 与结果加固】：InvocationRecord 新记录强制携带当前 Job、attempt、lease、worker 和输入 hash 归属，FinalizationGate 对缺失或错配归属 fail-closed；`AlgorithmResult.result_ref` 必须严格由自身 invocation 和规范非负 result index 构成，executor、Adapter 和 Gate 均复核 input/result_ref 契约。
- 【撤销与 MCP 语义】：`JobExecutionRevoked`/`CancelledError` 继续作为控制流传播，不生成普通失败 AlgorithmResult、不进入 FinalizationGate/degraded；MCP stale lease 映射为撤销，在途调用按 at-least-once 语义重建，不承诺从 HTTP 中途恢复。
- 【工具生命周期与 RAG】：算法、RAG、Web Tool 在真实外部调用前后通过 worker `OrderedEventWriter` 写入有序 lifecycle start/result，并用稳定 `event_key` 支持重放幂等；RAG readiness、release id 和输入快照绑定已传入实际 RAG Tool，未就绪或 release 不匹配时调用前短路。新增取消事件同键 payload 一致性回归，避免 sink 与 stream 产生幂等冲突。
- 【Ledger 归属补强与最终验证】：FinalizationGate 现在先对 Ledger 中指向当前 Job 的所有 invocation 统一校验 identity 与所有权字段，再仅纳入当前 attempt、lease、worker 和输入 hash 的算法记录；缺失、跨 Job 污染和当前 Job 的非法 invocation identity 均 fail-closed。新增相关回归后 Docker Agent unit/integration 共 `368 passed`；LangSmith 外部上报因测试环境 DNS 不可达产生告警，但不影响测试结果。

- 【普通端 Vue 并行迁移】
  - 【工程与传输层】：新增独立 `chat-frontend/` Vue 3 + TypeScript 工程，按 API schema、Pinia、Job runtime、组件和渲染器分域；普通端 SSE 改用 `fetch()` + `ReadableStream`，手工解析 `id/event/data`，保留 Flask SSE 路径和公共内容，增加未知事件游标、协议坏包和有界重连处理。
  - 【入口与构建】：增加 `/chat-next`、`/chat-legacy`、`/chat-assets/` 和 `CHAT_FRONTEND_ENTRY`/`CHAT_FRONTEND_DIST_DIR`/`CHAT_VITE_DEV_SERVER_URL`；Docker 通过 Node 24 `chat-builder` 构建 Vue 产物，最终 runtime 不包含 Node/npm。迁移期根入口默认仍为旧版。
  - 【文档与验证】：同步 `Document/`、`tests/README.md` 和普通端基线，明确启动配置错误以 `web.startup.failed` 日志加非零进程退出为失败证据。前端 Lint、类型检查、23 项 unit/contract、3 项组件、1 项 Mock E2E 和生产构建已通过；真实 Flask/数据库/worker/模型、Chrome/Edge、桌面壳等人工等价验收尚未完成，未切换根入口或删除旧版。
- 【普通端 Vue 视觉等价修复】
  - 【布局骨架】：以旧版 HTML/CSS/JS 为事实基线，恢复默认收起的 300px 抽屉侧栏、无顶部标题栏的主内容区，以及欢迎区与输入卡居中、会话输入卡置底的双状态 880px 内容列。
  - 【视觉语义】：恢复浅绿用户气泡、无卡片 AI 与思考文本流、旧版输入卡/按钮/文件草稿、认证遮罩、设置弹窗、用户信息弹窗、报告和因果图样式，并把语言切换入口放回设置菜单。

---
2026.9.16
- 【问题修复】
  - 【MCP 隔离修复】：新增无 `app`/LangChain/数据库依赖的共享 `Agent.execution_control.JobExecutionRevoked`，避免私有 MCP 镜像导入 `Agent.deep_agent_tools` 时加载 worker/job runtime；MCP-only import smoke 和相关 36 项测试通过。
  - 【PostgreSQL Store 启动修复】：移除传给 `psycopg.conninfo.make_conninfo()` 的非法 `autocommit` DSN 参数，补充连接串契约测试；相关 checkpoint/MCP/planner 回归 31 项通过。
- 【Deep Agent 调用后 Guard 修复】：为生产 `AgentRunContext` 补齐 `check_after_call()` 委托，确保节点与父图在模型或工具返回后继续校验当前 Job lease/cancel 状态，避免成功的 DeepSeek 响应因上下文接口缺失触发 `AttributeError` 和无效重试；新增生产上下文包装回归，Docker Agent unit/integration 共 `370 passed`。
- 【Deep Agent 记忆 namespace 修复】：修正官方 `StoreBackend` 传入 LangGraph `Runtime` 时的可信身份解包，按 `Runtime.context.trusted_identity.user_id` 生成既有用户隔离 namespace，避免 Deep Agent 在 child checkpoint 建立前因误判身份缺失而失败；新增 Runtime 包装形态回归，Docker Agent unit/integration 共 `371 passed`。确认无活动 Job/lease 后重启开发 worker，`worker.startup.ready` 与两个 `worker.slot.ready` 均正常。
- 【DeepSeek Responses 配置修复】：生产 Deep Agent 模型恢复 P0 已验证的 `output_version="responses/v1"` 与 `reasoning.effort="none"`，避免 DeepSeek 默认思考模式在携带 tools 和既有 assistant 历史时因缺少回传 `reasoning_text` 返回 HTTP 400；新增生产模型配置回归，Docker Agent unit/integration 共 `372 passed`。重启 worker 后真实 DeepSeek Responses 单/双工具、Tool 结果回填、结构化输出 smoke 以及真实 Deep Agent checkpoint、权限、取消、schema retry、无 reasoning stream、summarization P0 均通过。
- 【RAG 降级日志收敛】：移除 `RagService` 对 embedding API 故障的重复 `rag.enrichment.degraded` 记录，只保留 `embedding_runtime` 的单次分类日志；将 DashScope `AllocationQuota.FreeTierOnly`/免费额度耗尽归类为 `quota_billing`。
- 【工具状态文案修复】：公共 Tool 生命周期将 Web Search 关闭显示为“未启用”并保留 `not_ready` 状态，将 RAG/Web 依赖不可用显示为“暂不可用”，取消和超时继续使用独立文案；RAG/工具事件与日志合同定向 Docker 回归共 `100 passed`。
- 【MCP 调用可观测性】：worker 与 MCP 服务端统一按 `job_id + invocation_id + tool` 关联调用生命周期，补齐请求发起、接收、接受、完成、失败、取消与 60 秒慢调用事件；新增 Grafana「CausalAgent MCP Job 时间线」面板，关联字段只从 JSON 正文解析，不提升为 Loki 高基数标签。
- 【MCP 精确取消】：worker 在 Job lease/cancel 撤销时中断本地等待并发送签名控制请求，MCP 端按完整执行身份幂等取消排队 invocation 或终止对应独立算法进程，同时保留并行 sibling；取消继续作为控制流传播，不生成普通失败结果或进入算法重试。
- 【MCP 架构文档】：新增 MCP 客户端池与服务端算法执行池说明，完整记录 Worker 通信、Streamable HTTP/HTTP/1.1/TCP 分层、容量计算、generation 重连、精确取消、进程终止、一致性边界和运维注意事项，并加入技术文档导航。
- 【OLC 默认停用】：以 `DEFAULT_ALGORITHM_SPECS` 作为 worker Adapter 与 MCP runner 的共同 allowlist，当前只注册 PC、DirectLiNGAM；OLC 的 Spec、Adapter、runner 和算法实现继续保留，旧兼容 MCP 入口也取消工具装饰器，后续可通过恢复 allowlist 注册重新启用。
- 【FinalizationGate 修复】：补齐父图子图包装器传入的 `config` 参数，修复最终节点因签名不匹配导致的 `TypeError`；新增真实 LangGraph 包装执行回归，并同步两算法 schema 快照与架构/测试文档。Docker Agent unit/integration 共 `380 passed`，注册面调整后的定向回归 `38 passed`；测试容器的 LangSmith DNS 上报告警不影响断言结果。
- 【开发产物忽略】：忽略 Playwright CLI 在仓库根目录生成的 `.playwright-cli/` 页面快照与控制台产物，避免浏览器验证文件进入版本控制候选。
- 【MCP 控制 lane】：将 Worker MCP 客户端池拆为 execute/control 两个独立请求 lane；默认普通容量 `2 × 1`、控制容量 `1 × 2`，取消不再受普通请求占满影响，重连保留 lane 并记录 `pool_lane`。
- 【取消预算与原因】：新增控制槽获取 1 秒、取消确认总预算 6 秒配置；取消失败细分为 `control_capacity_timeout`、`response_timeout`、`transport_error` 和 `invalid_response`，外层 `CancelledError` 继续传播。
- 【部署与验收】：同步开发、预发、生产 Compose 与 `.env.example`，补充 lane/容量/事件目录契约测试及真实 HTTP 饱和并发验收场景；Docker 定向回归 `62 passed`、完整单元测试 `575 passed`，MCP spike 与真实 HTTP 饱和/双取消验收通过。
- 【Deep Agent 公开决策与刷新恢复】：算法 Tool Call 新增可选 `public_decision.summary` 公开说明 envelope，dependency middleware 在调度和执行前剥离该字段并将有效说明持久化为幂等 `decision` 事件；FinalizationGate 通过后再把内部结果引用映射为公开算法名和最终选择说明，degraded 路径不公开未验证决策。
- 【前端历史回放】：确认现有 Deep Agent lifecycle 已完整落入 `analysis_job_events` 并能由 `/api/load_session` 重建；前端增加明细先于父阶段到达时的 `step_id` 暂存补绘，并为相关静态脚本增加版本参数，避免缓存旧恢复代码。
- 【Agent worker：graph 终态失败日志保真】
  - 【异常传递】：`graph_runner` 不再把 LangGraph 抛出的异常压缩成一句脱敏文案后丢弃；公开 `message` 保持 `sanitize_public_error()` 原有文案不变，真实异常改由 `_diagnostic` 内部字段携带 `error_category`、真实 `reason_code` 和 `exc_info` 继续传递。
  - 【内部通道】：`OrderedEventWriter` 只把 `message` 交给 `fail_job`，诊断与 `terminal_type == "error"` 同步挂在只读的 `terminal_diagnostic` 上；`_diagnostic` 不进入 `analysis_job_events`、SSE、聊天投影或管理员接口。
  - 【稳定分类】：`worker.job.failed` 新增 `error_category` 字段，取值为 `provider_error/protocol_error/checkpoint_error/runtime_contract_error/internal_error`，只按异常类名（含基类）判定、不读取异常文本；`reason_code` 由固定 `node_error` 改为按异常映射到既有 `REASON_CODES`，未识别异常仍保留 `node_error`。
  - 【日志产出】：`worker.job.failed` 现在带上非 null 的 `exception_type` 与清理后的 `stack`，可定位到具体堆栈帧。
  - 【同步更新】：`Document/development/observability.md` 的事件表与关联链路补充 `error_category` 取值和内部诊断边界。

- 【普通端 Vue 单架构收敛】
  - 【布局与任务交互】：侧栏内容区改为占满剩余高度，使设置与用户入口固定在底部；Thinking 标题和执行步骤统一左对齐；移除独立取消按钮，运行时发送键显示旋转进度环与中心停止方块，再次点击沿用 Job 取消接口。
  - 【入口与部署】：根路由固定提供 Vue 构建产物，`/chat-next` 仅保留兼容别名，移除 `/chat-legacy` 和 `CHAT_FRONTEND_ENTRY` 的路由、配置及三个 Compose 引用；缺少 Vue dist 时继续返回带 request ID 的稳定 503。
  - 【旧文件边界】：旧普通端静态文件已经退出运行时引用；受仓库禁止 agent 删除重要文件的规则限制，物理文件仍保留并在普通端文档中列出人工删除清单，独立 RAG 工作台不在清理范围内。

---
2026.9.17
- 【工具公开决策扩展与渐进展示】
  - 【检索决策】：`rag_evidence_search` 与 `web_evidence_search` 复用 `public_decision.summary` envelope，在外部检索前剥离该字段并写入 `decision_kind=evidence` 幂等事件；检索器、Adapter 和 MCP 入参不含该字段，缺失或格式无效不阻断工具。
  - 【渐进展示】：`decision` 事件仍以完整校验文本单条落库；实时页面按字符渐进显示，历史回放与 `prefers-reduced-motion` 环境直接展示完整文本，展示速度不阻塞算法执行。聊天页脚本与样式缓存版本更新为 `20260917-tool-decisions-2`。
  - 【并行 evidence 修复】：RAG/Web evidence reference 在进入 Deep Agent State 和 ToolMessage 前增加稳定 invocation 作用域，修复不同并行查询复用 `E1` 或重叠来源时触发不可变 reducer 冲突；同一调用恢复仍复用相同 reference。
  - 【并行展示修复】：同一阶段同时到达的公开决策继续各自保留，但渐进动画改为按事件到达顺序串行执行，避免多行文字同时流式出现；历史回放和减少动态效果偏好保持即时展示。
- 【文档：Deep Agent 文档核对】
  - 【Agent：运行事实与历史材料收束】：以生产 worker、父/子图、AlgorithmSpec allowlist、MCP execute/control lane、公共事件和部署配置为准重写 Agent 运行时及系统总览；将冗长实施计划收束为长期维护记录，并把产品规划与技术设计标为历史决策材料。修正公共工具状态、实际配置入口、父子 checkpoint 身份和 raw 文件归属；明确当前 cleanup outbox 尚未删除 Deep Agent child thread，以及 recursion/finalization retry 两项配置尚未接入生产调用路径。
- 【FinalizationGate：终态引用契约与修正链路】
  - 【引用契约】：`FinalAnalysisDecision` 明确两套引用命名空间：`result_assessments`、`primary_result_ref`、`conflicts.result_refs` 与 `revision_proposals.result_ref` 只接受本次运行返回的算法结果引用，RAG/Web 证据引用只能出现在 `revision_proposals.evidence_refs`；该分工写入结构化字段描述与系统提示，使模型在提交时就能区分“算法结果取舍”和“检索证据引用”。
  - 【规则化修正指令】：Gate 校验失败改为携带稳定规则码，并把规则翻译成脱敏的中文修正要求写进重试指令，使模型知道具体违反了哪条引用规则，而不是只收到泛泛的重新提交要求；身份、账本与状态一致性失败不带规则码，继续使用通用指令，不把内部完整性问题包装成模型可修正的指令。
  - 【阶段公开说明】：Gate 拒绝时发布 `progress` 阶段说明，可修正时挂在 `finalization_gate` 阶段并给出修正要求，降级时说明本次仅基于已验证输入生成报告；第二次 Deep Agent 启动修正时，新阶段同样收到一条 `progress` 说明，指出该阶段沿用已有工具结果、不重复调用工具。事件适配器按登记节点名绑定活跃阶段，并拒绝未登记节点名与空文本。
  - 【事故回归】：新增复现真实事故的用例，把证据引用写进 `result_assessments` 时先以 `assessment_ref_unknown_result` 拒绝，只修该处后继续以 `proposal_evidence_ref_unknown` 拒绝，两处都修正后才通过，锁定两处违规与修正路径。

- 【Agent 持久化清理：父子图 checkpoint 与用户长期记忆统一清理】
  - 【数据库迁移】：新增 `user_memory_cleanup_outbox`（revision `t5e6f7a8b9c0`），按 `user_id` 唯一保存状态、重试次数、可领取时间、租约、脱敏错误结论和完成时间；只与 `admin_operations` 建立外键，不关联 `users`，从而在用户行删除后仍保留任务；迁移不回填也不激活历史数据，downgrade 只删除新表；同时新增 `u7a8b9c0d1e2` 把 `database_monitor_snapshots.snapshot_key` 扩展到 64 字符，容纳按进程命名的清理心跳与队列快照键。
  - 【删除事务】：用户物理删除在同一个 MySQL 事务中为每个 Job 登记 checkpoint 清理、为同一 `user_id` 登记一条长期记忆清理，并写入管理员操作聚合；任一登记失败回滚整个删除；单独删除 Session 或 Job 仍只清理 checkpoint，保留用户长期记忆。
  - 【清理 worker】：`Database/checkpoint_cleanup_worker.py` 与 `app/agent/checkpoint_cleanup.py` 改名为 `Database/agent_persistence_cleanup_worker.py` 与 `app/agent/persistence_cleanup.py`；一个进程轮转消费两张 outbox，复用同一个 PostgreSQL 连接池构造 `AsyncPostgresSaver` 和 `AsyncPostgresStore`，不新增第二个容器；Compose 服务名、容器名、启动命令和 `AGENT_PERSISTENCE_CLEANUP_*` 环境变量同步切换。
  - 【父子图清理】：checkpoint 任务在一次 attempt 内删除父图 `thread_id=job_id` 和子图 `thread_id=deep-agent:<uuid5(job_id)>`，两者都成功才标记成功，部分成功整项重试并依赖官方删除接口的幂等性。
  - 【长期记忆清理】：记忆任务按可信 `("causalagent", "memory", str(user_id))` namespace 使用官方 Store API 枚举并逐条删除，删除后重新查询确认 namespace 为空，不对 Store 表执行宽泛 SQL；清理 worker 启动时校验 checkpoint 与 Store schema 版本，Store 未 setup 时有界等待，不自行建表。
  - 【操作聚合与维护入口】：管理员用户删除只有在 checkpoint 清理和记忆清理全部成功后进入 `succeeded`，任一任务最终失败进入 `failed`；`Database/lifecycle_repair.py` 的失败/过期重置扩展到两张 outbox。
  - 【监控与看板】：worker 心跳快照改为 `agent_persistence_cleanup_runtime` 并新增当前任务类型；队列快照改为 `agent_persistence_cleanup_outbox`，分别汇总两类 outbox 的 pending、due、processing、过期租约和 failed 数量；管理员数据库看板、用户删除结果视图和 SQL 语义映射同步更新字段与名称。
  - 【日志事件】：cleanup 事件前缀统一为 `agent.persistence.cleanup.*`，成功事件新增 `task_type` 和 `deleted_count`，运行快照不再保留旧的 `checkpoint_cleanup_runtime` 名称。
  - 【文档同步】：更新系统架构总览、Job/文件生命周期、数据库总览与迁移 checkpoint、监控、部署、可观测性、测试和管理员模块文档，删除已经修复的父子图与长期记忆清理缺口描述。

- 【因果图渲染修复】
  - 【载荷投影】：`process_final_result` 在展示层把 Agent 内部标准化图（节点名列表与 `source`/`target` 边）投影为前端 vis-network 的 `{id,label}` 节点与 `{from,to}` 边，边类型映射为箭头与虚线，权重按 `.6g` 作为边标签，`graph_semantics` 等内部字段不再进入公开载荷。
  - 【历史会话】：`/api/load_session` 读取 `causal_graph` 附件时执行同一投影，早期版本按内部格式写入的附件不需要重跑分析即可恢复显示。
  - 【失败可见】：前端 `renderCausalGraph` 把节点/边建表与网络创建一起纳入异常处理，数据格式不符合 vis-network 要求时在图上直接给出说明文本，同时更新聊天页脚本缓存版本号。

---
2026.9.18
- 【普通端 Vue 合并 develop 的 Deep Agent 变更】
  - 【合并】：在 `refactor(Frontend)/refactor-fronten` 上合并 `origin/develop`（PR #74 合入的 Deep Agent 后端与公共事件契约）；文本冲突只出现在 `CHANGELOG.md` 与 `Document/README.md`，自动合并的 `Dockerfile`、`config/settings.py`、三个 Compose、`tests/README.md` 和 `Document/` 页面逐项核对后同时保留双方内容。
  - 【阶段明细】：Vue 端补齐 `decision_delta` 增量、`decision_kind` 公开前缀、同一工具生命周期事件的挂起与放行、明细早于父 `node_start` 时的暂存补绘，以及 `node_retry`/`node_end` 的错误与重试文案。
  - 【展示】：新增草稿与公开决策的逐字展示（40 字/秒、25ms 步进）；终态校正同一草稿，报告布局同样复用；`prefers-reduced-motion` 和终态直接展示完整文本。聊天区新增 80px 阈值滚动跟随，用户主动上滑后停止跟随。
  - 【验证】：`npm ci` 与 `npm run check`（Lint、`vue-tsc -b` 类型检查、35 项 unit/contract、9 项组件、1 项 Mock E2E、生产构建）通过；`python -m pytest tests/integration/deployment/test_chat_frontend.py` 8 项通过；`docker compose -f docker-compose.yml config --quiet` 通过；遗留静态脚本的 19 项 Node 测试继续通过。
  - 【计划外修正】：`chat-frontend` 的 `typecheck` 原本是 `vue-tsc --noEmit`，在 solution 配置下不检查任何文件，改为 `vue-tsc -b` 后才暴露并修正事件适配器的一处类型错误；`Document/development/deployment.md` 的镜像阶段数量和依赖锁定说明按当前 `Dockerfile` 更新。
  - 【边界】：真实 Flask、数据库、worker、模型和浏览器人工验收未执行；`app/static/` 旧静态文件及其配套 Node 测试仍按 `Document/development/chat-frontend.md` 的清单由用户自行删除。
  - 【工具事件顺序】：同一工具的 `tool_call_start`/`tool_call_result` 改为在该决策的逐字展示追平之后才出现；收到完整 `decision` 只结束该决策流，工具结果与任务终态负责强制放行，修正原先收到完整 `decision` 就立即放行、决策文本与工具调用同时出现的问题。
  - 【界面尺寸】：普通端侧栏宽度由 300px 调整为 360px，会话与文件条目在右侧为编辑、删除按钮留出固定位置，标题不再被按钮压住；用户消息气泡的上下内边距由 7px 调整为 12px。
  - 【计划外修正】：`chat-frontend` 的 `typecheck` 原本是 `vue-tsc --noEmit`，在 solution 配置下不检查任何文件，改为 `vue-tsc -b` 后修正了事件适配器的一处类型错误；`Document/development/deployment.md` 的镜像阶段数量和依赖锁定说明按当前 `Dockerfile` 更新。
- 【CDFM v0.1 causal-mcp 能力接入】
  - 【契约与接线】：默认 Algorithm Registry 新增 `causal.cdfm`/`causal_cdfm`，工具只公开 `threshold`；新增连续数值输入边界、CPU CDFM runner、`directed_graph` 语义和独立矩阵方向转换。
  - 【结果与部署】：通过私有 `AlgorithmExecutionResponse` 保存 logits、probabilities、threshold 到 raw artifact，标准结果和公共事件不携带 raw 字段；causal-mcp 镜像固定 `cdfm-base==0.1.0`、CPU Torch 与 `CDFM_MODEL_PATH` 配置。
  - 【验收边界】：新增单元契约和独立 `CDFM_MCP_ENGINEERING_SMOKE` 入口；本轮不扩展算法路由、共识、准确率、模型常驻化或生产图环路修复承诺。
- 【多模态索引目录改为可写命名卷】
  - 【Compose】：开发、兼容副本、预发和生产把 `Agent/knowledge_base/multimodal_indexes` 从仓库直挂或只读挂载改为可写命名卷（`kb_multimodal_indexes`、`kb_multimodal_indexes_staging`、`kb_multimodal_indexes_prod`），覆盖会打开该索引的 app、worker 和开发/兼容副本的 rag-eval-worker；新增一次性 `kb-indexes-sync` 服务作为这些服务的 `service_completed_successfully` 依赖，在启动前把宿主 release 复制进卷；staging 的 `STAGING_VOLUME_NAMES` 同步登记新卷。
  - 【文档】：部署文档新增“多模态索引目录与挂载”小节，说明 Chroma 构造 `PersistentClient` 时写入 `acquire_write` 写锁记录、只读挂载会让 RAG 降级为 `rag_unavailable`、卷与宿主 release 的一致性要求、发布后导出与卷重建步骤以及禁止 `down -v`；开发环境文档补充同步与导出命令并登记 `kb-indexes-sync`；根 AGENTS.md 增加对应核对项。
  - 【测试】：`tests/test_staging_compose_contract.py` 改为断言四份 Compose 的可写命名卷挂载，并移除 staging 的索引只读挂载契约。
- 【报告模块：结构化报告文档】
  - 【数据结构】：新增 `Agent/Report/document.py` 与 `Agent/Report/assets.py`，定义 `ReportDraft`、`ReportDocument`、四种报告块（`section`、`markdown`、`chart`、`causal_graph`）、图表资源、因果图业务模型、来源与证据模型，并提供块 ID 唯一性、块类型、资源引用和证据引用校验；因果图节点 ID 由变量名生成（如 `node_age`），边 ID 由端点变量名生成（如 `edge_age_income`），来源和证据 ID 使用前缀加随机 UUID，`markdown.content` 是新报告中唯一允许 Markdown 的字段。
  - 【报告节点】：`report_node` 改为通过统一结构化输出入口生成 `ReportDraft`，图表资源、因果图模型、来源和证据由后端注入；块 ID 重复、块类型未知、`asset_key` 不存在或类型不匹配、`evidence_id` 不存在时进入受控错误路径并生成降级报告文档，不保存部分报告。提示词中的数据概览改为按列压缩后的 JSON 文本，只保留数据规模、列清单和每列的类型与质量标记，去掉每列的取值分布；资源清单和证据清单同样以 JSON 文本传入，避免字典字面量表示和提示词长度随取值数量增长。
  - 【移除旧格式依赖】：新报告不再使用 `visualization_mapping`、Base64 图片、HTML 图片标签和 `[[CHART:...]]` 占位符；预处理阶段改为生成直方图、分类柱状图和相关性热力图的结构化数据（`bins`/`counts`、`categories`/`counts`、`variables`/`matrix`），不再生成 Base64 图表。
  - 【State 与追问】：`CausalAgentState` 用 `chart_assets` 保存结构化图表资源、用 `report_document` 保存结构化报告文档，移除 `final_report` 与 `visualization_mapping`；路由判断和报告追问只读取报告摘要、资源说明、来源说明和证据说明。
- 【报告终态与持久化】
  - 【SSE】：`process_final_result` 报告分支返回 `{type: "report", layout: "report", render_mode: "structured", document}`，保留 `finalization_status` 与联网搜索 `references` 附加字段，不新增 SSE 事件类型；结构化报告不产生文字增量，`final_result` 到达后整份渲染为结构化报告组件。
  - 【附件与迁移】：新增迁移 `v8b9c0d1e2f3` 为 `chat_attachments.attachment_type` 增加 `report_document`，只追加枚举值并保留历史附件；`prepare_ai_response_for_storage` 把完整报告文档写入 `report_document` 附件，`chat_messages.content` 只保存报告标题预览，新报告不再写入 `visualization` 附件。
  - 【历史恢复】：`/api/load_session` 读取 `report_document` 附件后重新通过后端 schema 校验，再包装成与 SSE 一致的结构化报告载荷；附件损坏或 schema 非法时记录 `chat.attachment.degraded` 并回退到消息预览正文。
  - 【就绪检查】：数据库 readiness 的附件类型枚举检查同步要求 `report_document`。
- 【普通端报告渲染】
  - 【渲染器】：新增 `ReportRenderer.vue`、`ReportSection.vue`、`ReportBlockView.vue`、`MarkdownBlock.vue`、`ChartBlock.vue` 和 `CausalGraphBlock.vue`；`MessageBody.vue` 按 `type=report` 与 `render_mode=structured` 分发到报告渲染器，普通聊天消息继续使用 Markdown。
  - 【防御性解析】：新增 `renderers/report-document.ts`，用 Zod 校验报告文档与资源；未知块类型降级为占位块，非法图表或因果图资源引用显示受控提示，顶层载荷不合法时显示报告不可用提示，都不影响页面其余部分。
  - 【图表与因果图】：直方图和分类柱状图使用 SVG、相关性热力图使用 CSS 网格绘制，支持响应式宽度、数值提示与空数据提示，不引入第三方图表库；`graph-renderer.ts` 增加业务模型到 vis-network 的投影函数、`view`/`select` 模式和实例更新/销毁接口，`CausalGraph.vue` 支持图数据变化时原地更新并向上抛出 `selectNode`/`selectEdge`，图形库仍按需动态加载。
  - 【样式】：报告主题变量集中在 `.report-document`，报告背景、标题层级、章节间距、Markdown 表格、证据提示和移动端布局由组件作用域样式控制，模型不返回类名或样式。
- 【文档：结构化报告契约】
  - 【API、架构与数据库】：更新 `Document/api/agent-jobs.md`、`Document/api/chat-files.md`、`Document/architecture/agent-runtime.md`、`Document/database/migrations-checkpoints.md` 和 `Document/development/chat-frontend.md`，写入报告文档结构、`report_document` 附件、历史恢复与降级行为、迁移 head 和前端渲染边界。
- 【应用 README：Deep Agent 与 CDFM 能力补齐】
  - 【中文 README：技术栈与运行流程】：技术栈加入 Deep Agents、TypeScript 和 Pinia；Agent 运行流程改为父图编排预处理、Deep Agent 子图、终态校验与报告节点，并补上 PostgreSQL 长期记忆的独立边界。
  - 【中文 README：深度分析 Agent】：新增该章节，说明显式状态投影、父子图独立执行身份、按 `requires/produces` 分层的工具编排、长期记忆写入白名单、有界预算、终态校验与取消路径。
  - 【中文 README：算法清单】：因果分析改为默认启用 PC、DirectLiNGAM 和 CDFM，补充 CDFM 只公开 `threshold`、缺失值掩码与分类变量不适用的边界，并说明 OLC 的实现保留但不进入默认工具面。
  - 【中文 README：报告、配置与部署】：报告生成补充结构化报告渲染；最小配置新增 `DEEP_AGENT_*`、`CAUSAL_MCP_*` 和 `CDFM_MODEL_PATH`；Docker 说明改为 Node 24 分别构建普通端与管理员 Vue。
  - 【中文 README：开发入口与项目结构】：新增“普通端前端（Vue）”章节，说明 `chat-frontend/` 构建产物的入口、缺少构建时的 503 边界、Vite 开发方式和检查命令；项目结构补充 `chat-frontend/`、`Agent/deep_agent` 和 `Agent/deep_agent_tools`。
  - 【英文 README】：同步上述能力、配置、开发入口和项目结构。
- 【旧普通端静态前端清理】
  - 【删除静态文件】：删除 `app/static/chat.html`、`app/static/css/style.css` 与 `app/static/js/` 下的 `script.js`、`chat_layout_state.js`、`execution_phase_state.js`、`job_subscription_state.js`、`stream_state.js`、`marked.min.js`，并移除随之为空的 `app/static/css` 与 `app/static/js` 目录；`app/static/rag_eval_app/` 是 RAG 工作台自己的产物，保留原样。
  - 【失效测试】：删除只覆盖旧页面的 `tests/unit/frontend/` 四个 Node 测试与 `admin-frontend/tests/e2e-mock/chat-auth.spec.ts`，管理员端 Mock E2E 仍由 `admin-ui.spec.ts` 承担。
  - 【文档】：`Document/development/chat-frontend.md` 的“旧文件清理边界”改写为已完成的清理记录，并说明 `admin-frontend/tests/e2e/admin.spec.ts` 仍在使用旧页面元素 id，需要按 Vue 普通端选择器更新。
- 【公开预览、登录拦截与匿名访问日志】
  - 【公开预览】：普通端入口区分 `checking`、匿名和已登录三种状态，未登录访客先看到公开预览，包含产品说明、静态示例会话、结构化示例报告和示例因果图；示例数据放在 `chat-frontend/src/preview/public-preview-data.ts`，只在前端渲染，不经过任何公开数据接口，也不创建数据库 Session、消息、文件或分析 Job。
  - 【登录拦截与草稿】：预览中的 Composer 以 `auth-required` 模式渲染，点击发送或上传只打开登录面板，上传按钮不弹出文件选择框；文字草稿保存在共享的 Composer store 并同步写入 `sessionStorage`（键 `causalagent.preview.draft`），登录面板关闭后文字仍在，登录成功后草稿进入正式 Composer 但不自动发送，用户再次点击发送才创建真实 Session 和 Job；文件对象、文件名和文件内容不写入任何存储或统计请求。
  - 【匿名统计接口】：新增 `POST /api/analytics/events`，不要求登录也不创建任何业务数据，只接受 UUID `visitor_id` 和最多 10 个已登记事件，请求体上限 8 KiB，不接受客户端时间，非法批次整体拒绝且不产生部分日志，成功返回 `202` 和已接受数量。
  - 【事件与脱敏】：事件目录新增 `analytics.public_preview.view`、`analytics.public_preview.demo_open`、`analytics.public_preview.send_click`、`analytics.auth.panel_open` 和 `analytics.auth.login_success`，详情只允许 `visitor_hash`、`page`、`demo_key` 且页面与示例标识按固定取值登记；浏览器匿名标识经服务端密钥 HMAC-SHA256 后只作为日志正文关联字段，原始标识、消息正文、文件名、文件内容和凭据都不进入日志，也不配置为 Loki 标签。登录成功事件由认证路由直接记录，不依赖前端上报。
  - 【日志量控制】：同一浏览器会话内公开预览展示、示例打开和登录面板打开按页面与示例去重，发送点击每次真实点击上报一次；前端使用 `sendBeacon` 或 `keepalive` `fetch` 发送，不等待响应、失败不重试，统计异常不影响浏览、登录和真实业务接口。
  - 【测试】：新增 `tests/unit/analytics/test_public_analytics_events.py`，覆盖接口边界、整体拒绝、访客标识脱敏、业务隔离和登录侧事件写入；`tests/unit/test_event_catalog.py` 与 `tests/integration/test_logging_policy.py` 同步新增事件；`chat-frontend` 新增匿名统计客户端单元测试、公开预览组件测试，并把 Mock E2E 改为未登录公开预览到登录后真实 Job 的完整流程。
  - 【文档同步】：更新 `Document/api/conventions.md`、`Document/development/chat-frontend.md` 和 `Document/development/observability.md`，登记公开预览状态、登录拦截、草稿与文件边界、匿名事件合同、脱敏和日志量控制；同时修正日志事件目录表缺失的 `rag.runtime.ready`/`rag.sparse.ready` 行、`mcp.client.reconnected` 的 `pool_lane` 字段，以及测试文档引用失效测试文件路径的问题。

---

2026.9.19

- 【普通用户前端：登录面板修复】
  - 【卡片背景】：`AuthPanel` 的卡片此前没有设置背景色，公开预览的正文、按钮和消息会透过卡片显示，与登录标题、标签和输入框叠在一起；现在卡片改为不透明的白色表面，带边框、12px 圆角和阴影，遮罩保持 55% 深色以隔离底层页面。
  - 【表单样式】：原样式只依赖浏览器默认输入框外观，现按 `tokens.css` 的设计变量重写标签、输入框、主按钮和次级链接的字号、间距、圆角与悬停/禁用状态，并保留全局键盘焦点轮廓；宽度收窄到 400px，480px 以下收窄内边距。
  - 【面板交互】：面板改为带 `role="dialog"` 和 `aria-modal` 的模态卡片，打开时自动聚焦用户名输入框；匿名预览下可以按 Esc、点击遮罩或点击“先浏览公开预览”关闭面板，等待接口响应期间关闭动作不生效。
  - 【注册与密码】：新增显示/隐藏密码按钮；注册成功消息改用独立的提示样式而不是错误样式，面板自动回到登录态，已填写的用户名和密码保留。
  - 【测试与文档】：`tests/components/auth-panel.spec.ts` 新增密码切换、注册成功后回到登录态、Esc 与遮罩关闭的用例；Mock E2E 的密码定位改为精确匹配；`Document/development/chat-frontend.md` 补充面板遮罩、关闭方式和注册成功后的行为。
- 【官网、应用入口与权限系统重构】
  - 【RBAC 数据模型】：新增迁移 `w9c0d1e2f3a4` 建立 `roles`、`permissions`、`user_roles`、`role_permissions` 四张表，初始化 `user` 与 `admin` 两个角色和第一阶段 13 个权限键，并按现有 `users.role` 回填 `user_roles` 关系；`users.role` 保留为过渡兼容字段，启动就绪检查同步要求四张新表和两个已初始化角色。
  - 【授权与身份缓存】：新增 `app/auth/rbac.py` 统一读取角色与权限；`get_current_session_user()` 与新增的 `get_current_permissions()` 在同一个请求内只解析一次身份和权限；新增 `require_authenticated_user()`、`require_permission()` 与 `enforce_permission()`，未登录 API 返回 `401`、已登录但权限不足返回 `403`、页面未登录跳转 `/auth/sign-in`、页面权限不足返回受控 403 页面；`admin_required` 改为要求 `admin.access` 权限，Session 仍只保存 `user_id`、`auth_version` 和展示用用户名。
  - 【登录与回跳】：登录和 `check_auth` 返回角色、权限列表和统一登录默认落点；登录回跳改为白名单校验，只接受 `/dashboard`、`/dashboard/settings`、`/dashboard/session/<id>`、`/rag-eval` 和已知管理员页面，查询参数与片段一律丢弃；管理员登录入口由根路径改为 `/auth/sign-in`。
  - 【RAG 评测台】：`/api/rag_eval` 蓝图统一要求 `rag_eval.access`，读请求要求 `rag_eval.read`、写请求要求 `rag_eval.run` 并额外要求 CSRF 令牌，发布、回滚和治理路由分别要求 `rag_eval.publish`、`rag_eval.rollback` 和 `rag_eval.governance`，并按下发状态把操作者、动作、目标、请求 ID 和结果写入 `admin_audit_events`；页面移动到 `/rag-eval`，页面与 `/rag-eval/assets/` 使用同一权限边界；旧的 `/rag_eval` 别名和 `app/static/rag_eval_app/` 无保护静态路径一并移除，应用不再暴露 Flask 默认 `/static` 目录，RAG 前端产物改为 `app/rag_eval/frontend_dist/`，构建 base 改为 `/rag-eval/`，前端写请求统一携带 CSRF 令牌。
  - 【官网前端】：新增 `website-frontend/` 独立 Vue 工程，提供 `/`、`/product`、`/about`、`/docs`、`/changelog` 与 `/auth/sign-in`、`/auth/sign-up` 页面，包含类型化内容数据、导航、下载入口和登录注册表单，构建 base 为 `/site-assets/`；Flask 新增 `WEBSITE_FRONTEND_DIST_DIR` 与 `WEBSITE_VITE_DEV_SERVER_URL` 配置，缺少构建产物时返回 503 和 request ID。
  - 【普通用户应用】：`chat-frontend/` 收窄为登录后的工作区，入口改为 `/dashboard`、`/dashboard/session/<id>` 和 `/dashboard/settings`，构建 base 改为 `/dashboard-assets/`，未登录访问跳转 `/auth/sign-in`，退出登录回到 `/`；地址成为当前会话的来源，浏览器前进后退可以回到同一会话，用户菜单按 `admin.access` 与 `rag_eval.access` 显示管理后台和 RAG 评测台入口；普通应用不再展示公开预览和内部登录面板，`/chat-next` 与 `/chat-assets/` 别名移除。
  - 【部署与桌面端】：Dockerfile 新增 `website-builder` 与 `rag-eval-builder` 阶段，分别输出到 `/opt/causalagent-website` 和 `/opt/causalagent-rag-eval`；三套 Compose 向 app 传递官网与 RAG 的产物目录和开发服务器变量；Windows 客户端默认入口与 Release 默认地址由根路径改为 `/dashboard`。
  - 【测试】：新增 `tests/unit/auth/test_rbac.py`、`tests/unit/auth/test_authorization_permissions.py`、`tests/unit/auth/test_rag_eval_gate.py` 和 `tests/integration/deployment/test_frontend_entrypoints.py`（替代原 `test_chat_frontend.py`），RAG 路由契约测试、管理员授权测试、迁移链路测试和 `chat-frontend` Mock E2E 按新入口与权限边界更新；`tests/support/authorization.py` 提供路由契约测试共用的身份与权限替身。
- 【文档事实同步与前端清理】
  - 【文档事实】：按当前实现更新 `Document/development/chat-frontend.md`（`/dashboard` 入口、地址与会话映射、删除公开预览与登录拦截章节）、`Document/development/setup.md`（桌面默认地址、四个前端的开发服务器与 base）、`Document/development/testing.md`（测试分层、四个前端的检查命令、迁移 head、dist 缺失断言）、`Document/development/deployment.md`（六个镜像阶段、四套入口与产物目录、head）、`Document/api/conventions.md`（权限化授权与回跳白名单）、`Document/api/rag-eval.md`（页面地址与蓝图权限）、`Document/architecture/overview.md`（桌面入口与四个前端边界）、`Document/architecture/rag-evaluation.md`、`Document/database/overview.md`（角色与权限表）、`Document/database/consistency.md`、`Document/database/migrations-checkpoints.md`（RBAC 迁移与 head）、`Document/admin/api.md`、`Document/admin/architecture.md`、`Document/admin/development.md`、`Document/admin/system-overview.md` 和 `Document/development/observability.md`（匿名访问事件），并新增 `Document/development/website-frontend.md`。
  - 【README 与局部规则】：同步中文与英文 README 的可访问入口、前端章节和目录树；`chat-frontend/AGENTS.md` 与 `chat-frontend/BASELINE.md` 登记 `/dashboard` 入口、地址来源和已删除的预览与登录面板；`windows-client/README.md` 的默认地址改为 `/dashboard`。
  - 【删除预览与登录面板】：删除 `chat-frontend/src/components/PublicPreview.vue`、`AuthPanel.vue`、`src/preview/public-preview-data.ts`、`src/runtime/analytics/analytics-client.ts` 及其组件/单元测试，移除只在预览中使用的 i18n 文案，并清理样式表中已无引用的登录面板与公开预览规则；登录与注册职责由官网前端承担。
  - 【构建产物不入库】：管理员前端产物不再作为发布文件跟踪，`.gitignore` 移除 `!admin-frontend/dist/index.html` 例外，`git rm --cached admin-frontend/dist/index.html` 停止跟踪但保留本地文件；`tests/integration/admin/test_admin_deployment.py` 的用例改为断言四个前端产物都被忽略，`Document/admin/system-overview.md` 改为说明源码变更必须重新构建、未构建时 `/admin/` 返回带 request ID 的 503。
- 【开发脚本：前端 Vite 一键启动】
  - 【新增脚本】：新增 `scripts/dev_frontends.ps1`，用 `Start-Process` 按 `-Frontends` 参数在独立窗口启动 `website-frontend/`、`chat-frontend/`、`admin-frontend/` 和 `app/rag_eval/frontend/` 的 Vite 开发服务器；端口和资源前缀从各工程的 `vite.config.ts` 读取，支持 `-Install` 先执行 `npm ci`、`-WhatIf` 只打印将要执行的操作，端口已被占用或缺少 `node_modules` 的前端会跳过并打印原因，脚本只依赖 PowerShell 内置命令和本机 npm。
  - 【文档】：`Document/development/setup.md` 新增四个前端开发服务器一键启动章节和端口表，并把 RAG 评测台的开发地址由 `http://127.0.0.1:5176/rag-eval/` 更正为 `http://localhost:5176/rag-eval/`（该工程的 `vite` 未指定 `host`，只监听 IPv6 的 `::1`）；`README.md` 与 `README_EN.md` 的前端开发章节改用这个入口，目录树补充 `scripts/` 的开发用途。
---
2026.9.20
- 【Session 多分析上下文：跨 Job 的分析事实来源】
  - 【数据模型】：新增迁移 `c9d0e1f2a3b4` 建立 `analysis_contexts` 表，保存创建时的文件快照（用户文件、对象、hash、文件名）、分析参数（target、treatment、分析问题）、最新结构化算法摘要、RAG/Web 证据摘要和最新报告引用；同时给 `sessions` 增加可空的 `active_analysis_context_id`，给 `analysis_jobs` 和 `analysis_job_inputs` 增加可空的 `analysis_context_id`。`sessions` 指向上下文的一侧不建外键，避免与 `analysis_contexts` 到 `sessions` 的级联删除形成环；上下文写入始终按 `user_id + session_id` 校验归属。
  - 【上下文生命周期】：创建 Job 时在同一事务中按冻结文件复用当前 active 上下文或新建上下文；fold 解析出 target/treatment 后回填参数，参数与已绑定上下文不一致时先复用同一文件上参数一致的历史上下文，没有才新建并切换 active 指针。分析成功才写回算法摘要、证据摘要和报告引用，失败或澄清不覆盖原有有效结果。
  - 【Agent 意图路由】：`agent_node` 改用结构化 `AgentIntentDecision`（normal_chat、start_analysis、answer_report、revise_report、rerun_analysis、switch_analysis_context、clarify），模型只能表达意图、上下文线索和澄清问题；图路由由后端按固定映射生成，模型无法输出分析上下文 ID 或用户文件 ID。原有“明确因果分析请求”的确定性分支保留，但用户点名了其他文件时不再抢路由。
  - 【上下文切换】：新增 `context_switch` 节点，按用户表述匹配同一会话的历史分析（文件名、目标变量、处理变量、报告标题、问题描述或展示序号），唯一命中才切换；歧义、文件缺失、无匹配都返回澄清问题，并且不修改 active 指针。切换在一个 MySQL 事务里更新 Session 默认指针、当前 Job 和当前输入账本的上下文绑定，并带 worker/attempt/lease fencing 校验，节点重复执行得到同一结果。
  - 【冻结输入与切换文件】：用户点名了文件库里其他文件时，切换事务会同时重写 `analysis_jobs` 的冻结文件快照（用户文件、对象、hash、文件名），即服务端主动重新冻结输入。新增 invocation 级可刷新引用 `FrozenInputRef`，使 MCP 输入摘要、适配器校验、FinalizationGate 的 provenance 校验和 Deep Agent 子图 execution scope 跟随同一次写入变化，切换后的算法调用按新文件通过 MCP 强读校验，旧上下文的算法结果、证据和因果图不进入新 State。
  - 【报告与追问】：`report_node` 增加 `full_regeneration_from_context` 模式，只使用当前上下文已确认的事实重新生成完整报告，不修改算法图；`inquiry_answer_node` 改为回答报告问题或转达后端澄清问题，既不决定是否重跑算法，也不修改报告。
- 【分析与运行事实同步】
  - 【就绪检查】：`check_database_readiness()` 增加 `analysis_contexts` 表、`sessions.active_analysis_context_id`、`analysis_jobs.analysis_context_id`、`analysis_job_inputs.analysis_context_id` 和四个上下文索引；`Database/deep_audit.py` 的期望字段、索引与外键同步覆盖新结构。
  - 【文档】：`Document/architecture/agent-runtime.md` 记录意图路由、上下文切换与报告修订模式；`Document/architecture/job-file-lifecycle.md` 记录 Session、AnalysisContext、Job、Checkpoint 的边界与切换后的重新冻结语义；`Document/api/agent-jobs.md` 记录创建 Job 的上下文绑定、澄清与切换行为；迁移 head 同步为 `c9d0e1f2a3b4`。
- 【测试补充】
  - 【Agent 与上下文】：新增 `tests/unit/agent/test_agent_intent_routing.py`、`test_context_switch_node.py` 和 `test_analysis_context_projection.py`，覆盖意图到路由的映射、结构化失败回退、报告意图在缺少报告时的降级、澄清路径不调用模型、上下文投影与匹配、冻结输入刷新；`tests/integration/migrations/test_migration_chain.py` 增加新迁移的结构、绑定字段、回滚边界与就绪检查断言。
- 【运行错误修复：节点降级结果不再被引擎异常覆盖】
  - 【worker 终态收敛】：LangGraph 在节点错误处理器提交降级结果后仍会把原任务异常抛给 `astream`，worker 现在检测 updates 流中带 `__error_handler__` 前缀的降级结果，并在确认图状态已经收敛（没有待执行节点、没有 pending interrupt）时按正常终态收尾，缺少任一条件时保持原有失败路径，避免报告节点降级后整个 Job 被判失败。
  - 【结构化输出诊断】：`StructuredOutputError` 按底层异常类名归类出稳定原因代码，节点降级日志 `job.node.degraded` 新增 `cause_code`，事件目录同步登记该字段，用于区分模型未按结构化契约返回、JSON 解析失败、上下文截断、超时、连接、限流和服务端错误，不记录异常正文。
  - 【测试补充】：新增节点错误处理器收敛与无处理器仍按失败处理两个用例，并补充结构化输出失败原因归类用例。
- 【修复：上下文绑定的幂等写入与意图输入上限】
  - 【幂等写入】：Session 默认指针的写入不再用 MySQL `rowcount` 判断越权。当指针本来就指向目标上下文时 `rowcount` 为 0，旧实现把这种正常情况误判成「会话不存在或不属于当前用户」，导致切换或重新分析在 `context_switch` 节点里抛错、降级，并最终让整个 Job 失败；归属校验保留在 `FOR UPDATE` 锁定 Session 与上下文的读取里，同时去掉重复的 `rollback`，让回滚归属唯一。
  - 【意图输入上限】：`agent` 节点的历史渲染限制单条消息 400 字符、整体 2000 字符并优先保留最近的对话，避免上一轮的长报告或长解释整段重复进入意图判断提示词。
  - 【测试补充】：新增分析上下文服务的事务语义用例（幂等写入不算越权、跨用户与跨会话上下文被拒绝、旧 lease 被 fencing）和意图历史长度上限用例。
- 【普通用户前端：报告底色与执行记录重复】
  - 【报告消息盒子】：报告正文不再自带浅灰色底色、边框和内边距，与普通聊天和追问消息共用同一条消息盒子；`.report-document` 只保留块间距和文字颜色，旧版 Markdown 报告的 `#f5f5f5` 底色与 16/20 内边距一并移除，图表和因果图块继续使用各自的白色卡片。
  - 【执行记录归属】：`MessageTimeline` 不再让所有引用同一 Job 的消息都消费同一条运行态记录：历史阶段由 `thinking_after` 静态投影；答案已经作为独立消息存进历史时不再复用运行态记录，答案消息本身也不显示执行记录；只有本页新发送的提问消息才直接消费运行态记录。加载会话时只为仍在执行的 Job 建立运行态记录，`JobRecord.phaseInputId` 记录它代表的分析输入，同一 Job 更早的输入保持静态展示，重新加载后不再重复出现任务执行记录。
  - 【追问恢复】：开始新一轮追问时把上一阶段的执行记录固定到发起它的用户消息上（`ChatMessage.frozenThinking`），运行态记录从空投影和原游标继续，同一份记录不再同时出现在两条消息下面。
  - 【测试与文档】：新增 `chat-frontend/tests/unit/phase-ownership.spec.ts` 与 `chat-frontend/tests/e2e-mock/session-history.spec.ts`，并扩充 `chat-frontend/tests/components/message-timeline.spec.ts`，覆盖阶段归属、追问固定、刷新后仍在执行、等待补充输入等场景；`Document/development/chat-frontend.md` 补充执行记录归属和报告消息盒子事实。
- 【修复：Store 工厂半构造实例启动日志】
  - 【Store 装配】：`build_async_postgres_store` 改为按官方签名直接 `AsyncPostgresStore(conn=pool)`，不再用 `pool` 关键字试错；试错会让官方 `__init__` 绑定失败并留下 `_task` 未赋值的半构造实例，回收时 `__del__` 抛 `AttributeError`，在清理 worker 启动日志里打印一次。
  - 【测试】：`tests/integration/agent/test_deep_agent_checkpoint.py` 新增用例，断言 Store 只按 `conn` 构造一次。
- 【报告来源：RAG 知识库来源接入】
  - 【来源装配】：报告来源装配同时接受 State 中的 pydantic 证据对象与等价字典，RAG 与 Web 证据不再因类型不匹配被静默丢弃；来源类别改由证据通道显式给出，知识库来源即使带可点击地址也保持 `knowledge_base`，不再按“是否存在 URL”推断。
  - 【来源身份】：知识库来源的展示名由 release manifest 的 `document_id → relative_path` 解析，解析 active release 时随 `RagRuntimeConfig.document_names` 一并投影，不新增文件读取；manifest 未覆盖时回退到检索元数据的 title/source_name，`asset_uri` 不再充当 `source_url`，避免把内部资源路径渲染成点不开的相对链接。
  - 【测试补充】：`tests/unit/agent/test_report_document.py` 覆盖证据对象与字典两种形态产出一致、知识库来源带地址仍为 knowledge_base、无法归一化的载荷被安全跳过；`tests/test_rag_service_and_tool.py` 覆盖 manifest 展示名解析、`asset_uri` 不进入 `source_url`，以及 manifest 未覆盖时的回退。
  - 【文档】：`Document/architecture/agent-runtime.md` 记录知识库来源展示名的解析来源与来源类别显式规则。
- 【分析运行强制算法结果】
  - 【按运行注入约束】：进入 Deep Agent 的运行都是分析运行（`agent` 的 `start_analysis`/`rerun_analysis` 直接路由到 `fold`，`context_switch` 也会把 `route_decision` 改写成后续节点名），因此父图投影时按运行追加一条“必须至少调用一个算法工具并在最终决策中引用算法结果”的系统约束；该约束不写进 worker 级系统提示词，同一次部署里不同 Job 的要求互不干扰。
  - 【Gate 新规则】：`FinalizationGate` 在分析路由下新增 `analysis_route_without_algorithm_result`：没有任何算法结果时不得提交 `evidence_only` 或 `no_valid_algorithm`，按既有的一次修正预算要求模型补做；算法确实返回未就绪或失败时会留下算法结果，不受该规则影响。
  - 【测试补充】：`tests/unit/agent/test_deep_agent_state.py` 覆盖分析路由注入与非分析路由不注入；`tests/unit/agent/test_final_analysis_decision.py` 覆盖分析路由缺少算法结果被拒、非分析路由放行，以及算法结果被丢弃时放行。
- 【文档】：`Document/architecture/agent-runtime.md` 记录分析运行的按运行约束与新的 Gate 规则。
- 【分析运行强制 RAG 检索】
  - 【按运行注入约束】：进入因果分析的 Deep Agent 必须至少调用一次 `rag_evidence_search`；有证据、无相关证据或知识库不可用都保留真实 terminal 状态后再提交最终决策。
  - 【Gate 校验】：`FinalizationGate` 新增 `analysis_route_without_rag_invocation`，没有当前 Job attempt 的 RAG 调用记录时拒绝终态并交回一次受控修正。
  - 【测试与文档】：补充 Deep Agent State、FinalizationGate 的强制检索合同测试，并同步 `Document/architecture/agent-runtime.md`。
- 【分析运行强制联网搜索】
  - 【按开关注入约束】：Job 的 `web_search_enabled=true` 时，Deep Agent 按运行收到“至少调用一次 `web_evidence_search`”的系统约束；关闭时不注入该要求，工具仍由 runtime 开关阻断真实触网。
  - 【Gate 校验】：`FinalizationGate` 新增 `analysis_route_without_web_invocation`，开启联网搜索但没有当前 Job attempt 的 Web terminal 调用时拒绝终态并交回一次受控修正；无结果或暂不可用仍保留真实 terminal 状态。
  - 【测试与文档】：补充 Web 开关的 State 投影、父图上下文传递和 Gate 合同测试，并同步 `Document/architecture/agent-runtime.md`。

---
2026.9.21
- 【报告来源正文定位】：报告装配时把正文中出现且属于当前证据清单的 `ev_...` ID 回填到 `markdown.evidence_refs`，前端兼容历史报告中的同类漏填数据，恢复来源证据到正文块的“定位正文”按钮和高亮跳转。
- 【分析运行工具选择调整】：取消“分析运行必须至少调用一次 `rag_evidence_search`”的运行级提示与 `FinalizationGate` 门禁，知识库检索恢复为 Agent 自主决策；至少一个因果算法调用和开启联网搜索时至少一次 `web_evidence_search` 的约束保持不变。
- 【报告来源展示】：知识库来源的长证据引用默认收起，来源标题旁新增展开/收起按钮；文件来源与联网来源保持原有展示方式。
- 【报告引用控件调整】：撤回带边框和“引用 N 条”文字的展开控件，改为来源标题旁的纯折叠箭头；引用数量不再占用收起状态的展示空间。
- 【Agent 报告节点容错】
  - 【结构化输出分类】：统一入口新增 `tool_call_invalid` 原因代码，模型没有返回任何可解析工具调用时不再被误报成 `schema_invalid`；`NoToolCallError` 在结构化结果为空时抛出，并按类名归类。
  - 【有界重试】：`Agent/llm_structured_output.py` 对「模型本次产出的结构化结果不可用」的失败（`tool_call_invalid`、`schema_invalid`、`json_invalid`、`output_parser_error`）重试一次，第二次调用前重新确认 Job 执行资格；模型自身异常、超时、限流、连接和供应商错误不在入口内重试，撤销控制流不再被包装成结构化输出失败。
  - 【降级日志】：`job.node.degraded` 新增 `schema_name`、`structured_attempts`、`validation_error_count`、`validation_first_type` 和 `validation_first_loc`，字段路径只保留 schema 字段名、额外字段统一写成 extra，不记录模型取值、提示词和异常正文；事件目录同步登记这些字段，并修正观测文档中该事件遗漏的 `cause_code`。
  - 【报告节点流式边界】：报告节点改用非流式模型生成报告草稿，其空闲超时从 60 秒放宽到 120 秒，以容纳只在调用开始和结束时刷新计时的非流式长响应；普通问答和报告追问继续使用流式模型。
  - 【测试补充】：`tests/unit/agent/test_structured_output_runtime.py` 覆盖原因分类、单次重试、重试成功恢复、模型异常不重试和撤销时停止重试；`tests/unit/agent/test_execution_guard.py` 覆盖降级日志新字段与脱敏边界；`tests/unit/agent/test_graph_llm_streaming_scope.py` 约束报告节点绑定非流式模型与新的空闲超时。
  - 【文档】：`Document/architecture/agent-runtime.md`、`Document/development/observability.md` 和 `Document/api/agent-jobs.md` 同步原因代码、重试约定、日志字段和报告节点的文字增量边界。
- 【CDFM 工具描述改为选择依据】
  - 【描述改写】：`CDFM_SPEC.description` 不再只写“零样本推断 + 免责声明”，改为说明单次调用返回整张候选有向图、同一个 checkpoint 覆盖线性与非线性、异方差、有序或离散观测、测量误差和潜在混杂等多类机制，因此机制未知或明显非线性时比依赖单一假设的工具更合适，也可与 PC、DirectLiNGAM 的结果并行对照；同时写明不传 `threshold` 时由服务端按模型自带校准器自动决定判决边界、缺失值按 NaN/±Inf 掩码传递、当前只接受连续数值列，以及输出只有边及其方向，方向可能随阈值变化、不可识别场景下方向证据不足、两条相反方向的边也可能对应潜在混杂。
  - 【参数与元数据】：`CausalCdfmInput.threshold` 增加字段说明，模型在 schema 中即可读到自动校准语义；`description_source` 改为覆盖机制覆盖、阈值与自动校准和结果解读，`assumptions` 增补“零样本识别以预训练机制覆盖当前数据分布为前提”。
  - 【文档与快照】：`README.md` 与 `README_EN.md` 的 CDFM 条目同步为新描述；`Document/architecture/agent-runtime.md` 补充对称邻接对应潜在混杂的读法；`tests/unit/agent/snapshots/` 的 Tool schema 固定快照按新描述与新的 `spec_digest` 重新生成。
- 【runner 诊断映射统一】
  - 【共享映射】：新增 `build_diagnostics_from_runner_payload()`，把 runner 的 `n_samples`、`n_features` 和其余标量诊断统一映射为 `sample_count`、`variable_count` 与受限 `metrics`；worker 侧的 `result_from_runner_payload()` 与 causal-mcp 的 `service` 改用它，删掉服务端本地的重复实现，避免一侧白名单与 runner 键名不一致时诊断被静默丢弃。
  - 【文档】：`Document/architecture/mcp-runtime.md` 记录 runner 诊断的共享映射函数，并明确两侧不得各写一套键名白名单。
  - 【测试补充】：`tests/unit/agent/test_algorithm_adapters.py` 新增 legacy runner payload 的诊断映射用例；`tests/unit/agent/test_mcp_v2_contract.py` 的成功路径断言补充 `sample_count`、`variable_count` 与 `metrics` 映射结果。
- 【前端设计系统：设计文档与共享组件包】
  - 【文档体系】：新增 `Document/design-system/`，包含视觉方向决策（design-decision.md）、token 清单与原型变量映射（tokens.md）、共享组件契约（components.md）和四个前端改造前的样式现状与迁移顺序（current-state.md）；`Document/README.md` 增加导航，`Document/documentation.md` 增加主题归属，根 `AGENTS.md` 增加局部规则入口，新增 `packages/design-system/AGENTS.md`。
  - 【视觉基础】：`packages/design-system/src/styles/` 从官网原型页面提取纸白到墨黑的灰阶、单字重字体栈、字号与行高、间距与阅读宽度、四档圆角、三层投影和动效时长与缓动，按基础取值与语义取值两层组织；颜色字面值只出现在 colors.css，声明放在 `@layer ca-tokens` 中，业务页面可以覆盖同名变量。
  - 【品牌基础与字体】：品牌基础只包含字体、字重、焦点环、文字链接和减少动态规则，不重置外边距、不改变布局结构，因此可以按前端逐个页面引入；随包提供 Geist Sans 400 拉丁字形，中文回退系统无衬线。
  - 【共享组件】：`packages/design-system/src/components/` 提供 CaButton、CaCard、CaBadge、CaPageHeader、CaTabs、CaInput、CaEmptyState、CaLoadingState 和 CaErrorState 九个基础组件，只开放受控变体，不接受自定义颜色、圆角或投影；标签页自带方向键与首尾跳转，输入控件自带标签绑定和错误描述关联。
  - 【自检与开发入口】：`packages/design-system` 是独立 npm 工程，`npm run check` 执行类型检查和组件行为测试（变体类名、禁用与加载状态、键盘操作、无障碍关联和加载进度声明），`.npmrc` 固定 `legacy-peer-deps`，`node_modules` 进入忽略规则；`Document/development/setup.md` 与 `testing.md` 增加该包的开发入口和验证矩阵条目。四个前端尚未接入共享包。
---
2026.9.23
- 【普通用户聊天工作区视觉重建】
  - 【工作区布局】：将普通用户工作区调整为原型中的固定会话侧栏、会话与文件分区、账户区和居中的消息输入布局；窄屏下侧栏改为带遮罩的抽屉。
  - 【侧边栏宽度与收起】：展开宽度从 264px 调整为 280px；新增桌面收起与展开操作，改用单一宽度过渡并交叉淡入淡出侧栏内容和快捷栏；微调展开箭头的位置，使它与上方品牌标志、下方新建图标的间距更均匀，其他快捷图标位置不变，窄屏抽屉行为保持不变。
  - 【已发送文件附件】：文件发送后在对应用户消息上方展示文件名；历史加载按用户消息、初始输入账本和 Job 快照的关联恢复附件，不返回文件内容或内部文件标识。
  - 【侧栏、字体与输入控件】：会话行固定高度并禁止 flex 收缩，新建会话和设置入口使用原型中的圆角及图标；加载 Geist Sans 400 拉丁字形，将联网搜索、上传和发送控件改为原型图标样式，保留现有开关和上传行为。
  - 【页面与处理动效】：移除中央文字水印，按原型节点和边绘制浅色因果网络背景与流动粒子；活动任务显示不定长进度线，并遵守减少动态偏好。
  - 【报告排版】：报告正文统一占满正文宽度并在报告边缘换行，移除段落 62ch 限宽；报告表格横向占满正文宽度并在窄屏下于正文区域滚动，表格使用轻量横向分隔线；相关矩阵按绝对值变化灰阶并显示数值，条状图按各条频数相对最大值的比例调整灰阶深浅，引用定位改用中性灰底。
  - 【现有功能】：保留当前会话地址映射、历史加载与重命名删除、文件上传与选择、权限控制入口、联网搜索开关、消息发送、任务取消和设置内容加载行为；删除前继续要求用户确认，并将确认操作放入应用内对话框。
  - 【共享品牌字体】：设计系统以 Geist Sans 400 绘制拉丁字形，以随前端发布的 Noto Sans SC 400 绘制中文与 CJK 标点；通过 unicode-range 阻止 Geist 窄全角标点抢占中文排版，四个前端统一使用 --ca-font-sans，并保留两种字体的 OFL 授权文件。
  - 【发布构建与说明】：Docker 的四个前端构建阶段纳入共享字体源码；Vite 将字体 OFL 文本复制到前端产物的 font-licenses 目录，镜像构建上下文保留许可文件并排除设计系统 node_modules；同步修正四个前端测试命令的实际覆盖范围。

---
2026.9.22
- 【RAG评测台前端：接入共享设计系统】
  - 【构建接入】：为 `app/rag_eval/frontend/` 增加共享设计系统源码别名、样式入口和 TypeScript 路径映射，生产构建同时带出 Geist Sans 字体资源。
  - 【页面迁移】：页面标题、后端连接状态和报告标签页改用共享组件；保留评测工作流与高信息密度布局，将纸面/墨色、token、圆角、描边、焦点和动效规则收敛到共享视觉方向。
  - 【Docker 构建链】：RAG builder 复制 `packages/design-system/` 源码并排除其本机 `node_modules`，确保共享样式能进入镜像且不携带本地依赖；部署契约同步覆盖该复制边界。
  - 【组件级迁移】：评测台实际使用 `CaButton`、`CaCard`、`CaBadge`、`CaPageHeader`、`CaTabs`、`CaInput`、`CaEmptyState`、`CaLoadingState` 和 `CaErrorState`，并新增页面质量层统一密度、焦点、悬停和进度表达。

---
2026.9.23
- 【RAG 评测台：尺寸与长文本适配】
  - 【长标识换行】：索引版本、来源身份、样本编号和文件元数据在容器内断行显示，避免内容撑宽整页；筛选选择框限制在面板宽度内，表格仅在自己的滚动区域横向浏览。
  - 【收敛卡片高度】：移除工作台、评测区和报告区按视口设置的空白最小高度，让面板跟随真实内容；评测事件无数据时不再保留大块空白。
  - 【窄屏工作流】：步骤条改为三列网格，候选集面板头改用真实网格并在窄屏堆叠，页脚版本号可断行。
  - 【导航与报告列表组件化】：侧边导航与报告历史项改用 CausalAgent `CaButton`，导航选中态改为白底墨色侧标，报告历史改为细分隔线列表和墨色选中标记，保留键盘焦点提示。
  - 【全局工作索引控件】：将选择器标题改为“全局工作索引”，移除说明行，删除操作收敛为与选择框等高、居中显示的垃圾桶图标按钮。
  - 【评测配置对齐】：正式回答 evidence 压缩控件移入右侧参数列并缩短选择栏；子导航文字增加左内边距，避免与墨色选中标记重叠。
  - 【索引与卡片间距】：索引控件左缘与多模态说明文字对齐，收起态截断过长索引名但保留完整下拉选项；策略配置和候选发布卡增加底部留白。
  - 【连接状态徽标】：将状态点固定在徽标左侧并单独定位，使“后端接口已连接”在灰色背景中水平居中。
  - 【删除按钮与元信息间距】：索引删除按钮改用来源删除按钮的红色描边与图标色，并将右缘对齐来源操作列；候选发布卡元信息行增加顶部间距。

---
2026.9.24
- 【RAG 评测台前端：统一品牌字体落地】
  - 【字体渲染】：工作台构建产物切换到统一品牌字体栈 `--ca-font-sans`（Geist Sans 400 绘制拉丁与数字、随包 Noto Sans SC 400 绘制中文与 CJK 标点、系统字体最后回退），正文、页面质量层与共享迁移样式一致生效。
  - 【产物重建】：重建 `app/rag_eval/frontend_dist/`，随产物携带字体子集与 OFL 授权文件（`assets/font-licenses/`），入口改引新的内容哈希资源并移除旧构建产物。

---
2026.9.24
- 【RAG 评测台前端：中文标题层级收敛】
  - 【标题文案】：移除评测集、评测配置、评测进度、评测对比、正式发布和报告工作区等卡片中文标题上方重复的英文眉标题，保留中文语义和必要的其他状态标签。
  - 【标题布局】：收紧标题组内部间距，增加中文标题的自然换行和最小宽度约束，避免英文副标题挤压中文或在窄屏下造成不协调断行。
  - 【标题覆盖补齐】：同步移除知识源、统一评测流程和报告历史卡片中遗漏的 `SOURCE INPUT`、`NEXT STEP` 和 `REPORT HISTORY` 英文眉标题。
- 【品牌图标资源与网页入口】
  - 【矢量与位图资源】：新增 CausalAgent 无文字标志的透明 SVG、白底实体 SVG、maskable SVG，以及 1024 像素透明、实体和 maskable PNG；同步生成 favicon、Apple 主屏幕图标、PWA 图标和 Web App Manifest 资源。
  - 【前端资源目录】：将品牌资源保留在共享设计系统和官网、普通用户应用、管理员端、RAG 评测台的前端资源目录中，保留原有 PNG 参考文件。
  - 【入口接入】：四个网页入口增加 favicon、Apple 主屏幕图标和主题色；三个应用入口增加 Web App Manifest 引用。
---
2026.9.25
- 【管理员后台设计统一】
  - 【共享组件接入】：管理员后台 8 个页面、应用外壳和 4 个局部组件接入共享设计系统；Element Plus 复杂控件使用共享 token 适配。
  - 【品牌标识】：侧栏和移动端改用共享 CausalAgent 图标，展开侧栏显示品牌名与“管理后台”，移除旧 logo 裁切显示。
  - 【视觉收尾】：统一次要说明文字、普通错误内容入口及局部加载进度表现，保留危险操作的警示样式。
  - 【构建配置】：管理员 Vite 同时接入共享组件别名和字体许可证插件，保持字体许可文本进入构建产物。
  - 【品牌资源托管】：新增管理员授权保护的品牌资源路由，为后台网页提供新 favicon、应用图标和 manifest。
---
2026.9.28
- 【官网正式页面重建】
  - 【页面与路由】：官网正式页面固定为 `/`、`/product`、`/pricing`、`/about`、`/docs`、`/changelog`、`/auth/sign-in`、`/auth/sign-up`，Flask 官网路由与部署入口契约同步登记 `/pricing`；导航、页脚和页面索引移除原型中的“专家智囊”“应用原型”等不存在页面，`/dashboard`、`/rag-eval`、`/admin` 只作为真实系统入口链接。
  - 【设计系统接入】：官网入口改为引入 `packages/design-system` 的完整样式与共享组件，Vite 与 TypeScript 增加 `@causalagent/design-system` 别名；字体、颜色、圆角、描边、投影和减少动态规则与共享包保持一致，官网专属动效保留在官网内。
  - 【公共壳层】：新增统一黄色通告条、站点页头与页脚；通告内容来自单一数据源，关闭状态只保存在浏览器本地，导航与页脚按当前路径高亮。
  - 【移动端提示】：宽度低于断点时显示统一的桌面端访问提示页，不再压缩渲染桌面布局。
  - 【首页展示区】：首屏保留滚动交接结构，展示区继续使用可点击的结论、因果图、指标与联网检索示意，并把演示内容统一标注为结构示意；核心能力、运行指标、分析流程和算法库改为类型化内容模块驱动。
  - 【产品与定价】：产品页按 CausalAgent、CDFM、DMIR 算法库、RAG 测评端、管理员端五个入口组织，提供入口切换、作用说明、访问地址与权限边界；定价页只公开 FREE 与 FUTURE 两个已确认方案，不编造价格与权益。
  - 【文档与更新日志】：公开文档按概览、快速开始、工作区、材料与文件、任务执行与过程、结果与报告、算法说明、账号与权限、常见问题重写并支持关键词过滤；官网公开更新日志提供版本索引，顶部公告指向当前版本。
  - 【静态素材】：官网发布应用工作区截图与 CDFM 基准图两张静态素材，RAG 测评端与管理员端改为说明面板呈现作用与权限边界。
- 【部署与界面截图补充】
  - 【镜像重建】：按当前源码重建 app 与 worker 镜像并启动完整开发拓扑；官网构建阶段补齐 `packages/design-system` 的依赖链接，使官网新增的设计系统依赖能在镜像内解析。
  - 【RAG 测评台品牌标志】：侧栏标志改用设计系统提供的 CausalAgent 品牌图，替换原来的通用柱状图图标；品牌图以内联资源进入构建产物，不依赖额外请求。
  - 【RAG 品牌资源路由】：新增 `/rag-eval/brand/` 路由，沿用页面与 `assets/` 相同的权限边界，修复 favicon 与应用清单此前返回 404 的问题。
  - 【产品页截图】：在产品矩阵的 RAG 测评端与管理端模块接入登录后采集的真实界面截图，RAG 截图不含知识源清单与内容哈希，管理员截图只保留聚合指标，避免公开内部数据。
  - 【文档同步】：部署文档补充 `/rag-eval/brand/` 与 `/admin/brand/` 两个资源前缀；部署入口契约测试增加 RAG 品牌资源的未登录跳转与授权读取断言。
- 【官网按原型逐页重建】
  - 【页面移植】：六个公开页面（`/`、`/product`、`/pricing`、`/about`、`/docs`、`/changelog`）改为逐字沿用官网原型的结构、样式与交互；每个页面组件承载原型对应页面的完整标记，原型的内联样式与内联脚本分别转写到 `src/styles/pages/<page>.css` 与 `src/scripts/<page>.js`，脚本导出初始化函数由页面挂载后调用。
  - 【路由与页面选择】：`src/App.vue` 按 `location.pathname` 异步引入对应页面组件，整页加载时同一个文档只解析一个页面；未登记的公开地址仍由 `main_bp` 返回 404，构建产物内的 `not-found` 页面只作为兜底。
  - 【文档元信息】：新增 `SITE_PAGE_META`，按地址写入各页原型 `head` 中的标题与说明。
  - 【认证页面】：`/auth/sign-in` 与 `/auth/sign-up` 改用原型 `auth/` 的结构与样式，保留真实接口调用、服务端回跳白名单与 `?notice=admin_required` 提示；字段校验、密码显隐、密码规则与提交状态沿用原型脚本，认证页共用 `src/scripts/causal-map.js` 的三维因果图动效。
  - 【静态素材】：原型引用的标志、专家照片、研究论文页面、基准图与团队图发布到 `public/assets/` 与 `public/`，通过 `/site-assets/` 同源提供。
  - 【滚动动效依赖】：原型脚本以 `window.gsap` 与 `window.ScrollTrigger` 使用 GSAP，但原型目录导出的 vendor 文件缺失；新增 `gsap@3.15.0` 依赖与 `src/scripts/vendor-gsap.js`，由首页与更新日志页挂载时按原型的全局约定挂载，恢复标题拆字、滚动渐入与数字滚动动效。
  - 【样式入口】：官网入口改为引入共享设计系统的品牌字体与基础规则，加上站点自身的 `src/styles/base.css`；原型的页面级样式在共享基础之外生效，页面排版保持原型取值。
  - 【单元测试】：官网网页内容契约测试改为校验页面元信息、页面引用的发布素材是否都已发布、以及页面内站内链接是否指向已登记路径或真实系统入口。
  - 【文档同步】：`Document/development/website-frontend.md` 与 `Document/design-system/current-state.md` 同步记录原型移植方式与官网样式入口的当前事实。

---
2026.9.29
- 【官网图标、真实界面截图与素材清理】
  - 【品牌标志】：六个公开页面页头与登录注册页顶栏的文字字形改为仓库品牌图 `public/brand/causalagent-mark.svg`，`img` 以 28px 与 34px 两档等比显示、`object-fit: contain` 不裁切；同步移除各页画文字圆形的旧样式。
  - 【真实界面截图】：首页“隐私、保护与RAG管理”轮播的三张产品截图与产品页五处配图占位替换为登录后采集的真实界面截图，统一 1600×1000（16:10）、顶部对齐裁切；对应素材为工作台 `workspace-console.png`、管理员端 `admin-console.png`、RAG 测评台 `rag-eval-console.png` 与日志仪表盘 `observability-logs.png`。
  - 【截图边界】：四张截图只保留聚合指标、配置项与日志状态，不含知识源清单、内容哈希或用户文件内容；配置与评测内容以“当前配置”形式呈现，不承载真实评测数据。
  - 【配图说明】：产品页五处配图的说明改为描述各自界面，不再声明“截图位置已预留”。
  - 【无用源码移除】：删除已被原型页面取代且不再被引用的 `src/components/` 壳层组件、`src/content/` 类型化内容模块、`src/assets.ts` 与 `src/styles/site.css`，并移除不再使用的 `SITE_NAV` 导出。
  - 【无用素材移除】：删除页面从未引用的发布素材，包括三张被真实截图取代的旧截图、原型自带字体、与既有文件重复的 `xu-baiyan.jpg`、以及仅作为原件的 PDF 与图片。
  - 【临时产物清理】：清空 `output/` 下的一次性对照与验收截图（含此前提交的两张字体对比图），并在 `.gitignore` 增加 `/output/`，此后本地截图不再进入版本库。
  - 【官网交互与导航】：移除首页滚动停手后的章节自动吸附，统一各页面主导航为首页、产品、文档、更新日志和关于，统一页脚导航并移除旧的“应用原型”“专家智囊”选项；更新日志页面的历史版本说明，修正页脚文字为“即刻提出”。
  - 【浏览器图标】：入口页面改用透明背景的 `causalagent-mark-transparent.png` 作为 favicon 与触摸图标，去除浏览器标签图标的白色背景。
  - 【浏览器图标优化】：改用黑色标志配白色圆角底板的 `causalagent-mark-rounded.png`，四角保持透明，提升浅色浏览器标签栏中的可辨识度。
  - 【官网导航补充】：将“价格”加入所有公开页面的顶部主导航和页脚官网导航，保持价格页与其他官网页面的导航一致。
  - 【首页因果地球动效】：将首屏原有的环绕因果图改为缓慢自转的球面点阵，呈现变量之间的有向关系、周期性干预与影响传播，并在减少动态设置下显示静止图形；动效沿用官网灰阶风格，只替换首页 Canvas 绘制逻辑。

---
2026.9.23
- 【数据集线性度信号接入 Deep Agent】
  - 【指标】：新增 `Agent/Processing/nonlinearity.py`，`measure_nonlinearity()` 按 `S = mean(η²−r²)`（`η²` 为按秩分 10 等量箱后的组间方差占比）衡量变量间非线性，噪声上限 `null_p99` 由各列独立打乱重算 1000 次取 99 分位，`ratio = S / null_p99 ≥ 1` 判为非线性主导。阈值比较落在代码里（`verdict` 字段），`ratio < 1` 只表示未检出超过噪声的非线性结构、不等于线性。候选列取 `continuous` 且非 `possible_id`，列数上限 12、行数钳在 1000（`null_p99` 随 n 骤降，必须按数据集现算），列不足或行少于 100 判 `insufficient`。
  - 【载荷】：写入 `analysis_parameters["nonlinearity"]` 的只有 `{ratio, verdict, n_vars, n_rows, sampled, reason}`；`S` 与 `null_p99` 只对人有价值，不进 prompt。
  - 【故障边界与注入】：`measure_nonlinearity` 内部全兜、不抛异常，调用点放在 `Agent/causal_agent/nodes.py` 的 `fold_node` 里 `get_data_summary` 之后、且在该 `try` 块之外——该块异常分支会走 `interrupt()` 挂起等用户输入。结果随 `analysis_parameters` 自动流向预处理报告与 Deep Agent 子图两处消费端。
  - 【消费端 prompt】：预处理报告任务清单加第 5 条「线性/非线性说明」；`MANDATORY_ALGORITHM_INSTRUCTION` 追加一句解释 `ratio` 含义。刻意不点具体算法名，算法仍由模型从既有 spec 自主选择，以免把自主决策变成照令执行、使后续召回提升无法归因。
  - 【preprocess_summary 送达 Deep Agent】：此前预处理报告跑在 Deep Agent 之前却从不进入它，现由 `to_deep_agent_input` 把父图的 `preprocess_summary` 显式写进子图那条 system message 的 JSON；报告不投影进子图 state（无代码消费它，投影只多占 checkpoint），`ParentStateUpdate` / `from_deep_agent_output` 的回写白名单不动。
  - 【测试与验证】：新增 `tests/unit/agent/test_nonlinearity.py`，断言本体在模块自检 `_self_check()` 里（`python -m Agent.Processing.nonlinearity` 可直跑）；`test_deep_agent_state.py` 新增用例锁住 `nonlinearity` 与 `preprocess_summary` 出现在那条 system message 里。25 个 d5 数据集复现归档倍率最大偏差 0.0049，λ=0 误报 0/5、λ=1.0 漏报 0/5，`pytest tests/unit` 全绿。
  - 【已知边界】：序数程度而非刻度，同一强度 p=5 读 3.29~12.27、p=12 掉一半，故只保留 `ratio = 1` 一条判据线、不做强度分档。

---
2026.9.29
- 【用户认证：新注册账号默认权限修复】
  - 【默认角色关系】：注册用户时在同一数据库事务内写入 `user` 角色关系，使新账号按既有角色权限获得普通工作区访问能力，无需提升为管理员。
  - 【遗留账号修复】：新增数据迁移，为缺少关系的历史账号补齐默认 `user` 角色，并保持兼容字段为 `admin` 的账号拥有管理员关系。
  - 【回归覆盖】：新增注册事务与迁移链回归用例，锁定用户创建、默认角色写入和事务提交顺序以及无损回填边界。
- 【预发部署与镜像发布】：新增独立的预发 Compose、搜索与可观测性服务、隔离网络和持久化卷。
  - 【GHCR 镜像】：新增仅允许从 develop 手动触发的镜像发布流程，按 commit 生成 sha 标签并推送 app、MCP、MySQL 主库和从库镜像，同时输出不可变 digest。
  - 【部署配置】：新增不含真实密钥的 .env.staging.example，规划服务器专用 .env.staging，并限制公网入口为 gateway。
  - 【CI 合同】：增加 Compose、Dockerfile、预发部署和 Alloy 配置检查，镜像直接推送 GHCR，不通过 SSH 部署或导出镜像 artifact。
  - 【搜索密钥注入】：开发与预发统一提交非密钥 settings.yml，删除 searxng-init 初始化容器、init_settings.sh 与对应的 init 单元测试；SEARXNG_SECRET 与其他密钥一样由环境变量注入，配置目录只读挂载。
  - 【搜索与观测数据】：Grafana、Loki 和 Alloy 数据统一使用 staging 命名卷。
  - 【预发参数对齐】：预发应用服务的并发、超时、连接池、监控、RAG 评测和 LangSmith 配置与 .env.example 取值一致。
  - 【Grafana 同源入口】：Grafana 统一以 /grafana/ 为子路径；预发由 gateway 反代到同源路径且不再映射宿主端口，开发保留本机映射端口。管理员侧栏地址改由后端 /api/admin/runtime-config 下发 GRAFANA_PUBLIC_URL，不再硬编码 127.0.0.1:3000。
- 【管理员前端品牌资源】：管理员侧栏 Logo 改为使用受保护的 /api/admin/brand/logo 接口，并补充可访问名称。

---
2026.9.30
- 【汇报演示】：新增 CausalAgent 蓝色学术风 HTML 汇报 Demo，包含首页研究定位页与系统架构页，支持键盘和页码按钮翻页。
  - 【首页】：展示自然语言问题、因果推理、证据约束与结构化报告之间的研究闭环。
  - 【架构页】：展示 Web、Agent worker、LangGraph / Deep Agent、因果工具、RAG / Web evidence、MySQL 与 PostgreSQL 的当前协作边界。
- 【审查修复】：补充预发网关对 Grafana 的启动依赖、CI 的 YAML 测试依赖和管理员 API 的 request ID；修正 SearXNG 密钥校验误判，并让 Grafana 运行期配置失败不阻塞管理员页面。

---
2026.10.6
- 【仓库归属迁移到组织】把仓库从个人账号转移到 CausalAgent-team 组织后，同步仓库内指向旧地址的引用。
  - 【README】：中英文 README 的 clone 地址与 Star History 图表来源改为组织地址，图表在新地址下仍返回有效图像。
  - 【生产 Compose】：首次部署注释中的 clone 地址改为组织地址。
  - 【管理员文档】：管理员系统总览引用的历史 PR 链接改为组织地址。
  - 【预发镜像合同测试】：预发 Compose 契约不再断言固定组织名，改为校验 ghcr.io 下任意拥有者的摘要锁定镜像，使组织改名不会导致测试失败。
- 【组织改名兼容】：镜像发布流程继续从运行环境解析拥有者，仓库内不再出现写死的组织名。
- 【CI 存量失败修复】：修复 lightweight-ci 中三个长期失败的任务。
  - 【Light tests】：该任务只安装 pytest，但被测模块 Agent/llm_structured_output.py 在顶层导入了 langchain_openai 和 pydantic。ChatOpenAI 仅用于类型标注，改为 TYPE_CHECKING 下延迟导入；pydantic 是运行时真实依赖，在安装步骤中显式安装锁定版本。
  - 【前端检查】：admin-frontend 与 app/rag_eval/frontend 的类型检查会解析 packages/design-system 的源码组件，而 CI 只在各自目录安装依赖。矩阵增加 design_system 标记，命中时先安装设计系统依赖，并在 npm 缓存路径中登记其 lock 文件。
- 【组织改名同步】组织名由 CausalAgent-team 改为 ArcheCausal 后，同步仓库内指向旧组织名的引用。
  - 【README】：中英文 README 的 clone 地址与 Star History 图表来源改为 ArcheCausal 地址。
  - 【生产 Compose】：首次部署注释中的 clone 地址改为 ArcheCausal 地址。
  - 【管理员文档】：管理员系统总览引用的历史 PR 链接改为 ArcheCausal 地址。
  - 【预发镜像】：预发环境变量模板中的 GHCR 镜像前缀改为 ghcr.io/archecausal。镜像发布流程继续从运行环境解析拥有者，镜像合同测试不校验固定拥有者，两者都不需要跟着改。
- 【CI 分层与缓存】：把 lightweight-ci 的验证门禁收敛到 Pull Request，并让 Docker unit 测试镜像跨运行复用层缓存。
  - 【触发边界】：push 到 main 或 develop 不再运行门禁，只保留一个不产生检查结果的缓存预热任务；合并 Pull Request 后自动写入层缓存，供后续 Pull Request 只读恢复。Pull Request 提交仍取消同分支旧运行，push 预热则不被取消，避免写入前被中断。
  - 【按模块执行】：新增 .github/filters/paths.yaml，用 backend、frontends、contracts 三个过滤器按改动路径选择执行范围；面向 main 的 Pull Request 无条件运行全部检查，python-syntax、light-tests 与 pull-request-policy 始终执行。
  - 【层缓存】：后端 unit 的镜像构建改用 docker buildx bake 并叠加 type=gha 层缓存，测试运行仍走原有的 docker compose run，隔离契约（network_mode: none、只读挂载、env_file）不变；docker-compose.test.yml 保持本地与 CI 共用，不含仅 CI 可用的缓存参数。

---
2026.10.7
- 【Windows Developer Preview 发布】：将 tag 推送触发改为 GitHub Actions workflow_dispatch 手动启动，保留制品构建、Draft Release 创建和已发布附件补齐流程。
