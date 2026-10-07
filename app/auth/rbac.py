"""
app.auth.rbac - 角色与权限查询

- 角色键与权限键常量
- 按用户查询角色和权限
- 管理员事务内替换用户角色关系

权限以 user_roles 与 role_permissions 为准，users.role 只作为过渡兼容字段。
"""

from __future__ import annotations

from app.db import get_read_connection


ROLE_USER = "user"
ROLE_ADMIN = "admin"
KNOWN_ROLE_KEYS = (ROLE_USER, ROLE_ADMIN)

PERMISSION_DASHBOARD_ACCESS = "dashboard.access"

PERMISSION_RAG_EVAL_ACCESS = "rag_eval.access"
PERMISSION_RAG_EVAL_READ = "rag_eval.read"
PERMISSION_RAG_EVAL_RUN = "rag_eval.run"
PERMISSION_RAG_EVAL_PUBLISH = "rag_eval.publish"
PERMISSION_RAG_EVAL_ROLLBACK = "rag_eval.rollback"
PERMISSION_RAG_EVAL_GOVERNANCE = "rag_eval.governance"

PERMISSION_ADMIN_ACCESS = "admin.access"
PERMISSION_ADMIN_USERS_READ = "admin.users.read"
PERMISSION_ADMIN_USERS_WRITE = "admin.users.write"
PERMISSION_ADMIN_DATABASE_READ = "admin.database.read"
PERMISSION_ADMIN_DATABASE_WRITE = "admin.database.write"
PERMISSION_ADMIN_SENSITIVE_READ = "admin.sensitive.read"

KNOWN_PERMISSION_KEYS = (
    PERMISSION_DASHBOARD_ACCESS,
    PERMISSION_RAG_EVAL_ACCESS,
    PERMISSION_RAG_EVAL_READ,
    PERMISSION_RAG_EVAL_RUN,
    PERMISSION_RAG_EVAL_PUBLISH,
    PERMISSION_RAG_EVAL_ROLLBACK,
    PERMISSION_RAG_EVAL_GOVERNANCE,
    PERMISSION_ADMIN_ACCESS,
    PERMISSION_ADMIN_USERS_READ,
    PERMISSION_ADMIN_USERS_WRITE,
    PERMISSION_ADMIN_DATABASE_READ,
    PERMISSION_ADMIN_DATABASE_WRITE,
    PERMISSION_ADMIN_SENSITIVE_READ,
)


def get_user_role_keys(user_id) -> tuple[str, ...]:
    """从主库读取用户的角色键，按角色键排序以便稳定比较。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT roles.role_key
            FROM user_roles
            JOIN roles ON roles.id = user_roles.role_id
            WHERE user_roles.user_id = %s
            ORDER BY roles.role_key
            """,
            (user_id,),
        )
        return tuple(row[0] for row in cursor.fetchall())


def get_user_permissions(user_id) -> frozenset[str]:
    """从主库读取用户通过全部角色获得的权限键集合，重复权限自动去重。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT permissions.permission_key
            FROM user_roles
            JOIN role_permissions ON role_permissions.role_id = user_roles.role_id
            JOIN permissions ON permissions.id = role_permissions.permission_id
            WHERE user_roles.user_id = %s
            """,
            (user_id,),
        )
        return frozenset(row[0] for row in cursor.fetchall())


def primary_role_key(role_keys) -> str:
    """把角色集合归约为登录响应使用的单一角色键，管理员优先。"""
    keys = set(role_keys)
    if ROLE_ADMIN in keys:
        return ROLE_ADMIN
    if ROLE_USER in keys:
        return ROLE_USER
    known = [key for key in KNOWN_ROLE_KEYS if key in keys]
    return known[0] if known else ROLE_USER


def role_keys_for_primary_role(role_key: str) -> tuple[str, ...]:
    """把兼容字段里的单一角色键展开成关系行，管理员同时保留普通用户角色。"""
    if role_key == ROLE_ADMIN:
        return (ROLE_USER, ROLE_ADMIN)
    return (ROLE_USER,)


def replace_user_roles(cursor, user_id: int, role_keys) -> tuple[str, ...]:
    """在调用方事务内替换用户角色关系，并返回排序后的角色键。"""
    normalized = sorted({key for key in role_keys if key in KNOWN_ROLE_KEYS})
    if not normalized:
        raise ValueError("用户至少需要保留一个已知角色。")
    placeholders = ", ".join(["%s"] * len(normalized))
    cursor.execute(
        f"SELECT id, role_key FROM roles WHERE role_key IN ({placeholders})",
        tuple(normalized),
    )
    role_ids = {row["role_key"]: row["id"] for row in cursor.fetchall()}
    missing = [key for key in normalized if key not in role_ids]
    if missing:
        raise ValueError(f"角色未初始化: {missing}")
    cursor.execute("DELETE FROM user_roles WHERE user_id = %s", (user_id,))
    cursor.executemany(
        "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
        [(user_id, role_ids[key]) for key in normalized],
    )
    return tuple(normalized)
