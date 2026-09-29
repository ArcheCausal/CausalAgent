import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from flask import Flask


TEST_ENV = {
    "SECRET_KEY": "frontend-entry-test-secret",
    "API_KEY": "frontend-entry-test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "frontend-entry-test-model",
    "MYSQL_HOST": "frontend-entry-test-mysql",
    "MYSQL_USER": "frontend-entry-test-user",
    "MYSQL_PASSWORD": "frontend-entry-test-password",
    "MYSQL_DATABASE": "frontend-entry-test-database",
}
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)


from app.chat.page_routes import dashboard_asset_bp, dashboard_page_bp  # noqa: E402
from app.main import routes as site_routes  # noqa: E402
from app.rag_eval.page_routes import rag_eval_page_bp  # noqa: E402
from app.request_context import register_request_context  # noqa: E402
from config.settings import AppConfig, settings  # noqa: E402
from tests.support.authorization import (  # noqa: E402
    ADMIN_PERMISSIONS,
    RAG_ADMIN_USER,
    USER_PERMISSIONS,
    authorized_as,
)


ACTIVE_USER = {"id": 4, "username": "operator", "role": "user", "is_active": True}
SITE_PATHS = ("/", "/product", "/pricing", "/about", "/docs", "/changelog", "/auth/sign-in", "/auth/sign-up")


def build_app():
    """注册官网、普通应用和 RAG 页面蓝图的最小应用。"""
    app = Flask(__name__)
    app.secret_key = "frontend-entry-route-secret"
    register_request_context(app)
    app.register_blueprint(site_routes.main_bp)
    app.register_blueprint(dashboard_page_bp)
    app.register_blueprint(dashboard_asset_bp)
    app.register_blueprint(rag_eval_page_bp)
    return app


def write_shell(dist_dir: Path, marker: str) -> None:
    """写入一个最小构建产物，用于区分四个前端的入口。"""
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "index.html").write_text(
        f"<!doctype html><html><body>{marker}</body></html>",
        encoding="utf-8",
    )


class WebsiteEntryTests(unittest.TestCase):
    """官网公开页面与认证页面由官网构建产物提供。"""

    def test_public_and_auth_pages_share_the_website_bundle(self):
        app = build_app()
        with TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir)
            write_shell(dist_dir, "website-shell")
            with patch.multiple(
                settings,
                WEBSITE_FRONTEND_DIST_DIR=str(dist_dir),
                WEBSITE_VITE_DEV_SERVER_URL="",
            ):
                with app.test_client() as client:
                    responses = [(path, client.get(path)) for path in SITE_PATHS]
                    payloads = [(path, response.status_code, response.get_data()) for path, response in responses]
                    cache_controls = [response.headers.get("Cache-Control", "") for _, response in responses]
                    for _, response in responses:
                        response.close()

        for path, status_code, body in payloads:
            with self.subTest(path=path):
                self.assertEqual(status_code, 200)
                self.assertIn(b"website-shell", body)
        self.assertTrue(all("no-cache" in value for value in cache_controls))

    def test_website_assets_are_hashed_and_cacheable(self):
        app = build_app()
        with TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir)
            write_shell(dist_dir, "website-shell")
            (dist_dir / "assets").mkdir()
            (dist_dir / "assets" / "app.js").write_text("console.log('site');", encoding="utf-8")
            with patch.multiple(
                settings,
                WEBSITE_FRONTEND_DIST_DIR=str(dist_dir),
                WEBSITE_VITE_DEV_SERVER_URL="",
            ):
                with app.test_client() as client:
                    response = client.get("/site-assets/assets/app.js")
                    body = response.get_data()
                    cache_control = response.headers["Cache-Control"]
                    missing = client.get("/site-assets/assets/missing.js")
                    missing_status = missing.status_code
                    response.close()
                    missing.close()

        self.assertEqual(body, b"console.log('site');")
        self.assertIn("public", cache_control)
        self.assertIn("max-age=31536000", cache_control)
        self.assertIn("immutable", cache_control)
        self.assertEqual(missing_status, 404)

    def test_missing_website_build_fails_closed_with_request_id(self):
        app = build_app()
        with TemporaryDirectory() as temp_dir:
            with patch.multiple(
                settings,
                WEBSITE_FRONTEND_DIST_DIR=str(Path(temp_dir) / "missing"),
                WEBSITE_VITE_DEV_SERVER_URL="",
            ):
                with app.test_client() as client:
                    responses = [client.get(path) for path in ("/", "/product", "/auth/sign-in")]
                    responses.append(client.get("/site-assets/assets/app.js"))

        for response in responses:
            with self.subTest(path=response.request.path):
                self.assertEqual(response.status_code, 503)
                payload = response.get_json()
                self.assertEqual(payload["code"], "website_frontend_missing")
                self.assertEqual(payload["request_id"], response.headers["X-Request-ID"])

    def test_explicit_website_dev_server_takes_over_pages(self):
        app = build_app()
        with patch.multiple(
            settings,
            WEBSITE_FRONTEND_DIST_DIR="",
            WEBSITE_VITE_DEV_SERVER_URL="http://127.0.0.1:5175",
        ):
            with app.test_client() as client:
                root_response = client.get("/")
                product_response = client.get("/product")
                pricing_response = client.get("/pricing")
                auth_response = client.get("/auth/sign-in?next=%2Fdashboard")

        self.assertEqual(root_response.status_code, 302)
        self.assertEqual(root_response.headers["Location"], "http://127.0.0.1:5175/site-assets/")
        self.assertEqual(product_response.headers["Location"], "http://127.0.0.1:5175/site-assets/product")
        self.assertEqual(pricing_response.headers["Location"], "http://127.0.0.1:5175/site-assets/pricing")
        self.assertEqual(
            auth_response.headers["Location"],
            "http://127.0.0.1:5175/site-assets/auth/sign-in?next=%2Fdashboard",
        )


class DashboardEntryTests(unittest.TestCase):
    """普通用户应用只在登录后提供，未登录跳转统一登录入口。"""

    def test_anonymous_visitor_is_redirected_to_sign_in(self):
        app = build_app()
        with authorized_as(None, ()):
            with app.test_client() as client:
                root_response = client.get("/dashboard")
                settings_response = client.get("/dashboard/settings")
                session_response = client.get("/dashboard/session/abc-1")
                asset_response = client.get("/dashboard-assets/assets/app.js")

        expected = {
            root_response: "/auth/sign-in?next=%2Fdashboard",
            settings_response: "/auth/sign-in?next=%2Fdashboard%2Fsettings",
            session_response: "/auth/sign-in?next=%2Fdashboard%2Fsession%2Fabc-1",
            asset_response: "/auth/sign-in",
        }
        for response, location in expected.items():
            with self.subTest(path=response.request.path):
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], location)

    def test_logged_in_user_with_dashboard_permission_gets_workspace(self):
        app = build_app()
        with TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir)
            write_shell(dist_dir, "chat-shell")
            (dist_dir / "assets").mkdir()
            (dist_dir / "assets" / "app.js").write_text("console.log('chat');", encoding="utf-8")
            with (
                authorized_as(ACTIVE_USER, USER_PERMISSIONS),
                patch.multiple(
                    settings,
                    CHAT_FRONTEND_DIST_DIR=str(dist_dir),
                    CHAT_VITE_DEV_SERVER_URL="",
                ),
            ):
                with app.test_client() as client:
                    page = client.get("/dashboard/session/abc-1")
                    asset = client.get("/dashboard-assets/assets/app.js")
                    page_body = page.get_data()
                    asset_body = asset.get_data()
                    asset_cache = asset.headers["Cache-Control"]
                    page.close()
                    asset.close()

        self.assertEqual(page_body, b"<!doctype html><html><body>chat-shell</body></html>")
        self.assertEqual(asset_body, b"console.log('chat');")
        self.assertIn("immutable", asset_cache)

    def test_missing_dashboard_permission_returns_controlled_403(self):
        app = build_app()
        with authorized_as(ACTIVE_USER, ()):
            with app.test_client() as client:
                response = client.get("/dashboard")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("无访问权限", response.get_data(as_text=True))

    def test_missing_chat_build_returns_stable_503(self):
        app = build_app()
        with TemporaryDirectory() as temp_dir:
            with (
                authorized_as(ACTIVE_USER, USER_PERMISSIONS),
                patch.multiple(
                    settings,
                    CHAT_FRONTEND_DIST_DIR=str(Path(temp_dir) / "missing"),
                    CHAT_VITE_DEV_SERVER_URL="",
                ),
            ):
                with app.test_client() as client:
                    response = client.get("/dashboard")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "chat_frontend_missing")


class RagEvalEntryTests(unittest.TestCase):
    """RAG 评测台页面与页面资源共用同一权限边界。"""

    def test_anonymous_visitor_is_redirected_to_sign_in(self):
        app = build_app()
        with authorized_as(None, ()):
            with app.test_client() as client:
                page = client.get("/rag-eval")
                asset = client.get("/rag-eval/assets/index.js")
                brand = client.get("/rag-eval/brand/favicon.svg")

        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["Location"], "/auth/sign-in?next=%2Frag-eval")
        self.assertEqual(asset.status_code, 302)
        self.assertEqual(asset.headers["Location"], "/auth/sign-in")
        self.assertEqual(brand.status_code, 302)
        self.assertEqual(brand.headers["Location"], "/auth/sign-in")

    def test_normal_user_is_denied_with_controlled_page(self):
        app = build_app()
        with authorized_as(ACTIVE_USER, USER_PERMISSIONS):
            with app.test_client() as client:
                page = client.get("/rag-eval")
                asset = client.get("/rag-eval/assets/index.js")

        for response in (page, asset):
            with self.subTest(path=response.request.path):
                self.assertEqual(response.status_code, 403)
                self.assertIn("无访问权限", response.get_data(as_text=True))

    def test_administrator_gets_page_and_hashed_assets(self):
        app = build_app()
        with TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir)
            write_shell(dist_dir, "rag-shell")
            (dist_dir / "assets").mkdir()
            (dist_dir / "assets" / "index.js").write_text("console.log('rag');", encoding="utf-8")
            (dist_dir / "brand").mkdir()
            (dist_dir / "brand" / "favicon.svg").write_text("<svg />", encoding="utf-8")
            with (
                authorized_as(RAG_ADMIN_USER, ADMIN_PERMISSIONS),
                patch.object(settings, "RAG_EVAL_FRONTEND_DIST_DIR", str(dist_dir)),
            ):
                with app.test_client() as client:
                    page = client.get("/rag-eval")
                    asset = client.get("/rag-eval/assets/index.js")
                    brand = client.get("/rag-eval/brand/favicon.svg")
                    page_body = page.get_data()
                    asset_body = asset.get_data()
                    asset_cache = asset.headers["Cache-Control"]
                    brand_body = brand.get_data()
                    page.close()
                    asset.close()
                    brand.close()

        self.assertIn(b"rag-shell", page_body)
        self.assertEqual(asset_body, b"console.log('rag');")
        self.assertEqual(brand_body, b"<svg />")
        self.assertIn("immutable", asset_cache)

    def test_missing_rag_build_returns_stable_503(self):
        app = build_app()
        with TemporaryDirectory() as temp_dir:
            with (
                authorized_as(RAG_ADMIN_USER, ADMIN_PERMISSIONS),
                patch.object(
                    settings,
                    "RAG_EVAL_FRONTEND_DIST_DIR",
                    str(Path(temp_dir) / "missing"),
                ),
            ):
                with app.test_client() as client:
                    response = client.get("/rag-eval")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "rag_eval_frontend_missing")


class RemovedAliasTests(unittest.TestCase):
    """旧入口不再保留兼容别名。"""

    def test_legacy_aliases_and_unprotected_rag_static_are_gone(self):
        app = build_app()
        with app.test_client() as client:
            chat_next = client.get("/chat-next")
            rag_eval_alias = client.get("/rag_eval")
            legacy_static = client.get("/static/rag_eval_app/index.html")
            chat_assets = client.get("/chat-assets/assets/app.js")

        self.assertEqual(chat_next.status_code, 404)
        self.assertEqual(rag_eval_alias.status_code, 404)
        self.assertEqual(legacy_static.status_code, 404)
        self.assertEqual(chat_assets.status_code, 404)

    def test_frontend_directories_follow_the_entry_layout(self):
        self.assertFalse(Path("app/static/rag_eval_app").exists())
        self.assertTrue(Path("app/rag_eval/frontend_dist/index.html").is_file())
        self.assertEqual(
            Path("app/rag_eval/frontend_dist/index.html").read_text(encoding="utf-8").count("/rag-eval/assets/"),
            2,
        )
        self.assertTrue(Path("website-frontend/src/App.vue").is_file())
        self.assertTrue(Path("chat-frontend/src/App.vue").is_file())


class FrontendDeploymentTests(unittest.TestCase):
    """验证四个前端的构建阶段、产物目录和映像边界。"""

    def test_dockerfile_builds_four_frontends_without_node_in_runtime(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

        for fragment in (
            "FROM node:24-alpine AS admin-builder",
            "FROM node:24-alpine AS chat-builder",
            "FROM node:24-alpine AS website-builder",
            "FROM node:24-alpine AS rag-eval-builder",
            "COPY packages/design-system/ /packages/design-system/",
            "COPY packages/design-system/ /workspace/packages/design-system/",
            "COPY --from=admin-builder /frontend/dist /opt/causalagent-admin",
            "COPY --from=chat-builder /frontend/dist /opt/causalagent-chat",
            "COPY --from=website-builder /frontend/dist /opt/causalagent-website",
            "COPY --from=rag-eval-builder /workspace/app/rag_eval/frontend_dist /opt/causalagent-rag-eval",
            "WEBSITE_FRONTEND_DIST_DIR=/opt/causalagent-website",
            "RAG_EVAL_FRONTEND_DIST_DIR=/opt/causalagent-rag-eval",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, dockerfile)
        self.assertEqual(dockerfile.count("COPY packages/design-system/ /packages/design-system/"), 3)

        runtime_stage = dockerfile.split("FROM python-deps AS runtime", 1)[1]
        self.assertNotIn("npm ", runtime_stage)
        self.assertNotIn("node:", runtime_stage)
        self.assertNotIn("vite", runtime_stage.lower())

    def test_compose_passes_every_frontend_directory_outside_the_source_mount(self):
        for filename in (
            "docker-compose.yml",
            "docker-compose.staging.yml",
            "docker-compose.prod.yml",
        ):
            with self.subTest(filename=filename):
                compose = yaml.safe_load(Path(filename).read_text(encoding="utf-8"))
                app = compose["services"]["app"]
                environment = _environment_dict(app["environment"])

                self.assertNotIn("CHAT_FRONTEND_ENTRY", environment)
                self.assertEqual(
                    environment["CHAT_FRONTEND_DIST_DIR"],
                    "${CHAT_FRONTEND_DIST_DIR:-/opt/causalagent-chat}",
                )
                self.assertEqual(
                    environment["WEBSITE_FRONTEND_DIST_DIR"],
                    "${WEBSITE_FRONTEND_DIST_DIR:-/opt/causalagent-website}",
                )
                self.assertEqual(
                    environment["RAG_EVAL_FRONTEND_DIST_DIR"],
                    "${RAG_EVAL_FRONTEND_DIST_DIR:-/opt/causalagent-rag-eval}",
                )
                volumes = [str(volume) for volume in app.get("volumes", [])]
                for directory in (
                    "/opt/causalagent-chat",
                    "/opt/causalagent-website",
                    "/opt/causalagent-rag-eval",
                ):
                    mounted = [volume for volume in volumes if directory in volume]
                    self.assertEqual(mounted, [], directory)

    def test_ignores_do_not_send_local_build_outputs(self):
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        gitignore = Path(".gitignore").read_text(encoding="utf-8")

        for text in (dockerignore, gitignore):
            for entry in (
                "chat-frontend/node_modules/",
                "chat-frontend/dist/",
                "website-frontend/node_modules/",
                "website-frontend/dist/",
                "app/rag_eval/frontend/node_modules/",
                "packages/design-system/node_modules/",
            ):
                with self.subTest(entry=entry):
                    self.assertIn(entry, text)

        self.assertIn("app/rag_eval/frontend_dist/", gitignore)
        self.assertNotIn("app/static/rag_eval_app/", gitignore)
        self.assertIn("app/rag_eval/frontend_dist/", dockerignore)
        self.assertIn("packages/design-system/node_modules/", dockerignore)
        self.assertIn("!packages/design-system/src/fonts/licenses/*.txt", dockerignore)

    def test_frontend_configs_use_their_own_asset_base(self):
        expectations = {
            "website-frontend/vite.config.ts": "base: '/site-assets/'",
            "chat-frontend/vite.config.ts": "base: '/dashboard-assets/'",
            "app/rag_eval/frontend/vite.config.ts": 'base: "/rag-eval/"',
            "admin-frontend/vite.config.ts": "base: '/admin/'",
        }
        for filename, fragment in expectations.items():
            with self.subTest(filename=filename):
                self.assertIn(fragment, Path(filename).read_text(encoding="utf-8"))


class FrontendConfigTests(unittest.TestCase):
    """验证前端目录与开发服务器配置的默认值。"""

    def test_frontend_paths_default_to_local_dist(self):
        with patch.dict(os.environ, TEST_ENV, clear=True):
            config = AppConfig()

        self.assertEqual(config.WEBSITE_FRONTEND_DIST_DIR, "")
        self.assertEqual(config.WEBSITE_VITE_DEV_SERVER_URL, "")
        self.assertEqual(config.CHAT_FRONTEND_DIST_DIR, "")
        self.assertEqual(config.RAG_EVAL_FRONTEND_DIST_DIR, "")


def _environment_dict(environment):
    """统一读取 Compose 的 list/mapping 两种 environment 写法。"""
    if isinstance(environment, dict):
        return environment
    return dict(item.split("=", 1) for item in environment)


if __name__ == "__main__":
    unittest.main()
