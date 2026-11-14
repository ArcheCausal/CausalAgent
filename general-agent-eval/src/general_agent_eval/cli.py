"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import data, runner, selector
from .config import load_config


def _require_secret(config) -> None:
    name = config["agent"]["auth_env"]
    if not os.environ.get(name):
        raise SystemExit(f"缺少环境变量 {name}")


def _chosen(config, selection, limit=0):
    root = data.ensure_data(config)
    specs = selector.scan(root)
    chosen = selector.select(
        specs,
        selection["mechanisms"],
        selection["dims"],
        selection["ns"],
        selection["per_cell"],
        selection["seed"],
        tuple(config["data"].get("exclude") or ()),
    )
    return chosen[:limit] if limit else chosen


def _selection(config, args) -> dict:
    return {
        "mechanisms": [item for item in args.mechanisms.split(",") if item],
        "dims": list(args.dims),
        "ns": list(args.ns),
        "per_cell": args.per_cell or int(config["selection"]["per_cell"]),
        "seed": int(config["selection"]["seed"]),
    }


def cmd_setup(config, args) -> int:
    root = data.ensure_data(config)
    specs = selector.scan(root)
    print(f"数据目录：{root}")
    print(f"数据集：{len(specs)}")
    print(json.dumps(selector.available(specs), ensure_ascii=False, indent=2))
    return 0


def cmd_run(config, args) -> int:
    root = data.ensure_data(config)
    specs = selector.scan(root)
    if not (args.mechanisms and args.dims and args.ns):
        print("必须显式指定 --mechanisms / --dims / --ns。可选值：", file=sys.stderr)
        print(json.dumps(selector.available(specs), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    selection = _selection(config, args)
    chosen = _chosen(config, selection, args.limit)
    print(f"共 {len(chosen)} 集")
    for spec in chosen:
        print(" ", spec.dataset_id)
    if args.dry_run:
        return 0

    _require_secret(config)
    runner.execute(config, chosen, selection)
    return 0


def cmd_resume(config, args) -> int:
    run_dir = Path(args.run).expanduser().resolve()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    selection = meta["selection"]
    chosen = _chosen(config, selection)
    _require_secret(config)
    runner.execute(config, chosen, selection, resume_run=run_dir)
    return 0


def cmd_report(config, args) -> int:
    from .report import build

    build(Path(args.run).expanduser().resolve(), config.get("baseline_results"))
    return 0


def cmd_doctor(config, args) -> int:
    from .preflight import doctor

    return doctor(config)


def cmd_smoke(config, args) -> int:
    if not args.yes:
        print("smoke 会真实调用模型并产生费用；确认请加 --yes", file=sys.stderr)
        return 2
    root = data.ensure_data(config)
    available = selector.available(selector.scan(root))
    if not available["mechanisms"] or not available["dims"] or not available["ns"]:
        print("没有可用数据集", file=sys.stderr)
        return 2
    mechanism = "Linear" if "Linear" in available["mechanisms"] else available["mechanisms"][0]
    selection = {
        "mechanisms": [mechanism],
        "dims": [min(available["dims"])],
        "ns": [min(available["ns"])],
        "per_cell": 1,
        "seed": int(config["selection"]["seed"]),
    }
    chosen = _chosen(config, selection, 1)
    if not chosen:
        print("找不到可用于 smoke 的数据集", file=sys.stderr)
        return 2
    _require_secret(config)
    runner.execute(config, chosen, selection)
    return 0


def cmd_shell(config, args) -> int:
    os.execvp("docker", ["docker", "exec", "-it", args.container, "bash"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="general-agent-eval")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="准备/检查数据集").set_defaults(func=cmd_setup)
    sub.add_parser("selftest", help="离线自检").set_defaults(func=None)
    sub.add_parser("doctor", help="检查 Docker 与镜像").set_defaults(func=cmd_doctor)

    run_parser = sub.add_parser("run", help="运行一批评测")
    run_parser.add_argument("--mechanisms", help="逗号分隔，如 Linear,CPT")
    run_parser.add_argument("--dims", type=int, nargs="+")
    run_parser.add_argument("--ns", type=int, nargs="+")
    run_parser.add_argument("--per-cell", type=int, default=0)
    run_parser.add_argument("--limit", type=int, default=0)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    resume_parser = sub.add_parser("resume", help="续跑一次运行")
    resume_parser.add_argument("--run", required=True)
    resume_parser.set_defaults(func=cmd_resume)

    report_parser = sub.add_parser("report", help="从 case.json 重新生成报告")
    report_parser.add_argument("--run", required=True)
    report_parser.set_defaults(func=cmd_report)

    smoke_parser = sub.add_parser("smoke", help="端到端跑一集（付费）")
    smoke_parser.add_argument("--yes", action="store_true")
    smoke_parser.set_defaults(func=cmd_smoke)

    shell_parser = sub.add_parser("shell", help="进入保留的容器")
    shell_parser.add_argument("--container", required=True)
    shell_parser.set_defaults(func=cmd_shell)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "selftest":
        from .selftest import main as selftest

        return selftest()
    config = load_config(args.config)
    return args.func(config, args) or 0
