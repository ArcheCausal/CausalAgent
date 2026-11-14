"""编排：准备 case → 跑容器 → 判状态 → 评分 → 落盘。"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import audit, container, data, metrics, parse, trace

RESULT_HEADER = [
    "run_id", "dataset", "mechanism", "dim", "n_rows", "n_gold_edges", "n_pred_edges",
    "precision", "recall", "f1", "shd", "skeleton_precision", "skeleton_recall",
    "final_algorithm", "status", "reason", "duration_s",
    "tokens_input", "tokens_output", "num_turns", "total_cost_usd",
    "attempt", "exit_code", "oom_killed", "container",
]

ALGORITHM_PATTERNS = {
    "PC": ("pc(", "constraintbased.pc", "fisherz", "pcorr"),
    "DirectLiNGAM": ("directlingam", "lingam"),
    "GES": ("scorebased.ges", "ges("),
    "ANM": ("anm(", "fcmbased.anm", "resit"),
    "FCI": ("fci(",),
    "CAM": ("camuv", "causal additive"),
    "NOTEARS": ("notears",),
    "BIC子集回归": ("best_subset", "bic"),
    "pwling/DMI": ("dmi(", "pwling"),
}

SCORED = ("success", "timeout_partial")
# 有结论的状态：resume 时跳过
FINAL = {"success", "timeout_partial", "invalid_leak", "invalid_answer", "docker_oom"}


def prompt_for(dim: int) -> str:
    return (
        f"请读取上传的 CSV 数据文件，对变量 X0 到 X{dim - 1} 执行因果分析\n\n"
        f"数据文件是当前工作目录下的 data.csv。请先规划任务步骤（快速检查变量类型与分布/线性性"
        f"→ 选择合适的因果发现算法 → 执行 → 输出），再按计划逐步执行，控制总耗时。"
        f"保持分析轻量：直接使用现成的因果发现算法（如 PC、DirectLiNGAM、ANM 等），"
        f"不要做不必要的可视化、异常值深挖或重复稳健性检查。"
        f"每完成一种算法的评估，立即把当前最优的邻接矩阵覆盖写入 answer/adjacency.csv，"
        f"后续再改进就覆盖更新，不要等到最后才落盘。"
        f"请在 10 分钟内完成任务，不要访问上级目录或系统路径。\n\n"
        f"完成后，将最终因果结构保存为 answer/adjacency.csv：\n"
        f"- {dim}×{dim} 的 0/1 邻接矩阵，第一行表头 X0..X{dim - 1}，其余每行以 X0..X{dim - 1} 开头\n"
        f"- adjacency[i][j]=1 表示 Xi → Xj，对角线为 0\n"
        f"并在 answer/algorithm.txt 写一行你最终采用的算法名称（如 DirectLiNGAM）"
    )


def _atomic_json(path: Path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _redact(info: dict) -> dict:
    clone = json.loads(json.dumps(info or {}))
    config = clone.setdefault("Config", {})
    env = []
    for item in config.get("Env") or []:
        key = str(item).split("=", 1)[0]
        if any(word in key.upper() for word in ("TOKEN", "KEY", "SECRET", "PASSWORD")):
            env.append(f"{key}=<redacted>")
        else:
            env.append(item)
    config["Env"] = env
    return clone


def _usage(events) -> dict:
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    usage = result.get("usage") or {}
    return {
        "tokens_input": usage.get("input_tokens", ""),
        "tokens_output": usage.get("output_tokens", ""),
        "num_turns": result.get("num_turns", ""),
        "total_cost_usd": result.get("total_cost_usd", ""),
    }


def _final_algorithm(case_dir: Path, events) -> str:
    choice = case_dir / "answer" / "algorithm.txt"
    if choice.exists():
        lines = choice.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if lines:
            return lines[0].strip()[:50]
    text = "\n".join(
        json.dumps(block.get("input") or {}, ensure_ascii=False)
        for event in events
        for block in (event.get("message") or {}).get("content") or ()
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ).lower()
    found = sorted(name for name, keys in ALGORITHM_PATTERNS.items() if any(k in text for k in keys))
    return ";".join(found)


def _classify(result, matrix, hits, parse_error, oom) -> tuple[str, str]:
    # 先判基础设施，再判业务：容器没起来时不该去解析答案
    if hits:
        return "invalid_leak", hits[0]["token"]
    if result.exit_code in (125, 126, 127):
        return "docker_error", f"exit {result.exit_code}"
    if oom:
        return "docker_oom", "OOMKilled"
    if matrix is not None:
        if result.timed_out:
            return "timeout_partial", ""
        if result.exit_code not in (0, None):
            return "claude_error", f"exit {result.exit_code}"
        return "success", ""
    if result.timed_out:
        return "timeout", ""
    if result.exit_code in (137, 143):
        return "docker_error", f"exit {result.exit_code}"
    if result.exit_code not in (0, None):
        return "claude_error", f"exit {result.exit_code}"
    return "invalid_answer", parse_error or "invalid"


def _prepare_case(case_dir: Path, X) -> None:
    shutil.rmtree(case_dir, ignore_errors=True)
    (case_dir / "answer").mkdir(parents=True)
    (case_dir / ".harness" / "home").mkdir(parents=True)
    data.write_csv(X, case_dir / "data.csv")


def _execute_case(config, spec, case_dir: Path, run_dir: Path, run_id: str, case_no: int) -> dict:
    X, adjacency = data.load(spec)
    gold = data.gold_edges(adjacency)
    dim = int(X.shape[1])

    _prepare_case(case_dir, X)
    container.probe_mount(config, case_dir)

    name = f"gae-{run_id}-{case_no:02d}"
    container.remove_container(name)
    log_path = run_dir / "raw" / f"{data.artifact(spec)}.jsonl"

    attempts = int(config["agent"]["retries"]) + 1
    result = None
    matrix = None
    events: list[dict] = []
    hits: list[dict] = []
    status = reason = ""
    attempt = 0

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            shutil.rmtree(case_dir / "answer", ignore_errors=True)
            (case_dir / "answer").mkdir()
        result = container.run_container(
            config, case_dir, prompt_for(dim), name, log_path, run_id, f"case-{case_no:02d}",
        )
        events = trace.load_events(log_path)
        hits = audit.scan(events)
        matrix, parse_error = parse.parse_adjacency(case_dir / "answer" / "adjacency.csv", dim)
        oom = bool((result.inspect.get("State") or {}).get("OOMKilled"))
        status, reason = _classify(result, matrix, hits, parse_error, oom)
        # 只重试"秒退"的 CLI/基础设施失败；超时不重试，10 分钟一次太贵
        if status not in ("docker_error", "claude_error") or result.duration >= 30:
            break

    oom = bool((result.inspect.get("State") or {}).get("OOMKilled"))
    scored = status in SCORED
    values: dict = {}
    if scored:
        edges = [(i, j, "directed") for i in range(dim) for j in range(dim) if matrix[i, j] == 1]
        raw = metrics.score(edges, gold)
        values = {
            "n_pred_edges": raw["n_pred_edges"],
            "precision": raw["precision"],
            "recall": raw["recall"],
            "f1": metrics.f1_score(raw["precision"], raw["recall"]),
            "shd": metrics.shd(edges, gold),
            "skeleton_precision": raw["skeleton_precision"],
            "skeleton_recall": raw["skeleton_recall"],
        }

    payload = {
        "dataset": spec.dataset_id,
        "mechanism": spec.mechanism,
        "dim": dim,
        "n": spec.n,
        "n_rows": int(X.shape[0]),
        "n_gold_edges": len(gold),
        "case": f"case-{case_no:02d}",
        "status": status,
        "reason": reason,
        "attempt": attempt,
        "duration_s": result.duration,
        "container": {
            "name": name,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "oom_killed": oom,
        },
        "evidence": {
            "pre": (case_dir / ".harness" / "pre.json").exists(),
            "post": (case_dir / ".harness" / "post.json").exists(),
        },
        "final_algorithm": _final_algorithm(case_dir, events),
        "metrics": values,
        "usage": _usage(events),
        "dataset_path": str(spec.path),
    }

    _atomic_json(case_dir / "case.json", payload)
    _atomic_json(run_dir / "raw" / f"{data.artifact(spec)}.inspect.json", _redact(result.inspect))
    trace.append_csv(
        run_dir / "trace.csv",
        trace.tool_rows(events, f"case-{case_no:02d}", spec.dataset_id),
    )

    keep = bool(config["docker"].get("keep_container_on_failure")) and not scored
    if not keep:
        container.remove_container(name)
    return payload


def _row(run_id: str, payload: dict) -> dict:
    m = payload.get("metrics") or {}
    usage = payload.get("usage") or {}
    c = payload.get("container") or {}
    return {
        "run_id": run_id,
        "dataset": payload["dataset"],
        "mechanism": payload["mechanism"],
        "dim": payload["dim"],
        "n_rows": payload["n_rows"],
        "n_gold_edges": payload["n_gold_edges"],
        "n_pred_edges": m.get("n_pred_edges", ""),
        "precision": m.get("precision", ""),
        "recall": m.get("recall", ""),
        "f1": m.get("f1", ""),
        "shd": m.get("shd", ""),
        "skeleton_precision": m.get("skeleton_precision", ""),
        "skeleton_recall": m.get("skeleton_recall", ""),
        "final_algorithm": payload.get("final_algorithm", ""),
        "status": payload["status"],
        "reason": payload.get("reason", ""),
        "duration_s": payload.get("duration_s", ""),
        "tokens_input": usage.get("tokens_input", ""),
        "tokens_output": usage.get("tokens_output", ""),
        "num_turns": usage.get("num_turns", ""),
        "total_cost_usd": usage.get("total_cost_usd", ""),
        "attempt": payload.get("attempt", ""),
        "exit_code": c.get("exit_code", ""),
        "oom_killed": c.get("oom_killed", ""),
        "container": c.get("name", ""),
    }


def _host_info() -> dict:
    try:
        uname = os.uname()
        return {
            "platform": f"{uname.sysname} {uname.release}",
            "machine": uname.machine,
            "uid": getattr(os, "getuid", lambda: None)(),
        }
    except AttributeError:
        return {"platform": os.name}


def _load_done(run_dir: Path) -> dict[str, str]:
    done: dict[str, str] = {}
    for path in sorted((run_dir / "cases").glob("*/case.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") in FINAL:
            done[payload["dataset"]] = path.parent.name
    return done


def _rebuild_results(run_dir: Path, run_id: str) -> None:
    # results.csv 只是投影：结束时从 case.json 全量重写，避免 resume 后出现重复行/半截行
    rows = []
    for case in sorted((run_dir / "cases").glob("*/case.json")):
        try:
            rows.append(_row(run_id, json.loads(case.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    with (run_dir / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _finish(run_dir: Path) -> None:
    path = run_dir / "run.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()

    versions, statuses = set(), {}
    for case in sorted((run_dir / "cases").glob("*/case.json")):
        try:
            payload = json.loads(case.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        statuses[payload.get("status", "unknown")] = statuses.get(payload.get("status", "unknown"), 0) + 1
        pre = case.parent / ".harness" / "pre.json"
        if pre.exists():
            try:
                versions.add(json.loads(pre.read_text(encoding="utf-8")).get("claude", ""))
            except (OSError, json.JSONDecodeError):
                pass
    meta["claude_versions"] = sorted(v for v in versions if v)
    meta["status_counts"] = statuses
    _atomic_json(path, meta)


def execute(config, specs, selection, resume_run=None) -> Path:
    root = data.ensure_data(config)

    if resume_run:
        run_dir = Path(resume_run)
        run_id = run_dir.name
        done = _load_done(run_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = Path(config["output_dir"]) / "runs" / run_id
        (run_dir / "raw").mkdir(parents=True, exist_ok=False)
        (run_dir / "cases").mkdir()
        meta = {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "runtime": "docker",
            "image": config["agent"]["image"],
            "image_digest": container.image_digest(config["agent"]["image"]),
            "model": config["agent"]["model"],
            "base_url": config["agent"]["base_url"],
            "timeout_s": config["agent"]["timeout_s"],
            "selection": selection,
            "data": {
                "name": config["data"]["name"],
                "local_dir": str(config["data"].get("local_dir") or ""),
                "fingerprint": data.fingerprint(root),
            },
            "host": _host_info(),
        }
        _atomic_json(run_dir / "run.json", meta)
        done = {}

    results_path = run_dir / "results.csv"
    new_file = not results_path.exists()
    with results_path.open("a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_HEADER)
        if new_file:
            writer.writeheader()
        try:
            for index, spec in enumerate(specs, 1):
                if spec.dataset_id in done:
                    print(f"skip     {spec.dataset_id}")
                    continue
                payload = _execute_case(
                    config, spec, run_dir / "cases" / f"case-{index:02d}", run_dir, run_id, index,
                )
                writer.writerow(_row(run_id, payload))
                handle.flush()
                m = payload.get("metrics") or {}
                print(
                    f"{payload['status']:15s} {spec.dataset_id} "
                    f"f1={m.get('f1', '-')} shd={m.get('shd', '-')} {payload.get('reason', '')}"
                )
        except KeyboardInterrupt:
            print("\n中断：按标签清理本次运行的容器…", file=sys.stderr)
            container.kill_run(run_id, "TERM")
            time.sleep(3)
            container.kill_run(run_id, "KILL")
            raise SystemExit(130)

    _rebuild_results(run_dir, run_id)
    _finish(run_dir)
    print(f"运行目录：{run_dir}")
    return run_dir
