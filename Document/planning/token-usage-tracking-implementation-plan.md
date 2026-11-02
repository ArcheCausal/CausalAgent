# Token 用量追踪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个数据集运行在 `results.csv` 中记录 `tokens_input / tokens_output / tokens_total`，覆盖 Job 内全部 LLM 调用，失败/超时/取消也保留已发生的用量。

**Architecture:** 每个 Job attempt 创建一个 `UsageCollector`（LangChain callback），通过图 `config["callbacks"]` 采集用量；`graph_runner` 把累计快照发成 `usage` 公共事件，终态事件附带最终快照；`evaluation-runner` 按 `(job, attempt)` 取每个 attempt 的最大累计值再跨 attempt/Job 求和，写入结果行；`report.py` 输出总体与分组 token 均值。

**Tech Stack:** Python 3.11、LangChain 1.x（langchain-openai 1.6.2）、LangGraph、unittest/pytest、evaluation-runner 自带 `selftest.py`。

## Global Constraints

- 不改两个 `ChatOpenAI` 的构造参数（`app/agent/worker/runtime.py`）。
- 公共事件只能经 `app/agent/public_events.py` 白名单投影，不得直接透传内部字段。
- runner 只统计 `usage` 事件；终态事件里的 `usage` 快照不得重复计入。
- 没有产生任何 LLM 用量的行三列留空 `""`，不写 0；均值只统计有值的行。
- 所有命令在 WSL 内通过 `docker exec` 在 `causalagent_app` 容器执行：源码挂载在 `/app`。
- 提交信息不带 `Co-Authored-By` 署名；分支保持在 `feat-evaluation`，不合并、不推送。
- 设计依据：`Document/planning/token-usage-tracking-design.md`。

---

### Task 1: UsageCollector

**Files:**
- Create: `app/agent/worker/usage_collector.py`
- Test: `tests/unit/agent/test_usage_collector.py`

**Interfaces:**
- Produces: `UsageCollector()`，`on_llm_end(response, **kwargs)`，`snapshot() -> dict[str, int]`，键固定为 `input_tokens`、`output_tokens`、`total_tokens`。

- [ ] **Step 1: 写失败测试**

`tests/unit/agent/test_usage_collector.py`：

```python
import unittest

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.agent.worker.usage_collector import UsageCollector


def _result(*, message_usage=None, token_usage=None):
    generations = []
    if message_usage is not None:
        message = AIMessage(content="", usage_metadata=message_usage)
        generations = [[ChatGeneration(message=message)]]
    llm_output = {"token_usage": token_usage} if token_usage is not None else {}
    return LLMResult(generations=generations, llm_output=llm_output)


class UsageCollectorTests(unittest.TestCase):
    def test_reads_message_usage_without_double_counting(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(
                message_usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                token_usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            )
        )
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        )

    def test_falls_back_to_llm_output(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(
                token_usage={
                    "prompt_tokens": 7,
                    "completion_tokens": 1,
                    "total_tokens": 8,
                }
            )
        )
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 7, "output_tokens": 1, "total_tokens": 8},
        )

    def test_accumulates_and_snapshot_is_copy(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(message_usage={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4})
        )
        collector.on_llm_end(
            _result(message_usage={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})
        )
        snapshot = collector.snapshot()
        snapshot["input_tokens"] = 999
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
        )

    def test_ignores_malformed_values(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(
                message_usage={
                    "input_tokens": True,
                    "output_tokens": "x",
                    "total_tokens": -1,
                }
            )
        )
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_usage_collector.py -q"
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.agent.worker.usage_collector'`。

- [ ] **Step 3: 实现**

`app/agent/worker/usage_collector.py`：

```python
"""把 LangChain 每次 LLM 调用的 token 用量累计到当前 Job attempt。"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


class UsageCollector(BaseCallbackHandler):
    """线程安全地累加 usage_metadata；snapshot 返回累计快照。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals = {name: 0 for name in USAGE_FIELDS}

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        found = False
        for batch in getattr(response, "generations", None) or ():
            for generation in batch:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if isinstance(usage, Mapping):
                    self._add(usage)
                    found = True
        if found:
            return
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, Mapping):
            token_usage = llm_output.get("token_usage")
            if isinstance(token_usage, Mapping):
                self._add(
                    {
                        "input_tokens": token_usage.get("prompt_tokens"),
                        "output_tokens": token_usage.get("completion_tokens"),
                        "total_tokens": token_usage.get("total_tokens"),
                    }
                )

    def _add(self, usage: Mapping[str, Any]) -> None:
        values: dict[str, int] = {}
        for name in USAGE_FIELDS:
            value = usage.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                value = 0
            values[name] = value
        if not any(values.values()):
            return
        with self._lock:
            for name, value in values.items():
                self._totals[name] += value

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._totals)
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_usage_collector.py -q"
```

Expected: `4 passed`。

- [ ] **Step 5: 提交**

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3 && git add app/agent/worker/usage_collector.py tests/unit/agent/test_usage_collector.py && git commit -m 'feat(worker): 新增 LLM token 用量收集器'"
```

---

### Task 2: 图执行接入与 usage 事件

**Files:**
- Modify: `app/agent/worker/graph_runner.py`（`_graph_config`、`ai_call_stream`、chunk 循环与终态路径）
- Test: `tests/unit/agent/test_graph_runner.py`

**Interfaces:**
- Consumes: Task 1 的 `UsageCollector.snapshot()`。
- Produces: `_graph_config(..., callbacks=None)`；`ai_call_stream(..., usage_collector=None)`；事件 `{"type": "usage", "attempt": int, "input_tokens": int, "output_tokens": int, "total_tokens": int}`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/agent/test_graph_runner.py` 的 `_snapshot` 定义之前加入：

```python
class StubCollector:
    """提供与 UsageCollector 相同的只读快照。"""

    def __init__(self):
        self.values = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def snapshot(self):
        return dict(self.values)


class UsageGraph(FakeGraph):
    """在图执行中更新用量，并产生两个空 chunk。"""

    def __init__(self, collector):
        super().__init__([_snapshot(), _snapshot(values={"messages": []})])
        self.collector = collector

    async def astream(self, input_data, config, **_kwargs):
        self.inputs.append((input_data, config))
        self.collector.values = {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
        }
        yield {"type": "updates", "ns": (), "data": {}}
        yield {"type": "updates", "ns": (), "data": {}}
```

把 `_collect` 的签名改为：

```python
async def _collect(
    graph,
    text="hello",
    *,
    claim_kind="initial",
    input_record=None,
    initial_input_record=None,
    usage_collector=None,
    **file_snapshot,
):
```

并在 `ai_call_stream(...)` 调用里追加：

```python
                usage_collector=usage_collector,
```

在 `GraphRunnerTests` 中加入测试：

```python
    async def test_usage_collector_is_attached_and_emitted_once(self):
        """collector 进入图 config；累计值只变化一次就只发一条 usage。"""
        collector = StubCollector()
        graph = UsageGraph(collector)

        events = await _collect(graph, usage_collector=collector)

        self.assertIn(collector, graph.inputs[0][1]["callbacks"])
        usage_events = [event for event in events if event["type"] == "usage"]
        self.assertEqual(
            usage_events,
            [
                {
                    "type": "usage",
                    "attempt": 1,
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                }
            ],
        )
        self.assertEqual(events[-1]["type"], "final_result")
        self.assertLess(events.index(usage_events[0]), events.index(events[-1]))

    async def test_without_collector_no_usage_events(self):
        events = await _collect(FakeGraph([_snapshot(), _snapshot()]))
        self.assertTrue(all(event["type"] != "usage" for event in events))
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_graph_runner.py -q"
```

Expected: FAIL，`TypeError: _collect() got an unexpected keyword argument 'usage_collector'` 或 `KeyError: 'callbacks'`。

- [ ] **Step 3: 修改 `_graph_config`**

把 `app/agent/worker/graph_runner.py` 的 `_graph_config` 改为：

```python
def _graph_config(
    *,
    user_id: int,
    session_id: str,
    job_id: str,
    callbacks: list[Any] | None = None,
) -> dict[str, Any]:
    """构造 Job 根图配置；业务 session 只作为 State/metadata 上下文。"""
    thread_id, _ = checkpoint_identity(job_id)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "user_id": user_id,
    }
    config: dict[str, Any] = {
        "configurable": configurable,
        "metadata": {
            "job_id": thread_id,
            "session_id": str(session_id),
        },
    }
    if callbacks:
        config["callbacks"] = list(callbacks)
    return config
```

- [ ] **Step 4: 修改 `ai_call_stream` 签名与 config 构造**

在 `ai_call_stream` 参数末尾（`agent_runtime_context` 之后）加入：

```python
    usage_collector: Any | None = None,
```

把该函数内的 `_graph_config` 调用改为：

```python
    config = _graph_config(
        user_id=user_id,
        session_id=session_id,
        job_id=job_id,
        callbacks=[usage_collector] if usage_collector is not None else None,
    )
```

- [ ] **Step 5: 增加快照与发事件的辅助函数**

在 `config = _graph_config(...)` 之后、`state = await graph.aget_state(config)` 之前加入：

```python
    last_usage: tuple[int, int, int] | None = None

    def flush_usage() -> dict[str, Any] | None:
        nonlocal last_usage
        if usage_collector is None:
            return None
        current = usage_collector.snapshot()
        marker = (
            current.get("input_tokens", 0),
            current.get("output_tokens", 0),
            current.get("total_tokens", 0),
        )
        if marker == last_usage or not any(marker):
            return None
        last_usage = marker
        return {"type": "usage", "attempt": job_attempt, **current}
```

- [ ] **Step 6: 在 chunk 循环与终态前发送**

在 `for event_data in adapter.convert(chunk):` 的 `yield event_data` 之后（仍在 `async for chunk` 循环体内）加入：

```python
            usage_event = flush_usage()
            if usage_event is not None:
                yield usage_event
```

在以下四个 `yield` 之前分别加入同样的三行：

1. interrupt 分支的 `yield payload`
2. `yield final_result`
3. `yield recovered`
4. `yield {"type": "error", ...}`

示例（final_result）：

```python
        usage_event = flush_usage()
        if usage_event is not None:
            yield usage_event
        yield final_result
```

- [ ] **Step 7: 运行测试**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_graph_runner.py -q"
```

Expected: 全部通过。

- [ ] **Step 8: 提交**

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3 && git add app/agent/worker/graph_runner.py tests/unit/agent/test_graph_runner.py && git commit -m 'feat(worker): 图执行采集并发布 token 用量事件'"
```

---

### Task 3: 终态事件附带用量

**Files:**
- Modify: `app/agent/worker/event_writer.py`
- Modify: `app/agent/job_service.py`（`fail_job` 可选 `usage`）
- Modify: `app/agent/worker/execution.py`（创建 collector、接入 writer 与图执行、兜底异常路径传 usage）
- Test: `tests/unit/agent/test_ordered_event_writer.py`

**Interfaces:**
- Consumes: `UsageCollector.snapshot()`。
- Produces: `OrderedEventWriter(..., usage_collector=None)`；`job_service.fail_job(..., usage=None)`；终态 payload 含 `usage: {"input_tokens", "output_tokens", "total_tokens"}`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/agent/test_ordered_event_writer.py` 的 `OrderedEventWriterTests` 中加入：

```python
    async def test_complete_terminal_payload_carries_usage_snapshot(self):
        """final_result / interrupt 落库前附加累计用量。"""
        class Collector:
            def snapshot(self):
                return {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}

        for event_type in ("final_result", "interrupt"):
            with self.subTest(event_type=event_type):
                writer = OrderedEventWriter(
                    build_job(), "worker-a", usage_collector=Collector()
                )
                complete = AsyncMock(return_value=True)
                with patch(
                    "app.agent.worker.event_writer._complete_terminal_event",
                    new=complete,
                ):
                    await writer.submit({"type": event_type, "data": {}})
                    await writer.close()
                payload = complete.call_args.args[2]
                self.assertEqual(
                    payload["usage"],
                    {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                )

    async def test_error_terminal_passes_usage_to_fail_job(self):
        """error 事件由 fail_job 写入，用量必须作为参数传进去。"""
        class Collector:
            def snapshot(self):
                return {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}

        writer = OrderedEventWriter(build_job(), "worker-a", usage_collector=Collector())
        fail_job = Mock(return_value=FailJobResult.APPLIED)
        with patch("app.agent.job_service.fail_job", fail_job):
            await writer.submit({"type": "error", "message": "safe"})
            await writer.close()
        self.assertEqual(
            fail_job.call_args.kwargs["usage"],
            {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_ordered_event_writer.py -q"
```

Expected: FAIL，`TypeError: __init__() got an unexpected keyword argument 'usage_collector'`。

- [ ] **Step 3: 修改 writer**

`OrderedEventWriter.__init__` 签名改为：

```python
    def __init__(
        self,
        job: dict[str, Any],
        worker_id: str,
        execution_guard: JobExecutionGuard | None = None,
        usage_collector: Any | None = None,
    ):
```

在 `self.terminal_diagnostic = None` 附近加入：

```python
        self.usage_collector = usage_collector
```

在 `_persist` 中，`event_type = payload.get("type", "message")` 之后、`if event_type in {"final_result", "interrupt"}:` 之前加入：

```python
        usage = (
            self.usage_collector.snapshot()
            if self.usage_collector is not None
            else None
        )
        if usage is not None and event_type in {"final_result", "interrupt"}:
            payload = {**payload, "usage": usage}
```

在 error 分支的 `job_service.fail_job(...)` 调用中追加：

```python
                usage=usage,
```

- [ ] **Step 4: 修改 `fail_job`**

`app/agent/job_service.py` 的 `fail_job` 签名加入关键字参数：

```python
    write_error_event: bool = True,
    usage: Any | None = None,
) -> FailJobResult:
```

把 `payload = {"type": "error", "message": message}` 改为：

```python
    payload: dict[str, Any] = {"type": "error", "message": message}
    if usage is not None:
        payload["usage"] = dict(usage)
```

- [ ] **Step 5: 接入 `execution.py`**

顶部导入：

```python
from app.agent.worker.usage_collector import UsageCollector
```

在 `writer: OrderedEventWriter | None = None` 下一行加入：

```python
    usage_collector = UsageCollector()
```

把 writer 构造改为：

```python
        writer = OrderedEventWriter(
            job,
            worker_id,
            execution_guard=guard,
            usage_collector=usage_collector,
        )
```

在 `ai_call_stream(...)` 调用中加入：

```python
            usage_collector=usage_collector,
```

在 `except Exception as exc` 分支的 `job_service.fail_job(...)` 调用中追加：

```python
                usage=usage_collector.snapshot(),
```

- [ ] **Step 6: 运行测试**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_ordered_event_writer.py tests/unit/agent/test_worker_execution.py -q"
```

Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3 && git add app/agent/worker/event_writer.py app/agent/job_service.py app/agent/worker/execution.py tests/unit/agent/test_ordered_event_writer.py && git commit -m 'feat(worker): 终态事件附带 token 用量快照'"
```

> 注：Task 2 只改 `graph_runner`；本任务才把 collector 接进 `execution`，避免出现 `OrderedEventWriter` 尚未支持 `usage_collector` 参数时的不一致状态。

---

### Task 4: 公共事件白名单

**Files:**
- Modify: `app/agent/public_events.py`
- Test: `tests/unit/agent/test_stream_events.py`

**Interfaces:**
- Produces: `usage` 事件允许 `attempt/input_tokens/output_tokens/total_tokens`；`final_result/error/interrupt/canceled` 允许 `usage`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/agent/test_stream_events.py` 的 `StreamEventAdapterTests` 中加入：

```python
    def test_sse_public_payload_exposes_usage_event_and_terminal_usage(self):
        usage = _public_event_payload(
            {
                "type": "usage",
                "attempt": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "prompt": "private",
            }
        )

        self.assertEqual(
            usage,
            {
                "type": "usage",
                "attempt": 2,
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
            },
        )

        final = _public_event_payload(
            {
                "type": "final_result",
                "data": {"type": "text"},
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                "_context_commit": {"private": True},
            }
        )
        self.assertEqual(
            final["usage"],
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        )
        self.assertNotIn("_context_commit", final)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_stream_events.py -q"
```

Expected: FAIL，`usage` 被白名单剥离。

- [ ] **Step 3: 修改白名单**

`app/agent/public_events.py`：

```python
    "usage": {"attempt", "input_tokens", "output_tokens", "total_tokens"},
    "final_result": {"data", "usage"},
    "interrupt": {"message", "question_id", "interaction", "usage"},
    "error": {"message", "usage"},
    "canceled": {"message", "usage"},
```

- [ ] **Step 4: 运行测试**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent/test_stream_events.py -q"
```

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3 && git add app/agent/public_events.py tests/unit/agent/test_stream_events.py && git commit -m 'feat(events): 放行 token 用量事件字段'"
```

---

### Task 5: runner 采集、聚合与落盘

**Files:**
- Modify: `evaluation-runner/runner.py`
- Test: `evaluation-runner/selftest.py`

**Interfaces:**
- Produces: `rn.USAGE_FIELDS`、`rn.usage_totals(records) -> dict[str, int]`、`rn._add_usage(total, row)`；结果列 `tokens_input/tokens_output/tokens_total`。

- [ ] **Step 1: 写失败测试**

在 `evaluation-runner/selftest.py` 中加入：

```python
def test_usage_totals_sums_attempts_and_ignores_terminal_snapshot():
    records = [
        {"type": "usage", "payload": {"attempt": 1, "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
        {"type": "usage", "payload": {"attempt": 1, "input_tokens": 15, "output_tokens": 5, "total_tokens": 20}},
        {
            "type": "final_result",
            "payload": {"attempt": 1, "usage": {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20}},
        },
        {"type": "usage", "payload": {"attempt": 2, "input_tokens": 7, "output_tokens": 1, "total_tokens": 8}},
    ]
    assert rn.usage_totals(records) == {
        "tokens_input": 22,
        "tokens_output": 6,
        "tokens_total": 28,
    }
    assert rn.usage_totals([]) == {}
    assert rn.usage_totals([{"type": "usage", "payload": {"attempt": 1}}]) == {}


def test_add_usage_accumulates_jobs_and_skips_blank_rows():
    total = {}
    rn._add_usage(total, {"tokens_input": 10, "tokens_output": 2, "tokens_total": 12})
    rn._add_usage(total, {"tokens_input": "", "tokens_output": "", "tokens_total": ""})
    rn._add_usage(total, {"tokens_input": 5, "tokens_output": 1, "tokens_total": 6})
    assert total == {"tokens_input": 15, "tokens_output": 3, "tokens_total": 18}
```

把 `test_run_once_marks_missing_graph_failed` 的 `FakeClient.events` 改为：

```python
        def events(self, job_id, last_event_id=0):
            yield {"id": 1, "type": "usage",
                   "payload": {"attempt": 1, "input_tokens": 10,
                               "output_tokens": 2, "total_tokens": 12}}
            yield {"id": 2, "type": "final_result",
                   "payload": {"data": {"document": {"blocks": [], "assets": {}}}}}
```

并在断言后追加：

```python
        assert (row["tokens_input"], row["tokens_output"], row["tokens_total"]) == (10, 2, 12)
```

- [ ] **Step 2: 运行自检确认失败**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app/evaluation-runner causalagent_app python selftest.py"
```

Expected: `AttributeError: module 'runner' has no attribute 'usage_totals'` 或新增断言失败。

- [ ] **Step 3: 修改列名**

`evaluation-runner/runner.py`：

```python
USAGE_FIELDS = ("tokens_input", "tokens_output", "tokens_total")
_EVENT_USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")

RESULT_HEADER = [
    "run_id", "dataset", "mechanism", "rep", "n_rows", "n_cols", "dim", "lambda",
    "n_gold_edges", "n_pred_edges", "n_undirected_edges", "recall", "precision", "f1",
    "skeleton_recall", "skeleton_precision", "shd", "auroc", "auprc",
    "final_algorithm", "final_confidence", "final_selection_rationale",
    "finalization_status", "interactions", "duration_s",
    "tokens_input", "tokens_output", "tokens_total", "status", "failure_reason",
]
```

同时删除 `_failed_row` 中 `"tokens": "not_collected"` 这一行。

- [ ] **Step 4: 增加聚合函数**

在 `_failed_row` 之前加入：

```python
def usage_totals(records) -> dict[str, int]:
    """按 attempt 取累计最大值再求和；终态快照不重复计入。"""
    per_attempt: dict[object, tuple[int, int, int]] = {}
    for record in records:
        if record.get("type") != "usage":
            continue
        payload = record.get("payload") or {}
        values = tuple(payload.get(name) for name in _EVENT_USAGE_FIELDS)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            continue
        key = payload.get("attempt")
        previous = per_attempt.get(key, (0, 0, 0))
        per_attempt[key] = tuple(
            max(old, new) for old, new in zip(previous, values)
        )
    if not per_attempt:
        return {}
    return {
        name: sum(item[index] for item in per_attempt.values())
        for index, name in enumerate(USAGE_FIELDS)
    }


def _add_usage(total: dict[str, int], row: dict) -> None:
    for name in USAGE_FIELDS:
        value = row.get(name, "")
        if value not in ("", None):
            total[name] = total.get(name, 0) + int(value)
```

- [ ] **Step 5: 在 `_run_once` 写入行**

在 `row.update({"n_rows": ...})` 之后加入：

```python
    usage = usage_totals(records)
    if usage:
        row.update(usage)
```

- [ ] **Step 6: 在 `_run_dataset` 跨重试累加**

改为：

```python
        client.login(*credentials)
        total_usage: dict[str, int] = {}
        row = _run_once(spec, config, client, run_dir, run_id)
        _add_usage(total_usage, row)
        attempts = 0
        while row["status"] == "failed" and row["failure_reason"] in RETRYABLE and attempts < 1:
            attempts += 1
            row = _run_once(spec, config, client, run_dir, run_id)
            _add_usage(total_usage, row)
        if total_usage:
            row.update(total_usage)
        return row
```

- [ ] **Step 7: 结果读取转换**

在 `main()` 的数值转换循环中加入：

```python
        for name in USAGE_FIELDS:
            value = row.get(name, "")
            row[name] = float(value) if value not in ("", None) else ""
```

- [ ] **Step 8: 运行自检**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app/evaluation-runner causalagent_app python selftest.py"
```

Expected: 全部 PASS（数量较之前增加 2 个）。

- [ ] **Step 9: 提交**

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3 && git add evaluation-runner/runner.py evaluation-runner/selftest.py && git commit -m 'feat(evaluation): 记录每个数据集的 token 用量'"
```

---

### Task 6: 汇总与报告

**Files:**
- Modify: `evaluation-runner/report.py`
- Test: `evaluation-runner/selftest.py`

**Interfaces:**
- Consumes: Task 5 的三列。
- Produces: `summary.csv` 字段 `tokens_input_mean/tokens_output_mean/tokens_total_mean/tokens_n`；`report.md` 总体行与分组表 token 列。

- [ ] **Step 1: 写失败测试**

在 `evaluation-runner/selftest.py` 中：

把 `_row` 的 `"tokens": "not_collected"` 替换为：

```python
        "tokens_input": 1200, "tokens_output": 300, "tokens_total": 1500,
```

在 `test_summarize_counts_and_means` 末尾加入：

```python
    assert summary["tokens_total_mean"] == 1500
    assert summary["tokens_n"] == 2
```

新增：

```python
def test_summary_tokens_skip_blank_rows():
    rows = [_row(), _row("Linear/D010_N00500/rep00", status="failed", reason="timeout")]
    rows[1]["tokens_input"] = ""
    rows[1]["tokens_output"] = ""
    rows[1]["tokens_total"] = ""
    summary = report.summarize(rows, {"sampling": {}})
    assert summary["tokens_total_mean"] == 1500
    assert summary["tokens_n"] == 1


def test_report_markdown_contains_token_columns():
    markdown = report.report_markdown([_row()], {"run_id": "r1", "git_commit": "abc"})
    assert "tokens 平均" in markdown
    assert "tokens(入/出/总)" in markdown
```

- [ ] **Step 2: 运行自检确认失败**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app/evaluation-runner causalagent_app python selftest.py"
```

Expected: FAIL，`KeyError: 'tokens_total_mean'`。

- [ ] **Step 3: 修改 summary 字段**

`evaluation-runner/report.py` 的 `SUMMARY_HEADER` 在 `"interactions_mean"` 后加入：

```python
    "tokens_input_mean", "tokens_output_mean", "tokens_total_mean", "tokens_n",
```

`summarize` 的返回字典在 `"interactions_mean"` 后加入：

```python
        "tokens_input_mean": _mean_present(rows, "tokens_input"),
        "tokens_output_mean": _mean_present(rows, "tokens_output"),
        "tokens_total_mean": _mean_present(rows, "tokens_total"),
        "tokens_n": len(_present(rows, "tokens_total")),
```

- [ ] **Step 4: 修改分组表**

在 `_table` 中加入辅助函数：

```python
    def tokens_cell(items):
        present = [
            row for row in items
            if row.get("tokens_total", "") not in ("", None)
        ]
        if not present:
            return "-"
        return (
            f"{_fmt(_mean_present(present, 'tokens_input'), 0)}"
            f"/{_fmt(_mean_present(present, 'tokens_output'), 0)}"
            f"/{_fmt(_mean_present(present, 'tokens_total'), 0)}"
        )
```

表头改为：

```python
        "| 分组 | n | recall | precision | F1 | SHD | 骨架 recall | 骨架 precision | tokens(入/出/总) |",
        "|---|---|---|---|---|---|---|---|---|",
```

无数据行改为：

```python
            lines.append(f"| {key} | 0 | - | - | - | - | - | - | - |")
```

有数据行的追加列：

```python
            f"| {tokens_cell(items)} |"
```

- [ ] **Step 5: 修改总体节与口径**

在 AUROC 行之后加入：

```python
        f"- tokens 平均 入/出/总: {_fmt(summary['tokens_input_mean'], 0)}"
        f"/{_fmt(summary['tokens_output_mean'], 0)}"
        f"/{_fmt(summary['tokens_total_mean'], 0)}"
        f"（{summary['tokens_n']} 集）",
```

在 `## 口径` 列表末尾加入：

```python
        "- token 均值只统计产生过 LLM 用量的行；总体节含失败行，分组表沿用该表的成功/部分成功范围。",
```

- [ ] **Step 6: 运行自检**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app/evaluation-runner causalagent_app python selftest.py"
```

Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3 && git add evaluation-runner/report.py evaluation-runner/selftest.py && git commit -m 'feat(evaluation): 报告展示 token 用量'"
```

---

### Task 7: README 与端到端验证

**Files:**
- Modify: `evaluation-runner/README.md`

- [ ] **Step 1: 更新 README**

修改以下三处：

1. `results.csv` 行（第 129 行附近）：把 `28 列` 改为 `30 列`，字段列表中 `duration_s, tokens, status` 改为 `duration_s, tokens_input, tokens_output, tokens_total, status`。
2. `summary.csv` 行（第 130 行附近）：把 `26 列` 改为 `30 列`，字段说明追加 `tokens_input_mean, tokens_output_mean, tokens_total_mean, tokens_n`。
3. 第 138 行：删除 `tokens 固定写 not_collected；阶段 2 才采集 token 用量。`，改为：

```markdown
token 用量来自 Job 的 `usage` 事件：按 `(job, attempt)` 取每 attempt 累计最大值，再跨 attempt 与 runner 重试求和；无 LLM 用量的失败行留空。终态事件里的用量快照只服务 API 客户端，不重复计入。
```

4. 第 183 行阶段 2 说明：删除 tokens 待办，保留“全量 1875 集与更细的失败分析”。

- [ ] **Step 2: 重启服务加载新代码**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker restart causalagent_worker causalagent_app && sleep 15 && docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'causalagent_(worker|app)'"
```

Expected: 两个容器均为 `Up`。

- [ ] **Step 3: 跑单集冒烟**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app/evaluation-runner causalagent_app python runner.py --config config.smoke.yaml --yes"
```

Expected: `完成：1/1 成功`，输出新的 `local/smoke/<run_id>`。

> 单集耗时可能超过 10 分钟；用后台方式启动，轮询输出文件，不要用 sleep 空转。

- [ ] **Step 4: 校验结果**

Run:

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3/evaluation-runner/local/smoke && ls -td */ | head -1"
```

然后用最新目录执行：

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3/evaluation-runner/local/smoke/<run_id> && head -2 results.csv && grep -o 'tokens[^,]*' summary.csv | head && grep -n 'tokens' report.md | head"
```

Expected:
- `results.csv` 的 `tokens_input/tokens_output/tokens_total` 三列非空；
- `tokens_total == tokens_input + tokens_output`；
- `summary.csv` 含四个新字段；
- `report.md` 含“tokens 平均”和“tokens(入/出/总)”。

- [ ] **Step 5: 运行全量单测回归**

Run:

```bash
wsl -d Ubuntu -e bash -lc "docker exec -w /app causalagent_app python -m pytest tests/unit/agent -q"
```

Expected: 全绿（允许既有的 skip）。

- [ ] **Step 6: 提交**

```bash
wsl -d Ubuntu -e bash -lc "cd /home/yuki/CausalAgent3 && git add evaluation-runner/README.md && git commit -m 'docs(evaluation): 说明 token 用量采集口径'"
```

---

## Self-Review

- 规格覆盖：采集（Task 1-2）、事件（Task 2）、终态（Task 3）、白名单（Task 4）、落盘（Task 5）、报告（Task 6）、验证（Task 7）一一对应设计文档。
- 无占位符：每个代码步骤均含完整实现或精确改动。
- 类型一致：事件字段 `input_tokens/output_tokens/total_tokens` 与 CSV 字段 `tokens_input/tokens_output/tokens_total` 的映射只在 `usage_totals` 一处完成。
- 已知回归点：`evaluation-runner` 表头变化会让旧目录 `--resume` 被拒绝，属预期。
