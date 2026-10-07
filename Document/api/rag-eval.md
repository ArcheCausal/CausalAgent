# RAG 评测 HTTP API

文档职责：定义当前 `/api/rag_eval` 蓝图提供的完整 HTTP API 契约，包括来源、摄取、staged index、评测、治理、release、配置、数据集、运行生命周期和 SSE。

适用范围：面向 RAG 评测工作台、脚本和开发者客户端；业务对象与 worker 边界见 [`../architecture/rag-evaluation.md`](../architecture/rag-evaluation.md)，通用 request ID、分页和 HTTP 约定见 [`conventions.md`](conventions.md)。

> 事实来源是 `app/rag_eval/routes.py` 及其调用的 service/manager。本文按当前 checkout 的 77 个 method-path 操作编排；同一路径的多个方法在表中合并展示。具体实现发生变化时，先更新路由代码，再同步本页。

## 1. 通用约定

### 1.1 Base URL、请求和响应

- Base URL：`/api/rag_eval`。路径使用下划线，例如 `/api/rag_eval/isolated/evaluation-runs`；页面地址 `/rag-eval` 不是本 API 前缀。
- 鉴权：整个蓝图要求 `rag_eval.access`（未登录 `401`，缺少权限 `403`）；读请求要求 `rag_eval.read`，写请求要求 `rag_eval.run` 且必须带 `X-CSRF-Token`，发布、回滚和治理路由分别要求 `rag_eval.publish`、`rag_eval.rollback` 和 `rag_eval.governance`。发布、回滚和治理结果会写入 `admin_audit_events`，包含操作者、动作、目标、请求 ID 和结果。
- JSON 成功响应通常为 `{"success": true, "data": ...}`；创建长任务使用 HTTP `202`，创建数据集/来源/profile 使用 `201`。
- JSON 失败响应通常为 `{"success": false, "error": "..."}`，数据集校验还可能包含 `error_code`，发布门禁失败还包含 `data` 报告。
- `NaN` 和无穷浮点数序列化为 JSON `null`。
- 应用级 request context 会提供 `X-Request-ID`；客户端可按通用 API 约定传递合法 request ID。
- JSON 请求使用 `Content-Type: application/json`。上传使用 `multipart/form-data`，文件字段名是 `file`。
- 该蓝图自身没有统一的 `login_required`/CSRF 装饰器；profile 的自定义记录通过当前 session `user_id` 区分，未登录时使用本地开发共享 owner。生产部署如需访问控制，应由外层认证/网关提供，不能把本页当作鉴权承诺。

### 1.2 状态码

| 状态码 | 语义 |
| ---: | --- |
| `200` | 查询、更新、取消请求、删除或 gate-check 请求完成；gate-check 即使 `state=blocked` 也通常返回 `200` |
| `201` | 成功创建来源、profile 或注册数据集 |
| `202` | 长任务已写入 `rag_eval_jobs` 队列，尚未代表执行成功 |
| `400` | 请求格式、字段、数据集或来源参数无效 |
| `404` | run、artifact、dataset、revision 或 source 不存在 |
| `409` | index identity 不匹配、运行仍在执行、引用保护或 publish 的 release gate 失败；具体接口仍以各节说明为准 |
| `500` | 未预期的服务端错误 |

所有长任务都要以返回的 `run_id` 查询状态；不能把 `202` 当作 staged、succeeded 或 publishable。

### 1.3 长任务、运行状态和 SSE

状态对象因 `kind` 不同会附加字段，但通常包含：`run_id`、`kind`、`status`、`created_at`、`started_at`、`finished_at`、`current_stage`、`cancel_requested`、`execution_backend`、`job_id`、`error`、`ingestion_run_id`、`index_version`、`manifest_sha256`、`events` 及类型专属摘要。摄取成功的 run 状态是 `staged`；其它任务通常以 `succeeded`、`cancelled` 或 `failed` 终止。

统一运行接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/isolated/runs/<run_id>` | 读取任意隔离 run 当前状态 |
| `DELETE` | `/isolated/runs/<run_id>` | 按 `kind` 选择受保护删除策略 |
| `GET` | `/isolated/runs/<run_id>/result` | 读取最终结果 |
| `GET` | `/isolated/runs/<run_id>/artifacts/<artifact_name>` | 读取 JSON、Markdown 或文本产物 |
| `GET` | `/isolated/runs/<run_id>/stream` | 订阅统一 SSE |
| `POST` | `/isolated/runs/<run_id>/cancel` | 请求取消 |

状态、结果、产物和取消成功响应都是 `200` 的 JSON `data`；不存在的 run 是 `404`。统一删除按类型处理：evaluation 读取 `{ "force": true }`，ingestion 读取 `{ "cascade": true }`，RAG/candidate/governance/tuning derived run 不需要 body。运行中或仍被引用时返回 `409`。

SSE 响应为 `text/event-stream`，并带 `Cache-Control: no-cache`、`Connection: keep-alive`、`X-Accel-Buffering: no`。每条消息当前使用：

```text
data: {"type":"...", "run_id":"...", ...}

```

连接首先发送 `type=connected`；跨进程 persistent worker 的事件由运行目录轮询，约每秒读取一次，15 秒无事件发送 `type=heartbeat`。当前不发送标准 SSE `id:` 或 `event:` 字段。`run_done`、`run_error`、`run_cancelled` 或其它终态会结束流。

具体 run 类型的旧状态/结果/产物/SSE/取消路径仍注册，但会附加：

```http
Deprecation: true
Link: </api/rag_eval/isolated/runs/<run_id>>; rel="successor-version"
```

新客户端应使用统一路径；创建、列表、review、rebind、history、diff 和 release 路径不因这一兼容机制自动变为旧接口。

## 2. 状态、配置、策略 profile 与数据集

### 2.1 状态和配置

| 方法 | 路径 | 请求/查询 | 成功 `data` |
| --- | --- | --- | --- |
| `GET` | `/isolated/capacity` | 无 | 队列容量快照 |
| `GET` | `/status` | 无 | 当前 benchmark、向量库和最近评测汇总 |
| `GET` | `/config` | 无 | 当前运行时可调参数 |
| `PUT` | `/config` | JSON 对象 | 更新后的运行时参数；重启后恢复默认 |
| `GET` | `/steps` | 无 | pipeline 步骤说明 |
| `GET` | `/production-config` | 无 | 正式 RAG retrieval 配置快照 |
| `POST` | `/production-config/publish` | 可选 JSON | 将当前评测配置写为正式 retrieval 配置 |

`GET /isolated/capacity` 返回 `configured_slots`、`queued_total`、`running_total`、`available_slots`、按 kind 的 `kinds`、`oldest_queued_age_seconds` 和 `stale_running`；它只读 MySQL 汇总，不 reconcile 或修改任务。

`PUT /config` 的 retrieval 参数包括 `dense_fetch_k`、`dense_mmr_k`、`sparse_fetch_k`、`final_top_k`、`dense_score_threshold`、`final_rerank_threshold`、`mmr_lambda`、`answer_max_contexts` 和 `answer_context_compression`（`none`/`page_dedupe`）。评测参数包括 `limit`、`max_contexts`、`max_context_chars`、`max_response_chars`、`ragas_timeout`、`ragas_max_workers`、`ragas_max_retries`、`ragas_max_wait`、`repeat_count`、`low_score_threshold`、`retrieval_recall_low_threshold` 和 `retrieval_mrr_low_threshold`；数值范围由 service 校验，非法值为 `400`。

`POST /production-config/publish` 是正式 retrieval policy 的兼容管理入口，不替代基于 staged index 的 `/multimodal/releases/publish`。它可接收 `config_overrides`、`source_run_id` 和 `note`；本路由的成功只说明 policy 快照已写入，不说明索引或生产 worker 已切换。

### 2.2 unified strategy profile

| 方法 | 路径 | 请求/查询 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/profiles` | 无 | 内置和当前用户可访问的 profile |
| `POST` | `/profiles` | JSON | 创建自定义 profile，返回 `201` |
| `PUT` | `/profiles/<profile_id>` | JSON | 更新自定义 profile；内置只读 |
| `DELETE` | `/profiles/<profile_id>` | 无 | 删除自定义 profile；内置/当前正式 profile 受保护 |
| `POST` | `/profiles/<profile_id>/publish` | 可选 `{ "note": "..." }` | 发布自定义 profile 的 retrieval 快照 |

创建/更新 body 的核心字段是 `name`、`retrieval` 对象、`ragas` 对象；可选 `retrieval_profile` 默认 `active_current`，`ragas_profile` 默认 `generic_pipeline`。profile 持久化允许的 Ragas 字段包括 `limit`、`selected_metrics`、`include_reference_metrics`、`run_ragas`、`reuse_prepared_dataset`、`reuse_score_cache`、`max_contexts`、`max_context_chars`、`max_response_chars`、`ragas_timeout`、`ragas_max_workers`、`ragas_max_retries`、`ragas_max_wait`、`answer_relevancy_strictness`、`judge_profile`、`repeat_count`、`low_score_threshold`、`retrieval_recall_low_threshold` 和 `retrieval_mrr_low_threshold`。这些是 profile 存储字段，不等同于 evaluation 运行请求字段。

内置 profile ID 是 `active_current`、`quick_cached`、`reviewed_5_core_metrics`。`GET /profiles` 的顶层 `data` 包含 `default_profile_id`、`published_profile_id` 和 `profiles`。自定义 profile 的创建、更新、发布错误通常为 `400`，不存在为 `404`，不可删除冲突为 `409`。

一次隔离 evaluation 的 `retrieval` 还可提交 `profile`、`overrides`、`sweep`、`sweep_max_workers`；Ragas 可提交 `profile`、指标/上下文/响应/超时/并发/重试/judge/重复和阈值字段，以及 `run`、`prepare_only`。profile 持久化专用的 `run_ragas`、`reuse_prepared_dataset`、`reuse_score_cache` 不是当前 evaluation 请求字段。`strategy_profile` 用于记录本次运行的策略身份；API 不会仅凭 `strategy_profile.profile_id` 自动解析自定义 profile，调用方必须同时提交展开后的 `retrieval` 与 `ragas` 配置。策略 profile 不能绕过 index binding 或 release gate。

### 2.3 注册数据集

| 方法 | 路径 | 请求/查询 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/datasets` | 完整 `rag_eval_v1` JSON | 注册不可变 snapshot，返回 `201` |
| `GET` | `/datasets` | `page`、`page_size`、`dataset_kind`、`lifecycle_status` | 分页读取元数据；`page>=1`、`1<=page_size<=100` |
| `GET` | `/datasets/<dataset_id>/revisions` | `page`、`page_size` | 分页读取 revisions；`page>=1`、`1<=page_size<=100` |
| `GET` | `/datasets/<dataset_id>/revisions/<revision>` | 无 | 返回 revision 元数据和完整 bundle |

数据集顶层至少包含：

```json
{
  "schema_version": "rag_eval_v1",
  "dataset_id": "example",
  "dataset_kind": "gold_regression",
  "dataset_revision": "v1",
  "source_snapshot": {},
  "samples": []
}
```

每个 sample 必须有非空 `question`；可选 `sample_id`、`reference_answer`、`expected_claims`、`gold_evidence`、`judge_rubric` 和 `source`。`gold_evidence` 是含稳定 locator 的对象列表，推荐使用 `document_id`、`page_number`、`unit_id`、`modality`、`content_kind`、`asset_uri` 等字段。`gold_regression` 还必须有 reference answer、claims 和 evidence；`generated_candidate` 必须有 reference answer 和 claims。RAG 试跑和 evaluation 输入最多 100 个 sample；数据集注册本身还会按 schema、ID 和 revision 校验。注册冲突返回 `409` 与 `error_code=dataset_revision_conflict`；未找到 revision 返回 `404` 与 `dataset_not_found`。

## 3. 来源、摄取与 staged index

### 3.1 来源目录和上传

| 方法 | 路径 | 请求 | 成功响应 |
| --- | --- | --- | --- |
| `GET` | `/isolated/source-catalog` | 无 | `{ "sources": [...] }` |
| `POST` | `/isolated/sources` | multipart 字段 `file` | `{ "source": {...} }`，`201` |
| `PATCH` | `/isolated/sources/<source_id>` | `{ "display_name": "..." }` | 更新后的来源摘要 |
| `DELETE` | `/isolated/sources/<source_id>` | 无 | 删除结果 |

上传先按扩展名、非空、20 MiB 默认大小、内容可读性和 SHA-256 校验，再幂等保存到来源目录。上传接口只登记 source，不启动摄取；同内容重复上传返回已有 source 摘要。`PATCH` 只改显示名；`DELETE` 只接受 `upload_` source，活动摄取引用时为 `409`，固定来源或非法 ID 为 `400`。`upload_` 来源只具备隔离评测资格，不能直接作为正式 release 的受控来源。

### 3.2 摄取历史和创建

| 方法 | 路径 | 请求/查询 | 成功响应 |
| --- | --- | --- | --- |
| `GET` | `/isolated/ingestion-runs` | `status`、`source_id`、`page` 默认 1、`page_size` 默认 50（上限 100） | `{items,page,page_size,total,total_pages}` |
| `POST` | `/isolated/ingestion-runs` | 见下 | 入队 run 状态，`202` |

创建 body：

```json
{
  "source_ids": ["source_x"],
  "max_pages": 12,
  "allow_remote_data": false,
  "authorized_source_ids": [],
  "embedding_config": {
    "mode": "api",
    "provider": "openai_compatible",
    "model": "embedding-test",
    "dimension": 1536,
    "api_key_env": "EMBEDDING_API_KEY",
    "base_url_env": "EMBEDDING_BASE_URL",
    "endpoint_identity": "https://embedding.example/v1"
  }
}
```

可用字段及约束：

- `source_ids` 与 `sources` 互斥；`source_ids` 是目录 ID 列表，`sources` 是含 `uri`、可选 `source_id`、`display_name`/`name` 的对象列表；最多 20 个。`sources[].uri` 是服务端本地文件或目录路径，仅适用于受信内部调用，不会下载 HTTP URL，也不自动限制在来源目录内。外部调用应先上传来源或使用 `source_ids`；生产部署应由外层鉴权并增加允许根目录校验。
- `max_pages` 必须是正整数。`page_ranges` 是对象列表，每项含 `source_id`、`start_page`、`end_page`，页码从 1 开始且闭区间；不能与 `max_pages` 同时提交。
- `allow_remote_data` 必须是 boolean，默认 `false`；开启时必须提交来源级 `authorized_source_ids`，且必须通过服务端 `VISION_ALLOW_REMOTE_DATA`。
- `embedding_config` 可选；提交后在 run 创建时冻结，staged 构建、候选生成和 isolated 查询复用同一份配置。只允许 `api_key_env`/`base_url_env` 等凭据引用，禁止提交 `api_key`、Token、Cookie 或带凭据 URL；省略时使用当前 production defaults（API-key embedding）。本地配置即使在隔离 run 中可用，也不能通过正式 production policy 发布。

响应的 run 至少包含 `kind=ingestion`、`status=queued`、来源 IDs/显示名、页范围、远程授权摘要、`execution_backend=persistent_worker` 和后续待填充的 `index_version`、`manifest_sha256`、`unit_count`、`vector_count`。参数/来源错误是 `400`，远程权限或策略冲突是 `409`。

具体摄取 run 的旧路径仍保留兼容：

| 方法 | 旧路径 | 说明 |
| --- | --- | --- |
| `GET` | `/isolated/ingestion-runs/<run_id>` | deprecated，改用统一状态 |
| `DELETE` | `/isolated/ingestion-runs/<run_id>` | deprecated；body 可为 `{ "cascade": true }` |
| `GET` | `/isolated/ingestion-runs/<run_id>/stream` | deprecated SSE |
| `POST` | `/isolated/ingestion-runs/<run_id>/cancel` | deprecated 取消 |

## 4. release API

| 方法 | 路径 | 请求 | 语义 |
| --- | --- | --- | --- |
| `GET` | `/multimodal/releases/status` | 可选 query `ingestion_run_id` + `index_version`，以及 `evaluation_run_id` | active/fallback 和指定 release 摘要；指定 release 时前两个参数必须成对提交 |
| `POST` | `/multimodal/releases/gate-check` | `ingestion_run_id`、`index_version`，可选 `evaluation_run_id`、`expected_active_index_version`、`expected_generation` | 运行全部门禁，不切 pointer |
| `POST` | `/multimodal/releases/publish` | 必须 `confirm=true`、`ingestion_run_id`、`index_version`、`evaluation_run_id`；可选 `expected_active_index_version`、`expected_generation` | 通过门禁后封存 evaluation binding，由 release manager 物化 incoming、校验并原子切 active/fallback；成功响应包含 `requires_worker_restart=true` |
| `POST` | `/multimodal/releases/rollback` | 必须 `confirm=true`、`index_version`；可选 `expected_active_index_version`、`expected_generation` | 复用正式门禁执行回滚 |

`status` 的 `data` 包含 `active`、`previous`、`candidates`、`candidate_overflow`，提交成对的 `ingestion_run_id`/`index_version` 后还包含 `release`。`gate-check` 请求完成时返回 `publishable`、`state`、`checks`、`evaluation` 和 `requires_worker_restart`；即使 `state=blocked` 也不代表 HTTP 请求失败，并且它永远不切 pointer。

`publish` 成功才返回 promotion、active/previous 等发布结果；正式发布门禁失败返回 `409`，响应为 `{ "success": false, "error": "...", "data": <gate report> }`。`expected_generation` 必须是非负整数，和 active 版本一起作为并发 CAS 快照；generation 不一致时不会切换 pointer。`confirm` 缺失/错误、参数缺失、候选不可解析或当前 active 版本冲突按当前实现返回 `400`。回滚同样需要显式确认，参数/版本错误也按当前实现返回 `400`，不是“读取 previous”操作。

## 5. 候选题集、调参集和 Gold 治理

### 5.1 候选题集

| 方法 | 路径 | 语义 |
| --- | --- | --- |
| `POST` | `/isolated/candidate-runs` | 在 staged index 上生成 `generated_candidate` |
| `POST` | `/isolated/candidate-runs/rebound-import` | 导入固定本地候选产物的兼容审核入口 |
| `POST` | `/isolated/candidate-runs/<run_id>/review` | 提交人工审核和逐题更新 |
| `POST` | `/isolated/candidate-runs/<run_id>/rebind` | 重新绑定 staged index，要求重新审核 |

创建候选的 body 至少需要 `ingestion_run_id`、`index_version`；`question_count` 默认 48，范围 1–128；`max_workers` 默认 1，范围 1–4；可选 `dataset_id`。旧字段 `max_units`（1–128）和 `questions_per_unit`（1–3）在未提交 `question_count` 时兼容。成功入队返回 `202`；index binding 不匹配返回 `409`。

review body 需要非空 `reviewer`（最多 120 字符）和非空 `decisions`；每项至少有 `sample_id`、`decision`（`approved`/`rejected`/`needs_revision`）和可选 note（最多 1000 字符）。`updates` 可改 `question`、`reference_answer`、`expected_claims`、`gold_evidence`，更新写入新的 reviewed revision，不覆盖原候选。

rebind body 需要 `ingestion_run_id` 和 `index_version`；成功后会清除旧 index 绑定并将审核状态置为需要重新批准。`GET/DELETE/result/artifacts/stream/cancel` 的具体 candidate run 路径与统一 run lifecycle 相同，但当前均附加 deprecated successor headers。

### 5.2 调参集治理

| 方法 | 路径 | 请求 | 语义 |
| --- | --- | --- | --- |
| `POST` | `/isolated/tuning-dataset-runs` | `ingestion_run_id`、`index_version`、可选 `target_count` 默认 48（1–128）、`minimum_metric` 默认 0.2（0–1） | 入队索引绑定调参集治理 |
| `GET` | `/isolated/tuning-dataset-runs/<run_id>` | 无 | deprecated 状态 |
| `DELETE` | `/isolated/tuning-dataset-runs/<run_id>` | 无 | deprecated 删除终态 derived run |
| `GET` | `/isolated/tuning-dataset-runs/<run_id>/stream` | 无 | deprecated SSE |
| `GET` | `/isolated/tuning-dataset-runs/<run_id>/result` | 无 | deprecated 结果 |
| `GET` | `/isolated/tuning-dataset-runs/<run_id>/artifacts/<path:artifact_name>` | 无 | deprecated 产物 |

该流程只服务于隔离索引绑定调参，不进入正式 evaluation history，不切换 Gold、active index 或 production profile。

### 5.3 Gold v2 和治理

| 方法 | 路径 | 请求/查询 | 语义 |
| --- | --- | --- | --- |
| `POST` | `/gold-v2/freeze` | `candidate_run_id`、`ingestion_run_id`、`index_version`；可选 `replace_existing` | 冻结符合条件的 Gold v2 |
| `GET` | `/gold-v2/status` | 可选成对 `ingestion_run_id` + `index_version` | Gold 摘要及 staged 兼容性 |
| `POST` | `/baseline-v2/bind` | 无 | 只读绑定 active pointer、active_current 和 Gold |
| `POST` | `/gold-v2/governance` | `evaluation_run_id`、`confirm=true` | 入队 Gold 健康治理 |

`freeze` 只接受满足当前 Pearl/候选审核和 index binding 条件的候选；校验冲突为 `409`。`gold-v2/status` 在指定 staged index 时会返回 `compatibility`（`unselected`/`compatible`/`rebind_required`）、`index_status`、绑定版本和 checked counts；两个 query 参数必须同时出现。

`governance` 返回 `202` 的治理 run；它不会切换 active index 或 strategy profile。其具体 run 的以下路径均是 deprecated successor wrapper：

```text
GET    /gold-v2/governance-runs/<run_id>
DELETE /gold-v2/governance-runs/<run_id>
GET    /gold-v2/governance-runs/<run_id>/result
GET    /gold-v2/governance-runs/<run_id>/artifacts/<path:artifact_name>
GET    /gold-v2/governance-runs/<run_id>/stream
POST   /gold-v2/governance-runs/<run_id>/cancel
```

## 6. staged index RAG 试跑

| 方法 | 路径 | 请求 | 语义 |
| --- | --- | --- | --- |
| `POST` | `/isolated/rag-runs` | `ingestion_run_id`、`index_version`，加一类问题输入 | 在指定 staged index 上真实检索和回答，`202` |
| `GET` | `/isolated/rag-runs/<run_id>` | 无 | deprecated 状态 |
| `GET` | `/isolated/rag-runs/<run_id>/result` | 无 | deprecated 结果 |
| `GET` | `/isolated/rag-runs/<run_id>/stream` | 无 | deprecated SSE |
| `POST` | `/isolated/rag-runs/<run_id>/cancel` | 无 | deprecated 取消 |

问题输入支持以下形式：

- `dataset_ref`：注册数据集引用对象，服务端按 dataset ID、revision 和 content hash 解析；
- `eval_dataset` 或 `dataset`：内联 `rag_eval_v1` 对象；
- `questions`：问题列表；
- `question`：单个问题。

`dataset_ref` 不能与内联 dataset 同时提交；如果没有 dataset 或 `questions`，服务端使用 `question` 生成单问题。问题最多 100 个，每项最终归一化为 `{ "question": "...", ... }`。内联/注册数据集会记录 `rag_eval_v1` identity；questions 和单问题分别记录 `isolated_questions_v1`、`isolated_question_v1`。index identity 不匹配返回 `409`。

## 7. evaluation API

### 7.1 单次与并行批次

| 方法 | 路径 | 请求 | 语义 |
| --- | --- | --- | --- |
| `POST` | `/isolated/evaluation-runs` | index binding、dataset、retrieval/Ragas options | 入队完整评测，`202` |
| `POST` | `/isolated/evaluation-batches` | index binding、dataset、`experiments` | 一次入队 2–4 个独立 evaluation runs，`202` |

单次评测 body 的核心字段：

```json
{
  "ingestion_run_id": "ingest_...",
  "index_version": "mm_...",
  "dataset_ref": {
    "dataset_id": "example",
    "dataset_revision": "v1"
  },
  "retrieval": {},
  "ragas": {},
  "strategy_profile": {},
  "steps": ["validate_datasets", "retrieval_eval", "ragas_eval", "trace_export", "summary"]
}
```

也可以提交 `eval_dataset`/`dataset` 内联题集；`dataset_ref` 与内联题集互斥，服务端会按注册 revision 的存储哈希校验快照。`dataset_source=gold_v2` 时不能内联，服务端读取本地冻结 Gold 并严格校验其 staged index 绑定。evaluation 输入限制最多 100 samples。

`retrieval` 默认空对象，支持 `profile`、`overrides`、可选 `sweep` 和 `sweep_max_workers`（最多 8，并行配置最多 16 组）。`ragas` 默认空对象，支持 profile、指标选择、上下文/响应限制、timeout、worker/retry/wait、judge profile、重复次数和低分阈值；`run` 控制是否执行 judge，`prepare_only=true` 时不会执行 judge。profile 持久化专用的 `run_ragas`、`reuse_prepared_dataset`、`reuse_score_cache` 不属于当前 evaluation 请求字段。`steps` 必须是列表，允许默认步骤和 `retrieval_sweep`，具体允许值由评测 service 校验。

批次 body 复用同一 dataset/index 字段，并要求 `experiments` 是 2–4 个对象。每个 experiment 有自己的展开后 `retrieval`、`ragas`、`steps`、`strategy_profile`；`strategy_profile.profile_id` 仅作为策略身份记录，必须非空且在批次内唯一。成功响应：

```json
{
  "success": true,
  "data": {
    "batch_id": "eval_batch_...",
    "run_count": 2,
    "runs": [{"run_id": "...", "batch_position": 1, "batch_size": 2}]
  }
}
```

批次中途入队失败时，已经创建的兄弟 run 会被请求取消，不把不完整批次当作有效对照组。

### 7.2 历史、diff 和具体 run

| 方法 | 路径 | 查询/请求 | 语义 |
| --- | --- | --- | --- |
| `GET` | `/isolated/evaluation-history` | `dataset_id`、`index_version`、`status`、`source_name`、`since`、`until`、`page`、`page_size` | 读取摘要历史，不执行评测 |
| `GET` | `/isolated/evaluation-diff` | 必须 `base_run_id`、`candidate_run_id` | 同题集 identity 的指标、配置、样本差异 |
| `GET` | `/isolated/evaluation-runs/<run_id>` | 无 | deprecated 状态 |
| `DELETE` | `/isolated/evaluation-runs/<run_id>` | 可选 `{ "force": true }` | 删除已结束/确认失活评测 |
| `GET` | `/isolated/evaluation-runs/<run_id>/result` | 无 | deprecated 结果 |
| `GET` | `/isolated/evaluation-runs/<run_id>/artifacts/<path:artifact_name>` | 无 | deprecated 产物 |
| `GET` | `/isolated/evaluation-runs/<run_id>/stream` | 无 | deprecated SSE |
| `POST` | `/isolated/evaluation-runs/<run_id>/cancel` | 无 | deprecated 取消 |

history 返回 `items`、`page`、`page_size`、`total`、`total_pages`，只读取 run.json/summary/run_manifest 等持久产物，不复用 legacy latest 或 active pointer。diff 要求两次运行的 dataset identity 完全一致，返回 `available`、`base`、`candidate`、`metric_deltas`、`config_deltas`、`sample_deltas` 和 `summary`。

评测删除不会删除 ingestion/index；运行中返回 `409`。`force=true` 只用于超过 `RAG_EVAL_EVALUATION_STALE_AFTER_SECONDS` 的失活运行，最小有效窗口由实现保证；仍有活动迹象时拒绝删除。

评测结果和产物的典型 machine 文件包括：`run_manifest.json`、`dataset_snapshot.json`、`machine/rag_eval_result.json`、`machine/rag_eval_sweep_result.json`、`machine/ragas_eval_dataset.json`、`machine/ragas_eval_result.json`、`machine/ragas_low_score_cases.json`、`machine/ragas_cross_metric_bad_cases.json`、`machine/trace.jsonl`、`machine/trace_index.json`、`summary.json` 以及 Markdown 报告。artifact API 返回 JSON 时使用 `application/json` envelope；Markdown/其它文本产物返回原始文本，不包 JSON envelope。

## 8. 当前路由总表

下面的表用于核对完整性；`deprecated` 表示仍可调用但应迁移到统一 run lifecycle 的具体运行路径。

| 方法 | 路径 | 状态 |
| --- | --- | --- |
| `GET` | `/isolated/capacity` | active |
| `GET` | `/status` | compatibility |
| `GET/PUT` | `/config` | compatibility |
| `GET/POST` | `/profiles` | active |
| `PUT/DELETE` | `/profiles/<profile_id>` | active |
| `POST` | `/profiles/<profile_id>/publish` | active |
| `GET` | `/production-config` | active |
| `POST` | `/production-config/publish` | compatibility/formal policy |
| `GET` | `/steps` | compatibility |
| `POST/GET` | `/datasets` | active |
| `GET` | `/datasets/<dataset_id>/revisions` | active |
| `GET` | `/datasets/<dataset_id>/revisions/<revision>` | active |
| `GET/DELETE` | `/isolated/runs/<run_id>` | active |
| `GET` | `/isolated/runs/<run_id>/result` | active |
| `GET` | `/isolated/runs/<run_id>/artifacts/<artifact_name>` | active |
| `GET` | `/isolated/runs/<run_id>/stream` | active |
| `POST` | `/isolated/runs/<run_id>/cancel` | active |
| `GET/POST` | `/isolated/ingestion-runs` | active |
| `GET` | `/isolated/source-catalog` | active |
| `GET` | `/multimodal/releases/status` | active |
| `POST` | `/multimodal/releases/gate-check` | active |
| `POST` | `/multimodal/releases/publish` | active/confirm |
| `POST` | `/multimodal/releases/rollback` | active/confirm |
| `POST` | `/isolated/sources` | active |
| `PATCH/DELETE` | `/isolated/sources/<source_id>` | active |
| `GET/DELETE` | `/isolated/ingestion-runs/<run_id>` | deprecated |
| `GET` | `/isolated/ingestion-runs/<run_id>/stream` | deprecated |
| `POST` | `/isolated/ingestion-runs/<run_id>/cancel` | deprecated |
| `POST` | `/isolated/candidate-runs` | active |
| `POST` | `/isolated/tuning-dataset-runs` | active |
| `GET/DELETE` | `/isolated/tuning-dataset-runs/<run_id>` | deprecated |
| `GET` | `/isolated/tuning-dataset-runs/<run_id>/stream` | deprecated |
| `GET` | `/isolated/tuning-dataset-runs/<run_id>/result` | deprecated |
| `GET` | `/isolated/tuning-dataset-runs/<run_id>/artifacts/<path:artifact_name>` | deprecated |
| `POST` | `/isolated/candidate-runs/rebound-import` | compatibility |
| `GET/DELETE` | `/isolated/candidate-runs/<run_id>` | deprecated |
| `GET` | `/isolated/candidate-runs/<run_id>/result` | deprecated |
| `GET` | `/isolated/candidate-runs/<run_id>/artifacts/<path:artifact_name>` | deprecated |
| `GET` | `/isolated/candidate-runs/<run_id>/stream` | deprecated |
| `POST` | `/isolated/candidate-runs/<run_id>/cancel` | deprecated |
| `POST` | `/isolated/candidate-runs/<run_id>/review` | active |
| `POST` | `/isolated/candidate-runs/<run_id>/rebind` | active |
| `POST` | `/gold-v2/freeze` | active |
| `GET` | `/gold-v2/status` | active |
| `POST` | `/baseline-v2/bind` | active |
| `POST` | `/gold-v2/governance` | active |
| `GET/DELETE` | `/gold-v2/governance-runs/<run_id>` | deprecated |
| `GET` | `/gold-v2/governance-runs/<run_id>/result` | deprecated |
| `GET` | `/gold-v2/governance-runs/<run_id>/artifacts/<path:artifact_name>` | deprecated |
| `GET` | `/gold-v2/governance-runs/<run_id>/stream` | deprecated |
| `POST` | `/gold-v2/governance-runs/<run_id>/cancel` | deprecated |
| `POST` | `/isolated/rag-runs` | active |
| `GET` | `/isolated/rag-runs/<run_id>` | deprecated |
| `GET` | `/isolated/rag-runs/<run_id>/result` | deprecated |
| `GET` | `/isolated/rag-runs/<run_id>/stream` | deprecated |
| `POST` | `/isolated/rag-runs/<run_id>/cancel` | deprecated |
| `POST` | `/isolated/evaluation-runs` | active |
| `POST` | `/isolated/evaluation-batches` | active |
| `GET/DELETE` | `/isolated/evaluation-runs/<run_id>` | deprecated |
| `GET` | `/isolated/evaluation-history` | active |
| `GET` | `/isolated/evaluation-diff` | active |
| `GET` | `/isolated/evaluation-runs/<run_id>/result` | deprecated |
| `GET` | `/isolated/evaluation-runs/<run_id>/artifacts/<path:artifact_name>` | deprecated |
| `GET` | `/isolated/evaluation-runs/<run_id>/stream` | deprecated |
| `POST` | `/isolated/evaluation-runs/<run_id>/cancel` | deprecated |

## 9. 客户端迁移顺序

一个完整的隔离评测客户端通常按以下顺序调用：

1. `GET /isolated/source-catalog`，必要时 `POST /isolated/sources`；
2. `POST /isolated/ingestion-runs`，保存返回的 `run_id`，用统一 state/stream 等待 `status=staged`；
3. 选择 registered/inline dataset，调用 `POST /isolated/rag-runs` 或 `POST /isolated/evaluation-runs`；
4. 用 `/isolated/evaluation-history` 和 `/isolated/evaluation-diff` 查看结果，必要时读取 artifacts；
5. 对准备发布的 controlled source 调用 release `gate-check`；只有人工确认后调用 `publish`；
6. 发布成功后按部署流程 drain/restart 生产 Agent worker，并重新检查 active release readiness。

上述每一步都应保存 source/index/dataset/run identity。任何一个 identity 发生变化，都应重新执行后续绑定和门禁，而不是复用旧结果。
