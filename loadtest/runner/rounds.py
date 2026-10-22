"""固定轮次与精确 run_id 的证据入口；只读取原始运行，派生报告另存。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from runner import config
from runner.manifest import collect_client_manifest

DISPOSITIONS = {"planned", "adopted", "failed", "interrupted", "invalidated"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(path: Path | str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "loadtest.round.v1":
        raise ValueError("轮次清单 schema 无效")
    seen: set[str] = set()
    for row in payload["runs"]:
        run_id = row["run_id"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id) or run_id in {".", ".."} or run_id in seen:
            raise ValueError("run_id 无效或重复")
        if row["disposition"] not in DISPOSITIONS:
            raise ValueError("运行采用状态无效")
        seen.add(run_id)
    return payload


def write_json(path: Path, payload: dict, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def declared_parameters(stage: str, run_id: str | None) -> dict:
    """执行器只接受清单中该 run_id 的预声明参数。"""
    path = os.environ.get("LOADTEST_ROUND_MANIFEST")
    if not path:
        return {}
    chosen = run_id or os.environ.get("LOADTEST_RUN_ID")
    rows = [row for row in read_manifest(path)["runs"]
            if row["run_id"] == chosen and row["stage"] == stage and row["disposition"] == "planned"]
    if len(rows) != 1:
        raise ValueError("必须选择清单中唯一的待执行 run_id")
    return deepcopy(rows[0].get("parameters") or {})


def apply_declared_spec(spec: dict, parameters: dict) -> dict:
    allowed = set(spec) | {"task_budget", "active_jobs", "sse", "create_budget",
                           "sessions_per_user", "arrival_workers", "arrival_pending"}
    result = {**spec, **{key: value for key, value in parameters.items() if key in allowed}}
    for key in ("users", "sse", "flows", "rows", "tasks", "task_budget",
                "sessions_per_user", "arrival_workers", "arrival_pending"):
        if key in result and (isinstance(result[key], bool) or not isinstance(result[key], int) or result[key] < 1):
            raise ValueError(f"invalid_stage_parameter_{key}")
    for key in ("active_jobs", "create_budget"):
        if result.get(key) is not None and (isinstance(result[key], bool) or not isinstance(result[key], int) or result[key] < 1):
            raise ValueError(f"invalid_stage_parameter_{key}")
    for key in ("duration", "arrival_rate"):
        if key in result and (isinstance(result[key], bool) or not isinstance(result[key], (int, float))
                              or not math.isfinite(result[key]) or result[key] <= 0):
            raise ValueError(f"invalid_stage_parameter_{key}")
    if "seed" in result and (isinstance(result["seed"], bool) or not isinstance(result["seed"], int)):
        raise ValueError("invalid_stage_parameter_seed")
    return result


def record_run(path: Path | str, stage: str, run_id: str, disposition: str, **extra) -> None:
    """串行编排的状态更新；保留采用历史并拒绝清单外的运行。"""
    if disposition not in DISPOSITIONS:
        raise ValueError("运行采用状态无效")
    target = Path(path)
    payload = read_manifest(target)
    matches = [row for row in payload["runs"] if row["run_id"] == run_id and row["stage"] == stage]
    if len(matches) != 1:
        raise ValueError("运行必须在提交前列入轮次清单")
    row = matches[0]
    row.setdefault("history", []).append({"at_utc": utc_now(), "from": row["disposition"], "to": disposition})
    row.update(extra)
    row["disposition"] = disposition
    row["updated_at_utc"] = utc_now()
    write_json(target, payload)


def selected_runs(runs_dir: Path, manifest_path: Path | str) -> dict[str, Path]:
    """兼容单档工具的精确选择；重复档位必须使用 LOADTEST_RUN_ID。"""
    manifest = read_manifest(manifest_path)
    selected: dict[str, Path] = {}
    explicit = os.environ.get("LOADTEST_RUN_ID")
    for row in manifest["runs"]:
        if row["disposition"] == "invalidated" or (explicit and row["run_id"] != explicit):
            continue
        path = Path(row.get("summary_path") or runs_dir / row["run_id"] / "summary.json")
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("run_id") != row["run_id"] or data.get("stage") != row["stage"]:
            raise ValueError("运行摘要与轮次清单身份不符")
        if row["stage"] in selected:
            raise ValueError("同档重复运行必须逐 run_id 渲染，禁止自动选最近一次")
        selected[row["stage"]] = path.parent
    return selected


def create_manifest(target: Path, stages: list[str], fingerprint: dict, *, round_id: str | None = None) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    payload = {"schema_version": "loadtest.round.v1", "round_id": round_id or stamp,
               "created_at_utc": utc_now(), "time_standard": "UTC",
               "display_timezone": "UTC", "fingerprint": fingerprint,
               "client": collect_client_manifest(), "runs": []}
    for index, stage in enumerate(stages):
        if stage not in config.STAGES and stage != "recovery":
            raise ValueError(f"未知档位 {stage}")
        payload["runs"].append({"stage": stage, "run_id": f"{stamp}-{index:02d}-{stage}",
                                "disposition": "planned", "parameters": dict(config.STAGES.get(stage, {}))})
    write_json(target, payload, exclusive=True)
    return payload


def add_repeat(path: Path, source_run_id: str, count: int) -> list[str]:
    """依据已完成的真实档位，在再次提交前追加同参数复测计划。"""
    if not 1 <= count <= 10:
        raise ValueError("复测次数必须为 1 至 10")
    manifest = read_manifest(path)
    source = next(row for row in manifest["runs"] if row["run_id"] == source_run_id)
    if source["disposition"] != "adopted":
        raise ValueError("边界复测必须引用已采用的真实运行")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    added = []
    for index in range(count):
        run_id = f"{stamp}-repeat-{index:02d}-{source['stage']}"
        manifest["runs"].append({"stage": source["stage"], "run_id": run_id,
            "disposition": "planned", "parameters": deepcopy(source["parameters"]),
            "purpose": "boundary_repeat", "repeat_of": source_run_id, "declared_at_utc": utc_now()})
        added.append(run_id)
    write_json(path, manifest)
    return added


def render_round(manifest_path: Path, output: Path) -> dict:
    """原始产物保持只读；报告数字直接来自同一轮的结构化派生结果。"""
    from runner.capacity import enrich_summary
    from verify_report import verify

    manifest = read_manifest(manifest_path)
    output = output.resolve()
    if "raw" in {part.lower() for part in output.parts} or output / "round-manifest.json" == manifest_path.resolve():
        raise ValueError("报告输出不能覆盖原始清单或写入 raw 归档")
    for row in manifest["runs"]:
        source = Path(row.get("summary_path") or config.RUNS_DIR / row["run_id"] / "summary.json").resolve()
        if source.exists() and (output == source.parent or output.is_relative_to(source.parent)):
            raise ValueError("报告输出不能位于原始运行目录")
    output.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": "loadtest.round.result.v1", "round_id": manifest["round_id"],
              "generated_at_utc": utc_now(), "manifest_sha256": file_hash(manifest_path),
              "renderer_client": collect_client_manifest(), "runs": []}
    lines = ["# 压测轮次报告", "", "文档职责：按固定轮次清单汇总真实运行及证据缺口。", "",
             "适用范围：本清单列出的运行；时间统一使用 UTC，历史时区未知时不推算。", "",
             f"轮次：`{manifest['round_id']}`；生成时间（UTC）：{result['generated_at_utc']}。", "",
             "| 档位 | run_id | 采用状态 | 原始摘要 SHA256 | 报告 |", "| --- | --- | --- | --- | --- |"]
    for row in manifest["runs"]:
        run_id = row["run_id"]
        entry = {"stage": row["stage"], "run_id": run_id, "disposition": row["disposition"],
                 "reason": row.get("reason"), "parameters": row.get("parameters")}
        source = Path(row.get("summary_path") or config.RUNS_DIR / run_id / "summary.json")
        entry["source"] = str(source.resolve())
        report_name = None
        if source.exists() and row["disposition"] != "invalidated":
            raw = json.loads(source.read_text(encoding="utf-8"))
            if raw.get("run_id") != run_id or raw.get("stage") != row["stage"]:
                raise ValueError(f"摘要身份不符 {run_id}")
            entry["source_sha256"] = file_hash(source)
            sample_path = source.parent / "stage-sample.json"
            if sample_path.exists():
                from runner.resource import summarize
                sample = json.loads(sample_path.read_text(encoding="utf-8"))
                if sample.get("run_id") != run_id:
                    raise ValueError("资源采样身份不符")
                sample["observation_ended_at"] = raw.get("observation_ended_at")
                raw["resource_curve"] = summarize(sample)
                entry["resource_source"] = str(sample_path.resolve())
                entry["resource_source_sha256"] = file_hash(sample_path)
            enriched = enrich_summary(raw)
            report_name = f"{run_id}-report.md"
            body, problems = verify(raw, raw.get("logcheck"), strict=False)
            (output / report_name).write_text(body, encoding="utf-8")
            write_json(output / f"{run_id}-derived.json", enriched)
            entry["report_check_problems"] = problems
            entry["report_check_status"] = "failed" if problems else "passed"
            entry["capacity"] = enriched.get("capacity")
            entry["scenario"] = enriched.get("scenario")
            entry["orchestration"] = enriched.get("orchestration")
            entry["measurements"] = run_measurements(enriched)
        else:
            entry["evidence_status"] = "invalidated" if row["disposition"] == "invalidated" else "missing"
        result["runs"].append(entry)
        lines.append(f"| {row['stage']} | {run_id} | {row['disposition']} | {entry.get('source_sha256') or '未采集'} | {report_name or '未生成'} |")
    lines.extend(["", "计划、失败、中断与作废运行均保留在清单。未采集项为未知。报告结构检查通过不代表性能达标。",
                  "CDFM 调用成功、调用覆盖和算法准确率分别判断；本轮不评估算法准确率。", ""])
    write_json(output / "round-result.json", result)
    (output / "round-report.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(output / "round-manifest.json", manifest)
    (output / "analysis-summary.md").write_text(render_analysis(result), encoding="utf-8")
    return result


def run_measurements(summary: dict) -> dict:
    from runner.metrics import percentile
    cap = summary.get("capacity") or {}
    samples = cap.get("samples") or []
    waits = [sample["user_wait_ms"] for sample in samples if sample.get("user_wait_ms") is not None]
    queues = [sample["initial_queue_ms"] for sample in samples if sample.get("initial_queue_ms") is not None]
    phases = summary.get("phases") or {}
    curve = summary.get("resource_curve") or {}
    return {"admission": cap.get("admission"), "db_success": cap.get("db_success"),
            "success_120s": (cap.get("completion_120s") or {}).get("success_rate"),
            "success_300s": (cap.get("completion_300s") or {}).get("success_rate"),
            "wait_completed_samples": len(waits), "wait_p50_ms": percentile(waits, .5),
            "wait_p95_ms": percentile(waits, .95), "wait_p99_ms": percentile(waits, .99),
            "unfinished_wait_bounds_ms": [sample.get("wait_lower_bound_ms") for sample in samples if sample.get("terminal_received_at") is None],
            "queue_p50_ms": percentile(queues, .5), "queue_p95_ms": percentile(queues, .95),
            "phases": phases, "queue_peak": curve.get("queued_max"),
            "resource_phases": curve.get("phases"), "drain": summary.get("drain"),
            "cdfm": cap.get("cdfm"), "scenario": summary.get("scenario")}


def render_analysis(result: dict) -> str:
    """摘要数字来自 round-result，不人工抄写，不预设业务性能门槛。"""
    def number(value, *, percent=False):
        return "未知" if value is None else f"{value * 100:.2f}%" if percent else f"{value:.3f}"
    lines = ["# 当前配置能力观测", "", "文档职责：从轮次结构化结果生成负载、吞吐和等待摘要。", "",
             "适用范围：本轮真实运行；仅描述已观测范围，不作性能达标判定。", "",
             "| 档位 / run_id | 唯一接纳 | 主库成功率 | 120s 成功率 | 300s 成功率 | 负载成功 Job/s | 排空成功 Job/s | 仅完成用户等待 p95 ms | 首次排队 p95 ms | 排队峰值 | 排空 ms |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    comparable: dict[str, list[dict]] = {}
    for row in result["runs"]:
        if row["disposition"] == "invalidated":
            continue
        stats = row.get("measurements") or {}
        phases = stats.get("phases") or {}
        load, drain = phases.get("load") or {}, phases.get("drain") or {}
        lines.append(f"| {row['stage']} / {row['run_id']} | {(stats.get('admission') or {}).get('accepted', '未知')} | "
                     f"{number((stats.get('db_success') or {}).get('rate'), percent=True)} | {number(stats.get('success_120s'), percent=True)} | "
                     f"{number(stats.get('success_300s'), percent=True)} | {number(load.get('server_success_throughput'))} | "
                     f"{number(drain.get('server_success_throughput'))} | {number(stats.get('wait_p95_ms'))} | "
                     f"{number(stats.get('queue_p95_ms'))} | {number(stats.get('queue_peak'))} | {number((stats.get('drain') or {}).get('drain_ms'))} |")
        if stats and row["disposition"] == "adopted" and row.get("parameters"):
            parameters = json.dumps(row["parameters"], sort_keys=True)
            comparable.setdefault(f"{row['stage']}:{parameters}", []).append(row)
    lines.extend(["", "等待分位仅包含客户端实际收到终态的样本。未完成等待下界、分阶段资源、队列与全部终态吞吐见 round-result.json。",
                  "未知项不会转换为零。取消专项单独计分；CDFM 准确率、算法 PID 终止、真实浏览器和未触发恢复分支均需独立证据。", "", "重复运行差异：", ""])
    repeated = False
    for group in comparable.values():
        if len(group) < 2:
            continue
        repeated = True
        for field in ("success_120s", "success_300s", "wait_p95_ms", "queue_p95_ms", "queue_peak"):
            values = [row["measurements"].get(field) for row in group]
            observed = [value for value in values if value is not None]
            spread = max(observed) - min(observed) if len(observed) == len(values) else None
            lines.append(f"- {group[0]['stage']}，{field}：样本 {values}；最大差值 {number(spread)}。")
    if not repeated:
        lines.append("本清单尚无参数一致的重复运行，重复性未验证。")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="固定轮次、精确运行与只读派生报告")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--manifest", type=Path, required=True)
    init.add_argument("--fingerprint", type=Path, required=True)
    init.add_argument("stages", nargs="+")
    render = commands.add_parser("render")
    render.add_argument("--manifest", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    repeat = commands.add_parser("repeat")
    repeat.add_argument("--manifest", type=Path, required=True)
    repeat.add_argument("--source-run-id", required=True)
    repeat.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    if args.command == "init":
        create_manifest(args.manifest, args.stages, json.loads(args.fingerprint.read_text(encoding="utf-8")))
    elif args.command == "repeat":
        print(json.dumps(add_repeat(args.manifest, args.source_run_id, args.count)))
    else:
        render_round(args.manifest, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
