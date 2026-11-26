# evaluation-runner 新指标设计：F1 / SHD / AUROC / AUPRC

> 状态：已评审
> 日期：2026-10-31
> 分支：`feat-evaluation`（基于 develop）
> 背景：`evaluation-runner-design.md`（端到端流程与既有指标口径）

## 1. 目标

在 `evaluation-runner/` 现有严格定向 P/R 与骨架 P/R 之外，增加四个指标：

- **F1**：严格定向 precision/recall 的调和平均。
- **SHD**：预测主图与 gold DAG 的结构汉明距离。
- **AUROC / AUPRC**：CDFM 主结果时的概率排序质量。

并为 AUROC/AUPRC 在上游建立最小、可校验的分数通路。

## 2. 非目标

- 不评 LiNGAM 系数或 PC 的 P 值（与概率不可混比）。
- 不改现有 P/R、骨架口径、失败分类与重试逻辑。
- 不引入 sklearn；AUROC/AUPRC 用 numpy 实现。
- 不在非 CDFM 主结果时构造伪分数或做受限空间的 AUROC。

## 3. 指标定义

### 3.1 F1

`f1 = 2 * precision * recall / (precision + recall)`；分母为 0 时记 0。使用现有严格定向 P/R（镜像对已按一对无向边处理）。

### 3.2 SHD

记预测有向边集合 `D`（已去镜像对）、无向类边与镜像对集合 `U`、gold 有向边集合 `G`。反转/无向各计 1：

- `matched = D ∩ G`
- `reversed = {(i,j) ∈ D | (j,i) ∈ G 且 (i,j) ∉ G}`
- `extra = D − matched − reversed`
- `covered_gold = matched ∪ {(j,i) | (i,j) ∈ reversed} ∪ {g ∈ G | frozenset(g) ∈ skeleton(U)}`
- `missing = G − covered_gold`
- `shd = |reversed| + |extra| + |U| + |missing|`

说明：无向边无论骨架是否命中都计 1；若骨架命中，只抵消一条 gold，不额外计缺失。自环按多余边计 1（沿用现有 FP 口径）；端点无法映射的边丢弃。

### 3.3 AUROC / AUPRC

适用条件：主图资产带 `score_type == "probability"` 与 `edge_score_matrix`。其余情况两列留空。

- 节点重排：按 `asset.nodes[].variable` 解析 `X{k}`，重排为 `0..n-1`；任一节点无法映射或索引越界 → 两列留空（不判失败）。
- 样本空间：所有有序对 `(i,j), i≠j`；标签 `y=1 ⇔ (i,j) ∈ gold`；分数 `matrix[i][j]`。
- AUROC：秩统计（Mann–Whitney），并列取平均秩；`n_pos=0` 或 `n_neg=0` 时留空。
- AUPRC：average precision。按分数降序、并列分数分组，逐组累加 TP/FP，`AP = Σ (Δrecall × precision_at_group)`；正样本为 0 时留空。
- 阈值为 CDFM 输出阈值，只影响图构建，不参与排序指标。

## 4. 上游数据通路（契约扩展，方案 A）

### 4.1 契约

`Agent/deep_agent_tools/models.py` 的 `StandardizedGraph` 增加两个可选字段：

- `edge_score_matrix: list[list[float]] | None = None`
- `score_type: str | None = None`，本期只允许 `"probability"`

校验：两者同时为空或同时存在；矩阵为 `n×n`（`n = len(nodes)`），有限；`score_type == "probability"` 时取值在 `[0,1]`。

### 4.2 归一化

`Agent/deep_agent_tools/normalization.py::standardize_runner_graph`：当 capability 为 `causal.cdfm` 且 payload 含 `probabilities` 时填入，保留 6 位小数；缺失时留空，保持向后兼容。矩阵方向为行→列（`matrix[i][j]` 表示 `Xi → Xj`），与 `adjacency_to_edges` 一致。

### 4.3 分析事实

`Agent/causal_agent/analysis_context.py::normalize_algorithm_summary` 在 `graph` 字典中透传两个字段，使报告重生成与保存快照时分数不丢失。`ANALYSIS_FACTS_VERSION` 不变：字段为增量，旧快照自然缺分数。

### 4.4 报告资产

`Agent/Report/assets.py::build_causal_graph_model` 将分数写入 `CausalGraphModel.metadata`：

```
"score_type": "probability",
"edge_score_matrix": [[...], ...]
```

只有主图资产会携带，因此 AUROC/AUPRC 仅在 CDFM 为主结果时存在。报告 schema 版本不变；CDFM 主结果文档体积增加约 10–30KB（`n ≤ 30`）。

## 5. runner 改动

- `evaluate.py`：新增 `f1_score`、`shd`、`ranking_metrics`（AUROC/AUPRC）纯函数；沿用现有边解析结果。
- `runner.py`：
  - `RESULTS_HEADER` 新增 `f1`、`shd`、`auroc`、`auprc`，顺序为 `... recall, precision, f1, skeleton_recall, skeleton_precision, shd, auroc, auprc, final_algorithm ...`。
  - 成功行计算 F1/SHD；从主图 `metadata` 取分数矩阵，重排后计算 AUROC/AUPRC；缺失留空。
  - 失败行保持空值。
  - 旧表头继续由现有 `--resume` 保护拒绝。
- `report.py`：
  - `summary.csv` 新增 `f1_mean`、`shd_mean`、`auroc_mean`、`auprc_mean`、`auroc_n`、`auprc_n`；均值只统计有值行。
  - `report.md` 总体段增加 F1、SHD、AUROC/AUPRC（标覆盖数）；按机制、按 D、按 N 表增加 F1、SHD 两列；AUROC/AUPRC 只在总体展示。

## 6. 缺失与失败语义

- 非 CDFM 主结果：`auroc`、`auprc` 为空，`auroc_n`/`auprc_n` 不计入。
- 分数矩阵缺失、形状不符或节点无法映射：两列留空，不改变 `status`，不新增失败类型。
- `status=failed`：四个新指标全部为空。

## 7. 测试与验收

离线 `selftest.py` 新增：

1. F1：正常值、`P+R=0`、空图。
2. SHD：完全一致 0、缺边/多余/反转各 1、无向骨架命中 1、镜像对 1、无向骨架不命中 1。
3. AUROC/AUPRC：完美排序 1、反向排序 0、并列分数、无分数矩阵为空；AUPRC 用手算小样例。
4. 节点重排：节点乱序时按 `variable` 对齐；无法映射时返回空。

上游契约测试：

- `StandardizedGraph`：非方阵、取值越界、只给一半字段均报错。
- `standardize_runner_graph`：CDFM payload 提取概率；无 `probabilities` 时留空。
- `build_causal_graph_model`：分数进入资产 metadata。

端到端验收（需栈已启动）：

- CDFM 主结果单集：AUROC/AUPRC 非空且在 `[0,1]`；非 CDFM 主结果为空白。
- F1/SHD 在成功集上恒有值。
- `report.md` 分组表出现 F1/SHD，总体行出现 AUROC/AUPRC 与覆盖数。

## 8. 风险与取舍

- **概率与系数不可混比**：因此 `score_type` 目前只允许 `probability`；LiNGAM 系数不进入排序指标。
- **文档体积**：矩阵随报告持久化；`n≤30` 时可接受，若后续维度增大再考虑稀疏或截断。
- **上游改动面**：涉及模型、归一化、分析事实、报告资产四处，需同步契约测试；改动均为增量，不破坏旧字段。
- **覆盖不均衡**：AUROC/AUPRC 只覆盖 CDFM 主结果的子集，跨机制比较时需以 `auroc_n` 为准，不与 P/R 混比。
