"""报告渲染自检：验证报告与逐 Job 比对能消费服务器的真实采集格式，
且不写入历史硬编码结论。

用法：python verify_report.py <server-side JSON> <job-db JSON>

它把两份真实服务器只读输出接到一份合成的运行摘要上渲染报告，不产生新的压测数据。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runner.evidence import _job_comparison  # noqa: E402
from runner.report import render_stage_report  # noqa: E402

# 报告里不应再出现的旧硬编码结论；出现即说明渲染路径退回了历史文本。
FORBIDDEN_MARKERS = (
    "SSE 峰值受活动 Job 数制约",
    "4 核 8 GB（腾讯云",
    "主库终态与客户端逐档汇总的创建数",
)


def _parse_time(value) -> float:
    """把采集脚本的 UTC 时间戳解析为浮点秒；无法解析时返回 0。"""
    if not value:
        return 0.0
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").timestamp()
    except ValueError:
        return 0.0


def build_summary(baseline: dict, after: dict) -> dict:
    """构造一份最小运行摘要，只用于验证渲染路径。"""
    return {
        "run_id": "verify-report",
        "stage": "verify",
        "users": 1,
        "peak_active_jobs": None,
        "peak_sse": None,
        "peak_sessions_per_user": 1,
        "peak_active_jobs_per_account": None,
        "started_at": _parse_time(baseline.get("collected_at")),
        "finished_at": _parse_time(after.get("collected_at")),
        "elapsed_seconds": 0.0,
        "http": {
            "rps": 0,
            "requests": 0,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "status_counts": {},
            "client_errors_4xx": 0,
            "server_errors_5xx": 0,
            "timeouts_or_transport_errors": 0,
            "by_endpoint": {},
        },
        "job": {
            "created": 1,
            "idempotent_replays": 0,
            "recorded": 1,
            "create_attempts": 1,
            "submissions_over_budget": 0,
            "submissions_session_busy": 0,
            "observation_timeouts": 0,
            "queue_observe_timeouts": 0,
            "completed": 1,
            "created_by_user": {"1": 1},
            "budget_used_max": 1,
            "terminal_status_counts": {"succeeded": 1},
            "queue_p50_seconds": None,
            "queue_p95_seconds": None,
            "execution_p50_seconds": None,
            "execution_p95_seconds": None,
            "total_p50_seconds": None,
            "total_p95_seconds": None,
            "queue_timeout_wait_lower_bound_min_seconds": None,
            "queue_timeout_wait_lower_bound_max_seconds": None,
            "history_checked": 0,
            "history_consistent": 0,
        },
        "sse": {
            "subscriptions": 1,
            "first_event_p50_ms": None,
            "first_event_p95_ms": None,
            "terminal_latency_p50_ms": None,
            "terminal_latency_p95_ms": None,
            "disconnects": 0,
            "reconnects": 0,
            "duplicate_events": 0,
            "out_of_order_events": 0,
            "verified_missing_events": 0,
            "id_gap_events_informational": 0,
            "samples": [],
        },
        "resource_curve": None,
        "server_baseline": baseline,
        "server_after": after,
        "notes": [],
    }


def main(server_path: str, job_db_path: str) -> int:
    baseline = json.loads(Path(server_path).read_text(encoding="utf-8"))
    job_db = json.loads(Path(job_db_path).read_text(encoding="utf-8"))
    after = dict(baseline)
    after["phase"] = "after"
    body = render_stage_report(build_summary(baseline, after))
    comparison = "\n".join(_job_comparison(job_db))
    print(body)
    print(comparison)
    if "逐 Job 比对" not in comparison:
        print("逐 Job 比对小节缺失")
        return 1
    problems = [marker for marker in FORBIDDEN_MARKERS if marker in body]
    if problems:
        print(f"发现历史硬编码结论: {problems}")
        return 1
    print("未发现历史硬编码结论，逐 Job 比对小节已生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
