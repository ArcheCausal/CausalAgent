"""把一次运行的聚合结果整理为“最终报告格式”的报告正文。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from runner import config, resource
from runner.capacity import enrich_summary

def _server_spec(summary: dict) -> str:
    """从服务器侧采集读取主机规格；没有采集时如实写明未采集。"""
    baseline = summary.get("server_baseline") or {}
    spec = baseline.get("host_spec")
    if spec:
        return str(spec)
    cpu = baseline.get("cpu_count")
    memory = baseline.get("memory_mb")
    if cpu or memory:
        return f"CPU 核数 {cpu or '未采集'}；内存 {memory or '未采集'}"
    return "未采集（缺少服务器只读采集）"


def _fmt(value, unit: str = "") -> str:
    """数值为空时返回“未采集”，否则保留一位小数。"""
    if value is None:
        return "未采集"
    if isinstance(value, float):
        return f"{value:.1f}{unit}"
    return f"{value}{unit}"


def _timestamp(value) -> str:
    if value is None or value == "" or value == 0:
        return "未采集"
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return str(value)


def _cell(value) -> str:
    return _value(value).replace("|", r"\|").replace("\n", "<br>")


def _rate(value) -> str:
    return "未采集" if value is None else f"{value:.2%}"


def _sse_states_section(sse: dict) -> list[str]:
    lines = ["## SSE 连接状态", "",
             "| 状态 | 当前连接数 | 峰值 | 累计进入次数 |",
             "| --- | --- | --- | --- |"]
    states = sse.get("states") or {}
    for state, data in states.items():
        lines.append(f"| {_cell(state)} | {_value(data.get('current'))} | "
                     f"{_value(data.get('peak'))} | {_value(data.get('entries'))} |")
    if not states:
        lines.append("| 未采集 | 未采集 | 未采集 | 未采集 |")
    lines.extend(["", "v2 SSE 峰值只计成功收到 HTTP 200 响应头的连接；等待额度与等待响应头分别统计。"
                  "订阅到终态延迟不包含首次 POST 之前的步骤，完整用户等待以 user_wait_ms 为准。", ""])
    return lines


def _capacity_section(summary: dict) -> list[str]:
    capacity = summary.get("capacity") or {}
    lines = ["## 容量口径 v2", "",
             "| 指标 | 分母 | 已观测数量 | 比率 / 覆盖率 |",
             "| --- | --- | --- | --- |"]
    fields = (
        ("admission", "新 Job 接纳", ("accepted", "replays", "conflicts", "unknown"), ("rate",)),
        ("db_success", "主库成功", ("checked", "succeeded"), ("rate", "coverage")),
        ("completion_120s", "120 秒内完成", ("completed", "unknown"), ("rate",)),
        ("completion_300s", "300 秒内完成", ("completed", "unknown"), ("rate",)),
        ("client_terminal", "客户端终态", ("realtime", "after_db"), ("coverage",)),
        ("sse_integrity", "SSE 全量事件集合核对", ("checked", "missing_events"), ("coverage",)),
    )
    for key, label, counts, rates in fields:
        data = capacity.get(key) or {}
        count_text = "；".join(f"{name}={_value(data.get(name))}" for name in counts)
        rate_text = "；".join(f"{name}={_rate(data.get(name))}" for name in rates)
        lines.append(f"| {label} | {_value(data.get('denominator'))} | {count_text} | {rate_text} |")
    admission = capacity.get("admission") or {}
    lines.append(f"| 请求回执率 | {_value(admission.get('denominator'))} | 与新 Job 接纳分别统计 | "
                 f"{_rate(admission.get('request_receipt_rate'))} |")
    lines.extend(["", "新 Job 接纳按有效逻辑请求口径统计，accepted 为唯一新 Job，幂等重放不增加接纳数；请求回执率单列。主库成功率与核对覆盖率分别报告；"
                  "120/300 秒完成率保留未知样本，未核对事件集合时 missing_events 为未采集。", "",
                  "### 延迟分组", "",
                  "| group | 样本数 | 接纳数 | 仅完成样本 p95（字段单位 ms） |",
                  "| --- | --- | --- | --- |"])
    groups = capacity.get("latency_groups") or {}
    for group, data in groups.items():
        values = "；".join(f"{key}={_fmt(value, ' ms')}" for key, value in data.items()
                           if key.startswith("completed_sample_p95_") and key.endswith("_ms"))
        group_label = group if isinstance(summary.get("requests"), list) else "未采集（旧产物未记录 group）"
        lines.append(f"| {_cell(group_label)} | {_value(data.get('sample_count'))} | "
                     f"{_value(data.get('accepted'))} | {values or '未采集'} |")
    if not groups:
        lines.append("| 未采集 | 未采集 | 未采集 | 未采集 |")
    lines.extend(["", "仅完成样本 p95 排除观察结束时仍未完成的请求，不能代替全量用户等待分布。", ""])
    requests = summary.get("requests")
    if isinstance(requests, list):
        bounds = [row["wait_lower_bound_ms"] for row in summary.get("job", {}).get("samples", [])
                  if row.get("terminal_received_at") is None and row.get("wait_lower_bound_ms") is not None]
        lines.append(f"- 未收到终态且有等待下界的唯一 Job：{len(bounds)}；等待下界范围："
                     f"{_fmt(min(bounds) if bounds else None, ' ms')} ~ "
                     f"{_fmt(max(bounds) if bounds else None, ' ms')}")
    cdfm = capacity.get("cdfm") or {}
    lines.extend(["", "### CDFM", "", f"- 状态：{_value(cdfm.get('status'))}"])
    for key, value in cdfm.items():
        if key != "status":
            lines.append(f"- {key}：{_value(value)}")
    lines.append("CDFM 场景名称或 Job 成功均不能证明 CDFM 已执行；必须有归属到本轮 Job 的 invocation、"
                 "attempt/retry 和最终结果证据。调用统计不等于模型准确率、进程终止或完整验收通过。")
    if summary.get("db_checked_at") is None:
        lines.append("主库核对待完成：当前摘要只包含已采集的客户端证据，主库成功率与服务器计时尚不能定论。")
    return lines + [""]


def _timing_section(summary: dict) -> list[str]:
    lines = ["## 计时与分阶段吞吐", "",
             f"- 停止提交 stop_submitting_at：{_timestamp(summary.get('stop_submitting_at'))}",
             f"- 观察结束 observation_ended_at：{_timestamp(summary.get('observation_ended_at'))}",
             f"- 主库核对 db_checked_at：{_timestamp(summary.get('db_checked_at'))}",
             "", "| 阶段 | HTTP 请求数 | HTTP RPS | 客户端完成数 | 客户端完成吞吐（个/s） | 主库完成数 | 主库完成吞吐（个/s） | 仅完成样本排队 p95（ms） |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    phases = summary.get("phases") or (summary.get("capacity") or {}).get("phases") or {}
    for name in ("load", "drain"):
        data = phases.get(name) or {}
        lines.append(f"| {name} | {_value(data.get('http_requests'))} | {_fmt(data.get('http_rps'))} | "
                     f"{_value(data.get('client_completions'))} | {_fmt(data.get('client_completion_throughput'))} | "
                     f"{_value(data.get('server_completions'))} | {_fmt(data.get('server_completion_throughput'))} | "
                     f"{_fmt(data.get('completed_sample_queue_p95_ms'))} |")
    for name in ("load", "drain"):
        data = phases.get(name) or {}
        if data.get("queue_phase_basis"):
            lines.extend(["", f"{name} 排队分位按 {data['queue_phase_basis']} 所在阶段分组；已完成样本数 "
                          f"{_value(data.get('queue_samples'))}。"])
    drain = summary.get("drain") or {}
    checkpoint = drain.get("checkpoint") or {}
    lines.extend(["",
                  f"- 排空检查点：{_fmt(drain.get('checkpoint_ms'), ' ms')}；"
                  f"检查点已排空：{_value(checkpoint.get('drained'))}；检查点状态：{_value(checkpoint.get('state'))}",
                  f"- 主库确认最终已排空 finally_drained：{_value(drain.get('finally_drained'))}；"
                  f"主库确认排空耗时 drain_ms：{_fmt(drain.get('drain_ms'), ' ms')}；"
                  f"客户端活动查询状态 final_state：{_value(drain.get('final_state'))}；"
                  f"实际观察 observed_ms：{_fmt(drain.get('observed_ms'), ' ms')}",
                  f"- 客户端观察集合已清空 client_observers_cleared：{_value(drain.get('client_observers_cleared'))}；"
                  f"客户端排空耗时 client_drain_ms：{_fmt(drain.get('client_drain_ms'), ' ms')}",
                  f"- 业务活动已清空 business_active_cleared：{_value(drain.get('business_active_cleared'))}；"
                  f"执行释放 execution_released：{_value(drain.get('execution_released'))}",
                  f"- 主库核对当时已排空 server_drained_at_check：{_value(drain.get('server_drained_at_check'))}",
                  "客户端没有待观察 Job 只表示客户端观察结束；业务活动清空与执行释放分别以同轮主库覆盖和释放证据判定，unknown 表示尚不能判断。"])
    lines.extend(["", "user_wait_ms 从创建请求发出计至客户端收到终态；history_check_ms 单独统计历史核对耗时。"
                  "未收到终态时只给 wait_lower_bound_ms。主库核对发生在观察结束之后时，不能回填成实时收到终态。",
                  "普通 HTTP 默认 30 秒，SSE 空闲读取默认 45 秒，单 Job 总观察默认 1800 秒；"
                  "停止提交后 300 秒是检查点，最终排空观察上限默认 1800 秒。实际覆盖值以本轮配置为准。", ""])
    if summary.get("schema_version") != "loadtest.run.v2":
        lines.extend(["旧产物兼容：未保存的逻辑请求时间点、完整事件 ID 集合和分阶段数据均标为未采集；"
                      "旧 SSE 峰值可能包含等待建连，不能解释为成功收到 HTTP 200 响应头的连接峰值。", ""])
    return lines


def _value(value) -> str:
    """无采集来源的值统一显示为未采集。"""
    return "未采集" if value is None else str(value)


def _field(value) -> str:
    """采集字段缺失或为空时显示为未采集，避免报告出现 None 或空字符串。"""
    if value is None or value == "":
        return "未采集"
    return str(value)


def _json_or_absent(value) -> str:
    if value is None:
        return "未采集（缺少服务器只读采集入口）"
    return json.dumps(value, ensure_ascii=False)


def _listed(value) -> str:
    """把采集脚本用分号连接的列表渲染成可读文本；空值表示没有命中项。"""
    if value is None:
        return "未采集"
    items = [item.strip() for item in str(value).split(";") if item.strip()]
    return "、".join(items) if items else "无"


def _parse_job_queue(raw) -> dict:
    """把采集脚本的 Job 分布还原为字段字典。

    采集脚本有两种输出形状：计数写成一条字符串，或直接给出字段。两种都要能读，
    否则旧快照会渲染成空值。
    """
    if not raw:
        return {}
    if isinstance(raw, dict) and raw.get("status") in ("missing", "error"):
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
        f"- 采集时刻：{_field(payload.get('collected_at'))}"
        f"（{_field(payload.get('phase'))}；{_field(payload.get('schema_version'))}）",
        f"- 采集方式：{_field(payload.get('source'))}",
        f"- 主机：{_host_line(payload)}",
        f"- 运行时长：{_field(payload.get('uptime'))}",
        f"- 负载均值 / CPU 核数：{_field(payload.get('load_average'))} / {_field(payload.get('cpu_count'))}",
        f"- 内存（MB）：{_field(payload.get('memory_mb'))}",
        f"- swap（MB）：{_field(payload.get('swap_mb'))}",
        f"- 磁盘：{_field(payload.get('disk'))}",
        f"- inode：{_field(payload.get('disk_inodes'))}",
        f"- 容器：运行 {_field(payload.get('containers_up'))} 个；"
        f"OOM 容器：{_listed(payload.get('oom_killed'))}；"
        f"重启计数：{_listed(payload.get('restart_counts'))}",
    ]
    images = payload.get("images") or {}
    if images:
        lines.append("- 关键镜像：")
        for name, value in images.items():
            lines.append(f"  - {name}：{value}")
    exited = payload.get("containers_exited") or []
    if isinstance(exited, str):
        items = [item.strip() for item in exited.split(";") if item.strip()]
    else:
        items = list(exited)
    if items:
        lines.append(f"- 已退出容器：{'；'.join(items)}")
    mysql = payload.get("mysql") or {}
    if mysql.get("status") in ("missing", "error"):
        lines.append(f"- MySQL：{_field(mysql.get('status'))}（{_field(mysql.get('reason'))}）")
        mysql = {}
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


def _host_line(payload: dict) -> str:
    """渲染主机一行：有 host_spec 时用它，否则用采集脚本实际给出的主机名与规格字段。"""
    hostname = _field(payload.get("hostname"))
    spec = payload.get("host_spec")
    if spec:
        return f"{hostname}，{spec}"
    cpu = payload.get("cpu_count")
    memory = payload.get("memory_mb")
    if cpu or memory:
        return f"{hostname}，CPU 核数 {cpu or '未采集'}；内存（MB）{memory or '未采集'}"
    return f"{hostname}，规格未采集"


def _wait_lower_bound(job: dict) -> str:
    """渲染观察期限外未到终态 Job 的等待时间下界。

    本档没有这类 Job 时写明“无”，不要写成“未采集”：前者表示该项不适用，
    后者表示应当采集却缺失，两者含义不能混用。
    """
    minimum = job.get("queue_timeout_wait_lower_bound_min_seconds")
    maximum = job.get("queue_timeout_wait_lower_bound_max_seconds")
    if minimum is None and maximum is None:
        if job.get("observation_timeouts") == 0:
            return "无（本档没有观测期限外未到终态的 Job）"
        return "未采集"
    return f"{_fmt(minimum, ' s')} ~ {_fmt(maximum, ' s')}"


def _mcp_section(logcheck: dict | None, summary: dict) -> list[str]:
    """渲染模型与 MCP 错误一节，缺少日志核查时如实说明。"""
    if (not logcheck or not summary.get("run_id") or logcheck.get("scope") != "run"
            or logcheck.get("run_id") != summary.get("run_id")):
        return [
            "本轮日志侧指标：未采集。缺少相同 run_id 且 scope=run 的日志核查，无法给出模型与 MCP 错误分类或成功数量。",
            "旧日志产物仍可读取，但缺少本轮归属时不参与本轮统计；同 stage 不能证明属于同一轮。",
            "补齐入口：runner logcheck --stage " + str(summary.get("stage", "")) + "。",
        ]
    date_dir = summary.get("date_dir") or config.date_key(summary.get("started_at") or 0)
    lines = [
        f"- 日志证据状态：{_value(logcheck.get('status'))}；scope：{_value(logcheck.get('scope'))}",
        f"- 日志 run_id：{_value(logcheck.get('run_id'))}；逐 Job/服务覆盖：{_value(logcheck.get('coverage'))}",
        f"- 日志核查文件：local/evidence/{date_dir}/logcheck-"
        + str(logcheck.get("stage") or summary.get("stage", ""))
        + ".json",
        "",
        "| 能力 | 收到 | 完成 | 失败 | 慢调用 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, stats in (logcheck.get("capability") or {}).items():
        lines.append(
            f"| {name} | {_value(stats.get('received'))} | {_value(stats.get('finished'))} | "
            f"{_value(stats.get('failed'))} | {_value(stats.get('slow'))} |"
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
    if (logcheck and summary.get("run_id") and logcheck.get("run_id") == summary.get("run_id")
            and logcheck.get("scope") == "run" and isinstance(logcheck.get("cdfm"), dict)):
        summary = {**summary, "cdfm_log_evidence": {**logcheck["cdfm"],
                   "run_id": logcheck["run_id"], "scope": "run"}}
    summary = enrich_summary(summary)
    http = summary.get("http", {})
    job = summary.get("job", {})
    sse = summary.get("sse", {})
    baseline = summary.get("server_baseline")
    after = summary.get("server_after")
    client = _client_from_notes(summary)
    # 专项流程（recovery）逐项检查串联执行，不适用分级负载模型的并发与耗时分位口径；
    # 这些字段应当写成“不适用”，与“应当采集却缺失”的未采集区分开。
    special = bool(summary.get("checks"))

    def load_value(value) -> str:
        """渲染只在分级负载模型里有含义的字段。"""
        if special and value is None:
            return "不适用（专项流程不运行分级负载模型）"
        return _value(value)

    def load_fmt(value, unit: str = "") -> str:
        """渲染只在分级负载模型里有含义的耗时字段。"""
        if special and value is None:
            return "不适用"
        return _fmt(value, unit)

    started = _timestamp(summary.get("started_at"))
    lines = [
        f"# 压测运行报告 {summary['run_id']}",
        "",
        "## 运行信息",
        "",
        f"- run_id：{summary['run_id']}",
        f"- 摘要版本：{_value(summary.get('schema_version'))}",
        f"- 场景参数：{_value(summary.get('scenario'))}",
        f"- 测试时间：{started}",
        f"- 服务版本或镜像 digest：{_image_note(summary)}",
        f"- 服务器规格：{_server_spec(summary)}",
        f"- 压测客户端规格和网络位置：{json.dumps(client, ensure_ascii=False)}",
        f"- 虚拟账号数：{summary['users']}，每账号真实会话数："
        f"{load_value(summary.get('peak_sessions_per_user'))}",
        f"- 全观察窗口 HTTP RPS：{_fmt(http.get('rps'))}",
        f"- 实际活动 Job 峰值：{load_value(summary.get('peak_active_jobs'))}",
        f"- 单账号活动 Job 峰值：{load_value(summary.get('peak_active_jobs_per_account'))}",
        f"- 客户端 SSE 峰值：{load_value(summary.get('peak_sse'))}",
        "",
        "## HTTP",
        "",
        f"- 请求数：{http.get('requests', '未采集')}",
        f"- p50 / p95 / p99：{_fmt(http.get('p50_ms'), ' ms')} / "
        f"{_fmt(http.get('p95_ms'), ' ms')} / {_fmt(http.get('p99_ms'), ' ms')}",
        f"- 状态码分布：{json.dumps(http.get('status_counts', {}), ensure_ascii=False)}",
        f"- 4xx / 5xx / 超时或传输错误：{_value(http.get('client_errors_4xx'))} / "
        f"{_value(http.get('server_errors_5xx'))} / {_value(http.get('timeouts_or_transport_errors'))}",
        "",
        "## Job",
        "",
        f"- 创建尝试：{_value(job.get('create_attempts'))}",
        f"- 成功创建唯一新 Job：{_value(job.get('created'))}",
        f"- 幂等重放（不占用额度）：{_value(job.get('idempotent_replays'))}",
        f"- 超额跳过（每账号额度用尽）：{_value(job.get('submissions_over_budget'))}",
        f"- 会话忙跳过：{_value(job.get('submissions_session_busy'))}",
        f"- 每账号成功创建上限使用最多：{_value(job.get('budget_used_max'))}",
        f"- 终态分布：{json.dumps(job.get('terminal_status_counts', {}), ensure_ascii=False)}",
        f"- 观察期限内未到终态的 Job：{_value(job.get('observation_timeouts'))}",
        f"- 排队观测到达 {config.QUEUE_OBSERVE_TIMEOUT_SECONDS:.0f} 秒仍未进入 running 的 Job："
        f"{_value(job.get('queue_observe_timeouts'))}",
        "- 等待时间下界（未完成 Job）：" + _wait_lower_bound(job),
        f"- 仅完成样本排队 p50 / 仅完成样本 p95：{load_fmt(job.get('queue_p50_seconds'), ' s')} / "
        f"{load_fmt(job.get('queue_p95_seconds'), ' s')}",
        f"- 仅完成样本执行 p50 / 仅完成样本 p95：{load_fmt(job.get('execution_p50_seconds'), ' s')} / "
        f"{load_fmt(job.get('execution_p95_seconds'), ' s')}",
        f"- 仅完成样本总耗时 p50 / 仅完成样本 p95：{load_fmt(job.get('total_p50_seconds'), ' s')} / "
        f"{load_fmt(job.get('total_p95_seconds'), ' s')}",
        f"- 历史记录一致：{_value(job.get('history_consistent'))}/{_value(job.get('history_checked'))}",
        "",
        "## SSE",
        "",
        f"- 订阅数：{_value(sse.get('subscriptions'))}",
        f"- 建连尝试数：{_value(sse.get('connection_attempts'))}",
        f"- SSE 错误次数（恢复后仍保留）：{_value(sse.get('error_events_count'))}",
        f"- SSE 错误类型与次数：{_value(sse.get('error_events'))}",
        f"- 出现错误的订阅数 subscriptions_with_errors：{_value(sse.get('subscriptions_with_errors'))}；"
        f"订阅错误率 subscription_error_rate：{_rate(sse.get('subscription_error_rate'))}；"
        f"分母为全部 {_value(sse.get('subscriptions'))} 次订阅，恢复后仍保留先前错误",
        f"- 首事件 p50 / p95：{_fmt(sse.get('first_event_p50_ms'), ' ms')} / "
        f"{_fmt(sse.get('first_event_p95_ms'), ' ms')}",
        f"- 终态延迟 p50 / p95：{_fmt(sse.get('terminal_latency_p50_ms'), ' ms')} / "
        f"{_fmt(sse.get('terminal_latency_p95_ms'), ' ms')}",
        f"- 断线 / 重连：{_value(sse.get('disconnects'))} / {_value(sse.get('reconnects'))}",
        f"- 重复事件 / 乱序事件：{_value(sse.get('duplicate_events'))} / "
        f"{_value(sse.get('out_of_order_events'))}",
        f"- 与全量事件集合比对得到的漏事件："
        f"{_value((summary.get('capacity', {}).get('sse_integrity') or {}).get('missing_events'))}",
        f"- 事件 ID 缺口（信息项，非漏事件证据）："
        f"{_value(sse.get('id_gap_events_informational'))}",
        "",
        "## 负载窗口资源曲线",
        "",
    ]
    # 容量结论先给分母、覆盖率和阶段，再展示资源与专项证据。
    resource_heading = lines[-3:]
    lines[-3:] = _sse_states_section(sse) + _timing_section(summary) + _capacity_section(summary) + resource_heading
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
            f"| {endpoint} | {_value(stats.get('count'))} | {_fmt(stats.get('p50_ms'), ' ms')} | "
            f"{_fmt(stats.get('p95_ms'), ' ms')} | {_fmt(stats.get('p99_ms'), ' ms')} | "
            f"{json.dumps(stats.get('status_counts', {}), ensure_ascii=False)} | "
            f"{_value(stats.get('errors'))} |"
        )
    lines.extend(["", "## 模型与 MCP 错误", ""])
    lines.extend(_mcp_section(logcheck, summary))
    checks = summary.get("checks")
    if checks:
        lines.extend(["", "## 专项检查", "",
                      f"- 专项整体状态：{_value(summary.get('special_status'))}",
                      f"- 故意冲突与非预期冲突：{_value(summary.get('special_counts'))}", ""])
        if summary.get("schema_version") != "loadtest.run.v2":
            lines.extend(["旧专项记录没有按 v2 重新执行；下表保留历史结果，不构成当前专项验收通过。", ""])
        lines.extend(["| 检查项 | 结果 | 说明 |", "| --- | --- | --- |"])
        for item in checks:
            passed = item.get("passed")
            result = item.get("outcome") or ("未触发" if passed is None else ("通过" if passed else "未通过"))
            lines.append(f"| {_cell(item['check'])} | {_cell(result)} | {_cell(item['detail'])} |")
    if summary.get("stage") == "recovery" or checks:
        lines.extend(["", "### 专项证据缺口", ""])
        gaps = summary.get("special_evidence_gaps")
        if gaps is None:
            gaps = ["取消执行释放：未取得主库 execution_state / execution_released_at 与 worker 槽位证据。",
                    "算法进程终止：未取得 invocation、目标 PID 消失及重复取消证据。",
                    "前端阶段：未取得真实浏览器刷新、取消后迟到事件和 waiting_input 展示证据。"]
        lines.extend(f"- {gap}" for gap in gaps)
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

