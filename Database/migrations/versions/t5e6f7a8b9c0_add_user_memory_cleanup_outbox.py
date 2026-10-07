"""add user memory cleanup outbox

Revision ID: t5e6f7a8b9c0
Revises: s4d5e6f7a8b9
Create Date: 2026-09-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "t5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "s4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建立用户长期记忆清理 outbox，不回填也不激活任何历史记录。"""
    op.execute(
        """
        CREATE TABLE user_memory_cleanup_outbox (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            operation_id CHAR(36) DEFAULT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
            available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            lease_expires_at DATETIME(6) DEFAULT NULL,
            last_error TEXT DEFAULT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            completed_at DATETIME(6) DEFAULT NULL,
            CONSTRAINT ck_user_memory_cleanup_outbox_status
                CHECK (status IN ('pending', 'processing', 'succeeded', 'failed')),
            CONSTRAINT fk_user_memory_cleanup_outbox_operation
                FOREIGN KEY (operation_id) REFERENCES admin_operations(operation_id)
                ON DELETE SET NULL,
            UNIQUE KEY uq_user_memory_cleanup_outbox_user (user_id),
            INDEX idx_user_memory_cleanup_outbox_claim
                (status, available_at, id),
            INDEX idx_user_memory_cleanup_outbox_operation
                (operation_id, status, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    """只移除本次新增的清理账本，不触碰任何 Store 数据。"""
    op.execute("DROP TABLE IF EXISTS user_memory_cleanup_outbox")
