"""前端入口的构建阶段、产物目录与 Compose 传参契约。

文档职责：静态验证四个前端在 Dockerfile 中的构建阶段与运行镜像边界、Compose
传入的产物目录、忽略规则、已提交产物清单和各工程资源前缀。

适用范围：修改 Dockerfile、Compose 前端变量、产物目录或前端 vite base 时使用；
页面请求行为由 tests/integration/deployment/test_frontend_entries.py 覆盖。
"""

import unittest
from pathlib import Path

import yaml

from tests.contracts.deployment.frontend_layout import FRONTEND_LAYOUTS


class FrontendEntryLayoutTests(unittest.TestCase):
    """前端源码目录与已提交产物目录保持入口布局。"""

    def test_frontend_directories_follow_the_entry_layout(self):
        self.assertFalse(Path("app/static/rag_eval_app").exists())
        self.assertTrue(Path("app/rag_eval/frontend_dist/index.html").is_file())
        self.assertTrue(Path("website-frontend/src/App.vue").is_file())
        self.assertTrue(Path("chat-frontend/src/App.vue").is_file())

    def test_committed_rag_eval_dist_references_real_assets(self):
        """已提交的 RAG 评测台 index 必须指向 /rag-eval/ 下的真实资源。"""
        dist_dir = Path("app/rag_eval/frontend_dist")
        index = (dist_dir / "index.html").read_text(encoding="utf-8")
        references = {
            value.split('"', 1)[0]
            for value in index.split("/rag-eval/")[1:]
            if value.split('"', 1)[0].startswith(("assets/", "brand/"))
        }

        self.assertTrue(any(reference.startswith("assets/index") for reference in references))
        self.assertIn("brand/favicon.svg", references)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue((dist_dir / reference).is_file(), reference)


class FrontendDeploymentTests(unittest.TestCase):
    """验证四个前端的构建阶段、产物目录和映像边界。"""

    def test_dockerfile_builds_four_frontends_without_node_in_runtime(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

        for layout in FRONTEND_LAYOUTS:
            with self.subTest(stage=layout.builder_stage):
                self.assertIn(f"FROM node:24-alpine AS {layout.builder_stage}", dockerfile)
                self.assertIn(f"{layout.dist_env}={layout.dist_dir}", dockerfile)
                self.assertIn(
                    f"COPY --from={layout.builder_stage} {layout.copy_source} {layout.dist_dir}",
                    dockerfile,
                )

        for fragment in (
            "COPY packages/design-system/ /packages/design-system/",
            "COPY packages/design-system/ /workspace/packages/design-system/",
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
                volumes = [str(volume) for volume in app.get("volumes", [])]
                for layout in FRONTEND_LAYOUTS:
                    if layout.name == "admin":
                        continue
                    with self.subTest(filename=filename, frontend=layout.name):
                        self.assertEqual(
                            environment[layout.dist_env],
                            f"${{{layout.dist_env}:-{layout.dist_dir}}}",
                        )
                        mounted = [volume for volume in volumes if layout.dist_dir in volume]
                        self.assertEqual(mounted, [], layout.dist_dir)

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
        for layout in FRONTEND_LAYOUTS:
            with self.subTest(filename=layout.vite_config):
                text = Path(layout.vite_config).read_text(encoding="utf-8")
                self.assertIn(layout.asset_base, text)
                self.assertIn("base:", text)



def _environment_dict(environment):
    """统一读取 Compose 的 list/mapping 两种 environment 写法。"""
    if isinstance(environment, dict):
        return environment
    return dict(item.split("=", 1) for item in environment)


if __name__ == "__main__":
    unittest.main()
