"""add public interaction payload and waiting deadline to analysis jobs

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """保存当前公开交互、等待截止时间和过期扫描索引。"""
    op.execute(
        """
        ALTER TABLE analysis_jobs
            ADD COLUMN current_interaction JSON DEFAULT NULL
                AFTER current_waiting_prompt,
            ADD COLUMN input_wait_expires_at DATETIME(6) DEFAULT NULL
                AFTER current_interaction,
            MODIFY COLUMN execution_release_reason
                ENUM('worker_confirmed', 'lease_expired', 'input_timeout')
                DEFAULT NULL,
            ADD INDEX idx_analysis_jobs_waiting_deadline
                (status, input_wait_expires_at)
        """
    )


def downgrade() -> None:
    """只移除本次新增字段和索引，不删除 Job 或历史数据。"""
    op.execute(
        """
        ALTER TABLE analysis_jobs
            DROP INDEX idx_analysis_jobs_waiting_deadline,
            DROP COLUMN input_wait_expires_at,
            DROP COLUMN current_interaction,
            MODIFY COLUMN execution_release_reason
                ENUM('worker_confirmed', 'lease_expired')
                DEFAULT NULL
        """
    )
