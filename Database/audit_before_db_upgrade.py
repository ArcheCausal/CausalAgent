"""
数据库生产化升级前审计脚本。

只读检查，不修改数据。若发现 FAIL 项，应先修复数据再执行 alembic upgrade head。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import mysql.connector

from observability.logging_runtime import configure_logging, current_environment

if __name__ == "__main__":
    configure_logging("maintenance", current_environment(), logging.INFO)

try:
    from Database.inspection import execute_migration_preflight_checks
except ModuleNotFoundError:  # 兼容 `python Database/audit_before_db_upgrade.py`
    from inspection import execute_migration_preflight_checks

def load_env() -> None:
    """加载服务器密钥文件；读取密码等敏感配置。"""
    from config.secret_file import load_secrets

    load_secrets()


def get_connection():
    load_env()
    host = os.environ.get("MYSQL_WRITE_HOST") or os.environ.get("MYSQL_HOST")
    user = os.environ.get("MYSQL_READ_USER") or os.environ.get("MYSQL_USER")
    from config.secret_file import require_secret

    password = require_secret("MYSQL_READ_PASSWORD")
    required = {
        "MYSQL_HOST 或 MYSQL_WRITE_HOST": host,
        "MYSQL_READ_USER 或 MYSQL_USER": user,
        "MYSQL_DATABASE": os.environ.get("MYSQL_DATABASE"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"缺少数据库环境变量: {missing}")

    return mysql.connector.connect(
        host=host,
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=user,
        password=password,
        database=os.environ["MYSQL_DATABASE"],
    )


def audit() -> list[dict]:
    """按当前 schema 在主库只读连接上执行迁移前置审计。"""
    with get_connection() as conn:
        timeout_ms = int(os.environ.get("DB_INSPECTION_QUERY_TIMEOUT_MS", "3000"))
        if timeout_ms <= 0:
            raise RuntimeError("DB_INSPECTION_QUERY_TIMEOUT_MS 必须大于 0")
        return execute_migration_preflight_checks(
            conn,
            timeout_ms=timeout_ms,
            source_role="primary",
            source_alias="primary",
        )


def main() -> int:
    """输出迁移前审计结果，并在阻塞项非零或检查未知时拒绝升级。"""
    configure_logging("maintenance", current_environment(), logging.INFO)
    failures = 0
    for check in audit():
        if check["status"] == "unknown":
            status = "UNKNOWN"
            failures += 1
        elif check["severity"] == "blocking" and check["status"] == "error":
            status = "FAIL"
            failures += 1
        else:
            status = "PASS"
        logging.info(
            "%s | %s | count=%s | source=%s",
            status,
            check["label"],
            check["value"],
            check["source_alias"],
        )

    if failures:
        logging.error("审计未通过：发现 %s 类阻塞问题。请先修复数据。", failures)
        return 1
    logging.info("审计通过，可以继续执行 Alembic 迁移。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
