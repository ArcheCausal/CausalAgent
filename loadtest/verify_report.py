"""只用真实运行摘要和真实服务器/主库产物检查报告，不构造运行样本。

用法：python verify_report.py --run-summary local/runs/<run_id>/summary.json
可选：--server-json <before.json> --after-json <after.json> --job-db-json <job-db.json>
日志：--logcheck-json <logcheck.json>，必须属于同 run_id 且 scope=run。
原来的两个位置参数仍可表示 server/db，但必须同时提供 --run-summary。
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runner.capacity import enrich_summary  # noqa: E402
from runner.report import render_stage_report  # noqa: E402

FORBIDDEN_MARKERS = (
    "SSE 峰值受活动 Job 数制约",
    "4 核 8 GB（腾讯云",
    "主库终态与客户端逐档汇总的创建数",
)


def _load(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"产物必须是 JSON 对象：{path}")
    return payload


def _attach(summary: dict, path: str | None, field: str) -> None:
    if not path:
        return
    payload = _load(path)
    if not payload.get("run_id") or payload["run_id"] != summary["run_id"]:
        raise ValueError(f"{field} 的 run_id 缺失或与运行摘要不同，不能跨轮合并证据")
    summary[field] = payload


def verify(summary: dict, logcheck: dict | None = None) -> tuple[str, list[str]]:
    """返回真实产物的报告及结构问题；没有证据时报告未采集，不把缺失算通过。"""
    original = deepcopy(summary)
    original_log = deepcopy(logcheck)
    enriched = enrich_summary(summary)
    body = render_stage_report(summary, logcheck)
    problems = [f"发现历史硬编码结论：{marker}" for marker in FORBIDDEN_MARKERS if marker in body]
    if summary != original:
        problems.append("报告渲染修改了输入摘要")
    if logcheck != original_log:
        problems.append("报告渲染修改了输入日志证据")
    for marker in ("容量口径 v2", "计时与分阶段吞吐", "仅完成样本 p95"):
        if marker not in body:
            problems.append(f"报告缺少 {marker}")
    capacity = enriched.get("capacity") or {}
    if not isinstance(summary.get("requests"), list):
        if (capacity.get("admission") or {}).get("denominator") is not None:
            problems.append("旧产物缺少 requests，接纳分母应为空")
        if (capacity.get("client_terminal") or {}).get("coverage") is not None:
            problems.append("旧产物没有客户端时间点，实时终态覆盖率应为空")
    if summary.get("schema_version") == "loadtest.run.v2":
        for field in ("requests", "stop_submitting_at", "observation_ended_at", "db_checked_at"):
            if field not in summary:
                problems.append(f"v2 摘要缺少字段 {field}")
        request_fields = ("job_id", "admission", "created_request_at", "terminal_received_at",
                          "user_wait_ms", "wait_lower_bound_ms", "history_check_ms", "received_event_ids", "group")
        for index, row in enumerate(summary.get("requests") or []):
            if not isinstance(row, dict):
                problems.append(f"requests[{index}] 不是字典")
                continue
            absent = [field for field in request_fields if field not in row]
            if absent:
                problems.append(f"requests[{index}] 缺少字段 {','.join(absent)}")
        ids = {str(value) for value in summary.get("job", {}).get("created_ids", [])}
        samples = summary.get("job", {}).get("samples")
        if not isinstance(samples, list):
            problems.append("v2 job.samples 不是全量数组")
        elif ids - {str(row.get("job_id")) for row in samples}:
            problems.append("v2 job.samples 未覆盖全部 created_ids")
    integrity = capacity.get("sse_integrity") or {}
    if not integrity.get("checked") and integrity.get("missing_events") is not None:
        problems.append("没有事件集核对，missing_events 应为空")
    return body, problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_server", nargs="?", help="兼容位置参数：真实服务器采集 JSON")
    parser.add_argument("legacy_job_db", nargs="?", help="兼容位置参数：真实主库核对 JSON")
    parser.add_argument("--run-summary", required=True, help="必须提供真实运行的 summary.json")
    parser.add_argument("--server-json", help="同 run_id 的真实负载前采集")
    parser.add_argument("--after-json", help="同 run_id 的真实观察结束后采集")
    parser.add_argument("--job-db-json", help="同 run_id 的真实逐 Job 主库核对")
    parser.add_argument("--logcheck-json", help="同 run_id、scope=run 的真实日志核查")
    parser.add_argument("--output", help="可选报告输出路径，推荐 local/evidence/ 下")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = _load(args.run_summary)
        if not summary.get("run_id") or not summary.get("stage") or "started_at" not in summary:
            raise ValueError("必须提供包含 run_id、stage、started_at 的真实运行摘要")
        _attach(summary, args.server_json or args.legacy_server, "server_baseline")
        _attach(summary, args.after_json, "server_after")
        _attach(summary, args.job_db_json or args.legacy_job_db, "job_db_check")
        for field in ("server_baseline", "server_after", "job_db_check"):
            attached = summary.get(field)
            if isinstance(attached, dict) and attached.get("run_id") and attached["run_id"] != summary["run_id"]:
                raise ValueError(f"摘要中 {field} 属于不同 run_id")
        logcheck = _load(args.logcheck_json) if args.logcheck_json else None
        if logcheck is not None and (logcheck.get("scope") != "run" or logcheck.get("run_id") != summary["run_id"]):
            raise ValueError("日志核查必须声明相同 run_id 和 scope=run")
        body, problems = verify(summary, logcheck)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # 不回显 JSON 解析器可能携带的原始文件正文。
        print(f"无法验证真实产物：{type(exc).__name__}", file=sys.stderr)
        return 2
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        print(f"报告已写入 {target}")
    else:
        print(body)
    for problem in problems:
        print(f"报告结构问题：{problem}", file=sys.stderr)
    print(f"真实产物报告结构检查：{'未通过' if problems else '通过'}。该结果不代表被测系统、CDFM 或专项验收通过。")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
