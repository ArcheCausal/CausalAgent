# 因果效应门禁 v1 设计

**目标：** 用户明确要求估计处理变量对结果变量的效应时，本次运行必须留下一次真实的 `causal_ate` 调用记录；门禁只保证覆盖与可追溯，不保证估计成功，也不替模型选算法。

**上游文档：** `Document/planning/causal-effect-estimation-v1-design.md`、`Document/development/causal-effect-estimation-v1-plan.md`。

**范围外：** 调度层自动补调、重试预算调整、强制 ATE 结果成为主结果、`no_valid_algorithm` 语义变更、CDFM 推理能力变更。

---

## 1. 背景与实测事实

2026-10-26 的 job `d4e0d62a-5c79-4f94-ad7f-bbb4304094e7`（问题："用因果知识分析上述数据集，并分析 T 到 Y 的因果效应"）暴露的问题：

| 事实 | 证据 |
|---|---|
| fold 已抽取处理/结果变量 | `analysis_contexts` 行 `treatment=T, target=Y`；deep agent checkpoint 的 `analysis_parameters` 含同值 |
| 工具白名单包含 ATE | deep agent 可见 `causal_pc / causal_direct_lingam / causal_cdfm / causal_ate` |
| 模型从未调用 ATE | `action_ledger`：web×2、`causal_cdfm`(failed)、`causal_direct_lingam`(valid)、`causal_pc`(valid) |
| 存在可用图 | 两张图 `status=valid` 且通过 `validate_graph_structure`（DirectLiNGAM 图含 T→age→Y） |
| 最终降级 | 模型两次提交 `outcome=no_valid_algorithm`；第二次被 `no_valid_algorithm_with_valid_result` 拒绝后重试耗尽，`finalization_status=degraded` |

结论：确定性层当前只约束**顺序**（估计前必须有 valid 图）与**账本/决策一致性**，不约束**问题类型到工具覆盖**。本次在 finalization 层补齐覆盖门禁，与现有"分析路由必须调用 `web_evidence_search`"规则同构。

## 2. 决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 约束层 | finalization 硬门禁 | 模型选型保持自主；门禁只要求一次真实尝试 |
| 触发条件 | `target`/`treatment` 都是真实列 + 问题含效应词 + 至少一张 eligible valid 图 | 避免误伤"分析 T 和 Y 的关系"；发现全失败时不白耗重试 |
| 问题文本来源 | 父 State 里最新 HumanMessage | `analysis_context.analysis_question` 在上下文复用时会保留旧问题 |
| 判定依据 | `action_ledger` 的终态记录 | 复用 `_has_current_terminal_tool_invocation`；`succeeded / not_ready / failed / timed_out / not_applicable` 都算尝试（`not_applicable` 仅对 ATE 规则放宽） |
| 失败处理 | 复用现有单次重试 | 不新增循环；提示同时说明 `evidence_only` 的正确用法 |

## 3. 触发谓词

```python
_EFFECT_INTENT_KEYWORDS = ("效应", "影响", "作用", "干预效果", "处理效应", "提升", "降低")

def _requires_effect_estimation(parameters, question) -> bool:
    if not isinstance(parameters, Mapping) or not isinstance(question, str):
        return False
    target = parameters.get("target")
    treatment = parameters.get("treatment")
    if not (
        isinstance(target, str) and target.strip()
        and isinstance(treatment, str) and treatment.strip()
    ) or target == treatment:
        return False
    columns = parameters.get("columns")
    if isinstance(columns, (list, tuple)) and columns and (
        target not in columns or treatment not in columns
    ):
        return False
    return any(keyword in question for keyword in _EFFECT_INTENT_KEYWORDS)
```

"至少一张 eligible valid 图"不进入谓词，由调用方用门禁已算出的 `valid_refs` 判定，避免重复实现图校验。

## 4. 接口改动

### 4.1 `Agent/deep_agent/finalization.py`

`FinalizationGate.validate()` 新增两个可选关键字参数：

```python
def validate(
    self,
    *,
    decision: Any,
    algorithm_results: Mapping[str, Any] | None,
    action_ledger: Mapping[str, Any] | None,
    trusted_identity: Any,
    rag_evidence: Mapping[str, Any] | None = None,
    web_evidence: Mapping[str, Any] | None = None,
    route_decision: str | None = None,
    web_search_enabled: bool | None = None,
    analysis_parameters: Mapping[str, Any] | None = None,
    analysis_question: str | None = None,
) -> FinalAnalysisDecision:
```

规则放在 `analysis_route_without_algorithm_result` 之后、`analysis_route_without_web_invocation` 之前：

```python
if (
    route_decision == ANALYSIS_ROUTE
    and valid_refs
    and _requires_effect_estimation(analysis_parameters, analysis_question)
    and not self._has_current_terminal_tool_invocation(
        current_ledger,
        tool_name="causal_ate",
        job_id=job_id,
        attempt_count=current_attempt,
        lease_epoch=current_lease,
        worker_id=current_worker,
        input_identity=current_input,
        terminal_statuses={
            "succeeded", "not_ready", "failed", "timed_out", "not_applicable",
        },
    )
):
    raise StructuredResponseError(
        "effect question requires a causal_ate attempt",
        rule="effect_question_without_ate_attempt",
    )
```

`FINALIZATION_RETRY_HINTS` 新增：

```python
"effect_question_without_ate_attempt": (
    "本次问题要求估计处理变量对结果变量的效应，且已有有效因果图。"
    "请携带其中一张图的 graph_result_ref、treatment、outcome 调用一次 causal_ate；"
    "即使你认为该图不适用，也保留真实返回的未就绪/不适用/失败状态。"
    "若最终不采用任何图，请改用 evidence_only 并把所有有效结果标记为 discarded。"
),
```

同时修改既有提示：

```python
"no_valid_algorithm_with_valid_result": (
    "outcome=no_valid_algorithm 时不能存在有效算法结果。"
    "若本次问题要求估计处理变量对结果变量的效应，还必须先留下一次 causal_ate 尝试"
    "（可引用其中一张有效图）。"
    "若你认为已有有效结果不可用，请改用 evidence_only，"
    "并把每个有效结果标记为 discarded。"
),
```

### 4.2 `Agent/causal_agent/graph.py`

`_finalization_gate_node` 调用处补传：

```python
analysis_parameters=state.get("analysis_parameters"),
analysis_question=nodes._latest_human_text(state),
```

`graph.py` 已导入 `nodes`，无需新增依赖。

### 4.3 `Agent/deep_agent/prompts.py`

`MANDATORY_ALGORITHM_INSTRUCTION` 追加一句：

> 若 `analysis_parameters` 中 target 与 treatment 都已指定，在获得有效标准图后必须调用 `causal_ate`；即使你认为该图不可信，也要保留一次真实的估计或不可估计结果。

## 5. 部署配置修复

`docker-compose.yml` 的 causal-mcp 服务：

```yaml
- CDFM_MODEL_PATH=${CDFM_MODEL_PATH:-/opt/cdfm-weights-image}
```

原因：镜像构建期已把校验过的权重写入 `/opt/cdfm-weights-image`，而 dev compose 默认值指向 HF Hub（`DMIRLAB/CDFM`），容器内访问受限导致 CDFM 每次 `LocalEntryNotFoundError`。staging 用命名卷 `/opt/cdfm-weights`，不受影响。

生效方式：`docker compose up -d --no-deps causal-mcp`，无需重建镜像。

## 6. 测试

| 测试 | 位置 | 断言 |
|---|---|---|
| 触发规则 | `tests/unit/agent/test_final_analysis_decision.py` | 三条件成立且账本无 `causal_ate` → `effect_question_without_ate_attempt` |
| 尝试即放行 | 同上 | `causal_ate` 终态为 `not_ready` 时通过 |
| 不触发 | 同上 | 无效应词 / treatment 缺失 / 无 valid 图时通过 |
| 重试路径 | 同上 | 首次失败返回重试指令，二次失败走现有 degraded |
| 接线 | `tests/unit/agent/test_deep_agent_parent_graph.py` | `_finalization_gate_node` 把 `analysis_parameters` 与最新 human 文本传入 gate |
| 部署配置 | `tests/contracts/deployment/test_mcp_compose.py` | dev compose 默认值为 `/opt/cdfm-weights-image` |

既有 `FinalizationGate` 测试不传新参数，默认 `None` 时行为不变。

## 7. 验证

1. `docker compose -f docker-compose.test.yml run --rm unit-test python -m pytest -p no:cacheprovider <测试路径> -q`
2. `docker compose up -d --no-deps causal-mcp`，确认容器内 `CDFM_MODEL_PATH=/opt/cdfm-weights-image` 且 `/ready` 正常。
3. 用同一数据集重跑"分析 T 到 Y 的因果效应"，检查：
   - `action_ledger` 出现 `causal_ate` 终态记录；
   - `finalization_status` 不为 `degraded`；
   - 报告包含真实效应值，或 ATE 返回的结构化不可估计理由。

## 8. 已知取舍

- 模型若在还没有 valid 图时先调 ATE，会拿到 `not_ready`；按"尝试即放行"规则门禁不再拦截。这是覆盖型门禁的边界，不额外引入时序判定。
- 效应词表是字符串匹配；"影响/作用"等词在少数结构性问题中可能误触发一次 ATE 尝试。代价是一次可解释的工具调用，不影响结论正确性。
- 不修改重试预算（当前为 1）。提示文案已包含 `evidence_only` 指引，降低同一轮二次失败的概率。
- ATE 返回 `not_applicable`（例如图中不存在 treatment→outcome 的有向路径）也算一次尝试；否则图结构上不可估计时会误判为未尝试。
