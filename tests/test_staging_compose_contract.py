"""预发 Compose 的 TCP readiness 与远程 VLM 注入契约。"""

from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class StagingComposeContractTests(unittest.TestCase):
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
