# Token 用量追踪设计

- 日期：2026-10-31
- 状态：设计已确认，待实现
- 范围：分析 Job 图内 LLM 调用的用量采集、公共事件、`evaluation-runner` 落盘与报告

## 背景

`evaluation-runner` 的 `results.csv` 目前有一列 `tokens`，值固定为 `not_collected`。
评测只能回答"跑没跑通、图准不准"，无法回答"每个数据集花了多少 token"。

目标：每完成一个数据集运行，都在结果行记录该数据集全部尝试的 token 用量，包含：

- `tokens_input`
- `tokens_output`
- `tokens_total`

失败、超时、取消的运行也要记录已经发生的用量。

## 已验证的运行时事实

在 `causalagent_app` 容器内用真实配置做过验证（langchain-openai 1.6.2）：

| 路径 | usage 可得 | 来源 |
|---|---|---|
| 外层 `create_llm()`（非流式） | 是 | `message.usage_metadata`；`llm_output["token_usage"]` 也有 |
| Deep Agent 模型（`streaming=True` + Responses API）`invoke` | 是 | `message.usage_metadata` |
| Deep Agent 模型 `stream()`（消费迭代器） | 是，最后一个 chunk | `message.usage_metadata` |
| JSON mode（两条模型路径） | 是 | `message.usage_metadata` |
| `with_structured_output(json_schema)` | 服务端 400 | 项目已降级为 JSON mode（`Agent/tool_node/structured_output.py`） |
| `on_llm_end` 回调 | 上述所有路径都触发 | 必须读 `generation.message.usage_metadata`；Responses API 的 `llm_output` 为空 |
| 图 `config["callbacks"]` 注入 | 能传到图内节点的模型调用 | mini graph 实测 |

结论：用量从 LangChain 回调采集；归属通过每次 Job 的图 config 注入，不需要全局 handler 或 ContextVar。

## 采集设计

新增 `app/agent/worker/usage_collector.py`：

```python
class UsageCollector(BaseCallbackHandler):
    def on_llm_end(self, response, **kwargs) -> None: ...
    def snapshot(self) -> dict[str, int]: ...  # {"input_tokens", "output_tokens", "total_tokens"}
```

- 逐条 `generations` 读 `generation.message.usage_metadata`。
- 缺失时退回 `response.llm_output["token_usage"]`，字段映射：
  `prompt_tokens → input_tokens`、`completion_tokens → output_tokens`、`total_tokens → total_tokens`。
- 用 `threading.Lock` 保护累加，`snapshot()` 返回不可变副本。
- 只累加，不做费用换算、不区分模型。

归属：

- `app/agent/worker/execution.py` 每个 Job attempt 创建一个 `UsageCollector`，与 `OrderedEventWriter` 同生命周期。
- `ai_call_stream` 新增参数传入 collector；`graph_runner._graph_config(...)` 在返回的 config 中加 `"callbacks": [collector]`。
- 两个 `ChatOpenAI` 的构造参数不变。

发射：

- `graph_runner` 的 `astream` chunk 循环每轮比较 `snapshot()` 与"上次已发快照"。
- 有变化时 yield 一条 `usage` 事件；usage 只在 LLM 调用结束时变化，因此等价于"每次 LLM 调用后发一次"。
- 无变化不发送，避免 messages 流产生大量重复事件。

## 事件契约

### usage 事件

```json
{
  "type": "usage",
  "attempt": 2,
  "input_tokens": 1234,
  "output_tokens": 456,
  "total_tokens": 1690
}
```

- 数值是**当前 attempt 的累计值**，不是单次调用增量。
- `attempt` 是 Job attempt 编号，用于区分断点续跑后的重置。
- 事件先于终态事件产生并按序持久化，重放 SSE 也能拿到。

### 终态事件

Worker 侧终态 `final_result`、`error`、`interrupt` 由 `OrderedEventWriter` 在持久化前注入：

```json
"usage": {"input_tokens": 1234, "output_tokens": 456, "total_tokens": 1690}
```

- 用于 API 客户端展示最终总量。
- `error` 事件由 `job_service.fail_job` 写入，调用方（writer 与 execution 的兜底异常路径）把快照作为参数传入，最终进入事件 payload。
- `canceled` 事件由 API 侧取消接口产生，拿不到 collector，不注入；调用方（runner）依赖此前收到的 `usage` 事件。

### 公共事件白名单

`app/agent/public_events.py`：

- 新增事件类型 `usage`，允许字段 `attempt`、`input_tokens`、`output_tokens`、`total_tokens`。
  `attempt` 在这里是对外暴露的刻意例外：它是`usage`的去重与重置边界，不含敏感内容；其他事件仍按现有规则剥离该字段。
- 给 `final_result`、`error`、`interrupt` 增加 `usage` 字段；`canceled` 同步增加，保持协议一致。

事件在写库后按白名单投影，新增字段不会影响既有事件。

## evaluation-runner 落盘

### 结果列

`RESULT_HEADER` 中用三列替换 `tokens`：

```
tokens_input, tokens_output, tokens_total
```

表头变化会让旧目录的 `--resume` 被现有守卫拒绝并提示换新目录，属预期。

### 聚合算法

只认 `usage` 事件（终态 `usage` 不重复计入）：

1. 对同一 Job 内每个 `attempt`，取该 attempt 所有 `usage` 事件中字段的**最大值**（累计值单调不减）。
2. 跨 attempt 求和，得到该 Job 总量。
3. `_run_dataset` 的 runner 级重试会创建新 Job；把各次 `_run_once` 的结果**求和**，写入同一数据集行。
4. 没有任何 usage 的行（`dataset_invalid`、`auth_error`、上传失败）三列留空 `""`，不写 0。

平均值只统计三列有值的行。

### 失败语义

- `timeout` / `canceled` / `terminal_error`：保留取消前已收到的 `usage` 事件。
- `interaction_budget_exceeded` 等 runner 主动取消：同上。
- 重试后成功：保留两次 Job 的用量之和，反映真实成本。

## 报告与 summary

`summary.csv` 新增：

- `tokens_input_mean`
- `tokens_output_mean`
- `tokens_total_mean`
- `tokens_n`（有用量数据的行数）

`report.md`：

- 总体节增加一行：`tokens 平均 入/出/总（n 集）`。
- 按机制、按 D、按 N 三个分组表各增加一列 `tokens(入/出/总)`。
- 使用现有 `_mean_present`，失败且无数据的行不参与均值。

## 测试

上游：

- `UsageCollector`：读取 `usage_metadata`；退回 `llm_output.token_usage`；多次调用累加；`snapshot()` 返回副本。
- `graph_runner`：快照无变化不发事件；变化时发 `usage`；字段与 `attempt` 正确；顺序在终态前。
- `event_writer`：`final_result` / `error` / `interrupt` 注入 `usage` 快照。
- `public_events`：放行 `usage` 事件与终态 `usage` 字段，其余字段仍被剥离。

runner（`evaluation-runner/selftest.py`，不引入测试框架）：

- 单 attempt 多次调用取累计最大值。
- attempt 重置后按 attempt 分组，再跨 attempt 求和。
- runner 重试跨 Job 求和。
- 终态 `usage` 不重复计入。
- 无 usage 的失败行留空。
- `summary.csv` 四个新字段与三个分组表的 token 列。

端到端：

- 跑一个 smoke 数据集，确认三列非空且 `tokens_total == tokens_input + tokens_output`。
- 覆盖一个失败但已产生用量的场景（超时或取消）。

## 非目标

- 不统计图外 LLM 调用：聊天标题生成、RAG embedding、MCP 算法进程、图外工具。
- 不新增数据库列、不做迁移；用量全部走事件。
- 不做费用估算。
- 不按模型拆分用量（外层模型与 Deep Agent 合并为一个总量）。
- 不做历史数据回填。

## 风险与边界

- 采集依赖 LangChain 回调和图 config 传播。若未来升级改变传播行为，退路是全局 handler + ContextVar 的 per-job 归属。
- 服务商把缓存命中 token 计入 `input_tokens`、推理 token 计入 `output_tokens`；这是服务商口径，本设计原样记录。
- 每个 LLM 调用约增加一条持久化事件，量级可忽略。
- 若外层模型将来改成流式，需要重新验证 `stream_usage`。

## 涉及文件

- 新增 `app/agent/worker/usage_collector.py`
- 修改 `app/agent/worker/graph_runner.py`
- 修改 `app/agent/worker/execution.py`
- 修改 `app/agent/worker/event_writer.py`
- 修改 `app/agent/job_service.py`（`fail_job` 接受可选 `usage`）
- 修改 `app/agent/public_events.py`
- 修改 `evaluation-runner/runner.py`
- 修改 `evaluation-runner/report.py`
- 修改 `evaluation-runner/selftest.py`
- 修改 `evaluation-runner/README.md`
- 新增上游单测

## 上线

- 源码挂载在容器 `/app`；改完重启 `causalagent_worker` 与 `causalagent_app`。
- 无数据库迁移。
- `evaluation-runner` 是本地脚本，无需重启服务。
