"""OpenSandbox 的配置边界必须拒绝可变容器镜像标签。"""

import pytest

from Agent.data_processing.opensandbox_runtime import (
    OpenSandboxConfigurationError,
    OpenSandboxSettings,
    _sandbox_capacity_lease_seconds,
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


def test_capacity_lease_covers_the_maximum_sandbox_lifetime_after_worker_loss() -> None:
    settings = _settings(
        "registry.example.invalid/causalagent/data-preparation@sha256:" + "a" * 64
    )

    lease_seconds = _sandbox_capacity_lease_seconds(settings)

    assert lease_seconds == settings.sandbox_ttl_seconds + 2 * settings.api_timeout_seconds
    assert lease_seconds > settings.sandbox_ttl_seconds
