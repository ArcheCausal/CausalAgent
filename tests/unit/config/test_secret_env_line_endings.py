"""用真实 POSIX Shell 验证密钥读取器的 LF/CRLF 行边界。"""

import os
from pathlib import Path
import secrets
import subprocess

import pytest


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_shell_secret_reader_excludes_the_line_separator(tmp_path, newline):
    password = secrets.token_urlsafe(32)
    path = tmp_path / "credentials"
    path.write_bytes(b"MYSQL_PASSWORD=" + password.encode() + newline)
    reader = Path(__file__).resolve().parents[3] / "deploy/scripts/secret-env.sh"
    environment = dict(os.environ, CAUSALAGENT_SECRET_FILE=str(path))
    result = subprocess.run(
        ["sh", "-c", '. "$1"; secret_value MYSQL_PASSWORD', "_", str(reader)],
        env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert secrets.compare_digest(result.stdout, password)
    assert not result.stderr


def test_shell_secret_reader_rejects_an_empty_crlf_value(tmp_path):
    path = tmp_path / "credentials"
    path.write_bytes(b"MYSQL_PASSWORD=\r\n")
    reader = Path(__file__).resolve().parents[3] / "deploy/scripts/secret-env.sh"
    result = subprocess.run(
        ["sh", "-c", '. "$1"; secret_value MYSQL_PASSWORD', "_", str(reader)],
        env=dict(os.environ, CAUSALAGENT_SECRET_FILE=str(path)),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert not result.stdout
