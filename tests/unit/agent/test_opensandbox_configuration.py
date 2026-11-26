"""OpenSandbox 的配置边界必须拒绝可变容器镜像标签。"""

import pytest

from Agent.data_processing.opensandbox_runtime import (
    OpenSandboxConfigurationError,
    OpenSandboxSettings,
)


def _settings(image: str) -> OpenSandboxSettings:
    return OpenSandboxSettings(
        api_url="https://sandbox.example.invalid",
        api_key="configuration-test-key",
        image=image,
    )


def test_opensandbox_requires_an_immutable_sha256_image_reference() -> None:
    _settings("registry.example.invalid/causalagent/data-preparation@sha256:" + "a" * 64)

    with pytest.raises(OpenSandboxConfigurationError):
        _settings("registry.example.invalid/causalagent/data-preparation:latest")

    with pytest.raises(OpenSandboxConfigurationError):
        _settings("registry.example.invalid/causalagent/data-preparation@sha256:replace-me")
