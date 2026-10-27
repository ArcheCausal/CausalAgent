# 因果效应估计 step 0：架构落地设计

- 日期：2026-10-27
- 分支：`feat(estimate)/causal-effect`（基线提交 `8a9760a`）
- 上游清单：`Document/planning/causal-effect-estimation-gap-alignment.md`
- 范围：只做该清单「建议推进顺序」的 step 0；step 1–6 不在本文档内

## 0. 已拍板的决策

| ID | 决策 |
|---|---|
| 范围 | 只做 step 0：`effect_diagnose` + 收尾门禁判据 + `causal.g_formula` + `causal.ate` 改名 |
| 决策记录来源 | 新增 Agent 侧非算法工具 `effect_diagnose`，终态 ledger 记录即决策记录 |
| U1 | `causal.ate` 改名为 `causal.backdoor_ols`（内核不变） |
| U2/U3 | `g_formula` 只收单值 `outcome_model`；中间方程一律线性，偏差写入 limitations |
| U4 | 需要独立工具 `effect_diagnose` |
| U5 | 首版只支持 `contrast=[x0, x1]` |
| 门禁语义 | 严格：诊断为可估计时必须有真实估计调用 |
| 图数一致性 | 完整一致：主结果必须是估计结果，且其源图必须有成功诊断记录 |
| 模型族 | `g_formula` 首版只开 `linear` / `hgb` |
| CausalEffect | 现在就加 `contrast` / `outcome_model` / `bootstrap_n` 三个可选字段 |

## 1. 现状

- 能力池只有 `causal.ate`（`Agent/deep_agent_tools/algorithm_specs.py:544`），内核 `Agent/CausalAgentMCP/inference/ate.py::estimate_ate` 为单方程后门 OLS，解析 SE。
- 收尾门禁 `Agent/deep_agent/finalization.py:555` 只认一次 `causal_ate` 终态调用，不认任何 Agent 侧判定。
- 预处理阶段 `Agent/causal_agent/nodes.py:1036` 已把数据集级 `nonlinearity` verdict 写入 `analysis_parameters`；该模式用于处理无图依赖的数据级事实，但拿不到因果图。
- 运行期传入 Deep Agent 的工具在 `app/agent/worker/runtime.py` 的 `domain_tools` 装配；非算法工具已有 `WebEvidenceTool` / `RagEvidenceTool` 先例，可读写 `ToolRuntime.state` 并通过 `build_tool_command` 写终态 ledger。
- 报告只展示 `primary_result_ref` 对应的图（`Agent/causal_agent/analysis_context.py::_primary_algorithm_summary`）；估计结果按自身源图语义校验（提交 `7119745`），但没有任何规则要求「估计源图 == 主图」。

## 2. 目标架构

```
Agent 侧（host process）
  effect_diagnose (StructuredTool)
    ├─ 读 state.algorithm_results[ref].standardized_graph
    ├─ 读 state.analysis_parameters / trusted analysis_input.semantic_kinds
    ├─ 结构检查 → verdict
    └─ 终态 ledger：succeeded = 可估计 / not_applicable = 拒绝 / failed = 异常

MCP 侧（算法池）
  causal.backdoor_ols   ← causal.ate 改名，内核 estimate_ate 不变
  causal.g_formula      ← 新增 g-computation + bootstrap

finalization
  ├─ 效应问题 + 有效图 → 必须有终态 effect_diagnose
  ├─ 诊断 succeeded → 必须有终态估计调用
  └─ 有有效估计 → 主结果必须是估计，且其源图有 succeeded 诊断
```

原则沿用 §0：**MCP 只存算法，判定规则留在 Agent 侧**。`effect_diagnose` 的判定全部是确定性代码，LLM 只决定「调用哪个 ref、何时调用、拿到 verdict 后调哪个估计器」。

## 3. effect_diagnose

### 3.1 工具契约

- 模块：`Agent/deep_agent_tools/effect_diagnose_tool.py`
- 输入（模型可见）：`graph_result_ref`、`treatment`、`outcome`；均为非空白字符串
- 装配：`app/agent/worker/runtime.py` 的 `domain_tools` 追加 `build_default_effect_diagnose_tool()`；常驻可见，不做场景开关
- 状态：每次调用必写终态 ledger（`build_terminal_invocation`），`subject_ref = graph_result_ref`

### 3.2 校验与检查

1. `graph_result_ref` 在 `state.algorithm_results` 中存在、`status == "valid"`、`standardized_graph` 非空
2. 结果 provenance 与当前可信身份一致：job / attempt / lease / input_identity / source_input_identity / analysis_dataset_id
3. `treatment != outcome` 且两者都在图的节点中
4. 所有边 `edge_type == "directed"`
5. 图无有向环（Kahn）
6. `treatment` 是 `outcome` 的祖先

step 0 检查范围与现有 MCP 内核一致（全图）；Y 祖先子图收窄属于 B1，留到 step 2 与内核一起改，避免同一轮出现「诊断放行、估计拒绝」。

### 3.3 输出 payload

```json
{
  "verdict": "proceed",
  "treatment_type": "continuous",
  "n_rows": 1000,
  "checks": {
    "reference_valid": true,
    "variables_present": true,
    "directed_only": true,
    "acyclic": true,
    "treatment_is_ancestor": true
  },
  "reasons": [],
  "recommended_estimator": "causal.backdoor_ols",
  "graph_result_ref": "...",
  "treatment": "X",
  "outcome": "Y"
}
```

- `treatment_type` 取自可信 `analysis_input.semantic_kinds`；缺失为 `"unknown"`，不参与判定
- `n_rows` 取自 `analysis_parameters.data_profile.row_count`，缺失为 `null`
- `recommended_estimator` 在 step 0 恒为 `causal.backdoor_ols`；step 1 加入线性/灵活判定后才会返回 `causal.g_formula`
- `reasons` 为 `[{"code": ..., "message": ...}]`，code 稳定、message 面向模型

### 3.4 终态映射

| 情况 | attempt / final status |
|---|---|
| 六项检查全部通过 | `succeeded` |
| 任一项不通过 | `not_applicable` |
| ref 缺失、校验异常、内部错误 | `failed` |

step 0 不新增 State 字段；payload 保存在 ToolMessage 与 checkpoint 中。报告事实链接线（E2）时再决定是否落 `effect_diagnoses`。

## 4. 收尾门禁

`Agent/deep_agent/finalization.py` 的三条新规则取代原 `effect_question_without_ate_attempt`：

| 条件 | 要求 | rule |
|---|---|---|
| `route_decision == "fold"` 且 `valid_refs` 非空 且 `_requires_effect_estimation(analysis_parameters)` | 当前 attempt 存在终态 `effect_diagnose`（接受 `succeeded` / `not_applicable`） | `effect_question_without_diagnose` |
| 上述条件成立且任一次诊断为 `succeeded` | 当前 attempt 存在终态估计调用（`causal_backdoor_ols` 或 `causal_g_formula`；接受 `succeeded` / `not_ready` / `not_applicable` / `failed` / `timed_out`） | `effect_question_without_estimation` |
| `outcome == "algorithm_supported"` 且存在有效估计结果 | `primary_result_ref` 必须是有效估计结果；其 `causal_effect.graph_result_ref` 必须对应一条 `succeeded` 诊断记录（匹配 `subject_ref`） | `primary_not_estimation_result` / `estimation_graph_not_diagnosed` |

实现要点：

- 诊断记录不是算法记录：`_current_ledger` 会过滤非算法 record，因此诊断检查与 Web 证据一致，扫描完整 `ledger`，并按 job / attempt / lease / worker / input_identity / source_input_identity / analysis_dataset_id 过滤
- 估计结果的身份由 `capability_id` 反查 `get_algorithm_spec(...).kind == "causal_estimation"` 判定，不硬编码工具名
- 有效估计缺少 `causal_effect` 属于内部契约破坏，直接抛 `StructuredResponseError`（rule：`estimation_result_missing_effect`）
- 新增三条 retry hint，文案要求：先诊断、再估计、主结果换成估计结果；不得编造调用
- 原有「效应问题必须有一次 ATE 尝试」中的 `causal_ate` 字样、提示文案、测试全部替换

### 4.1 图数一致性示例

- 诊断 PC 图 → 成功；估计 CDFM 图 → 成功；主结果选 PC 图 → 拒绝（`primary_not_estimation_result`）
- 诊断 PC 图 → 成功；估计 CDFM 图 → 成功；主结果选 CDFM 估计 → 通过（报告展示 CDFM 源图）
- 诊断 PC 图 → 拒绝；无估计调用；主结果选 PC 图 → 通过（拒绝本身是合法结论）
- 诊断 PC 图 → 成功；无估计调用 → 拒绝（`effect_question_without_estimation`）

## 5. 改名与规格

### 5.1 causal.ate → causal.backdoor_ols

| 字段 | 旧 | 新 |
|---|---|---|
| capability_id | `causal.ate` | `causal.backdoor_ols` |
| tool_name | `causal_ate` | `causal_backdoor_ols` |
| concurrency_key | `causal_ate` | `causal_backdoor_ols` |
| version | `1.0` | `2.0` |
| adapter | `adapters/ate.py::AteAdapter` | `adapters/backdoor_ols.py::BackdoorOlsAdapter` |

内核 `estimate_ate`、返回字段、`estimator="backdoor_ols"`、`default_timeout_seconds=120` 全部不变。

波及文件：`Agent/deep_agent_tools/algorithm_specs.py`、`Agent/deep_agent_tools/adapters/__init__.py`、`Agent/CausalAgentMCP/runner_registry.py`、`Agent/deep_agent/prompts.py`、`Agent/deep_agent/finalization.py`、约 10 个测试文件、2 个 schema 快照、`changelog/2026/2026-10.md`。

### 5.2 CausalEffect

`Agent/deep_agent_tools/models.py::CausalEffect` 增加三个可选字段：

```python
contrast: tuple[float, float] | None = None
outcome_model: str | None = None
bootstrap_n: int | None = Field(default=None, ge=0)
```

`backdoor_ols` 保持为 `None`。本轮不把这些字段接入报告事实链。

## 6. causal.g_formula

### 6.1 Spec

| 字段 | 值 |
|---|---|
| capability_id | `causal.g_formula` |
| tool_name | `causal_g_formula` |
| version | `1.0` |
| kind | `causal_estimation` |
| requires | `{"standardized_graph"}` |
| produces | `∅` |
| default_timeout_seconds | 600 |
| concurrency_key | `causal_g_formula` |
| default_concurrency | 1 |
| graph_semantics | `causal_estimation_input` |
| matrix_convention | `g_formula` |
| result_contract | `CausalEffect` |

模型输入：

```python
class CausalGFormulaInput(ContractModel):
    graph_result_ref: str
    treatment: str
    outcome: str
    contrast: tuple[float, float]
    outcome_model: Literal["linear", "hgb"] = "linear"
    bootstrap: int = Field(default=200, ge=0, le=2000)
```

随机种子由服务端固定（常量 0），模型不可传。

### 6.2 内核

模块 `Agent/CausalAgentMCP/inference/g_formula.py`，函数 `estimate_g_formula(...)`：

1. 结构前置：全边 `directed`、无环、变量在图中、`treatment != outcome`、treatment 是 outcome 祖先；相关列存在且为有限数值。不满足抛 `GFormulaNotApplicable`
2. 相关节点 = `ancestors(outcome) ∪ {outcome}`；按拓扑序对每个非 treatment 节点拟合 `node ~ 相关父节点`；结局方程用 `outcome_model`（`LinearRegression` 或 `HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, random_state=0)`），中间方程一律 `LinearRegression`
3. do 模拟：固定 `treatment = x0` 与 `x1` 各传播一次，按拓扑序预测；父节点取实测值或已预测值；结局取其预测均值
4. `effect = mean(Y|do(T=x1)) - mean(Y|do(T=x0))`
5. bootstrap：行重采样 → 重拟合 → 重算效应；`standard_error` 取 bootstrap 标准差，`ci95` 取 2.5/97.5 分位；`bootstrap=0` 时两者为 `None`
6. 返回 `CausalEffect`：`estimand="ATE"`、`estimator="g_formula"`、`n_rows`、`contrast`、`outcome_model`、`bootstrap_n`；`graph_result_ref` 由 `service._estimation_result` 注入

### 6.3 接线

- `Agent/CausalAgentMCP/runner_registry.py` 新增 `_g_formula`，返回结构与 `_ate` 一致：`schema_version="causal_estimation_v1"`、`success`、`algorithm="g_formula"`、成功时 `causal_effect`
- `Agent/deep_agent_tools/adapters/g_formula.py::GFormulaAdapter`，`validate_input` 同 `AteAdapter`
- `Agent/CausalAgentMCP/service.py::_estimation_result` 的 diagnostics 文案「后门调整估计完成」改为通用「效应估计完成」
- 依赖：sklearn 已在 `causal-mcp` 镜像内验证可导入

### 6.4 Limitations（写入 spec）

- 中间方程固定线性；真实中介关系非线性时总效应有偏
- 不处理二值/计数/有序结局的尺度转换（风险差/率差/累积概率差属于 step 3/5）
- bootstrap 为 percentile 区间，不做 BCa
- 不处理未观测混杂、选择偏倚、测量误差

## 7. 提示词

`Agent/deep_agent/prompts.py` 的强制指令改为两步：

1. `analysis_parameters` 同时有 target 与 treatment，且已获得有效标准图 → 必须调用一次 `effect_diagnose`，携带该图的 `graph_result_ref` 与 target/treatment
2. 诊断返回 `succeeded` → 用同一张图调用 `recommended_estimator`，保留真实终态（成功、未就绪、不适用、失败都算一次尝试）
3. 诊断返回 `not_applicable` → 保留拒绝理由，不再强制估计；最终主结果不得指向估计

## 8. 测试与验收

### 8.1 单元测试

- `effect_diagnose`：proceed / 每一项 reject；ref 不存在、ref 不是 valid、provenance 不匹配、变量缺失；status 与 `subject_ref` 写入正确；payload 字段齐全
- `finalization`：三条新规则各一正一反；`primary_not_estimation_result`；`estimation_graph_not_diagnosed`；纯拒绝路径通过；旧 rule 名不再存在
- `g_formula` 内核（合成数据）：
  - 线性 DAG 上 g_formula(linear) 的点估计与 `estimate_ate` 的系数接近（同一数据同一 DAG，n=10000，`abs(diff) < 0.02`；两者是不同估计方程，有限样本下不要求逐位相等）
  - 非线性 DAG 上 g_formula(hgb) 与 g_formula(linear) 的差显著（超出 linear 的 CI）
  - bootstrap 区间包含点估计；同种子两次调用结果完全一致
  - 非 directed 边、有环、treatment 非祖先、缺列 → `GFormulaNotApplicable`
- 改名：`get_algorithm_spec("causal.backdoor_ols")` 可解析；旧名抛 `unknown algorithm capability`

### 8.2 快照与集成

- 用 `tests/unit/agent/regenerate_schema_snapshots.py` 重新生成 `deep_agent_schema_snapshot.json` 与 `deep_agent_full_schema_snapshot.json`
- 集成测试覆盖诊断 → 估计 → 收尾的完整 ledger 流（可用 fake executor / 现有测试夹具）

### 8.3 手工验收

真实 job 跑一个效应问题：

1. ledger 有 `effect_diagnose` 终态与估计终态
2. `primary_result_ref` 指向估计结果
3. 报告展示的图 = 估计结果携带的源图
4. 构造拒绝场景（treatment 非 outcome 祖先），确认无估计调用也能通过收尾，且报告写明拒绝原因

## 9. 风险与回滚

- **改名风险**：旧 `causal_ate` 名称在 15 处左右被引用；一次性改齐，靠 schema 快照与全量测试兜底。回滚以提交为单位
- **bootstrap 成本**：200 次重拟合，timeout 600 秒；hgb 单方程在小数据上可接受，数据大时可能超时，返回真实 `timed_out`，不阻断收尾
- **ledger 新字段**：`InvocationRecord.subject_ref` 为可选字段，旧 checkpoint 缺失时为 `None`，不影响既有校验
- **一致性门禁**：主结果被强制为估计结果的场景可能改变已有报告的展示，需在真实数据集上复验
- **向后兼容**：`_requires_effect_estimation` 判据不变；只有门禁从「ATE 调用」改为「诊断 + 条件估计」

## 10. 非目标

- step 1：残差诊断、线性/灵活判定（C1/C3）
- step 2：`treatment_type` 贯通、Y 祖先子图收窄、样本量规则（A2/B1/B7/C4）
- step 3：CV 对比、逐方程门禁与路径聚合、结果类型分支（C2/C5/C6）
- step 4：g-formula 的 bootstrap 已在 step 0 提供；D2 系数复用仍留待后续
- step 5：请求形态（平移/曲线）、报告字段接线（E2/E3/E5）、工具接口整合（F1）
- 前端展示、沙箱预处理链路、Deep Agent 路由策略
