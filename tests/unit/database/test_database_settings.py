"""验证数据库配置、密钥文件与应用/模型配置的导入边界。"""

from __future__ import annotations

import os
import stat
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_CONFIG_NAMES = ("SECRET_KEY", "API_KEY", "BASE_URL", "MODEL")
SECRET_KEYS_FOR_TESTS = (
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
DATABASE_ENV = {
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_DATABASE": "test-database",
    "MYSQL_READ_HOSTS": "",
    # 非密钥配置继续来自环境变量；密钥只来自密钥文件。
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
}


def _write_secret_file(path: Path, keys=SECRET_KEYS_FOR_TESTS) -> Path:
    """写入一个包含所需键的密钥文件，并设置 0600 权限。"""
    path.write_text(
        "".join(f"{name}=test-value-for-{name}\n" for name in keys),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def _run_python(
    source: str,
    *,
    tmp_path: Path,
    secret_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in MODEL_CONFIG_NAMES:
        env.pop(name, None)
    env.update(DATABASE_ENV)
    if secret_file is not None:
        env["CAUSALAGENT_SECRET_FILE"] = str(secret_file)
    else:
        env.pop("CAUSALAGENT_SECRET_FILE", None)
        env["CAUSALAGENT_SECRET_FILE"] = str(tmp_path / "absent.env")
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_app_db_import_does_not_initialize_full_app_config(tmp_path) -> None:
    secret_file = _write_secret_file(tmp_path / "causalagent.env")
    result = _run_python(
        """
import sys

import app.db
from config.database_settings import database_settings

assert "config.settings" not in sys.modules
assert database_settings.MYSQL_DATABASE == "test-database"
""",
        tmp_path=tmp_path,
        secret_file=secret_file,
    )

    assert result.returncode == 0, result.stderr


def test_missing_secret_file_keeps_model_configuration_fail_fast(tmp_path) -> None:
    result = _run_python(
        """
from config.settings import AppConfig

AppConfig()
""",
        tmp_path=tmp_path,
    )

    assert result.returncode != 0
    assert "密钥文件" in result.stderr


def test_secret_file_supplies_model_and_database_credentials(tmp_path) -> None:
    secret_file = _write_secret_file(tmp_path / "causalagent.env")
    result = _run_python(
        """
from config.settings import AppConfig
from config.database_settings import database_settings

config = AppConfig()
assert config.API_KEY == "test-value-for-API_KEY"
assert config.SECRET_KEY == "test-value-for-SECRET_KEY"
assert database_settings.MYSQL_WRITE_PASSWORD == "test-value-for-MYSQL_WRITE_PASSWORD"
""",
        tmp_path=tmp_path,
        secret_file=secret_file,
    )

    assert result.returncode == 0, result.stderr
