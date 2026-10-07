from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from causalagent_desktop.config import build_config
from causalagent_desktop.launcher import DesktopLauncher, render_unavailable_page


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        if handler not in self.handlers:
            self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self

    def fire(self, *args):
        for handler in list(self.handlers):
            handler(*args)


class FakeArgs:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.Cancel = False
        self.Handled = False

    def set_Cancel(self, value: bool) -> None:
        self.Cancel = value

    def set_Handled(self, value: bool) -> None:
        self.Handled = value


class FakeCore:
    def __init__(self) -> None:
        self.NewWindowRequested = FakeEvent()
        self.pages: list[str] = []

    def NavigateToString(self, page: str) -> None:
        self.pages.append(page)


class FakeNativeWebview:
    def __init__(self) -> None:
        self.NavigationStarting = FakeEvent()
        self.NavigationCompleted = FakeEvent()
        self.CoreWebView2InitializationCompleted = FakeEvent()
        self.CoreWebView2 = FakeCore()


class EdgeChrome:
    def __init__(self) -> None:
        self.webview = FakeNativeWebview()
        self.on_new_window_request = self._default_new_window_handler

    def _default_new_window_handler(self, *_args) -> None:
        return None


EdgeChrome.__module__ = "webview.platforms.edgechromium"


class FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(before_show=FakeEvent(), loaded=FakeEvent())
        self.native = SimpleNamespace(browser=EdgeChrome())
        self.loaded_urls: list[str] = []
        self.destroyed = False

    def load_url(self, url: str) -> None:
        self.loaded_urls.append(url)

    def destroy(self) -> None:
        self.destroyed = True


class FakeWebviewModule:
    def __init__(self) -> None:
        self.window = FakeWindow()
        self.create_window_kwargs = None
        self.start_kwargs = None

    def create_window(self, *_args, **kwargs):
        self.create_window_kwargs = kwargs
        return self.window

    def start(self, **kwargs) -> None:
        self.start_kwargs = kwargs
        self.window.events.before_show.fire(self.window)
        native_webview = self.window.native.browser.webview
        native_webview.CoreWebView2InitializationCompleted.fire(
            native_webview,
            FakeArgs(IsSuccess=True),
        )
        native_webview.NavigationStarting.fire(
            native_webview,
            FakeArgs(Uri="http://127.0.0.1:5001/"),
        )
        native_webview.NavigationCompleted.fire(
            native_webview,
            FakeArgs(IsSuccess=True),
        )
        self.window.events.loaded.fire(self.window)


def _config(tmp_path: Path):
    return build_config(environ={"LOCALAPPDATA": str(tmp_path)})


def test_launcher_forces_edgechromium_and_persists_webview_data(tmp_path: Path) -> None:
    webview = FakeWebviewModule()
    launcher = DesktopLauncher(_config(tmp_path), webview_module=webview)

    assert launcher.run(validate_environment=False) == 0
    assert launcher.used_renderer == "edgechromium"
    assert launcher.remote_page_loaded is True
    assert webview.start_kwargs == {
        "gui": "edgechromium",
        "debug": False,
        "private_mode": False,
        "storage_path": str(tmp_path / "CausalAgent" / "WebView"),
        "icon": None,
    }
    assert "js_api" not in webview.create_window_kwargs
    assert webview.window.native.browser.on_new_window_request not in (
        webview.window.native.browser.webview.CoreWebView2.NewWindowRequested.handlers
    )


def test_navigation_policy_opens_external_https_and_blocks_unknown_protocol(tmp_path: Path) -> None:
    webview = FakeWebviewModule()
    opened: list[str] = []
    launcher = DesktopLauncher(
        _config(tmp_path),
        webview_module=webview,
        browser_opener=opened.append,
    )
    assert launcher.run(validate_environment=False) == 0
    assert launcher.remote_page_loaded is True

    external = FakeArgs(Uri="https://external.example.test/docs")
    webview.window.native.browser.webview.NavigationStarting.fire(
        webview.window.native.browser.webview,
        external,
    )
    assert external.Cancel is True
    assert opened == ["https://external.example.test/docs"]

    blocked = FakeArgs(Uri="javascript:alert(document.cookie)")
    webview.window.native.browser.webview.NavigationStarting.fire(
        webview.window.native.browser.webview,
        blocked,
    )
    assert blocked.Cancel is True
    assert opened == ["https://external.example.test/docs"]


def test_same_origin_new_window_stays_in_client_and_error_page_can_open_browser(
    tmp_path: Path,
) -> None:
    webview = FakeWebviewModule()
    opened: list[str] = []
    config = _config(tmp_path)
    launcher = DesktopLauncher(
        config,
        webview_module=webview,
        browser_opener=opened.append,
    )
    assert launcher.run(validate_environment=False) == 0
    assert launcher.remote_page_loaded is True
    core = webview.window.native.browser.webview.CoreWebView2

    internal = FakeArgs(Uri="http://127.0.0.1:5001/help")
    core.NewWindowRequested.fire(core, internal)
    assert internal.Handled is True
    assert webview.window.loaded_urls == ["http://127.0.0.1:5001/help"]
    assert opened == []

    launcher._error_page_visible = True
    browser_action = FakeArgs(Uri=config.url)
    core.NewWindowRequested.fire(core, browser_action)
    assert browser_action.Handled is True
    assert opened == [config.url]


def test_navigation_failure_renders_reload_and_browser_actions_without_query_text(
    tmp_path: Path,
) -> None:
    config = build_config(
        ["--url", "http://127.0.0.1:5001/?token=secret-token"],
        environ={"LOCALAPPDATA": str(tmp_path)},
    )
    webview = FakeWebviewModule()
    launcher = DesktopLauncher(config, webview_module=webview)
    assert launcher.run(validate_environment=False) == 0
    native_webview = webview.window.native.browser.webview

    native_webview.NavigationCompleted.fire(native_webview, FakeArgs(IsSuccess=False))
    page = native_webview.CoreWebView2.pages[-1]
    assert "暂时无法连接 CausalAgent" in page
    assert "重新加载" in page
    assert "在浏览器中打开" in page
    assert "<code>http://127.0.0.1:5001/</code>" in page
    assert "secret-token" in page  # 仅存在于 href；可见地址不展示查询参数。


def test_unavailable_page_is_inline_and_contains_no_python_bridge() -> None:
    page = render_unavailable_page("http://localhost:5001/?query=private")

    assert "window.pywebview.api" not in page
    assert "<code>http://localhost:5001/</code>" in page
