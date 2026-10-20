import os
import stat
import tempfile
from pathlib import Path

import pytest


# 测试使用的密钥文件：与部署一致，密码、令牌和签名密钥只从文件读取。
_SECRET_VALUES = {
    "MYSQL_ROOT_PASSWORD": "unit-test-root-password",
    "MYSQL_PASSWORD": "unit-test-password",
    "MYSQL_WRITE_PASSWORD": "unit-test-writer-password",
    "MYSQL_READ_PASSWORD": "unit-test-reader-password",
    "MYSQL_REPLICA_STATUS_PASSWORD": "unit-test-replica-status-password",
    "MYSQL_REPLICATION_PASSWORD": "unit-test-replication-password",
    "CHECKPOINT_POSTGRES_PASSWORD": "unit-test-checkpoint-password",
    "SECRET_KEY": "unit-test-secret",
    "API_KEY": "unit-test-api-key",
    "DEEP_AGENT_API_KEY": "unit-test-deep-agent-api-key",
    "VISION_API_KEY": "unit-test-vision-api-key",
    "EMBEDDING_API_KEY": "unit-test-embedding-api-key",
    "SEARXNG_SECRET": "unit-test-searxng-secret",
    "GRAFANA_ADMIN_PASSWORD": "unit-test-grafana-password",
    "CAUSAL_MCP_SERVICE_TOKEN": "unit-test-mcp-service-token",
    "CAUSAL_MCP_SIGNING_KEY_CURRENT": "unit-test-mcp-signing-key",
}


def _write_secret_file(directory: Path, values: dict[str, str]) -> Path:
    """写入权限为 0600 的密钥文件，并返回路径。"""
    path = directory / "causalagent.env"
    path.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


_SECRET_DIRECTORY = Path(tempfile.mkdtemp(prefix="causalagent-test-secrets-"))
SECRET_FILE_PATH = _write_secret_file(_SECRET_DIRECTORY, _SECRET_VALUES)

# 默认 pytest 范围（unit、离线 integration 和 contracts）需要占位配置；真实环境
# 入口（e2e、acceptance、smoke）必须使用自己的凭据，不能继承这里的离线设置。
EXPLICIT_ENVIRONMENT_ROOTS = ("tests/e2e", "tests/acceptance", "tests/smoke")


def _selected_roots(config) -> tuple[str, ...]:
    """返回本次 pytest 明确选择的路径，忽略选项和测试节点。"""
    roots: list[str] = []
    for argument in config.args:
        # 只接受文件系统路径；-m、-k、-p 等选项及其取值不参与目录判断。
        if not argument or argument.startswith("-") or not Path(argument).exists():
            continue
        candidate = argument.split("::", 1)[0].replace("\\", "/")
        candidate = candidate.removeprefix("./")
        roots.append(candidate.rstrip("/"))
    return tuple(roots)


def _uses_explicit_environment(config) -> bool:
    """只有全部选择都落在真实环境目录时才跳过离线配置。"""
    roots = _selected_roots(config)
    if not roots:
        return False
    return all(root.startswith(EXPLICIT_ENVIRONMENT_ROOTS) for root in roots)


def _install_offline_environment() -> None:
    """安装占位密钥文件并关闭 tracing。"""
    os.environ["CAUSALAGENT_SECRET_FILE"] = str(SECRET_FILE_PATH)
    os.environ["LANGCHAIN_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ.pop("LANGCHAIN_API_KEY", None)
    os.environ.pop("LANGSMITH_API_KEY", None)


def pytest_configure(config):
    """默认范围安装离线配置；显式真实环境入口不继承它。"""
    if _uses_explicit_environment(config):
        return
    _install_offline_environment()


@pytest.fixture
def secret_file_path() -> Path:
    """返回测试密钥文件路径，供需要显式构造密钥文件的用例使用。"""
    _install_offline_environment()
    return SECRET_FILE_PATH


@pytest.fixture
def write_secret_file(tmp_path: Path):
    """返回一个写入临时密钥文件的工厂，供缺失键和格式错误用例使用。"""
    _install_offline_environment()

    def _write(values: dict[str, str]) -> Path:
        return _write_secret_file(tmp_path, values)

    return _write
