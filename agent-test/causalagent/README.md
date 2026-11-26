# 端到端因果发现评测 runner

文档职责：记录本地对已启动的 CausalAgent 入口逐集执行因果发现、自动应答交互并按金标准打分的客户端、抽样口径、指标语义与产物。

适用范围：使用固定数据集验证已部署 CausalAgent 的 HTTP/SSE 因果分析流程。运行证据和模型评审规则见 [Agent 评测 AGENTS.md](../AGENTS.md)。接口见 [Job API](../../Document/api/agent-jobs.md) 和 [会话与文件 API](../../Document/api/chat-files.md)。

## 边界

- runner 在本机运行，只调用被测入口的公开 HTTP/SSE 接口；服务端资源与并发能力不在本工具的评价范围。
- 使用测试账号和测试数据。凭据保存在本地 `config.yaml` 或 `local/accounts.local.json`，两者均被 Git 忽略。
- 上传内容来自数据集的 `X` 矩阵，固定提示词不附加领域知识、不引导算法选择；是否触发数据处理交互由被测分支决定。
- 金标准取自数据集自带的 `adjacency`，不读取服务端中间结果，也不按报告文字人工判分。
- `local/` 用于存放运行产物。根 `.dockerignore` 从应用镜像的构建上下文排除整个 `agent-test/`。

## 安装

~~~powershell
Set-Location agent-test/causalagent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
~~~

命令从仓库根目录开始。后续命令在当前子目录内执行，WSL/Linux 使用 `.venv/bin/python`。数据集不随仓库分发，配置模板从当前子目录解析 `../datasets/cdfm_bench_D10_30_fixed_r5`。也可将 `data.dir` 设为本机已有数据目录的绝对路径。首次运行前检查 `run.base_url`、测试账号与样本范围。

## 常用命令

~~~powershell
# 只看抽样结果，不发请求
.\.venv\Scripts\python.exe runner.py --config config.yaml --dry-run

# 正常运行：先探活，再打印摘要并要求输入 y
.\.venv\Scripts\python.exe runner.py --config config.yaml

# 无人值守 / 脚本化运行，跳过确认
.\.venv\Scripts\python.exe runner.py --config config.yaml --yes

# 续跑：跳过 results.csv 中 status=success 的集，结束后重建汇总
.\.venv\Scripts\python.exe runner.py --config config.yaml --resume "local/runs/<run_id>"
~~~

`--dry-run` 只扫描、抽样并打印摘要（机制数、格数、总集数、`parallel`、输出目录），随后退出；它不探活、不登录、不发请求。正常运行的顺序是：打印摘要 → `GET /api/check_auth` 探活 → `y/N` 确认 → 登录 → 建 Job。

## 配置

字段与默认值见 `config.example.yaml`：

| 字段 | 含义 | 默认 / 约束 |
| --- | --- | --- |
| `run.base_url` | 被测入口 | 必填；末尾 `/` 自动去掉 |
| `run.parallel` | 并发数据集数 | 1；正整数 |
| `run.timeout_minutes` | 单集总期限 | 15；正整数；在 SSE 循环内按事件到达检查 |
| `run.max_interactions` | 单集最多交互轮数 | 6；正整数 |
| `run.interaction_seconds` | 建 Job 起算的交互窗口上限 | 300；正整数 |
| `run.seed` | 抽样种子 | 代码默认 0，模板使用 20261030 |
| `run.backend_version` | 被测后端的已知版本（git sha / 镜像 tag） | 空字符串；写入清单并参与续跑校验 |
| `auth.username` / `auth.password` | 测试账号 | 留空时读 `local/accounts.local.json`；不存在则自动注册并存盘 |
| `data.dir` | `.npz` 根目录 | 必填，递归扫描 |
| `data.mechanisms` | 机制筛选 | `all` 或机制名列表；标量会被拒绝 |
| `data.exclude` | 排除机制 | `[TimeLag]`；必须是列表 |
| `data.dims` / `data.ns` | 选择测试数据的维度 D/样本数N 筛选 | 空列表；必须是列表 |
| `data.per_cell` | 每格抽样数 | 1；1..5 |
| `output.dir` | 运行产物根目录 | `./local/runs` |

`mechanisms`/`dims`/`ns` 会对照扫描到的实际数据校验，不存在的机制、D 或 N 直接报错；`exclude` 与它们重叠时以排除为准。

## 数据集与抽样

- 每个 `.npz` 必须含 `X (n_rows, dim)` 与 `adjacency (dim, dim)`；`adjacency[i,j]=1` 表示 `Xi → Xj`。缺键、形状不符、维度为 0 或 `gold` 为空记 `dataset_invalid`，不发请求。多余键忽略。
- `dataset` 标识为去 `.npz` 的相对路径，如 `CAM/D010_N00500/rep00`；落盘文件名把 `/` 换成 `__`。
- 路径解析：`mechanism = parts[-3]`，`parts[-2]` 形如 `D010_N00500`，文件名形如 `rep00`；解析失败留空，不阻塞。
- `dim` / `n_rows` 一律取 `X.shape` 实测值，名义 D/N 只用于筛选和报告分组。
- **分层抽样**：每个 `机制×D×N` 格内按 `blake2b(seed|dataset)` 排序取前 `per_cell` 个，确定性可复现；执行顺序按 `dataset` 字典序。
- `TimeLag` 默认排除：其 `adjacency` 是 lag-1 块，转成 CSV 后无法恢复时序。需要时从 `exclude` 移除。
- 完整的 `cdfm_bench_D10_30_fixed_r5` 使用模板预选时，排除 TimeLag 后每格取 1 集。以实际 `--dry-run` 输出为准。

## 端到端流程（单集）

1. 登录 `POST /api/login`；账号不存在时注册。写操作带 `X-CSRF-Token`。
2. 上传 CSV（multipart 字段 `file`，`text/csv`）→ `user_file_id`。
3. `POST /api/new_chat` → `session_id`。
4. `POST /api/agent/jobs`，body 含 `{message, session_id, input_user_file_id, web_search_enabled: false}`，头带 `Idempotency-Key`。
5. 订阅 `GET /api/agent/jobs/{job_id}/events`（SSE）。断流或传输错误时带最后事件 id 重连，最多 3 次；仍失败记 `sse_error`。超时先 `cancel` 再记 `timeout`。
6. `interrupt` 时自动应答：自由文本问题用数据集画像回答（行数、列数、取值范围、唯一值数、缺失情况，并声明目标是从匿名观测恢复因果结构）；方案确认按下面的安全规则批准或拒绝，随后 `POST /api/agent/jobs/{job_id}/resume`。
7. `final_result` 取报告；`error` / `canceled` 记失败。
8. 从报告取因果图、映射节点，计算严格定向/骨架指标与 F1/SHD/AUROC/AUPRC，追加写 `results.csv`，并保存 CSV 与原始事件证据。

固定提示词：

~~~
请读取上传的 CSV 数据文件，对变量 X0 到 X{dim-1} 执行因果分析
~~~

方案确认的安全规则：`variable_semantics` 必须覆盖全部变量，且 `transformations` 不得命中删除、筛选、聚合、重命名等破坏性动作（`drop/delete/删除/删行/删列/rename/重命名/aggregate/聚合/filter/筛选/新增/mutate`）。通过则提交 `{"action":"confirm","plan_version":<事件中的版本>}`；不通过则文本说明拒绝理由。当前分支只会产生 `data_processing_question` 与 `data_processing_plan_confirmation` 两类交互，其他类型按自由文本问题处理。

## 指标口径

金标准 `gold = {(i,j) | adjacency[i,j]=1}`。

取图：`final_result.data.document`；`report_mode == "diagnostic"` 直接记 `diagnostic_report`（`failed`，不重试）。否则在 `blocks` 中找 `type == "causal_graph"` 的 block，经 `asset_key` 取 `assets` 中的图；找不到则退化为带 `graph_id` 的资产；仍无记 `no_causal_graph`（`failed`，可重试 1 次）。

节点映射：`asset.nodes[].id → variable`，变量必须匹配 `^X(\d+)$` 且索引小于 `dim`；边端点先映射为索引。无法映射的边丢弃并记 `edge_variable_unmapped`，该集 `status=partial`。

| 边类型 | 计入 |
| --- | --- |
| `directed` | 有向边；与金标准同向命中计 TP |
| `undirected` / `bidirected` / `partially_directed` / `partially_oriented` | 无向类边，每条计 1 个 FP |
| `(i,j)` 与 `(j,i)` 同时 directed | 视为一条无向边（镜像对），计 1 个 FP、0 TP |

- 严格定向：`recall = TP / |gold|`；`precision = TP / (有向边数 + 无向类边数 + 镜像对数)`，分母为 0 记 0。
- 骨架：所有边无向化后取点对集合，重复点对只计一次。骨架 precision 除以预测点对数，骨架 recall 除以 gold 点对数。
- 空图：全部记 0。
- F1：严格定向 precision / recall 的调和平均，分母为 0 记 0。
- SHD：缺失 + 多余 + 反转；反转、无向、镜像各计 1，骨架命中的无向边抵消一条 gold。
- AUROC/AUPRC：仅主图为 CDFM（资产带 `score_type=probability`）时计算；样本空间为全部有序对 `i≠j`；非 CDFM 主结果留空，不与 P/R 混比。
- `diagnostic` 报告不计入均值。`partial` 表示部分边无法映射，仍计入指标均值。与通用 Agent 结果比较时，先核对计分状态和图指标的定义。

## 产物

运行目录 `output.dir/<run_id>/`，`run_id = YYYYMMDD-HHMMSS-<git short sha>`；`--resume` 时沿用被续跑目录名。

| 路径 | 内容 |
| --- | --- |
| `results.csv` | 逐次尝试一行，33 列：`run_id, dataset, attempt, job_id, session_id, mechanism, rep, n_rows, n_cols, dim, lambda, n_gold_edges, n_pred_edges, n_undirected_edges, recall, precision, f1, skeleton_recall, skeleton_precision, shd, auroc, auprc, final_algorithm, final_confidence, final_selection_rationale, finalization_status, interactions, duration_s, tokens_input, tokens_output, tokens_total, status, failure_reason` |
| `summary.csv` | 单行汇总，30 列，新增 `f1_mean, shd_mean, auroc_mean, auprc_mean, auroc_n, auprc_n, tokens_input_mean, tokens_output_mean, tokens_total_mean, tokens_n`；`succeeded` 含 `success` 与 `partial`，均值只统计成功行，失败不补零；排名指标按有值行求均值，`auroc_n`/`auprc_n` 为覆盖集数 |
| `report.md` | 运行信息、总体均值（含 F1/SHD 与 AUROC/AUPRC 及覆盖集数）、按机制 / 按实测 D / 按实测 `n_rows` 分组表（含 F1/SHD 列）、失败分布、recall 最低 10 集、口径声明 |
| `csv/<dataset 净化>.csv` | 实际上传的 CSV，列 `X0..X{n-1}`，`%.8g` |
| `raw/<dataset 净化>.attempt<N>.jsonl` | 第 N 次尝试的全量 SSE 事件 |
| `raw/<dataset 净化>.attempt<N>.json` | 终态 payload 与交互次数；无终态时为失败原因 |
| `raw/<dataset 净化>.attempt<N>.requests.jsonl` | create / resume / cancel 的请求内容、UUID 与发送结果 |
| `manifest.json` | 运行身份清单：选中案例、数据 sha256、配置快照、客户端/指标/后端版本，供 `--resume` 校验 |

`summary.csv` 与 `report.md` 在运行结束后从 `results.csv` 重建；同一 dataset 出现多行时取最后一行，因此 `--resume` 重跑不会重复计数。

`results.csv` 每次尝试写一行；`summary.csv` 与 `report.md` 仍按 dataset 取最后一行。最后一行 `tokens_*` 是该数据集所有尝试的合计，其它行只写各自用量。表头变化后旧运行目录不能 `--resume`。

token 用量来自 Job 的 `usage` 事件：按 `(job, attempt)` 取每 attempt 累计最大值，再跨 attempt 与 runner 重试求和；无 LLM 用量的失败行留空。终态事件里的用量快照只服务 API 客户端，不重复计入。`lambda` 列保留但当前数据集不存在该字段，留空。

## 失败与重试

| `failure_reason` | 触发 | 重试 |
| --- | --- | --- |
| `dataset_invalid` | 缺键 / 形状错 / 维度为 0 / 无 gold | 否（不发请求） |
| `auth_error` | 登录或注册的 HTTP 失败 | 否 |
| `upload_error` | 上传或建会话失败（含传输失败） | 否 |
| `job_rejected` | 建 Job 非 2xx 或传输失败 | 否 |
| `timeout` | 超过 `timeout_minutes` 未终态 | 1 次 |
| `sse_error` | 断流后重连 3 次仍失败 | 否 |
| `terminal_error` | 收到 `error` 事件；或请求传输错误 / 未预期异常 | 1 次（逃出 `_run_once` 的传输错误除外，不予重试） |
| `canceled` | 收到 `canceled` 事件 | 否 |
| `resume_rejected` | resume 返回 409 或其它 4xx，无法证明答复已落库 | 否 |
| `no_causal_graph` | 报告无因果图资产 | 1 次 |
| `diagnostic_report` | `report_mode == diagnostic` | 否 |
| `interaction_budget_exceeded` | 超过交互轮数或时长 | 否 |

`edge_variable_unmapped` 不是失败，表示该集为 `partial`。重试会使用新会话、新 Job、新 `Idempotency-Key`；超时先 `cancel` 再记失败；重试结果追加为新行，汇总时同一 dataset 取最后一行。

重试只针对 `timeout / terminal_error / no_causal_graph`，最多 1 次。收到 interrupt 后先保存待发答复，不推进事件游标；resume 传输失败时用原 `Idempotency-Key` 重试，成功后才重放后续事件。建 Job 结果未知时先查活动 Job，再考虑用原键重试。

## 并发与续跑

- `parallel` 决定 `ThreadPoolExecutor` 的线程数。每集使用独立 `httpx.Client` 并单独登录一次，不共享 Cookie。
- 每完成一集（`as_completed`，按完成顺序）立即追加写入 `results.csv` 并 flush；进程中断不丢已完成的集。
- `--resume <run_dir>` 跳过 `status == success` 的集，其余重跑；`partial` 会重跑。结束后重建 `summary.csv` 与 `report.md`；已成功的旧行保留，失败旧行与新行按 dataset 取最后一行。
- 首次运行会在 `run_dir/manifest.json` 记录六项身份：选中案例、每个 npz 的 sha256、配置快照、客户端版本、`run.backend_version`、指标版本；另存 `git_commit`。
- `--resume` 默认硬校验这些身份；清单缺失或任一不一致时打印差异并以 1 退出，不写新行。
- `--force-resume` 只能与 `--resume` 同用；它绕过身份校验，并把差异写进 `report.md` 的“续跑身份差异”一节。表头不兼容仍然拒绝。
## 自检与验收

`selftest.py` 包含离线纯函数检查和模拟 HTTP 客户端用例。真实验收使用测试服务的 Job 和 SSE 证据。先在配置中将 `data.mechanisms`、`data.dims`、`data.ns` 缩至一个组合，设置 `data.per_cell: 1`，用 `--dry-run` 确认只选中一集。

## 已知限制

- 工具计算图指标，评审模型按任务目标审阅报告文字。运行时固定关闭 `web_search`。
- `diagnostic` 报告或缺少因果图按失败计；模型空回答导致的 `no_causal_graph` 只重试 1 次。
- 报告中变量名若不是 `X{k}`，整图无法映射，会集体落入 `partial` 或失败；冒烟必须先确认这一映射。
- 报告按实测 `n_rows` 分组，名义 N 仅用于抽样筛选，两者可能不一致。

## 目录

| 路径 | 作用 |
| --- | --- |
| `config.py` | 配置加载与字段校验 |
| `dataset.py` | npz 扫描、路径解析、分层抽样、CSV 落盘、数据画像、gold |
| `answer.py` | 交互答复与方案确认安全判断（纯函数） |
| `evaluate.py` | 取图、节点映射、严格定向/骨架指标与 F1/SHD/AUROC/AUPRC |
| `client.py` | HTTP 与 SSE 客户端、凭据与错误分类 |
| `manifest.py` | 运行身份清单的构建、写入与续跑校验 |
| `runner.py` | 单集流程、并发编排、续跑、CLI |
| `report.py` | `summary.csv` 与 `report.md` 生成 |
| `selftest.py` | 离线函数检查和模拟 HTTP 用例 |
| `config.example.yaml` | 配置模板 |
| `local/` | 账号、运行产物与 CSV/事件证据，整体 gitignore |
| `../datasets/cdfm_bench_D10_30_fixed_r5/` | 配置模板指向的数据目录，由使用者单独准备 |
