"""预发 Compose 的 TCP readiness 与远程 VLM 注入契约。"""

from __future__ import annotations

import json
import shlex
import unittest
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WEB_PARAMETERS = (
    ("--workers", "WEB_WORKERS", "1"),
    ("--threads", "WEB_THREADS", "12"),
    ("--timeout", "WEB_TIMEOUT", "120"),
)


class StagingComposeContractTests(unittest.TestCase):
    def test_staging_web_parameters_expand_in_container_shell_after_guard(self) -> None:
        compose = yaml.safe_load(
            (REPOSITORY_ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
        )
        app = compose["services"]["app"]
        self.assertEqual(app["command"][:2], ["sh", "-c"])
        command = shlex.split(app["command"][2])
        self.assertEqual(
            command[:5],
            ["python", "scripts/staging_environment_guard.py", "&&", "exec", "gunicorn"],
        )
        self.assertEqual(command[-1], "CausalAgent:app")
        for option, variable, default in WEB_PARAMETERS:
            with self.subTest(variable=variable):
                self.assertEqual(command[command.index(option) + 1], f"$${{{variable}}}")
                self.assertEqual(app["environment"][variable], f"${{{variable}:-{default}}}")

    def test_production_web_parameters_feed_dockerfile_default_command(self) -> None:
        compose = yaml.safe_load(
            (REPOSITORY_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
        )
        app = compose["services"]["app"]
        self.assertNotIn("command", app)
        self.assertNotIn("entrypoint", app)
        environment = dict(item.split("=", 1) for item in app["environment"])
        dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
        runtime_cmd = next(line for line in reversed(dockerfile.splitlines()) if line.startswith("CMD "))
        command = json.loads(runtime_cmd.removeprefix("CMD "))
        self.assertEqual(command[:2], ["sh", "-c"])
        arguments = shlex.split(command[2])
        for option, variable, default in WEB_PARAMETERS:
            with self.subTest(variable=variable):
                expression = f"${{{variable}:-{default}}}"
                self.assertEqual(environment.get(variable), expression)
                self.assertEqual(arguments[arguments.index(option) + 1], expression)

    def test_mysql_readiness_uses_tcp_port_not_init_socket(self) -> None:
        expected = 'mysqladmin ping --protocol=tcp -h 127.0.0.1 -P 3306 -u root'
        for filename in (
            "docker-compose.yml",
            "docker-compose.prod.yml",
            "docker-compose.replica.yml",
            "docker-compose.staging.yml",
        ):
            with self.subTest(filename=filename):
                self.assertIn(expected, (REPOSITORY_ROOT / filename).read_text(encoding="utf-8"))

    def test_staging_requires_remote_vlm_credentials_when_egress_is_enabled(self) -> None:
        compose = (REPOSITORY_ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
        example = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
        secret_example = (
            REPOSITORY_ROOT / "deploy/secrets/causalagent.env.example"
        ).read_text(encoding="utf-8")

        self.assertIn('VISION_ALLOW_REMOTE_DATA: "true"', compose)
        self.assertIn("VISION_BASE_URL: ${VISION_BASE_URL:?", compose)
        self.assertIn("VISION_MODEL: ${VISION_MODEL:-qwen-vl-plus}", compose)
        # 远程 VLM 的密钥只在服务器密钥文件里，非密钥地址仍在环境文件。
        self.assertIn("VISION_API_KEY=", secret_example)
        self.assertNotIn("VISION_API_KEY=", compose)
        for key in ("VISION_BASE_URL=", "VISION_MODEL=qwen-vl-plus"):
            self.assertIn(key, example)

    def test_worker_compose_contract_has_bounded_drain_and_release_mounts(self) -> None:
        """worker Compose 应传递 drain 配置，并暴露只读 runtime 与检索策略目录。"""
        for filename in ("docker-compose.yml", "docker-compose.replica.yml"):
            compose = (REPOSITORY_ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("JOB_DRAIN_TIMEOUT_SECONDS=${JOB_DRAIN_TIMEOUT_SECONDS:-60}", compose)
            self.assertIn("stop_grace_period: 75s", compose)

        staging = (REPOSITORY_ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
        self.assertIn("JOB_DRAIN_TIMEOUT_SECONDS: ${JOB_DRAIN_TIMEOUT_SECONDS:-60}", staging)
        self.assertIn("stop_grace_period: 75s", staging)
        # 预发不再挂载宿主 runtime 指针；active pointer 位于正式 RAG 命名卷。
        self.assertNotIn("multimodal_runtime", staging)
        self.assertNotIn("rag/runtime", staging)
        self.assertIn("rag_eval_staging_formal:/var/lib/causalagent/rag/formal", staging)

    def test_multimodal_index_mounts_use_writable_named_volumes(self) -> None:
        """正式索引改用独立 RAG 卷；预发与生产不再挂宿主仓库目录。"""
        formal = "/var/lib/causalagent/rag/formal"
        expectations = {
            # 预发：app、worker、rag-eval-worker 各挂一次正式卷。
            "docker-compose.staging.yml": ("rag_eval_staging_formal:" + formal, 3),
            # 生产：app、worker、rag-eval-worker 各挂一次正式卷。
            "docker-compose.prod.yml": ("rag_formal_prod:" + formal, 3),
        }
        for filename, (mount, expected_mounts) in expectations.items():
            with self.subTest(filename=filename):
                compose = (REPOSITORY_ROOT / filename).read_text(encoding="utf-8")
                self.assertEqual(compose.count(mount), expected_mounts)
                self.assertNotIn("kb_multimodal_indexes", compose)
                self.assertNotIn("kb-indexes-sync", compose)

        # 开发环境直接挂载宿主源码目录，不再使用一次性同步容器。
        for filename in ("docker-compose.yml", "docker-compose.replica.yml"):
            with self.subTest(filename=filename):
                compose = (REPOSITORY_ROOT / filename).read_text(encoding="utf-8")
                self.assertNotIn("kb-indexes-sync", compose)
                self.assertIn(
                    "./Agent/knowledge_base/multimodal_indexes:/app/Agent/knowledge_base/multimodal_indexes",
                    compose,
                )
