# evaluation-runner 实现设计

> 状态：待评审
> 日期：2026-10-30
> 分支：`feat-evaluation`（基于 develop）
> 背景方案：`Document/planning/evaluation-new-design.md`（口径来源，本文覆盖其实现细节）

## 1. 目标

在 `evaluation-runner/` 实现一键端到端评测：读取 `config.yaml` → 探活与确认 → 按分层抽样串行/并发跑真实 HTTP 流程 → 产出 `results.csv` / `summary.csv` / `report.md`。

被测对象是本分支 CausalAgent 的**对外行为**：上传 CSV、自动应答交互、生成报告、从报告取因果图与金标准比边。

## 2. 非目标

- 不评报告文字质量、可视化、web search。
- 不做 token 采集（`tokens` 列固定 `not_collected`）。
- 不做逐节点耗时、并发压力测试。
- 不引入前端配置页面。

## 3. 目录与模块

```
evaluation-runner/
  config.py      # 读取/校验 yaml、打印摘要、确认
  dataset.py     # 扫描、过滤、分层抽样、转 CSV、画像、gold
  answer.py      # 交互答复与方案确认（纯函数）
  client.py      # 登录/上传/建会话/建 Job/SSE/resume/cancel
  evaluate.py    # 取图、节点映射、双口径指标（纯函数）
  report.py      # summary.csv 与 report.md 生成（纯函数）
  runner.py      # 编排、并发、落盘
  selftest.py    # 无网络自检
  config.example.yaml
  requirements.txt
  local/                                   # 运行产物（gitignore）
  cdfm_bench_D10_30_fixed_r5/              # 已导入数据集
```

依赖方向：`runner → config/dataset/answer/client/evaluate/report`；`dataset`、`answer`、`evaluate`、`report` 不依赖网络；`client` 不依赖指标。依赖 `httpx==0.28.1`、`PyYAML>=6`、`numpy>=1.26`（`requirements.txt`），自检用标准库断言，不新增测试框架。

## 4. 配置

```yaml
run:
  base_url: http://localhost:5001
  parallel: 1              # 用户按机器决定，仅校验 >=1
  timeout_minutes: 15
  max_interactions: 6
  interaction_seconds: 300
  seed: 20261030
auth:
  username: ""             # 留空读 local/accounts.local.json
  password: ""
data:
  dir: ./cdfm_bench_D10_30_fixed_r5
  mechanisms: all          # 或 [CAM, Linear, ...]
  exclude: [TimeLag]
  dims: []                 # 空 = 全部
  ns: []                   # 空 = 全部
  per_cell: 1              # 每格抽几个 rep，1..5
output:
  dir: ./local/runs
```

校验规则（失败即退出并指出字段）：

- `base_url` 必填；`parallel`、`timeout_minutes`、`max_interactions`、`interaction_seconds` 为正整数。
- `per_cell` 取 1..5；`mechanisms` 必须是机制名列表或 `all`，标量（如单个字符串）会被拒绝。
- `mechanisms`/`dims`/`ns` 的取值在 runner 扫描数据目录后由 `validate_selection` 校验，非实际出现的机制、D 或 N 直接报错；`exclude` 可与之重叠，重叠以排除为准。
- 账号：`username` 为空时读 `local/accounts.local.json`；不存在则自动注册并把凭据写入该文件（gitignore）。

**启动确认**：先 `GET /api/check_auth` 探活，再打印摘要（机制数、格数、总集数、`parallel`、输出目录），要求交互输入 `y`；`--dry-run` 打印摘要后退出，`--yes` 跳过确认（脚本化/后台运行）。

## 5. 数据集与抽样

- 递归扫描 `data.dir` 下 `.npz`；每个文件须含 `X (n_rows, dim)` 与 `adjacency (dim, dim)`，`adjacency[i,j]=1` 表示 `Xi → Xj`。多余键（`mask`、`meta_json`、`adjacency_lag`）忽略。
- 幂等校验：缺键、形状不符、维度为 0、`gold` 为空 → 该集记 `dataset_invalid`，不发请求。
- 数据集标识 `dataset` = 去 `.npz` 的相对路径，如 `CAM/D010_N00500/rep00`；落盘文件名把 `/` 换成 `__`。
- 路径解析：`mechanism = parts[-3]`；`parts[-2]` 用 `^D(\d+)_N(\d+)$` 得名义 D/N；文件名用 `rep(\d+)` 得 rep。解析不到留空，不阻塞。
- `dim` / `n_rows` 一律取 `X.shape` 实测值，名义 D/N 仅作筛选。
- **抽样**：对每个选中的 `机制×D×N` 格，格内所有 rep 按 `blake2b(seed|dataset)` 排序取前 `per_cell` 个。确定性、可复现；执行顺序按 `dataset` 字典序。
- `TimeLag` 默认排除（`adjacency` 为 lag-1 块，CSV 无法恢复时序）；从 `exclude` 移除后可纳入，`report.md` 口径段以抽样配置中的 `exclude` 为准。

## 6. 端到端流程（单集）

1. 登录 `POST /api/login`（失败则不存在的账号走 `/api/register`）→ Cookie + `csrf_token`；写操作带 `X-CSRF-Token`。
2. `POST /api/upload_file`（multipart，字段 `file`，`text/csv`）→ `user_file_id`。
3. `POST /api/new_chat` → `session_id`。
4. `POST /api/agent/jobs`，body `{message, session_id, input_user_file_id, web_search_enabled: false}`，头 `Idempotency-Key: <uuid4>`。
5. `GET /api/agent/jobs/{job_id}/events`（SSE，重连只带查询参数 `last_event_id`）；流在终态或 `interrupt` 后自行结束；断流或传输错误时带最后事件 `id` 重连，最多 3 次，仍失败记 `sse_error`。`timeout_minutes` 在 SSE 循环内按事件到达检查（服务端发送真实的 `event: heartbeat` 事件，因此检查间隔约 15 s），超时先 cancel 再记 `timeout`。
6. `interrupt` → 按第 7 节生成答复 → `POST /api/agent/jobs/{job_id}/resume`（body `{question_id, answer}`，新 `Idempotency-Key`）。
7. 终态：`final_result` 继续；`error` / `canceled` 记对应失败。
8. 解析图与指标 → 追加写 `results.csv` → 保存 `csv/` 与 `raw/` 证据。

固定提示词（不加任何引导）：

```text
请读取上传的 CSV 数据文件，对变量 X0 到 X{dim-1} 执行因果分析
```

## 7. 交互自动化

当前分支只产出两类交互（`interaction.interaction_type`）：

- **`data_processing_question`（自由文本）**：用数据集画像事实回答——行数、列数、数值/二值类型、是否含缺失（转换后 `NaN`）、每列取值范围与唯一值数；语义兜底为「匿名观测变量 X0..X{d-1}，目标是恢复因果结构，请按数据本身推断，不要依赖领域先验」。
- **方案确认**（`data_processing_plan_confirmation`）：
  - `variable_semantics` 覆盖全部 dim 个变量；
  - `transformations` 不命中 `drop/delete/删除/删行/删列/rename/重命名/aggregate/聚合/filter/筛选/新增/mutate`；
  - 通过 → `{"action": "confirm", "plan_version": <事件中的 plan_version>}`；不通过 → 文本说明拒绝理由，等下一轮。
  - 与 `Agent/data_processing/plans.py` 的 `plan_confirmation_matches` 语义对齐。
- 预算：`max_interactions` 轮或 `interaction_seconds` 秒，超限记 `interaction_budget_exceeded` 并 `POST /api/agent/jobs/{id}/cancel`。

## 8. 取图与指标

**取图**：`final_result.data.document` →
1. `document.report_mode == "diagnostic"` → 直接记 `diagnostic_report`（`status=failed`，不重试，后续步骤不再执行）；
2. 递归遍历 `document.blocks`，找 `type == "causal_graph"` 的 block → `assets[block.asset_key]`；
3. 找不到时退化为「`assets` 中带 `graph_id` 的项」；
4. 仍无 → `no_causal_graph`（`status=failed`，可重试 1 次）；
5. `document.finalization_status` 写入结果列。

**节点映射**：`asset.nodes[].id → variable`，`variable` 必须匹配 `^X(\d+)$` 且索引 `< dim`；`edges[].source/target` 是 **node id**，先映射为索引。端点无法映射的边丢弃，记 `edge_variable_unmapped`，`status=partial`。

**边分类**：`edge_type == "directed"` 为有向边 `(i,j)`；`undirected` / `bidirected` / `partially_directed` / `partially_oriented` 为无向类，每条计 1 个 FP。

**镜像**：`(i,j)` 与 `(j,i)` 同时以 `directed` 出现时，按一对无向边计（1 FP），不计 TP。

**边计数**：`n_pred_edges` = 去掉镜像对后的有向边条数；`n_undirected_edges` = 无向类边数 + 镜像对数量。

**金标准** `gold = {(i,j) | adjacency[i,j]=1}`。

- `TP = |有向边 ∩ gold|`
- `recall = TP / |gold|`
- `precision = TP / (n_pred_edges + n_undirected_edges)`，分母为 0 记 0
- 骨架：有向边、无向类边与镜像对一律无向化后取点对集合（重复点对在集合中只计一次），与 gold 骨架求交；`skeleton_recall / skeleton_precision` 同构；precision 分母中无向类边每条计 1 个 FP，镜像对计 1 个
- 空图：全部记 0，`n_pred_edges = 0`
- 两条口径不可混比，`report.md` 必须声明

## 9. 输出

运行目录 `output.dir/<run_id>/`，`run_id = YYYYmmdd-HHMMSS-<git short sha>`。

### 9.1 results.csv

```
run_id, dataset, mechanism, rep, n_rows, n_cols, dim, lambda, n_gold_edges, n_pred_edges,
n_undirected_edges, recall, precision, skeleton_recall, skeleton_precision,
final_algorithm, finalization_status, interactions, duration_s, tokens, status, failure_reason
```

- `final_algorithm` 取 `asset.metadata.algorithm`
- `interactions`：resume 次数；`duration_s`：建 Job 到终态的墙钟秒数（1 位小数）
- `tokens`：固定 `not_collected`；`lambda`：本数据集不存在，留空
- `status`：`success` / `partial` / `failed`；`no_causal_graph`、`diagnostic_report` 等无图情况记 `failed`

### 9.2 summary.csv

```
run_id, started_at, git_commit, base_url, data_dir, seed, per_cell, parallel, mechanisms,
datasets, succeeded, failed, recall_mean, precision_mean, skeleton_recall_mean,
skeleton_precision_mean, duration_mean_s, interactions_mean, algorithm_distribution,
failure_reasons
```

- `succeeded` 含 `success` 与 `partial`；`failed` 只计 `failed`
- 均值只统计 `succeeded` 行；`failed` 不补零
- `algorithm_distribution` / `failure_reasons` 用 `a:2;b:1` 形式

### 9.3 report.md

依次包含：运行信息（commit、base_url、seed、parallel、抽样口径）→ 总体均值 → 按机制族表 → 按 D 表 → 按 N 表（按实测 `n_rows` 分组，非名义 N）→ 失败分布 → recall 最低 Top 10 → 口径声明（严格定向 vs 骨架、TimeLag 以抽样配置 `exclude` 为准、diagnostic 不计均值）。

### 9.4 证据

`csv/<dataset 净化>.csv`、`raw/<dataset 净化>.jsonl`（全量事件）、`raw/<dataset 净化>.json`（终态与交互记录）。

`summary.csv` 与 `report.md` 在运行结束后由 `results.csv` 重新生成，保证 `--resume` 后汇总一致。

## 10. 失败与重试

| failure_reason | 触发 | 重试 |
|---|---|---|
| `dataset_invalid` | 缺键/形状错/无 gold | 否 |
| `auth_error` | 登录/注册失败 | 否 |
| `upload_error` | 上传或建会话失败 | 否 |
| `job_rejected` | 建 Job 非 2xx 或传输失败 | 否 |
| `timeout` | 超过 `timeout_minutes` 未终态 | 1 次 |
| `sse_error` | 重连 3 次仍失败 | 否 |
| `terminal_error` | 收到 `error` 事件；或请求传输错误/未预期异常 | 1 次 |
| `canceled` | 收到 `canceled` 事件 | 否 |
| `no_causal_graph` | 报告无因果图资产 | 1 次 |
| `diagnostic_report` | `report_mode == diagnostic` | 否 |
| `interaction_budget_exceeded` | 超轮数/时长 | 否 |
| `edge_variable_unmapped` | 部分端点无法映射（`status=partial`） | 否 |

`upload_error` 覆盖上传文件或建会话失败（含传输失败），`job_rejected` 覆盖建 Job 非 2xx 或传输失败；SSE 传输断流最多重连 3 次后记 `sse_error`；`auth_error` 仅指登录/注册的 HTTP 层面失败；只有逃出 `_run_once` 的传输错误才归 `terminal_error`。此类 `terminal_error` 虽在 `RETRYABLE` 中，但不会被重试循环重新执行。重试必须新会话 + 新 Job + 新 `Idempotency-Key`，结果覆盖原行。超时先 cancel 再记失败。

## 11. 并发与续跑

- `parallel` 来自配置：`ThreadPoolExecutor(max_workers=parallel)`，每集独立 `httpx.Client` 与独立会话，并为该集登录一次（不共享 Cookie）。
- 每完成一集（`as_completed`，按完成顺序而非提交顺序）立即追加写 `results.csv`（进程内锁），崩溃后不丢已完成的集。
- `--resume <run_dir>`：读取已有 `results.csv`，跳过 `status == success` 的 `dataset`，其余重跑；结束后重建 `summary.csv` 与 `report.md`。

## 12. 自检与验收

`selftest.py`（无网络，`python selftest.py`，当前 29 项全过）：

1. 配置校验：缺项、`per_cell=0/6`、`parallel=0`、标量 `mechanisms`、`validate_selection` 的未知机制/D/N
2. 路径解析与产物名净化（`CAM/D010_N00500/rep00` → `CAM__D010_N00500__rep00`）
3. 抽样确定性：同 seed 抽样结果逐集一致，不同 seed 只是换选择、重跑同样可复现
4. npz → CSV：列名、行数、`%.8g` 格式、整型列
5. gold 提取：已知小矩阵边集
6. 指标：node id 映射、五种 `edge_type`、镜像对、反向边、空图、骨架口径
7. 交互答复：两类 interaction 的确定性输出、破坏性 plan 判拒
8. `report.md` 生成：包含分组表与失败分布
9. SSE 解析与心跳、`RETRYABLE` 集合、`build_prompt`、`_run_once` 无图记 `failed`

真实冒烟（人工，需栈已启动）：`--dry-run` 打印 350 集（14 机制 × 25 格，TimeLag 排除后）→ 单集跑通登录/上传/交互/出图/两 CSV/report.md。

## 13. 已知风险

- 模型是否触发 data_processing 交互取决于分支行为；runner 对零交互与多轮交互都能处理。
- SSE `interrupt` 事件短命，必须连接即读并按 `last_event_id` 回放。
- 模型偶发空回答导致无图，归入 `no_causal_graph` 重试 1 次。
- WSL 内存 6GB、`JOB_WORKERS` 默认 2：`parallel > 2` 只会排队且可能 OOM，启动摘要需提示。
- 1875 集全量耗时极长，默认分层抽样 `per_cell=1`（350 集）用于回归。
- 图中变量名若不是 `X{k}`（例如使用标签名），整图无法映射，会集体落入 `edge_variable_unmapped` / `partial`；冒烟时必须先确认这一映射。
