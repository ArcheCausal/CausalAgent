"""独立的 MySQL 数据库配置。

该模块供 ``app.db`` 以及不需要模型能力的数据库消费者使用。它不能依赖
``config.settings``，因此导入数据库访问层不会触发应用密钥或模型配置校验。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path


def _load_dotenv_if_available() -> None:
    """在独立配置入口中加载项目根目录 ``.env``，不输出任何敏感值。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _get_config(name: str, *, required: bool = True, default: str | None = None) -> str | None:
    """读取字符串环境变量，并在缺失时给出不包含配置值的错误。"""
    value = os.getenv(name)
    if value:
        return value
    if required:
        error_msg = (
            f"配置错误: 缺少必需的环境变量 '{name}'。\n"
            f"请确保：\n"
            f"  - Docker环境：在项目根目录的 .env 文件中设置 {name}=...\n"
            f"  - 本地开发：在项目根目录的 .env 文件中设置 {name}=...\n"
            f"  - 或直接设置系统环境变量\n"
        )
        logging.error(error_msg)
        raise ValueError(error_msg)
    return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"配置错误: 环境变量 '{name}' 必须是整数。") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"配置错误: 环境变量 '{name}' 必须是数字。") from exc


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(repr=False)
class DatabaseConfig:
    """MySQL 连接、连接池和副本读策略配置。"""

    MYSQL_HOST: str
    MYSQL_WRITE_HOST: str
    MYSQL_READ_HOSTS: list[str]
    MYSQL_PORT: int
    MYSQL_USER: str | None
    MYSQL_PASSWORD: str | None
    MYSQL_WRITE_USER: str
    MYSQL_WRITE_PASSWORD: str
    MYSQL_READ_USER: str
    MYSQL_READ_PASSWORD: str
    MYSQL_REPLICA_STATUS_USER: str | None
    MYSQL_REPLICA_STATUS_PASSWORD: str | None
    MYSQL_DATABASE: str
    MYSQL_POOL_SIZE_WRITE: int
    MYSQL_POOL_SIZE_READ: int
    MYSQL_CONNECT_TIMEOUT_SECONDS: int
    MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS: float
    MYSQL_POOL_ACQUIRE_RETRY_MS: int
    MYSQL_REPLICA_STATUS_CACHE_SECONDS: float
    MYSQL_REPLICA_MAX_LAG_SECONDS: int
    MYSQL_QUERY_WARN_MS: int

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """从环境变量加载数据库配置，不读取应用或模型配置。"""
        _load_dotenv_if_available()

        legacy_host = _get_config("MYSQL_HOST", required=False)
        write_host = _get_config(
            "MYSQL_WRITE_HOST",
            required=False,
            default=legacy_host,
        )
        if not write_host:
            raise ValueError(
                "配置错误: 缺少必需的环境变量 'MYSQL_HOST' 或 'MYSQL_WRITE_HOST'。"
            )

        user = _get_config("MYSQL_USER", required=False)
        password = _get_config("MYSQL_PASSWORD", required=False)
        write_user = _get_config(
            "MYSQL_WRITE_USER",
            required=False,
            default=user,
        )
        write_password = _get_config(
            "MYSQL_WRITE_PASSWORD",
            required=False,
            default=password,
        )
        read_user = _get_config(
            "MYSQL_READ_USER",
            required=False,
            default=user,
        )
        read_password = _get_config(
            "MYSQL_READ_PASSWORD",
            required=False,
            default=password,
        )
        missing_credentials = [
            name
            for name, value in {
                "MYSQL_WRITE_USER 或 MYSQL_USER": write_user,
                "MYSQL_WRITE_PASSWORD 或 MYSQL_PASSWORD": write_password,
                "MYSQL_READ_USER 或 MYSQL_USER": read_user,
                "MYSQL_READ_PASSWORD 或 MYSQL_PASSWORD": read_password,
            }.items()
            if not value
        ]
        if missing_credentials:
            raise ValueError(f"配置错误: 缺少数据库账号配置 {missing_credentials}")

        config = cls(
            # 与旧 AppConfig 保持兼容：最终 MYSQL_HOST 等于写库地址。
            MYSQL_HOST=write_host,
            MYSQL_WRITE_HOST=write_host,
            MYSQL_READ_HOSTS=_parse_csv(
                _get_config("MYSQL_READ_HOSTS", required=False, default="")
            ),
            MYSQL_PORT=_int_env("MYSQL_PORT", 3306),
            MYSQL_USER=user,
            MYSQL_PASSWORD=password,
            MYSQL_WRITE_USER=write_user,
            MYSQL_WRITE_PASSWORD=write_password,
            MYSQL_READ_USER=read_user,
            MYSQL_READ_PASSWORD=read_password,
            MYSQL_REPLICA_STATUS_USER=_get_config(
                "MYSQL_REPLICA_STATUS_USER",
                required=False,
            ),
            MYSQL_REPLICA_STATUS_PASSWORD=_get_config(
                "MYSQL_REPLICA_STATUS_PASSWORD",
                required=False,
            ),
            MYSQL_DATABASE=_get_config("MYSQL_DATABASE"),
            MYSQL_POOL_SIZE_WRITE=_int_env("MYSQL_POOL_SIZE_WRITE", 5),
            MYSQL_POOL_SIZE_READ=_int_env("MYSQL_POOL_SIZE_READ", 5),
            MYSQL_CONNECT_TIMEOUT_SECONDS=_int_env(
                "MYSQL_CONNECT_TIMEOUT_SECONDS",
                5,
            ),
            MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS=_float_env(
                "MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS",
                3.0,
            ),
            MYSQL_POOL_ACQUIRE_RETRY_MS=_int_env(
                "MYSQL_POOL_ACQUIRE_RETRY_MS",
                50,
            ),
            MYSQL_REPLICA_STATUS_CACHE_SECONDS=_float_env(
                "MYSQL_REPLICA_STATUS_CACHE_SECONDS",
                2.0,
            ),
            MYSQL_REPLICA_MAX_LAG_SECONDS=_int_env(
                "MYSQL_REPLICA_MAX_LAG_SECONDS",
                2,
            ),
            MYSQL_QUERY_WARN_MS=_int_env("MYSQL_QUERY_WARN_MS", 500),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """校验连接池和连接策略的边界。"""
        if not 1 <= self.MYSQL_PORT <= 65535:
            raise ValueError("配置错误: MYSQL_PORT 必须是有效端口。")

        positive = {
            "MYSQL_POOL_SIZE_WRITE": self.MYSQL_POOL_SIZE_WRITE,
            "MYSQL_POOL_SIZE_READ": self.MYSQL_POOL_SIZE_READ,
            "MYSQL_CONNECT_TIMEOUT_SECONDS": self.MYSQL_CONNECT_TIMEOUT_SECONDS,
            "MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS": self.MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS,
            "MYSQL_POOL_ACQUIRE_RETRY_MS": self.MYSQL_POOL_ACQUIRE_RETRY_MS,
            "MYSQL_REPLICA_STATUS_CACHE_SECONDS": self.MYSQL_REPLICA_STATUS_CACHE_SECONDS,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"配置错误: {name} 必须大于 0。")
        if self.MYSQL_POOL_SIZE_WRITE > 32 or self.MYSQL_POOL_SIZE_READ > 32:
            raise ValueError(
                "配置错误: MySQL Connector/Python 单连接池大小不能超过 32。"
            )


# app.db 的唯一配置来源。该对象的初始化不会导入 config.settings。
database_settings = DatabaseConfig.from_env()
