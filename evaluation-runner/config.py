"""读取并校验评测配置。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """配置缺项或取值非法。"""


@dataclass(frozen=True)
class RunSettings:
    base_url: str
    parallel: int = 1
    timeout_minutes: int = 15
    max_interactions: int = 6
    interaction_seconds: int = 300
    seed: int = 0


@dataclass(frozen=True)
class AuthSettings:
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class DataSettings:
    dir: Path
    mechanisms: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    dims: tuple[int, ...] = ()
    ns: tuple[int, ...] = ()
    per_cell: int = 1


@dataclass(frozen=True)
class OutputSettings:
    dir: Path


@dataclass(frozen=True)
class Config:
    run: RunSettings
    auth: AuthSettings
    data: DataSettings
    output: OutputSettings


def _positive(value, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{name} 必须是正整数")
    return value


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是映射")

    run_raw = raw.get("run") or {}
    base_url = str(run_raw.get("base_url") or "").strip()
    if not base_url:
        raise ConfigError("run.base_url 必填")
    run = RunSettings(
        base_url=base_url.rstrip("/"),
        parallel=_positive(run_raw.get("parallel", 1), "run.parallel"),
        timeout_minutes=_positive(run_raw.get("timeout_minutes", 15), "run.timeout_minutes"),
        max_interactions=_positive(run_raw.get("max_interactions", 6), "run.max_interactions"),
        interaction_seconds=_positive(
            run_raw.get("interaction_seconds", 300), "run.interaction_seconds"
        ),
        seed=int(run_raw.get("seed", 0)),
    )

    auth_raw = raw.get("auth") or {}
    auth = AuthSettings(
        username=str(auth_raw.get("username") or "").strip(),
        password=str(auth_raw.get("password") or ""),
    )

    data_raw = raw.get("data") or {}
    if not data_raw.get("dir"):
        raise ConfigError("data.dir 必填")
    mechanisms_raw = data_raw.get("mechanisms", "all")
    if mechanisms_raw is None or mechanisms_raw == "all":
        mechanisms = None
    elif isinstance(mechanisms_raw, list):
        mechanisms = tuple(str(value) for value in mechanisms_raw)
    else:
        raise ConfigError("data.mechanisms 必须是机制名列表或 all")
    per_cell = data_raw.get("per_cell", 1)
    if not isinstance(per_cell, int) or isinstance(per_cell, bool) or not 1 <= per_cell <= 5:
        raise ConfigError("data.per_cell 必须是 1..5 的整数")
    data = DataSettings(
        dir=Path(data_raw["dir"]),
        mechanisms=mechanisms,
        exclude=tuple(data_raw.get("exclude") or ()),
        dims=tuple(int(value) for value in data_raw.get("dims") or ()),
        ns=tuple(int(value) for value in data_raw.get("ns") or ()),
        per_cell=per_cell,
    )

    output_raw = raw.get("output") or {}
    output = OutputSettings(dir=Path(output_raw.get("dir") or "./local/runs"))

    return Config(run=run, auth=auth, data=data, output=output)
