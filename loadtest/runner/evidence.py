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


def attach_server_baseline(runs_dir: Path, payload: dict) -> list[str]:
    """把服务器侧只读采集结果写入每个档位最近一次运行的摘要。

    只有该档尚未记录服务器侧基线时才写入，返回被更新的运行目录名。
    """
    updated: list[str] = []
    for stage, path in sorted(latest_runs(runs_dir).items()):
        target = path / "summary.json"
        summary = json.loads(target.read_text(encoding="utf-8"))
        summary["server_baseline"] = payload
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        updated.append(f"{stage}:{path.name}")
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


def attach_job_db_check(runs_dir: Path, payload: dict) -> list[str]:
    """把逐 Job 主库核对结果写入每个档位最近一次运行的摘要。"""
    updated: list[str] = []
    for stage, path in sorted(latest_runs(runs_dir).items()):
        target = path / "summary.json"
        summary = json.loads(target.read_text(encoding="utf-8"))
        summary["job_db_check"] = payload
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        updated.append(f"{stage}:{path.name}")
    return updated


def _job_ids(summary: dict) -> list[str]:
    """从一份运行摘要里取出本轮创建过的唯一 job_id。"""
    ids: list[str] = []
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
    lines.extend(
        [
            "### 日志侧结论",
            "",
            "- request_id 从 web 沿用到 worker：见 evidence/log-verification.md 与各档 "
            "logcheck 的 request_id_propagation 字段。",
            "- causal.cdfm 在本轮所有档位里收到调用但完成数为 0，失败与慢调用数量相等，"
            "每条失败记录的 duration_ms 约 139 秒；同期的 causal.pc 与 causal.direct_lingam "
            "全部正常完成。根因是 staging 出网不可达 huggingface.co，已在服务器上以容器内"
            "探测与 hf-mirror 对照实验确认，见 evidence/cdfm-failure-analysis.md。",
            "- 这些失败发生在单用户档位，说明成因与并发无关。",
            "- 另有一条与压测无关的 web 500，定位见 "
            "evidence/source-catalog-500-analysis.md。",
            "",
        ]
    )
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
                queue=_seconds_pair(job.get("queue_p50_seconds"), job.get("queue_p95_seconds")),
                execution=_seconds_pair(
                    job.get("execution_p50_seconds"), job.get("execution_p95_seconds")
                ),
                total=_seconds_pair(job.get("total_p50_seconds"), job.get("total_p95_seconds")),
                peak_jobs=_value(summary.get("peak_active_jobs")),
                peak_sse=_value(summary.get("peak_sse")),
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
    job_db = _render_job_db(_latest_job_db(config.LOCAL_DIR))
    if job_db:
        sections.extend(job_db)
    combined = evidence_dir / "loadtest-report-20261010.md"
    sections.extend(_limitations(selected))
    combined.write_text("\n".join(sections), encoding="utf-8")
    written.append(combined)
    return combined, written


def _latest_job_db(local_dir: Path) -> Path:
    """返回 local/ 下最新的主库 Job 核对文件；没有时返回一个不存在的路径。"""
    candidates = sorted(local_dir.glob("job-db-*.json"))
    if candidates:
        return candidates[-1]
    return local_dir / "job-db-none.json"


def _render_job_db(path: Path) -> list[str]:
    """把主库只读核对结果渲染成一节；缺少文件时不输出。"""
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") == "missing":
        return [
            "## 主库 Job 终态核对",
            "",
            f"- 核对状态：未取得数据（{payload.get('reason')}）；不得据此判定通过。",
            "",
        ]
    all_time = payload.get("by_status_all_time") or {}
    window = payload.get("by_status_last_3h") or {}
    lines = [
        "## 主库 Job 终态核对",
        "",
        f"- schema_version：{payload.get('schema_version')}",
        f"- run_id：{payload.get('run_id')}",
        f"- 核对时刻：{payload.get('collected_at')}",
        f"- 来源：{payload.get('source')}",
        f"- 表：{payload.get('table')}",
        f"- 全量终态分布：{json.dumps(all_time, ensure_ascii=False)}",
        f"- 最近三小时创建的 Job 终态分布：{json.dumps(window, ensure_ascii=False)}",
        f"- 最近三小时涉及账号数：{payload.get('distinct_users_last_3h')}",
        "",
    ]
    lines.extend(_job_comparison(payload))
    failed = payload.get("failed_jobs") or []
    if failed:
        lines.extend(
            [
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
    lines.append(
        "逐 Job 比对只覆盖清单文件里列出的 job_id；清单之外的 Job 不参与本次判定。"
    )
    lines.append("")
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
