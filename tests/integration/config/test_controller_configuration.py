"""完整控制服务的真实导入检查，需要后端镜像中的运行依赖。"""

import os
import secrets
import json
from pathlib import Path

import pytest

from tests.support.configuration_import import run_configuration_import


def _database_file(tmp_path):
    path = tmp_path / "database.env"
    keys = ("MYSQL_PASSWORD", "MYSQL_WRITE_PASSWORD", "MYSQL_READ_PASSWORD")
    path.write_text("".join(f"{name}=database-{index}\n" for index, name in enumerate(keys)), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def test_controller_imports_with_only_three_database_secrets_and_no_model_configuration(tmp_path):
    result = run_configuration_import(
        _database_file(tmp_path),
        "import sys; import sandbox_controller.__main__; "
        "from config.database_settings import database_settings; "
        "assert database_settings.MYSQL_PASSWORD; "
        "assert database_settings.MYSQL_WRITE_PASSWORD; "
        "assert database_settings.MYSQL_READ_PASSWORD; "
        "assert database_settings.MYSQL_REPLICA_STATUS_PASSWORD is None; "
        "assert 'config.settings' not in sys.modules; print('controller-imported')",
    )
    assert result.returncode == 0, result.stderr
    assert "controller-imported" in result.stdout


def test_app_import_rejects_database_only_file_after_controller_import(tmp_path):
    result = run_configuration_import(
        _database_file(tmp_path),
        "import sandbox_controller.__main__; print('controller-imported', flush=True); import config.settings",
    )
    assert "controller-imported" in result.stdout
    assert result.returncode != 0
    assert "SecretFileError" in result.stderr
    assert "API_KEY" in result.stderr


def _shared_file(tmp_path, *, missing=None):
    path = _database_file(tmp_path)
    values = {
        "API_KEY": secrets.token_hex(32),
        "CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN": secrets.token_hex(32),
        "CAUSALAGENT_SANDBOX_API_KEY": secrets.token_hex(32),
    }
    values.pop(missing, None)
    with path.open("a", encoding="utf-8") as output:
        output.write("".join(f"{name}={value}\n" for name, value in values.items()))
    return path


def test_management_components_read_same_file_without_model_configuration(tmp_path):
    path = _shared_file(tmp_path)
    source = (
        "import os, sys; import sandbox_controller.__main__; "
        "from sandbox_controller.config import ControllerSettings; "
        "from Agent.data_processing.sandbox_client import SandboxControllerSettings; "
        "from deploy.opensandbox.server_entrypoint import load_secrets; "
        "controller = ControllerSettings.from_environment(); "
        "worker = SandboxControllerSettings.from_environment(); "
        "upstream = load_secrets(required_keys=('CAUSALAGENT_SANDBOX_API_KEY',)); "
        "assert worker.token == controller.token; "
        "assert controller.remote.api_key == upstream['CAUSALAGENT_SANDBOX_API_KEY']; "
        "assert controller.token != controller.remote.api_key; "
        "assert not any(key in os.environ for key in upstream); "
        "assert 'config.settings' not in sys.modules; print('shared-secrets-loaded')"
    )
    lock = json.loads((Path(__file__).resolve().parents[3] / "deploy/opensandbox/release-lock.json").read_text())
    result = run_configuration_import(
        path, source, CAUSALAGENT_SANDBOX_IMAGE=lock["images"]["interpreter_base"],
        CAUSALAGENT_SANDBOX_URL="https://sandbox.example.invalid",
    )
    assert result.returncode == 0, result.stderr
    assert "shared-secrets-loaded" in result.stdout


@pytest.mark.parametrize("missing", ["CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN", "CAUSALAGENT_SANDBOX_API_KEY"])
def test_controller_requires_its_authentication_fields_in_shared_file(tmp_path, missing):
    result = run_configuration_import(
        _shared_file(tmp_path, missing=missing),
        "from sandbox_controller.config import ControllerSettings; ControllerSettings.from_environment()",
    )
    assert result.returncode != 0
    assert "SecretFileError" in result.stderr
    assert missing in result.stderr
