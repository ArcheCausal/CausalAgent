import os
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch


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
SERVER_A_ID = hashlib.sha256(b"server-a").hexdigest()[:24]
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)


from Database.inspection import (
    _integrity_definitions,
    execute_migration_preflight_checks,
    execute_quick_integrity_checks,
    get_database_overview,
    inspect_slow_queries,
)
from Database.monitoring import (
    _collect_payload,
    _enrich_snapshot,
    _record_is_due,
    collect_due_snapshots,
    collect_snapshot,
    get_dashboard_snapshots,
    get_db_health,
    get_slow_query_summary,
    request_snapshot_refresh,
)
from app.agent.job_service import get_worker_snapshot_report
from app.db import get_read_connection_with_source
from config.database_settings import database_settings
from config.settings import AppConfig, settings
from observability.noise_control import FailureTransitionTracker


def result(status="healthy", value=None, source_alias="primary", warning=None):
    """构造测试使用的标准检查结果。"""
    return {
        "status": status,
        "value": value,
        "observed_at": "2026-07-22T00:00:00.000Z",
        "source_role": "primary" if source_alias == "primary" else "replica",
        "source_alias": source_alias,
        "is_estimate": False,
        "warning": warning,
    }


def monitor_config_payload(*, auto_refresh=True, slow_query_delta=1):
    """构造动态监控配置服务的标准测试响应。"""
    return {
        "version": 1,
        "state": "current",
        "warning": None,
        "effective": {
            "auto_refresh_enabled": auto_refresh,
            "realtime_interval_seconds": 10,
            "sql_interval_seconds": 60,
            "table_capacity_interval_seconds": 900,
            "slow_query_warning_delta": slow_query_delta,
            "integrity_enabled": False,
            "integrity_interval_seconds": 86400,
        },
    }


class IntegrityCursor:
    """逐项返回完整性计数，并可让单项查询失败。"""

    def __init__(self, counts, failing_index=None):
        self.counts = counts
        self.failing_index = failing_index
        self.index = -1

    def execute(self, _sql):
        """前进到下一检查，并在指定位置模拟超时。"""
        self.index += 1
        if self.index == self.failing_index:
            raise TimeoutError("simulated timeout")

    def fetchone(self):
        """返回当前检查的计数。"""
        return {"count_value": self.counts[self.index]}


class IntegrityConnection:
    """为完整性检查提供唯一字典游标。"""

    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, dictionary=False):
        """确认调用方申请字典游标并返回替身。"""
        self.dictionary = dictionary
        return self.fake_cursor


class SlowCursor:
    """返回 SHOW 结果，并模拟 performance_schema 无权限。"""

    def __init__(self):
        self.rows = []

    def execute(self, sql, params=None):
        """根据只读语句设置结果或抛出权限异常。"""
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT @@GLOBAL.server_uuid"):
            self.rows = [{"server_uuid": "server-a"}]
        elif normalized.startswith("SHOW GLOBAL STATUS"):
            self.rows = [
                {"Variable_name": "Slow_queries", "Value": "7"},
                {"Variable_name": "Uptime", "Value": "120"},
            ]
        elif normalized.startswith("SHOW GLOBAL VARIABLES"):
            self.rows = [
                {"Variable_name": "slow_query_log", "Value": "ON"},
                {"Variable_name": "long_query_time", "Value": "2.0"},
            ]
        elif "performance_schema.events_statements_summary_by_digest" in normalized:
            raise PermissionError("performance_schema denied")
        else:
            raise AssertionError(normalized)
        self.params = params

    def fetchall(self):
        """返回当前 SHOW 查询结果。"""
        return self.rows

    def fetchone(self):
        """返回单行 MySQL 实例标识。"""
        return self.rows[0] if self.rows else None


class SqlPerformanceCursor:
    """返回可配置的累计慢查询状态与 SQL digest。"""

    def __init__(self, slow_queries, uptime=120, statements=None, server_uuid="server-a"):
        self.slow_queries = slow_queries
        self.uptime = uptime
        self.server_uuid = server_uuid
        self.statements = statements or [{"digest_text": "SELECT ?", "total_seconds": 3.5}]
        self.rows = []
        self.digest_sql = None

    def execute(self, sql, params=None):
        """根据采集语句切换结果集。"""
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT @@GLOBAL.server_uuid"):
            self.rows = [{"server_uuid": self.server_uuid}]
        elif normalized.startswith("SHOW GLOBAL STATUS"):
            self.rows = [
                {"Variable_name": "Slow_queries", "Value": str(self.slow_queries)},
                {"Variable_name": "Uptime", "Value": str(self.uptime)},
            ]
        elif normalized.startswith("SHOW GLOBAL VARIABLES"):
            self.rows = [
                {"Variable_name": "slow_query_log", "Value": "ON"},
                {"Variable_name": "long_query_time", "Value": "2.0"},
            ]
        elif "performance_schema.events_statements_summary_by_digest" in normalized:
            self.digest_sql = normalized
            self.rows = self.statements
        else:
            raise AssertionError(normalized)
        self.params = params

    def fetchall(self):
        """返回当前 SQL 对应的结果集。"""
        return self.rows

    def fetchone(self):
        """返回单行 MySQL 实例标识。"""
        return self.rows[0] if self.rows else None


class ContextConnection:
    """支持 with 语法并返回指定游标的连接替身。"""

    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        """返回连接自身。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """不吞掉异常。"""
        return False

    def cursor(self, dictionary=False):
        """返回唯一游标。"""
        self.dictionary = dictionary
        return self.fake_cursor


class SnapshotWriteCursor:
    """记录共享快照刷新请求的写入参数。"""

    def __init__(self, requested_at=None):
        self.calls = []
        self.requested_at = requested_at

    def execute(self, sql, params=None):
        """保存执行语句与参数供断言使用。"""
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        """返回数据库生成的共享刷新请求时间。"""
        return {"requested_at": self.requested_at}


class SnapshotWriteConnection(ContextConnection):
    """支持事务提交计数的快照写连接替身。"""

    def __init__(self, requested_at=None):
        super().__init__(SnapshotWriteCursor(requested_at=requested_at))
        self.commit_count = 0

    def commit(self):
        """记录事务已提交。"""
        self.commit_count += 1


class BusySnapshotLockCursor(SnapshotWriteCursor):
    """模拟另一个 monitor 已持有同类快照的命名锁。"""

    def fetchone(self):
        """返回未取得 MySQL 命名锁。"""
        return {"acquired": 0}


class FailedSnapshotLockCursor(SnapshotWriteCursor):
    """模拟 GET_LOCK 操作没有返回可判定结果。"""

    def fetchone(self):
        return {"acquired": None}


class AcquiredSnapshotLockCursor(SnapshotWriteCursor):
    """模拟已取得命名锁并支持释放锁结果。"""

    def fetchone(self):
        """首次读取表示成功取得锁，后续读取表示成功释放。"""
        if self.calls and "release_lock" in self.calls[-1][0].lower():
            return {"released": 1}
        return {"acquired": 1}


class WorkerCursor:
    """依次返回 worker 聚合计数和活动任务明细。"""

    def __init__(self):
        self.query_index = 0

    def execute(self, _sql):
        """记录当前是聚合查询还是明细查询。"""
        self.query_index += 1

    def fetchone(self):
        """返回包含异常任务的聚合结果。"""
        return {"queued": 2, "running": 1, "stale": 1, "max_attempts_running": 0}

    def fetchall(self):
        """返回一个活动任务。"""
        return [{
            "job_id": "job-1",
            "status": "running",
            "worker_id": "worker-1",
            "heartbeat_at": None,
            "attempt_count": 1,
            "max_attempts": 3,
            "created_at": None,
        }]


class PreflightCursor:
    """按当前 schema 返回表和外键，并记录是否触发全表扫描。"""

    def __init__(self, tables=(), foreign_keys=(), revision=None):
        self.tables = set(tables)
        if revision:
            self.tables.add("alembic_version")
        self.foreign_keys = set(foreign_keys)
        self.revision = revision
        self.rows = []
        self.executed_sql = []

    def execute(self, sql):
        """为 schema 元数据查询提供结果，其他查询返回零条问题。"""
        normalized = " ".join(sql.lower().split())
        self.executed_sql.append(normalized)
        if "from information_schema.tables" in normalized:
            self.rows = [{"table_name": table} for table in self.tables]
        elif "select version_num from alembic_version" in normalized:
            self.rows = [{"version_num": self.revision}] if self.revision else []
        elif "from information_schema.referential_constraints" in normalized:
            self.rows = [
                {"table_name": table, "constraint_name": constraint}
                for table, constraint in self.foreign_keys
            ]
        else:
            self.rows = [{"count_value": 0}]

    def fetchall(self):
        """返回当前元数据查询结果。"""
        return self.rows

    def fetchone(self):
        """返回当前计数查询结果。"""
        return self.rows[0]


class DatabaseMonitorConfigTests(unittest.TestCase):
    """验证数据库监控配置的严格解析、默认值和允许范围。"""

    def build_config(self, **overrides):
        """使用最小必需环境变量构造隔离的配置实例。"""
        env = {**TEST_ENV, **{key: str(value) for key, value in overrides.items()}}
        with patch.dict(os.environ, env, clear=True):
            return AppConfig()

    def test_monitor_defaults_match_layered_collection_policy(self):
        """未显式配置时使用已约定的分层采集默认值。"""
        config = self.build_config()

        self.assertTrue(config.DB_MONITOR_AUTO_REFRESH_ENABLED)
        self.assertEqual(config.DB_MONITOR_REALTIME_INTERVAL_SECONDS, 10)
        self.assertEqual(config.DB_MONITOR_SQL_INTERVAL_SECONDS, 60)
        self.assertEqual(config.DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS, 900)
        self.assertEqual(config.DB_MONITOR_SLOW_QUERY_WARNING_DELTA, 1)
        self.assertFalse(config.DB_MONITOR_INTEGRITY_ENABLED)
        self.assertEqual(config.DB_MONITOR_INTEGRITY_INTERVAL_SECONDS, 86400)

    def test_monitor_booleans_only_accept_explicit_supported_values(self):
        """布尔配置只接受 true/false，且忽略大小写和首尾空格。"""
        accepted = {
            "true": True,
            " TRUE ": True,
            "false": False,
            " False ": False,
        }
        for raw_value, expected in accepted.items():
            with self.subTest(raw_value=raw_value):
                config = self.build_config(
                    DB_MONITOR_AUTO_REFRESH_ENABLED=raw_value,
                    DB_MONITOR_INTEGRITY_ENABLED=raw_value,
                )
                self.assertIs(config.DB_MONITOR_AUTO_REFRESH_ENABLED, expected)
                self.assertIs(config.DB_MONITOR_INTEGRITY_ENABLED, expected)

        for raw_value in ("yes", "on", "enabled", "1", "0", "2"):
            with self.subTest(invalid=raw_value):
                with self.assertRaises(ValueError):
                    self.build_config(DB_MONITOR_AUTO_REFRESH_ENABLED=raw_value)

    def test_monitor_interval_boundaries_are_inclusive(self):
        """实时、SQL、容量和完整性周期接受各自边界值。"""
        minimums = self.build_config(
            DB_MONITOR_REALTIME_INTERVAL_SECONDS=5,
            DB_MONITOR_SQL_INTERVAL_SECONDS=30,
            DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS=300,
            DB_MONITOR_INTEGRITY_INTERVAL_SECONDS=3600,
        )
        maximums = self.build_config(
            DB_MONITOR_REALTIME_INTERVAL_SECONDS=10,
            DB_MONITOR_SQL_INTERVAL_SECONDS=60,
            DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS=900,
        )

        self.assertEqual(minimums.DB_MONITOR_REALTIME_INTERVAL_SECONDS, 5)
        self.assertEqual(minimums.DB_MONITOR_SQL_INTERVAL_SECONDS, 30)
        self.assertEqual(minimums.DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS, 300)
        self.assertEqual(minimums.DB_MONITOR_INTEGRITY_INTERVAL_SECONDS, 3600)
        self.assertEqual(maximums.DB_MONITOR_REALTIME_INTERVAL_SECONDS, 10)
        self.assertEqual(maximums.DB_MONITOR_SQL_INTERVAL_SECONDS, 60)
        self.assertEqual(maximums.DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS, 900)

    def test_monitor_rejects_out_of_range_intervals_and_threshold(self):
        """超出允许范围的周期和非正慢查询阈值在启动时失败。"""
        invalid_cases = (
            ("DB_MONITOR_REALTIME_INTERVAL_SECONDS", 4),
            ("DB_MONITOR_REALTIME_INTERVAL_SECONDS", 11),
            ("DB_MONITOR_SQL_INTERVAL_SECONDS", 29),
            ("DB_MONITOR_SQL_INTERVAL_SECONDS", 61),
            ("DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS", 299),
            ("DB_MONITOR_TABLE_CAPACITY_INTERVAL_SECONDS", 901),
            ("DB_MONITOR_INTEGRITY_INTERVAL_SECONDS", 3599),
            ("DB_MONITOR_SLOW_QUERY_WARNING_DELTA", 0),
            ("DB_MONITOR_SLOW_QUERY_WARNING_DELTA", -1),
        )
        for key, value in invalid_cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    self.build_config(**{key: value})


class DatabaseInspectionTests(unittest.TestCase):
    """验证只读检查的来源、降级和旧接口兼容行为。"""

    def test_integrity_single_failure_does_not_stop_other_checks(self):
        """单项超时只标记该项未知，其余检查继续返回。"""
        definitions = _integrity_definitions(3000)
        healthy_counts = [
            1 if definition.get("healthy_when") == "one" else 0
            for definition in definitions
        ]
        connection = IntegrityConnection(IntegrityCursor(healthy_counts, failing_index=1))
        checks = execute_quick_integrity_checks(
            connection,
            timeout_ms=3000,
            source_role="primary",
            source_alias="primary",
        )

        self.assertEqual(len(checks), len(definitions))
        self.assertEqual(checks[1]["status"], "unknown")
        self.assertEqual(
            checks[1]["warning"],
            "检查失败（权限不足、查询超时或节点不可用）",
        )
        self.assertIn("chat_messages.user_id", checks[1]["description"])
        self.assertEqual(checks[2]["status"], "healthy")
        self.assertTrue(all(check["description"] for check in checks))
        self.assertTrue(all(check["source_alias"] == "primary" for check in checks))
        self.assertTrue(all("observed_at" in check for check in checks))

    def test_runtime_integrity_avoids_fk_protected_full_table_joins(self):
        """运行期审计不扫描已有外键关系，并检查 cleanup outbox 状态。"""
        definitions = _integrity_definitions(3000)
        keys = {definition["key"] for definition in definitions}
        sql_by_key = {
            definition["key"]: " ".join(definition["sql"].lower().split())
            for definition in definitions
        }
        removed_keys = {
            "orphan_chat_messages_session",
            "orphan_chat_messages_user",
            "orphan_chat_attachments_message",
            "orphan_analysis_jobs_user",
            "orphan_analysis_jobs_session",
            "orphan_analysis_job_events",
            "orphan_checkpoint_writes",
            "chat_messages_partitions",
        }

        self.assertTrue(removed_keys.isdisjoint(keys))
        self.assertIn("checkpoint_cleanup_failed", keys)
        self.assertIn("checkpoint_cleanup_outbox", sql_by_key["checkpoint_cleanup_failed"])
        self.assertIn("user_memory_cleanup_failed", keys)
        self.assertIn(
            "user_memory_cleanup_outbox",
            sql_by_key["user_memory_cleanup_failed"],
        )
        self.assertIn(
            "idx_user_memory_cleanup_outbox_claim",
            sql_by_key["constraint_user_memory_cleanup_outbox_claim"],
        )
        self.assertTrue(any("information_schema" in sql for sql in sql_by_key.values()))
        checkpoint_fk_sql = sql_by_key["constraint_fk_checkpoint_cleanup_outbox_operation"]
        self.assertIn("information_schema.key_column_usage", checkpoint_fk_sql)
        self.assertIn("ordinal_position = 1", checkpoint_fk_sql)
        self.assertIn("column_name = 'operation_id'", checkpoint_fk_sql)
        self.assertIn("referenced_column_name = 'operation_id'", checkpoint_fk_sql)
        memory_fk_sql = sql_by_key["constraint_fk_user_memory_cleanup_outbox_operation"]
        self.assertIn("information_schema.key_column_usage", memory_fk_sql)
        self.assertIn("ordinal_position = 1", memory_fk_sql)
        descriptions = {definition["key"]: definition["description"] for definition in definitions}
        self.assertIn("visualization", descriptions["constraint_chat_attachment_type_enum"])
        self.assertIn("report_document", descriptions["constraint_chat_attachment_type_enum"])
        self.assertIn("数量为 0 时健康", descriptions["checkpoint_cleanup_failed"])
        self.assertIn("长期记忆", descriptions["user_memory_cleanup_failed"])

    def test_migration_preflight_skips_tables_not_present_in_current_schema(self):
        """新库或较早 schema 尚无未来表时，预检标为不适用而不是失败。"""
        cursor = PreflightCursor(tables={"users", "sessions"})
        checks = execute_migration_preflight_checks(
            IntegrityConnection(cursor),
            timeout_ms=3000,
        )

        self.assertTrue(checks)
        self.assertTrue(all(check["status"] == "healthy" for check in checks))
        self.assertTrue(all(check["applicable"] is False for check in checks))
        self.assertFalse(any(" left join " in sql for sql in cursor.executed_sql))

    def test_migration_preflight_skips_relationships_with_existing_foreign_keys(self):
        """目标外键已存在时，升级预检不再重复执行孤立关系扫描。"""
        foreign_keys = {
            ("chat_messages", "fk_chat_messages_session"),
            ("chat_messages", "fk_chat_messages_user"),
            ("chat_attachments", "fk_chat_attachments_message"),
        }
        cursor = PreflightCursor(
            tables={
                "users",
                "sessions",
                "chat_messages",
                "chat_attachments",
            },
            foreign_keys=foreign_keys,
        )
        checks = execute_migration_preflight_checks(
            IntegrityConnection(cursor),
            timeout_ms=3000,
        )

        self.assertTrue(all(check["applicable"] is False for check in checks))
        self.assertFalse(any(" left join " in sql for sql in cursor.executed_sql))

    def test_migration_preflight_skips_scans_after_fk_revision(self):
        """当前 revision 已越过外键迁移时，即使 schema 损坏也不误称迁移前扫描。"""
        cursor = PreflightCursor(
            tables={
                "users",
                "sessions",
                "chat_messages",
                "chat_attachments",
            },
            revision="b1c2d3e4f5a6",
        )
        checks = execute_migration_preflight_checks(
            IntegrityConnection(cursor),
            timeout_ms=3000,
        )

        self.assertTrue(all(check["applicable"] is False for check in checks))
        self.assertFalse(any(" left join " in sql for sql in cursor.executed_sql))

    def test_migration_preflight_runs_scans_before_fk_revision(self):
        """旧库位于目标外键迁移之前时，只对已存在且缺约束的关系执行扫描。"""
        cursor = PreflightCursor(
            tables={
                "users",
                "sessions",
                "chat_messages",
                "chat_attachments",
            },
            revision="d876b980dc9a",
        )
        checks = execute_migration_preflight_checks(
            IntegrityConnection(cursor),
            timeout_ms=3000,
        )

        self.assertTrue(any(check["applicable"] is True for check in checks))
        self.assertTrue(any(" left join " in sql for sql in cursor.executed_sql))

    def test_read_connection_reports_logical_source_without_hostname(self):
        """强一致和副本读取只返回 primary/replica-N 逻辑别名。"""
        primary_connection = object()
        primary_pool = Mock()
        primary_pool.get_connection.return_value = primary_connection
        with (
            patch.object(database_settings, "MYSQL_WRITE_HOST", "internal-primary-host"),
            patch.object(database_settings, "MYSQL_READ_HOSTS", []),
            patch("app.db._get_read_pool", return_value=primary_pool),
        ):
            connection, source = get_read_connection_with_source("strong")

        self.assertIs(connection, primary_connection)
        self.assertEqual(source, {"source_role": "primary", "source_alias": "primary"})
        self.assertNotIn("internal-primary-host", source.values())

        replica_connection = object()
        replica_pool = Mock()
        replica_pool.get_connection.return_value = replica_connection
        with (
            patch.object(database_settings, "MYSQL_READ_HOSTS", ["internal-replica-host"]),
            patch("app.db.should_use_replica", return_value=True),
            patch("app.db.random.shuffle"),
            patch("app.db._get_read_pool", return_value=replica_pool),
        ):
            connection, source = get_read_connection_with_source("eventual")

        self.assertIs(connection, replica_connection)
        self.assertEqual(source, {"source_role": "replica", "source_alias": "replica-1"})
        self.assertNotIn("internal-replica-host", source.values())

    def test_overview_preserves_partial_results(self):
        """主库区块未知时 revision、从库、连接和容量结果仍被保留。"""
        with (
            patch("Database.inspection.inspect_revision", return_value=result(value={"matches": True})),
            patch(
                "Database.inspection.inspect_primary",
                return_value=result("unknown", warning="主库检查失败"),
            ),
            patch("Database.inspection.inspect_replica", return_value=result(value={"available": True})),
            patch("Database.inspection.inspect_connections", return_value=result(value={"utilization_percent": 2})),
            patch("Database.inspection.inspect_table_capacity", return_value=result(value=[])),
        ):
            overview = get_database_overview()

        self.assertEqual(overview["status"], "warning")
        self.assertEqual(overview["revision"]["status"], "healthy")
        self.assertEqual(overview["primary"]["status"], "unknown")
        self.assertEqual(overview["tables"]["value"], [])

    def test_slow_query_permission_failure_returns_warning_metadata(self):
        """performance_schema 无权限时保留配置和累计计数，不返回 500。"""
        connection = ContextConnection(SlowCursor())
        with patch(
            "app.db.get_read_connection_with_source",
            return_value=(connection, {"source_role": "replica", "source_alias": "replica-1"}),
        ):
            slow = inspect_slow_queries(limit=20)

        self.assertEqual(slow["status"], "warning")
        self.assertEqual(slow["source_alias"], "replica-1")
        self.assertEqual(slow["value"]["Slow_queries"], 7)
        self.assertEqual(slow["value"]["top_statements"], [])
        self.assertIn("performance_schema", slow["warning"])

    def test_sql_performance_first_collection_establishes_baseline(self):
        """首次采集只记录累计值，不把历史累计量误报为周期增量。"""
        connection = ContextConnection(SqlPerformanceCursor(slow_queries=12, uptime=500))
        with (
            patch(
                "app.db.get_read_connection_with_source",
                return_value=(connection, {"source_role": "primary", "source_alias": "primary"}),
            ) as get_connection,
            patch("Database.inspection._observed_at", return_value="2026-07-22T00:01:00.000Z"),
        ):
            report = inspect_slow_queries(limit=20)

        get_connection.assert_called_once_with(consistency="strong")
        value = report["value"]
        self.assertTrue(value["baseline_reset"])
        self.assertIsNone(value["slow_queries_delta"])
        self.assertIsNone(value["window_started_at"])
        self.assertEqual(value["slow_queries_total"], 12)
        self.assertEqual(value["Slow_queries"], 12)
        self.assertEqual(value["high_load_statements"], value["top_statements"])

    def test_sql_performance_orders_digest_by_average_then_total_duration(self):
        """Digest 先按平均耗时降序选取，并用累计耗时稳定处理同值。"""
        statements = [{
            "digest_text": "SELECT * FROM users WHERE id = ?",
            "count_star": 2,
            "total_seconds": 0.5,
            "avg_seconds": 0.25,
            "rows_examined": 2,
            "rows_sent": 2,
        }]
        cursor = SqlPerformanceCursor(slow_queries=12, uptime=500, statements=statements)
        connection = ContextConnection(cursor)
        with patch(
            "app.db.get_read_connection_with_source",
            return_value=(connection, {"source_role": "primary", "source_alias": "primary"}),
        ):
            report = inspect_slow_queries(limit=20)

        self.assertIn(
            "ORDER BY AVG_TIMER_WAIT DESC, SUM_TIMER_WAIT DESC",
            cursor.digest_sql,
        )
        self.assertEqual(cursor.params, (20,))
        self.assertEqual(report["value"]["high_load_statements"], statements)
        self.assertEqual(report["value"]["top_statements"], statements)

    def test_sql_performance_calculates_delta_window_and_threshold_warning(self):
        """同一主库计数增长按窗口计算，并在达到阈值时告警。"""
        previous = result(value={
            "slow_queries_total": 7,
            "Slow_queries": 7,
            "uptime_seconds": 440,
            "source_instance_id": SERVER_A_ID,
            "window_ended_at": "2026-07-22T00:00:00.000Z",
        })
        connection = ContextConnection(SqlPerformanceCursor(slow_queries=10, uptime=500))
        with (
            patch(
                "app.db.get_read_connection_with_source",
                return_value=(connection, {"source_role": "primary", "source_alias": "primary"}),
            ),
            patch("Database.inspection._observed_at", return_value="2026-07-22T00:01:00.000Z"),
            patch.object(settings, "DB_MONITOR_SLOW_QUERY_WARNING_DELTA", 3),
        ):
            report = inspect_slow_queries(limit=20, previous=previous)

        value = report["value"]
        self.assertFalse(value["baseline_reset"])
        self.assertEqual(value["slow_queries_delta"], 3)
        self.assertEqual(value["window_started_at"], "2026-07-22T00:00:00.000Z")
        self.assertEqual(value["window_seconds"], 60.0)
        self.assertEqual(value["slow_query_warning_threshold"], 3)
        self.assertEqual(report["status"], "warning")
        self.assertIn("告警阈值", report["warning"])

    def test_sql_performance_normal_delta_below_threshold_is_healthy(self):
        """正常窗口增量低于阈值时保持健康，同时保留准确的增量值。"""
        previous = result(value={
            "slow_queries_total": 7,
            "uptime_seconds": 440,
            "source_instance_id": SERVER_A_ID,
            "window_ended_at": "2026-07-22T00:00:00.000Z",
        })
        connection = ContextConnection(SqlPerformanceCursor(slow_queries=9, uptime=500))
        with (
            patch(
                "app.db.get_read_connection_with_source",
                return_value=(connection, {"source_role": "primary", "source_alias": "primary"}),
            ),
            patch("Database.inspection._observed_at", return_value="2026-07-22T00:01:00.000Z"),
            patch.object(settings, "DB_MONITOR_SLOW_QUERY_WARNING_DELTA", 3),
        ):
            report = inspect_slow_queries(limit=20, previous=previous)

        self.assertEqual(report["value"]["slow_queries_delta"], 2)
        self.assertEqual(report["status"], "healthy")
        self.assertIsNone(report["warning"])

    def test_sql_performance_counter_rollback_resets_baseline(self):
        """累计计数或 Uptime 回退时重建基线，绝不产生负增量。"""
        previous = result(value={
            "slow_queries_total": 7,
            "uptime_seconds": 500,
            "source_instance_id": SERVER_A_ID,
            "window_ended_at": "2026-07-22T00:00:00.000Z",
        })
        connection = ContextConnection(SqlPerformanceCursor(slow_queries=2, uptime=30))
        with (
            patch(
                "app.db.get_read_connection_with_source",
                return_value=(connection, {"source_role": "primary", "source_alias": "primary"}),
            ),
            patch("Database.inspection._observed_at", return_value="2026-07-22T00:01:00.000Z"),
        ):
            report = inspect_slow_queries(limit=20, previous=previous)

        self.assertTrue(report["value"]["baseline_reset"])
        self.assertIsNone(report["value"]["slow_queries_delta"])
        self.assertIsNone(report["value"]["window_seconds"])

    def test_sql_performance_source_instance_change_resets_baseline(self):
        """主库逻辑别名不变但 server UUID 变化时重建累计计数基线。"""
        previous = result(value={
            "slow_queries_total": 7,
            "uptime_seconds": 500,
            "source_instance_id": SERVER_A_ID,
            "window_ended_at": "2026-07-22T00:00:00.000Z",
        })
        connection = ContextConnection(SqlPerformanceCursor(
            slow_queries=20,
            uptime=800,
            server_uuid="server-b",
        ))
        with (
            patch(
                "app.db.get_read_connection_with_source",
                return_value=(connection, {"source_role": "primary", "source_alias": "primary"}),
            ),
            patch("Database.inspection._observed_at", return_value="2026-07-22T00:01:00.000Z"),
        ):
            report = inspect_slow_queries(limit=20, previous=previous)

        self.assertTrue(report["value"]["baseline_reset"])
        self.assertIsNone(report["value"]["slow_queries_delta"])
        self.assertNotEqual(report["value"]["source_instance_id"], SERVER_A_ID)

    def test_snapshot_due_respects_layer_interval_and_manual_request(self):
        """自动周期按层判断到期，而新于快照的手动请求始终优先。"""
        now = datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc)
        policy = {
            "auto_refresh_enabled": True,
            "realtime_interval_seconds": 10,
            "sql_interval_seconds": 60,
            "table_capacity_interval_seconds": 900,
            "slow_query_warning_delta": 1,
            "integrity_enabled": False,
            "integrity_interval_seconds": 86400,
        }
        with patch("Database.monitoring._utc_now", return_value=now):
            recent = {"observed_at": now - timedelta(seconds=9)}
            due = {"observed_at": now - timedelta(seconds=10)}
            requested = {
                "observed_at": now - timedelta(seconds=1),
                "refresh_requested_at": now,
            }
            self.assertFalse(_record_is_due("realtime", recent, policy=policy))
            self.assertTrue(_record_is_due("realtime", due, policy=policy))
            self.assertTrue(_record_is_due("realtime", requested, policy=policy))

    def test_auto_refresh_off_still_allows_manual_refresh(self):
        """总开关关闭时不执行缺失或到期采集，但仍处理显式刷新请求。"""
        now = datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc)
        policy = {
            "auto_refresh_enabled": False,
            "realtime_interval_seconds": 10,
            "sql_interval_seconds": 60,
            "table_capacity_interval_seconds": 900,
            "slow_query_warning_delta": 1,
            "integrity_enabled": False,
            "integrity_interval_seconds": 86400,
        }
        with patch("Database.monitoring._utc_now", return_value=now):
            self.assertFalse(_record_is_due("realtime", None, policy=policy))
            self.assertFalse(_record_is_due(
                "capacity",
                {"observed_at": now - timedelta(days=1)},
                policy=policy,
            ))
            self.assertTrue(_record_is_due(
                "integrity",
                {"observed_at": None, "refresh_requested_at": now},
                policy=policy,
            ))

    def test_due_collection_with_auto_off_only_processes_manual_request(self):
        """monitor 在自动刷新关闭后保持空闲，仅响应仍被允许的手动请求。"""
        now = datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc)
        records = {
            "realtime": {"observed_at": now - timedelta(days=1)},
            "sql_performance": {"observed_at": None},
            "capacity": {"observed_at": now - timedelta(days=1)},
            "integrity": {"observed_at": None, "refresh_requested_at": now},
        }
        with (
            patch("Database.monitoring._utc_now", return_value=now),
            patch(
                "Database.monitoring.get_monitor_settings",
                return_value=monitor_config_payload(auto_refresh=False),
            ),
            patch("Database.monitoring._read_snapshot_records", return_value=records),
            patch("Database.monitoring.collect_snapshot", return_value=True) as collect,
        ):
            result_by_group = collect_due_snapshots()

        self.assertEqual(result_by_group, {"integrity": True})
        collect.assert_called_once_with("integrity", require_due=True)

    def test_snapshot_enrichment_marks_stale_and_pending_independently(self):
        """快照过期和手动请求待处理是独立状态，均由统一元数据计算。"""
        now = datetime(2026, 7, 22, 0, 10, tzinfo=timezone.utc)
        observed_at = now - timedelta(seconds=25)
        requested_at = now - timedelta(seconds=5)
        row = {
            "payload_json": {"status": "healthy", "observed_at": observed_at.isoformat()},
            "observed_at": observed_at,
            "refresh_requested_at": requested_at,
        }
        policy = {
            "auto_refresh_enabled": True,
            "realtime_interval_seconds": 10,
            "sql_interval_seconds": 60,
            "table_capacity_interval_seconds": 900,
            "slow_query_warning_delta": 1,
            "integrity_enabled": False,
            "integrity_interval_seconds": 86400,
        }
        with patch("Database.monitoring._utc_now", return_value=now):
            snapshot = _enrich_snapshot("realtime", row, policy=policy)

        self.assertTrue(snapshot["is_stale"])
        self.assertTrue(snapshot["refresh_pending"])
        self.assertTrue(snapshot["scheduled"])
        self.assertEqual(snapshot["interval_seconds"], 10)

    def test_refresh_request_deduplicates_groups_and_commits_once(self):
        """并发页面使用同一行登记请求，重复分组不会产生重复写入。"""
        requested_at = datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc)
        connection = SnapshotWriteConnection(requested_at=requested_at.replace(tzinfo=None))
        with patch("Database.monitoring.get_write_connection", return_value=connection):
            response = request_snapshot_refresh(("realtime", "capacity", "realtime"))

        self.assertEqual(response["groups"], ["realtime", "capacity"])
        self.assertEqual(response["requested_at"], "2026-07-22T00:01:00.000Z")
        self.assertEqual(len(connection.fake_cursor.calls), 3)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.fake_cursor.calls[0][1], ("realtime",))
        self.assertEqual(connection.fake_cursor.calls[1][1], ("capacity",))
        self.assertIn("greatest", connection.fake_cursor.calls[0][0].lower())
        with self.assertRaises(ValueError):
            request_snapshot_refresh(("unknown",))

    def test_snapshot_collection_skips_when_named_lock_is_busy(self):
        """多个 monitor 竞争同一分层时，未取得命名锁的实例不执行采集。"""
        connection = SnapshotWriteConnection()
        connection.fake_cursor = BusySnapshotLockCursor()
        with (
            patch("Database.monitoring.get_write_connection", return_value=connection),
            patch("Database.monitoring._read_snapshot_records") as read_records,
            patch("Database.monitoring._collect_payload") as collect_payload,
            patch(
                "Database.monitoring._LOCK_FAILURES",
                FailureTransitionTracker(),
            ),
            patch("Database.monitoring.log_event") as log_event,
        ):
            collected = collect_snapshot("realtime")

        self.assertFalse(collected)
        read_records.assert_not_called()
        collect_payload.assert_not_called()
        log_event.assert_not_called()

    def test_snapshot_lock_unknown_result_is_a_limited_operation_failure(self):
        connection = SnapshotWriteConnection()
        connection.fake_cursor = FailedSnapshotLockCursor()
        with (
            patch("Database.monitoring.get_write_connection", return_value=connection),
            patch(
                "Database.monitoring._LOCK_FAILURES",
                FailureTransitionTracker(),
            ),
            patch("Database.monitoring.log_event") as log_event,
        ):
            collected = collect_snapshot("realtime")

        self.assertFalse(collected)
        log_event.assert_called_once()
        self.assertEqual(log_event.call_args.args[1], "monitor.lock.failed")

    def test_snapshot_collection_rechecks_due_state_after_named_lock(self):
        """等待命名锁后再次核对快照，避免并发 monitor 重复执行同一采集。"""
        now = datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc)
        connection = SnapshotWriteConnection()
        connection.fake_cursor = AcquiredSnapshotLockCursor()
        with (
            patch("Database.monitoring.get_write_connection", return_value=connection),
            patch("Database.monitoring._utc_now", return_value=now),
            patch(
                "Database.monitoring.get_monitor_settings",
                return_value=monitor_config_payload(),
            ),
            patch(
                "Database.monitoring._read_snapshot_records",
                return_value={"realtime": {"observed_at": now}},
            ),
            patch("Database.monitoring._collect_payload") as collect_payload,
        ):
            collected = collect_snapshot("realtime", require_due=True)

        self.assertFalse(collected)
        collect_payload.assert_not_called()

    def test_snapshot_collection_persists_degraded_payload_on_collector_failure(self):
        """单层采集器异常只更新该层为未知状态，不让旧快照伪装成新鲜数据。"""
        connection = SnapshotWriteConnection()
        connection.fake_cursor = AcquiredSnapshotLockCursor()
        with (
            patch("Database.monitoring.get_write_connection", return_value=connection),
            patch("Database.monitoring._read_snapshot_records", return_value={}),
            patch(
                "Database.monitoring.get_monitor_settings",
                return_value=monitor_config_payload(),
            ),
            patch("Database.monitoring._collect_payload", side_effect=RuntimeError("boom")),
        ):
            collected = collect_snapshot("sql_performance")

        self.assertTrue(collected)
        write_calls = [
            call for call in connection.fake_cursor.calls
            if "insert into database_monitor_snapshots" in call[0].lower()
        ]
        self.assertEqual(len(write_calls), 1)
        payload = json.loads(write_calls[0][1][1])
        self.assertEqual(payload["status"], "unknown")
        self.assertTrue(payload["baseline_reset"])
        self.assertEqual(payload["top_statements"], [])

    def test_dashboard_read_never_invokes_live_collectors(self):
        """管理页面读取只查询共享快照，不触发任一现场采集器。"""
        with (
            patch("Database.monitoring._read_snapshot_records", return_value={}),
            patch(
                "Database.monitoring.get_monitor_settings",
                return_value=monitor_config_payload(),
            ),
            patch("Database.monitoring.get_realtime_report") as realtime_collector,
            patch("Database.monitoring.inspect_slow_queries") as sql_collector,
            patch("Database.monitoring.get_capacity_report") as capacity_collector,
            patch("Database.monitoring.get_quick_integrity_report") as integrity_collector,
        ):
            dashboard = get_dashboard_snapshots()

        self.assertEqual(
            set(dashboard),
            {"realtime", "sql_performance", "capacity", "integrity", "refresh_policy"},
        )
        for collector in (
            realtime_collector,
            sql_collector,
            capacity_collector,
            integrity_collector,
        ):
            collector.assert_not_called()

    def test_sql_collection_uses_resolved_dynamic_warning_threshold(self):
        """SQL 采集必须使用数据库/环境统一解析后的动态慢查询阈值。"""
        policy = monitor_config_payload(slow_query_delta=7)["effective"]
        with patch(
            "Database.monitoring.get_slow_query_summary",
            return_value={"status": "healthy"},
        ) as summary:
            payload = _collect_payload(
                "sql_performance",
                {"Slow_queries": 3},
                policy=policy,
            )

        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(summary.call_args.kwargs["warning_threshold"], 7)

    def test_legacy_monitoring_fields_are_kept_with_additive_metadata(self):
        """兼容层继续返回旧字段，并追加来源与采集信息。"""
        overview = {
            "status": "healthy",
            "observed_at": "2026-07-22T00:00:00.000Z",
            "connections": result(value={
                "threads_connected": 2,
                "threads_running": 1,
                "max_connections": 100,
                "slow_queries": 3,
            }),
            "replica": result(value={
                "available": True,
                "io_running": "Yes",
                "sql_running": "Yes",
                "lag_seconds": 0,
                "last_io_error": None,
                "last_sql_error": None,
            }, source_alias="replica-1"),
            "tables": result(value=[{"table_name": "users"}], source_alias="replica-1"),
        }
        dashboard = {
            "realtime": {
                **overview,
                "jobs": {},
            },
            "capacity": {
                "revision": result(value={"matches": True}),
                "tables": overview["tables"],
                "blocking_issues": [],
            },
            "sql_performance": {"slow_queries_total": 3},
            "integrity": {"status": "healthy"},
            "refresh_policy": {},
        }
        with patch("Database.monitoring.get_dashboard_snapshots", return_value=dashboard):
            health = get_db_health()

        self.assertEqual(health["connections"]["Threads_connected"], 2)
        self.assertEqual(health["slow_queries"], 3)
        self.assertEqual(health["replica"]["Replica_IO_Running"], "Yes")
        self.assertIsInstance(health["tables"], list)
        self.assertIn("sources", health)

        with patch(
            "Database.monitoring.inspect_slow_queries",
            return_value=result(value={
                "Slow_queries": 4,
                "top_statements": [],
                "slow_query_log": "ON",
                "long_query_time": 2.0,
                "limit": 20,
            }),
        ):
            slow = get_slow_query_summary(20)
        self.assertEqual(slow["Slow_queries"], 4)
        self.assertEqual(slow["top_statements"], [])
        self.assertIn("source_alias", slow)

    def test_worker_report_adds_summary_without_changing_job_list(self):
        """worker 报告保留列表明细并追加聚合和来源元数据。"""
        connection = ContextConnection(WorkerCursor())
        with (
            patch(
                "app.agent.job_service.get_read_connection_with_source",
                return_value=(connection, {"source_role": "primary", "source_alias": "primary"}),
            ),
            patch.object(settings, "JOB_STALE_AFTER_SECONDS", 120),
            patch.object(settings, "DB_INSPECTION_QUERY_TIMEOUT_MS", 3000),
        ):
            report = get_worker_snapshot_report()

        self.assertIsInstance(report["jobs"], list)
        self.assertEqual(report["summary"]["queued"], 2)
        self.assertEqual(report["summary"]["stale"], 1)
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["source_alias"], "primary")


if __name__ == "__main__":
    unittest.main()
