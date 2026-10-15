"""把一次运行的聚合结果整理为“最终报告格式”的报告正文。"""

from __future__ import annotations

import json
from datetime import datetime

from runner import resource


def _fmt(value, unit: str = "") -> str:
    """数值为空时返回“未采集”，否则保留一位小数。"""
    if value is None:
        return "未采集"
    if isinstance(value, float):
        return f"{value:.1f}{unit}"
    return f"{value}{unit}"


def _value(value) -> str:
    """无采集来源的值统一显示为未采集。"""
    return "未采集" if value is None else str(value)


def _json_or_absent(value) -> str:
    if value is None:
        return "未采集（缺少服务器只读采集入口）"
    return json.dumps(value, ensure_ascii=False)


def _parse_job_queue(raw) -> dict:
    """把采集脚本的 Job 分布还原为字段字典。

    采集脚本有两种输出形状：计数写成一条字符串，或直接给出字段。两种都要能读，
    否则旧快照会渲染成空值。
    """
    if not raw:
        return {}
    if isinstance(raw, dict) and "counts" not in raw:
        return raw
    if not isinstance(raw, dict):
        return {}
    result: dict = {"table": raw.get("table", "")}
    for part in str(raw.get("counts", "")).split():
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _server_section(
    baseline: dict | None, after: dict | None, label_prefix: str = ""
) -> list[str]:
    """渲染服务器侧一节；缺采集时如实说明，有采集时按字段展开。

    label_prefix 用来区分同一份采集在负载前后的含义：负载前是基线，负载后是收尾。
    """
    if not baseline:
        if after and isinstance(after, dict) and len(after) > 1:
            lines = ["- 基线快照：未采集", ""]
            lines.extend(_server_fields(after, "停止后"))
            return lines
        return [
            "- 基线快照：未采集（缺少服务器只读采集入口）",
            f"- 停止后恢复：{_json_or_absent(after)}",
            "",
        ]
    lines = _server_fields(baseline, label_prefix)
    if after:
        drain = after.get("drain_seconds") if isinstance(after, dict) else None
        if drain is not None:
            lines.append(f"- 停止提交后活动 Job 清空用时：{drain} 秒")
        if not isinstance(after, dict) or len(after) > 1:
            lines.extend(_server_fields(after, "停止后"))
    lines.append("")
    return lines


def _server_fields(payload: dict, label_prefix: str) -> list[str]:
    """把一份服务器采集渲染成条目；label_prefix 说明它是负载前还是停止后。"""
    lines = [
        f"- 采集时刻：{payload.get('collected_at')}（{payload.get('collected_at_note')}）",
        f"- 采集方式：{payload.get('source')}",
        f"- 主机：{payload.get('hostname')}，{payload.get('host_spec')}",
        f"- 运行时长：{payload.get('uptime')}",
        f"- 负载均值 / CPU 核数：{payload.get('load_average')} / {payload.get('cpu_count')}",
        f"- 内存（MB）：{payload.get('memory_mb')}",
        f"- swap（MB）：{payload.get('swap_mb')}",
        f"- 磁盘：{payload.get('disk')}",
        f"- inode：{payload.get('disk_inodes')}",
        f"- 容器：运行 {payload.get('containers_up')} 个；OOM：{payload.get('oom_killed')}；"
        f"重启计数：{payload.get('restart_counts')}",
    ]
    images = payload.get("images") or {}
    if images:
        lines.append("- 关键镜像：")
        for name, value in images.items():
            lines.append(f"  - {name}：{value}")
    exited = payload.get("containers_exited") or []
    if exited:
        lines.append(f"- 已退出容器：{'；'.join(exited)}")
    mysql = payload.get("mysql") or {}
    if mysql:
        lines.append(
            "- MySQL：连接数 "
            f"{mysql.get('threads_connected')}，运行查询 {mysql.get('threads_running')}，"
            f"累计创建 {mysql.get('threads_created')}，失败连接 {mysql.get('aborted_connects')}，"
            f"实例 uptime {mysql.get('uptime_seconds')} 秒"
        )
        lines.append(
            f"- 副本只读：{mysql.get('replica_read_only')}；"
            f"副本可读性：{mysql.get('replica_read_query')}"
        )
        lines.append(
            f"- 复制状态：IO 线程 {mysql.get('replication_io_state')}，"
            f"最近错误 {mysql.get('replication_last_error')}"
        )
    queue = _parse_job_queue(payload.get("job_queue"))
    if queue:
        label = f"{label_prefix} Job 分布" if label_prefix else "Job 分布"
        lines.append(
            f"- {label}（{queue.get('table')}）：总数 {queue.get('total')}，"
            f"排队 {queue.get('queued')}，运行 {queue.get('running')}，"
            f"成功 {queue.get('succeeded')}，失败 {queue.get('failed')}，"
            f"取消 {queue.get('canceled')}，等待输入 {queue.get('waiting_input')}"
        )
    for name, value in (payload.get("env_facts") or {}).items():
        lines.append(f"- {name} 环境：{value}")
    probe = payload.get("network_probe") or {}
    if probe:
        lines.append("- 出网探测：")
        for name, value in probe.items():
            lines.append(f"  - {name}：{value}")
    return lines


def _mcp_section(logcheck: dict | None, summary: dict) -> list[str]:
    """渲染模型与 MCP 错误一节，缺少日志核查时如实说明。"""
    if not logcheck:
        return [
            "日志侧核查未执行，本档无法给出模型与 MCP 错误分类。",
            "补齐入口：runner logcheck --stage " + str(summary.get("stage", "")) + "。",
        ]
    lines = [
        "- 日志核查文件：evidence/logcheck-"
        + str(logcheck.get("stage") or summary.get("stage", ""))
        + ".json",
        "",
        "| 能力 | 收到 | 完成 | 失败 | 慢调用 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, stats in (logcheck.get("capability") or {}).items():
        lines.append(
            f"| {name} | {stats.get('received', 0)} | {stats.get('finished', 0)} | "
            f"{stats.get('failed', 0)} | {stats.get('slow', 0)} |"
        )
    lines.append("")
    lines.append("- 各服务各级别条数：" + json.dumps(logcheck.get("counts") or {}, ensure_ascii=False))
    failures = logcheck.get("web_failures") or []
    if failures:
        lines.extend(["", "web 请求失败："])
        for item in failures:
            lines.append(
                f"- {item.get('timestamp')} {item.get('method')} "
                f"{item.get('endpoint')} 状态码 {item.get('status_code')} "
                f"原因 {item.get('reason_code')} 异常 {item.get('exception_type')}"
            )
    degraded = logcheck.get("worker_degraded") or []
    if degraded:
        lines.extend(["", "worker 降级与告警："])
        for item in degraded:
            lines.append(
                f"- {item.get('timestamp')} {item.get('event_code')} "
                f"{json.dumps(item.get('details'), ensure_ascii=False)}"
            )
    return lines


def render_stage_report(summary: dict, logcheck: dict | None = None) -> str:
    """渲染一档负载的完整报告正文。

    字段缺失时按“未采集”渲染，兼容早期运行产生的摘要。传入该档的日志核查
    结果时，报告会补上模型与 MCP 错误一节；没有日志核查时按未执行说明。
    """
    http = summary.get("http", {})
    job = summary.get("job", {})
    sse = summary.get("sse", {})
    baseline = summary.get("server_baseline")
    after = summary.get("server_after")
    client = _client_from_notes(summary)
    started = datetime.fromtimestamp(summary["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 压测运行报告 {summary['run_id']}",
        "",
        "## 运行信息",
        "",
        f"- run_id：{summary['run_id']}",
        f"- 测试时间：{started}",
        f"- 服务版本或镜像 digest：{_image_note(summary)}",
        f"- 服务器规格：4 核 8 GB（腾讯云，按计划给定）",
        f"- 压测客户端规格和网络位置：{json.dumps(client, ensure_ascii=False)}",
        f"- 虚拟用户数：{summary['users']}",
        f"- 实际 RPS：{http['rps']}",
        f"- 实际活动 Job 峰值：{_value(summary.get('peak_active_jobs'))}",
        f"- 实际 SSE 峰值：{_value(summary.get('peak_sse'))}",
        "",
        "## HTTP",
        "",
        f"- 请求数：{http.get('requests', '未采集')}",
        f"- p50 / p95 / p99：{_fmt(http.get('p50_ms'), ' ms')} / "
        f"{_fmt(http.get('p95_ms'), ' ms')} / {_fmt(http.get('p99_ms'), ' ms')}",
        f"- 状态码分布：{json.dumps(http.get('status_counts', {}), ensure_ascii=False)}",
        f"- 4xx / 5xx / 超时或传输错误：{http.get('client_errors_4xx', 0)} / "
        f"{http.get('server_errors_5xx', 0)} / {http.get('timeouts_or_transport_errors', 0)}",
        "",
        "## Job",
        "",
        f"- 接受（创建）：{job.get('created', 0)}，幂等重放：{job.get('idempotent_replays', 0)}",
        f"- 终态分布：{json.dumps(job.get('terminal_status_counts', {}), ensure_ascii=False)}",
        f"- 排队 p50 / p95：{_fmt(job.get('queue_p50_seconds'), ' s')} / "
        f"{_fmt(job.get('queue_p95_seconds'), ' s')}",
        f"- 执行 p50 / p95：{_fmt(job.get('execution_p50_seconds'), ' s')} / "
        f"{_fmt(job.get('execution_p95_seconds'), ' s')}",
        f"- 总耗时 p50 / p95：{_fmt(job.get('total_p50_seconds'), ' s')} / "
        f"{_fmt(job.get('total_p95_seconds'), ' s')}",
        f"- 历史记录一致：{job.get('history_consistent', 0)}/{job.get('history_checked', 0)}",
        "",
        "## SSE",
        "",
        f"- 订阅数：{sse.get('subscriptions', 0)}",
        f"- 首事件 p50 / p95：{_fmt(sse.get('first_event_p50_ms'), ' ms')} / "
        f"{_fmt(sse.get('first_event_p95_ms'), ' ms')}",
        f"- 终态延迟 p50 / p95：{_fmt(sse.get('terminal_latency_p50_ms'), ' ms')} / "
        f"{_fmt(sse.get('terminal_latency_p95_ms'), ' ms')}",
        f"- 断线 / 重连：{sse.get('disconnects', 0)} / {sse.get('reconnects', 0)}",
        f"- 重复事件 / 乱序事件：{sse.get('duplicate_events', 0)} / "
        f"{sse.get('out_of_order_events', 0)}",
        f"- 与全量事件比对得到的漏事件：{sse.get('verified_missing_events', 0)}",
        f"- 事件 ID 缺口（信息项，非漏事件证据）："
        f"{sse.get('id_gap_events_informational', 0)}",
        "",
        "## 负载窗口资源曲线",
        "",
    ]
    lines.extend(resource.render(summary.get("resource_curve")))
    lines.extend(
        [
            "",
            "## 服务器侧",
            "",
        ]
    )
    lines.extend(_server_section(baseline, after, label_prefix="负载前"))
    lines.extend(
        [
            "## 逐接口延迟",
            "",
            "| 接口 | 请求数 | p50 | p95 | p99 | 状态码 | 错误 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for endpoint, stats in http.get("by_endpoint", {}).items():
        lines.append(
            f"| {endpoint} | {stats.get('count', 0)} | {_fmt(stats.get('p50_ms'), ' ms')} | "
            f"{_fmt(stats.get('p95_ms'), ' ms')} | {_fmt(stats.get('p99_ms'), ' ms')} | "
            f"{json.dumps(stats.get('status_counts', {}), ensure_ascii=False)} | "
            f"{stats.get('errors', 0)} |"
        )
    lines.extend(["", "## 模型与 MCP 错误", ""])
    lines.extend(_mcp_section(logcheck, summary))
    checks = summary.get("checks")
    if checks:
        lines.extend(["", "## 专项检查", "", "| 检查项 | 结果 | 说明 |", "| --- | --- | --- |"])
        for item in checks:
            passed = item.get("passed")
            result = item.get("outcome") or ("未触发" if passed is None else ("通过" if passed else "未通过"))
            lines.append(f"| {item['check']} | {result} | {item['detail']} |")
    lines.extend(["", "## 运行说明", ""])
    lines.extend(f"- {note}" for note in summary.get("notes", []))
    return "\n".join(lines) + "\n"


def _image_note(summary: dict) -> str:
    baseline = summary.get("server_baseline") or {}
    images = baseline.get("images") or {}
    if images.get("app"):
        return str(images["app"])
    for key in ("app_image", "image_digest", "version"):
        if key in baseline:
            return str(baseline[key])
    return "未采集"


def _client_from_notes(summary: dict) -> dict:
    for note in summary.get("notes", []):
        prefix = "客户端清单: "
        if note.startswith(prefix):
            try:
                return json.loads(note[len(prefix):])
            except json.JSONDecodeError:
                return {}
    return {}

