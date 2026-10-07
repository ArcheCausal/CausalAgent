from __future__ import annotations

import sys
from pathlib import Path

import pytest

from causalagent_desktop.config import (
    ConfigError,
    DEFAULT_DEVELOPMENT_URL,
    DEFAULT_DEVELOPER_PREVIEW_URL,
    DEFAULT_RELEASE_ORIGIN,
    DesktopMode,
    build_config,
    normalize_origin,
    normalize_url,
)


def _environment(tmp_path: Path, **values: str) -> dict[str, str]:
    environment = {"LOCALAPPDATA": str(tmp_path)}
    environment.update(values)
    return environment


def test_url_precedence_is_cli_then_environment_then_default(tmp_path: Path) -> None:
    environment = _environment(
        tmp_path,
        CAUSALAGENT_DESKTOP_URL="http://localhost:5001/from-env",
    )

    cli_config = build_config(
        ["--url", "http://127.0.0.1:5001/from-cli"],
        environ=environment,
    )
    env_config = build_config(environ=environment)
    default_config = build_config(environ=_environment(tmp_path))

    assert cli_config.url == "http://127.0.0.1:5001/from-cli"
    assert env_config.url == "http://localhost:5001/from-env"
    assert default_config.url == DEFAULT_DEVELOPMENT_URL


def test_development_allows_both_local_flask_origins(tmp_path: Path) -> None:
    for host in ("127.0.0.1", "localhost"):
        config = build_config(
            ["--url", f"http://{host}:5001/"],
            environ=_environment(tmp_path),
        )
        assert config.mode is DesktopMode.DEVELOPMENT
        assert f"http://{host}:5001" in config.allowed_origins


def test_developer_preview_defaults_to_loopback_and_forces_debug_off(tmp_path: Path) -> None:
    config = build_config(
        ["--mode", "developer-preview", "--debug"],
        environ=_environment(tmp_path, CAUSALAGENT_DESKTOP_DEBUG="true"),
    )

    assert config.mode is DesktopMode.DEVELOPER_PREVIEW
    assert config.url == DEFAULT_DEVELOPER_PREVIEW_URL
    assert config.allowed_origins == frozenset(
        {
            "http://127.0.0.1:5001",
            "http://localhost:5001",
        }
    )
    assert config.debug is False


def test_developer_preview_accepts_only_http_loopback_origins(tmp_path: Path) -> None:
    config = build_config(
        ["--mode", "developer-preview", "--url", "http://[::1]:5317/chat"],
        environ=_environment(tmp_path),
    )
    assert config.url == "http://[::1]:5317/chat"
    assert "http://[::1]:5317" in config.allowed_origins

    for unsafe_url in (
        "https://causalagent.example.com/",
        "http://192.168.1.10:5001/",
        "http://0.0.0.0:5001/",
    ):
        with pytest.raises(ConfigError):
            build_config(
                ["--mode", "developer-preview", "--url", unsafe_url],
                environ=_environment(tmp_path),
            )


def test_source_developer_preview_does_not_enable_test_autoclose(tmp_path: Path) -> None:
    config = build_config(
        ["--mode", "developer-preview"],
        environ=_environment(
            tmp_path,
            CAUSALAGENT_DESKTOP_TEST_AUTOCLOSE_SECONDS="1",
        ),
    )

    assert config.test_autoclose_seconds is None


def test_release_uses_https_origin_and_forces_debug_off(tmp_path: Path) -> None:
    config = build_config(
        ["--mode", "release", "--debug"],
        environ=_environment(
            tmp_path,
            CAUSALAGENT_DESKTOP_DEBUG="true",
        ),
    )

    assert config.mode is DesktopMode.RELEASE
    assert config.url == f"{DEFAULT_RELEASE_ORIGIN}/dashboard"
    assert config.allowed_origins == frozenset({DEFAULT_RELEASE_ORIGIN})
    assert config.debug is False
    assert config.storage_path == tmp_path / "CausalAgent" / "WebView"


def test_development_debug_can_be_enabled_explicitly(tmp_path: Path) -> None:
    config = build_config(
        ["--debug"],
        environ=_environment(tmp_path),
    )

    assert config.mode is DesktopMode.DEVELOPMENT
    assert config.debug is True


def test_release_accepts_only_the_preconfigured_https_origin(tmp_path: Path) -> None:
    environment = _environment(
        tmp_path,
        CAUSALAGENT_DESKTOP_MODE="release",
        CAUSALAGENT_DESKTOP_RELEASE_ORIGIN="https://prod.example.test",
    )
    accepted = build_config(
        ["--url", "https://prod.example.test/chat"],
        environ=environment,
    )
    assert accepted.url == "https://prod.example.test/chat"

    with pytest.raises(ConfigError):
        build_config(["--url", "http://prod.example.test/chat"], environ=environment)
    with pytest.raises(ConfigError):
        build_config(["--url", "https://other.example.test/chat"], environ=environment)


def test_frozen_release_without_channel_marker_keeps_https_safe_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root = tmp_path / "bundle"
    release_data = bundle_root / "causalagent_desktop"
    release_data.mkdir(parents=True)
    (release_data / "release_origin.txt").write_text(
        "https://release.example.test\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    config = build_config(
        ["--url", "https://release.example.test/chat", "--debug"],
        environ=_environment(
            tmp_path,
            CAUSALAGENT_DESKTOP_URL="http://127.0.0.1:5001/",
            CAUSALAGENT_DESKTOP_MODE="development",
        ),
    )

    assert config.mode is DesktopMode.RELEASE
    assert config.release_origin == "https://release.example.test"
    assert config.url == "https://release.example.test/chat"
    assert config.debug is False


def test_frozen_developer_preview_uses_embedded_channel_and_loopback_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root = tmp_path / "bundle"
    release_data = bundle_root / "causalagent_desktop"
    release_data.mkdir(parents=True)
    (release_data / "desktop_mode.txt").write_text("developer-preview\n", encoding="utf-8")
    (release_data / "release_origin.txt").write_text(
        "https://release.example.test\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    config = build_config(
        ["--mode", "release", "--debug"],
        environ=_environment(
            tmp_path,
            CAUSALAGENT_DESKTOP_MODE="release",
            CAUSALAGENT_DESKTOP_DEBUG="true",
            CAUSALAGENT_DESKTOP_TEST_AUTOCLOSE_SECONDS="1",
        ),
    )

    assert config.mode is DesktopMode.DEVELOPER_PREVIEW
    assert config.url == DEFAULT_DEVELOPER_PREVIEW_URL
    assert config.allowed_origins == frozenset(
        {
            "http://127.0.0.1:5001",
            "http://localhost:5001",
        }
    )
    assert config.debug is False
    assert config.test_autoclose_seconds is None


def test_frozen_developer_preview_rejects_non_loopback_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root = tmp_path / "bundle"
    release_data = bundle_root / "causalagent_desktop"
    release_data.mkdir(parents=True)
    (release_data / "desktop_mode.txt").write_text("developer-preview\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    with pytest.raises(ConfigError):
        build_config(
            ["--url", "https://causalagent.example.com/"],
            environ=_environment(tmp_path),
        )


def test_frozen_package_rejects_unknown_channel_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root = tmp_path / "bundle"
    release_data = bundle_root / "causalagent_desktop"
    release_data.mkdir(parents=True)
    (release_data / "desktop_mode.txt").write_text("unknown\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    with pytest.raises(ConfigError):
        build_config(environ=_environment(tmp_path))


def test_development_rejects_non_local_configured_origin(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_config(
            ["--url", "https://causalagent.example.com/"],
            environ=_environment(tmp_path),
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("HTTP://LOCALHOST:5001", "http://localhost:5001/"),
        ("https://CAUSALAGENT.EXAMPLE.COM:443/chat#section", "https://causalagent.example.com/chat#section"),
        ("http://127.0.0.1:5001/?tab=chat", "http://127.0.0.1:5001/?tab=chat"),
    ],
)
def test_url_normalization(source: str, expected: str) -> None:
    assert normalize_url(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "file:///C:/secret.txt",
        "javascript:alert(1)",
        "custom-scheme://host/path",
        "https://user:password@example.com/",
        "https://[invalid/",
    ],
)
def test_non_http_schemes_and_unsafe_urls_are_rejected(source: str) -> None:
    with pytest.raises(ConfigError):
        normalize_url(source)


def test_origin_normalization_removes_default_port() -> None:
    assert normalize_origin("https://CAUSALAGENT.EXAMPLE.COM:443/") == DEFAULT_RELEASE_ORIGIN
