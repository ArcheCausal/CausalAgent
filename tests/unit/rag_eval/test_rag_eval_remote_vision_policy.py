"""验证上传来源在隔离评测中的远程视觉授权策略。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.rag_eval import isolated_runs


class RagEvalRemoteVisionPolicyTests(unittest.TestCase):
    def test_remote_vision_is_disabled_by_default(self):
        """没有用户授权时，隔离摄取默认不进入远程视觉链路。"""
        with tempfile.TemporaryDirectory() as directory:
            uploaded = Path(directory) / "upload_example__private.pdf"
            uploaded.write_bytes(b"private")
            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(isolated_runs._remote_data_enabled())
                self.assertFalse(isolated_runs._remote_data_enabled_for_sources([uploaded]))

    def test_remote_vision_requires_explicit_source_authorization(self):
        """开启环境能力也不能替代来源级授权。"""
        with patch.dict(os.environ, {"VISION_ALLOW_REMOTE_DATA": "true"}, clear=False):
            self.assertTrue(isolated_runs._remote_data_enabled())
