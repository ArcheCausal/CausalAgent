"""生成日志侧与管理员端核查所需的定位清单。

计划第一、六阶段要求能用 request_id 和 job_id 在一次测试里定位请求与 Job。
客户端无法代替管理员端和服务器日志，但可以把每次运行的时间窗口、Job 标识
和 Request ID 样本整理出来，交给有服务器访问的人核查。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from runner import config
from runner.evidence import STAGE_ORDER, latest_runs


def build_verify_packet(runs_dir: Path, evidence_dir: Path) -> Path:
    """把各档运行的定位信息写入 evidence/log-verification.md。"""
    selected = latest_runs(runs_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 日志与管理员端核查定位清单",
        "",
        "文档职责：给出每档运行的标识与时间窗口，供有服务器访问的人按 job_id、"
        "request_id 在管理员端和日志系统中定位同一次测试。",
        "",
        "适用范围：只用于核查 staging 服务端行为；本清单不包含服务器侧指标。",
        "",
        "## 预检 Request ID 样本",
        "",
    ]
    lines.extend(_preflight_samples(runs_dir))
    lines.extend(
        [
            "",
            "## 各档运行定位信息",
            "",
            "| 档位 | run_id | 时间窗口 | 创建 Job | 活动 Job 峰值 | SSE 断线 | 示例 job_id |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    job_lines: list[str] = []
    for stage in STAGE_ORDER:
        path = selected.get(stage)
        if path is None:
            continue
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        job_ids = _job_ids(summary)
        sample = ", ".join(job_ids[:3]) or "无"
        lines.append(
            f"| {stage} | {summary.get('run_id')} | {_window(summary)} | "
            f"{summary.get('job', {}).get('created', 0)} | "
            f"{_value(summary.get('peak_active_jobs'))} | "
            f"{summary.get('sse', {}).get('disconnects', 0)} | {sample} |"
        )
        if job_ids:
            job_lines.append(
                f"- **{stage}**（{summary.get('run_id')}）：" + ", ".join(job_ids)
            )
    lines.extend(["", "## 按 job_id 核查", "", "~~~text"])
    lines.extend(job_lines or ["无"])
    lines.extend(
        [
            "~~~",
            "",
            "## 核查步骤",
            "",
            "1. 在管理员端按上表 job_id 打开任务详情，确认状态、事件和 checkpoint 摘要。",
            "2. 在日志系统按同一 job_id 检索 worker、causal-mcp 与 app 的关联记录。",
            "3. 用上表 Request ID 样本核对网关与应用两端是否沿用同一个 ID。",
            "4. 把服务器侧 CPU、内存、磁盘、swap、OOM、容器重启、数据库连接数与"
            "复制延迟按时间窗口填入报告，并在报告中保留同一 run_id。",
            "",
        ]
    )
    target = evidence_dir / "log-verification.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _preflight_samples(runs_dir: Path) -> list[str]:
    """列出最近一次预检里每个路径的 Request ID。"""
    candidates = sorted(runs_dir.glob("*preflight.json"))
    if not candidates:
        return ["本次未执行入口预检。"]
    payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
    lines = [f"- 预检入口：{payload.get('base_url')}"]
    for path, detail in payload.get("http", {}).items():
        lines.append(
            f"- {path}：状态码 {detail.get('status')}，"
            f"Request ID {detail.get('request_id')}"
        )
    return lines


def _job_ids(summary: dict) -> list[str]:
    """从 SSE 样本中提取本次运行创建过的 job_id。"""
    ids = [
        str(sample.get("job_id"))
        for sample in summary.get("sse", {}).get("samples", [])
        if sample.get("job_id")
    ]
    return list(dict.fromkeys(ids))


def _window(summary: dict) -> str:
    started = summary.get("started_at")
    finished = summary.get("finished_at")
    if not started or not finished:
        return "未记录"
    return (
        f"{datetime.fromtimestamp(started).strftime('%Y-%m-%d %H:%M:%S')} ~ "
        f"{datetime.fromtimestamp(finished).strftime('%H:%M:%S')}"
    )


def _value(value) -> str:
    return "未采集" if value is None else str(value)


def main() -> int:
    target = build_verify_packet(config.RUNS_DIR, config.EVIDENCE_DIR)
    print(f"已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
