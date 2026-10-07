import os
import unittest
from unittest.mock import ANY, patch

from flask import Flask


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

from app.chat.routes import chat_bp


class FakeCursor:
    """记录删除会话路由执行的 SQL，并按顺序返回查询结果。"""

    def __init__(self, fetch_results, raise_on_prefix=None):
        self.fetch_results = list(fetch_results)
        self.raise_on_prefix = raise_on_prefix
        self.rowcount = 0
        self.statements = []

    def execute(self, sql, params=None):
        """记录 SQL；命中指定前缀时模拟数据库异常。"""
        statement = " ".join(sql.split())
        self.statements.append((statement, params))
        if self.raise_on_prefix and statement.startswith(self.raise_on_prefix):
            raise RuntimeError("checkpoint delete failed")
        self.rowcount = 1

    def fetchone(self):
        """返回当前 SQL 对应的预设单行结果。"""
        return self.fetch_results.pop(0) if self.fetch_results else None

    def fetchall(self):
        """返回当前 SQL 对应的预设多行结果。"""
        return self.fetch_results.pop(0) if self.fetch_results else []


class FakeConnection:
    """模拟删除会话路由使用的事务型 MySQL 连接。"""

    def __init__(self, fetch_results, raise_on_prefix=None):
        self.fake_cursor = FakeCursor(fetch_results, raise_on_prefix)
        self.transaction_started = False
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        """返回连接自身，兼容数据库连接上下文管理器。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """不吞掉路由中的异常。"""
        return False

    def cursor(self, **_kwargs):
        """返回记录 SQL 的游标。"""
        return self.fake_cursor

    def start_transaction(self):
        """记录事务开始。"""
        self.transaction_started = True

    def commit(self):
        """记录事务提交。"""
        self.committed = True

    def rollback(self):
        """记录事务回滚。"""
        self.rolled_back = True


def build_app():
    """构建仅包含聊天蓝图的最小 Flask 测试应用。"""
    app = Flask(__name__)
    app.secret_key = "chat-delete-test-secret"
    app.register_blueprint(chat_bp)
    return app


class DeleteSessionTests(unittest.TestCase):
    """验证会话业务删除与 PostgreSQL checkpoint outbox 的事务边界。"""

    def _post_delete(self, connection, current_user=None):
        """用指定登录用户和数据库连接调用删除会话接口。"""
        app = build_app()
        current_user = current_user or {"id": 7, "username": "owner"}
        with (
            patch("app.chat.routes.get_current_session_user", return_value=current_user),
            patch("app.db.get_write_connection", return_value=connection),
            app.test_client() as client,
        ):
            return client.post("/api/delete_session", json={"session_id": "session-1"})

    def test_delete_session_enqueues_checkpoint_cleanup_in_the_same_transaction(self):
        """正常删除按锁定、任务检查、outbox、业务数据的顺序提交一次。"""
        connection = FakeConnection(
            fetch_results=[
                [{"job_id": "job-1", "status": "succeeded"}],
                {"id": "session-1", "user_id": 7},
                {"status": "pending"},
            ]
        )

        response = self._post_delete(connection)

        self.assertEqual(response.status_code, 202)
        self.assertTrue(connection.transaction_started)
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        statements = connection.fake_cursor.statements
        self.assertEqual(len(statements), 7)
        self.assertEqual(
            statements[0],
            (
                "SELECT job_id, status, execution_state FROM analysis_jobs WHERE session_id = %s AND user_id = %s ORDER BY id FOR UPDATE",
                ("session-1", 7),
            ),
        )
        self.assertEqual(
            statements[1],
            (
                "SELECT id FROM sessions WHERE id = %s AND user_id = %s FOR UPDATE",
                ("session-1", 7),
            ),
        )
        self.assertIn("INSERT INTO checkpoint_cleanup_outbox", statements[2][0])
        self.assertEqual(statements[2][1], ("job-1", None))
        self.assertIn("SELECT status FROM checkpoint_cleanup_outbox", statements[3][0])
        self.assertTrue(statements[4][0].startswith("DELETE ca FROM chat_attachments"))
        self.assertEqual(
            statements[5],
            ("DELETE FROM chat_messages WHERE session_id = %s AND user_id = %s", ("session-1", 7)),
        )
        self.assertEqual(
            statements[6],
            ("DELETE FROM sessions WHERE id = %s AND user_id = %s", ("session-1", 7)),
        )
        # 只删除会话时保留用户长期记忆，不为用户级 namespace 登记清理。
        self.assertFalse(
            any(
                "user_memory_cleanup_outbox" in statement
                for statement, _ in statements
            )
        )

    def test_active_job_blocks_all_delete_statements(self):
        """有 queued/running job 时回滚并拒绝删除 checkpoint 和会话数据。"""
        connection = FakeConnection(
            fetch_results=[
                [{"job_id": "job-1", "status": "running", "execution_state": "leased"}],
                {"id": "session-1", "user_id": 7},
            ]
        )

        response = self._post_delete(connection)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertEqual(len(connection.fake_cursor.statements), 2)
        self.assertFalse(
            any(statement.startswith("DELETE") for statement, _ in connection.fake_cursor.statements)
        )

    def test_checkpoint_outbox_failure_rolls_back_the_whole_transaction(self):
        """outbox 写入异常时，不提交消息或会话的后续删除。"""
        connection = FakeConnection(
            fetch_results=[
                [{"job_id": "job-1", "status": "succeeded"}],
                {"id": "session-1", "user_id": 7},
            ],
            raise_on_prefix="INSERT INTO checkpoint_cleanup_outbox",
        )

        response = self._post_delete(connection)

        self.assertEqual(response.status_code, 500)
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertEqual(len(connection.fake_cursor.statements), 3)

    def test_confirmed_cross_owner_session_logs_security_without_binding_id(self):
        connection = FakeConnection(
            fetch_results=[
                [],
                None,
                {"user_id": 8},
            ]
        )

        with patch("app.chat.routes.log_authorization_denied") as denied:
            response = self._post_delete(connection)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(connection.rolled_back)
        self.assertEqual(len(connection.fake_cursor.statements), 3)
        denied.assert_called_once_with(
            ANY,
            resource_type="session",
            action="delete_session",
        )

    def test_missing_login_does_not_open_a_database_connection(self):
        """未登录请求在鉴权失败后不应访问数据库。"""
        app = build_app()
        with (
            patch("app.chat.routes.get_current_session_user", return_value=None),
            patch("app.db.get_write_connection") as get_write_connection,
            app.test_client() as client,
        ):
            response = client.post("/api/delete_session", json={"session_id": "session-1"})

        self.assertEqual(response.status_code, 401)
        get_write_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
