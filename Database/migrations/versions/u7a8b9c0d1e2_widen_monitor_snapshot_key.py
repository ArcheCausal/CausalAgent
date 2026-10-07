"""widen database monitor snapshot key

Revision ID: u7a8b9c0d1e2
Revises: t5e6f7a8b9c0
Create Date: 2026-09-17 00:10:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "u7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "t5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """把快照键上限扩展到 64 字符，容纳按进程命名的 Agent 持久化清理快照。"""
    op.execute(
        """
        ALTER TABLE database_monitor_snapshots
            MODIFY snapshot_key VARCHAR(64) NOT NULL
        """
    )


def downgrade() -> None:
    """恢复 32 字符上限；存在超长快照键时由数据库拒绝，不做静默截断。"""
    op.execute(
        """
        ALTER TABLE database_monitor_snapshots
            MODIFY snapshot_key VARCHAR(32) NOT NULL
        """
    )
