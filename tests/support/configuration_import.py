"""在独立 Python 进程中检查配置导入，不连接数据库或模型。"""

import os
from pathlib import Path
import subprocess
import sys

from config.secret_file import SECRET_KEYS


def run_configuration_import(path: Path, source: str, **overrides: str):
    environment = os.environ.copy()
    for name in (*SECRET_KEYS, "BASE_URL", "MODEL", "DEEP_AGENT_BASE_URL", "DEEP_AGENT_MODEL"):
        environment.pop(name, None)
    environment.update({
        "CAUSALAGENT_SECRET_FILE": str(path),
        "MYSQL_HOST": "127.0.0.1", "MYSQL_WRITE_HOST": "127.0.0.1", "MYSQL_READ_HOSTS": "",
        "MYSQL_USER": "configuration_user", "MYSQL_WRITE_USER": "configuration_writer",
        "MYSQL_READ_USER": "configuration_reader", "MYSQL_DATABASE": "configuration_test",
    })
    environment.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[2],
        env=environment, capture_output=True, text=True, timeout=60,
    )
