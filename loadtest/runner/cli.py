"""命令行入口：预检、建账号、分级负载、专项与报告整理。"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from runner import config
from runner.accounts import ensure_accounts, load_accounts, verify_accounts
from runner.evidence import (
    attach_job_db_check,
    attach_server_baseline,
    build_evidence,
    export_job_id_lists,
    latest_runs,
    select_run,
)
from runner import resource
from runner.preflight import render_preflight, run_preflight
from runner.recovery import run_recovery_suite
from runner.report import _value, render_stage_report
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
    parser.add_argument(
        "--selected-run-id",
        default="",
        help="证据附加、日志与报告使用的精确 run_id；缺省时取最近一个有运行的日期目录",
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
    stage.add_argument("--rows", type=int, help="CDFM 固定数据行数")
    stage.add_argument("--seed", type=int, help="CDFM 数据随机种子；相同规模和种子生成相同输入")
    stage.add_argument("--tasks", type=int, help="CDFM 本档任务总量")
    stage.add_argument("--arrival-rate", type=float, help="仅 cdfm_rate：整档每秒计划到达请求数")
    stage.add_argument(
        "--run-id",
        default="",
        help="固定本次运行的 run_id；编排脚本在负载前采集服务器快照时需要预先确定",
    )

    recovery = sub.add_parser("recovery", help="第五阶段：取消与 SSE 恢复专项")
    recovery.add_argument(
        "--run-id",
        default="",
        help="固定本次运行的 run_id；编排脚本在负载前采集服务器快照时需要预先确定",
    )
    sub.add_parser(
        "evidence",
        help="整理 local/runs/<date>/ 为归档到 local/evidence/<date>/ 的报告",
    )
    attach = sub.add_parser(
        "attach-server",
        help="把服务器侧只读采集结果写入各档运行摘要，供报告引用",
    )
    attach.add_argument("--from", dest="server_from", required=True)
    attach.add_argument("--stage", default="", help="只写入该档位；缺省时写入全部已有档位")
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
    after.add_argument("--stage", default="", help="只写入该档位；缺省时写入全部已有档位")
    export = sub.add_parser(
        "export-job-ids",
        help="把各档本轮创建的 job_id 导出为清单文件，供服务器上逐 Job 主库核对",
    )
    export.add_argument("--out", default="", help="输出目录；默认为 local/runs/<date>/job-ids")
    migrate = sub.add_parser(
        "migrate-artifacts",
        help="把旧版平铺的运行产物移动到按日期分组的目录；默认只预览",
    )
    migrate.add_argument(
        "--apply", action="store_true", help="实际执行移动；缺省时只输出计划"
    )
    job_db = sub.add_parser(
        "attach-job-db",
        help="把 job_db_check.sh 的逐 Job 主库核对结果写入各档运行摘要",
    )
    job_db.add_argument("--from", dest="job_db_from", required=True)
    job_db.add_argument("--stage", default="", help="只写入该档位；缺省时写入全部已有档位")
    sub.add_parser("verify-packet", help="生成日志与管理员端核查定位清单")
    logs = sub.add_parser("logs", help="通过 Grafana/Loki 核对日志侧（需要可用凭据）")
    logs.add_argument("--stage", default="", help="限定到某档运行的时间窗口")
    check = sub.add_parser("logcheck", help="按档位核查服务端日志并产出证据")
    check.add_argument("--stage", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selected_run_id:
        os.environ["LOADTEST_RUN_ID"] = args.selected_run_id
    server_baseline = _load_server_json(args.server_json)
    if args.command == "preflight":
        return _cmd_preflight(args.base_url)
    if args.command == "accounts":
        return _cmd_accounts(args.base_url, args.count, args.verify)
    if args.command == "stage":
        return _cmd_stage(args, server_baseline)
    if args.command == "recovery":
        return _cmd_recovery(args.base_url, server_baseline, args.run_id or None)
    if args.command == "evidence":
        return _cmd_evidence()
    if args.command == "verify-packet":
        runs_dir, evidence_dir = _command_date_dirs(args.selected_run_id)
        target = build_verify_packet(runs_dir, evidence_dir)
        print(f"已写入 {target}")
        return 0
    if args.command == "attach-after":
        return _cmd_attach_after(args.after_from, args.stage, args.selected_run_id)
    if args.command == "export-job-ids":
        return _cmd_export_job_ids(args.out)
    if args.command == "migrate-artifacts":
        return _cmd_migrate_artifacts(args.apply)
    if args.command == "attach-job-db":
        return _cmd_attach_job_db(args.job_db_from, args.stage, args.selected_run_id)
    if args.command == "attach-server":
        return _cmd_attach_server(args.server_from, args.stage, args.selected_run_id)
    if args.command == "attach-sample":
        return _cmd_attach_sample(args.stage, args.sample_from, args.selected_run_id)
    if args.command == "logs":
        from runner.logs import main as logs_main

        return logs_main(args.stage or None)
    if args.command == "logcheck":
        return _cmd_logcheck(args.base_url, args.stage)
    return 2


def _command_date_dirs(run_id: str | None) -> tuple[Path, Path]:
    """为附加、日志与报告命令确定日期目录。

    给出 run_id 时按该运行所在日期取目录；否则使用最近一个有运行的日期目录，
    没有历史运行时使用当天日期。实际写入的摘要仍按 run_id 核对。
    """
    if run_id:
        _run_dir, day_dir = select_run(run_id)
        return day_dir, config.EVIDENCE_DIR / day_dir.name
    day = config.latest_run_date()
    if day is None:
        return config.run_artifacts_dir(time.time())
    return config.RUNS_DIR / day, config.EVIDENCE_DIR / day


def _run_output_dir(summary: dict) -> Path:
    """返回一次运行的产物目录，优先使用摘要登记的日期目录。"""
    if summary.get("date_dir"):
        return config.RUNS_DIR / summary["date_dir"] / summary["run_id"]
    runs_dir, _evidence_dir = config.run_dir_for(
        summary["run_id"], summary.get("started_at")
    )
    return runs_dir / summary["run_id"]


def _cmd_logcheck(base_url: str, stage: str) -> int:
    from runner.logs import logcheck

    result = logcheck(stage, base_url)
    _runs_dir, evidence_dir = _command_date_dirs(result["run_id"])
    target = evidence_dir / f"logcheck-{stage}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(render_logcheck(result))
    print(f"日志核查结果已写入 {target}")
    return 0


def render_logcheck(result: dict) -> str:
    """把日志核查结果整理为可读文本。"""
    if result.get("scope") != "run" or not result.get("run_id"):
        return "本轮日志侧指标：未采集。旧日志缺少 run_id 或 scope=run，不提供本轮 MCP 错误或成功数量。\n"
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
                    f"{item.get('event_code')} "
                    f"{json.dumps(item.get('details') or {}, ensure_ascii=False)} "
                    f"异常 {item.get('exception_type')} 栈顶 {item.get('stack_top')}"
                )
    lines.extend(["", "## MCP 按能力统计", "",
                  "| 能力 | 收到 | 完成 | 失败 | 慢调用 |",
                  "| --- | --- | --- | --- | --- |"])
    for capability, stats in (result.get("capability") or {}).items():
        lines.append(
            f"| {capability} | {_value(stats.get('received'))} | {_value(stats.get('finished'))} | "
            f"{_value(stats.get('failed'))} | {_value(stats.get('slow'))} |"
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
    lines.extend(["", "## 日志取证范围", "",
                  f"- run_id：{result.get('run_id') or '未采集'}；scope：{result.get('scope') or '未采集'}",
                  f"- 采集状态：{result.get('status') or '未采集'}",
                  f"- 逐 Job/服务覆盖：{json.dumps(result.get('coverage'), ensure_ascii=False)}",
                  f"- CDFM 证据状态：{(result.get('cdfm') or {}).get('status') or '未采集'}",
                  "旧日志产物若未声明本轮 Job 范围，不用于计算本轮成功率。"])
    return "\n".join(lines) + "\n"


def _cmd_attach_after(path: str, stage: str = "", run_id: str | None = None) -> int:
    """把负载结束后的服务器采集写入档位运行摘要的 server_after。

    传入 stage 时只写该档，否则写入全部已有档位。
    """
    payload = _load_server_json(path)
    if not payload:
        print(f"无法读取服务器采集文件：{path}")
        return 1
    summary = dict(payload)
    summary.pop("collected_at", None)
    updated: list[str] = []
    runs_dir, _evidence_dir = _command_date_dirs(run_id)
    for stage_name, run in sorted(latest_runs(runs_dir).items()):
        if stage and stage_name != stage:
            continue
        target = run / "summary.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        merged = dict(payload)
        merged["drain_seconds"] = (data.get("server_after") or {}).get("drain_seconds")
        data["server_after"] = merged
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        updated.append(f"{stage_name}:{run.name}")
    for item in updated:
        print(f"已写入停止后快照: {item}")
    print(f"共更新 {len(updated)} 个档位运行摘要")
    return 0


def _cmd_attach_sample(stage: str, path: str, run_id: str | None = None) -> int:
    """把一次资源采样写入指定档位的运行摘要。"""
    sample = resource.load_sample(Path(path))
    if sample is None:
        print(f"无法读取资源采样文件：{path}")
        return 1
    summary = resource.summarize(sample)
    runs_dir, _evidence_dir = _command_date_dirs(run_id)
    target = latest_runs(runs_dir).get(stage)
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


def _cmd_attach_server(path: str, stage: str = "", run_id: str | None = None) -> int:
    """把服务器侧采集 JSON 写入档位运行摘要。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("服务器侧采集文件必须是 JSON 对象。")
        return 1
    runs_dir, _evidence_dir = _command_date_dirs(run_id)
    updated = attach_server_baseline(runs_dir, payload, stage)
    for item in updated:
        print(f"已写入服务器侧基线: {item}")
    print(f"共更新 {len(updated)} 个档位运行摘要")
    return 0


def _cmd_evidence() -> int:
    runs_dir, evidence_dir = _command_date_dirs(os.environ.get("LOADTEST_RUN_ID"))
    combined, written = build_evidence(runs_dir, evidence_dir)
    for path in written:
        print(f"已写入 {path}")
    print(f"合并报告: {combined}")
    return 0


def _cmd_export_job_ids(out: str) -> int:
    """导出各档本轮 job_id 清单，供服务器上的逐 Job 主库核对使用。

    输出默认落在所用的日期目录；同一 run_id 的请求身份与 job_id 不会被别的日期
    覆盖。
    """
    runs_dir, _evidence_dir = _command_date_dirs(os.environ.get("LOADTEST_RUN_ID"))
    target_dir = Path(out) if out else runs_dir / "job-ids"
    written = export_job_id_lists(runs_dir, target_dir)
    total = 0
    for path in written:
        count = len([line for line in path.read_text(encoding="utf-8").splitlines() if line])
        total += count
        print(f"已写入 {path}（{count} 个 job_id）")
        request_manifest = path.with_name(path.name.replace("job-ids-", "requests-")).with_suffix(".json")
        if request_manifest.exists():
            print(f"已写入含未知接纳请求的身份清单：{request_manifest}")
    print(f"共导出 {total} 个 job_id 到 {target_dir}")
    print(
        "下一步：把清单上传到服务器后执行 "
        "sudo bash loadtest/job_db_check.sh <run_id> <requests-stage.json>；"
        "同时部署 loadtest/ops/server_evidence.py。旧 job_ids.txt 仅核对已知 Job。"
    )
    return 0


def _cmd_migrate_artifacts(apply: bool) -> int:
    """把旧版平铺运行目录移动到日期目录；默认只打印计划。"""
    from runner.artifacts import apply_migration, plan_migration

    moves = plan_migration()
    if not moves:
        print("没有需要移动的旧版运行目录。")
        return 0
    for source, target in moves:
        print(f"{source} -> {target}")
    if not apply:
        print(f"共 {len(moves)} 项待移动；确认后加 --apply 执行。")
        return 0
    for line in apply_migration(moves):
        print(line)
    return 0


def _cmd_attach_job_db(path: str, stage: str = "", run_id: str | None = None) -> int:
    """把逐 Job 主库核对结果写入档位运行摘要。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("主库核对文件必须是 JSON 对象。")
        return 1
    runs_dir, _evidence_dir = _command_date_dirs(run_id)
    updated = attach_job_db_check(runs_dir, payload, stage)
    for item in updated:
        print(f"已写入逐 Job 主库核对: {item}")
    print(f"共更新 {len(updated)} 个档位运行摘要")
    return 0


def _cmd_preflight(base_url: str) -> int:
    result = run_preflight(base_url)
    print(render_preflight(result))
    runs_dir, _evidence_dir = config.run_artifacts_dir(time.time())
    target = runs_dir / f"{make_run_id('preflight')}-preflight.json"
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
    cdfm = spec["mode"] in ("cdfm_fixed", "cdfm_rate")
    sandbox = str(spec["mode"]).startswith("sandbox") or spec["mode"] == "mixed"
    if args.users < 0 or not math.isfinite(args.duration) or args.duration < 0:
        print("users 必须为正整数，duration 必须为有限正数；0 表示使用档位默认值。")
        return 2
    for name in ("rows", "tasks"):
        value = getattr(args, name)
        if value is not None and ((not cdfm and not sandbox) or value <= 0):
            print(f"--{name} 只用于 CDFM 与沙盒档位，且必须为正整数。")
            return 2
        if value is not None:
            spec[name] = value
    if args.seed is not None:
        if not cdfm and not sandbox:
            print("--seed 只用于 CDFM 与沙盒档位。")
            return 2
        spec["seed"] = args.seed
    if args.arrival_rate is not None:
        if spec["mode"] not in {"cdfm_rate", "sandbox_rate"} or not math.isfinite(args.arrival_rate) or args.arrival_rate <= 0:
            print("--arrival-rate 只用于 cdfm_rate 与 sandbox_rate，且必须为有限正数。")
            return 2
        spec["arrival_rate"] = args.arrival_rate
    if args.users:
        spec["users"] = args.users
    if args.duration:
        if spec["mode"] == "cdfm_fixed":
            print("cdfm_fixed 以 --tasks 指定任务量；持续时间请使用 cdfm_rate 的 --duration。")
            return 2
        spec["duration"] = args.duration
        # 只有原本按持续时间运行的 mixed 档位才切换模式；沙盒与 full_flow 档保持各自流程。
        if not cdfm and spec["mode"] == "mixed":
            spec["mode"] = "mixed"
    config.STAGES[args.stage] = spec
    accounts = load_accounts()
    if len(accounts) < spec["users"]:
        print(
            f"账号不足：需要 {spec['users']} 个，本地只有 {len(accounts)} 个。"
            "先运行 accounts 命令。"
        )
        return 1
    summary = run_stage(
        args.stage, accounts, args.base_url, server_baseline, run_id=args.run_id or None
    )
    run_dir = _run_output_dir(summary)
    write_run_outputs(summary, run_dir)
    print(render_stage_report(summary))
    print(f"运行产物目录: {run_dir}")
    if summary.get("db_checked_at") is None:
        print("主库核对待完成：请按本轮 run_id 导出 job_id，采集后 attach-job-db，再生成最终报告。")
    return 0


def _cmd_recovery(
    base_url: str, server_baseline: dict | None, run_id: str | None = None
) -> int:
    accounts = load_accounts()
    if len(accounts) < 2:
        print("专项检查至少需要 2 个测试账号，先运行 accounts 命令。")
        return 1
    summary = run_recovery_suite(base_url, accounts, server_baseline, run_id=run_id)
    run_dir = _run_output_dir(summary)
    write_run_outputs(summary, run_dir)
    print(render_stage_report(summary))
    print(f"运行产物目录: {run_dir}")
    if any(item.get("passed") is False for item in summary.get("checks", [])):
        return 1
    if summary.get("special_evidence_gaps") or any(item.get("passed") is None for item in summary.get("checks", [])):
        print("专项尚未完成：执行释放、进程终止或真实前端等证据缺口见报告（退出码 3）。")
        return 3
    return 0


def _load_server_json(path: str) -> dict | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


if __name__ == "__main__":
    sys.exit(main())
