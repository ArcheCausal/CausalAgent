"""把一次负载窗口内的主机资源采样汇总为可渲染的曲线摘要。

采样文件由 loadtest/stage_resource_sample.sh 在服务器上生成，产出原始计数器；
本模块换算为 CPU 使用率、峰值负载与内存水位，供逐档报告引用。
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path


def load_sample(path: Path) -> dict | None:
    """读取一次资源采样文件；文件缺失或无法解析时返回 None。"""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _numeric(values: list) -> list[float]:
    """只保留可转换为数值的项；missing/error 状态被排除，不会当成 0。"""
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
            if not isinstance(value, bool) and math.isfinite(number):
                result.append(number)
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
        if host.get("status") in {"missing", "error", "unknown"}:
            return None
        return host.get(name, sample.get(name))
    return sample.get(name)


def summarize(sample: dict, *, _with_phases: bool = True) -> dict:
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
        values = _numeric([previous_total, current_total, previous_idle, current_idle])
        if len(values) != 4:
            continue
        previous_total, current_total, previous_idle, current_idle = values
        total_delta = current_total - previous_total
        idle_delta = current_idle - previous_idle
        if total_delta <= 0 or not 0 <= idle_delta <= total_delta:
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
        "status": sample.get("status", "legacy"),
        "observation_status": sample.get("observation_status", "missing"),
        "run_id": sample.get("run_id"),
        "started_at": sample.get("started_at"),
        "finished_at": sample.get("finished_at"),
        "interval_seconds": sample.get("interval_seconds"),
        "cpu_count": sample.get("cpu_count"),
        "sample_count": len(samples) if isinstance(sample.get("samples"), list) else None,
        "cpu_interval_count": len(cpu_rates),
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
    if _with_phases:
        from runner.capacity import epoch

        stopped = epoch(sample.get("stop_submitting_at") or sample.get("submission_stopped_at"))
        load_started = epoch(sample.get("load_started_at"))
        ended = epoch(sample.get("observation_ended_at"))
        released = epoch(sample.get("server_drained_at"))
        finished = epoch(sample.get("finished_at"))
        boundary = released if released is not None else ended
        basis = "server_drained_at" if released is not None else "client_observation_ended_at"
        summary.update(stop_submitting_at=stopped, load_started_at=load_started, observation_ended_at=ended,
                       server_drained_at=released, phase_boundary_at=boundary, phase_boundary_basis=basis,
                       checkpoints=sample.get("checkpoints") or {}, phases={})
        buckets = {"load": [], "drain": [], "extra": []}
        valid_boundaries = stopped is not None and load_started is not None and load_started <= stopped
        complete_boundaries = valid_boundaries and boundary is not None and stopped <= boundary
        unknown_timestamps = 0
        for item in samples:
            at = epoch(_host_field(item, "timestamp"))
            if at is None:
                unknown_timestamps += 1
            elif valid_boundaries and load_started <= at <= stopped:
                buckets["load"].append(item)
            elif complete_boundaries and stopped < at <= boundary:
                buckets["drain"].append(item)
            elif complete_boundaries and at > boundary:
                buckets["extra"].append(item)
        summary["samples_without_timestamp"] = unknown_timestamps
        summary["phases"] = {}
        for name, rows in buckets.items():
            known = valid_boundaries if name == "load" else complete_boundaries
            phase = summarize({"samples": rows}, _with_phases=False) if known else None
            if phase is not None:
                phase["status"] = "collected" if rows else "missing"
                phase["started_at"] = load_started if name == "load" else stopped if name == "drain" else boundary
                phase["finished_at"] = stopped if name == "load" else boundary if name == "drain" else finished
                phase["boundary_basis"] = "stop_submitting_at" if name == "load" else basis
            summary["phases"][name] = phase
        # 保留旧读取方的键名；三个新阶段互斥，不把排空重复算入额外观察。
        summary["phases"]["client_drain"] = summary["phases"]["drain"]
        summary["phases"]["server_observation"] = summary["phases"]["extra"]
        summary["server_observation_seconds"] = finished - stopped if finished is not None and stopped is not None and finished >= stopped else None
    return summary


def render(summary: dict | None) -> list[str]:
    """把资源摘要渲染为报告段落；没有采样时如实说明。"""
    if not summary:
        return [
            "- 负载窗口资源曲线：未采集（需要在服务器上并行运行 "
            "loadtest/stage_resource_sample.sh）",
        ]
    lines = [
        f"- 采集状态：{summary.get('status', 'legacy')}；停止提交后检查点："
        f"{summary.get('observation_status', 'missing')}，客户端排空与服务器观察分别记录。",
        f"- 采样文件：{summary.get('schema_version')}（run_id {summary.get('run_id')}）",
        f"- 服务器采样窗口：{summary.get('started_at')} 至 {summary.get('finished_at')}"
        f"（每 {summary.get('interval_seconds')} 秒采样，共 {summary.get('sample_count')} 次）",
        f"- 全窗口 CPU 使用率（{_show(summary.get('cpu_count'))} 核）：均值 {_show(summary.get('cpu_percent_avg'))}%，"
        f"峰值 {_show(summary.get('cpu_percent_max'))}%",
        f"- 1 分钟负载均值：均值 {_show(summary.get('load1_avg'))}，峰值 {_show(summary.get('load1_max'))}",
        f"- 可用内存最低值 {_show(summary.get('mem_available_mb_min'))} MB"
        f"（均值 {_show(summary.get('mem_available_mb_avg'))} MB），"
        f"swap 空闲最低值 {_show(summary.get('swap_free_mb_min'))} MB",
        f"- 队列深度峰值：排队 {_show(summary.get('queued_max'))}，运行 "
        f"{_show(summary.get('running_max'))}，draining {_show(summary.get('draining_max'))}",
        f"- 主库连接数峰值：{_show(summary.get('threads_connected_max'))}",
        f"- 窗口末容器：{_container_summary(summary)}",
    ]
    lines.extend(["", "| 资源阶段 | 样本数 | CPU 均值/峰值 % | 可用内存最低 MB | 排队峰值 | 连接峰值 |",
                  "| --- | --- | --- | --- | --- | --- |"])
    phases = summary.get("phases") or {}
    boundary = summary.get("phase_boundary_at", summary.get("observation_ended_at"))
    server_boundary = summary.get("phase_boundary_basis") == "server_drained_at"
    boundary_notes = []
    for key, legacy, label in (("load", "load", "load 负载"), ("drain", "client_drain", "drain 客户端排空"),
                               ("extra", "server_observation", "extra 额外服务器观察")):
        phase = phases.get(key, phases.get(legacy))
        if key == "drain":
            label = "drain 服务器确认排空" if server_boundary else "drain 客户端观察窗口"
        elif key == "extra":
            label = "extra 确认排空后观察" if server_boundary else "extra 客户端结束后服务器窗口（未证明排空）"
        boundary_missing = summary.get("stop_submitting_at") is None or (
            key != "load" and boundary is None) or (
            key == "load" and summary.get("load_started_at") is None)
        if boundary_missing or not phase or not phase.get("sample_count"):
            lines.append(f"| {label} | 未采集 | 未采集 | 未采集 | 未采集 | 未采集 |")
        else:
            lines.append(f"| {label} | {phase['sample_count']} | "
                         f"{_show(phase.get('cpu_percent_avg'))}/{_show(phase.get('cpu_percent_max'))} | "
                         f"{_show(phase.get('mem_available_mb_min'))} | {_show(phase.get('queued_max'))} | "
                         f"{_show(phase.get('threads_connected_max'))} |")
        phase_start = summary.get("load_started_at") if key == "load" else summary.get("stop_submitting_at") if key == "drain" else boundary
        phase_end = summary.get("stop_submitting_at") if key == "load" else boundary if key == "drain" else summary.get("finished_at")
        boundary_notes.append(f"- {key} 资源边界：{_time(phase_start)} 至 {_time(phase_end)}；依据："
                     f"{'stop_submitting_at' if key == 'load' else summary.get('phase_boundary_basis', 'client_observation_ended_at')}。")
    lines.extend([""] + boundary_notes)
    lines.extend(["", "load 从负载开始至停止提交（含停止边界）；drain/extra 按上述显式边界分段。"
                  "若依据为客户端观察结束，extra 仍可能包含服务器实际执行，不能解释为服务器已经排空。"
                  "各阶段的 CPU 使用率仅由阶段内相邻采样计算；缺少边界或有效采样时保留未采集。资源采样本身不证明 Job 执行已释放。",
                  f"无时间戳样本数：{_show(summary.get('samples_without_timestamp'))}。"] )
    return lines


def _time(value) -> str:
    if value is None:
        return "未采集"
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if isinstance(value, (int, float)) else str(value)


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

