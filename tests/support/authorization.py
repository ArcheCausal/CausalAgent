"""
tests.support.authorization - 路由契约测试的授权替身

- 管理员与普通用户的权限集合常量
- 请求级授权替身：绕过真实数据库，只验证路由的授权边界
- 登录接口替身：只提供角色键与权限查询，不连接数据库

权限表本身的读写由 tests/unit/auth/test_rbac.py 使用假游标验证，
真实数据库行为仍由迁移与部署环境验证。
"""

from contextlib import contextmanager
from unittest.mock import patch

from app.auth.rbac import KNOWN_PERMISSION_KEYS, PERMISSION_DASHBOARD_ACCESS


ADMIN_PERMISSIONS = frozenset(KNOWN_PERMISSION_KEYS)
USER_PERMISSIONS = frozenset({PERMISSION_DASHBOARD_ACCESS})
RAG_ADMIN_USER = {
    "id": 1,
    "username": "rag-admin",
    "role": "admin",
    "is_active": True,
}


@contextmanager
def authorized_as(user, permissions=ADMIN_PERMISSIONS):
    """替身当前请求身份与权限查询，供路由契约测试使用。"""
    with (
        patch("app.auth.authorization.get_current_session_user", return_value=user),
        patch(
            "app.auth.authorization.get_current_permissions",
            return_value=frozenset(permissions),
        ),
    ):
        yield


@contextmanager
def patch_login_identity(role_keys=("user",), permissions=USER_PERMISSIONS):
    """替身登录接口使用的角色键与权限查询，不连接数据库。"""
    with (
        patch("app.auth.routes.get_user_role_keys", return_value=tuple(role_keys)),
        patch(
            "app.auth.routes.get_user_permissions",
            return_value=frozenset(permissions),
        ),
    ):
        yield


@contextmanager
def csrf_verified():
    """在缺少 Cookie Session 的路由契约测试里替身写请求 CSRF 校验通过。"""
    with patch("app.rag_eval.routes.csrf_token_is_valid", return_value=True):
        yield


def rag_eval_route_authorization():
    """返回 RAG 路由契约测试需要的授权替身上下文管理器。"""
    return (authorized_as(RAG_ADMIN_USER), csrf_verified())
