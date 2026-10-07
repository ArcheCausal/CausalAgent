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

from app.auth.admin_cli import main, promote_user_to_admin


class FakeCursor:
    """模拟管理员提升所需的最小字典游标。"""

    ROLE_ROWS = ({"id": 1, "role_key": "user"}, {"id": 2, "role_key": "admin"})

    def __init__(self, user, update_rowcount=1):
        self.user = user
        self.update_rowcount = update_rowcount
        self.rowcount = -1
        self.statements = []
        self.role_relation_writes = []

    def execute(self, sql, params):
        """记录 SQL，并为 UPDATE 设置预期影响行数。"""
        self.statements.append((" ".join(sql.split()), params))
        if sql.lstrip().upper().startswith("UPDATE"):
            self.rowcount = self.update_rowcount

    def executemany(self, sql, params):
        """记录角色关系批量写入，供提升事务断言使用。"""
        self.role_relation_writes.append((" ".join(sql.split()), tuple(params)))

    def fetchone(self):
        """返回预设的目标用户。"""
        return self.user

    def fetchall(self):
        """返回角色表的两个已初始化角色，供关系写入使用。"""
        return list(self.ROLE_ROWS)


class FakeConnection:
    """模拟带事务结果记录的数据库连接。"""

    def __init__(self, user, update_rowcount=1):
        self.fake_cursor = FakeCursor(user, update_rowcount)
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        """返回连接自身供 with 使用。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """模拟连接上下文退出。"""
        return False

    def cursor(self, dictionary=False):
        """返回唯一的模拟字典游标。"""
        self.assert_dictionary = dictionary
        return self.fake_cursor

    def commit(self):
        """记录事务已提交。"""
        self.committed = True

    def rollback(self):
        """记录事务已回滚或只读锁已释放。"""
        self.rolled_back = True


class AdminCliTests(unittest.TestCase):
    """验证初始管理员提升命令的安全边界与幂等行为。"""

    def test_missing_user_is_not_created(self):
        """目标用户不存在时不得自动创建账号。"""
        connection = FakeConnection(None)
        with patch("app.auth.admin_cli.get_write_connection", return_value=connection):
            success, message = promote_user_to_admin("missing")

        self.assertFalse(success)
        self.assertIn("不存在", message)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)

    def test_disabled_user_is_not_promoted(self):
        """已禁用用户不能成为不可用的初始管理员。"""
        user = {"id": 1, "username": "disabled", "role": "user", "is_active": False}
        connection = FakeConnection(user)
        with patch("app.auth.admin_cli.get_write_connection", return_value=connection):
            success, message = promote_user_to_admin("disabled")

        self.assertFalse(success)
        self.assertIn("已被禁用", message)
        self.assertTrue(connection.rolled_back)

    def test_existing_admin_is_idempotent(self):
        """重复提升管理员时不执行 UPDATE。"""
        user = {"id": 2, "username": "admin", "role": "admin", "is_active": True}
        connection = FakeConnection(user)
        with patch("app.auth.admin_cli.get_write_connection", return_value=connection):
            success, message = promote_user_to_admin("admin")

        self.assertTrue(success)
        self.assertIn("已经是管理员", message)
        self.assertEqual(len(connection.fake_cursor.statements), 1)
        self.assertTrue(connection.rolled_back)

    def test_active_user_is_promoted_in_one_transaction(self):
        """普通启用用户应被单次更新并提交。"""
        user = {"id": 3, "username": "charchar", "role": "user", "is_active": True}
        connection = FakeConnection(user)
        with patch("app.auth.admin_cli.get_write_connection", return_value=connection):
            success, message = promote_user_to_admin("charchar")

        self.assertTrue(success)
        self.assertIn("已提升为管理员", message)
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        statements = connection.fake_cursor.statements
        self.assertEqual(len(statements), 4)
        self.assertIn("UPDATE users", statements[1][0])
        self.assertIn("DELETE FROM user_roles", statements[3][0])
        self.assertEqual(
            connection.fake_cursor.role_relation_writes,
            [(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                ((3, 2), (3, 1)),
            )],
        )

    def test_main_uses_success_exit_code(self):
        """CLI 成功时返回零退出码。"""
        with patch(
            "app.auth.admin_cli.promote_user_to_admin",
            return_value=(True, "ok"),
        ):
            self.assertEqual(main(["promote", "charchar"]), 0)


if __name__ == "__main__":
    unittest.main()
