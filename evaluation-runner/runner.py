"""评测编排入口。"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import dataset as ds
import evaluate as ev
import report as rp
from answer import answer_for
from client import AgentClient, ClientError, TransportError
from config import (AuthSettings, Config, ConfigError, DataSettings, OutputSettings,
                    RunSettings, load_config)

RETRYABLE = {"timeout", "terminal_error", "no_causal_graph"}
ACCOUNTS_PATH = Path("local/accounts.local.json")


class StepError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate_selection(specs, mechanisms, dims, ns) -> None:
    unknown = sorted(set(mechanisms or ()) - {spec.mechanism for spec in specs})
    if unknown:
        raise ConfigError("数据目录中不存在的机制: " + ",".join(unknown))
    bad_dims = sorted(set(dims) - {spec.dim_nominal for spec in specs})
    if bad_dims:
        raise ConfigError("数据目录中不存在的 D: " + ",".join(map(str, bad_dims)))
    bad_ns = sorted(set(ns) - {spec.n_nominal for spec in specs})
    if bad_ns:
        raise ConfigError("数据目录中不存在的 N: " + ",".join(map(str, bad_ns)))


def resolve_credentials(config: Config) -> tuple[str, str]:
    if config.auth.username:
        return config.auth.username, config.auth.password
    if ACCOUNTS_PATH.exists():
        saved = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
        return saved["username"], saved["password"]
    credentials = {"username": f"eval-{uuid.uuid4().hex[:8]}", "password": uuid.uuid4().hex}
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_PATH.write_text(json.dumps(credentials), encoding="utf-8")
    return credentials["username"], credentials["password"]


USAGE_FIELDS = ("tokens_input", "tokens_output", "tokens_total")
_EVENT_USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")

RESULT_HEADER = [
    "run_id", "dataset", "mechanism", "rep", "n_rows", "n_cols", "dim", "lambda",
    "n_gold_edges", "n_pred_edges", "n_undirected_edges", "recall", "precision", "f1",
    "skeleton_recall", "skeleton_precision", "shd", "auroc", "auprc",
    "final_algorithm", "final_confidence", "final_selection_rationale",
    "finalization_status", "interactions", "duration_s",
    "tokens_input", "tokens_output", "tokens_total", "status", "failure_reason",
]


def build_prompt(dim: int) -> str:
    return f"请读取上传的 CSV 数据文件，对变量 X0 到 X{dim - 1} 执行因果分析"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def usage_totals(records) -> dict[str, int]:
    """按 attempt 取累计最大值再求和；终态快照不重复计入。"""
    per_attempt: dict[object, tuple[int, int, int]] = {}
    for record in records:
        if record.get("type") != "usage":
            continue
        payload = record.get("payload") or {}
        values = tuple(payload.get(name) for name in _EVENT_USAGE_FIELDS)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            continue
        key = payload.get("attempt")
        previous = per_attempt.get(key, (0, 0, 0))
        per_attempt[key] = tuple(
            max(old, new) for old, new in zip(previous, values)
        )
    if not per_attempt:
        return {}
    return {
        name: sum(item[index] for item in per_attempt.values())
        for index, name in enumerate(USAGE_FIELDS)
    }


def _add_usage(total: dict[str, int], row: dict) -> None:
    for name in USAGE_FIELDS:
        value = row.get(name, "")
        if value not in ("", None):
            total[name] = total.get(name, 0) + int(value)


def _failed_row(spec, run_id, reason: str, usage: dict | None = None) -> dict:
    row = {name: "" for name in RESULT_HEADER}
    row.update({
        "run_id": run_id, "dataset": spec.dataset_id, "mechanism": spec.mechanism,
        "rep": spec.rep if spec.rep is not None else "", "dim": spec.dim_nominal or "",
        "status": "failed", "failure_reason": reason,
        "interactions": 0, "duration_s": 0.0,
    })
    if usage:
        row.update(usage)
    return row


def _run_once(spec, config: Config, client: AgentClient, run_dir: Path, run_id: str) -> dict:
    X, adjacency = ds.load(spec)
    gold = ds.gold_edges(adjacency)
    if not gold:
        raise ds.DatasetInvalid("gold 为空")
    dim = X.shape[1]
    upload_path = run_dir / "csv" / f"{ds.artifact_name(spec.dataset_id)}.csv"
    ds.write_csv(X, upload_path)
    profile = ds.profile(X)

    try:
        user_file_id = client.upload_csv(upload_path)
        session_id = client.new_session()
    except ClientError:
        raise StepError("upload_error")
    started = time.monotonic()
    try:
        job_id = client.create_job(session_id, build_prompt(dim), user_file_id)
    except ClientError:
        raise StepError("job_rejected")
    deadline = started + config.run.timeout_minutes * 60

    last_event_id = 0
    reconnects = 0
    interactions = 0
    interaction_deadline = started + config.run.interaction_seconds
    final_payload = None
    failure = None
    records = []

    while time.monotonic() < deadline:
        try:
            event_iter = client.events(job_id, last_event_id)
            terminal = False
            answered = False
            for event in event_iter:
                if time.monotonic() > deadline:
                    failure = "timeout"
                    client.cancel(job_id)
                    terminal = True
                    break
                if event["id"] is not None:
                    last_event_id = event["id"]
                records.append({"id": event["id"], "type": event["type"], "payload": event["payload"]})
                if event["type"] == "interrupt":
                    if interactions >= config.run.max_interactions or time.monotonic() > interaction_deadline:
                        failure = "interaction_budget_exceeded"
                        client.cancel(job_id)
                        terminal = True
                        break
                    answer, _ = answer_for(event["payload"], profile)
                    client.resume(job_id, event["payload"].get("question_id"), answer)
                    interactions += 1
                    answered = True
                    break
                if event["type"] in ("final_result", "error", "canceled"):
                    final_payload = event["payload"]
                    failure = {"error": "terminal_error", "canceled": "canceled"}.get(event["type"])
                    terminal = True
                    break
            if answered:
                continue
            if terminal:
                break
            reconnects += 1
            if reconnects > 3:
                failure = "sse_error"
                break
            continue
        except ClientError:
            reconnects += 1
            if reconnects > 3:
                failure = "sse_error"
                break

    if failure is None and final_payload is None:
        failure = "timeout"
        client.cancel(job_id)
    duration = round(time.monotonic() - started, 1)
    (run_dir / "raw" / f"{ds.artifact_name(spec.dataset_id)}.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    row = _failed_row(spec, run_id, failure or "")
    row.update({"n_rows": int(X.shape[0]), "n_cols": dim, "dim": dim,
                "n_gold_edges": len(gold), "interactions": interactions,
                "duration_s": duration, "status": "failed" if failure else "success"})
    usage = usage_totals(records)
    if usage:
        row.update(usage)
    if failure:
        return row

    document = ((final_payload or {}).get("data") or {}).get("document") or {}
    rationale, confidence = ev.final_decision(records)
    row.update({"final_selection_rationale": rationale, "final_confidence": confidence})
    asset, reason = ev.find_graph_asset(document)
    (run_dir / "raw" / f"{ds.artifact_name(spec.dataset_id)}.json").write_text(
        json.dumps({"document": document, "interactions": interactions}, ensure_ascii=False),
        encoding="utf-8",
    )
    if asset is None:
        row["status"] = "failed"
        row["failure_reason"] = reason
        return row

    edges, unmapped = ev.parse_edges(asset, dim)
    metrics = ev.score(edges, gold)
    row.update(metrics)
    row["f1"] = ev.f1_score(metrics["precision"], metrics["recall"])
    row["shd"] = ev.shd(edges, gold)
    matrix = ev.probability_matrix(asset, dim)
    ranking = ev.ranking_metrics(matrix, gold) if matrix is not None else {"auroc": None, "auprc": None}
    for name in ("auroc", "auprc"):
        row[name] = "" if ranking[name] is None else ranking[name]
    row.update({
        "final_algorithm": (asset.get("metadata") or {}).get("algorithm", ""),
        "finalization_status": document.get("finalization_status", ""),
        "status": "partial" if unmapped else "success",
        "failure_reason": "edge_variable_unmapped" if unmapped else "",
    })
    return row


def _run_dataset(spec, config, credentials, run_id, run_dir) -> dict:
    try:
        _, adjacency = ds.load(spec)
        if not ds.gold_edges(adjacency):
            raise ds.DatasetInvalid("gold 为空")
    except ds.DatasetInvalid:
        return _failed_row(spec, run_id, "dataset_invalid")
    except Exception as exc:
        print(f"[warn] {spec.dataset_id}: {exc}", file=sys.stderr)
        return _failed_row(spec, run_id, "terminal_error")
    client = AgentClient(config.run.base_url)
    total_usage: dict[str, int] = {}
    try:
        client.login(*credentials)
        row = _run_once(spec, config, client, run_dir, run_id)
        _add_usage(total_usage, row)
        attempts = 0
        while row["status"] == "failed" and row["failure_reason"] in RETRYABLE and attempts < 1:
            attempts += 1
            row = _run_once(spec, config, client, run_dir, run_id)
            _add_usage(total_usage, row)
        if total_usage:
            row.update(total_usage)
        return row
    except ds.DatasetInvalid:
        return _failed_row(spec, run_id, "dataset_invalid", total_usage)
    except StepError as exc:
        return _failed_row(spec, run_id, exc.reason, total_usage)
    except TransportError:
        return _failed_row(spec, run_id, "terminal_error", total_usage)
    except ClientError:
        return _failed_row(spec, run_id, "auth_error", total_usage)
    except Exception as exc:
        print(f"[warn] {spec.dataset_id}: {exc}", file=sys.stderr)
        return _failed_row(spec, run_id, "terminal_error", total_usage)
    finally:
        client.close()


def _load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["dataset"] for row in csv.DictReader(handle) if row["status"] == "success"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CausalAgent 端到端评测")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    specs_all = ds.scan(config.data.dir)
    validate_selection(specs_all, config.data.mechanisms, config.data.dims, config.data.ns)
    specs = ds.select(specs_all, config.data.mechanisms, config.data.exclude,
                      config.data.dims, config.data.ns, config.data.per_cell, config.run.seed)
    cells = {(spec.mechanism, spec.dim_nominal, spec.n_nominal) for spec in specs}
    print(f"机制 {len({spec.mechanism for spec in specs})} 个，格 {len(cells)} 个，"
          f"共 {len(specs)} 集；parallel={config.run.parallel}；输出 {config.output.dir}")
    if args.dry_run:
        return 0

    probe = AgentClient(config.run.base_url)
    try:
        probe.check_auth()
    except Exception as exc:
        print(f"探活失败：{exc}（确认被测栈已启动）", file=sys.stderr)
        return 1
    finally:
        probe.close()

    if not args.yes and input("开始评测？[y/N] ").strip().lower() != "y":
        return 1
    credentials = resolve_credentials(config)
    bootstrap = AgentClient(config.run.base_url)
    try:
        bootstrap.login(*credentials)
    except ClientError as exc:
        print(f"登录失败：{exc}", file=sys.stderr)
        return 1
    finally:
        bootstrap.close()

    if args.resume:
        run_dir = args.resume
        run_id = run_dir.name
    else:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + _git_commit()
        run_dir = config.output.dir / run_id
    for name in ("csv", "raw"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.csv"
    if results_path.exists() and results_path.stat().st_size:
        with results_path.open(encoding="utf-8-sig", newline="") as handle:
            if next(csv.reader(handle), []) != RESULT_HEADER:
                print("results.csv 表头与当前版本不一致，请换新目录运行", file=sys.stderr)
                return 1
    done = _load_done(results_path) if args.resume else set()
    pending = [spec for spec in specs if spec.dataset_id not in done]

    new_file = not results_path.exists() or results_path.stat().st_size == 0
    lock = threading.Lock()
    with results_path.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as results:
        writer = csv.DictWriter(results, fieldnames=RESULT_HEADER)
        if results.tell() == 0:
            writer.writeheader()
        with ThreadPoolExecutor(max_workers=config.run.parallel) as pool:
            futures = [
                pool.submit(_run_dataset, spec, config, credentials, run_id, run_dir)
                for spec in pending
            ]
            for future in as_completed(futures):
                row = future.result()
                with lock:
                    writer.writerow(row)
                    results.flush()
                print(f"{row['status']:7s} {row['dataset']} {row['failure_reason']}")

    rows = list(csv.DictReader(results_path.open(encoding="utf-8-sig", newline="")))
    for row in rows:
        for name in ("interactions", "n_rows", "n_cols", "dim", "n_gold_edges",
                     "n_pred_edges", "n_undirected_edges"):
            row[name] = float(row[name] or 0)
        for name in ("recall", "precision", "skeleton_recall", "skeleton_precision", "duration_s"):
            row[name] = float(row[name] or 0)
        for name in ("f1", "shd", "auroc", "auprc"):
            value = row.get(name, "")
            row[name] = float(value) if value not in ("", None) else ""
        for name in USAGE_FIELDS:
            value = row.get(name, "")
            row[name] = float(value) if value not in ("", None) else ""
    meta = {
        "run_id": run_id, "started_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(), "base_url": config.run.base_url,
        "data_dir": str(config.data.dir), "parallel": config.run.parallel,
        "sampling": {"mechanisms": len({spec.mechanism for spec in specs}),
                     "per_cell": config.data.per_cell, "seed": config.run.seed},
    }
    summary = rp.summarize(rows, meta)
    with (run_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rp.SUMMARY_HEADER)
        writer.writeheader()
        writer.writerow(summary)
    (run_dir / "report.md").write_text(rp.report_markdown(rows, meta), encoding="utf-8")
    print(f"完成：{summary['succeeded']}/{summary['datasets']} 成功，输出 {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
