"""Alembic revision 图必须保持单一、无重复的可升级 head。

工具需求：真实的 Alembic 运行依赖；缺少时跳过并保留未执行结论。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class AlembicMigrationGraphTests(unittest.TestCase):
    def test_heads_has_one_unique_merge_revision(self) -> None:
        pytest.importorskip("alembic", reason="验证 migration head 需要 Alembic 运行依赖")
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("present more than once", result.stderr)
        heads = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(heads), 1, heads)
        self.assertTrue(heads[0].endswith("(head)"), heads)
        head_revision = heads[0].split()[0]
        self.assertTrue(
            any(
                f'revision: str = "{head_revision}"' in path.read_text(encoding="utf-8")
                or f"revision: str = '{head_revision}'" in path.read_text(encoding="utf-8")
                for path in (REPOSITORY_ROOT / "Database" / "migrations" / "versions").glob("*.py")
            ),
            head_revision,
        )
