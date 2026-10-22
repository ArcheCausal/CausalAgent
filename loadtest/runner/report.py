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


def _throughput(value) -> str:
    return "未采集" if value is None else f"{value:.3f}"


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
    lines = ["## 容量口径 v2", "", "| 指标 | 分母 | 已观测数量 | 比率 / 覆盖率 |",
             "| --- | --- | --- | --- |"]
    orchestration = summary.get("orchestration") or {}
    cleanup = (capacity.get("evidence_boundary") or {}).get("cleanup") or {}
    lines[2:2] = [f"场景检查：{_value((orchestration.get('scenario') or {}).get('status'))}；"
                   f"采集完整性：{_value((orchestration.get('evidence') or {}).get('status'))}。", "",
                   f"严格清理核对：业务活动清空={_value(cleanup.get('business_active_cleared'))}；"
                   f"执行释放={_value(cleanup.get('execution_released'))}；"
                   f"释放证据未知 Job={_value(cleanup.get('release_unknown_jobs'))}。旧编排通过记录不能替代严格核对。", ""]
    fields = (
        ("admission", "新 Job 接纳", ("accepted", "replays", "conflicts", "unknown"), ("rate",)),
        ("db_success", "全部接纳 Job 主库成功", ("checked", "succeeded", "unidentified_accepted"), ("rate", "coverage")),
        ("client_terminal", "全部接纳 Job 客户端终态", ("realtime", "after_db"), ("coverage",)),
        ("continuous_client_terminal", "预声明持续观察客户端终态", ("realtime",), ("coverage",)),
        ("sse_integrity", "要求完整 SSE 的全量事件集合核对", ("checked", "missing_events", "unexpected_events"), ("coverage",)),
        ("truncated_sse", "主动截断 / 部分流的集合差（不计漏事件）", ("checked", "unobserved_events"), ("coverage",)),
    )
    for key, label, counts, rates in fields:
        data = capacity.get(key) or {}
        lines.append(f"| {label} | {_value(data.get('denominator'))} | "
                     + "；".join(f"{name}={_value(data.get(name))}" for name in counts) + " | "
                     + "；".join(f"{name}={_rate(data.get(name))}" for name in rates) + " |")
    admission = capacity.get("admission") or {}
    lines.append(f"| 请求回执率 | {_value(admission.get('denominator'))} | 包含幂等重放的有效请求回执 | "
                 f"{_rate(admission.get('request_receipt_rate'))} |")
    observation = capacity.get("observation") or {}
    lines.extend(["", f"- 全部接纳：{_value(observation.get('all_accepted'))}；预声明持续观察："
                  f"{_value(observation.get('continuous_denominator'))}；主动截断："
                  f"{_value(observation.get('truncated_denominator'))}；缺少预声明观察策略："
                  f"{_value(observation.get('unknown_policy'))}。",
                  f"- 要求完整 SSE：{_value(observation.get('full_stream_denominator'))}；部分流："
                  f"{_value(observation.get('partial_stream_denominator'))}；缺少流策略："
                  f"{_value(observation.get('unknown_stream_policy'))}。",
                  "新 Job 接纳按有效逻辑请求统计，幂等重放不增加唯一 Job 数。持续观察分母只读取请求预声明的 requires_terminal；"
                  "完整 SSE 分母只读取 requires_full_stream。未知策略仍保留在全部接纳分母中，不能按最终结果排除。",
                  "主库成功率的分母是全部接纳 Job，覆盖不足时观测成功数只是下界。实时终态与事后主库终态分别统计。",
                  "", "### deadline 终态率与成功率", "",
                  "| 观察口径 | 期限 | 分母 | 终态数 | 成功数 | 终态未知 | 成功未知 | 终态率 | 成功率 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
    for prefix, label in (("completion", "全部接纳 / 主库"), ("continuous_completion", "预声明持续观察 / 客户端")):
        for seconds in (120, 300):
            data = capacity.get(f"{prefix}_{seconds}s") or {}
            values = [_value(data.get(key)) for key in ("denominator", "terminal", "succeeded", "unknown", "success_unknown")]
            lines.append(f"| {label} | {seconds} s | " + " | ".join(values)
                         + f" | {_rate(data.get('terminal_rate'))} | {_rate(data.get('success_rate'))} |")
    lines.extend(["", "终态包含 succeeded、failed、canceled；成功只包含 succeeded。主库期限从创建计时，客户端期限从创建请求发出计时。"
                  "未取得该 Job 自己的观察时刻时不能由整轮采集结束时间证明它已经超期；null 不补成 0。",
                  "", "### 业务终态分布总表", "",
                  "| 业务类别 | 全部接纳 | 主库覆盖 | succeeded | failed | canceled | queued / running / waiting_input | 未知 | 终态率 | 成功率 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"] )
    for name in ("total", "ordinary", "cancellation", "unknown"):
        data = (capacity.get("business_results") or {}).get(name) or {}
        counts = data.get("status_counts") or {}
        lines.append(f"| {name} | {_value(data.get('denominator'))} | {_value(data.get('checked'))} / {_rate(data.get('coverage'))} | "
                     f"{_value(counts.get('succeeded'))} | {_value(counts.get('failed'))} | {_value(counts.get('canceled'))} | "
                     f"{_value(counts.get('queued'))} / {_value(counts.get('running'))} / {_value(counts.get('waiting_input'))} | "
                     f"{_value(data.get('unknown'))} | {_rate(data.get('terminal_rate'))} | {_rate(data.get('success_rate'))} |")
    lines.extend(["", "ordinary、cancellation、unknown 为互斥业务类别，分母不随终态变化；缺少声明的旧专项不按最终 canceled 状态反推取消分组。",
                  f"观察结束原因：{_value(observation.get('end_reasons'))}。",
                  "", "### 业务分类与延迟分组", "",
                  "| group | 主库终态样本数 | 接纳数 | 状态 / 观察组 | 仅完成样本 p95（ms） |",
                  "| --- | --- | --- | --- | --- |"])
    groups = capacity.get("latency_groups") or {}
    for group, data in groups.items():
        values = "；".join(f"{key}={_fmt(value, ' ms')}" for key, value in data.items() if key.startswith("completed_sample_p95_"))
        states = json.dumps({"db": data.get("db_status_counts"), "observation": data.get("observation_groups")}, ensure_ascii=False)
        lines.append(f"| {_cell(group)} | {_value(data.get('sample_count'))} | {_value(data.get('accepted'))} | {_cell(states)} | {values or '未采集'} |")
    if not groups:
        lines.append("| 未采集 | 未采集 | 未采集 | 未采集 | 未采集 |")
    lines.extend(["", "业务分类保留原声明，并独立标记恢复、等待输入、重试与取消；同一 Job 可以进入多个业务分组，不能将分组数量相加。"
                  "仅完成样本 p95 排除未完成请求，不能代替全量用户等待分布。", ""])
    multi = capacity.get("multi_session") or {}
    lines.extend(["### 多会话归属与重叠核查", "",
                  f"- 状态：{_value(multi.get('status'))}；归属与活动重叠核心状态：{_value(multi.get('core_status'))}；缺口：{_value(multi.get('reason'))}",
                  "", "| Job / 会话 | 归属核查 | activity_overlap | running 状态重叠 | execution_overlap | 执行交叠 ms | 依据 / 缺口 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"] )
    multi_notes = []
    for pair in multi.get("pairs") or []:
        lines.append(f"| {_cell(pair.get('job_ids'))} / {_cell(pair.get('session_ids'))} | {_value(pair.get('ownership_verified'))} | "
                     f"{_value(pair.get('activity_overlap'))} | {_value(pair.get('running_status_overlap'))} | "
                     f"{_value(pair.get('execution_overlap'))} | {_fmt(pair.get('execution_overlap_ms'))} | "
                     f"{_cell(pair.get('execution_overlap_source'))} / {_cell(pair.get('reasons'))} |")
        multi_notes.append(f"- 活动共同快照时刻：{_timestamp(pair.get('activity_overlap_at'))}；主库单次执行区间：{_cell(pair.get('execution_intervals'))}。")
    if not multi.get("pairs"):
        lines.append("| 未采集 | 未验证 | 未验证 | 未验证 | 未验证 | 未采集 | 缺少 multi_session_evidence |")
    lines.extend([""] + multi_notes)
    lines.extend(["", "两个 Job 接纳不证明活动重叠；running 状态重叠不证明执行区间重叠。执行区间只采用主库可靠的 first_started_at / finished_at，"
                  "且必须为单次 attempt、无 resume/recovery；区间相交也不等于 CPU 同时执行。缺少历史专项身份或时间证据时保持未验证。", ""])
    bounds = [row["wait_lower_bound_ms"] for row in capacity.get("samples", [])
              if row.get("terminal_received_at") is None and row.get("wait_lower_bound_ms") is not None]
    lines.append(f"- 未收到终态且有等待下界的唯一 Job：{len(bounds)}；等待下界范围："
                 f"{_fmt(min(bounds) if bounds else None, ' ms')} ~ {_fmt(max(bounds) if bounds else None, ' ms')}")
    cancel = capacity.get("cancellation") or {}
    lines.extend(["", "### 取消专项分组", "",
                  f"- 接纳 Job：{_value(cancel.get('denominator'))}；取消 API 尝试：{_value(cancel.get('api_attempts'))}；结果：{_value(cancel.get('api_outcomes'))}",
                  f"- 主库已核对：{_value(cancel.get('db_checked'))}；业务取消：{_value(cancel.get('business_canceled'))}；"
                  f"执行释放已核对：{_value(cancel.get('execution_release_checked'))}；已释放：{_value(cancel.get('execution_released'))}",
                  f"- 算法进程终止：{_value(cancel.get('process_termination'))}；真实浏览器证据：{_value(cancel.get('browser_evidence'))}",
                  "取消 API 响应、业务取消、执行释放、算法进程终止与前端阶段分别判定。", "", "### CDFM", ""])
    cdfm = capacity.get("cdfm") or {}
    lines.extend([f"- 状态：{_value(cdfm.get('status'))}",
                  f"- CDFM 调用成功率：{_rate(cdfm.get('final_success_rate'))}；逻辑调用成功：{_value(cdfm.get('succeeded'))} / {_value(cdfm.get('logical_calls'))}。",
                  f"- CDFM 证据覆盖率：{_rate(cdfm.get('job_coverage'))}；已完整核查 Job：{_value(cdfm.get('covered_jobs'))} / {_value(cdfm.get('job_denominator'))}。",
                  f"- CDFM 算法准确率：{_rate(cdfm.get('accuracy'))}（缺少真实基准答案及评价证据，保留未知）。"])
    for key, value in cdfm.items():
        if key not in {"status", "accuracy", "final_success_rate", "job_coverage", "records", "attempt_records", "outcomes", "source_reconciliation"}:
            lines.append(f"- {key}：{_value(value)}")
    lines.extend(["", "逻辑调用终态分布（仅已识别 job + invocation；身份不完整时保留已观察终态，不确认最终 attempt）：",
                  f"- {_value(cdfm.get('outcomes'))}", "",
                  "| Job | invocation | 观察结果 | 最终身份已验证 | 已观察终态 | 来源 |",
                  "| --- | --- | --- | --- | --- | --- |"])
    for row in cdfm.get("records") or []:
        lines.append(f"| {row['job_id']} | {row['invocation_id']} | {row['outcome']} | {row['final_identity_verified']} | {_value(row['observed_terminal_statuses'])} | {_value(row['sources'])} |")
    lines.extend(["", "| Job | invocation | worker attempt | MCP retry | 身份状态 | 状态证据 | 来源 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"])
    for row in cdfm.get("attempt_records") or []:
        lines.append(f"| {row['job_id']} | {row['invocation_id']} | {_value(row['worker_attempt'])} | {_value(row['mcp_retry'])} | {row['identity_status']} | {_value(row['source_statuses'])} | {_value(row['sources'])} |")
    lines.extend(["", "| 跨来源身份层级 | 核对状态 | intersection | events only | logs only |",
                  "| --- | --- | --- | --- | --- |"])
    reconciliation = cdfm.get("source_reconciliation") or {}
    for level in ("logical", "attempt"):
        row = reconciliation.get(level) or {}
        lines.append(f"| {level} | {_value(row.get('status'))} | {_value(row.get('intersection'))} | {_value(row.get('events_only'))} | {_value(row.get('logs_only'))} |")
    lines.append(f"- 共同完整采集 Job：{_value(reconciliation.get('both_collected_jobs'))}；单侧未采集时差异保持未知。身份差异不代表算法结果错误。")
    lines.append("CDFM 按 job_id / invocation_id / worker attempt / MCP retry 身份归并，重复日志与 received/finished 事件不增加调用数。"
                 "缺少任一层身份时完整尝试数和最终成功率保留未知；worker 重新执行不算 MCP retry。调用成功及证据覆盖均不代表算法准确率、进程终止或专项验收通过。")
    if summary.get("db_checked_at") is None and capacity.get("db_checked_at") is None:
        lines.append("主库核对待完成：当前摘要未保存主库核对时间。")
    lines.extend(["", "### 能力与证据边界", "",
                  "结构化摘要见 capacity.evidence_boundary。结构检查、证据覆盖与产品验收分别判断；客户端终态时间仅来自原客户端记录。",
                  "算法准确率、PID 终止、真实浏览器行为及未预声明门槛的性能验收保持未知。"])
    return lines + [""]


def _timing_section(summary: dict) -> list[str]:
    lines = ["## 计时与分阶段吞吐", "",
             f"- 停止提交 stop_submitting_at：{_timestamp(summary.get('stop_submitting_at'))}",
             f"- 观察结束 observation_ended_at：{_timestamp(summary.get('observation_ended_at'))}",
             f"- 主库核对 db_checked_at：{_timestamp((summary.get('capacity') or {}).get('db_checked_at') or summary.get('db_checked_at'))}",
             "", "| 阶段 | HTTP 请求数 | HTTP RPS | 客户端成功数 / 吞吐 | 客户端全部终态数 / 吞吐 | 主库成功数 / 吞吐 | 主库全部终态数 / 吞吐 | 主库覆盖率 | 仅完成样本排队 p95（ms） |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    phases = summary.get("phases") or (summary.get("capacity") or {}).get("phases") or {}
    phase_notes = []
    for name in ("load", "drain"):
        data = phases.get(name) or {}
        lines.append(f"| {name} | {_value(data.get('http_requests'))} | {_throughput(data.get('http_rps'))} | "
                     f"{_value(data.get('client_successes'))} / {_throughput(data.get('client_success_throughput'))} | "
                     f"{_value(data.get('client_completions'))} / {_throughput(data.get('client_completion_throughput'))} | "
                     f"{_value(data.get('server_successes'))} / {_throughput(data.get('server_success_throughput'))} | "
                     f"{_value(data.get('server_completions'))} / {_throughput(data.get('server_completion_throughput'))} | "
                     f"{_rate(data.get('server_coverage'))} | {_fmt(data.get('completed_sample_queue_p95_ms'), ' ms')} |")
        if data.get("queue_phase_basis"):
            phase_notes.extend(["", f"{name} 客户端窗口：{_timestamp(data.get('started_at'))} 至 {_timestamp(data.get('client_finished_at'))}；"
                          f"时长 {_fmt(data.get('client_duration_ms'), ' ms')}。",
                          f"{name} 服务器窗口：{_timestamp(data.get('started_at'))} 至 {_timestamp(data.get('server_finished_at'))}；"
                          f"时长 {_fmt(data.get('server_duration_ms'), ' ms')}；结束依据 {_value(data.get('server_end_source'))}。",
                          f"服务器确认释放时刻：{_timestamp(data.get('server_execution_released_at'))}；"
                          f"服务器实际采集结束：{_timestamp(data.get('server_observed_until'))}。"] )
            phase_notes.extend(["", f"{name} 排队分位按 {data['queue_phase_basis']} 所在阶段分组；已完成样本数 "
                          f"{_value(data.get('queue_samples'))}。主库终态缺少 finished_at 的数量："
                          f"{_value(data.get('server_finished_timestamp_unknown'))}。"])
    lines.extend(phase_notes)
    lines.extend(["", "所有吞吐单位为个/s，至少保留三位小数。全部终态包含失败和取消；成功吞吐只计 succeeded。"
                  "主库覆盖不足时仅报告已核对子集，不能解释为服务器全量吞吐。"])
    submission = (summary.get("capacity") or {}).get("submission") or {}
    lines.extend(["", "### 提交前等待与发送统计", "",
                  "| 请求组 | 意图数 / 有意图时间戳 | 实际发送 | 未发送 | 发送未知 | scheduler_lag_ms 样本数 / p50 / p95 | pre_submit_wait_ms 样本数 / p50 / p95 | 未发送原因 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"] )
    submission_rows = [("total", submission.get("summary") or {})] + list((submission.get("groups") or {}).items())
    submission_notes = []
    for name, data in submission_rows:
        lag = f"{_value(data.get('scheduler_lag_ms_samples'))} / {_fmt(data.get('scheduler_lag_ms_p50'))} / {_fmt(data.get('scheduler_lag_ms_p95'))}"
        wait = f"{_value(data.get('pre_submit_wait_ms_samples'))} / {_fmt(data.get('pre_submit_wait_ms_p50'))} / {_fmt(data.get('pre_submit_wait_ms_p95'))}"
        lines.append(f"| {_cell(name)} | {_value(data.get('intents'))} / {_value(data.get('intent_timestamp_known'))} | "
                     f"{_value(data.get('actual_sent'))} | {_value(data.get('not_sent'))} | {_value(data.get('send_unknown'))} | "
                     f"{lag} | {wait} | {_cell(data.get('not_sent_reasons'))} |")
        submission_notes.append(f"- {name} 尚未发送的等待下界：样本数 {_value(data.get('wait_lower_bound_ms_samples'))}，"
                     f"p50 / p95：{_fmt(data.get('wait_lower_bound_ms_p50'), ' ms')} / {_fmt(data.get('wait_lower_bound_ms_p95'), ' ms')}；"
                     f"not_sent 却带发送时间戳的原始记录：{_value(data.get('not_sent_with_send_timestamp'))}。")
    lines.extend([""] + submission_notes)
    lines.extend(["", "意图时间采用到达率 scheduled_at 或普通请求 intent_created_at；实际发送采用首次 created_request_at。"
                  "pre_submit_wait_ms 包含会话和 Job 配额等待。scheduler_lag_ms 保留采集值；未发送下界单列，旧记录缺时间戳或耗时字段时不补零。"] )
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
                  "客户端没有待观察 Job 只表示客户端观察结束；业务活动清空与执行释放分别以同轮主库覆盖和释放证据判定，unknown 表示尚不能判断。",
                  "空 execution_state 不能独立证明执行释放；曾执行任务须有 worker_confirmed 与释放时间。终态且 attempt_count=0、started_at 为空的任务单列为从未领取；lease_expired 不证明实际执行已停止。"])
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
    lines = [
        f"- 日志证据状态：{_value(logcheck.get('status'))}；scope：{_value(logcheck.get('scope'))}",
        f"- 日志 run_id：{_value(logcheck.get('run_id'))}；逐 Job/服务覆盖：{_value(logcheck.get('coverage'))}",
        "- 按 run_id 保存的日志核查：logcheck.json；能力表表示日志记录数，不表示逻辑调用次数。",
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
    job_failures = [event for job in (logcheck.get("job_lifecycle") or {}).get("jobs", [])
                    for event in job.get("events", []) if event.get("event_code") == "worker.job.failed"]
    lines.extend(["", "Job 失败诊断（仅受控字段）：", "",
        "| Job | 异常类型 | 失败阶段 | 类别 | 原因码 | 供应商状态码 | 执行累计耗时 ms | 清理后栈顶 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |"])
    for event in job_failures:
        details = event.get("details") or {}
        cells = [event.get("job_id"), event.get("exception_type"), details.get("failure_phase"),
                 details.get("error_category"), details.get("reason_code"), details.get("status_code"),
                 details.get("duration_ms"), event.get("stack_top")]
        lines.append("| " + " | ".join(str(_value(value)) for value in cells) + " |")
    if not job_failures:
        lines.append("未取得 worker.job.failed 记录；这不证明没有失败，须结合主库结果和日志覆盖。")
    request_evidence = logcheck.get("request_evidence") or {}
    lines.extend(["", f"读取请求关联诊断：{_value(request_evidence.get('status'))}。", "",
        "| 客户端 request ID | HTTP | 网关 | 应用 | 上游状态 | 上游响应秒 | 关联结果 |",
        "| --- | --- | --- | --- | --- | --- | --- |"])
    for record in request_evidence.get("records", []):
        client = record.get("client") or {}
        gateway = record.get("gateway") or []
        cells = [client.get("request_id") or client.get("client_request_id"), client.get("status"),
                 record.get("gateway_status"), record.get("web_status"),
                 [row.get("upstream_status") for row in gateway] if gateway else None,
                 [row.get("upstream_response_time") for row in gateway] if gateway else None, record.get("status")]
        lines.append("| " + " | ".join(str(_value(value)) for value in cells) + " |")
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
    logcheck = logcheck if logcheck is not None else summary.get("logcheck")
    summary = enrich_summary(summary, logcheck)
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
        f"- 全观察窗口 HTTP RPS：{_throughput(http.get('rps'))}",
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
    lines.extend(_http_identity_section(summary))
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




def _http_identity_section(summary: dict) -> list[str]:
    """HTTP 失败即使尚无 Job，也保留客户端与响应请求身份供同轮核查。"""
    failures = [row for row in (summary.get("http") or {}).get("samples", [])
                if isinstance(row, dict) and (row.get("error") or isinstance(row.get("status"), int) and row["status"] >= 500)]
    lines = ["", "## HTTP 失败请求身份", "",
             "| 状态 | run_id | request_id | client_request_id | response_request_id |",
             "| --- | --- | --- | --- | --- |"]
    for row in failures:
        lines.append("| " + " | ".join(_cell(row.get(key)) for key in ("status", "run_id", "request_id", "client_request_id", "response_request_id")) + " |")
    if not failures:
        samples = (summary.get("http") or {}).get("samples")
        lines.append("| 无已记录失败 | 未采集 | 未采集 | 未采集 | 未采集 |" if isinstance(samples, list) else "| 未采集 | 未采集 | 未采集 | 未采集 | 未采集 |")
    lines.extend(["", "创建请求返回 502 或传输失败且没有 job_id 时，按真实请求身份核查，不能因此从接纳未知请求中移除。", ""])
    return lines

