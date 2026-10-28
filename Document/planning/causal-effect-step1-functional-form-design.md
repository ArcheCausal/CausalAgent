# 因果效应估计 step 1：残差诊断与线性/灵活判定设计

- 日期：2026-10-28
- 分支：`feat(estimate)/causal-effect`（基线：step 0 完成并已 rebase 到 `origin/develop`，提交 `672ead3`）
- 上游清单：`Document/planning/causal-effect-estimation-gap-alignment.md`（C1、C3）
- 范围：只做推进顺序的 step 1；step 2–5 不在本文档内

## 0. 已拍板的决策

| ID | 决策 | 理由 |
|---|---|---|
| 触发条件 | 便宜信号先筛：`nonlinearity.ratio ≥ 1` 或 `max|spearman(resid, X)| ≥ 0.1` 或 `max|spearman(\|resid\|, X)| ≥ 0.1`，任一命中才补跑 g-formula | 每次效应问题都跑 200 次 bootstrap 代价过大；只看 ratio 会被数据集平均值稀释 |
| 残差实测位置 | MCP 返回实测值，Agent 侧做判定 | Agent 进程拿不到冻结 CSV；判定按 §0 留在 Agent 侧 |
| 判定落点 | 纯函数 `assess_functional_form()` + 收尾门禁，不新增工具 | 判定只需在收尾时消费已有结果；加工具会多一套账本语义且门禁仍要复核 |
| SE 口径 | 成对 bootstrap：每次重采样同时算线性路与灵活路，取差值分布标准差 | 两路用同一份数据、误差强相关；正交近似会把非线性判成线性 |
| g-formula 失败 | verdict=`undetermined`，允许以 OLS 收尾，报告必须声明“未完成”及真实终态 | 与 step 0“保留真实终态”一致；不把失败冒充成检出或未检出 |
| 报告可见性 | 最小事实块进事实链：verdict、触发信号、Δ 与 SE | 用户能看到选 OLS 的依据；E2/E3 全套口径留到 step 5 |

## 1. 现状

- 数据集级信号已存在：`Agent/Processing/nonlinearity.py::measure_nonlinearity` 在 fold 阶段写入 `analysis_parameters["nonlinearity"]`（`Agent/causal_agent/nodes.py:1036`），payload 含 `ratio` / `verdict` / `n_vars` / `n_rows` / `sampled` / `reason`，并已进入报告分析上下文（`Agent/causal_agent/analysis_context.py:465`）。`ratio ≥ 1` 在 d5 25 集上 0 误报、0 漏报；`ratio < 1` 不等于线性。
- 后门 OLS 内核 `Agent/CausalAgentMCP/inference/ate.py::estimate_ate` 已算出残差（用于 SE），但只返回 `estimand/treatment/outcome/adjustment_set/estimate/standard_error/ci95/estimator/n_rows`。
- g-formula 内核 `Agent/CausalAgentMCP/inference/g_formula.py::estimate_g_formula` 已做 bootstrap（重采样行 → 重跑调整后直接标准化），但每次重采样只算灵活路。
- `Diagnostics.metrics: dict[str, float | int | str | bool | None]`（`Agent/deep_agent_tools/models.py:180`）是通用实测值容器；工具消息用 `result.model_dump()` 全量下发（`Agent/deep_agent_tools/runtime_updates.py:482`），metrics 天然对模型可见。
- 收尾门禁 `Agent/deep_agent/finalization.py` 已有 step 0 的诊断/估计规则（`effect_question_without_diagnose`、`effect_question_without_estimation`、`primary_not_estimation_result`、`estimation_graph_not_diagnosed`），没有任何函数形式判据。
- 系统提示 `Agent/deep_agent/prompts.py::MANDATORY_ALGORITHM_INSTRUCTION` 只要求“诊断 → 估计”，不要求对比线性与灵活两路。

## 2. 目标架构

```
fold（已有）
  analysis_parameters["nonlinearity"] = {ratio, verdict, ...}

Deep Agent 流程
  effect_diagnose（结构）
    → causal_backdoor_ols
        → AlgorithmResult.diagnostics.metrics:
            residual_spearman_max, residual_abs_spearman_max
    →（信号触发时）causal_g_formula（同 contrast）
        → AlgorithmResult.diagnostics.metrics:
            linear_estimate, effect_difference, difference_se
    → FinalAnalysisDecision

收尾（finalization）
  纯函数 assess_functional_form(nonlinearity, ols_metrics, flex_metrics, flex_status)
    → verdict ∈ {not_detected, linear, nonlinear, flexible_primary, undetermined}
  门禁两条新规则 + step 0 既有规则
  → 报告分析上下文 functional_form 事实块
```

## 3. 组件

### 3.1 MCP `backdoor_ols`：残差实测

`estimate_ate` 在拟合后补算，仅当设计矩阵为 (n×k) 且全部列有限时：

- `residual_spearman_max = max_k |spearman(resid, X_k)|`
- `residual_abs_spearman_max = max_k |spearman(|resid|, X_k)|`

`X_k` 取设计矩阵列（treatment + 调整变量）。返回 payload 增加 `diagnostics_metrics` 字典；runner `_backdoor_ols` 原样带出，`runner_version` 由 `backdoor-ols-runner-v2` 升为 `backdoor-ols-runner-v3`。

只给实测，不给结论；阈值由 Agent 侧判定。

### 3.2 MCP `g_formula`：成对 bootstrap

每次重采样同时拟合并计算两路效应：

- 线性路：以 `treatment + parents(treatment)` 为设计矩阵的 OLS，取 treatment 系数（与 `backdoor_ols` 同式同列）。
- 灵活路：调整后直接标准化，结局方程按 `outcome_model`，调整集为处理变量的全部父节点。
- 每折差值 `d_b = effect_lin_b − effect_flex_b`。

输出到 `diagnostics_metrics`：

- `linear_estimate`：全样本线性路效应
- `effect_difference`：全样本 `effect_lin − effect_flex`
- `difference_se`：`sd(d_b)`（bootstrap ≥ 2 时；否则缺省）

bootstrap 次数、种子仍固定为服务端行为，不新增模型可传参数。`runner_version` 由 `g-formula-runner-v1` 升为 `g-formula-runner-v2`。

### 3.3 `service._estimation_result`：metrics 传递

从 runner payload 读取 `diagnostics_metrics`（必须是 `Mapping`，键值只接受有限数值），合并进 `Diagnostics.metrics`。两个估计能力共用，不改变 `AlgorithmResult` 契约。

### 3.4 Agent 侧纯函数 `Agent/deep_agent_tools/functional_form.py`

常量集中在此，门禁、提示词、报告共用：

```python
_SIGNAL_FLOOR = 0.1     # 任一残差相关绝对值达到即视为异常
_RATIO_LINE = 1.0       # 与 Agent/Processing/nonlinearity.py 的判据线一致
_DIFF_RATIO = 0.5       # |Δ| < 0.5·SE 视为一致
```

输入：`analysis_parameters["nonlinearity"]`、当前变量对的有效 OLS 结果 metrics、当前变量对的有效 flex 结果及终态。

输出：

| 字段 | 含义 |
|---|---|
| `verdict` | `not_detected` / `linear` / `nonlinear` / `flexible_primary` / `undetermined` |
| `signals` | `{"dataset_ratio": float|None, "residual_spearman": float|None, "residual_abs_spearman": float|None}` |
| `comparison` | `{"linear_estimate", "effect_difference", "difference_se"}` 或 None |
| `reason` | `undetermined` 时的机器可读原因（`flex_timed_out` / `flex_not_applicable` / `flex_failed` / `difference_se_unavailable`） |

纯函数、无 IO、不抛异常；非法或缺失输入按 `undetermined` 处理并给 reason。

### 3.5 收尾门禁（`finalization.py`）

新增两条规则，语义在 `FinalizationGate.validate` 的效应判定段之后：

1. `form_check_not_attempted`：效应问题 + 信号触发 + 当前批次没有任何 flex 终态（成功、超时、不适用、失败都算尝试）→ 拒绝，重试提示要求用同一图与同一 contrast 调用 `causal_g_formula`。
2. `linear_primary_despite_nonlinear_verdict`：verdict = `nonlinear` 但 `primary_result_ref` 不是 flex 的有效结果 → 拒绝，重试提示要求以灵活估计作为主结果。

step 0 规则保持不变；`undetermined` 只影响报告声明，不阻断。

### 3.6 系统提示（`prompts.py`）

强制段追加：估计结果 `diagnostics.metrics` 的残差相关达到 `_SIGNAL_FLOOR`、或 `analysis_parameters.nonlinearity.ratio` 达到 `_RATIO_LINE` 时，必须再调用 `causal_g_formula`（contrast 与 OLS 口径一致）。阈值由常量格式化进字符串，避免与门禁漂移。门禁从 metrics 重算，不依赖模型是否听话。

### 3.7 报告事实链

`build_report_analysis_context` 增加 `functional_form` 块：`verdict`、`signals`、`comparison`、`reason`。报告提示词要求：

- `not_detected` 写“未检出非线性”，不得写“已确认线性”；
- `linear` 写“线性与灵活估计一致”并给出 Δ、SE；
- `nonlinear` 写“线性近似偏离”并说明主结果取灵活估计；
- `flexible_primary` 写“采用灵活估计，未做线性对比”；
- `undetermined` 写“非线性检查未完成”并写明真实终态或缺失原因。

报告内容不逐字硬编码；用测试锁住四种措辞的边界。

## 4. 判定矩阵

| 条件 | verdict |
|---|---|
| OLS 与 flex 均有效，`|Δ| < 0.5·SE`（两路都在时总是比较，与是否触发无关） | `linear` |
| OLS 与 flex 均有效，`|Δ| ≥ 0.5·SE` | `nonlinear` |
| 两路都有效但 `difference_se` 缺失 | `undetermined`（reason = `difference_se_unavailable`） |
| 仅 flex 有效（无 OLS 基线） | `flexible_primary` |
| 仅 OLS 有效，信号未触发 | `not_detected` |
| 仅 OLS 有效，flex 已尝试但终态非有效 | `undetermined`（reason = 真实终态） |

信号是否触发在“无 OLS 结果”时只由 `dataset_ratio` 决定；`ratio` 缺失或 `verdict=insufficient` 视为无数据集信号，并在 `signals` 中如实置 `None`。

同一变量对存在多份有效估计时，按 `(capability_id, result_ref)` 字典序取第一份，并在 `signals` 中保持确定性；判定只使用 `(graph_result_ref, treatment, outcome)` 一致的结果。

## 5. 非目标

- A2/B7/C4：`treatment_type` 决定默认对比与尺度、样本量门槛
- C2/C5/C6：10 折 CV 对比、逐方程门禁与路径聚合、二值/计数/有序结果类型
- C4 的“样本量不足直接拒绝”
- E2/E3 全套口径字段（`cv_delta`、`overlap`、`scale`、`unit` 等）
- 前端展示、沙箱预处理链路、Deep Agent 路由策略

## 6. 测试

- 纯函数矩阵逐行单测：六种判定、缺失 metrics、`difference_se=None`、非法值不抛异常。
- `estimate_ate` 残差实测：线性合成数据低相关；`y = x²` 高 `residual_spearman`；异方差数据高 `residual_abs_spearman`。
- `estimate_g_formula` 成对 bootstrap：固定种子两次调用一致；线性 DAG 上 `|Δ|` 小；已知非线性 DAG 上 `|Δ|` 大且 `difference_se` 有限。
- 门禁：触发未尝试 → `form_check_not_attempted`；nonlinear 但 primary 是 OLS → `linear_primary_despite_nonlinear_verdict`；`undetermined` 放行；step 0 四条规则无回归。
- 事实链 round-trip：`build_report_analysis_context` → commit → restore 后 `functional_form` 块一致。
- 全量 unit + integration 通过；schema 快照无需变更（没有契约字段变化，只有 runner_version 与 metrics 值）。
- 手动验收两个真实 job：线性数据集应为 `not_detected` 或 `linear`；非线性数据集应触发、判 `nonlinear`，且主结果变为 g-formula。

## 7. 风险与取舍

- **阈值未标定**：三个常量是首版经验值，集中在一处；真实 job 后可调，报告措辞不得写死阈值。
- **额外成本**：g-formula 只在触发时运行；成对 bootstrap 每折多一条 OLS，相对 HGB 重拟合可忽略。
- **prompt 体积**：每个估计结果多十几个浮点数 metrics，可接受。
- **确定性**：成对 bootstrap 与现有 g-formula 一样固定种子，可复现。
- **回滚**：runner_version、门禁规则、提示词均为独立改动，可按提交回滚。
