"""add rbac roles permissions and relations

Revision ID: w9c0d1e2f3a4
Revises: v8b9c0d1e2f3
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "w9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "v8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROLE_SEEDS = (
    ("user", "普通用户", "可以登录普通用户应用并管理自己的会话与文件。"),
    ("admin", "管理员", "可以使用普通用户应用、RAG 评测台和管理员系统。"),
)


PERMISSION_SEEDS = (
    ("dashboard.access", "普通用户应用", "允许进入登录后的普通用户工作区。"),
    ("rag_eval.access", "RAG 评测台", "允许打开 RAG 评测台页面。"),
    ("rag_eval.read", "RAG 只读", "允许读取 RAG 评测台的状态、配置与运行记录。"),
    ("rag_eval.run", "RAG 运行", "允许创建和管理隔离评测运行。"),
    ("rag_eval.publish", "RAG 发布", "允许发布生产检索配置与正式索引。"),
    ("rag_eval.rollback", "RAG 回滚", "允许回滚正式索引 active pointer。"),
    ("rag_eval.governance", "RAG 治理", "允许执行题集冻结、门禁检查与治理运行。"),
    ("admin.access", "管理员系统", "允许进入管理员系统页面与接口。"),
    ("admin.users.read", "管理员读取用户", "允许在管理员系统中读取用户信息。"),
    ("admin.users.write", "管理员写入用户", "允许在管理员系统中修改用户状态与角色。"),
    ("admin.database.read", "管理员读取数据库", "允许读取数据库看板、配置与审计。"),
    ("admin.database.write", "管理员写入数据库", "允许修改监控配置并触发受控写操作。"),
    ("admin.sensitive.read", "管理员读取敏感内容", "允许读取会话、消息、附件与 checkpoint 正文。"),
)


ROLE_PERMISSION_SEEDS = {
    "user": ("dashboard.access",),
    "admin": tuple(permission_key for permission_key, _, _ in PERMISSION_SEEDS),
}


def upgrade() -> None:
    """创建 RBAC 表、初始化两个角色与第一阶段权限，并从 users.role 回填关系。"""
    op.execute(
        """
        CREATE TABLE roles (
            id INT AUTO_INCREMENT PRIMARY KEY,
            role_key VARCHAR(64) NOT NULL,
            display_name VARCHAR(128) NOT NULL,
            description VARCHAR(512) NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_roles_role_key (role_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE permissions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            permission_key VARCHAR(128) NOT NULL,
            display_name VARCHAR(128) NOT NULL,
            description VARCHAR(512) NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_permissions_permission_key (permission_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE user_roles (
            user_id INT NOT NULL,
            role_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, role_id),
            KEY idx_user_roles_role (role_id),
            CONSTRAINT fk_user_roles_user
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            CONSTRAINT fk_user_roles_role
                FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE role_permissions (
            role_id INT NOT NULL,
            permission_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (role_id, permission_id),
            KEY idx_role_permissions_permission (permission_id),
            CONSTRAINT fk_role_permissions_role
                FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
            CONSTRAINT fk_role_permissions_permission
                FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    for role_key, display_name, description in ROLE_SEEDS:
        op.execute(
            "INSERT INTO roles (role_key, display_name, description) VALUES "
            f"('{role_key}', '{display_name}', '{description}')"
        )
    for permission_key, display_name, description in PERMISSION_SEEDS:
        op.execute(
            "INSERT INTO permissions (permission_key, display_name, description) VALUES "
            f"('{permission_key}', '{display_name}', '{description}')"
        )
    for role_key, permission_keys in ROLE_PERMISSION_SEEDS.items():
        for permission_key in permission_keys:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT roles.id, permissions.id FROM roles "
                f"JOIN permissions ON permissions.permission_key = '{permission_key}' "
                f"WHERE roles.role_key = '{role_key}'"
            )

    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT users.id, roles.id
        FROM users
        JOIN roles ON roles.role_key = users.role
        LEFT JOIN user_roles
            ON user_roles.user_id = users.id AND user_roles.role_id = roles.id
        WHERE user_roles.user_id IS NULL
        """
    )


def downgrade() -> None:
    """删除本迁移新增的四张 RBAC 表，不动 users.role 与业务数据。"""
    op.execute("DROP TABLE IF EXISTS role_permissions")
    op.execute("DROP TABLE IF EXISTS user_roles")
    op.execute("DROP TABLE IF EXISTS permissions")
    op.execute("DROP TABLE IF EXISTS roles")
