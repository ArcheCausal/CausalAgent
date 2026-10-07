"""backfill missing default user role relations

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """补齐注册缺陷遗留的默认角色关系，并保持管理员兼容角色完整。"""
    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT users.id, roles.id
        FROM users
        JOIN roles ON roles.role_key = 'user'
        LEFT JOIN user_roles
            ON user_roles.user_id = users.id
            AND user_roles.role_id = roles.id
        WHERE user_roles.user_id IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT users.id, roles.id
        FROM users
        JOIN roles ON roles.role_key = 'admin'
        LEFT JOIN user_roles
            ON user_roles.user_id = users.id
            AND user_roles.role_id = roles.id
        WHERE users.role = 'admin'
          AND user_roles.user_id IS NULL
        """
    )


def downgrade() -> None:
    """角色关系无法与迁移前合法授权区分，回退时不删除任何关系。"""
