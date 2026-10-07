"""桌面客户端的纯配置与 URL 规范化逻辑。

本模块不导入 pywebview、pythonnet 或任何 Win32 API，便于在没有 GUI 的环境
中执行单元测试。启动器只消费这里产出的、已经通过白名单校验的配置。
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit


DEFAULT_DEVELOPMENT_URL = "http://127.0.0.1:5001/dashboard"
DEFAULT_DEVELOPER_PREVIEW_URL = DEFAULT_DEVELOPMENT_URL
DEFAULT_RELEASE_ORIGIN = "https://causalagent.example.com"
DEVELOPMENT_ORIGINS = frozenset(
    {
        "http://127.0.0.1:5001",
        "http://localhost:5001",
    }
)
DEFAULT_STORAGE_RELATIVE_PATH = Path("CausalAgent") / "WebView"


class ConfigError(ValueError):
    """桌面配置不满足安全边界。"""


class DesktopMode(str, Enum):
    DEVELOPMENT = "development"
    DEVELOPER_PREVIEW = "developer-preview"
    RELEASE = "release"


class NavigationAction(str, Enum):
    ALLOW_INTERNAL = "allow_internal"
    OPEN_EXTERNAL = "open_external"
    BLOCK = "block"


def _split_url(value: str) -> SplitResult:
    if not isinstance(value, str):
        raise ConfigError("URL 必须是文本")

    candidate = value.strip()
    if not candidate:
        raise ConfigError("URL 不能为空")
    if any(character.isspace() for character in candidate):
        raise ConfigError("URL 不能包含空白字符")

    try:
        parsed = urlsplit(candidate)
        # Accessing hostname/port makes urlsplit validate malformed brackets and
        # ports. The values are intentionally not included in any error message.
        _ = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ConfigError("URL 格式无效") from exc

    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("URL 不允许包含用户凭据")
    return parsed


def _normalized_netloc(parsed: SplitResult) -> str:
    hostname = parsed.hostname
    if not hostname:
        raise ConfigError("URL 必须包含主机名")

    try:
        normalized_host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ConfigError("URL 主机名无效") from exc

    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"

    port = parsed.port
    if (parsed.scheme.lower(), port) in (("http", 80), ("https", 443)):
        port = None
    return normalized_host if port is None else f"{normalized_host}:{port}"


def normalize_url(value: str) -> str:
    """返回只允许 HTTP(S) 的规范 URL。

    片段保留给页面导航使用；origin 判断不会使用片段。查询参数不参与日志
    或用户可见错误信息，但会保留在实际导航 URL 中以兼容现有页面。
    """

    parsed = _split_url(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ConfigError("桌面客户端只允许 HTTP(S) URL")
    if parsed.hostname is None:
        raise ConfigError("URL 必须包含主机名")

    netloc = _normalized_netloc(parsed)
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))


def normalize_origin(value: str) -> str:
    """规范化一个不带路径、查询和片段的 HTTP(S) origin。"""

    normalized = normalize_url(value)
    parsed = urlsplit(normalized)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("origin 不能包含路径、查询参数或片段")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def origin_of(value: str) -> str:
    """返回 URL 的规范 origin；无效或非 HTTP(S) URL 会抛出 ConfigError。"""

    normalized = normalize_url(value)
    parsed = urlsplit(normalized)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def display_url(value: str) -> str:
    """生成错误页面可展示的地址，不展示查询参数或片段。"""

    normalized = normalize_url(value)
    parsed = urlsplit(normalized)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


@dataclass(frozen=True)
class NavigationDecision:
    action: NavigationAction
    normalized_url: str | None
    origin: str | None
    reason: str


class NavigationPolicy:
    """只允许已配置 origin 留在 WebView 中的导航策略。"""

    def __init__(self, allowed_origins: frozenset[str] | set[str] | Sequence[str]):
        normalized_origins = {normalize_origin(origin) for origin in allowed_origins}
        if not normalized_origins:
            raise ConfigError("至少需要一个允许的 origin")
        self.allowed_origins = frozenset(normalized_origins)

    def decide(self, value: str) -> NavigationDecision:
        try:
            normalized = normalize_url(value)
        except ConfigError:
            # Do not expose the source URL or parser details to runtime logs/UI.
            reason = "unsupported_scheme" if _scheme_of(value) not in {"http", "https"} else "invalid_url"
            return NavigationDecision(NavigationAction.BLOCK, None, None, reason)

        origin = origin_of(normalized)
        if origin in self.allowed_origins:
            return NavigationDecision(NavigationAction.ALLOW_INTERNAL, normalized, origin, "allowed_origin")
        if urlsplit(normalized).scheme == "https":
            return NavigationDecision(NavigationAction.OPEN_EXTERNAL, normalized, origin, "external_https")
        return NavigationDecision(NavigationAction.BLOCK, normalized, origin, "non_whitelisted_http")


def _scheme_of(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return urlsplit(value.strip()).scheme.lower()
    except ValueError:
        return ""


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError("布尔环境变量值无效")


def _parse_autoclose(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        seconds = float(value)
    except ValueError as exc:
        raise ConfigError("测试自动关闭时间无效") from exc
    if not 0.1 <= seconds <= 60:
        raise ConfigError("测试自动关闭时间超出范围")
    return seconds


def _is_local_development_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname is None:
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                return False
        except ValueError:
            return False
    return parsed.port is not None and 1 <= parsed.port <= 65535


@dataclass(frozen=True)
class DesktopConfig:
    mode: DesktopMode
    url: str
    allowed_origins: frozenset[str]
    debug: bool
    storage_path: Path
    release_origin: str
    title: str = "CausalAgent"
    width: int = 1280
    height: int = 820
    min_width: int = 960
    min_height: int = 640
    background_color: str = "#F7F8FA"
    icon_path: Path | None = None
    test_autoclose_seconds: float | None = None

    @property
    def policy(self) -> NavigationPolicy:
        return NavigationPolicy(self.allowed_origins)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CausalAgent Windows WebView2 desktop client")
    parser.add_argument("--url", help="覆盖桌面客户端初始 URL")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in DesktopMode],
        help="运行模式；developer-preview 只允许 loopback，release 强制 HTTPS 并关闭 debug",
    )
    parser.add_argument("--debug", action="store_true", help="开发模式开启 pywebview debug")
    parser.add_argument(
        "--check-environment",
        action="store_true",
        help="只检查 Python、桌面依赖、Windows 和 WebView2 Runtime",
    )
    return parser


def _storage_root(environ: Mapping[str, str]) -> Path:
    local_app_data = environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data)
    return Path.home() / "AppData" / "Local"


def _bundled_release_origin() -> str:
    """读取 PyInstaller 构建时嵌入的公开 HTTPS origin。"""

    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "causalagent_desktop" / "release_origin.txt")
    candidates.append(Path(__file__).resolve().with_name("release_origin.txt"))
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if value:
            return value
    return DEFAULT_RELEASE_ORIGIN


def _bundled_mode() -> DesktopMode:
    """读取 PyInstaller 构建时嵌入的发行通道。

    旧版冻结包没有该文件，按 Release 处理，避免兼容旧包时意外放宽
    HTTPS/origin 安全边界。只有构建脚本明确写入 developer-preview 才会
    开启本地回环预览配置。
    """

    bundle_root = getattr(sys, "_MEIPASS", None)
    if not bundle_root:
        return DesktopMode.RELEASE

    marker = Path(bundle_root) / "causalagent_desktop" / "desktop_mode.txt"
    try:
        value = marker.read_text(encoding="utf-8").strip().lower()
    except (OSError, UnicodeError):
        return DesktopMode.RELEASE

    try:
        mode = DesktopMode(value)
    except ValueError as exc:
        raise ConfigError("桌面发行通道无效") from exc
    if mode is DesktopMode.DEVELOPMENT:
        # 构建产物不能被标记为可自由调试的 source development 模式。
        raise ConfigError("冻结桌面包不允许使用 development 发行通道")
    return mode


def build_config(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> DesktopConfig:
    """按 CLI > 环境变量 > 模式默认值生成并校验桌面配置。"""

    env = dict(os.environ if environ is None else environ)
    # 作为纯函数调用时默认不读取宿主进程（例如 pytest）的参数；真正的
    # CLI 入口由 launcher.main 显式传入 sys.argv[1:]。
    args = build_argument_parser().parse_args(list(argv) if argv is not None else [])
    # Source runs are developer-oriented. A frozen package follows the explicit
    # build-time marker; missing markers retain the old safe Release behavior.
    frozen = bool(getattr(sys, "frozen", False))
    mode_value = (
        _bundled_mode().value
        if frozen
        else args.mode or env.get("CAUSALAGENT_DESKTOP_MODE", DesktopMode.DEVELOPMENT.value)
    )
    try:
        mode = DesktopMode(mode_value.strip().lower())
    except (AttributeError, ValueError) as exc:
        raise ConfigError("桌面运行模式无效") from exc

    release_origin_raw = (
        _bundled_release_origin()
        if frozen
        else env.get("CAUSALAGENT_DESKTOP_RELEASE_ORIGIN", _bundled_release_origin())
    )
    release_origin = normalize_origin(release_origin_raw)
    if urlsplit(release_origin).scheme != "https":
        raise ConfigError("Release origin 必须使用 HTTPS")

    configured_url = args.url or env.get("CAUSALAGENT_DESKTOP_URL")
    if not configured_url:
        configured_url = (
            f"{release_origin}/dashboard"
            if mode is DesktopMode.RELEASE
            else DEFAULT_DEVELOPER_PREVIEW_URL
            if mode is DesktopMode.DEVELOPER_PREVIEW
            else DEFAULT_DEVELOPMENT_URL
        )
    url = normalize_url(configured_url)
    configured_origin = origin_of(url)

    if mode in {DesktopMode.DEVELOPMENT, DesktopMode.DEVELOPER_PREVIEW}:
        if configured_origin not in DEVELOPMENT_ORIGINS and not _is_local_development_origin(configured_origin):
            raise ConfigError("开发和 Developer Preview 模式只允许本地回环 Flask origin")
        # Keep the documented 5001 defaults and add an explicitly configured
        # loopback port for an isolated local stub or alternate Flask process.
        allowed_origins = frozenset(set(DEVELOPMENT_ORIGINS) | {configured_origin})
    else:
        allowed_origins = frozenset({release_origin})
        if configured_origin != release_origin or urlsplit(url).scheme != "https":
            raise ConfigError("Release 模式只允许预先配置的 HTTPS origin")

    debug_requested = args.debug or _parse_bool(env.get("CAUSALAGENT_DESKTOP_DEBUG"))
    debug = debug_requested if mode is DesktopMode.DEVELOPMENT else False

    storage_path = _storage_root(env) / DEFAULT_STORAGE_RELATIVE_PATH
    icon_value = env.get("CAUSALAGENT_DESKTOP_ICON")
    icon_path = Path(icon_value).expanduser() if icon_value else None

    # This hook is intentionally available only to source development runs for
    # the opt-in Windows smoke process. Frozen packages, including Developer
    # Preview, must never be made short-lived by an inherited test variable.
    autoclose = (
        _parse_autoclose(env.get("CAUSALAGENT_DESKTOP_TEST_AUTOCLOSE_SECONDS"))
        if not frozen and mode is DesktopMode.DEVELOPMENT
        else None
    )

    return DesktopConfig(
        mode=mode,
        url=url,
        allowed_origins=frozenset(allowed_origins),
        debug=debug,
        storage_path=storage_path,
        release_origin=release_origin,
        icon_path=icon_path,
        test_autoclose_seconds=autoclose,
    )


def is_check_environment_requested(argv: Sequence[str] | None) -> bool:
    return "--check-environment" in (list(argv) if argv is not None else sys.argv[1:])
