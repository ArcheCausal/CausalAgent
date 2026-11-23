"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import container, data, runner, selector
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


def _parse_picks(raw: str, size: int) -> list[int]:
    parts = [item for item in re.split(r"[,\s]+", raw.strip()) if item]
    if not parts:
        raise ValueError("空输入")
    picks: list[int] = []
    for item in parts:
        if not item.isdigit():
            raise ValueError(f"非法编号：{item}")
        index = int(item) - 1
        if index < 0 or index >= size:
            raise ValueError(f"编号超出范围：{item}")
        if index in picks:
            raise ValueError(f"重复编号：{item}")
        picks.append(index)
    return picks


def _expand_list(items, values) -> list:
    if any(str(item).lower() == "all" for item in items):
        return list(values)
    return list(items)


def _expand_csv(text, values) -> list:
    items = [item.strip() for item in str(text or "").split(",") if item.strip()]
    return _expand_list(items, values)


def estimate_text(count: int, config) -> str:
    est = config.get("estimate") or {}
    seconds = float(est.get("seconds_per_case", 600))
    cost = float(est.get("cost_usd_per_case", 0.6))
    return (
        f"预计耗时约 {seconds * count / 60:.1f} 分钟，成本约 ${cost * count:.2f}"
        f"（{count} 集 × 单集 {seconds:.0f}s / ${cost:.2f}）"
    )


def _selection(config, args, available) -> dict:
    return {
        "mechanisms": _expand_csv(args.mechanisms, available["mechanisms"]),
        "dims": _expand_list(args.dims or [], available["dims"]),
        "ns": _expand_list(args.ns or [], available["ns"]),
        "per_cell": args.per_cell or int(config["selection"]["per_cell"]),
        "seed": args.seed if args.seed is not None else int(config["selection"]["seed"]),
    }


def _report_check(name, fn):
    try:
        detail = fn()
    except Exception as exc:
        print(f"FAIL {name}  {str(exc)[:200]}")
        return False
    print(f"OK   {name}  {detail or ''}".rstrip())
    return True


def cmd_setup(config, args) -> int:
    image = config["agent"]["image"]
    ok = _report_check("docker", lambda: container.docker(
        ["version", "--format", "{{.Server.Version}}"]
    ).stdout.strip())

    root = data.ensure_data(config)
    ok = _report_check("data", lambda: str(root)) and ok
    image_ok = _report_check("image", lambda: container.image_digest(image))
    ok = image_ok and ok
    if not image_ok:
        repo = data.toolbox_lock_path().parent.parent
        print(f"镜像缺失。构建命令：docker build -t {image} -f docker/Dockerfile .（在 {repo} 下）")
        if not args.build:
            return 1
        container.build_image(image, repo)
        ok = _report_check("image", lambda: container.image_digest(image)) and ok

    def baseline():
        path = config.get("baseline_results")
        if not path:
            return "未配置"
        if not Path(path).is_file():
            raise RuntimeError(f"文件不存在：{path}")
        return str(path)

    ok = _report_check("baseline", baseline) and ok

    specs = selector.scan(root)
    print(f"数据集：{len(specs)}")
    print(json.dumps(selector.available(specs), ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _ask_axis(label: str, values: list) -> list:
    print(f"{label}：")
    for index, value in enumerate(values, 1):
        print(f"  {index}) {value}")
    while True:
        raw = input("选择编号（逗号分隔，a=全选，回车中止）：").strip()
        if not raw:
            raise SystemExit(0)
        if raw.lower() == "a":
            return list(values)
        try:
            return [values[index] for index in _parse_picks(raw, len(values))]
        except ValueError as exc:
            print(f"输入无效：{exc}")


def _resolve_selection(config, args, specs):
    available = selector.available(specs)
    if not (args.mechanisms and args.dims and args.ns):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        if not args.mechanisms:
            picked = _ask_axis("机制", available["mechanisms"])
            args.mechanisms = ",".join(str(item) for item in picked)
        if not args.dims:
            args.dims = _ask_axis("维度 D", available["dims"])
        if not args.ns:
            args.ns = _ask_axis("样本量 N", available["ns"])
    return _selection(config, args, available)


def cmd_run(config, args) -> int:
    root = data.ensure_data(config)
    specs = selector.scan(root)
    selection = _resolve_selection(config, args, specs)
    if selection is None:
        print("必须显式指定 --mechanisms / --dims / --ns。可选值：", file=sys.stderr)
        print(json.dumps(selector.available(specs), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    chosen = _chosen(config, selection, args.limit)
    if not chosen:
        print("没有匹配的数据集，请检查选择条件", file=sys.stderr)
        return 2
    print(f"共 {len(chosen)} 集")
    for spec in chosen:
        print(" ", spec.dataset_id)
    if args.dry_run:
        return 0
    if not args.yes and sys.stdin.isatty() and sys.stdout.isatty():
        print(estimate_text(len(chosen), config))
        if input("确认开跑？[y/N] ").strip().lower() not in ("y", "yes"):
            print("已取消")
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

    setup_parser = sub.add_parser("setup", help="检查环境与数据集")
    setup_parser.add_argument("--build", action="store_true")
    setup_parser.set_defaults(func=cmd_setup)
    sub.add_parser("selftest", help="离线自检").set_defaults(func=None)
    sub.add_parser("doctor", help="检查 Docker 与镜像").set_defaults(func=cmd_doctor)

    run_parser = sub.add_parser("run", help="运行一批评测")
    run_parser.add_argument("--mechanisms", help="逗号分隔，如 Linear,CPT")
    run_parser.add_argument("--dims", type=int, nargs="+")
    run_parser.add_argument("--ns", type=int, nargs="+")
    run_parser.add_argument("--per-cell", type=int, default=0)
    run_parser.add_argument("--limit", type=int, default=0)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--seed", type=int, default=None)
    run_parser.add_argument("--yes", action="store_true")
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
