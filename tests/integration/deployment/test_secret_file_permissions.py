"""使用真实文件与子进程验证 Python、Shell 的开发及服务器权限边界。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="需要 Linux 文件权限和 sh")


def _run_reader(driver: str, path: Path, environment: str | None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("ENVIRONMENT", None)
    if environment is not None:
        env["ENVIRONMENT"] = environment
    env["CAUSALAGENT_SECRET_FILE"] = str(path)
    if driver == "shell":
        command = [
            "sh", "-c", '. "$1"; require_secret_file_permissions',
            "secret-permissions", str(REPO_ROOT / "deploy/scripts/secret-env.sh"),
        ]
    else:
        command = [
            sys.executable, "-c",
            "from config.secret_file import load_secrets, SecretFileError; "
            "from pathlib import Path; import sys\n"
            "try:\n    load_secrets(path=Path(sys.argv[1]))\n"
            "except SecretFileError:\n    sys.exit(23)\n",
            str(path),
        ]
    return subprocess.run(command, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("driver", ["python", "shell"])
@pytest.mark.parametrize("environment", [None, "development", "staging", "production"])
@pytest.mark.parametrize("mode", [0o600, 0o777])
def test_real_file_permissions_follow_environment(tmp_path, driver, environment, mode):
    from config.secret_file import REQUIRED_SECRET_KEYS

    path = tmp_path / "causalagent.env"
    path.write_text(
        "".join(f"{key}=permission-probe-{index}\n" for index, key in enumerate(REQUIRED_SECRET_KEYS)),
        encoding="utf-8",
    )
    path.chmod(mode)

    result = _run_reader(driver, path, environment)

    assert (result.returncode == 0) == (environment == "development" or mode == 0o600)


@pytest.mark.parametrize("driver", ["python", "shell"])
@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_development_still_requires_a_regular_file(tmp_path, driver, environment, kind):
    path = tmp_path / "causalagent.env"
    if kind == "directory":
        path.mkdir()

    result = _run_reader(driver, path, environment)

    assert result.returncode != 0


@pytest.mark.parametrize("content", [
    "SECRET_KEY=\n",
    "UNKNOWN_KEY=value\n",
    "SECRET_KEY=first\nSECRET_KEY=second\n",
    "not-an-assignment\n",
    "SECRET_KEY=only-one-required-key\n",
])
def test_development_keeps_python_secret_content_validation(tmp_path, content):
    path = tmp_path / "causalagent.env"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o777)

    result = _run_reader("python", path, "development")

    assert result.returncode == 23
