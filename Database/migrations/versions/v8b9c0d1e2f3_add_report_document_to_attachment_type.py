"""add report_document to chat_attachments attachment_type

Revision ID: v8b9c0d1e2f3
Revises: u7a8b9c0d1e2
Create Date: 2026-09-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "v8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "u7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 chat_attachments.attachment_type 增加 report_document 枚举值。"""
    op.execute(
        """
        ALTER TABLE chat_attachments
        MODIFY COLUMN attachment_type
        ENUM('causal_graph', 'analysis_result', 'file_content', 'other', 'visualization', 'web_search_references', 'report_document')
        NOT NULL
        """
    )


def downgrade() -> None:
    """先删除 report_document 附件数据，再收缩 ENUM。"""
    op.execute(
        """
        DELETE FROM chat_attachments
        WHERE attachment_type = 'report_document'
        """
    )
    op.execute(
        """
        ALTER TABLE chat_attachments
        MODIFY COLUMN attachment_type
        ENUM('causal_graph', 'analysis_result', 'file_content', 'other', 'visualization', 'web_search_references')
        NOT NULL
        """
    )
