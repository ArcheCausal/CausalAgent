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
# 在收集测试模块之前就安装密钥文件，避免 import 阶段的配置读取落空。
os.environ["CAUSALAGENT_SECRET_FILE"] = str(SECRET_FILE_PATH)


def pytest_configure(config):
    """在测试进程中默认关闭 LangChain/LangSmith tracing，并安装测试密钥文件。"""
    os.environ["LANGCHAIN_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ.pop("LANGCHAIN_API_KEY", None)
    os.environ.pop("LANGSMITH_API_KEY", None)


@pytest.fixture
def secret_file_path() -> Path:
    """返回测试密钥文件路径，供需要显式构造密钥文件的用例使用。"""
    return SECRET_FILE_PATH


@pytest.fixture
def write_secret_file(tmp_path: Path):
    """返回一个写入临时密钥文件的工厂，供缺失键和格式错误用例使用。"""

    def _write(values: dict[str, str]) -> Path:
        return _write_secret_file(tmp_path, values)

    return _write
