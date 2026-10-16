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


def _numeric(values: list) -> list[float]:
    """只保留可转换为数值的项；missing/error 状态被排除，不会当成 0。"""
    result: list[float] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def _queue_field(sample: dict, name: str):
    """读取一次采样里的队列长度字段；状态不是 collected 时返回 None。"""
    queue = sample.get("queue") or {}
    if queue.get("status") != "collected":
        return None
    return queue.get(name)


def _connection_field(sample: dict, name: str):
    """读取一次采样里的数据库连接字段；状态不是 collected 时返回 None。"""
    connections = sample.get("connections") or {}
    if connections.get("status") != "collected":
        return None
    return connections.get(name)


def _last_field(samples: list, name: str) -> str | None:
    """取最后一次采样里的字符串字段；缺失时返回 None。"""
    for sample in reversed(samples):
        containers = sample.get("containers") or {}
        value = containers.get(name)
        if value:
            return str(value)
    return None


def _host_field(sample: dict, name: str):
    """读取一次采样的主机字段，同时兼容旧的扁平格式与新的 host 嵌套格式。"""
    host = sample.get("host")
    if isinstance(host, dict):
        return host.get(name)
    return sample.get(name)


def summarize(sample: dict) -> dict:
    """把原始采样换算为窗口内的资源摘要。

    CPU 使用率由相邻两次采样的 jiffies 差值计算，避免依赖某个瞬间的读数；
    内存水位取窗口内的最低可用值，因为那是容量压力的实际边界。
    """
    cpu_rates: list[float] = []
    samples = sample.get("samples") or []
    for previous, current in zip(samples, samples[1:]):
        previous_total = _host_field(previous, "cpu_total_jiffies")
        current_total = _host_field(current, "cpu_total_jiffies")
        previous_idle = _host_field(previous, "cpu_idle_jiffies")
        current_idle = _host_field(current, "cpu_idle_jiffies")
        if None in (previous_total, current_total, previous_idle, current_idle):
            continue
        total_delta = current_total - previous_total
        idle_delta = current_idle - previous_idle
        if total_delta <= 0:
            continue
        cpu_rates.append(round((1 - idle_delta / total_delta) * 100, 1))
    loads = _numeric([_host_field(item, "load1") for item in samples])
    available = [value / 1024 for value in _numeric(
        [_host_field(item, "mem_available_kb") for item in samples]
    )]
    swap_free = [value / 1024 for value in _numeric(
        [_host_field(item, "swap_free_kb") for item in samples]
    )]
    queue_depth = _numeric([_queue_field(item, "queued") for item in samples])
    running_depth = _numeric([_queue_field(item, "running") for item in samples])
    draining_depth = _numeric([_queue_field(item, "draining") for item in samples])
    connections = _numeric([_connection_field(item, "threads_connected") for item in samples])
    summary = {
        "label": sample.get("label"),
        "schema_version": sample.get("schema_version"),
        "run_id": sample.get("run_id"),
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
        "queued_max": max(queue_depth) if queue_depth else None,
        "running_max": max(running_depth) if running_depth else None,
        "draining_max": max(draining_depth) if draining_depth else None,
        "threads_connected_max": max(connections) if connections else None,
        "container_states_last": _last_field(samples, "container_states"),
        "exited_containers_last": _last_field(samples, "exited_containers"),
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
        f"- 采样文件：{summary.get('schema_version')}（run_id {summary.get('run_id')}）",
        f"- 负载窗口：{summary.get('started_at')} 至 {summary.get('finished_at')}"
        f"（每 {summary.get('interval_seconds')} 秒采样，共 {summary.get('sample_count')} 次）",
        f"- CPU 使用率（{summary.get('cpu_count')} 核）：均值 {summary.get('cpu_percent_avg')}%，"
        f"峰值 {summary.get('cpu_percent_max')}%",
        f"- 1 分钟负载均值：均值 {summary.get('load1_avg')}，峰值 {summary.get('load1_max')}",
        f"- 可用内存最低值 {summary.get('mem_available_mb_min')} MB"
        f"（均值 {summary.get('mem_available_mb_avg')} MB），"
        f"swap 空闲最低值 {summary.get('swap_free_mb_min')} MB",
        f"- 队列深度峰值：排队 {_show(summary.get('queued_max'))}，运行 "
        f"{_show(summary.get('running_max'))}，draining {_show(summary.get('draining_max'))}",
        f"- 主库连接数峰值：{_show(summary.get('threads_connected_max'))}",
        f"- 窗口末容器：{_container_summary(summary)}",
    ]


def _show(value) -> str:
    """缺失的峰值显示为未采集，区别于真实的 0。"""
    if value is None:
        return "未采集"
    return str(int(value)) if float(value).is_integer() else str(value)


def _container_summary(summary: dict) -> str:
    """把窗口末容器状态压缩成“总数、运行数、已退出名单”。"""
    states = summary.get("container_states_last")
    if not states:
        return "未采集"
    entries = [item for item in str(states).split(",") if item.strip()]
    running = sum(1 for item in entries if "=running/" in item)
    exited = summary.get("exited_containers_last") or "无"
    return f"共 {len(entries)} 个，运行 {running} 个；已退出：{exited}"

