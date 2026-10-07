"""初始管理员提升命令。"""

from __future__ import annotations

import argparse

import mysql.connector


def get_write_connection():
    """延迟导入数据库模块并返回主库写连接。"""
    from app.db import get_write_connection as _get_write_connection

    return _get_write_connection()


def replace_user_roles(cursor, user_id, role_keys):
    """延迟导入 RBAC 角色关系写入，避免 CLI 导入期加载应用配置。"""
    from app.auth.rbac import replace_user_roles as _replace_user_roles

    return _replace_user_roles(cursor, user_id, role_keys)


def role_keys_for_primary_role(role_key):
    """延迟导入兼容角色到关系行的展开规则。"""
    from app.auth.rbac import role_keys_for_primary_role as _role_keys_for_primary_role

    return _role_keys_for_primary_role(role_key)


def promote_user_to_admin(username: str) -> tuple[bool, str]:
    """把一个已注册且已启用的普通用户幂等提升为管理员。"""
    normalized_username = username.strip()
    if not normalized_username:
        return False, "用户名不能为空。"

    try:
        with get_write_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, username, role, is_active
                FROM users
                WHERE username = %s
                FOR UPDATE
                """,
                (normalized_username,),
            )
            user = cursor.fetchone()
            if user is None:
                conn.rollback()
                return False, f"用户 '{normalized_username}' 不存在，未执行任何修改。"
            if not user["is_active"]:
                conn.rollback()
                return False, f"用户 '{normalized_username}' 已被禁用，不能提升为管理员。"
            if user["role"] == "admin":
                conn.rollback()
                return True, f"用户 '{normalized_username}' 已经是管理员，无需重复修改。"

            cursor.execute(
                "UPDATE users SET role = 'admin' WHERE id = %s AND role = 'user'",
                (user["id"],),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return False, f"用户 '{normalized_username}' 的角色未更新。"
            replace_user_roles(cursor, int(user["id"]), role_keys_for_primary_role("admin"))
            conn.commit()
            return True, f"用户 '{normalized_username}' 已提升为管理员。"
    except ValueError:
        return False, "角色表未初始化，请先运行数据库迁移。"
    except mysql.connector.Error:
        return False, "数据库操作失败，未提升管理员。"


def build_parser() -> argparse.ArgumentParser:
    """构建管理员 CLI 的命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="CausalAgent 初始管理员管理命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    promote_parser = subparsers.add_parser("promote", help="提升一个现有用户为管理员")
    promote_parser.add_argument("username", help="数据库中已经注册的准确用户名")
    return parser


def main(argv: list[str] | None = None) -> int:
    """执行管理员 CLI 并以退出码表示操作是否成功。"""
    args = build_parser().parse_args(argv)
    if args.command == "promote":
        success, message = promote_user_to_admin(args.username)
        print(message)
        return 0 if success else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
