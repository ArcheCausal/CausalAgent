import os
from pathlib import Path
import time
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


from app.admin.contracts import AdminApiError
from app.admin.write_service import (
    _assert_admin_safety,
    _decode_existing_operation,
    _parse_idempotency_key,
    _request_fingerprint,
)
from app.auth.service import managed_password_error
from app import db as database_access
from mysql.connector.errors import PoolError


class ControlledAdminWriteTests(unittest.TestCase):
    """验证受控写入的纯规则、密码边界和 schema 契约。"""

    def test_managed_password_uses_length_without_composition_rule(self):
        """受控改密要求 15 字符，但不强制大小写、数字或特殊字符组合。"""
        self.assertIsNotNone(managed_password_error("short-password"))
        self.assertIsNone(managed_password_error("这是一条足够长且可记忆的中文口令短语"))
        self.assertIsNone(managed_password_error("a" * 15))
        self.assertIsNotNone(managed_password_error("中" * 25))

    def test_idempotency_key_and_hmac_fingerprint_hide_plaintext(self):
        """幂等键有固定字符边界，请求指纹不包含密码明文。"""
        key = _parse_idempotency_key("operation-key-1234")
        self.assertEqual(key, "operation-key-1234")
        with self.assertRaises(AdminApiError):
            _parse_idempotency_key("too-short")
        fingerprint = _request_fingerprint(
            "user.set_password",
            {"new_password": "private-password-value"},
        )
        self.assertEqual(len(fingerprint), 64)
        self.assertNotIn("private-password-value", fingerprint)

    def test_committed_idempotency_result_replays_and_rejects_changed_request(self):
        """并发后读取的同键同参结果可重放，同键异参必须冲突。"""
        fingerprint = _request_fingerprint("user.set_active", {"value": False})
        row = {
            "operation_id": "operation-1",
            "request_fingerprint": fingerprint,
            "status": "succeeded",
            "result_json": '{"target_count": 1, "replayed": false}',
        }
        replay = _decode_existing_operation(row, fingerprint=fingerprint)
        self.assertEqual(replay["operation_id"], "operation-1")
        self.assertTrue(replay["replayed"])
        with self.assertRaises(AdminApiError) as conflict:
            _decode_existing_operation(row, fingerprint="0" * 64)
        self.assertEqual(conflict.exception.code, "idempotency_conflict")

    def test_self_and_last_enabled_admin_are_protected(self):
        """并发事务锁后的最终状态不得禁用操作者或移除最后管理员。"""
        actor = {
            "id": 7,
            "username": "admin",
            "role": "admin",
            "is_active": True,
        }
        with self.assertRaises(AdminApiError) as self_error:
            _assert_admin_safety(
                action="set_active",
                value=False,
                actor_id=7,
                targets=[actor],
                enabled_admin_ids={7, 8},
            )
        self.assertEqual(self_error.exception.code, "self_protected")

        other_admin = {
            "id": 8,
            "username": "other",
            "role": "admin",
            "is_active": True,
        }
        with self.assertRaises(AdminApiError) as last_error:
            _assert_admin_safety(
                action="set_role",
                value="user",
                actor_id=7,
                targets=[other_admin],
                enabled_admin_ids={8},
            )
        self.assertEqual(last_error.exception.code, "last_admin_protected")

    def test_32_migration_is_append_only_and_downgrade_is_scoped(self):
        """3.2 migration 承接 3.1 head，且只回滚本次新增结构。"""
        text = Path(
            "Database/migrations/versions/"
            "e4f5a6b7c8d9_add_controlled_admin_writes.py"
        ).read_text(encoding="utf-8")
        self.assertIn('revision: str = "e4f5a6b7c8d9"', text)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"',
            text,
        )
        self.assertIn("ADD COLUMN auth_version", text)
        self.assertIn("CREATE TABLE admin_operations", text)
        self.assertIn("CREATE TABLE admin_operation_items", text)
        self.assertNotIn("DROP TABLE users", text)

    def test_checkpoint_runtime_and_admin_reads_use_postgres(self):
        """现行 worker 与管理员读取只引用 PostgreSQL checkpoint 链路。"""
        migration = Path(
            "Database/migrations/versions/"
            "f8b9c0d1e2f3_migrate_checkpoints_to_postgres.py"
        ).read_text(encoding="utf-8")
        inspection = Path("Database/checkpoint_inspection.py").read_text(
            encoding="utf-8"
        )
        graph_runner = Path("app/agent/worker/graph_runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("CREATE TABLE checkpoint_cleanup_outbox", migration)
        self.assertIn("DROP TABLE IF EXISTS checkpoints", migration)
        self.assertIn("metadata ->> 'job_id' = %s", inspection)
        self.assertIn("ORDER BY checkpoint_id DESC", inspection)
        self.assertIn('"job_id": job_id', graph_runner)

    def test_user_delete_registers_checkpoint_and_memory_cleanup(self):
        """用户物理删除同时登记父子图 checkpoint 与长期记忆清理。"""
        text = Path("app/admin/write_service.py").read_text(encoding="utf-8")
        self.assertIn("enqueue_checkpoint_cleanup_many(", text)
        self.assertIn("enqueue_user_memory_cleanup(", text)
        self.assertIn('"user_memory_cleanup"', text)
        session_route = Path("app/chat/routes.py").read_text(encoding="utf-8")
        self.assertIn("enqueue_checkpoint_cleanup_many(", session_route)
        self.assertNotIn("enqueue_user_memory_cleanup", session_route)

    def test_lifecycle_repair_is_dry_run_and_requires_database_confirmation(self):
        """孤立修复 CLI 默认 dry-run，apply 必须精确确认数据库。"""
        text = Path("Database/lifecycle_repair.py").read_text(encoding="utf-8")
        self.assertIn('"mode": "dry-run"', text)
        self.assertIn("args.confirm_database != settings.MYSQL_DATABASE", text)
        self.assertIn("MAX_BATCH_LIMIT = 1000", text)
        self.assertNotIn("TRUNCATE ", text)

    def test_pool_acquire_exhaustion_fails_within_bounded_timeout(self):
        """连接池耗尽按配置快速失败，不会无限阻塞 Web/worker。"""

        class ExhaustedPool:
            """模拟始终无法提供连接的 mysql-connector 池。"""

            def get_connection(self):
                raise PoolError("pool exhausted")

        started = time.monotonic()
        with (
            patch.object(
                database_access.database_settings,
                "MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS",
                0.01,
            ),
            patch.object(
                database_access.database_settings,
                "MYSQL_POOL_ACQUIRE_RETRY_MS",
                1,
            ),
            self.assertRaises(PoolError),
        ):
            database_access._acquire_pool_connection(
                ExhaustedPool(),
                target="test",
            )
        self.assertLess(time.monotonic() - started, 0.2)

    def test_replica_status_is_short_cached_per_host(self):
        """同一副本在缓存窗口内只执行一次 SHOW REPLICA STATUS。"""

        class FakeCursor:
            """返回稳定复制状态并记录执行次数。"""

            def __init__(self, owner):
                self.owner = owner

            def execute(self, sql):
                self.owner.executions += 1
                self.owner.sql = sql

            def fetchone(self):
                return {
                    "Replica_IO_Running": "Yes",
                    "Replica_SQL_Running": "Yes",
                    "Seconds_Behind_Source": 0,
                }

        class FakeConnection:
            """提供最小复制状态连接协议。"""

            executions = 0
            sql = ""

            def cursor(self, dictionary=False):
                self.dictionary = dictionary
                return FakeCursor(self)

            def close(self):
                self.closed = True

        connection = FakeConnection()
        database_access._replica_status_cache.clear()
        try:
            with patch.object(
                database_access,
                "_get_replica_status_connection",
                return_value=connection,
            ):
                first = database_access.get_replica_status("replica-test")
                second = database_access.get_replica_status("replica-test")
            self.assertEqual(first, second)
            self.assertEqual(connection.executions, 1)
            self.assertEqual(connection.sql, "SHOW REPLICA STATUS")
        finally:
            database_access._replica_status_cache.clear()


if __name__ == "__main__":
    unittest.main()
