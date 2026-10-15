"""把一次负载窗口内的主机资源采样汇总为可渲染的曲线摘要。

采样文件由 loadtest/stage_resource_sample.sh 在服务器上生成，产出原始计数器；
本模块换算为 CPU 使用率、峰值负载与内存水位，供逐档报告引用。
"""

from __future__ import annotations

import json
from pathlib import Path


def load_sample(path: Path) -> dict | None:
    """读取一次资源采样文件；文件缺失或无法解析时返回 None。"""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def summarize(sample: dict) -> dict:
    """把原始采样换算为窗口内的资源摘要。

    CPU 使用率由相邻两次采样的 jiffies 差值计算，避免依赖某个瞬间的读数；
    内存水位取窗口内的最低可用值，因为那是容量压力的实际边界。
    """
    samples = sample.get("samples") or []
    cpu_rates: list[float] = []
    for previous, current in zip(samples, samples[1:]):
        total_delta = current["cpu_total_jiffies"] - previous["cpu_total_jiffies"]
        idle_delta = current["cpu_idle_jiffies"] - previous["cpu_idle_jiffies"]
        if total_delta <= 0:
            continue
        cpu_rates.append(round((1 - idle_delta / total_delta) * 100, 1))
    loads = [item["load1"] for item in samples]
    available = [item["mem_available_kb"] / 1024 for item in samples]
    swap_free = [item["swap_free_kb"] / 1024 for item in samples]
    summary = {
        "label": sample.get("label"),
        "started_at": sample.get("started_at"),
        "finished_at": sample.get("finished_at"),
        "interval_seconds": sample.get("interval_seconds"),
        "cpu_count": sample.get("cpu_count"),
        "sample_count": len(samples),
        "cpu_percent_avg": round(sum(cpu_rates) / len(cpu_rates), 1) if cpu_rates else None,
        "cpu_percent_max": max(cpu_rates) if cpu_rates else None,
        "load1_avg": round(sum(loads) / len(loads), 2) if loads else None,
        "load1_max": max(loads) if loads else None,
        "mem_available_mb_min": round(min(available), 1) if available else None,
        "mem_available_mb_avg": round(sum(available) / len(available), 1) if available else None,
        "swap_free_mb_min": round(min(swap_free), 1) if swap_free else None,
    }
    return summary


def render(summary: dict | None) -> list[str]:
    """把资源摘要渲染为报告段落；没有采样时如实说明。"""
    if not summary:
        return [
            "- 负载窗口资源曲线：未采集（需要在服务器上并行运行 "
            "loadtest/stage_resource_sample.sh）",
        ]
    return [
        f"- 负载窗口：{summary.get('started_at')} 至 {summary.get('finished_at')}"
        f"（每 {summary.get('interval_seconds')} 秒采样，共 {summary.get('sample_count')} 次）",
        f"- CPU 使用率（{summary.get('cpu_count')} 核）：均值 {summary.get('cpu_percent_avg')}%，"
        f"峰值 {summary.get('cpu_percent_max')}%",
        f"- 1 分钟负载均值：均值 {summary.get('load1_avg')}，峰值 {summary.get('load1_max')}",
        f"- 可用内存最低值 {summary.get('mem_available_mb_min')} MB"
        f"（均值 {summary.get('mem_available_mb_avg')} MB），"
        f"swap 空闲最低值 {summary.get('swap_free_mb_min')} MB",
    ]

