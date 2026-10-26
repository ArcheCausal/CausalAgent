"""完整控制服务的真实导入检查，需要后端镜像中的运行依赖。"""

import os

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
