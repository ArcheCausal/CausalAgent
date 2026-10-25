"""控制服务客户端的纯配置边界，不启动替代 HTTP 服务或沙盒。"""

import secrets

import pytest

from Agent.data_processing.sandbox_client import SandboxControllerSettings
from sandbox_controller.protocol import SandboxProtocolError


def _environment(tmp_path):
    path = tmp_path / "controller.token"
    token = secrets.token_hex(32)
    path.write_text(token + "\n", encoding="utf-8")
    return {"CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN_FILE": str(path)}, token


def test_controller_uses_raw_token_file_and_default_port(tmp_path):
    environment, token = _environment(tmp_path)

    settings = SandboxControllerSettings.from_environment(environment)

    assert settings.controller_url == "http://sandbox-controller:8081"
    assert settings.token == token
    assert token not in repr(settings)
    assert settings.max_output_file_bytes == 64 * 1024 * 1024
    assert settings.execution_wait_seconds == 370


def test_controller_token_file_is_required():
    with pytest.raises(ValueError, match="TOKEN_FILE must be configured"):
        SandboxControllerSettings.from_environment({})


@pytest.mark.parametrize("content", ["", "short", "x" * 32 + " " + "y" * 32, "x" * 4097])
def test_controller_rejects_invalid_raw_token_files(tmp_path, content):
    environment, _ = _environment(tmp_path)
    path = tmp_path / "controller.token"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(SandboxProtocolError):
        SandboxControllerSettings.from_environment(environment)


@pytest.mark.parametrize("url", [
    "ftp://controller:8081", "http://user:password@controller:8081",
    "http://controller:8081/v1", "http://controller:8081?token=secret",
    "http://controller:8081#fragment", "http://", "http://controller:bad",
    "http://controller:0",
])
def test_controller_rejects_non_origin_urls(tmp_path, url):
    environment, _ = _environment(tmp_path)
    environment["CAUSALAGENT_SANDBOX_CONTROLLER_URL"] = url

    with pytest.raises(ValueError):
        SandboxControllerSettings.from_environment(environment)


@pytest.mark.parametrize("name", [
    "CONTROLLER_TIMEOUT_SECONDS", "COMMAND_TIMEOUT_SECONDS", "MAX_CONCURRENT_PER_WORKER",
    "GLOBAL_CAPACITY_WAIT_SECONDS", "MAX_INPUT_BYTES", "MAX_OUTPUT_FILE_BYTES",
    "MAX_PREVIEW_BYTES", "MAX_COMMAND_BYTES", "MAX_COMMAND_OUTPUT_BYTES",
])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "invalid"])
def test_controller_rejects_invalid_limits(tmp_path, name, value):
    environment, _ = _environment(tmp_path)
    environment["CAUSALAGENT_SANDBOX_" + name] = value

    with pytest.raises(ValueError):
        SandboxControllerSettings.from_environment(environment)


def test_controller_accepts_explicit_limits(tmp_path):
    environment, _ = _environment(tmp_path)
    options = {
        "CONTROLLER_TIMEOUT_SECONDS": "2.5", "COMMAND_TIMEOUT_SECONDS": "5",
        "MAX_CONCURRENT_PER_WORKER": "3", "GLOBAL_CAPACITY_WAIT_SECONDS": "4",
        "MAX_INPUT_BYTES": "4096", "MAX_OUTPUT_FILE_BYTES": "8192",
        "MAX_PREVIEW_BYTES": "1024", "MAX_COMMAND_BYTES": "2048",
        "MAX_COMMAND_OUTPUT_BYTES": "512",
    }
    environment.update({"CAUSALAGENT_SANDBOX_" + name: value for name, value in options.items()})

    settings = SandboxControllerSettings.from_environment(environment)

    assert settings.timeout_seconds == 2.5
    assert settings.command_timeout_seconds == 5
    assert settings.max_concurrent_per_worker == 3
    assert settings.global_capacity_wait_seconds == 4
    assert settings.max_input_bytes == 4096
    assert settings.max_output_file_bytes == 8192
    assert settings.max_preview_bytes == 1024
    assert settings.max_command_bytes == 2048
    assert settings.max_command_output_bytes == 512
    assert settings.execution_wait_seconds == 20
