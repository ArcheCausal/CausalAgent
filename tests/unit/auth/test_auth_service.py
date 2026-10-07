import ast
import inspect
import os
import textwrap
import unittest
from unittest.mock import patch

import mysql.connector


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

from app.auth.service import (
    find_user,
    find_user_by_id,
    record_successful_login,
    register_user,
)


class FakeCursor:
    """模拟返回单个认证用户的字典游标。"""

    def __init__(self, row):
        self.row = row
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        """记录认证查询文本与参数。"""
        self.sql = " ".join(sql.split())
        self.params = params

    def fetchone(self):
        """返回预设的认证用户。"""
        return self.row


class FakeConnection:
    """模拟认证查询使用的只读连接。"""

    def __init__(self, row):
        self.fake_cursor = FakeCursor(row)
        self.committed = False

    def __enter__(self):
        """返回连接自身供 with 使用。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """模拟只读连接上下文退出。"""
        return False

    def cursor(self, dictionary=False):
        """返回唯一的模拟字典游标。"""
        self.dictionary = dictionary
        return self.fake_cursor

    def commit(self):
        """记录认证写入是否提交。"""
        self.committed = True


class AuthServiceReadTests(unittest.TestCase):
    """验证登录和权限查询固定使用主库强一致读。"""

    def test_find_user_uses_strong_read_and_returns_role_state(self):
        """用户名查询必须读取密码、角色和启用状态。"""
        row = {
            "id": 1,
            "username": "charchar",
            "password_hash": "hash",
            "role": "admin",
            "is_active": True,
        }
        connection = FakeConnection(row)
        with patch(
            "app.auth.service.get_read_connection",
            return_value=connection,
        ) as get_read:
            result = find_user("charchar")

        get_read.assert_called_once_with(consistency="strong")
        self.assertEqual(result, row)
        self.assertIn("password_hash, role, is_active", connection.fake_cursor.sql)

    def test_find_user_by_id_uses_strong_read_and_returns_role_state(self):
        """session 回查必须从主库取得当前角色和启用状态。"""
        row = {"id": 2, "username": "normal", "role": "user", "is_active": True}
        connection = FakeConnection(row)
        with patch(
            "app.auth.service.get_read_connection",
            return_value=connection,
        ) as get_read:
            result = find_user_by_id(2)

        get_read.assert_called_once_with(consistency="strong")
        self.assertEqual(result, row)
        self.assertIn("role, is_active", connection.fake_cursor.sql)

    def test_record_successful_login_updates_utc_time_on_primary(self):
        """密码验证成功后必须通过主库提交 UTC 最后登录时间。"""
        connection = FakeConnection(None)
        with patch(
            "app.auth.service.get_write_connection",
            return_value=connection,
        ) as get_write:
            result = record_successful_login(7)

        get_write.assert_called_once_with()
        self.assertTrue(result)
        self.assertTrue(connection.committed)
        self.assertIn("SET last_login_at = UTC_TIMESTAMP()", connection.fake_cursor.sql)
        self.assertEqual(connection.fake_cursor.params, (7,))

    def test_mysql_failure_uses_database_failure_boundary(self):
        """MySQL 异常继续交给数据库日志边界，且不阻断登录。"""
        error = mysql.connector.Error("database-secret")
        with (
            patch(
                "app.auth.service.get_write_connection",
                side_effect=error,
            ),
            patch("app.auth.service.record_database_failure") as record_failure,
            patch("app.auth.service.log_event") as log_event,
        ):
            result = record_successful_login(8)

        self.assertFalse(result)
        record_failure.assert_called_once_with(
            error,
            operation="last_login_write",
        )
        log_event.assert_not_called()

    def test_known_non_mysql_failures_use_stable_reason_codes_and_are_non_blocking(self):
        """非 MySQL 异常按稳定原因码记录，且不把异常原文传给日志事件。"""
        failures = (
            (TimeoutError("timeout-secret"), "query_timeout"),
            (ConnectionError("connection-secret"), "connection_unavailable"),
            (RuntimeError("unexpected-secret"), "unexpected_error"),
        )

        for error, reason_code in failures:
            with self.subTest(reason_code=reason_code):
                with (
                    patch(
                        "app.auth.service.get_write_connection",
                        side_effect=error,
                    ),
                    patch("app.auth.service.log_event") as log_event,
                ):
                    result = record_successful_login(8)

                self.assertFalse(result)
                log_event.assert_called_once()
                self.assertEqual(
                    log_event.call_args.args[1],
                    "auth.login.last_login_update_failed",
                )
                self.assertEqual(
                    log_event.call_args.kwargs["details"],
                    {"reason_code": reason_code},
                )
                self.assertNotIn(str(error), repr(log_event.call_args_list))


class RegistrationContractTests(unittest.TestCase):
    """注册事务必须先建用户、再授予默认角色、最后提交。"""

    def test_register_assigns_default_user_role_before_commit(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(register_user)))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

        user_insert = next(
            node
            for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and "INSERT INTO users" in node.args[0].value
        )
        role_assignment = next(
            node
            for node in calls
            if isinstance(node.func, ast.Name)
            and node.func.id == "replace_user_roles"
        )
        commit = next(
            node
            for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr == "commit"
        )

        self.assertLess(user_insert.lineno, role_assignment.lineno)
        self.assertLess(role_assignment.lineno, commit.lineno)
        self.assertIsInstance(role_assignment.args[2], ast.Tuple)
        role_name = role_assignment.args[2].elts[0]
        self.assertIsInstance(role_name, ast.Name)
        self.assertEqual(role_name.id, "ROLE_USER")


if __name__ == "__main__":
    unittest.main()
