"""命令行入口：预检、建账号、分级负载、专项与报告整理。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runner import config
from runner.accounts import ensure_accounts, load_accounts, verify_accounts
from runner.evidence import (
    attach_job_db_check,
    attach_server_baseline,
    build_evidence,
    export_job_id_lists,
    latest_runs,
)
from runner import resource
from runner.preflight import render_preflight, run_preflight
from runner.recovery import run_recovery_suite
from runner.report import render_stage_report
from runner.stages import make_run_id, run_stage, write_run_outputs
from runner.verify_packet import build_verify_packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runner", description="staging 压测客户端")
    parser.add_argument("--base-url", default=config.base_url())
    parser.add_argument(
        "--server-json",
        default="",
        help="服务器侧只读采集结果 JSON 路径；缺少时报告标注未采集",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight", help="第一阶段：网络与入口预检")

    accounts = sub.add_parser("accounts", help="第二阶段：创建测试账号")
    accounts.add_argument("--count", type=int, default=10)
    accounts.add_argument("--verify", action="store_true")

    stage = sub.add_parser("stage", help="第四阶段：执行一个负载档位")
    stage.add_argument("--stage", required=True, choices=sorted(config.STAGES))
    stage.add_argument("--users", type=int, default=0, help="覆盖档位的虚拟用户数")
    stage.add_argument("--duration", type=float, default=0.0, help="覆盖档位的持续时间")

    sub.add_parser("recovery", help="第五阶段：取消与 SSE 恢复专项")
    sub.add_parser("evidence", help="整理 local/runs/ 为归档到 local/evidence/ 的报告")
    attach = sub.add_parser(
        "attach-server",
        help="把服务器侧只读采集结果写入各档运行摘要，供报告引用",
    )
    attach.add_argument("--from", dest="server_from", required=True)
    sample = sub.add_parser(
        "attach-sample",
        help="把一次负载窗口的主机资源采样写入某个档位的运行摘要",
    )
    sample.add_argument("--stage", required=True)
    sample.add_argument("--from", dest="sample_from", required=True)
    after = sub.add_parser(
        "attach-after",
        help="把负载结束后的服务器只读采集结果写入各档运行摘要的收尾快照",
    )
    after.add_argument("--from", dest="after_from", required=True)
    export = sub.add_parser(
        "export-job-ids",
        help="把各档本轮创建的 job_id 导出为清单文件，供服务器上逐 Job 主库核对",
    )
    export.add_argument("--out", default="", help="输出目录；默认为 local/runs/job-ids")
    job_db = sub.add_parser(
        "attach-job-db",
        help="把 job_db_check.sh 的逐 Job 主库核对结果写入各档运行摘要",
    )
    job_db.add_argument("--from", dest="job_db_from", required=True)
    sub.add_parser("verify-packet", help="生成日志与管理员端核查定位清单")
    logs = sub.add_parser("logs", help="通过 Grafana/Loki 核对日志侧（需要可用凭据）")
    logs.add_argument("--stage", default="", help="限定到某档运行的时间窗口")
    check = sub.add_parser("logcheck", help="按档位核查服务端日志并产出证据")
    check.add_argument("--stage", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server_baseline = _load_server_json(args.server_json)
    if args.command == "preflight":
        return _cmd_preflight(args.base_url)
    if args.command == "accounts":
        return _cmd_accounts(args.base_url, args.count, args.verify)
    if args.command == "stage":
        return _cmd_stage(args, server_baseline)
    if args.command == "recovery":
        return _cmd_recovery(args.base_url, server_baseline)
    if args.command == "evidence":
        return _cmd_evidence()
    if args.command == "verify-packet":
        target = build_verify_packet(config.RUNS_DIR, config.EVIDENCE_DIR)
        print(f"已写入 {target}")
        return 0
    if args.command == "attach-after":
        return _cmd_attach_after(args.after_from)
    if args.command == "export-job-ids":
        return _cmd_export_job_ids(args.out)
    if args.command == "attach-job-db":
        return _cmd_attach_job_db(args.job_db_from)
    if args.command == "attach-server":
        return _cmd_attach_server(args.server_from)
    if args.command == "attach-sample":
        return _cmd_attach_sample(args.stage, args.sample_from)
    if args.command == "logs":
        from runner.logs import main as logs_main

        return logs_main(args.stage or None)
    if args.command == "logcheck":
        return _cmd_logcheck(args.base_url, args.stage)
    return 2


def _cmd_logcheck(base_url: str, stage: str) -> int:
    from runner.logs import logcheck

    result = logcheck(stage, base_url)
    target = config.EVIDENCE_DIR / f"logcheck-{stage}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(render_logcheck(result))
    print(f"日志核查结果已写入 {target}")
    return 0


def render_logcheck(result: dict) -> str:
    """把日志核查结果整理为可读文本。"""
    counts = result["counts"]
    lines = [
        f"# 日志核查 {result['stage']}",
        "",
        f"- 运行窗口（纳秒）：{result['window_ns']}",
        f"- 各服务日志条数：{json.dumps(counts, ensure_ascii=False)}",
        f"- error 级日志：{json.dumps({k: len(v) for k, v in result['errors'].items()}, ensure_ascii=False)}",
        "",
        "## 请求 ID 沿用",
        "",
        f"- 关联 job_id：{result['request_id_propagation'].get('job_id')}",
        f"- 入口 request_id：{result['request_id_propagation'].get('gateway_entry_request_ids')}",
        f"- worker request_id：{result['request_id_propagation'].get('worker_request_ids')}",
        f"- 沿用的 request_id：{result['request_id_propagation'].get('shared_request_ids')}",
        f"- 是否沿用：{result['request_id_propagation'].get('propagated')}",
        "",
        "## Job 生命周期回放",
        "",
        f"- job_id：{result['job_lifecycle'].get('job_id')}",
        f"- 事件数：{result['job_lifecycle'].get('event_count')}",
        "",
        "| 时间 | 服务 | 级别 | 事件码 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for event in result["job_lifecycle"].get("events", []):
        lines.append(
            f"| {event.get('timestamp')} | {event.get('service')} | "
            f"{event.get('level')} | {event.get('event_code')} | "
            f"{(event.get('message') or '')[:60]} |"
        )
    if result["errors"]:
        lines.extend(["", "## error 级日志样本", ""])
        for service, items in result["errors"].items():
            for item in items[:5]:
                lines.append(
                    f"- {service} {item.get('timestamp')} "
                    f"{item.get('event_code')} {item.get('message')}"
                )
    lines.extend(["", "## MCP 按能力统计", "",
                  "| 能力 | 收到 | 完成 | 失败 | 慢调用 |",
                  "| --- | --- | --- | --- | --- |"])
    for capability, stats in (result.get("capability") or {}).items():
        lines.append(
            f"| {capability} | {stats.get('received', 0)} | {stats.get('finished', 0)} | "
            f"{stats.get('failed', 0)} | {stats.get('slow', 0)} |"
        )
    lines.extend(["", "## 事件码分布", ""])
    for service, codes in (result.get("event_codes") or {}).items():
        lines.append(f"- {service}：{json.dumps(codes, ensure_ascii=False)}")
    web_failures = result.get("web_failures") or []
    if web_failures:
        lines.extend(["", "## web 请求失败", ""])
        for item in web_failures:
            lines.append(
                f"- {item.get('timestamp')} {item.get('method')} {item.get('endpoint')} "
                f"状态码 {item.get('status_code')} 原因 {item.get('reason_code')} "
                f"异常 {item.get('exception_type')} 栈顶 {item.get('stack_top')}"
            )
    degraded = result.get("worker_degraded") or []
    if degraded:
        lines.extend(["", "## worker 降级与告警", ""])
        for item in degraded:
            lines.append(
                f"- {item.get('timestamp')} {item.get('event_code')} "
                f"{json.dumps(item.get('details'), ensure_ascii=False)}"
            )
    history = result.get("mcp_failure_history") or {}
    if history:
        lines.extend(
            [
                "",
                "## MCP 最近七天成功与失败",
                "",
                f"- 完成（按能力）：{json.dumps(history.get('finished_by_capability', {}), ensure_ascii=False)}",
                f"- 失败（按能力）：{json.dumps(history.get('failed_by_capability', {}), ensure_ascii=False)}",
                f"- CDFM 权重加载次数：{history.get('cdfm_model_load_attempts')}",
            ]
        )
    return "\n".join(lines) + "\n"


def _cmd_attach_after(path: str) -> int:
    """把负载结束后的服务器采集写入各档运行摘要的 server_after。"""
    payload = _load_server_json(path)
    if not payload:
        print(f"无法读取服务器采集文件：{path}")
        return 1
    summary = dict(payload)
    summary.pop("collected_at", None)
    updated: list[str] = []
    for stage, run in sorted(latest_runs(config.RUNS_DIR).items()):
        target = run / "summary.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        merged = dict(payload)
        merged["drain_seconds"] = (data.get("server_after") or {}).get("drain_seconds")
        data["server_after"] = merged
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        updated.append(f"{stage}:{run.name}")
    for item in updated:
        print(f"已写入停止后快照: {item}")
    print(f"共更新 {len(updated)} 个档位运行摘要")
    return 0


def _cmd_attach_sample(stage: str, path: str) -> int:
    """把一次资源采样写入指定档位的运行摘要。"""
    sample = resource.load_sample(Path(path))
    if sample is None:
        print(f"无法读取资源采样文件：{path}")
        return 1
    summary = resource.summarize(sample)
    target = latest_runs(config.RUNS_DIR).get(stage)
    if target is None:
        print(f"找不到 {stage} 的运行记录")
        return 1
    summary_file = target / "summary.json"
    payload = json.loads(summary_file.read_text(encoding="utf-8"))
    payload["resource_curve"] = summary
    summary_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已写入 {stage} 的资源曲线: {target.name}")
    for line in resource.render(summary):
        print(line)
    return 0


def _cmd_attach_server(path: str) -> int:
    """把服务器侧采集 JSON 写入各档运行摘要。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("服务器侧采集文件必须是 JSON 对象。")
        return 1
    updated = attach_server_baseline(config.RUNS_DIR, payload)
    for item in updated:
        print(f"已写入服务器侧基线: {item}")
    print(f"共更新 {len(updated)} 个档位运行摘要")
    return 0


def _cmd_evidence() -> int:
    combined, written = build_evidence(config.RUNS_DIR, config.EVIDENCE_DIR)
    for path in written:
        print(f"已写入 {path}")
    print(f"合并报告: {combined}")
    return 0


def _cmd_export_job_ids(out: str) -> int:
    """导出各档本轮 job_id 清单，供服务器上的逐 Job 主库核对使用。"""
    target_dir = Path(out) if out else config.RUNS_DIR / "job-ids"
    written = export_job_id_lists(config.RUNS_DIR, target_dir)
    total = 0
    for path in written:
        count = len([line for line in path.read_text(encoding="utf-8").splitlines() if line])
        total += count
        print(f"已写入 {path}（{count} 个 job_id）")
    print(f"共导出 {total} 个 job_id 到 {target_dir}")
    print(
        "下一步：把清单上传到服务器后执行 "
        "sudo bash loadtest/job_db_check.sh <run_id> <job_ids.txt>"
    )
    return 0


def _cmd_attach_job_db(path: str) -> int:
    """把逐 Job 主库核对结果写入各档运行摘要。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("主库核对文件必须是 JSON 对象。")
        return 1
    updated = attach_job_db_check(config.RUNS_DIR, payload)
    for item in updated:
        print(f"已写入逐 Job 主库核对: {item}")
    print(f"共更新 {len(updated)} 个档位运行摘要")
    return 0


def _cmd_preflight(base_url: str) -> int:
    result = run_preflight(base_url)
    print(render_preflight(result))
    target = config.RUNS_DIR / f"{make_run_id('preflight')}-preflight.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"预检结果已写入 {target}")
    return 0


def _cmd_accounts(base_url: str, count: int, verify: bool) -> int:
    accounts, notes = ensure_accounts(base_url, count)
    for note in notes:
        print(note)
    print(f"本地凭据文件: {config.ACCOUNTS_PATH}（共 {len(accounts)} 个账号）")
    if verify:
        problems = verify_accounts(base_url, accounts[:count])
        if problems:
            print("登录验证失败: " + ", ".join(problems))
            return 1
        print(f"登录验证通过 {min(count, len(accounts))} 个账号")
    return 0


def _cmd_stage(args, server_baseline: dict | None) -> int:
    spec = dict(config.STAGES[args.stage])
    if args.users:
        spec["users"] = args.users
    if args.duration:
        spec["duration"] = args.duration
        spec["mode"] = "mixed"
    config.STAGES[args.stage] = spec
    accounts = load_accounts()
    if len(accounts) < spec["users"]:
        print(
            f"账号不足：需要 {spec['users']} 个，本地只有 {len(accounts)} 个。"
            "先运行 accounts 命令。"
        )
        return 1
    summary = run_stage(args.stage, accounts, args.base_url, server_baseline)
    run_dir = config.RUNS_DIR / summary["run_id"]
    write_run_outputs(summary, run_dir)
    print(render_stage_report(summary))
    print(f"运行产物目录: {run_dir}")
    return 0


def _cmd_recovery(base_url: str, server_baseline: dict | None) -> int:
    accounts = load_accounts()
    if len(accounts) < 2:
        print("专项检查至少需要 2 个测试账号，先运行 accounts 命令。")
        return 1
    summary = run_recovery_suite(base_url, accounts, server_baseline)
    run_dir = config.RUNS_DIR / summary["run_id"]
    write_run_outputs(summary, run_dir)
    print(render_stage_report(summary))
    print(f"运行产物目录: {run_dir}")
    return 0


def _load_server_json(path: str) -> dict | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


if __name__ == "__main__":
    sys.exit(main())
