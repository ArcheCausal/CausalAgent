"""只用真实运行摘要和真实服务器/主库产物检查报告，不构造运行样本。

用法：python verify_report.py --run-summary local/runs/<run_id>/summary.json
可选：--server-json <before.json> --after-json <after.json> --job-db-json <job-db.json>
日志：--logcheck-json <logcheck.json>，必须属于同 run_id 且 scope=run。
原来的两个位置参数仍可表示 server/db，但必须同时提供 --run-summary。
"""

from __future__ import annotations

import argparse
import json
import hashlib
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runner.capacity import enrich_summary, epoch  # noqa: E402
from runner.report import render_stage_report  # noqa: E402
from runner import resource  # noqa: E402

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


class ReportVerificationError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def verify(summary: dict, logcheck: dict | None = None, *, strict: bool = True) -> tuple[str, list[str]]:
    """返回真实产物的报告及结构问题；没有证据时报告未采集，不把缺失算通过。"""
    original = deepcopy(summary)
    original_log = deepcopy(logcheck)
    enriched = enrich_summary(summary, logcheck)
    body = render_stage_report(summary, logcheck)
    problems = [f"发现历史硬编码结论：{marker}" for marker in FORBIDDEN_MARKERS if marker in body]
    if summary != original:
        problems.append("报告渲染修改了输入摘要")
    if logcheck != original_log:
        problems.append("报告渲染修改了输入日志证据")
    for field in ("requests", "job", "sse", "http", "job_db_check", "resource_curve", "multi_session_evidence"):
        if enriched.get(field) != original.get(field):
            problems.append(f"统计改写了原始字段 {field}")
    for marker in ("容量口径 v2", "计时与分阶段吞吐", "仅完成样本 p95",
                   "预声明持续观察", "deadline 终态率与成功率", "取消专项分组",
                   "CDFM 调用成功率", "CDFM 证据覆盖率", "CDFM 算法准确率"):
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
    if (capacity.get("truncated_sse") or {}).get("missing_events") is not None:
        problems.append("主动截断流不能统计为完整流漏事件")
    samples = capacity.get("samples") or []
    drain = enriched.get("drain") or {}
    if (drain.get("execution_released") is True or drain.get("server_drained_at_check") is True
            or drain.get("finally_drained") is True):
        if not samples or not all(sample.get("db_checked") and sample.get("execution_released") is True for sample in samples):
            problems.append("执行释放或排空通过但没有全部 Job 的严格释放证据")
    for sample in samples:
        raw_times = {epoch(row.get("terminal_received_at")) for row in summary.get("requests") or []
                     if isinstance(row, dict) and str(row.get("job_id")) == sample["job_id"]}
        at = sample.get("terminal_received_at")
        if at is not None and at not in raw_times:
            problems.append("派生 SSE 终态时间不来自原客户端记录")
        if at is None and sample.get("user_wait_ms") is not None:
            problems.append("缺少客户端终态时间却产生完整用户等待时延")
    observation = capacity.get("observation") or {}
    for field, key in (("requires_terminal", "continuous_denominator"),
                       ("requires_full_stream", "full_stream_denominator")):
        declared = [row for row in samples if row.get(field) is True]
        known = any(row.get(field) is not None for row in samples)
        expected = len(declared) if known else None
        if observation.get(key) != expected:
            problems.append(f"{key} 未按预声明策略计算")
    for key, data in capacity.items():
        if not isinstance(data, dict) or "terminal_rate" not in data:
            continue
        total, terminal, succeeded = (data.get(name) for name in ("denominator", "terminal", "succeeded"))
        if total is not None:
            for count in (terminal, succeeded, data.get("unknown"), data.get("success_unknown")):
                if count is not None and not 0 <= count <= total:
                    problems.append(f"{key} 数量超出分母")
        if terminal is not None and succeeded is not None and succeeded > terminal:
            problems.append(f"{key} 成功数大于终态数")
    cdfm = capacity.get("cdfm") or {}
    if cdfm.get("accuracy") is not None:
        problems.append("没有真实基准评价契约，CDFM 准确率应为未知")
    if any(cdfm.get(key) for key in ("missing_worker_identity_records", "missing_retry_identity_records", "unidentified_records")):
        if any(cdfm.get(key) is not None for key in ("actual_attempts", "retries", "final_success_rate")):
            problems.append("CDFM 身份不完整却给出完整尝试数或最终成功率")
    records = cdfm.get("records")
    if records is not None:
        identities = {(row["job_id"], row["invocation_id"]) for row in records}
        if len(identities) != len(records) or sum((cdfm.get("outcomes") or {}).values()) != len(records):
            problems.append("CDFM 逻辑调用身份或终态分布分母不一致")
        for row in records:
            if row["identity_status"] == "unknown" and row["final_outcome"] is not None:
                problems.append("CDFM attempt 身份未知却确认最终结果")
    for data in (cdfm.get("source_reconciliation") or {}).values():
        if isinstance(data, dict) and data.get("status") == "unknown" and any(data.get(key) is not None for key in ("intersection", "events_only", "logs_only")):
            problems.append("CDFM 来源采集未知却输出确定身份差异")
    if not summary.get("multi_session_evidence") and (capacity.get("multi_session") or {}).get("status") != "unverified":
        problems.append("缺少 multi_session_evidence 却判定多会话已验证")
    for pair in (capacity.get("multi_session") or {}).get("pairs") or []:
        if pair.get("core_status") == "verified" and pair.get("execution_overlap") is False and pair.get("status") != "unverified":
            problems.append("执行重叠未触发却判定为产品并发约束失败")
    business = capacity.get("business_results") or {}
    denominator = (business.get("total") or {}).get("denominator")
    if denominator is not None and sum((business.get(key) or {}).get("denominator", 0) for key in ("ordinary", "cancellation", "unknown")) != denominator:
        problems.append("业务互斥分组的分母之和不等于全部接纳")
    for phase in (capacity.get("phases") or {}).values():
        if not phase:
            continue
        if phase.get("server_completions") is not None and phase.get("server_end_source") is None:
            problems.append("服务器吞吐没有独立结束边界依据")
        if phase.get("server_finished_timestamp_unknown") and phase.get("server_completions") is not None:
            problems.append("服务器终态缺少时间戳却输出阶段完成数")
        if phase.get("client_terminal_timestamp_unknown") and phase.get("client_completions") is not None:
            problems.append("客户端终态缺少时间戳却输出阶段完成数")
    submission = capacity.get("submission") or {}
    for data in [submission.get("summary")] + list((submission.get("groups") or {}).values()):
        if data is not None and data["actual_sent"] + data["not_sent"] + data["send_unknown"] != data["intents"]:
            problems.append("发送/未发送/未知数量之和不等于意图数")
    for index, row in enumerate((summary.get("http") or {}).get("samples") or []):
        if row.get("run_id") and row["run_id"] != summary.get("run_id"):
            problems.append(f"http.samples[{index}] 属于不同 run_id")
    if strict and problems:
        raise ReportVerificationError(problems)
    return body, problems


def _protect_output(target: Path, inputs: list[Path]) -> None:
    target = target.resolve()
    if "raw" in {part.lower() for part in target.parts}:
        raise ValueError("报告输出不能位于 raw 归档目录")
    for source in inputs:
        source = source.resolve()
        if target == source or target.is_relative_to(source.parent) and source.name in {"summary.json", "client-summary.json"}:
            raise ValueError("报告输出不能覆盖输入或写入原始运行目录")


def verify_round(manifest_path: Path, output: Path) -> dict:
    """安全的轮次渲染入口：逐档自动验证，并核对原始输入字节未变。"""
    from runner.rounds import render_round
    manifest_path, output = manifest_path.resolve(), output.resolve()
    manifest = _load(str(manifest_path))
    inputs = [manifest_path]
    for row in manifest["runs"]:
        if not row.get("summary_path"):
            raise ValueError("验证轮次需要声明每个 summary_path")
        source = Path(row["summary_path"]).resolve()
        inputs.extend([source, source.parent / "stage-sample.json"])
    _protect_output(output / "round-manifest.json", inputs)
    for source in inputs:
        _protect_output(output, [source])
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs if path.is_file()}
    try:
        result = render_round(manifest_path, output)
    finally:
        changed = [str(path) for path, digest in before.items() if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest]
        if changed:
            raise ValueError("轮次渲染修改了原始输入文件")
    problems = [f"{row['run_id']}: {problem}" for row in result["runs"]
                for problem in row.get("report_check_problems", [])]
    if problems:
        raise ReportVerificationError(problems)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_server", nargs="?", help="兼容位置参数：真实服务器采集 JSON")
    parser.add_argument("legacy_job_db", nargs="?", help="兼容位置参数：真实主库核对 JSON")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-summary", help="真实运行的 summary.json")
    source.add_argument("--round-manifest", help="固定轮次清单；与 --output 目录一起使用")
    parser.add_argument("--server-json", help="同 run_id 的真实负载前采集")
    parser.add_argument("--after-json", help="同 run_id 的真实观察结束后采集")
    parser.add_argument("--job-db-json", help="同 run_id 的真实逐 Job 主库核对")
    parser.add_argument("--logcheck-json", help="同 run_id、scope=run 的真实日志核查")
    parser.add_argument("--resource-json", help="同 run_id 的真实服务器原始资源采样")
    parser.add_argument("--output", help="可选报告输出路径，推荐 local/evidence/ 下")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.round_manifest:
            if not args.output:
                raise ValueError("轮次验证需要独立输出目录")
            result = verify_round(Path(args.round_manifest), Path(args.output))
            print(f"轮次逐档报告验证通过：{len(result['runs'])} 个清单条目；缺失和作废证据仍单列。")
            return 0
        if args.output:
            _protect_output(Path(args.output), [Path(value) for value in
                (args.run_summary, args.server_json, args.after_json, args.job_db_json,
                 args.logcheck_json, args.resource_json, args.legacy_server, args.legacy_job_db) if value])
        summary = _load(args.run_summary)
        if not summary.get("run_id") or not summary.get("stage") or "started_at" not in summary:
            raise ValueError("必须提供包含 run_id、stage、started_at 的真实运行摘要")
        _attach(summary, args.server_json or args.legacy_server, "server_baseline")
        _attach(summary, args.after_json, "server_after")
        _attach(summary, args.job_db_json or args.legacy_job_db, "job_db_check")
        if args.resource_json:
            _attach(summary, args.resource_json, "resource_curve")
            summary["resource_curve"] = resource.summarize(summary["resource_curve"])
        for field in ("server_baseline", "server_after", "job_db_check"):
            attached = summary.get(field)
            if isinstance(attached, dict) and attached.get("run_id") and attached["run_id"] != summary["run_id"]:
                raise ValueError(f"摘要中 {field} 属于不同 run_id")
        logcheck = _load(args.logcheck_json) if args.logcheck_json else None
        if logcheck is not None and (logcheck.get("scope") != "run" or logcheck.get("run_id") != summary["run_id"]):
            raise ValueError("日志核查必须声明相同 run_id 和 scope=run")
        body, problems = verify(summary, logcheck)
    except ReportVerificationError as exc:
        for problem in exc.problems:
            print(f"报告结构问题：{problem}", file=sys.stderr)
        return 1
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
