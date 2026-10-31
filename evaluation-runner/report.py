"""从 results 行生成 summary 与 Markdown 报告。"""
from __future__ import annotations

from collections import Counter, defaultdict

SUMMARY_HEADER = [
    "run_id", "started_at", "git_commit", "base_url", "data_dir", "seed", "per_cell",
    "parallel", "mechanisms", "datasets", "succeeded", "failed", "recall_mean",
    "precision_mean", "skeleton_recall_mean", "skeleton_precision_mean",
    "f1_mean", "shd_mean", "auroc_mean", "auprc_mean", "auroc_n", "auprc_n",
    "duration_mean_s", "interactions_mean", "algorithm_distribution", "failure_reasons",
]


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _present(rows, key):
    return [float(row[key]) for row in rows if row.get(key, "") not in ("", None)]


def _mean_present(rows, key):
    values = _present(rows, key)
    return sum(values) / len(values) if values else ""


def _fmt(value, digits=4):
    return "-" if value in ("", None) else f"{float(value):.{digits}f}"


def _pairs(counter: Counter) -> str:
    return ";".join(f"{key}:{value}" for key, value in sorted(counter.items())) or ""


def latest(rows: list[dict]) -> list[dict]:
    """同一 dataset 只保留最后一行（--resume 重跑会追加）。"""
    by_dataset = {}
    for row in rows:
        by_dataset[row["dataset"]] = row
    return list(by_dataset.values())


def summarize(rows: list[dict], meta: dict) -> dict:
    rows = latest(rows)
    succeeded = [row for row in rows if row["status"] in ("success", "partial")]
    failed = [row for row in rows if row["status"] == "failed"]
    algorithms = Counter(row["final_algorithm"] for row in succeeded if row["final_algorithm"])
    reasons = Counter(row["failure_reason"] for row in failed if row["failure_reason"])
    sampling = meta.get("sampling") or {}
    return {
        "run_id": meta.get("run_id", ""),
        "started_at": meta.get("started_at", ""),
        "git_commit": meta.get("git_commit", ""),
        "base_url": meta.get("base_url", ""),
        "data_dir": meta.get("data_dir", ""),
        "seed": sampling.get("seed", ""),
        "per_cell": sampling.get("per_cell", ""),
        "parallel": meta.get("parallel", ""),
        "mechanisms": sampling.get("mechanisms", ""),
        "datasets": len(rows),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "recall_mean": _mean([row["recall"] for row in succeeded]),
        "precision_mean": _mean([row["precision"] for row in succeeded]),
        "skeleton_recall_mean": _mean([row["skeleton_recall"] for row in succeeded]),
        "skeleton_precision_mean": _mean([row["skeleton_precision"] for row in succeeded]),
        "f1_mean": _mean_present(succeeded, "f1"),
        "shd_mean": _mean_present(succeeded, "shd"),
        "auroc_mean": _mean_present(succeeded, "auroc"),
        "auprc_mean": _mean_present(succeeded, "auprc"),
        "auroc_n": len(_present(succeeded, "auroc")),
        "auprc_n": len(_present(succeeded, "auprc")),
        "duration_mean_s": _mean([row["duration_s"] for row in succeeded]),
        "interactions_mean": _mean([row["interactions"] for row in succeeded]),
        "algorithm_distribution": _pairs(algorithms),
        "failure_reasons": _pairs(reasons),
    }


def _table(title: str, groups: dict[str, list[dict]]) -> list[str]:
    lines = [
        f"### {title}", "",
        "| 分组 | n | recall | precision | F1 | SHD | 骨架 recall | 骨架 precision |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key in sorted(groups):
        items = [row for row in groups[key] if row["status"] in ("success", "partial")]
        if not items:
            lines.append(f"| {key} | 0 | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| {key} | {len(items)} | {_fmt(_mean_present(items, 'recall'))} "
            f"| {_fmt(_mean_present(items, 'precision'))} "
            f"| {_fmt(_mean_present(items, 'f1'))} "
            f"| {_fmt(_mean_present(items, 'shd'), 2)} "
            f"| {_fmt(_mean_present(items, 'skeleton_recall'))} "
            f"| {_fmt(_mean_present(items, 'skeleton_precision'))} |"
        )
    return lines + [""]


def _key(value) -> str:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return "-"


def report_markdown(rows: list[dict], meta: dict) -> str:
    rows = latest(rows)
    summary = summarize(rows, meta)
    by_mechanism, by_dim, by_n = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        by_mechanism[row["mechanism"] or "-"].append(row)
        by_dim[_key(row["dim"])].append(row)
        by_n[_key(row["n_rows"])].append(row)

    lines = ["# 评测报告", ""]
    lines += [
        f"- run_id: `{summary['run_id']}`",
        f"- commit: `{summary['git_commit']}`",
        f"- base_url: {summary['base_url']}",
        f"- 抽样: seed={summary['seed']} per_cell={summary['per_cell']} parallel={summary['parallel']}",
        f"- 数据集: {summary['datasets']}（成功 {summary['succeeded']} / 失败 {summary['failed']}）",
        "",
        "## 总体",
        "",
        f"- recall {_fmt(summary['recall_mean'])}，precision {_fmt(summary['precision_mean'])}，"
        f"F1 {_fmt(summary['f1_mean'])}，SHD {_fmt(summary['shd_mean'], 2)}",
        f"- 骨架 recall {_fmt(summary['skeleton_recall_mean'])}，"
        f"骨架 precision {_fmt(summary['skeleton_precision_mean'])}",
        f"- AUROC {_fmt(summary['auroc_mean'])}，AUPRC {_fmt(summary['auprc_mean'])}"
        f"（CDFM 主结果 {summary['auroc_n']} 集）",
        f"- 平均耗时 {summary['duration_mean_s']:.1f}s，平均交互 {summary['interactions_mean']:.2f} 次",
        f"- 算法分布: {summary['algorithm_distribution'] or '-'}",
        f"- 失败分布: {summary['failure_reasons'] or '-'}",
        "",
    ]
    lines += _table("按机制", by_mechanism)
    lines += _table("按 D", by_dim)
    lines += _table("按 N", by_n)

    failures = [row for row in rows if row["status"] == "failed"]
    lines += ["## 失败明细", ""]
    lines += [f"- `{row['dataset']}`: {row['failure_reason']}" for row in failures] or ["- 无"]
    worst = sorted(
        (row for row in rows if row["status"] in ("success", "partial")),
        key=lambda row: row["recall"],
    )[:10]
    lines += ["", "## recall 最低 10 集", ""]
    lines += [
        f"- `{row['dataset']}`: recall {row['recall']:.3f}, precision {row['precision']:.3f}"
        for row in worst
    ] or ["- 无"]
    lines += [
        "",
        "## 口径",
        "",
        "- recall/precision 为严格定向口径：只有 directed 边计 TP，无向类边各计 1 FP，镜像对按一个无向边计。",
        "- 骨架口径把边无向化后比较，两套数值不可混比。",
        "- TimeLag 是否纳入见抽样配置；diagnostic 报告不计入均值。",
        "",
    ]
    return "\n".join(lines)
