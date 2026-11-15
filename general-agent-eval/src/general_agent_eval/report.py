"""从 case.json 汇总生成 summary.csv 与 report.md。"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from . import runner

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


def _dict(value):
    return value if isinstance(value, dict) else {}


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


def _canonical(text) -> list[str]:
    lowered = str(text or "").lower()
    return [
        name
        for name, keys in runner.ALGORITHM_PATTERNS.items()
        if any(key in lowered for key in keys)
        or re.search(rf"\b{re.escape(name.lower())}\b", lowered)
    ]


def _baseline(path) -> dict:
    if not path or not Path(path).exists():
        return {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return {row["dataset"]: row for row in csv.DictReader(handle)}


def build(run_dir, baseline_path=None) -> Path:
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    cases = _load_cases(run_dir)
    rows = [_flat(case) for case in cases]
    runtime = _dict(run.get("runtime"))
    agent = _dict(run.get("agent"))
    toolbox = _dict(run.get("toolbox"))
    dataset = _dict(run.get("data"))
    scored = [row for row in rows if row["status"] in SCORED]
    baseline = _baseline(baseline_path)
    matched = [baseline[row["dataset"]] for row in rows if row["dataset"] in baseline]
    matched_scored = [row for row in scored if row["dataset"] in baseline]

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
        f"- 运行时：{runtime.get('mode') or 'docker'}",
        f"- 镜像：`{runtime.get('image') or '未知'}`",
        f"- 镜像指纹：`{runtime.get('image_id') or '未知'}`",
        f"- Docker：{runtime.get('docker_version') or '未知'}",
        f"- 模型：`{agent.get('model') or '未知'}`",
        f"- Claude CLI：{agent.get('claude_version') or '未知'}",
        f"- 数据指纹：`{dataset.get('dataset_sha256') or '未知'}`",
        f"- 工具箱 lock：`{str(toolbox.get('lock_sha256') or '未知')[:12]}`",
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

    missing = [
        case.get("case") or "?"
        for case in cases
        if not (case.get("evidence") or {}).get("pre")
        or not (case.get("evidence") or {}).get("post")
    ]
    drift = run.get("env_drift_cases") or []
    lines += ["", "## 环境证据", ""]
    if missing:
        lines.append(f"- 证据不完整：{len(missing)} 例（缺 pre 或 post）：{', '.join(missing)}")
    else:
        lines.append("- 全部 case 的 pre/post 证据完整。")
    if drift:
        lines.append(f"- 环境漂移（运行期依赖变化）：{', '.join(drift)}")
    else:
        lines.append("- 未检测到环境漂移。")

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

    def _sum(group, key):
        values = [_num(row.get(key)) for row in group]
        values = [value for value in values if value is not None]
        return sum(values) if values else None

    durations = [value for value in (_num(row.get("duration_s")) for row in scored) if value is not None]
    avg_duration = f"{sum(durations) / len(durations):.1f}" if durations else "-"
    tokens_in = _sum(scored, "tokens_input")
    tokens_out = _sum(scored, "tokens_output")
    lines += [
        "",
        "## 成本与耗时",
        "",
        f"- 计分集：平均耗时 {avg_duration}s，"
        f"token 输入 {int(tokens_in) if tokens_in is not None else '-'} / "
        f"输出 {int(tokens_out) if tokens_out is not None else '-'}，"
        f"成本 ${_fmt(_sum(scored, 'total_cost_usd'))}",
        f"- 全部 case 成本合计（含未计分）：${_fmt(_sum(rows, 'total_cost_usd'))}",
    ]

    failed = [row for row in rows if row["status"] not in SCORED]
    lines += ["", "## 未计分集", ""]
    if failed:
        lines += [
            "| dataset | 状态 | 原因 | 耗时(s) | 算法 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in failed:
            lines.append(
                f"| {row['dataset']} | {row['status']} | {row.get('reason', '')} "
                f"| {_fmt(row.get('duration_s'))} | {row.get('final_algorithm') or '-'} |"
            )
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
