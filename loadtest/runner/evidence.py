"""把 runs/ 中的各档运行摘要整理为可提交的证据目录。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from runner import config
from runner.report import _value, render_stage_report

# 证据目录中每档保留一次运行；按顺序对应计划中的执行顺序。
# 入口预检是独立结构，由 _render_preflight 单独渲染，不在此列表。
STAGE_ORDER = [
    "smoke",
    "baseline",
    "low",
    "mid_low",
    "mid",
    "recovery",
    "stability",
    "burst",
    "burst_ungated",
    "queue_pressure",
    "multi_session_smoke",
    "multi_session_pressure",
]


def latest_runs(runs_dir: Path) -> dict[str, Path]:
    """按档位选出最近一次运行目录。"""
    selected: dict[str, Path] = {}
    for path in sorted(runs_dir.iterdir()):
        if not path.is_dir():
            continue
        summary = path / "summary.json"
        if not summary.exists():
            continue
        payload = json.loads(summary.read_text(encoding="utf-8"))
        # 预检结果是另一种结构，只有负载档摘要才有 stage 与 http 字段。
        if not isinstance(payload, dict) or "http" not in payload:
            continue
        stage = payload.get("stage", "")
        if stage:
            selected[stage] = path
    return selected


def attach_server_baseline(
    runs_dir: Path, payload: dict, stage: str = ""
) -> list[str]:
    """把服务器侧只读采集结果写入档位最近一次运行的摘要。

    传入 stage 时只写该档，否则写入全部已有档位；返回被更新的运行目录名。
    """
    updated: list[str] = []
    for stage_name, path in sorted(latest_runs(runs_dir).items()):
        if stage and stage_name != stage:
            continue
        target = path / "summary.json"
        summary = json.loads(target.read_text(encoding="utf-8"))
        summary["server_baseline"] = payload
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        updated.append(f"{stage_name}:{path.name}")
    return updated


def export_job_id_lists(runs_dir: Path, out_dir: Path) -> list[Path]:
    """把各档本轮创建的唯一 job_id 按档位导出为清单文件。

    清单文件上传到服务器后作为 job_db_check.sh 的输入，用于逐 Job 核对主库终态。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for stage, path in sorted(latest_runs(runs_dir).items()):
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        job_ids = _job_ids(summary)
        target = out_dir / f"job-ids-{stage}.txt"
        target.write_text(
            ("\n".join(job_ids) + "\n") if job_ids else "",
            encoding="utf-8",
        )
        written.append(target)
    return written


def attach_job_db_check(runs_dir: Path, payload: dict, stage: str = "") -> list[str]:
    """把逐 Job 主库核对结果写入档位最近一次运行的摘要。

    传入 stage 时只写该档，否则写入全部已有档位。
    """
    updated: list[str] = []
    for stage_name, path in sorted(latest_runs(runs_dir).items()):
        if stage and stage_name != stage:
            continue
        target = path / "summary.json"
        summary = json.loads(target.read_text(encoding="utf-8"))
        summary["job_db_check"] = payload
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        updated.append(f"{stage_name}:{path.name}")
    return updated


def _job_ids(summary: dict) -> list[str]:
    """从一份运行摘要里取出本轮创建过的唯一 job_id。"""
    ids: list[str] = []
    # 优先使用创建时登记的清单：排空期限到达时未观测到终态的 Job 也要能核对。
    for job_id in summary.get("job", {}).get("created_ids") or []:
        if job_id:
            ids.append(str(job_id))
    for sample in summary.get("sse", {}).get("samples", []):
        job_id = str(sample.get("job_id") or "")
        if job_id:
            ids.append(job_id)
    for sample in summary.get("job", {}).get("samples", []):
        job_id = str(sample.get("job_id") or "")
        if job_id:
            ids.append(job_id)
    return list(dict.fromkeys(ids))

def _load_logchecks(evidence_dir: Path) -> dict:
    """按档位读取日志核查结果，供逐档报告补齐模型与 MCP 错误一节。"""
    result: dict = {}
    for path in sorted(evidence_dir.glob("logcheck-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stage = payload.get("stage")
        if stage:
            result[str(stage)] = payload
    return result


def _render_logcheck(evidence_dir: Path) -> list[str]:
    """把各档日志核查结果渲染成汇总报告的一节。

    logcheck 由 runner logcheck 生成；缺少结果时明确写成未执行，不用摘要补齐。
    """
    files = sorted(evidence_dir.glob("logcheck-*.json"))
    if not files:
        return []
    payloads = []
    for path in files:
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    if not payloads:
        return []
    lines = [
        "## 第六阶段：日志侧核查（Grafana / Loki）",
        "",
        "同源 /grafana/ 代理可读，按每档的运行时间窗口查询 web、worker、causal-mcp "
        "三个服务的日志。逐档结果见 evidence/logcheck-<档位>.json。",
        "",
        "### MCP 按算法能力统计",
        "",
        "MCP 侧每个算法能力有独立并发额度，把能力拆开后才能判断某个能力是否成功。",
        "",
        "| 档位 | 能力 | 收到 | 完成 | 失败 | 慢调用 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for payload in payloads:
        for capability, stats in (payload.get("capability") or {}).items():
            lines.append(
                f"| {payload.get('stage')} | {capability} | {stats.get('received', 0)} | "
                f"{stats.get('finished', 0)} | {stats.get('failed', 0)} | "
                f"{stats.get('slow', 0)} |"
            )
    history = payloads[-1].get("mcp_failure_history") or {}
    if history:
        lines.extend(
            [
                "",
                "### MCP 最近七天累计（跨档位）",
                "",
                f"- 完成（按能力）：{json.dumps(history.get('finished_by_capability', {}), ensure_ascii=False)}",
                f"- 失败（按能力）：{json.dumps(history.get('failed_by_capability', {}), ensure_ascii=False)}",
                f"- CDFM 权重加载次数：{history.get('cdfm_model_load_attempts')}",
                "",
            ]
        )
    failures = [item for payload in payloads for item in (payload.get("web_failures") or [])]
    if failures:
        lines.extend(["### web 请求失败", "", "| 时间 | 端点 | 状态码 | 异常 | 栈顶 |",
                      "| --- | --- | --- | --- | --- |"])
        for item in failures:
            lines.append(
                f"| {item.get('timestamp')} | {item.get('method')} {item.get('endpoint')} | "
                f"{item.get('status_code')} | {item.get('exception_type')} | "
                f"{item.get('stack_top')} |"
            )
        lines.append("")
    degraded = [
        (payload.get("stage"), item)
        for payload in payloads
        for item in (payload.get("worker_degraded") or [])
    ]
    if degraded:
        lines.extend(["### worker 降级与告警", "", "| 档位 | 时间 | 事件码 | 明细 |",
                      "| --- | --- | --- | --- |"])
        for stage, item in degraded:
            lines.append(
                f"| {stage} | {item.get('timestamp')} | {item.get('event_code')} | "
                f"{json.dumps(item.get('details'), ensure_ascii=False)} |"
            )
        lines.append("")
    lines.extend(_logcheck_conclusions(payloads))
    return lines


def _logcheck_conclusions(payloads: list[dict]) -> list[str]:
    """按本轮 logcheck 数据生成日志侧结论，逐条给出可核对的数量。

    结论只由本轮查询结果推导：没有任何能力失败时写明“本轮未出现能力失败”，
    不沿用任何历史轮次的判断。
    """
    lines = ["### 日志侧结论", ""]
    propagated = [
        str(payload.get("stage"))
        for payload in payloads
        if (payload.get("request_id_propagation") or {}).get("propagated") is True
    ]
    checked = [
        str(payload.get("stage"))
        for payload in payloads
        if (payload.get("request_id_propagation") or {}).get("job_id")
    ]
    if checked:
        verdict = "均已沿用" if len(propagated) == len(checked) else "部分沿用"
        lines.append(
            f"- request_id 沿用：可核查的 {len(checked)} 个档位（{'、'.join(checked)}）中 "
            f"{verdict}，逐档明细见 evidence/logcheck-<档位>.json 的 "
            "request_id_propagation 字段。"
        )
    else:
        lines.append("- request_id 沿用：本轮没有可用于关联的 job_id，未取得该项结论。")
    failing = [
        (str(payload.get("stage")), name, stats)
        for payload in payloads
        for name, stats in (payload.get("capability") or {}).items()
        if stats.get("failed", 0) > 0
    ]
    if failing:
        for stage, name, stats in failing:
            lines.append(
                f"- {stage} 的 {name}：收到 {stats.get('received', 0)}，完成 "
                f"{stats.get('finished', 0)}，失败 {stats.get('failed', 0)}，慢调用 "
                f"{stats.get('slow', 0)}；完成数为 0 时该能力在本档内未成功过一次。"
            )
    else:
        lines.append("- 本轮各档未出现按能力统计的失败记录。")
    web_failure_total = sum(len(payload.get("web_failures") or []) for payload in payloads)
    lines.append(f"- web 请求失败条数：{web_failure_total}。")
    lines.append("")
    return lines



def _summary_table(selected: dict[str, Path]) -> list[str]:
    """渲染跨档摘要表，覆盖计划要求的每一类指标。"""
    rows = ["## 跨档摘要", ""]
    header = (
        "| 档位 | run_id | 虚拟用户 | RPS | p95 | 5xx | Job 创建 | Job 终态 | "
        "排队 p50/p95 | 执行 p50/p95 | 总耗时 p50/p95 | "
        "活动 Job 峰值 | SSE 峰值 | 断线/重连 | 漏事件 | CPU 均值/峰值 | 可用内存最低 |"
    )
    rows.extend([
        header,
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for stage in STAGE_ORDER:
        path = selected.get(stage)
        if path is None:
            rows.append(
                f"| {stage} | 未执行 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |"
            )
            continue
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        http = summary.get("http") or {}
        job = summary.get("job") or {}
        sse = summary.get("sse") or {}
        curve = summary.get("resource_curve") or {}
        # 专项流程不运行分级负载模型，并发与耗时分位对它不适用；写成“不适用”，
        # 与“应当采集却缺失”的未采集区分开。
        special = bool(summary.get("checks"))
        na = "不适用"

        def value_for(field):
            return na if special else _value(field)

        def pair_for(p50, p95):
            return na if special else _seconds_pair(p50, p95)

        rows.append(
            "| {stage} | {run} | {users} | {rps} | {p95} | {f5} | {created} | {terminal} | "
            "{queue} | {execution} | {total} | "
            "{peak_jobs} | {peak_sse} | {dc}/{rc} | {miss} | {cpu} | {mem} |".format(
                stage=stage,
                run=summary.get("run_id"),
                users=summary.get("users"),
                rps=round(http.get("rps") or 0, 2),
                p95=f"{round(http.get('p95_ms') or 0, 1)} ms",
                f5=http.get("server_errors_5xx", 0),
                created=job.get("created", 0),
                terminal=json.dumps(job.get("terminal_status_counts") or {}, ensure_ascii=False),
                  queue=pair_for(job.get("queue_p50_seconds"), job.get("queue_p95_seconds")),
                  execution=pair_for(
                      job.get("execution_p50_seconds"), job.get("execution_p95_seconds")
                  ),
                  total=pair_for(job.get("total_p50_seconds"), job.get("total_p95_seconds")),
                  peak_jobs=value_for(summary.get("peak_active_jobs")),
                  peak_sse=value_for(summary.get("peak_sse")),
                dc=sse.get("disconnects", 0),
                rc=sse.get("reconnects", 0),
                miss=sse.get("verified_missing_events", 0),
                cpu=f"{curve.get('cpu_percent_avg')}/{curve.get('cpu_percent_max')} %" if curve else "未采集",
                mem=f"{curve.get('mem_available_mb_min')} MB" if curve else "未采集",
            )
        )
    rows.extend([
        "",
        "recovery 是专项流程，创建数只统计真正进入执行的 Job，终态分布不适用；"
        "其检查项见该档报告的“专项检查”一节。",
        "排队、执行、总耗时按 Job 逐个统计，起点是创建 Job 请求发出，终点是 SSE 收到终态事件；"
        "执行时间为总耗时减去排队时间。观察期限内未收到终态的 Job 不进这两个百分位，"
        "而是计入观察超时数量与排队等待下界。",
        "活动 Job 上限和 SSE 上限是本轮的客户端配置值，实际峰值由本节数值给出；"
        "上限为“不限”表示客户端不限制创建，运行中的并发数量由服务器默认 worker 能力形成。",
        "",
    ])
    return rows


def _seconds_pair(p50: float | None, p95: float | None) -> str:
    """把一对秒值渲染成 p50/p95 单元格；两个值都缺失时返回“未采集”。"""
    if p50 is None and p95 is None:
        return "未采集"
    return f"{_seconds(p50)} / {_seconds(p95)}"


def _seconds(value: float | None) -> str:
    """把秒值格式化为一位小数；空值返回“未采集”。"""
    if value is None:
        return "未采集"
    return f"{value:.1f} s"


def build_evidence(runs_dir: Path, evidence_dir: Path) -> tuple[Path, list[Path]]:
    """生成合并报告与每档独立报告，返回合并报告路径与文件清单。"""
    selected = latest_runs(runs_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    sections: list[str] = [
        "# staging 压测证据汇总",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 入口：{config.base_url()}",
        "- 数据来源：本报告只使用 local/runs/ 下本轮各档运行摘要、"
        "local/evidence/ 的日志核查与本轮服务器只读采集；没有采集的项按未采集写明。",
        "",
    ]
    sections.extend(_summary_table(selected))
    preflight = _render_preflight(runs_dir)
    if preflight:
        sections.extend(preflight)
    logchecks = _load_logchecks(evidence_dir)
    for stage in STAGE_ORDER:
        path = selected.get(stage)
        if path is None:
            sections.extend([f"## {stage}", "", "本次未执行。", ""])
            continue
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        body = render_stage_report(summary, logchecks.get(stage))
        stage_file = evidence_dir / f"{stage}-report.md"
        stage_file.write_text(body, encoding="utf-8")
        written.append(stage_file)
        sections.extend(body.splitlines())
        sections.append("")
    logcheck = _render_logcheck(evidence_dir)
    if logcheck:
        sections.extend(logcheck)
    job_db = _render_job_db_all(selected)
    if job_db:
        sections.extend(job_db)
    unobserved = _render_unobserved_jobs(evidence_dir)
    if unobserved:
        sections.extend(unobserved)
    # 合并报告按生成日期命名，便于跨轮次区分；日期取最新一次运行的开始时间。
    combined = evidence_dir / f"loadtest-report-{_report_date(selected)}.md"
    sections.extend(_limitations(selected))
    combined.write_text("\n".join(sections), encoding="utf-8")
    written.append(combined)
    return combined, written


def _report_date(selected: dict[str, Path]) -> str:
    """返回合并报告文件名使用的日期；没有运行记录时退回当天。

    取全部档位里最早一次的运行开始时间。一轮压测可能跨过午夜，也可能在全部档位
    跑完后补跑个别档位；用最早时间可以让同一轮的结果始终落在同一个报告文件里，
    不会因为补跑一档就分裂出第二份报告。
    """
    started = [
        summary["started_at"]
        for summary in (
            json.loads((path / "summary.json").read_text(encoding="utf-8"))
            for path in selected.values()
        )
        if summary.get("started_at")
    ]
    if not started:
        return time.strftime("%Y%m%d")
    return time.strftime("%Y%m%d", time.localtime(min(started)))


def _render_job_db_all(selected: dict[str, Path]) -> list[str]:
    """把各档的逐 Job 主库核对结果渲染成一节；没有核对数据时不输出。

    核对数据来自各档运行摘要的 job_db_check 字段，由 runner attach-job-db 写入。
    逐档分开渲染，避免只显示最后一档而丢掉其余档位的独立证据。
    """
    payloads: list[tuple[str, dict]] = []
    for stage in STAGE_ORDER:
        path = selected.get(stage)
        if path is None:
            continue
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        payload = summary.get("job_db_check")
        if isinstance(payload, dict):
            payloads.append((stage, payload))
    if not payloads:
        return []
    lines = [
        "## 主库 Job 终态核对",
        "",
        "每档的核对都由 loadtest/job_db_check.sh 在服务器上只读执行，输入是该档本轮"
        "导出的 job_id 清单；这是数据库层证据，与管理端网页界面核对相互独立。",
        "",
        "| 档位 | 核对时刻 | 全量终态分布 | 近三小时终态分布 | 清单内 Job | 一致 | 不一致或缺失 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for stage, payload in payloads:
        if payload.get("status") == "missing":
            lines.append(
                f"| {stage} | 未取得 | {payload.get('reason')} |  |  |  |  |"
            )
            continue
        consistent, mismatched = _comparison_counts(payload)
        rows = payload.get("jobs") or []
        lines.append(
            f"| {stage} | {payload.get('collected_at')} | "
            f"{json.dumps(payload.get('by_status_all_time') or {}, ensure_ascii=False)} | "
            f"{json.dumps(payload.get('by_status_last_3h') or {}, ensure_ascii=False)} | "
            f"{len(rows)} | {consistent} | {mismatched} |"
        )
    lines.append("")
    failed = payloads[-1][1].get("failed_jobs") or []
    if failed:
        lines.extend(
            [
                "### 主库失败 Job",
                "",
                "| job_id | 账号 | 创建时间 | 结束时间 | 尝试次数 | 错误信息 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in failed:
            lines.append(
                f"| {item.get('job_id')} | {item.get('user_id')} | {item.get('created_at')} | "
                f"{item.get('finished_at')} | {item.get('attempt_count')} | "
                f"{item.get('error_message')} |"
            )
        lines.append("")
    for stage, payload in payloads:
        lines.extend(_render_job_db_stage(stage, payload))
    lines.extend(
        [
            "逐 Job 比对只覆盖各档清单文件里列出的 job_id；清单之外的 Job 不参与本次判定。",
            "",
        ]
    )
    return lines


def _comparison_counts(payload: dict) -> tuple[int, int]:
    """统计一份主库核对里与客户端一致、不一致或缺失的条数。"""
    rows = payload.get("jobs") or []
    if not rows or rows[0].get("job_id") is None:
        return 0, 0
    client_results = _client_job_results(latest_runs(config.RUNS_DIR))
    consistent = 0
    mismatched = 0
    for row in rows:
        job_id = str(row.get("job_id"))
        client = client_results.get(job_id, {})
        client_status = client.get("final_status") or "未记录"
        if row.get("missing") or client_status == "未记录":
            mismatched += 1
        elif _status_equivalent(client_status, row.get("status")):
            consistent += 1
        else:
            mismatched += 1
    return consistent, mismatched


def _render_job_db_stage(stage: str, payload: dict) -> list[str]:
    """渲染一个档位的逐 Job 比对明细。"""
    if payload.get("status") == "missing":
        return [f"### {stage}", "", f"核对未取得数据（{payload.get('reason')}）。", ""]
    lines = [f"### {stage}", ""]
    lines.extend(_job_comparison(payload))
    return lines


def _render_unobserved_jobs(evidence_dir: Path) -> list[str]:
    """渲染“创建了但客户端未观测到终态”的 Job 的补充主库核对。

    停止提交后到达排空期限时，仍在排队的 Job 可能没有观测结果。这些 Job 已由
    loadtest/job_db_check.sh 用同一只读口径单独核对，结果存放在 evidence 目录的
    job-db-*-extras.json；本节把它们汇总出来，避免创建数与终态数之间的差额被
    当成失败或漏记。
    """
    files = sorted(evidence_dir.glob("job-db-*-extras.json"))
    if not files:
        return []
    rows: list[tuple[str, dict]] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("status") == "missing":
            rows.append((path.name, payload))
            continue
        for job in payload.get("jobs") or []:
            rows.append((path.name, job))
    if not rows:
        return []
    lines = [
        "## 客户端未观测到终态 Job 的补充核对",
        "",
        "停止提交后到达排空期限时，仍处于排队状态的 Job 没有客户端终态记录；"
        "它们的创建数与终态数的差额不表示失败。这些 job_id 由只读脚本在主库单独核对，"
        "结果见 evidence/job-db-*-extras.json：",
        "",
        "| 来源文件 | job_id | 主库终态 | 账号 | 创建时间 | 结束时间 | 执行释放 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, job in rows:
        if job.get("missing"):
            lines.append(
                f"| {name} | 未取得 | {job.get('reason') or '主库无该 Job'} |  |  |  |  |"
            )
            continue
        lines.append(
            f"| {name} | {job.get('job_id')} | {job.get('status')} | {job.get('user_id')} | "
            f"{job.get('created_at')} | {job.get('finished_at')} | "
            f"{job.get('execution_release_reason') or job.get('execution_state') or '未取得'} |"
        )
    statuses: dict[str, int] = {}
    for _name, job in rows:
        key = str(job.get("status") or "missing")
        statuses[key] = statuses.get(key, 0) + 1
    lines.extend(["", f"- 补充核对终态分布：{json.dumps(statuses, ensure_ascii=False)}", ""])
    return lines


def _job_comparison(payload: dict) -> list[str]:
    """把清单内每个 Job 的客户端结果与主库结果逐条比对。"""
    rows = payload.get("jobs") or []
    if rows and rows[0].get("job_id") is None:
        reason = rows[0].get("reason") or "未提供 job_id 清单文件"
        return [f"逐 Job 比对未执行：{reason}；本轮只有全库分布可用。", ""]
    if not rows:
        return [
            "逐 Job 比对未执行：本轮没有提供 job_id 清单文件，只有全库分布可用。",
            "",
        ]
    selected = latest_runs(config.RUNS_DIR)
    client_results = _client_job_results(selected)
    lines = [
        "### 逐 Job 比对",
        "",
        "| job_id | 客户端终态 | 主库终态 | 会话归属 | 执行释放 | 结论 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    consistent = 0
    mismatched = 0
    for row in rows:
        job_id = str(row.get("job_id"))
        client = client_results.get(job_id, {})
        client_status = client.get("final_status") or "未记录"
        db_status = row.get("status")
        session_note = (
            f"{row.get('session_id')}（账号 {row.get('user_id')}）"
            if row.get("session_id")
            else "未取得"
        )
        release = row.get("execution_release_reason") or row.get("execution_state") or "未取得"
        if row.get("missing"):
            verdict = "主库无该 Job"
            mismatched += 1
        elif client_status in ("未记录",):
            verdict = "客户端清单缺该 Job"
            mismatched += 1
        elif _status_equivalent(client_status, db_status):
            verdict = "一致"
            consistent += 1
        else:
            verdict = "不一致"
            mismatched += 1
        lines.append(
            f"| {job_id} | {client_status} | {db_status} | {session_note} | {release} | {verdict} |"
        )
    lines.extend(
        [
            "",
            f"- 逐 Job 比对结果：一致 {consistent} 条，不一致或缺失 {mismatched} 条。",
            "",
        ]
    )
    return lines


def _client_job_results(selected: dict[str, Path]) -> dict[str, dict]:
    """汇总本轮各档客户端记录的 Job 终态，供逐 Job 比对使用。"""
    results: dict[str, dict] = {}
    for path in selected.values():
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        for sample in summary.get("sse", {}).get("samples", []):
            job_id = str(sample.get("job_id") or "")
            if not job_id:
                continue
            terminal = sample.get("terminal_event")
            results[job_id] = {
                "stage": summary.get("stage"),
                "terminal_event": terminal,
                "final_status": _event_to_status(terminal, sample.get("error")),
            }
        for job in summary.get("job", {}).get("samples", []):
            job_id = str(job.get("job_id") or "")
            if not job_id:
                continue
            results.setdefault(
                job_id,
                {
                    "stage": summary.get("stage"),
                    "terminal_event": None,
                    "final_status": job.get("final_status"),
                },
            )
    return results


def _event_to_status(terminal_event, error) -> str | None:
    """把 SSE 终态事件映射为主库终态口径。"""
    if terminal_event == "final_result":
        return "succeeded"
    if terminal_event == "canceled":
        return "canceled"
    if terminal_event == "error":
        return "failed"
    if terminal_event == "interrupt":
        return "waiting_input"
    if error:
        return "unfinished"
    return None


def _status_equivalent(client_status: str, db_status) -> bool:
    """比较客户端终态与主库终态；等待输入和未完成不算一致。"""
    return client_status == db_status


def _limitations(selected: dict[str, Path]) -> list[str]:
    """列出未执行项与原因，避免把部分结果当成完整验收。"""
    lines = ["## 本轮的采集边界与未覆盖项", ""]
    for stage, path in sorted(selected.items()):
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        baseline = summary.get("server_baseline")
        curve = summary.get("resource_curve")
        job_db = summary.get("job_db_check")
        gaps: list[str] = []
        if not baseline:
            gaps.append("服务器侧单次快照未采集")
        if not curve:
            gaps.append("负载窗口资源采样未采集")
        if not job_db:
            gaps.append("逐 Job 主库核对未执行")
        if gaps:
            lines.append(f"- {stage}：暂无证据的项目为{'、'.join(gaps)}。")
    lines.extend(
        [
            "",
            "客户端侧可复现的检查均已执行并留下逐档证据；上面列出的项目不在本轮证据范围内，"
            "不因本报告其余部分通过而被视为通过。",
            "服务器侧指标只有通过 loadtest/server_side_collect.sh 与 "
            "loadtest/stage_resource_sample.sh 的只读采集才写入报告；缺少采集时逐档写明未采集。",
            "逐 Job 终态比对需要先导出本轮 job_id 清单并上传到服务器，再运行 "
            "loadtest/job_db_check.sh；没有该清单时脚本只输出全库分布，报告写明该状态。",
            "",
        ]
    )
    return lines


def _render_preflight(runs_dir: Path) -> list[str]:
    """把最近一次入口预检结果渲染成汇总报告的第一节。"""
    candidates = sorted(runs_dir.glob("*preflight.json"))
    if not candidates:
        return []
    payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
    http = payload.get("http", {})
    lines = [
        "## 第一阶段：入口预检",
        "",
        f"- 入口：{payload.get('base_url')}",
        f"- TCP 可达：{json.dumps(payload.get('tcp', {}), ensure_ascii=False)}",
        f"- 客户端：{json.dumps(payload.get('client', {}), ensure_ascii=False)}",
        "",
        "| 路径 | 状态码 | 耗时 | request_id | 跳转 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for path, detail in http.items():
        lines.append(
            f"| {path} | {detail.get('status')} | {detail.get('duration_ms')} ms | "
            f"{detail.get('request_id')} | {detail.get('location') or ''} |"
        )
    lines.append("")
    return lines
