"""验证数据库配置与应用/模型配置的导入边界。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_CONFIG_NAMES = ("SECRET_KEY", "API_KEY", "BASE_URL", "MODEL")
DATABASE_ENV = {
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "test-database",
    "MYSQL_READ_HOSTS": "",
}


def _run_python(source: str, *, include_secret_key: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in MODEL_CONFIG_NAMES:
        env.pop(name, None)
    if include_secret_key:
        env["SECRET_KEY"] = "test-secret"
    env.update(DATABASE_ENV)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_app_db_import_does_not_initialize_full_app_config() -> None:
    result = _run_python(
        """
import sys
import types

dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda *_args, **_kwargs: None
sys.modules["dotenv"] = dotenv

import app.db
from config.database_settings import database_settings

assert "config.settings" not in sys.modules
assert database_settings.MYSQL_DATABASE == "test-database"
"""
    )

    assert result.returncode == 0, result.stderr


def test_app_config_keeps_model_configuration_fail_fast() -> None:
    result = _run_python(
        """
import sys
import types

dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda *_args, **_kwargs: None
sys.modules["dotenv"] = dotenv

from config.settings import AppConfig

AppConfig()
""",
        include_secret_key=True,
    )

    assert result.returncode != 0
    assert "API_KEY" in result.stderr
