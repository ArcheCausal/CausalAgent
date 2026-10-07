# RAG 评测与 release 架构

文档职责：定义隔离 RAG 评测从知识源、摄取、staged index、数据集、评测运行到正式 release 和 worker 的业务架构。

适用范围：说明当前 `/rag-eval` 评测台及其后端任务边界、身份绑定与权限、发布门禁和运行生命周期；逐路由的请求与响应格式见 [`api/rag-eval.md`](../api/rag-eval.md)。

> 本文只记录当前实现已经提供的架构事实。路径、默认值和接口行为以 `app/rag_eval/`、`Agent/knowledge_base/multimodal/`、配置文件和迁移为准；历史演练、实验计划和旧接口清单不再作为独立权威文档维护。

## 1. 业务定位与边界

RAG 评测是一个与生产聊天链路隔离的长任务工作流。它允许用户选择或上传知识源，生成只属于本次运行的 staged index，在该索引上执行真实检索、回答生成、Ragas 和报告导出；只有显式通过发布门禁并确认，staged 产物才可能进入正式 release。

```text
来源目录/上传来源
        │ 显式选源、可选页范围、可选远程授权
        ▼
隔离 ingestion run ──► run-local assets + staged index
        │                         │
        │                         ├──► staged RAG 试跑
        │                         ├──► 候选题集/审核/Gold
        │                         └──► evaluation run ──► 报告与门禁证据
        │                                                  │
        └──────────────────────────────────────────────────┘
                                                           │ 显式 gate + confirm
                                                           ▼
                                    formal candidate ──► active/previous release
                                                           │
                                                           ▼
                                  production Agent worker drain + restart
```

以下边界是架构不变量：

- 对于长耗时任务，Web 进程负责请求校验、创建/取消任务、状态与结果查询以及 SSE；来源、数据集、profile 管理和 release 控制接口仍由 API 进程同步处理。摄取、候选题生成、RAG 试跑、评测和治理的实际执行由独立 `rag-eval-worker` 完成。
- 隔离运行不读取或写入旧的 `Agent/knowledge_base/db/` 生产/医疗兼容向量库，也不直接切换正式 `active_index.json`。
- 上传只登记来源，不自动摄取、不自动发起远程视觉请求；`upload_` 来源只具备隔离评测资格，不能直接满足正式 release 的受控来源门禁。评测完成也不等于 release，release 完成也不等于已经被运行中的生产 worker 使用。
- release 与 retrieval policy 是两个独立发布面：profile 发布只更新正式 retrieval 策略快照；索引发布才更新 active index pointer。

## 2. 核心对象与身份绑定

### 2.1 来源

来源分为两类：

- `frozen`：正式受控目录中的冻结来源，source identity 由内容哈希和正式配置共同确认。
- `uploaded`：工作台登记的用户来源，只具备隔离评测资格。正式 release 的 `controlled_source_identity` 门禁仍要求受控正式来源。

来源目录向 HTTP 客户端暴露摘要，不暴露宿主机路径。记录至少包含 `source_id`、`name`、`display_name`、`size_bytes`、`content_sha256`、`source_kind` 和可用时的 `page_count`。显示名保存在来源目录的 `source_metadata.json`，不改变 source ID、内容哈希或历史运行。

默认上传目录是 `tmp/rag_eval/sources/`，可由 `RAG_EVAL_ROOT` 或 `RAG_EVAL_SOURCE_ROOT` 覆盖；单文件默认上限为 20 MiB。当前解析器支持：`.pdf`、`.txt`、`.md`、`.markdown`、`.csv`、`.xlsx`、`.png`、`.jpg`、`.jpeg`、`.webp`、`.tif`、`.tiff`。

删除来源只允许 `upload_` 来源。若有引用该来源的活动摄取（`created`、`queued`、`running`、`cancelling`），删除会被阻断；删除来源不会删除已经生成的 run、staged index、评测或报告产物。固定来源不能通过上传来源接口删除。

### 2.2 隔离摄取运行

摄取请求必须明确给出 `source_ids` 或内联 `sources` 之一，最多 20 个来源。PDF 可以按每个来源指定 1-based、首尾包含的 `page_ranges`；`max_pages` 是按选中来源顺序累计的总页数上限，二者不能同时使用。

内联 `sources[].uri` 表示服务端本地文件或目录路径，仅适用于受信内部调用；该字段不会下载 HTTP URL，也不由摄取接口自动限制在来源目录内。外部调用应先上传来源或使用 `source_ids`；生产部署必须由外层鉴权并增加允许根目录校验。

若请求启用远程 VLM，必须同时满足：

1. 请求设置 `allow_remote_data=true`；
2. `authorized_source_ids` 非空且只包含本次选中的来源；
3. 服务端 `VISION_ALLOW_REMOTE_DATA` 已开启。

满足后，摄取层按本次来源和解析产物生成 run-scoped `outbound_manifest.json`。manifest、来源哈希、资源哈希和解析上下文共同限制可外发内容；任一授权、身份或策略不匹配都不能当作成功摄取。

默认隔离根目录是 `tmp/rag_eval/`：

```text
tmp/rag_eval/
├── sources/                  # 上传来源与 source_metadata.json
├── runs/<run_id>/             # 每次运行的状态、隔离索引、资源和评测产物
├── datasets/registered/       # 不可变注册数据集快照
├── datasets/candidates/       # 候选题集
├── datasets/tuning/          # 调参集治理产物
├── datasets/baselines/       # Gold/基线相关产物
├── artifacts/                 # 兼容产物根目录
└── reports/                   # 兼容或其他流程的 machine/human 输出
```

当前隔离评测的主要产物位于对应的 `runs/<run_id>/` 内，典型结构如下；不同 `kind` 的 run 只会生成适用的子目录：

```text
runs/<run_id>/
├── run.json
├── run_manifest.json          # evaluation
├── dataset_snapshot.json      # evaluation
├── indexes/<index_version>/   # ingestion
├── assets/                    # ingestion
├── machine/                   # evaluation 机器可读结果
├── reports/                   # evaluation Markdown 报告
├── summary.json               # evaluation
└── summary.md                 # evaluation
```

### 2.3 staged index

每个摄取运行在自己的目录中构建索引。一个可进入发布门禁的 staged index 至少包含：

- `manifest.json`：来源身份、解析产物、标准化单元以及可选资源引用和哈希关联；
- `units.jsonl`：带稳定 locator 的标准化知识单元；
- `build_state.json`：必须为 `staged_complete`；
- `chroma/`：批量写入且成功提交后的向量存储；
- 需要时的页级 `checkpoints.sqlite3` 和 run-local 解析资源目录。

staged 完整性检查会比较 manifest、unit/vector/ingestion 计数、embedding fingerprint、index version 和归属的 ingestion run，并严格回读 run-local source、Docling 产物和单元资源。新 manifest 还封存完整 `embedding_config`、`identity_sha256` 和 `release_id`；identity 投影使用规范 UTF-8 JSON 和 LF 换行，manifest 完整哈希对 CRLF/LF 等平台换行做同一规范化，评测绑定不改变 release identity。Chroma 以独立 attempt 目录构建，完成后把目录内容摘要和总字节数写入 `artifact_integrity.chroma`，staged、formal release manager 和 runtime readiness 都会重新计算并校验；构建失败不能留下可发布的伪完整索引。解析资源用于构建审计和可选的图片/表格复核，不是正式 RAG 检索运行时的必需发布文件；这类来源和解析产物校验属于构建、评测和发布门禁。

正式多模态 embedding 策略当前固定为 `openai_compatible` API，凭据只从 `EMBEDDING_API_KEY` 读取，endpoint 从 `EMBEDDING_BASE_URL` 读取；`production_defaults.json` 中的 `local_embedding.enabled=false` 关闭本地生产 embedding。隔离评测仍可显式使用自己的配置快照，但不会覆盖正式策略或 active pointer。

切换 defaults 不会改写已有 schema-5 active release 或其 Chroma；当前旧 active 仍是历史产物，必须在 API 配置就绪后重新构建、评测并显式 publish 新 release，才能完成正式迁移。

索引身份不是一个可随意传入的字符串，而是由 `ingestion_run_id`、`index_version`、`collection_name`、manifest 哈希和 embedding fingerprint 共同约束。后续 RAG 试跑、候选题集、评测和 Gold 校验必须绑定同一个 staged index；切换索引后不能复用旧绑定而跳过重新校验。

### 2.4 数据集与评测运行

统一题集契约为 `rag_eval_v1`。数据集注册表保存不可变 revision 和内容哈希；任务通过 `dataset_ref` 解析注册快照，或在请求中提交内联题集。题集类型包括：

- `gold_regression`：必须有 `reference_answer`、`expected_claims` 和 `gold_evidence`；
- `generated_candidate`：必须有 `reference_answer` 和 `expected_claims`，可在人工审核前用于隔离观测；
- `reference_free`：可以没有 gold，检索指标应为 `unscored`/`null`，不能伪造为 0；
- `untyped`：兼容未分类题集。

评测运行会在创建时冻结题集 identity、staged index identity、已展开的 retrieval/Ragas 配置、策略 profile 身份和执行步骤到 `run_manifest.json`。默认步骤为：

```text
validate_datasets → retrieval_eval → ragas_eval → trace_export → summary
```

Ragas 先生成回答，再使用有效回答执行 judge；回答生成失败时运行失败且不继续调用 judge；judge 全部没有有效分数时以 `ragas_judge_no_valid_scores` 失败关闭。

内置 profile `active_current`、`quick_cached`、`reviewed_5_core_metrics` 来自代码且只读；自定义 profile 存储在 MySQL `rag_eval_profiles`。profile 同时描述 retrieval 与 Ragas 配置，隔离运行仍保存完整快照。对 evaluation API 而言，`strategy_profile` 主要记录本次运行采用的策略身份；API 不会仅凭 `strategy_profile.profile_id` 自动加载自定义 profile，调用方必须同时提交已展开的 `retrieval` 和 `ragas` 配置。工作台会在提交前完成展开。发布自定义 profile 只写入正式 retrieval 配置，不改变索引 active pointer。

## 3. release 生命周期

正式多模态知识库由 formal index root 和 runtime pointer 管理；PNG、Docling 页级 JSON 以及 source 副本属于可独立保留的解析资产包，不作为正式 RAG 发布包的一部分。构建、评测和发布阶段必须在受控目录内按内容哈希唯一解析正式 PDF；已发布 release 的生产 worker 只校验 manifest 中的冻结来源身份，不回读原始 PDF。新 pointer schema 将 active/fallback、generation、相对索引路径和 manifest 哈希收敛在单个 `active_index.json`；旧 `previous_index.json` 只作为兼容读取来源。默认语义是：

- `active_index.json` 指向当前正式索引；
- pointer 的 `fallback` 最多保留唯一上一个可用 active 版本，也可以为空；当前部署只保留 `active`，隔离 candidate 不进入 formal 稳定目录；
- 发布只物化 staged index，不把 run-local 解析资产复制到 formal asset 目录；
- 发布成功后只保留 active/fallback 两个 formal release，最老 fallback 在 pointer 成功切换后清理；清理失败写入 `cleanup_pending.json`；
- 物化、完整性或 CAS 失败只清理 incoming，既有 active/fallback/pointer 不变；完整性失败的 active 才会进入 quarantine 并受控提升 fallback。

### 3.1 门禁

`gate-check` 只执行检查，不复制产物、不切换 pointer。当前所有检查均为阻断项：

| key | 含义 |
| --- | --- |
| `controlled_source_identity` | 发布门禁中 source ID、document ID、路径和哈希必须命中正式受控来源；运行时只校验同一冻结身份 |
| `staged_integrity` | staged 阶段严格校验 manifest、解析资源、units、向量和 build state；正式候选复核可使用不依赖外置解析资源的模式 |
| `production_retrieval` | 当前正式检索门禁通过 |
| `production_policy` | parser、VLM、embedding 等正式策略与 manifest 一致 |
| `ragas` | 与该 ingestion/index 完全绑定的 Ragas 评测成功且通过 |
| `active_pointer_consistency` | 发布前 active pointer 未被其他操作改变 |
| `active_pointer_generation` | 发布前 active pointer generation 未被其他操作改变 |
| `manifest_unchanged` | 门禁读取的 manifest 哈希未发生变化 |

远程图片或表格增强失败属于可降级解析告警，不单独阻断发布；来源缺失、页级质量路由失败、未授权外发、空检索文本、产物/计数/manifest 不一致仍然阻断，正式题集覆盖率和检索阈值继续兜底。门禁报告包含 `state`、`publishable`、`checked_at`、`release`、`checks`、`evaluation`、`active`、`previous` 和 `requires_worker_restart=true`。`state=ready_to_publish` 只表示可以进入显式发布步骤，不代表已经发布。

### 3.2 发布与回滚

`publish` 必须提交 `confirm=true`，并再次执行发布前门禁。通过后会把评测 run、数据集 revision/hash、评测结果 hash 和 gate hash 封存到 `evaluation_binding`，再由集中 release manager 将 run-local staged index 物化到 formal incoming，校验 manifest/必要产物后在发布锁下以一次 pointer 替换完成晋级；解析资产包保持在 run-local 或外部存储中。正式 RAG 运行时读取 active pointer 指向的 Chroma 和 manifest，只校验冻结来源身份，不要求原始 PDF、PNG 或 Docling 页级 JSON 在 formal 目录中存在。可选的 `expected_active_index_version` 或 `expected_generation` 用于阻止并发覆盖。

回滚同样需要 `confirm=true`，检查期望的 active 版本并只允许当前唯一 fallback；成功后仍需生产 Agent worker drain/restart 才能让已经加载的 lazy Runtime 使用新 release。正式 readiness 发现 manifest、release identity 或必要索引产物损坏时，release manager 会把失败 active 移入 quarantine，再提升 fallback；embedding API 的 401/402/429、超时、连接和服务端错误只进入独立熔断/不可用路径，不切换 pointer。

隔离评测的 run、候选题集、Ragas 报告或 profile 发布接口都不会隐式执行上述 release publish。生产 release 的控制入口是 `/api/rag_eval/multimodal/releases/*`，而不是评测完成回调。`Agent/knowledge_base/multimodal/cli.py` 仅用于开发/离线维护；其中的 `publish`、`rollback` 和 `run --publish` 都是非正式入口，其结果不能作为正式 release 证据。

## 4. worker 与进程边界

### 4.1 `rag-eval-worker`

入口是 `python -m app.rag_eval.worker`。Web 进程将任务和 payload 写入 MySQL `rag_eval_jobs` 后立即返回 202；worker 从持久队列领取任务，运行进度写入 run directory，事件由 SSE 跨进程读取。

当前任务类型、固定优先级和默认并发上限如下：

| job kind | 优先级 | 默认上限 |
| --- | ---: | ---: |
| `rag_query` | 60 | 2 |
| `evaluation` | 50 | 3 |
| `dataset_governance` | 40 | 1 |
| `tuning_dataset_governance` | 30 | 1 |
| `candidate_generation` | 20 | 1 |
| `ingestion` | 10 | 1 |

默认常驻 slot 数为 5（`RAG_EVAL_EVALUATION_WORKERS`，配置范围 1–16）。claim 使用 MySQL 命名锁、事务和 `FOR UPDATE SKIP LOCKED`，同一 job kind 的 running 数不能超过上限。worker 以 heartbeat 维护租约；进程遗留的 running job 超过 `RAG_EVAL_EVALUATION_JOB_STALE_AFTER_SECONDS` 后收敛为 `failed`，不会自动重跑可能产生外部模型调用的任务。

评测 worker 与生产 Agent worker 是两个不同进程：当前评测 worker 负责隔离任务队列和产物，不负责生产聊天 graph，也不负责 release pointer 切换。隔离 run 创建时冻结 embedding 配置，构建、候选生成和查询复用该快照；evaluation worker 的 API 并发、速率和熔断限制使用 `RAG_EVAL_EMBEDDING_*` 前缀，与正式 worker 的 `RAG_EMBEDDING_*` 状态隔离。

### 4.2 生产 Agent worker

生产聊天任务的入口是 `python -m app.agent.worker`，一般架构见 [`agent-runtime.md`](agent-runtime.md)。它启动时只做 active pointer、manifest 冻结来源身份、manifest 中的 embedding 配置、版本、collection 和向量目录的轻量 readiness 检查，不读取原始 PDF；release 完整性失败时尝试受控回退，embedding API 配置缺失或请求故障则保持当前 pointer 不变并以 `rag_unavailable` 运行，避免把 Web/worker 启动等同于 RAG 已可用。真正的 `RagRuntime` 在首次查询时 lazy 初始化，进程内共享，active pointer 变化后需要 drain 和 restart，不热切换已有 Runtime。

生产 Agent worker 具备独立的 SIGTERM/SIGINT drain 语义；这不等同于 `rag-eval-worker` 已实现相同的优雅停机机制。两者的运行、扩缩容和部署说明分别由 [`development/deployment.md`](../development/deployment.md) 与本文维护。

## 5. 生命周期保护

- 运行状态、事件、结果和产物通过统一 run lifecycle 读取；具体类型路径仍保留兼容，但新客户端应使用 `/isolated/runs/<run_id>` 族。
- 删除是受保护操作：运行中的任务不能直接删除，摄取运行有下游引用时不能删除，用户来源删除也不能影响已生成的历史产物。
- 取消只改变任务生命周期，不把未完成的外部调用伪装成成功；worker 租约失效时结果被丢弃并 fail closed。
- release gate 会重新读取 staged 产物和 active pointer；构建通过、评测通过或历史报告存在，都不足以单独授权 active 切换。
- 运行目录、上传来源、候选索引和报告是可审计对象；维护状态不会因为 candidate 超额而自动清理任何对象。

## 6. 关联文档与实现入口

- HTTP 路由、请求字段、响应、SSE 和兼容路径：[`api/rag-eval.md`](../api/rag-eval.md)。
- 生产 Agent、LangGraph、MCP 和用户 Job：[`agent-runtime.md`](agent-runtime.md) 与 [`job-file-lifecycle.md`](job-file-lifecycle.md)。
- 部署拓扑和 worker 启动边界：[`../development/deployment.md`](../development/deployment.md)。
- 主要实现：`app/rag_eval/routes.py`、`app/rag_eval/isolated_runs.py`、`app/rag_eval/job_service.py`、`app/rag_eval/worker.py`、`Agent/knowledge_base/multimodal/`。
