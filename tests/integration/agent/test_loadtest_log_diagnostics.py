"""验证真实 JSON 日志运行时到压测核查解析器的诊断与脱敏边界，不访问 Loki。"""

from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "loadtest"))

from observability.logging_runtime import JsonLogFormatter, log_context, log_event
from runner.logs import _parse_job_row


def _failure_log() -> tuple[str, str]:
    stream = io.StringIO()
    logger = logging.Logger("loadtest-diagnostic-regression", level=logging.ERROR)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter("worker", "test"))
    logger.addHandler(handler)
    job_id = str(uuid4())
    with log_context(job_id=job_id):
        try:
            int("private-error-value")
        except ValueError:
            log_event(
                logger,
                "worker.job.failed",
                details={
                    "failure_phase": "graph_terminal",
                    "reason_code": "node_error",
                    "error_category": "internal_error",
                    "attempt": 1,
                    "duration_ms": 1,
                },
                exc_info=True,
            )
    handler.close()
    return job_id, stream.getvalue().strip()


def test_logcheck_retains_existing_failure_diagnostics_without_exception_text():
    job_id, line = _failure_log()
    payload = json.loads(line)
    parsed = _parse_job_row({"timestamp": str(time.time_ns()), "line": line}, {job_id})

    assert parsed is not None
    assert parsed["exception_type"] == "ValueError"
    assert parsed["stack_top"] == payload["stack"][-1]
    assert parsed["details"]["failure_phase"] == "graph_terminal"
    assert parsed["details"]["error_category"] == "internal_error"
    assert parsed["details"]["reason_code"] == "node_error"
    assert "private-error-value" not in json.dumps(parsed)
    assert "invalid literal" not in line


def test_logcheck_does_not_attribute_another_jobs_failure():
    _, line = _failure_log()
    assert _parse_job_row({"timestamp": str(time.time_ns()), "line": line}, {str(uuid4())}) is None


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("decoration", ["plain", "quoted", "spaced"])
def test_grafana_credentials_match_the_real_shell_reader(tmp_path, newline, decoration):
    root = Path(__file__).resolve().parents[3]
    password = secrets.token_urlsafe(32)
    if decoration == "quoted":
        password = '"' + password + '"'
    elif decoration == "spaced":
        password = " " + password + " "
    path = tmp_path / "credentials"
    path.write_bytes(b"GRAFANA_ADMIN_PASSWORD=" + password.encode() + newline)
    environment = dict(
        os.environ, CAUSALAGENT_SECRET_FILE=str(path),
        GRAFANA_ADMIN_USER="credential-validation", GRAFANA_ADMIN_PASSWORD="",
    )
    shell = subprocess.run(
        ["sh", "-c", '. "$1"; secret_value GRAFANA_ADMIN_PASSWORD',
         "_", str(root / "deploy/scripts/secret-env.sh")],
        env=environment, capture_output=True, text=True, check=False,
    )
    assert shell.returncode == 0
    assert secrets.compare_digest(shell.stdout, password)
    script = (
        "import secrets,sys; from runner.logs import read_grafana_credentials; "
        "user,password=read_grafana_credentials(); "
        "sys.exit(0 if user=='credential-validation' "
        "and secrets.compare_digest(password,sys.stdin.read()) else 1)"
    )
    client = subprocess.run(
        [sys.executable, "-c", script], cwd=root / "loadtest",
        env=environment, input=shell.stdout, capture_output=True, text=True, check=False,
    )
    assert client.returncode == 0
    assert not client.stdout
