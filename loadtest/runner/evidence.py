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
    "cdfm_fixed",
    "cdfm_rate",
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
        export_request_manifest(summary, out_dir / f"requests-{stage}.json")
    return written


def export_request_manifest(summary: dict, target: Path) -> Path:
    """保留原请求身份，包括没有取得 job_id 的请求；不从旧样本猜测幂等键。"""
    fields = ("logical_request_id", "username", "user_index", "session_id",
              "idempotency_key", "job_id", "admission", "created_request_at", "group")
    payload = {"schema_version": "loadtest-request-manifest-v2", "run_id": summary.get("run_id"),
               "requests": [{key: row.get(key) for key in fields} for row in summary.get("requests", [])],
               "job_ids": _job_ids(summary)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _db_scope_matches(summary: dict, payload: dict) -> bool:
    """v1 缺 scope 仅以精确 run_id 和完整 Job 子集验证，不能扩大采集归属。"""
    if not payload.get("run_id") or payload["run_id"] != summary.get("run_id"):
        return False
    if payload.get("scope") == "run":
        return True
    if payload.get("schema_version") != "loadtest-job-db-check-v1" or payload.get("scope") is not None:
        return False
    allowed = set(_job_ids(summary))
    rows = payload.get("jobs")
    return isinstance(rows, list) and all(isinstance(row, dict) and row.get("job_id")
        and str(row["job_id"]) in allowed for row in rows)


def merge_job_db_check(summary: dict, payload: dict) -> dict:
    """按 run_id 合并证据，再交给容量统计；旧证据没有的身份信息保持未知。"""
    from runner.capacity import enrich_summary

    if not _db_scope_matches(summary, payload):
        raise ValueError("主库证据归属无效：需同 run_id 和 scope=run；旧 v1 缺 scope 时须全部 Job 属于本轮")
    summary["job_db_check"] = payload
    summary["db_checked_at"] = payload.get("db_checked_at") or payload.get("collected_at")
    resolutions = {row.get("logical_request_id"): row for row in payload.get("requests", [])
                   if row.get("logical_request_id")}
    for row in summary.get("requests", []):
        match = resolutions.get(row.get("logical_request_id"))
        if match:
            row["db_admission"] = match.get("admission", "unknown")
            if row.get("admission") not in {"unknown", "pending", "acceptance_unknown"}:
                continue
            if match.get("identity_match") is False:
                row["db_identity_match"] = False
                continue
            if (match.get("admission") == "accepted" and match.get("job_id")
                    and match.get("identity_match") is True):
                row.update(job_id=match["job_id"], admission="accepted")
            elif match.get("admission") == "not_found":
                row["admission"] = "not_found"
    return enrich_summary(summary)


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
        if payload.get("run_id") and payload["run_id"] != summary.get("run_id"):
            continue
        summary = merge_job_db_check(summary, payload)
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        updated.append(f"{stage_name}:{path.name}")
    return updated


def merge_logcheck(summary: dict, payload: dict) -> dict:
    """将本轮真实 MCP 调用交给容量模块；报告可以直接消费实时 logcheck。"""
    from runner.capacity import enrich_summary

    if payload.get("scope") != "run" or payload.get("run_id") != summary.get("run_id"):
        raise ValueError("日志证据缺少本轮 scope/run_id")
    summary["logcheck"] = payload
    cdfm = payload.get("cdfm") or {}
    summary["cdfm_log_evidence"] = {**cdfm, "run_id": payload["run_id"], "scope": "run"}
    summary["cdfm_calls"] = [row for row in cdfm.get("records", [])
                             if row.get("source") == "mcp_logs" and row.get("job_id") in _job_ids(summary)]
    return enrich_summary(summary)


def _job_ids(summary: dict) -> list[str]:
    """从一份运行摘要里取出本轮创建过的唯一 job_id。"""
    ids: list[str] = []
    for request in summary.get("requests", []):
        if request.get("job_id"):
            ids.append(str(request["job_id"]))
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

def _load_logchecks(evidence_dir: Path, selected: dict[str, Path] | None = None) -> dict:
    """按档位读取日志核查结果，供逐档报告补齐模型与 MCP 错误一节。"""
    result: dict = {}
    if not selected:
        return result
    for path in sorted(evidence_dir.glob("logcheck-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stage = payload.get("stage")
        if stage:
            if selected is not None:
                if stage not in selected:
                    continue
                summary = json.loads((selected[stage] / "summary.json").read_text(encoding="utf-8"))
                if payload.get("run_id") != summary.get("run_id"):
                    continue
            if payload.get("scope") == "run" and payload.get("run_id"):
                result[str(stage)] = payload
    return result


def _render_logcheck(evidence_dir: Path, selected: dict[str, Path] | None = None) -> list[str]:
    """把各档日志核查结果渲染成汇总报告的一节。

    logcheck 由 runner logcheck 生成；缺少结果时明确写成未执行，不用摘要补齐。
    """
    payloads = list(_load_logchecks(evidence_dir, selected).values())
    if not payloads:
        return []
    lines = [
        "## 第六阶段：日志侧核查（Grafana / Loki）",
        "",
        "日志核查只使用有本轮结构化 job_id 归属的事件；查询失败、缺失或截断时保持未知。"
        "逐档状态与覆盖范围见 evidence/logcheck-<档位>.json。",
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
                f"| {payload.get('stage')} | {capability} | {_value(stats.get('received'))} | "
                f"{_value(stats.get('finished'))} | {_value(stats.get('failed'))} | "
                f"{_value(stats.get('slow'))} |"
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
        if (stats.get("failed") or 0) > 0
    ]
    if failing:
        for stage, name, stats in failing:
            lines.append(
                f"- {stage} 的 {name}：收到 {stats.get('received', 0)}，完成 "
                f"{stats.get('finished', 0)}，失败 {stats.get('failed', 0)}，慢调用 "
                f"{stats.get('slow', 0)}；完成数为 0 时该能力在本档内未成功过一次。"
            )
    elif all(payload.get("status") == "collected" for payload in payloads):
        lines.append("- 在已完整采集的本轮 Job 日志中未观测到按能力统计的失败记录。")
    else:
        lines.append("- 日志采集不完整，无法判定本轮是否存在能力失败。")
    web_failure_total = sum(len(payload.get("web_failures") or []) for payload in payloads)
    lines.append(f"- 已观测到带本轮 Job 归属的 web 请求失败条数：{web_failure_total}；采集不完整时只代表已观测下界。")
    lines.append("")
    return lines



def _summary_table(selected: dict[str, Path]) -> list[str]:
    """跨档表只采用已核对的服务器毫秒计时；旧摘要缺失项保持未知。"""
    from runner.capacity import enrich_summary

    columns = ["档位", "run_id", "虚拟用户", "HTTP RPS", "HTTP p95 ms", "5xx", "唯一 Job", "主库核对/成功",
               "已完成样本排队 p95 ms（分组）", "已完成样本执行跨度 p95 ms（分组）",
               "已完成样本服务器总耗时 p95 ms（分组）", "活动 Job 峰值", "SSE 峰值", "断线/重连",
               "已核对缺失事件", "全窗口 CPU 均值/峰值 %", "可用内存最低 MB"]
    rows = ["## 跨档摘要", "", "| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for stage in STAGE_ORDER:
        path = selected.get(stage)
        if path is None:
            cells = [stage, "未执行"] + [""] * (len(columns) - 2)
        else:
            summary = enrich_summary(json.loads((path / "summary.json").read_text(encoding="utf-8")))
            http, job, sse = (summary.get(key) or {} for key in ("http", "job", "sse"))
            capacity, curve = summary.get("capacity") or {}, summary.get("resource_curve") or {}
            success = capacity.get("db_success") or {}
            groups = capacity.get("latency_groups") or {}

            def grouped(field):
                parts = []
                for name, bucket in groups.items():
                    value = bucket.get(f"completed_sample_p95_{field}")
                    count = bucket.get(f"{field}_samples")
                    parts.append(f"{name}: {_value(value)}（n={_value(count)}）")
                return "；".join(parts) or "未采集"

            cells = [stage, summary.get("run_id"), summary.get("users"), _value(http.get("rps")),
                     _value(http.get("p95_ms")), _value(http.get("server_errors_5xx")), _value(job.get("created")),
                     f"{_value(success.get('checked'))}/{_value(success.get('succeeded'))}",
                     grouped("initial_queue_ms"), grouped("execution_span_ms"), grouped("server_total_ms"),
                     _value(summary.get("peak_active_jobs")), _value(summary.get("peak_sse")),
                     f"{_value(sse.get('disconnects'))}/{_value(sse.get('reconnects'))}",
                     _value((capacity.get("sse_integrity") or {}).get("missing_events")),
                     f"{_value(curve.get('cpu_percent_avg'))}/{_value(curve.get('cpu_percent_max'))}",
                     _value(curve.get("mem_available_mb_min"))]
        rows.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    rows.extend(["", "服务器计时单位为毫秒：首次排队为 first_started_at − created_at，执行跨度为 "
                 "finished_at − first_started_at，总耗时为 finished_at − created_at。执行跨度包含期间的等待、"
                 "恢复和重试间隔，不等同于算法 CPU 时间。按普通、等待输入、恢复及重试组分别展示已完成样本 p95 与样本数。",
                 "客户端等待从请求发出到收到终态单独计量；未完成请求只提供等待下界。漏事件必须比较逐 Job 的持久化 ID 集合与客户端接收集合，缺少任一集合时为未采集。",
                 "全窗口资源值包含负载与观察期，分阶段资源见各档报告。", ""])
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
    logchecks = _load_logchecks(evidence_dir, selected)
    for stage in STAGE_ORDER:
        path = selected.get(stage)
        if path is None:
            sections.extend([f"## {stage}", "", "本次未执行。", ""])
            continue
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        scoped_log = logchecks.get(stage)
        if scoped_log and scoped_log.get("scope") == "run" and scoped_log.get("run_id") == summary.get("run_id"):
            summary = merge_logcheck(summary, scoped_log)
        body = render_stage_report(summary, logchecks.get(stage))
        stage_file = evidence_dir / f"{stage}-report.md"
        stage_file.write_text(body, encoding="utf-8")
        written.append(stage_file)
        sections.extend(body.splitlines())
        sections.append("")
    logcheck = _render_logcheck(evidence_dir, selected)
    if logcheck:
        sections.extend(logcheck)
    job_db = _render_job_db_all(selected)
    if job_db:
        sections.extend(job_db)
    unobserved = _render_unobserved_jobs(evidence_dir, selected)
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
        if isinstance(payload, dict) and _db_scope_matches(summary, payload):
            payloads.append((stage, payload))
    if not payloads:
        return []
    lines = [
        "## 主库 Job 终态核对",
        "",
        "每档的核对都由 loadtest/job_db_check.sh 在服务器上只读执行，输入是该档本轮"
        "导出的请求身份和唯一 job_id 清单；每个 Job 使用独立只读事务。核对开始与完成时刻"
        "表示采集区间，不能解释为同一瞬时快照；逐 Job 时刻见 db_read_started_at/db_read_finished_at。",
        "",
        "| 档位 | 核对完成时刻 | 本轮状态分布 | 采集状态 | 清单内 Job | 一致 | 不一致或缺失 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for stage, payload in payloads:
        if payload.get("status") in {"missing", "unknown", "error"}:
            lines.append(
                f"| {stage} | 未取得 | {payload.get('reason')} |  |  |  |  |"
            )
            continue
        consistent, mismatched = _comparison_counts(payload, selected)
        rows = payload.get("jobs") or []
        lines.append(
            f"| {stage} | {payload.get('collected_at')} | "
            f"{json.dumps(payload.get('by_status_run') or {}, ensure_ascii=False)} | "
            f"{payload.get('status')} | "
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
        lines.extend(_render_job_db_stage(stage, payload, selected))
    lines.extend(
        [
            "逐 Job 比对只覆盖各档清单文件里列出的 job_id；清单之外的 Job 不参与本次判定。",
            "",
        ]
    )
    return lines


def _comparison_counts(payload: dict, selected: dict[str, Path] | None = None) -> tuple[int, int]:
    """统计一份主库核对里与客户端一致、不一致或缺失的条数。"""
    rows = payload.get("jobs") or []
    if not rows or rows[0].get("job_id") is None:
        return 0, 0
    client_results = _client_job_results(selected or {}, payload.get("run_id"))
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


def _render_job_db_stage(stage: str, payload: dict, selected: dict[str, Path] | None = None) -> list[str]:
    """渲染一个档位的逐 Job 比对明细。"""
    if payload.get("status") in {"missing", "unknown", "error"}:
        return [f"### {stage}", "", f"核对未取得数据（{payload.get('reason')}）。", ""]
    lines = [f"### {stage}", ""]
    lines.extend(_job_comparison(payload, selected))
    return lines


def _render_unobserved_jobs(evidence_dir: Path, selected: dict[str, Path] | None = None) -> list[str]:
    """渲染“创建了但客户端未观测到终态”的 Job 的补充主库核对。

    停止提交后到达排空期限时，仍在排队的 Job 可能没有观测结果。这些 Job 已由
    loadtest/job_db_check.sh 用同一只读口径单独核对，结果存放在 evidence 目录的
    job-db-*-extras.json；本节把它们汇总出来，避免创建数与终态数之间的差额被
    当成失败或漏记。
    """
    files = sorted(evidence_dir.glob("job-db-*-extras.json"))
    if not files:
        return []
    run_ids = {json.loads((path / "summary.json").read_text(encoding="utf-8")).get("run_id")
               for path in (selected or {}).values()}
    rows: list[tuple[str, dict]] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("scope") != "run" or not payload.get("run_id") or payload["run_id"] not in run_ids:
            continue
        if payload.get("status") in {"missing", "unknown", "error"}:
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


def _job_comparison(payload: dict, selected: dict[str, Path] | None = None) -> list[str]:
    """把清单内每个 Job 的客户端结果与主库结果逐条比对。"""
    rows = payload.get("jobs") or []
    if rows and rows[0].get("job_id") is None:
        reason = rows[0].get("reason") or "未提供 job_id 清单文件"
        return [f"逐 Job 比对未执行：{reason}。", ""]
    if not rows:
        return [
            "逐 Job 比对未执行：本轮没有可供逐 Job 比对的主库记录。",
            "",
        ]
    client_results = _client_job_results(selected or {}, payload.get("run_id"))
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


def _client_job_results(selected: dict[str, Path], run_id: str | None = None) -> dict[str, dict]:
    """汇总本轮各档客户端记录的 Job 终态，供逐 Job 比对使用。"""
    results: dict[str, dict] = {}
    for path in selected.values():
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        if not run_id or summary.get("run_id") != run_id:
            continue
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
            "检查结论以本轮实际采集状态与覆盖率为准，缺少证据的项目保持未知。",
            "服务器侧指标只有通过 loadtest/server_side_collect.sh 与 "
            "loadtest/stage_resource_sample.sh 的只读采集才写入报告；缺少采集时逐档写明未采集。",
            "逐 Job 终态比对需要先导出本轮 job_id 清单并上传到服务器，再运行 "
            "loadtest/job_db_check.sh；没有本轮清单时核对状态为 unknown，不查询全库补充结论。",
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
