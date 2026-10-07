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

        self.assertIn('VISION_ALLOW_REMOTE_DATA: "true"', compose)
        self.assertIn("VISION_API_KEY: ${VISION_API_KEY:?", compose)
        self.assertIn("VISION_BASE_URL: ${VISION_BASE_URL:?", compose)
        self.assertIn("VISION_MODEL: ${VISION_MODEL:-qwen-vl-plus}", compose)
        for key in ("VISION_API_KEY=", "VISION_BASE_URL=", "VISION_MODEL=qwen-vl-plus"):
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
        for path in (
            "./Agent/knowledge_base/multimodal_runtime:/app/Agent/knowledge_base/multimodal_runtime:ro",
            "./Agent/knowledge_base/rag/runtime:/app/Agent/knowledge_base/rag/runtime:ro",
        ):
            self.assertIn(path, staging)

    def test_multimodal_index_mounts_use_writable_named_volumes(self) -> None:
        """多模态索引目录必须挂到可写命名卷，不能只读挂载或直挂宿主仓库。"""
        expectations = {
            "docker-compose.yml": ("kb_multimodal_indexes", 4),
            "docker-compose.replica.yml": ("kb_multimodal_indexes", 4),
            "docker-compose.prod.yml": ("kb_multimodal_indexes_prod", 3),
            "docker-compose.staging.yml": ("kb_multimodal_indexes_staging", 3),
        }
        target = "/app/Agent/knowledge_base/multimodal_indexes"
        host_mount = "./Agent/knowledge_base/multimodal_indexes:" + target
        for filename, (volume_key, expected_mounts) in expectations.items():
            with self.subTest(filename=filename):
                compose = (REPOSITORY_ROOT / filename).read_text(encoding="utf-8")
                self.assertEqual(compose.count(volume_key + ":" + target), expected_mounts)
                self.assertNotIn(volume_key + ":" + target + ":ro", compose)
                self.assertNotIn(host_mount, compose)
                self.assertIn("\n  " + volume_key + ":", compose)
