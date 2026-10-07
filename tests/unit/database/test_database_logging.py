"""数据库、monitor 的去敏摘要和转换式事件测试。"""

from __future__ import annotations

import os
from unittest.mock import Mock, patch


for key, value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "primary.test",
    "MYSQL_USER": "app",
    "MYSQL_PASSWORD": "password",
    "MYSQL_DATABASE": "causalagent",
}.items():
    os.environ.setdefault(key, value)


from Database import monitoring  # noqa: E402
from app import db  # noqa: E402
from config.database_settings import database_settings  # noqa: E402
from observability.noise_control import FailureTransitionTracker, RepeatEventLimiter  # noqa: E402


class _Cursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return "executed"


class _Pool:
    def __init__(self):
        self.connection = object()

    def get_connection(self):
        return self.connection


def test_slow_query_logs_only_operation_digest_duration_and_suppression_count():
    cursor = _Cursor()
    secret_sql = "SELECT * FROM private_table WHERE token = 'sql-secret' AND id = 42"
    with (
        patch.object(database_settings, "MYSQL_QUERY_WARN_MS", 100),
        patch("app.db.time.perf_counter", side_effect=(10.0, 10.2)),
        patch("app.db._SLOW_QUERY_LIMITER", RepeatEventLimiter()),
        patch("app.db.log_event") as log_event,
    ):
        result = db.execute_with_timing(cursor, secret_sql, ("param-secret",))

    assert result == "executed"
    event_code = log_event.call_args.args[1]
    details = log_event.call_args.kwargs["details"]
    assert event_code == "db.query.slow"
    assert details["operation"] == "select"
    assert details["duration_ms"] == 200.0
    assert len(details["statement_digest"]) == 64
    assert details["suppressed_count"] == 0
    rendered = repr(details)
    assert "private_table" not in rendered
    assert "sql-secret" not in rendered
    assert "param-secret" not in rendered


def test_slow_query_observability_failure_does_not_change_query_result():
    cursor = _Cursor()
    with (
        patch.object(database_settings, "MYSQL_QUERY_WARN_MS", 100),
        patch("app.db.time.perf_counter", side_effect=(10.0, 10.2)),
        patch("app.db._sql_identity", side_effect=RuntimeError("digest failed")),
    ):
        result = db.execute_with_timing(cursor, "SELECT 1")

    assert result == "executed"


def test_replica_fallback_is_transition_limited_and_recovers_without_hostname():
    pool = _Pool()
    tracker = FailureTransitionTracker()
    health = {"usable": False}

    def should_use(_host):
        return health["usable"]

    with (
        patch.object(database_settings, "MYSQL_READ_HOSTS", ["secret-replica.internal"]),
        patch("app.db.should_use_replica", side_effect=should_use),
        patch("app.db._replica_failure_reason", return_value=("replica_lag", 9)),
        patch("app.db._get_read_pool", return_value=pool),
        patch("app.db.random.shuffle"),
        patch("app.db._REPLICA_FAILURES", tracker),
        patch("app.db.log_event") as log_event,
    ):
        db.get_read_connection_with_source("eventual")
        db.get_read_connection_with_source("eventual")
        health["usable"] = True
        connection, source = db.get_read_connection_with_source("eventual")

    assert connection is pool.connection
    assert source == {"source_role": "replica", "source_alias": "replica-1"}
    assert [call.args[1] for call in log_event.call_args_list] == [
        "db.replica.fallback",
        "db.replica.recovered",
    ]
    rendered = repr(log_event.call_args_list)
    assert "secret-replica.internal" not in rendered


def test_connection_failure_is_transition_limited_and_success_rearms_first_failure():
    tracker = FailureTransitionTracker()
    with (
        patch("app.db._CONNECTION_FAILURES", tracker),
        patch("app.db.log_event") as log_event,
    ):
        db._log_connection_error(
            db.PoolError("first-pool-secret"),
            source_alias="primary",
            operation="write_connect",
        )
        db._log_connection_error(
            db.PoolError("repeated-pool-secret"),
            source_alias="primary",
            operation="write_connect",
        )
        db._record_connection_success(
            source_alias="primary",
            operation="write_connect",
        )
        db._log_connection_error(
            db.PoolError("rearmed-pool-secret"),
            source_alias="primary",
            operation="write_connect",
        )

    assert [call.args[1] for call in log_event.call_args_list] == [
        "db.connection.failed",
        "db.connection.failed",
    ]
    assert all("pool-secret" not in repr(call.kwargs["details"]) for call in log_event.call_args_list)


def test_database_failure_is_recorded_only_once_across_nested_boundaries():
    tracker = FailureTransitionTracker()
    error = db.PoolError("nested-pool-secret")
    with (
        patch("app.db._CONNECTION_FAILURES", tracker),
        patch("app.db.log_event") as log_event,
    ):
        db._log_connection_error(
            error,
            source_alias="primary",
            operation="write_connect",
        )
        db.record_database_failure(error, operation="file_upload_write")

    assert [call.args[1] for call in log_event.call_args_list] == [
        "db.connection.failed",
    ]
    assert "nested-pool-secret" not in repr(log_event.call_args_list)


def test_monitor_failure_repeats_are_suppressed_and_recovery_emits_once():
    tracker = FailureTransitionTracker()
    with (
        patch("Database.monitoring._SNAPSHOT_FAILURES", tracker),
        patch("Database.monitoring.log_event") as log_event,
    ):
        monitoring._record_snapshot_failure("realtime", RuntimeError("first-secret"))
        monitoring._record_snapshot_failure("realtime", RuntimeError("second-secret"))
        monitoring._record_snapshot_success("realtime")
        monitoring._record_snapshot_success("realtime")

    assert [call.args[1] for call in log_event.call_args_list] == [
        "monitor.snapshot.failed",
        "monitor.snapshot.recovered",
    ]
    assert all(
        "secret" not in repr(call.kwargs.get("details"))
        for call in log_event.call_args_list
    )


def test_nested_unknown_status_is_a_collection_failure_but_warning_is_not():
    assert monitoring._payload_has_unknown_status({
        "status": "warning",
        "checks": [{"status": "healthy"}, {"status": "unknown"}],
    })
    assert not monitoring._payload_has_unknown_status({
        "status": "warning",
        "checks": [{"status": "healthy"}, {"status": "warning"}],
    })
