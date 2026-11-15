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


def docker_version() -> str | None:
    proc = _run(["version", "--format", "{{.Server.Version}}"], check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


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
