"""
数据库初始化引导脚本。

本脚本只负责加载配置、确保数据库存在、检查连接，并提示使用 Alembic 维护业务表结构。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from observability.logging_runtime import configure_logging, current_environment, log_event
from config.secret_file import load_secrets, require_secret


LOGGER = logging.getLogger(__name__)

if __name__ == "__main__":
    configure_logging("maintenance", current_environment(), logging.INFO)

try:
    import mysql.connector
except Exception as exc:
    if __name__ == "__main__":
        log_event(
            LOGGER,
            "maintenance.startup.failed",
            details={
                "phase": "module_initialization",
                "dependency": "mysql_driver",
                "reason_code": "initialization_failed",
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        raise SystemExit(1) from None
    raise


class DatabaseBootstrap:
    def __init__(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.mysql_config: dict[str, str | int] = {}
        self.load_database_config()

    def load_database_config(self) -> None:
        load_secrets()
        host = os.environ.get("MYSQL_WRITE_HOST") or os.environ.get("MYSQL_HOST")
        write_user = os.environ.get("MYSQL_WRITE_USER") or os.environ.get("MYSQL_USER")
        write_password = require_secret("MYSQL_WRITE_PASSWORD")
        required = {
            "MYSQL_HOST 或 MYSQL_WRITE_HOST": host,
            "MYSQL_WRITE_USER 或 MYSQL_USER": write_user,
            "MYSQL_DATABASE": os.environ.get("MYSQL_DATABASE"),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"缺少数据库环境变量: {missing}")

        self.mysql_config = {
            "host": host,
            "port": int(os.environ.get("MYSQL_PORT", "3306")),
            "user": write_user,
            "password": write_password,
            "database": os.environ["MYSQL_DATABASE"],
        }

    def create_database_if_not_exists(self) -> None:
        root_password = require_secret("MYSQL_ROOT_PASSWORD")
        database_name = str(self.mysql_config["database"])
        try:
            conn = mysql.connector.connect(
                host=self.mysql_config["host"],
                port=self.mysql_config["port"],
                user="root",
                password=root_password,
            )
            cursor = conn.cursor()
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error:
            raise

    def check_database_connection(self) -> bool:
        try:
            conn = self.open_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            ok = bool(row and row[0] == 1)
            return ok
        except mysql.connector.Error:
            return False

    def open_connection(self):
        """打开供 bootstrap 的迁移前置检查使用的写库连接。"""
        return mysql.connector.connect(**self.mysql_config)

    def bootstrap(self) -> bool:
        self.create_database_if_not_exists()
        return self.check_database_connection()


def main() -> int:
    """命令行入口：仅执行 MySQL 建库和连接检查。"""
    configure_logging("maintenance", current_environment(), logging.INFO)
    print("CausalAgent 数据库初始化引导")
    try:
        bootstrap = DatabaseBootstrap()
        if not bootstrap.bootstrap():
            raise RuntimeError("database readiness failed")
    except Exception as exc:
        log_event(
            LOGGER,
            "maintenance.startup.failed",
            details={
                "phase": "mysql_bootstrap",
                "dependency": "mysql",
                "reason_code": "initialization_failed",
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        print("\n数据库初始化引导失败，请查看 stderr 中的结构化日志。")
        return 1

    log_event(
        LOGGER,
        "maintenance.startup.ready",
    )
    print("\n数据库已存在且连接可用。")
    print("\n如需完成 MySQL 和 PostgreSQL 的完整初始化，请执行：")
    print("  python -m Database.bootstrap")
    print("\n说明：业务表结构由 Alembic 迁移维护，本脚本不再创建或修改业务表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
