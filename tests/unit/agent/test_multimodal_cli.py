"""多模态维护 CLI 的公开命令契约。"""

import unittest

from Agent.knowledge_base.multimodal.cli import build_parser


class MultimodalCliTests(unittest.TestCase):
    def test_exposes_only_current_maintenance_commands(self):
        help_text = build_parser().format_help()

        self.assertNotIn("omnidocbench", help_text)
        for command in ("inspect", "ingest", "run", "evaluate", "publish", "status", "rollback"):
            self.assertIn(command, help_text)
