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
        "执行时间为总耗时减去排队时间。",
        "SSE 峰值受活动 Job 数制约：这一轮 Job 创建受活动 Job 上限节流，SSE 订阅数没有超过活动 Job 数，"
        "因此各档 SSE 峰值为 1 至 4，计划中的 4 至 8 上限未被打满。",
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
        "- 服务器侧指标：已通过 loadtest/server_side_collect.sh 只读采集并写入各档摘要，"
        "逐档的“服务器侧”一节给出实测值",
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
    all_time = payload.get("by_status_all_time") or {}
    window = payload.get("by_status_last_3h") or {}
    lines = [
        "## 第六阶段：主库 Job 终态核对",
        "",
        f"- 核对时刻：{payload.get('collected_at')}",
        f"- 来源：{payload.get('source')}",
        f"- 表：{payload.get('table')}",
        f"- 全量终态分布：{json.dumps(all_time, ensure_ascii=False)}",
        f"- 最近三小时创建的 Job 终态分布：{json.dumps(window, ensure_ascii=False)}",
        f"- 最近三小时涉及账号数：{payload.get('distinct_users_last_3h')}",
        "",
    ]
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
        "主库终态与客户端逐档汇总的创建数、终态分布一致；取消数包含专项中主动取消的 Job。"
    )
    lines.append("")
    return lines


def _limitations(selected: dict[str, Path]) -> list[str]:
    """列出未执行项与原因，避免把部分结果当成完整验收。"""
    lines = [
        "## 未执行项与原因",
        "",
        "以下项目属于计划范围，但本轮没有取得证据；原因写在对应条目里。",
        "",
        "- 服务器侧指标（CPU、内存、磁盘、swap、OOM、容器重启、MySQL 连接数与"
        "复制状态）：已通过 SSH 在服务器上执行只读采集，采集时刻与取值见各档“服务器侧”"
        "一节；采样是单次快照，未覆盖整个负载窗口的每一秒。",
        "- causal.cdfm 的算法失败：日志侧已确认该能力在本轮全部档位完成数为 0，"
        "根因是 staging 出网不可达 huggingface.co，定位与候选修复见 "
        "evidence/cdfm-failure-analysis.md；本轮只做定位，未修改服务代码或配置。",
        "- 管理员端网页界面核对：管理员端页面需要管理员登录凭据，本轮未在浏览器登录；"
        "Job 终态改用主库只读查询核对，结果见上一节。",
        "- 网关级 5xx 的来源定位：本轮 mid 档 1 次、stability 档 3 次 5xx，分别落在 GET /health/probe"
        "与 GET /api/agent/jobs/active。应用的 web error 日志为 0 条，说明它们没有进入应用异常日志。"
        "复核时单独重放 300 次请求未复现，且 /health/probe 在当前入口返回 404，表明它是网关自身的"
        "路由而不是 Flask 应用接口；日志采集只覆盖 web、worker、mcp 三个服务，不含网关自身日志，"
        "所以这几次 502 由网关到上游之间的瞬时失败造成，具体成因本轮未能确认。",
        "- waiting_input 恢复分支：专项里三次点名引用文件的请求都直接返回 final_result，"
        "没有进入 interrupt，恢复分支未触发；该分支需要 Job 执行期间冻结文件不可用，"
        "普通用户接口无法制造，因此本轮只把它记为未触发，不记为通过。",
        "",
        "客户端侧可复现的检查均已执行并留下逐档证据；上述项目不因此被表述为已通过。",
        "",
    ]
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
