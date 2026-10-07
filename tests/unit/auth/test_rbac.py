import os
import unittest
from unittest.mock import patch


TEST_ENV = {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "test-mysql",
    "MYSQL_USER": "test-user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "test-database",
}
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)


from app.auth.rbac import (  # noqa: E402
    KNOWN_PERMISSION_KEYS,
    ROLE_ADMIN,
    ROLE_USER,
    get_user_permissions,
    get_user_role_keys,
    primary_role_key,
    replace_user_roles,
    role_keys_for_primary_role,
)


class FakeCursor:
    """记录 SQL 并按预设行返回结果的最小字典游标。"""

    def __init__(self, rows=(), *, dictionary=False):
        self.rows = list(rows)
        self.dictionary = dictionary
        self.statements = []
        self.executed_many = []

    def execute(self, sql, params=None):
        """记录语句与参数。"""
        self.statements.append((" ".join(sql.split()), params))

    def executemany(self, sql, params):
        """记录批量写入。"""
        self.executed_many.append((" ".join(sql.split()), tuple(params)))

    def fetchall(self):
        """返回预设结果行。"""
        return list(self.rows)

    def fetchone(self):
        """返回第一行，便于单行查询。"""
        return self.rows[0] if self.rows else None


class FakeConnection:
    """提供 with 上下文和游标的最小连接。"""

    def __init__(self, rows=()):
        self.cursor_instance = FakeCursor(rows)
        self.requested_dictionary = None

    def __enter__(self):
        """进入连接上下文。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """退出连接上下文，不抑制异常。"""
        return False

    def cursor(self, dictionary=False, **kwargs):
        """返回记录在案的游标。"""
        self.requested_dictionary = dictionary
        self.cursor_instance.dictionary = dictionary
        return self.cursor_instance


class PermissionLookupTests(unittest.TestCase):
    """验证权限查询从主库强一致读取并做去重。"""

    def test_permissions_are_read_from_role_relations(self):
        connection = FakeConnection([("dashboard.access",), ("admin.access",), ("dashboard.access",)])
        with patch("app.auth.rbac.get_read_connection", return_value=connection) as read_connection:
            permissions = get_user_permissions(7)

        self.assertEqual(permissions, frozenset({"dashboard.access", "admin.access"}))
        self.assertEqual(read_connection.call_args.kwargs, {"consistency": "strong"})
        sql, params = connection.cursor_instance.statements[0]
        self.assertIn("JOIN role_permissions", sql)
        self.assertIn("JOIN permissions", sql)
        self.assertEqual(params, (7,))

    def test_role_keys_are_sorted_for_stable_comparison(self):
        connection = FakeConnection([("admin",), ("user",)])
        with patch("app.auth.rbac.get_read_connection", return_value=connection):
            roles = get_user_role_keys(7)

        self.assertEqual(roles, ("admin", "user"))
        sql, _ = connection.cursor_instance.statements[0]
        self.assertIn("ORDER BY roles.role_key", sql)

    def test_readiness_checks_require_every_seeded_permission_key(self):
        """第一阶段权限键必须在代码中集中登记，避免授权判断出现拼写漂移。"""
        self.assertEqual(len(KNOWN_PERMISSION_KEYS), len(set(KNOWN_PERMISSION_KEYS)))
        for permission_key in (
            "dashboard.access",
            "rag_eval.access",
            "rag_eval.read",
            "rag_eval.run",
            "rag_eval.publish",
            "rag_eval.rollback",
            "rag_eval.governance",
            "admin.access",
            "admin.users.read",
            "admin.users.write",
            "admin.database.read",
            "admin.database.write",
            "admin.sensitive.read",
        ):
            self.assertIn(permission_key, KNOWN_PERMISSION_KEYS)


class PrimaryRoleTests(unittest.TestCase):
    """验证过渡期兼容字段与角色关系的映射规则。"""

    def test_admin_dominates_when_both_roles_are_present(self):
        self.assertEqual(primary_role_key(("admin", "user")), ROLE_ADMIN)
        self.assertEqual(primary_role_key(("user",)), ROLE_USER)
        self.assertEqual(primary_role_key(()), ROLE_USER)

    def test_admin_compatibility_role_keeps_the_user_role(self):
        self.assertEqual(role_keys_for_primary_role(ROLE_ADMIN), (ROLE_USER, ROLE_ADMIN))
        self.assertEqual(role_keys_for_primary_role(ROLE_USER), (ROLE_USER,))


class ReplaceUserRolesTests(unittest.TestCase):
    """验证角色关系写入只替换关系行，不写入未知角色。"""

    def test_relationships_are_replaced_with_known_roles(self):
        cursor = FakeCursor(
            [{"id": 1, "role_key": "user"}, {"id": 2, "role_key": "admin"}],
            dictionary=True,
        )

        replaced = replace_user_roles(cursor, 9, ("user", "admin", "unknown-role"))

        self.assertEqual(replaced, ("admin", "user"))
        self.assertEqual(cursor.statements[0][1], ("admin", "user"))
        delete_sql, delete_params = cursor.statements[1]
        self.assertIn("DELETE FROM user_roles", delete_sql)
        self.assertEqual(delete_params, (9,))
        self.assertEqual(
            cursor.executed_many,
            [(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                ((9, 2), (9, 1)),
            )],
        )

    def test_unknown_only_role_set_is_rejected(self):
        cursor = FakeCursor([], dictionary=True)
        with self.assertRaises(ValueError):
            replace_user_roles(cursor, 9, ("viewer",))
        self.assertEqual(cursor.statements, [])

    def test_missing_role_rows_are_rejected_before_writing(self):
        cursor = FakeCursor([{"id": 1, "role_key": "user"}], dictionary=True)
        with self.assertRaises(ValueError):
            replace_user_roles(cursor, 9, ("user", "admin"))
        self.assertEqual(cursor.executed_many, [])


if __name__ == "__main__":
    unittest.main()

