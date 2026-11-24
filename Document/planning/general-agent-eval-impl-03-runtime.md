# 实现手册 3：容器执行、编排、命令行

> 本阶段产出：能真正跑完一集的完整链路。
> 结束标准：`--limit 1` 跑出一行 `success`，且 `case.json`、`raw/*.jsonl`、`answer/adjacency.csv` 都在。

---

## 3.1 执行链路

```text
CLI
 └─ runner.execute
     └─ 每集 _execute_case
         ├─ 载入 X / gold
         ├─ 准备 case 目录（data.csv、answer/、.harness/home）
         ├─ 挂载探针（失败即中止）
         ├─ docker run（不加 --rm）
         ├─ 业务超时 600s → TERM；再 30s → KILL
         ├─ docker inspect（退出码 / OOM）
         ├─ 解析 answer/adjacency.csv
         ├─ 审计工具调用
         ├─ 评分
         ├─ 写 case.json（原子）
         └─ 成功才删容器；失败按配置保留现场
```

---

## 3.2 `src/general_agent_eval/trace.py`

```python
"""从 stream-json 日志提取事件与工具调用。"""
from __future__ import annotations

import csv
import json
from pathlib import Path

HEADER = ["case", "dataset", "event", "tool", "input_preview"]


def load_events(path) -> list[dict]:
    events: list[dict] = []
    path = Path(path)
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def tool_rows(events, case_name: str, dataset: str) -> list[dict]:
    rows = []
    for index, event in enumerate(events):
        content = (event.get("message") or {}).get("content") or ()
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                rows.append({
                    "case": case_name,
                    "dataset": dataset,
                    "event": index,
                    "tool": block.get("name", ""),
                    "input_preview": json.dumps(block.get("input") or {}, ensure_ascii=False)[:200],
                })
    return rows


def append_csv(path, rows) -> None:
    if not rows:
        return
    path = Path(path)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        if new:
            writer.writeheader()
        writer.writerows(rows)
```

---

## 3.3 `src/general_agent_eval/container.py`

这是**唯一**允许出现 `docker` 命令的模块。

```python
"""容器执行：启动、探针、超时、inspect、清理。"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class DockerError(RuntimeError):
    pass


def _run(args, timeout=120, check=True):
    proc = subprocess.run(
        ["docker", *map(str, args)],
        capture_output=True, text=True, timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise DockerError(f"docker {' '.join(map(str, args))} 失败：{proc.stderr.strip()[:500]}")
    return proc


def image_digest(image: str) -> str:
    proc = _run(["inspect", "--format", "{{index .RepoDigests 0}}", image], check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return _run(["inspect", "--format", "{{.Id}}", image]).stdout.strip()


def host_user_args(config) -> list[str]:
    # 让容器内写的文件归宿主当前用户，否则下一集 rmtree 会失败
    if not config["docker"].get("host_user") or not hasattr(os, "getuid"):
        return []
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


def probe_mount(config, case_dir) -> None:
    case_dir = Path(case_dir)
    probe = case_dir / ".harness" / "mount-probe"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.unlink(missing_ok=True)
    _run([
        "run", "--rm", "--entrypoint", "sh", *host_user_args(config),
        "-v", f"{case_dir}:/work", "-w", "/work",
        config["agent"]["image"],
        "-c", "echo probe > /work/.harness/mount-probe",
    ])
    if not probe.exists() or probe.read_text(encoding="utf-8").strip() != "probe":
        raise DockerError("挂载探针失败：容器内写入未反映到宿主")


@dataclass
class Result:
    name: str
    exit_code: int | None
    timed_out: bool
    duration: float
    inspect: dict


def run_container(config, case_dir, prompt: str, name: str, log_path,
                  run_id: str, case_name: str) -> Result:
    agent = config["agent"]
    docker_cfg = config["docker"]
    case_dir = Path(case_dir)
    (case_dir / ".harness" / "home").mkdir(parents=True, exist_ok=True)
    (case_dir / "answer").mkdir(parents=True, exist_ok=True)

    command = [
        "docker", "run", "--name", name,
        "--label", f"gae.run={run_id}",
        "--label", f"gae.case={case_name}",
        *host_user_args(config),
        "--cpus", str(docker_cfg["cpus"]),
        "--memory", str(docker_cfg["memory"]),
        "--pids-limit", str(docker_cfg["pids_limit"]),
        "--shm-size", str(docker_cfg["shm_size"]),
        "--network", str(docker_cfg.get("network") or "bridge"),
        "-v", f"{case_dir}:/work",
        "-w", "/work",
        "-e", "HOME=/work/.harness/home",
        "-e", f"ANTHROPIC_BASE_URL={agent['base_url']}",
        "-e", f"ANTHROPIC_AUTH_TOKEN={os.environ.get(agent['auth_env'], '')}",
        "-e", f"ANTHROPIC_MODEL={agent['model']}",
        "-e", f"ANTHROPIC_SMALL_FAST_MODEL={agent['model']}",
        "-e", f"PIP_INDEX_URL={agent['pip_index_url']}",
        "-e", "OMP_NUM_THREADS=1",
        "-e", "OPENBLAS_NUM_THREADS=1",
        "-e", "MKL_NUM_THREADS=1",
        agent["image"],
        "claude", "-p", prompt,
        "--model", agent["model"],
        "--output-format", "stream-json",
        "--verbose",
        # 只在单 case 容器里放开：没有宿主仓库/金标准/密钥文件挂载，边界由容器定义
        "--dangerously-skip-permissions",
        "--strict-mcp-config",
        "--disallowedTools", agent["disallowed_tools"],
    ]

    timeout_s = float(agent["timeout_s"])
    grace_s = float(agent["grace_s"])
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    timed_out = False
    # 直接写文件而不是 PIPE：10 分钟的输出会超过管道缓冲区，PIPE + wait 会死锁
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _signal(name, "TERM")
            try:
                proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                _signal(name, "KILL")
                proc.wait(timeout=15)
    duration = round(time.monotonic() - started, 1)

    info = inspect_container(name)
    state_exit = (info.get("State") or {}).get("ExitCode")
    exit_code = state_exit if state_exit is not None else proc.returncode
    return Result(name, exit_code, timed_out, duration, info)


def _signal(name: str, sig: str) -> None:
    subprocess.run(["docker", "kill", "-s", sig, name], capture_output=True, text=True)


def inspect_container(name: str) -> dict:
    proc = _run(["inspect", name], check=False)
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout)[0]
    except (json.JSONDecodeError, IndexError):
        return {}


def remove_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


def kill_run(run_id: str, sig: str = "TERM") -> None:
    proc = _run(["ps", "-aq", "--filter", f"label=gae.run={run_id}"], check=False)
    names = proc.stdout.split()
    if names:
        subprocess.run(["docker", "kill", "-s", sig, *names], capture_output=True, text=True)


# preflight 只读检查也走这里，避免第二处拼 docker 命令
docker = _run
```

三个必须理解的细节：

1. **输出重定向到文件**，不用 `PIPE`。`subprocess.PIPE` + 长时间 `wait()` 会在缓冲区满时死锁。
2. **不加 `--rm`**。先 `inspect` 拿到 `ExitCode` / `OOMKilled`，再决定删不删。
3. **`--user uid:gid`**。容器内不 root 写文件，宿主才能正常删除和读取。

---

## 3.4 `src/general_agent_eval/runner.py`

```python
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
```

`case.json` 是权威状态，`results.csv` 只是投影——中断时 CSV 可能少最后一行，但 `case.json` 不会说谎。

---

## 3.5 `src/general_agent_eval/cli.py`

```python
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
```

---

## 3.6 验证

### 3.6.1 选集与拒绝空选择

```bash
cd ~/CausalAgent3/general-agent-eval
source .venv/bin/activate
export DEEPSEEK_API_KEY=你的key

general-agent-eval --config config.yaml setup
```

预期：打印数据目录、总数、可选的机制/D/N。

```bash
general-agent-eval --config config.yaml run --dry-run
echo $?
```

预期：打印可选项，退出码 `2`。

```bash
general-agent-eval --config config.yaml run \
  --mechanisms Linear --dims 10 --ns 500 --dry-run
```

预期：列出 `Linear/D010_N500/rep00`。

### 3.6.2 跑一集（真实调用）

```bash
general-agent-eval --config config.yaml run \
  --mechanisms Linear --dims 10 --ns 500 --limit 1
```

跑完后检查：

```bash
LATEST=$(ls -dt ~/general-agent-eval-runs/runs/* | head -1)
echo $LATEST
cat $LATEST/run.json
cat $LATEST/results.csv
cat $LATEST/cases/case-01/case.json
ls $LATEST/cases/case-01/answer/
ls $LATEST/cases/case-01/.harness/
```

必须确认：

- `run.json` 里有 `image_digest`、`selection`、`data.fingerprint`；
- `case.json` 里 `status` 是 `success`（或 `timeout_partial`），`metrics` 非空；
- `answer/adjacency.csv` 在**宿主**上能看到 → 说明挂载正确；
- `.harness/pre.json`、`post.json` 都存在；
- `docker ps -a` 里没有本次 `gae-*` 容器（成功会自动删）。

### 3.6.3 验证中断清理

再跑一集，在终端里按 Ctrl-C：

```bash
general-agent-eval --config config.yaml run \
  --mechanisms Linear --dims 10 --ns 500 --limit 1
```

预期：打印"按标签清理本次运行的容器"，然后 `docker ps -a | grep gae-` 为空。

---

## 3.7 常见错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `挂载探针失败` | `--user` 和目录属主不匹配 | 目录用当前用户创建；或临时 `host_user: false` 验证 |
| `permission denied` 删 case 目录 | 之前用 root 跑过 | `sudo chown -R $USER:$USER <run_dir>` |
| 每集都 `docker_error exit 125` | 镜像不存在/名字错 | `docker images` 核对 |
| `claude_error exit 1`，2 秒退出 | CLI 启动失败 | 看 `raw/*.jsonl` 和 `docker logs` |
| `timeout_partial` 但 F1 很低 | 模型没写完就被杀 | 正常现象，报告里单列 |
| `pre.json` 缺失 | 入口脚本没跑起来 | 检查 ENTRYPOINT、`/work` 可写 |
| 输出日志为空 | `docker run` 没起来 | 看 `inspect.json` 的 `State` |
| 中断后容器还在 | 没走 `kill_run` | 手动 `docker ps -aq --filter label=gae.run=... | xargs docker kill` |

---

## 3.8 本阶段检查点

- [ ] `setup` 能看到数据集清单
- [ ] 不带选择参数时退出码为 2
- [ ] `--limit 1` 能跑出一行 `success`
- [ ] `answer/adjacency.csv` 在宿主可见
- [ ] `case.json` 有 `metrics` 和 `evidence`
- [ ] `run.json` 有镜像指纹和数据指纹
- [ ] Ctrl-C 后无残留容器

下一份：[04-report-ops](general-agent-eval-impl-04-report-ops.md)。
