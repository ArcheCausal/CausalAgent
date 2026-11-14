"""配置加载：默认值 + config.yaml 深合并。"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "cache_dir": "~/.general-agent-eval/cache",
    "output_dir": "~/general-agent-eval-runs",
    "workspace_dir": "~/.general-agent-eval/work",
    "data": {
        "name": "cdfm_bench_rep00",
        "local_dir": "",
        "url": "",
        "sha256": "",
        "exclude": ["TimeLag"],
    },
    "selection": {
        "mechanisms": [],
        "dims": [],
        "ns": [],
        "per_cell": 1,
        "seed": 20261030,
    },
    "agent": {
        "image": "general-agent-eval/agent:py311-toolbox-v1",
        "base_url": "https://api.deepseek.com/anthropic",
        "model": "deepseek-chat",
        "auth_env": "DEEPSEEK_API_KEY",
        "timeout_s": 600,
        "grace_s": 30,
        "retries": 1,
        "disallowed_tools": "WebSearch,WebFetch",
        "pip_index_url": "https://mirrors.aliyun.com/pypi/simple/",
    },
    "docker": {
        "cpus": "2",
        "memory": "4g",
        "pids_limit": 512,
        "shm_size": "512m",
        "host_user": True,
        "keep_container_on_failure": True,
        "network": "bridge",
    },
    "judge": {"enabled": False},
    "baseline_results": "",
}


class ConfigError(RuntimeError):
    pass


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | str) -> dict:
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = _merge(DEFAULTS, raw)
    for key in ("cache_dir", "output_dir", "workspace_dir"):
        config[key] = Path(os.path.expanduser(str(config[key]))).resolve()
    local = config["data"].get("local_dir")
    if local:
        config["data"]["local_dir"] = Path(os.path.expanduser(str(local))).resolve()
    baseline = config.get("baseline_results")
    config["baseline_results"] = (
        Path(os.path.expanduser(str(baseline))).resolve() if baseline else None
    )
    return config
