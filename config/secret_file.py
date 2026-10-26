"""统一的服务器密钥文件读取边界。

文档职责：定义应用、worker、MCP 与数据库配置读取密码、令牌和签名密钥的唯一入口，
以及发布包校验密钥文件时使用的键集合。

适用范围：所有需要密钥的运行时进程、Compose 密钥挂载和发布校验脚本；非敏感配置继续
使用环境变量或非密钥配置文件，不进入密钥文件。

密钥只从服务器上的单个文件读取，默认路径为 /run/secrets/causalagent.env，可以用
环境变量 CAUSALAGENT_SECRET_FILE 覆盖。应用容器通过 Compose secret 挂载该文件；
MySQL、PostgreSQL 和 Grafana 等官方镜像由容器启动适配脚本读取同一个文件，再导出
官方入口需要的环境变量。

本模块不输出任何密钥值：错误信息只包含键名、行号、文件路径和权限位。
"""

from __future__ import annotations

import os
import re
import stat
import threading
from pathlib import Path
from typing import Mapping


SECRET_FILE_ENV_VAR = "CAUSALAGENT_SECRET_FILE"
DEFAULT_SECRET_FILE_PATH = Path("/run/secrets/causalagent.env")

# 密钥文件允许出现的键。未知键会被拒绝，避免把拼写错误或非密钥配置写进密钥文件。
SECRET_KEYS: tuple[str, ...] = (
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_PASSWORD",
    "MYSQL_WRITE_PASSWORD",
    "MYSQL_READ_PASSWORD",
    "MYSQL_REPLICA_STATUS_PASSWORD",
    "MYSQL_REPLICATION_PASSWORD",
    "CHECKPOINT_POSTGRES_PASSWORD",
    "SECRET_KEY",
    "API_KEY",
    "DEEP_AGENT_API_KEY",
    "VISION_API_KEY",
    "EMBEDDING_API_KEY",
    "LANGCHAIN_API_KEY",
    "SEARXNG_SECRET",
    "GRAFANA_ADMIN_PASSWORD",
    "CAUSAL_MCP_SERVICE_TOKEN",
    "CAUSAL_MCP_SIGNING_KEY_CURRENT",
    "CAUSAL_MCP_SIGNING_KEY_PREVIOUS",
    "CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN",
    "CAUSALAGENT_SANDBOX_API_KEY",
)

# 应用入口默认需要的基础密钥；独立服务必须显式声明自己的必需键。
RUNTIME_REQUIRED_SECRET_KEYS: tuple[str, ...] = (
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_PASSWORD",
    "MYSQL_WRITE_PASSWORD",
    "MYSQL_READ_PASSWORD",
    "MYSQL_REPLICATION_PASSWORD",
    "CHECKPOINT_POSTGRES_PASSWORD",
    "SECRET_KEY",
    "API_KEY",
    "DEEP_AGENT_API_KEY",
    "CAUSAL_MCP_SERVICE_TOKEN",
    "CAUSAL_MCP_SIGNING_KEY_CURRENT",
    "GRAFANA_ADMIN_PASSWORD",
)

SANDBOX_SECRET_KEYS: tuple[str, ...] = (
    "CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN",
    "CAUSALAGENT_SANDBOX_API_KEY",
)

# 发布部署包含沙盒服务；独立进程仍只验证自身必需键。
REQUIRED_SECRET_KEYS: tuple[str, ...] = (*RUNTIME_REQUIRED_SECRET_KEYS, *SANDBOX_SECRET_KEYS)

SECRET_SCHEMA_VERSION = "v2"

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_FILE_MODES = (0o600, 0o400, 0o640)

_lock = threading.RLock()
_cached_path: Path | None = None
_cached_values: dict[str, str] | None = None


class SecretFileError(RuntimeError):
    """密钥文件缺失、格式错误、权限不安全或缺少必需键。"""


def resolve_secret_file_path(environ: Mapping[str, str] | None = None) -> Path:
    """返回密钥文件路径；未显式配置时使用容器内默认挂载点。"""
    values = os.environ if environ is None else environ
    override = values.get(SECRET_FILE_ENV_VAR, "").strip()
    return Path(override) if override else DEFAULT_SECRET_FILE_PATH


def _check_file_permissions(path: Path) -> None:
    """开发环境兼容主机挂载；其他环境保留 POSIX 权限检查。"""
    if os.name == "nt" or os.environ.get("ENVIRONMENT") == "development":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode not in _ALLOWED_FILE_MODES:
        allowed = " 或 ".join(format(item, "04o") for item in _ALLOWED_FILE_MODES)
        raise SecretFileError(
            f"密钥文件权限不安全: {path} 当前为 {mode:04o}，必须是 {allowed}。"
            "使用 0640 时，文件必须属于容器专用组 GID 3000。"
        )


def _parse_secret_file(text: str, *, path: Path) -> dict[str, str]:
    """解析 KEY=VALUE 行并返回键值对；任何歧义都直接失败。"""
    values: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SecretFileError(f"密钥文件格式错误: {path} 第 {number} 行缺少等号。")
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not _KEY_PATTERN.match(key):
            raise SecretFileError(
                f"密钥文件格式错误: {path} 第 {number} 行的键名不合法。"
            )
        if key in values:
            raise SecretFileError(
                f"密钥文件格式错误: {path} 第 {number} 行重复定义键 {key}。"
            )
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value:
            raise SecretFileError(
                f"密钥文件格式错误: {path} 第 {number} 行的键 {key} 取值为空。"
            )
        values[key] = value

    unknown = sorted(set(values) - set(SECRET_KEYS))
    if unknown:
        raise SecretFileError(
            f"密钥文件包含未定义的键: {', '.join(unknown)}。"
            "请只保留 config/secret_file.py 中 SECRET_KEYS 声明的键。"
        )
    for key in SANDBOX_SECRET_KEYS:
        token = values.get(key)
        if token is not None and (not 32 <= len(token) <= 4096 or any(char.isspace() for char in token)):
            raise SecretFileError(f"密钥 {key} 必须为 32 至 4096 个字符且不含空白。")
    if all(key in values for key in SANDBOX_SECRET_KEYS) and values[SANDBOX_SECRET_KEYS[0]] == values[SANDBOX_SECRET_KEYS[1]]:
        raise SecretFileError("沙盒控制服务 token 与 OpenSandbox API key 必须使用不同的值。")
    return values


def _validate_required_keys(values: dict[str, str], required_keys: tuple[str, ...]) -> None:
    unknown = sorted(set(required_keys) - set(SECRET_KEYS))
    if unknown:
        raise SecretFileError(f"必需密钥包含未声明的键: {', '.join(unknown)}。")
    missing = [name for name in required_keys if name not in values]
    if missing:
        raise SecretFileError(f"密钥文件缺少必需键: {', '.join(missing)}。")


def load_secrets(
    *,
    path: Path | None = None,
    reload: bool = False,
    required_keys: tuple[str, ...] = RUNTIME_REQUIRED_SECRET_KEYS,
) -> dict[str, str]:
    """读取并缓存文件；每次调用均校验显式必需键，默认校验应用完整集合。"""
    global _cached_path, _cached_values

    resolved = Path(path) if path is not None else resolve_secret_file_path()
    with _lock:
        for name in SANDBOX_SECRET_KEYS:
            os.environ.pop(name, None)
        if not reload and _cached_values is not None and _cached_path == resolved:
            _validate_required_keys(_cached_values, required_keys)
            return dict(_cached_values)
        for name in SECRET_KEYS:
            os.environ.pop(name, None)
        if not resolved.is_file():
            raise SecretFileError(
                f"密钥文件不存在: {resolved}。请确认 Compose secret 已挂载，"
                f"或设置 {SECRET_FILE_ENV_VAR} 指向真实文件。"
            )
        _check_file_permissions(resolved)
        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise SecretFileError(f"密钥文件不可读: {resolved}") from exc
        values = _parse_secret_file(text, path=resolved)
        _validate_required_keys(values, required_keys)
        _cached_path = resolved
        _cached_values = dict(values)
        return dict(_cached_values)


def get_secret(name: str, *, default: str | None = None) -> str | None:
    """按键读取一个密钥；键不在 SECRET_KEYS 中时抛出配置错误。"""
    if name not in SECRET_KEYS:
        raise SecretFileError(f"{name} 不是已声明的密钥键。")
    return load_secrets(required_keys=()).get(name, default)


def require_secret(name: str) -> str:
    """按键读取一个必需密钥；缺失时抛出配置错误。"""
    value = get_secret(name)
    if not value:
        raise SecretFileError(f"密钥文件缺少必需键: {name}。")
    return value


def reset_secret_cache() -> None:
    """清空进程内缓存，供测试与显式重载使用。"""
    global _cached_path, _cached_values
    with _lock:
        _cached_path = None
        _cached_values = None
