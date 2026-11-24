# 实现手册 4：报告、自检、验收与收尾

> 本阶段产出：可交付的 v1。
> 结束标准：`doctor` 全绿、`smoke --yes` 成功、报告能从 `case.json` 离线重建。

---

## 4.1 `src/general_agent_eval/report.py`

报告**只读 `case.json`**，不依赖 `results.csv`。这样即使 CSV 被中断截断，报告仍然完整。

```python
"""从 case.json 汇总生成 summary.csv 与 report.md。"""
from __future__ import annotations

import csv
import json
from pathlib import Path

SCORED = ("success", "timeout_partial")
METRICS = ("precision", "recall", "f1", "shd", "skeleton_precision", "skeleton_recall")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(rows, key):
    values = [_num(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _fmt(value):
    if value is None:
        return "-"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def _load_cases(run_dir):
    cases = []
    for path in sorted((Path(run_dir) / "cases").glob("*/case.json")):
        try:
            cases.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return cases


def _flat(case) -> dict:
    metrics = case.get("metrics") or {}
    return {
        "dataset": case.get("dataset", ""),
        "mechanism": case.get("mechanism", ""),
        "dim": case.get("dim"),
        "n": case.get("n"),
        "status": case.get("status", ""),
        "reason": case.get("reason", ""),
        **{key: metrics.get(key) for key in METRICS},
    }


def _baseline(path) -> dict:
    if not path or not Path(path).exists():
        return {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return {row["dataset"]: row for row in csv.DictReader(handle)}


def build(run_dir, baseline_path=None) -> Path:
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rows = [_flat(case) for case in _load_cases(run_dir)]
    scored = [row for row in rows if row["status"] in SCORED]
    baseline = _baseline(baseline_path)
    matched = [baseline[row["dataset"]] for row in rows if row["dataset"] in baseline]

    summary = [
        {
            "metric": key,
            "agent": _mean(scored, key),
            "baseline": _mean(matched, key) if matched else None,
        }
        for key in METRICS
    ]
    with (run_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "agent", "baseline"])
        writer.writeheader()
        writer.writerows(summary)

    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    lines = [
        "# 通用 Agent 因果发现评测报告",
        "",
        f"- 运行：`{run.get('run_id')}`",
        f"- 运行时：{run.get('runtime')}",
        f"- 镜像：`{run.get('image')}`",
        f"- 镜像指纹：`{run.get('image_digest')}`",
        f"- 模型：`{run.get('model')}`",
        f"- Claude CLI：{', '.join(run.get('claude_versions') or ['未知'])}",
        f"- 数据指纹：`{(run.get('data') or {}).get('fingerprint')}`",
        f"- 超时：{run.get('timeout_s')}s",
        "",
        "> **不可比声明**：本结果由容器化运行时产生。与旧 Windows 原生 harness 的结果"
        "（解释器、依赖版本、CLI 版本均不同）不能直接混合计算均值。",
        "",
        "## 状态分布",
        "",
        "| 状态 | 数量 |",
        "| --- | --- |",
    ]
    for status in sorted(status_counts):
        lines.append(f"| {status} | {status_counts[status]} |")

    lines += [
        "",
        "## 总体指标（仅计分集）",
        "",
        f"计分集：{len(scored)} / {len(rows)}",
        "",
        "| 指标 | Agent | 基线 |",
        "| --- | --- | --- |",
    ]
    for item in summary:
        lines.append(f"| {item['metric']} | {_fmt(item['agent'])} | {_fmt(item['baseline'])} |")

    lines += [
        "",
        "## 分格（D × N）",
        "",
        "| D | N | 集数 | 计分 | F1 | SHD | 骨架 P | 骨架 R |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    cells: dict = {}
    for row in rows:
        cells.setdefault((row["dim"], row["n"]), []).append(row)
    for key in sorted(cells, key=lambda item: (item[0] or -1, item[1] or -1)):
        group = cells[key]
        group_scored = [row for row in group if row["status"] in SCORED]
        lines.append(
            f"| {key[0]} | {key[1]} | {len(group)} | {len(group_scored)} "
            f"| {_fmt(_mean(group_scored, 'f1'))} | {_fmt(_mean(group_scored, 'shd'))} "
            f"| {_fmt(_mean(group_scored, 'skeleton_precision'))} "
            f"| {_fmt(_mean(group_scored, 'skeleton_recall'))} |"
        )

    lines += [
        "",
        "## 分机制",
        "",
        "| 机制 | 集数 | 计分 | F1 | 基线 F1 | ΔF1 | SHD |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    mechanisms: dict = {}
    for row in rows:
        mechanisms.setdefault(row["mechanism"], []).append(row)
    for mechanism in sorted(mechanisms):
        group = mechanisms[mechanism]
        group_scored = [row for row in group if row["status"] in SCORED]
        base_rows = [baseline[row["dataset"]] for row in group if row["dataset"] in baseline]
        agent_f1 = _mean(group_scored, "f1")
        base_f1 = _mean(base_rows, "f1") if base_rows else None
        delta = agent_f1 - base_f1 if agent_f1 is not None and base_f1 is not None else None
        lines.append(
            f"| {mechanism} | {len(group)} | {len(group_scored)} | {_fmt(agent_f1)} "
            f"| {_fmt(base_f1)} | {_fmt(delta)} | {_fmt(_mean(group_scored, 'shd'))} |"
        )

    failed = [row for row in rows if row["status"] not in SCORED]
    lines += ["", "## 未计分集", ""]
    if failed:
        lines += ["| dataset | 状态 | 原因 |", "| --- | --- | --- |"]
        for row in failed:
            lines.append(f"| {row['dataset']} | {row['status']} | {row.get('reason', '')} |")
    else:
        lines.append("无。")

    lines += [
        "",
        "## 口径",
        "",
        "- 计分状态：`success`、`timeout_partial`；其余状态不计入均值。",
        "- 指标：严格定向 P/R/F1、骨架 P/R、CPDAG 口径 SHD。",
        "- 输出为 0/1 矩阵，不计算 AUROC / AUPRC。",
        "- `timeout_partial` 表示超时但已落盘矩阵，质量通常低于 `success`，需单独看。",
        "",
    ]

    path = run_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

---

## 4.2 `src/general_agent_eval/preflight.py`

```python
"""环境自检。只读，不跑评测。"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from . import container, data


def _writable(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return str(path)


def _disk(path: Path) -> str:
    free = shutil.disk_usage(path).free / (1024 ** 3)
    if free < 10:
        raise RuntimeError(f"剩余空间仅 {free:.1f} GB")
    return f"{free:.1f} GB free"


def _image_entry(config, entrypoint, *args) -> str:
    proc = container.docker(
        ["run", "--rm", "--entrypoint", entrypoint, config["agent"]["image"], *args],
    )
    return proc.stdout.strip()


def doctor(config) -> int:
    checks = []

    def check(name, fn):
        try:
            checks.append((name, True, fn() or ""))
        except Exception as exc:
            checks.append((name, False, str(exc)[:200]))

    def secret():
        name = config["agent"]["auth_env"]
        if not os.environ.get(name):
            raise RuntimeError(f"缺少环境变量 {name}")
        return "已设置（值不打印）"

    def mount():
        workspace = Path(config["workspace_dir"])
        workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(workspace)) as tmp:
            case = Path(tmp) / "probe"
            case.mkdir()
            container.probe_mount(config, case)
        return "ok"

    check("docker", lambda: container.docker(
        ["version", "--format", "{{.Server.Version}}"]
    ).stdout.strip())
    check("image", lambda: container.image_digest(config["agent"]["image"]))
    check("auth env", secret)
    check("output dir", lambda: _writable(Path(config["output_dir"])))
    check("workspace dir", lambda: _writable(Path(config["workspace_dir"])))
    check("data", lambda: str(data.ensure_data(config)))
    check("toolbox", lambda: _image_entry(
        config, "python3", "-c",
        "import numpy,pandas,sklearn,lingam;"
        "from causallearn.utils.DAG2CPDAG import dag2cpdag;print('ok')",
    ))
    check("claude", lambda: _image_entry(config, "claude", "--version"))
    check("mount", mount)
    check("disk", lambda: _disk(Path(config["output_dir"])))

    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{'OK  ' if ok else 'FAIL'} {name:<{width}}  {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1
```

`doctor` 的十项检查对应十类常见事故：daemon 没起、镜像没构建、密钥没导出、目录不可写、数据路径错、工具链没装上、CLI 没装上、挂载失败、空间不足。

---

## 4.3 验证：doctor

```bash
cd ~/CausalAgent3/general-agent-eval
source .venv/bin/activate
export DEEPSEEK_API_KEY=你的key
general-agent-eval --config config.yaml doctor
```

预期全部 `OK`。任何一项 `FAIL` 先修，别跑批次。

---

## 4.4 验证：报告可离线重建

先跑一集：

```bash
general-agent-eval --config config.yaml smoke --yes
```

不带 `--yes` 时应拒绝：

```bash
general-agent-eval --config config.yaml smoke
echo $?    # 2
```

找到运行目录并重建报告：

```bash
RUN=$(ls -dt ~/general-agent-eval-runs/runs/* | head -1)
general-agent-eval --config config.yaml report --run "$RUN"
cat "$RUN/report.md"
cat "$RUN/summary.csv"
```

确认：

- 报告里的计分集数与 `cases/*/case.json` 中 `success`/`timeout_partial` 的数量一致；
- 删除 `results.csv` 后再跑 `report`，报告仍然完整（证明只依赖 `case.json`）。

---

## 4.5 基线对比（可选）

把 `config.yaml` 的 `baseline_results` 指向基线 CSV：

```yaml
baseline_results: /home/yuki/CausalAgent3/evaluation-runner/local/staging-full/20261102-220224-be4bfdd/results.csv
```

然后重新生成报告。报告会只对**两边都存在的 dataset** 求基线均值，并在分机制表里给出 ΔF1。

> 如果基线的机制名或 D/N 命名与本次不同，Δ 会偏；先对齐 `dataset_id` 格式再比。

---

## 4.6 交付前验收清单

### 正确性

- [ ] 同一输入下 `metrics` 与 `evaluation-runner/evaluate.py` 分数一致
- [ ] `parse` 覆盖表头 / 行标签 / true-false / 多余列 / 形状错 / 对角线 / 非 0/1
- [ ] 状态语义正确：`docker_error`、`docker_oom`、`timeout`、`timeout_partial` 不混用
- [ ] 只有 `success` + `timeout_partial` 计分
- [ ] 报告声明"与旧 harness 结果不可比"

### 容器

- [ ] 不加 `--rm`，先 `inspect` 再删
- [ ] 每集挂载探针通过才继续
- [ ] Linux 下 `--user uid:gid` 生效，case 文件归当前用户
- [ ] 业务超时 600s → TERM → 30s → KILL
- [ ] Ctrl-C 后按 `gae.run` 标签清理干净
- [ ] 失败时容器默认保留，可 `shell` 进入

### 证据链

- [ ] `run.json` 有镜像指纹、模型、超时、selection、数据指纹、宿主机信息
- [ ] `case.json` 有容器退出码、OOM 标志、`pre/post` 是否存在
- [ ] `raw/*.jsonl` 与 `raw/*.inspect.json` 落盘
- [ ] `inspect.json` 里的密钥已脱敏
- [ ] `pre.json` 有 `claude --version` 与 `pip freeze`

### 安全

- [ ] 配置、镜像、报告中无密钥明文
- [ ] 数据集与金标准不挂进容器
- [ ] 容器只挂当前 case 目录
- [ ] `--dangerously-skip-permissions` 有注释说明边界

### 可用性

- [ ] 不带 `--mechanisms/--dims/--ns` 时拒绝执行并列出可选项
- [ ] `smoke` 必须 `--yes`
- [ ] `resume` 按 `case.json` 状态判断，失败集重跑
- [ ] `report` 可在不重跑的情况下重建

---

## 4.7 排错速查

| 现象 | 先看哪里 | 判断 |
| --- | --- | --- |
| 一集很快 `claude_error` | `raw/<ds>.jsonl` 末尾 | CLI 启动错误 / 额度 / 网络 |
| `docker_error exit 125` | `docker images` | 镜像名或构建问题 |
| `docker_oom` | `inspect.json` 的 `State.OOMKilled` | 调大 `docker.memory` 或减小任务 |
| 超时但没答案 | `case.json` 的 `duration_s` | 模型没在 600s 内落盘 |
| 答案在但 F1=0 | `answer/adjacency.csv` 内容 | 可能是方向整体反了，或解析错 |
| 宿主看不到 answer | 容器内 `ls /work` | 挂载/UID 问题 |
| `pre.json` 缺失 | `docker logs <name>` | 入口脚本或权限 |
| `post.json` 缺失 | 退出码 137/143 | 被强制杀死，环境证据不完整 |
| 报告全是 "-" | `cases/*/case.json` 的 `metrics` | 可能全部未计分 |
| resume 重复跑已完成集 | `case.json` 的 `status` | 状态拼写与 `FINAL` 不一致 |

---

## 4.8 交付前最后三件事

1. **锁版本**：镜像 tag 固定、`CLAUDE_VERSION` 固定、`toolbox.lock` 固定。
2. **留基线指纹**：把 `docker inspect` 的 digest 写进 README，换机器时先比对。
3. **归档一次运行**：整个 `runs/<run_id>/` 打包，包含 `run.json`、`case.json`、`raw/`，别再单独拷 `results.csv`。

---

## 4.9 后续怎么扩展（现在别做）

| 想做的事 | 最小改动 |
| --- | --- |
| 并发跑 | `runner.execute` 的 for 循环换成线程池；`jobs` 从配置读。注意日志/容器名要带 case 号 |
| 原生运行时 | 新增 `native.py`，实现与 `container.run_container` 相同的 `Result`，用配置 `runtime` 分派 |
| 推镜像到仓库 | 构建时加 registry 前缀；`image_digest` 会自然变成 `repo@sha256:...` |
| 网络白名单 | 给 `docker run` 加 `--network` 到自建网络，或在宿主做代理 |
| LLM judge | 实现 `judge.py`，在 `runner` 末尾追加调用；报告里单列，不参与矩阵判分 |

---

## 4.10 这个项目对应的工程能力

| 能力 | 在本项目里的具体体现 |
| --- | --- |
| 容器与进程 | PID 1 转发信号、退出码分类、OOM 识别、资源限制、镜像分层 |
| 可复现性 | 镜像指纹、CLI 版本、依赖快照、数据指纹、`run.json` 证据链 |
| 数据正确性 | 与参考实现对拍、CPDAG 口径、严格解析不做阈值化 |
| 测试分层 | 离线 selftest / 环境 doctor / 付费 smoke |
| 可观测性 | 原始事件流、inspect 落盘、失败保留容器、trace.csv |
| 安全 | 密钥只走环境变量、inspect 脱敏、最小挂载面、金标准不进容器 |
| CLI 设计 | 无默认选择、dry-run、报告可离线重建、危险操作需 `--yes` |
| 取舍 | v1 不做并发/原生/judge，先把单机串行做对 |

这些点面试时都能展开成"我遇到过什么问题、为什么这样设计"，比背概念有效。

---

## 4.11 完成

按顺序走完 00 → 04，你就有了一个可以换机器跑的 v1。下一步建议：

1. 用 `--limit 3` 跑三种机制各一集，检查分机制表是否合理；
2. 再跑一个完整格（14 集），确认耗时和成本；
3. 最后再放开多格，并保留原始运行目录。
