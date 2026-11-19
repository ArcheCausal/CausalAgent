# general-agent-eval P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `general-agent-eval-p1-design.md` 实现 TTY 交互选择与开跑确认，并补充报告的算法、成本、失败与基线一致性信息。

**Architecture:** 选择逻辑以纯函数放在 `cli.py`，交互只负责收集编号；报告在 `report.py` 基于现有 `case.json` 投影追加段落；预估用 `config.estimate` 常量。不新增依赖，不改执行链路。

**Tech Stack:** Python 3.11、pytest、PyYAML、argparse。

## Global Constraints

- 工作目录：WSL `~/CausalAgent3/general-agent-eval`；命令：`wsl -d Ubuntu -- bash -lc 'cd ~/CausalAgent3/general-agent-eval && source .venv/bin/activate && <cmd>'`
- 不新增第三方依赖；不改 `metrics.py`、`container.py`；`runner.py` 只改算法列相关（`RESULT_HEADER`、`_algorithms`、`_final_algorithm`、`_row`），不动执行链路
- 每次 git add 只列具体文件；commit 风格 `feat(evaluation): ...`；不加 `Co-Authored-By`
- **提交前先询问用户**
- 非 TTY 行为必须与现在完全一致：缺参返回 2，不进入交互

---

### Task 1: 预估配置默认值

**Files:**
- Modify: `src/general_agent_eval/config.py:10-50`
- Modify: `config.yaml`

- [ ] **Step 1: 加默认值**

在 `DEFAULTS` 的 `"judge"` 之前加：

```python
    "estimate": {
        "seconds_per_case": 600,
        "cost_usd_per_case": 0.6,
    },
```

- [ ] **Step 2: 同步示例配置**

`config.yaml` 在 `baseline_results` 后加：

```yaml
estimate:
  seconds_per_case: 600
  cost_usd_per_case: 0.6
```

- [ ] **Step 3: 验证合并**

Run: `python -c "from general_agent_eval.config import load_config; print(load_config('config.yaml')['estimate'])"`
Expected: `{'seconds_per_case': 600, 'cost_usd_per_case': 0.6}`

---

### Task 2: CLI 纯函数与测试

**Files:**
- Modify: `src/general_agent_eval/cli.py`
- Test: `tests/test_cli_selection.py`（新建）

**Interfaces：**
- Produces: `cli._parse_picks(raw: str, size: int) -> list[int]`；`cli._expand_csv(text, values) -> list`；`cli._expand_list(items, values) -> list`；`cli.estimate_text(count: int, config) -> str`
- Consumes: 无

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli_selection.py
import pytest

from general_agent_eval import cli


def test_parse_picks_accepts_commas_and_spaces():
    assert cli._parse_picks("1,3", 3) == [0, 2]
    assert cli._parse_picks("2 1", 3) == [1, 0]


@pytest.mark.parametrize("raw", ["", "x", "0", "4", "1,1"])
def test_parse_picks_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        cli._parse_picks(raw, 3)


def test_expand_all_and_plain():
    assert cli._expand_list(["all"], [10, 20]) == [10, 20]
    assert cli._expand_list([10], [10, 20]) == [10]
    assert cli._expand_csv("Linear,CPT", ["Linear"]) == ["Linear", "CPT"]
    assert cli._expand_csv("all", ["Linear", "CPT"]) == ["Linear", "CPT"]


def test_estimate_text_uses_constants():
    config = {"estimate": {"seconds_per_case": 600, "cost_usd_per_case": 0.6}}
    text = cli.estimate_text(3, config)
    assert "30.0 分钟" in text
    assert "$1.80" in text


def test_estimate_text_falls_back_to_defaults():
    text = cli.estimate_text(1, {})
    assert "10.0 分钟" in text
    assert "$0.60" in text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_cli_selection.py -q`
Expected: FAIL，`AttributeError`

- [ ] **Step 3: 实现纯函数**

`cli.py` 顶部补 `import re`，在 `_selection` 前加：

```python
def _parse_picks(raw: str, size: int) -> list[int]:
    parts = [item for item in re.split(r"[,\s]+", raw.strip()) if item]
    if not parts:
        raise ValueError("空输入")
    picks: list[int] = []
    for item in parts:
        if not item.isdigit():
            raise ValueError(f"非法编号：{item}")
        index = int(item) - 1
        if index < 0 or index >= size:
            raise ValueError(f"编号超出范围：{item}")
        if index in picks:
            raise ValueError(f"重复编号：{item}")
        picks.append(index)
    return picks


def _expand_list(items, values) -> list:
    if any(str(item).lower() == "all" for item in items):
        return list(values)
    return list(items)


def _expand_csv(text, values) -> list:
    items = [item.strip() for item in str(text or "").split(",") if item.strip()]
    return _expand_list(items, values)


def estimate_text(count: int, config) -> str:
    est = config.get("estimate") or {}
    seconds = float(est.get("seconds_per_case", 600))
    cost = float(est.get("cost_usd_per_case", 0.6))
    return (
        f"预计耗时约 {seconds * count / 60:.1f} 分钟，成本约 ${cost * count:.2f}"
        f"（{count} 集 × 单集 {seconds:.0f}s / ${cost:.2f}）"
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_cli_selection.py -q`
Expected: `9 passed`（参数化 5 例）

---

### Task 3: 交互选择接入 run

**Files:**
- Modify: `src/general_agent_eval/cli.py:20-92,163-171`

**Interfaces：**
- Consumes: Task 2 的 `_parse_picks/_expand_*/estimate_text`
- Produces: `_ask_axis(label, values) -> list`；`_resolve_selection(config, args, specs) -> dict | None`

- [ ] **Step 1: 替换 `_selection`**

```python
def _selection(config, args, available) -> dict:
    return {
        "mechanisms": _expand_csv(args.mechanisms, available["mechanisms"]),
        "dims": _expand_list(args.dims or [], available["dims"]),
        "ns": _expand_list(args.ns or [], available["ns"]),
        "per_cell": args.per_cell or int(config["selection"]["per_cell"]),
        "seed": args.seed if args.seed is not None else int(config["selection"]["seed"]),
    }


def _ask_axis(label: str, values: list) -> list:
    print(f"{label}：")
    for index, value in enumerate(values, 1):
        print(f"  {index}) {value}")
    while True:
        raw = input("选择编号（逗号分隔，a=全选，回车中止）：").strip()
        if not raw:
            raise SystemExit(0)
        if raw.lower() == "a":
            return list(values)
        try:
            return [values[index] for index in _parse_picks(raw, len(values))]
        except ValueError as exc:
            print(f"输入无效：{exc}")


def _resolve_selection(config, args, specs):
    available = selector.available(specs)
    if not (args.mechanisms and args.dims and args.ns):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        if not args.mechanisms:
            picked = _ask_axis("机制", available["mechanisms"])
            args.mechanisms = ",".join(str(item) for item in picked)
        if not args.dims:
            args.dims = _ask_axis("维度 D", available["dims"])
        if not args.ns:
            args.ns = _ask_axis("样本量 N", available["ns"])
    return _selection(config, args, available)
```

- [ ] **Step 2: 改 `cmd_run`**

```python
def cmd_run(config, args) -> int:
    root = data.ensure_data(config)
    specs = selector.scan(root)
    selection = _resolve_selection(config, args, specs)
    if selection is None:
        print("必须显式指定 --mechanisms / --dims / --ns。可选值：", file=sys.stderr)
        print(json.dumps(selector.available(specs), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    chosen = _chosen(config, selection, args.limit)
    if not chosen:
        print("没有匹配的数据集，请检查选择条件", file=sys.stderr)
        return 2
    print(f"共 {len(chosen)} 集")
    for spec in chosen:
        print(" ", spec.dataset_id)
    if args.dry_run:
        return 0
    if not args.yes and sys.stdin.isatty() and sys.stdout.isatty():
        print(estimate_text(len(chosen), config))
        if input("确认开跑？[y/N] ").strip().lower() not in ("y", "yes"):
            print("已取消")
            return 0

    _require_secret(config)
    runner.execute(config, chosen, selection)
    return 0
```

- [ ] **Step 3: 加参数**

在 `run_parser.add_argument("--dry-run", ...)` 后加：

```python
    run_parser.add_argument("--seed", type=int, default=None)
    run_parser.add_argument("--yes", action="store_true")
```

- [ ] **Step 4: 非 TTY 回归**

Run: `general-agent-eval --config config.yaml run --mechanisms Linear --dims 10 --ns 500 --dry-run`
Expected: 打印集数列表，返回 0

Run: `general-agent-eval --config config.yaml run --dry-run < /dev/null`
Expected: 打印可用值与错误，返回 2

- [ ] **Step 5: 运行全部单测**

Run: `pytest -q`
Expected: 全部通过

---

### Task 3b: 算法列（case.json + results.csv）

**Files:**
- Modify: `src/general_agent_eval/runner.py:17-35,125-138,223-249,264-294`
- Test: `tests/test_evidence.py`

**Interfaces：**
- Produces: `runner._algorithms(events) -> str`；`runner._final_algorithm(case_dir, algorithms) -> str`；`case.json` 新增 `algorithms`；`results.csv` 新增 `algorithms` 列（位于 `final_algorithm` 前）

- [ ] **Step 1: 写失败测试**

在 `tests/test_evidence.py` 末尾加（`Path`、`runner` 已导入）：

```python
def test_algorithms_detects_all_and_final_prefers_answer(tmp_path: Path):
    events = [{"message": {"content": [
        {"type": "tool_use", "input": {"code": "from causallearn.search.ConstraintBased.PC import pc"}},
        {"type": "tool_use", "input": {"code": "directlingam"}},
    ]}}]
    assert runner._algorithms(events) == "DirectLiNGAM;PC"

    case_dir = tmp_path / "case"
    (case_dir / "answer").mkdir(parents=True)
    assert runner._final_algorithm(case_dir, "DirectLiNGAM;PC") == "DirectLiNGAM;PC"

    (case_dir / "answer" / "algorithm.txt").write_text(
        "GES (BDeu) + DirectLiNGAM orientation\n", encoding="utf-8"
    )
    assert runner._final_algorithm(case_dir, "DirectLiNGAM;PC") == "GES (BDeu) + DirectLiNGAM orientation"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_evidence.py::test_algorithms_detects_all_and_final_prefers_answer -q`
Expected: FAIL，`AttributeError: module ... has no attribute '_algorithms'`

- [ ] **Step 3: 实现检测**

把现有 `_final_algorithm` 替换为：

```python
def _algorithms(events) -> str:
    text = "\n".join(
        json.dumps(block.get("input") or {}, ensure_ascii=False)
        for event in events
        for block in (event.get("message") or {}).get("content") or ()
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ).lower()
    found = sorted(name for name, keys in ALGORITHM_PATTERNS.items() if any(k in text for k in keys))
    return ";".join(found)


def _final_algorithm(case_dir: Path, algorithms: str) -> str:
    choice = case_dir / "answer" / "algorithm.txt"
    if choice.exists():
        lines = choice.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if lines:
            return lines[0].strip()[:50]
    return algorithms
```

- [ ] **Step 4: 接进 payload 与 CSV**

`_execute_case` 中 `payload = {` 前加一行：

```python
    algorithms = _algorithms(events)
```

`payload` 里把 `"final_algorithm": _final_algorithm(case_dir, events),` 替换为：

```python
        "algorithms": algorithms,
        "final_algorithm": _final_algorithm(case_dir, algorithms),
```

`RESULT_HEADER` 里 `"final_algorithm"` 前加 `"algorithms"`。

`_row` 返回字典里 `"final_algorithm"` 前加：

```python
        "algorithms": payload.get("algorithms", ""),
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest -q`
Expected: 全部通过

---

### Task 4: 报告补充

**Files:**
- Modify: `src/general_agent_eval/report.py:41-52,61-120,163-190`
- Test: `tests/test_report.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_report.py
import csv
import json
from pathlib import Path

from general_agent_eval import report


def _case(path: Path, dataset: str, status: str, algorithm: str,
          duration: float, cost, tokens_in, tokens_out):
    case_dir = path / "cases" / dataset.replace("/", "__")
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text(json.dumps({
        "dataset": dataset,
        "mechanism": "M",
        "dim": 10,
        "n": 100,
        "status": status,
        "reason": "",
        "final_algorithm": algorithm,
        "duration_s": duration,
        "evidence": {"pre": True, "post": True},
        "metrics": {"f1": 0.5},
        "usage": {
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "total_cost_usd": cost,
        },
    }), encoding="utf-8")


def test_report_adds_algorithms_cost_and_failures(tmp_path: Path):
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "r",
        "runtime": {"mode": "docker"},
        "timeout_s": 600,
    }), encoding="utf-8")
    _case(run_dir, "M/D010_N100/rep00", "success", "GES (BDeu) + DirectLiNGAM orientation", 100, 0.5, 100, 50)
    _case(run_dir, "M/D010_N100/rep01", "success", "PC", 200, 0.25, 50, 25)
    _case(run_dir, "M/D010_N100/rep02", "invalid_answer", "", 30, 0.1, 10, 5)

    baseline = tmp_path / "baseline.csv"
    with baseline.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dataset", "f1"])
        writer.writerow(["M/D010_N100/rep00", "0.5"])

    text = report.build(run_dir, baseline).read_text(encoding="utf-8")
    assert "## 算法选择" in text
    assert "| GES | 1 |" in text
    assert "| DirectLiNGAM | 1 |" in text
    assert "| PC | 1 |" in text
    assert "| （未识别） | 1 |" in text
    assert "## 成本与耗时" in text
    assert "token 输入 150" in text
    assert "$0.7500" in text
    assert "全部 case 成本合计（含未计分）：$0.8500" in text
    assert "| M/D010_N100/rep02 | invalid_answer |  | 30 | - |" in text
    assert "样本不一致：agent 计分 2 集 / 基线匹配 1 集" in text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_report.py -q`
Expected: FAIL，`## 算法选择` 不存在

- [ ] **Step 3: 扩展 `_flat`**

```python
def _flat(case) -> dict:
    metrics = case.get("metrics") or {}
    usage = case.get("usage") or {}
    return {
        "dataset": case.get("dataset", ""),
        "mechanism": case.get("mechanism", ""),
        "dim": case.get("dim"),
        "n": case.get("n"),
        "status": case.get("status", ""),
        "reason": case.get("reason", ""),
        "duration_s": case.get("duration_s"),
        "final_algorithm": case.get("final_algorithm") or "",
        "tokens_input": usage.get("tokens_input"),
        "tokens_output": usage.get("tokens_output"),
        "total_cost_usd": usage.get("total_cost_usd"),
        **{key: metrics.get(key) for key in METRICS},
    }
```

- [ ] **Step 4: 基线样本一致性**

在 `summary` 写入后、状态统计前加：

```python
    matched_scored = [row for row in scored if row["dataset"] in baseline]
```

把该段列表字面量整体替换为：

```python
    lines += [
        "",
        "## 总体指标（仅计分集）",
        "",
        f"计分集：{len(scored)} / {len(rows)}",
    ]
    if baseline and len(matched_scored) < len(scored):
        lines.append(
            f"> 样本不一致：agent 计分 {len(scored)} 集 / "
            f"基线匹配 {len(matched_scored)} 集（按 dataset 匹配）。"
        )
    lines += [
        "",
        "| 指标 | Agent | 基线 |",
        "| --- | --- | --- |",
    ]
```

- [ ] **Step 5: 追加算法与成本段**

`report.py` 顶部导入加 `from . import runner`；在 `_flat` 后加归一化函数：

```python
def _canonical(text) -> list[str]:
    lowered = str(text or "").lower()
    return [
        name
        for name, keys in runner.ALGORITHM_PATTERNS.items()
        if any(key in lowered for key in keys)
    ]
```

在“分机制”表格循环之后、`failed = [...]` 之前插入：

```python
    algorithms: dict[str, int] = {}
    for row in rows:
        for name in _canonical(row["final_algorithm"]) or ["（未识别）"]:
            algorithms[name] = algorithms.get(name, 0) + 1
    lines += ["", "## 算法选择", "", "| 算法 | 集数 |", "| --- | --- |"]
    for name in sorted(algorithms, key=lambda item: (-algorithms[item], item)):
        lines.append(f"| {name} | {algorithms[name]} |")

    by_mechanism: dict[str, dict[str, int]] = {}
    for row in rows:
        families = _canonical(row["final_algorithm"])
        if not families:
            continue
        counts = by_mechanism.setdefault(row["mechanism"], {})
        for name in families:
            counts[name] = counts.get(name, 0) + 1
    lines += ["", "按机制众数：", "", "| 机制 | 众数 | 集数 |", "| --- | --- | --- |"]
    if by_mechanism:
        for mechanism in sorted(by_mechanism):
            counts = by_mechanism[mechanism]
            mode = max(counts, key=lambda item: (counts[item], item))
            lines.append(f"| {mechanism} | {mode} | {counts[mode]} |")
    else:
        lines.append("| - | - | - |")
```

    def _sum(rows, key):
        values = [_num(row.get(key)) for row in rows]
        values = [value for value in values if value is not None]
        return sum(values) if values else None

    durations = [_num(row.get("duration_s")) for row in scored]
    durations = [value for value in durations if value is not None]
    avg_duration = sum(durations) / len(durations) if durations else None
    scored_cost = _sum(scored, "total_cost_usd")
    lines += [
        "",
        "## 成本与耗时",
        "",
        f"- 计分集：平均耗时 {_fmt(round(avg_duration, 1)) if avg_duration is not None else '-'}s，"
        f"token 输入 {_fmt(_sum(scored, 'tokens_input'))} / "
        f"输出 {_fmt(_sum(scored, 'tokens_output'))}，"
        f"成本 ${_fmt(scored_cost)}",
        f"- 全部 case 成本合计（含未计分）：${_fmt(_sum(rows, 'total_cost_usd'))}",
    ]
```

- [ ] **Step 6: 失败表补列**

把“未计分集”表头和行替换为：

```python
        lines += [
            "| dataset | 状态 | 原因 | 耗时(s) | 算法 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in failed:
            lines.append(
                f"| {row['dataset']} | {row['status']} | {row.get('reason', '')} "
                f"| {_fmt(row.get('duration_s'))} | {row.get('final_algorithm') or '-'} |"
            )
```

- [ ] **Step 7: 运行测试**

Run: `pytest tests/test_report.py -q`
Expected: PASS

Run: `pytest -q`
Expected: 全部通过

---

### Task 5: 文档

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 run 说明**

在命令表 `run` 行改为：

```markdown
| `run` | 缺参时 TTY 交互多选；`all` 全选；`--yes` 跳过确认；`--dry-run` 只选集 |
```

并在快速开始中加一行：

```bash
# 交互选择（不传三参）
general-agent-eval --config config.yaml run
```

- [ ] **Step 2: 报告说明**

在 `run.json` 段落后补：

```markdown
报告新增算法选择分布、按机制众数、token/成本/耗时汇总；未计分集带耗时与算法；
基线按 dataset 匹配不足时标注样本不一致。`results.csv` 新增 `algorithms` 列记录本集
试过的全部算法，`final_algorithm` 保持原始文本。
```

---

### Task 6: 全量验证

- [ ] **Step 1: 单测与自检**

Run: `pytest -q`
Expected: 全部通过（20 + 新增约 10）

Run: `python -m general_agent_eval.selftest`
Expected: `selftest OK`

- [ ] **Step 2: 非 TTY 回归**

Run: `general-agent-eval --config config.yaml run --dry-run < /dev/null`
Expected: 返回 2，打印可用值

- [ ] **Step 3: 旧 run 报告**

Run: `general-agent-eval --config config.yaml report --run ~/general-agent-eval-runs/runs/20261111-164624`
Expected: 正常生成，新段缺失字段显示 `-`

- [ ] **Step 4: 检查工作区**

Run: `git status --short | grep general-agent-eval`
Expected: 只含本计划涉及文件

---

## Self-Review

- 设计 §3 交互选择：Task 2/3 覆盖；非 TTY 行为 Task 3 Step 4 验证
- 设计 §3.3 预估常量：Task 1/2 覆盖
- 设计 §4.1-4.4：Task 4 覆盖
- 设计 §4.5 算法列：Task 3b 覆盖（`case.json`/`results.csv`）
- 设计 §6 测试：Task 2/3b/4 覆盖；交互 TTY 人工项列在验收
- 设计 §7 验收：Task 6 覆盖
